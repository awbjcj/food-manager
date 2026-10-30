from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app import handler_support
from app.callbacks.actions import handle_callback
from app.callbacks.items import handle_item_callback
from app.commands import (
    CommandError,
    parse_callback,
    parse_callback_request,
    parse_item_callback,
)
from app.handlers.pantry import handle_pantry
from app.models import Household, PantryItem, User
from app.renderer import (
    build_digest_keyboard,
    build_item_card_keyboard,
    render_pantry_page,
)
from app.telegram_text import text_units

TODAY = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def food(n):
    return PantryItem(id=n, household_id=1, raw_name=f"Long grocery {n}", normalized_name=f"food {n}",
        purchased_on=TODAY, shelf_life_days=7, shelf_life_source="llm",
        ingest_shelf_life_source="llm", expires_on=TODAY + timedelta(days=n % 7),
        created_via="manual", created_at=NOW)


@pytest.mark.parametrize("lang", ["en", "zh", "fr", "es"])
@pytest.mark.parametrize("sort_by", ["receipt", "category", "expires", "store"])
def test_every_large_pantry_page_is_bounded_and_all_items_remain_reachable(lang, sort_by):
    items = [food(n) for n in range(1, 111)]
    names = {item.raw_name: "🥦" * 250 + str(item.id) for item in items}
    first = render_pantry_page(items, today=TODAY, lang=lang, names=names, sort_by=sort_by)
    visited = []
    for page in range(first.page_count):
        view = render_pantry_page(items, today=TODAY, lang=lang, names=names,
                                  sort_by=sort_by, page=page)
        assert text_units(view.text) <= 4096
        assert view.total_count == 110
        visited.extend(view.rendered_item_ids)
        rows = build_digest_keyboard(view.rendered_items, has_more=view.has_more,
            today=TODAY, lang=lang, names=names, back_to="all", sort_by=sort_by,
            page=view.page, page_count=view.page_count)
        assert sum(len(row) for row in rows) < 30
        for row in rows:
            for button in row:
                assert button.callback_data is not None
                assert len(button.callback_data.encode()) <= 64
                parse_callback_request(button.callback_data)
        next_page = [b for row in rows for b in row if b.text.endswith("➡")]
        assert bool(next_page) == (page < first.page_count - 1)
    assert visited == list(range(1, 111))


@pytest.mark.parametrize("origin", ["all", "due"])
def test_card_actions_and_back_button_keep_page_origin_and_sort(origin):
    rows = build_item_card_keyboard(food(1), back_to=origin, sort_by="category", page=3)
    for row in rows:
        for b in row:
            assert b.callback_data is not None
            action = parse_item_callback(b.callback_data) if b.callback_data.startswith("item:") else parse_callback(b.callback_data)
            assert action.page == 3 and action.back_to == origin
            assert action.sort_by == ("category" if origin == "all" else "expires")


@pytest.mark.parametrize("suffix", [":all:p:-1", ":all:p:no", ":all:p:99999999", ":due:category:p:2", ":all:p:2:extra"])
def test_invalid_page_origins_are_rejected(suffix):
    with pytest.raises(CommandError):
        parse_item_callback("item:list" + suffix)


@pytest.fixture
def sessions(monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Household(id=1, created_at=NOW))
        db.commit()
        db.add(User(telegram_id=1, chat_id=1, household_id=1, created_at=NOW))
        db.add_all(food(n) for n in range(1, 111))
        db.commit()
    yield lambda: Session(engine)
    engine.dispose()


def callback(data):
    cb = MagicMock()
    cb.data = data
    cb.from_user.id = 1
    cb.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    return cb


@pytest.mark.parametrize("mode", ["", " category", " store", " expires", " digest"])
async def test_large_pantry_command_stays_within_text_limit(sessions, mode):
    msg = MagicMock()
    msg.from_user.id = 1
    msg.chat.id = 1
    msg.chat.type = "private"
    msg.text = "/pantry" + mode
    msg.answer = AsyncMock()
    await handle_pantry(msg, session_factory=sessions, now_provider=lambda _: NOW)
    assert text_units(msg.answer.await_args.args[0]) <= 4096
    rows = msg.answer.await_args.kwargs["reply_markup"].inline_keyboard
    assert sum(len(row) for row in rows) < 30


async def test_show_all_navigation_and_actions_return_to_the_same_due_page(sessions):
    first = callback("show:all")
    await handle_callback(first, session_factory=sessions, now_provider=lambda _: NOW)
    rows = first.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
    next_data = next(b.callback_data for row in rows for b in row if b.callback_data == "item:list:due:p:1")
    next_cb = callback(next_data)
    await handle_item_callback(next_cb, session_factory=sessions, now_provider=lambda _: NOW)
    assert "Page 2 of" in next_cb.message.edit_text.await_args.args[0]
    rows = next_cb.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
    open_data = next(b.callback_data for row in rows for b in row if b.callback_data.startswith("item:open:"))
    item_id = parse_item_callback(open_data).item_id
    action = callback(f"act:ate:{item_id}:due:p:1")
    await handle_callback(action, session_factory=sessions, now_provider=lambda _: NOW)
    assert "Page 2 of" in action.message.edit_text.await_args.args[0]
    assert text_units(action.message.edit_text.await_args.args[0]) <= 4096
    with sessions() as db:
        assert db.get(PantryItem, item_id).status == "eaten"


async def test_old_page_number_clamps_after_items_have_been_removed(sessions):
    cb = callback("item:list:all:category:p:1000000")
    await handle_item_callback(cb, session_factory=sessions, now_provider=lambda _: NOW)
    assert "Page 6 of 6" in cb.message.edit_text.await_args.args[0]
