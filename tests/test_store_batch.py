import json
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app import handler_support, views
from app.batch_service import (
    MAX_SELECTED,
    PAGE_SIZE,
    TTL,
    BatchError,
    change_selection,
    create_batch,
    load_batch,
)
from app.callbacks.batch import handle_batch_callback
from app.commands import CommandError, parse_callback_request, parse_pantry_arg
from app.handlers.pantry import handle_pantry
from app.ingest_service import ingest_photo
from app.llm import SYSTEM_PROMPT, LLMResult, ParsedItem, ParseResult
from app.models import (
    Household,
    PantryBatch,
    PantryItem,
    PantryOutcome,
    PendingCorrection,
    Receipt,
    User,
)
from app.pantry_service import ListFilter, PantrySort, list_active, mark_eaten
from app.pending_service import create_pending
from app.renderer import build_digest_keyboard, render_ingest_reply
from tests.fakes import FakeLLMClient

TODAY = date(2026, 10, 4)
NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)


def item(session, name="Milk", *, household=1, receipt=None, status="active"):
    row = PantryItem(
        household_id=household,
        raw_name=name,
        normalized_name=name.lower(),
        purchased_on=TODAY,
        shelf_life_days=5,
        shelf_life_source="llm",
        ingest_shelf_life_source="llm",
        expires_on=TODAY + timedelta(days=5),
        created_via="receipt" if receipt else "manual",
        source_receipt_id=receipt,
        created_at=NOW,
        status=status,
        snoozed_until=TODAY + timedelta(days=1),
    )
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'pantry.db').as_posix()}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [Household(id=1, created_at=NOW), Household(id=2, created_at=NOW)]
        )
        session.add_all(
            [
                User(
                    telegram_id=n,
                    chat_id=n,
                    household_id=1 if n < 3 else 2,
                    created_at=NOW,
                )
                for n in (1, 2, 3)
            ]
        )
        item(session, "Milk")
        item(session, "Apples")
        item(session, "Foreign food", household=2)
        session.commit()
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    monkeypatch.setattr(handler_support, "OPEN_REGISTRATION", False)
    monkeypatch.setattr(handler_support, "MULTI_TENANT_ENABLED", True)
    yield lambda: Session(engine)
    engine.dispose()


def start(session, sort: PantrySort = "receipt"):
    return create_batch(
        session, household_id=1, user_id=1, sort_by=sort, today=TODAY, now=NOW
    )


def act(session, batch, action, value=None):
    return change_selection(
        session,
        batch,
        version=batch.version,
        action=action,
        value=None if value is None else str(value),
        now=NOW,
    )


async def test_receipt_store_is_separate_and_persists_across_sessions(sessions):
    parsed = ParseResult(
        store_name="  Trader   Joe's\n  ",
        purchase_date=TODAY,
        purchase_date_confidence=1,
        items=[
            ParsedItem(
                is_food=True,
                name="Brussels sprouts",
                confidence=1,
                est_shelf_life_days=7,
                category="produce",
            )
        ],
    )
    with sessions() as session:
        summary = await ingest_photo(
            session,
            FakeLLMClient(canned=LLMResult(parse=parsed)),
            household_id=1,
            photo_file_id="store-receipt",
            image_bytes=b"image",
            today=TODAY,
            scanned_at=NOW,
        )
    with sessions() as session:
        receipt = session.get(Receipt, summary.receipt_id)
        assert receipt.store_name == "Trader Joe's"
        rows = session.exec(
            select(PantryItem).where(PantryItem.source_receipt_id == receipt.id)
        ).all()
        assert [row.raw_name for row in rows] == ["Brussels sprouts"]
        assert rows[0].expires_on == TODAY + timedelta(days=7)
    assert summary.store_name == "Trader Joe's"
    assert "Store · Trader Joe's" in render_ingest_reply(summary, today=TODAY)
    assert "store_name" in ParseResult.model_json_schema()["properties"]
    assert "Do not infer the retailer from a product brand" in SYSTEM_PROMPT


async def test_batch_command_opens_a_large_pantry_within_telegram_limits(sessions):
    with sessions() as session:
        for n in range(110):
            item(session, f"Long branded grocery name number {n}")
        session.commit()
        rows = list_active(session, household_id=1, f=ListFilter.default(), today=TODAY)
        full = await views.digest(
            session,
            rows,
            user=session.get(User, 1),
            today=TODAY,
            translation_llm=None,
            cap=None,
        )
        assert len(full.text) > 4096
    msg = MagicMock()
    msg.from_user.id = 1
    msg.chat.id = 1
    msg.chat.type = "private"
    msg.text = "/pantry batch"
    msg.answer = AsyncMock()
    await handle_pantry(msg, session_factory=sessions, now_provider=lambda tz: NOW)
    text = msg.answer.call_args.args[0]
    assert len(text) < 4096
    assert "Page 1 of 10" in text
    keyboard = msg.answer.call_args.kwargs["reply_markup"].inline_keyboard
    assert (
        sum(":toggle:" in b.callback_data for row in keyboard for b in row) == PAGE_SIZE
    )


@pytest.mark.parametrize(
    "value,expected", [(None, None), ("\n  ", None), ("X" * 500, "X" * 120)]
)
def test_store_identity_is_optional_and_bounded(value, expected):
    assert ParseResult(store_name=value, items=[]).store_name == expected


async def test_store_view_groups_multiple_receipts_and_keeps_unknown_items(sessions):
    with sessions() as session:
        for name in ("Costco", "COSTCO", "Trader Joe's", None):
            receipt = Receipt(
                household_id=1,
                photo_file_id=str(name),
                store_name=name,
                purchase_date=TODAY,
                purchase_date_source="receipt",
                scanned_at=NOW,
            )
            session.add(receipt)
            session.flush()
            item(session, str(name), receipt=receipt.id)
        foreign = Receipt(
            household_id=2,
            photo_file_id="foreign",
            store_name="Foreign brand",
            purchase_date=TODAY,
            purchase_date_source="receipt",
            scanned_at=NOW,
        )
        session.add(foreign)
        session.commit()
        user = session.get(User, 1)
        rows = list_active(
            session,
            household_id=1,
            f=ListFilter.default(),
            today=TODAY,
            sort_by="store",
        )
        view = await views.digest(
            session,
            rows,
            user=user,
            today=TODAY,
            translation_llm=None,
            cap=None,
            sort_by="store",
        )
        assert [row.raw_name for row in rows[:3]] == [
            "Costco",
            "COSTCO",
            "Trader Joe's",
        ]
        assert view.text.count("🏪 Costco") == 1
        assert "🏪 COSTCO" not in view.text
        assert "🏪 Unknown store" in view.text
        assert "Foreign brand" not in view.text
        keyboard = build_digest_keyboard(
            rows, has_more=False, today=TODAY, back_to="all", sort_by="store"
        )
        payloads = [button.callback_data for row in keyboard for button in row]
        assert "batch:start:store" in payloads
        assert f"item:open:{rows[0].id}:all:store" in payloads
        assert parse_pantry_arg(["store"]) == "store"


@pytest.mark.parametrize("target", ["eaten", "tossed", "removed"])
def test_batch_applies_once_after_restart_and_invalidates_pending(sessions, target):
    with sessions() as session:
        create_pending(
            session,
            household_id=1,
            action_type="correct",
            item_id=1,
            proposed_json="{}",
            snapshot_json=None,
            cost_micros_usd=None,
            chat_id=1,
            now=NOW,
        )
        batch = start(session)
        act(session, batch, "select_page")
        act(session, batch, "confirm", target)
        batch_id, version = batch.id, batch.version
        assert batch_id is not None
        assert session.get(PantryItem, 1).status == "active"
    with sessions() as session:
        batch = load_batch(
            session, batch_id=batch_id, household_id=1, user_id=1, now=NOW
        )
        act(session, batch, "apply")
        assert (batch.applied_count, batch.skipped_count) == (2, 0)
    with sessions() as session:
        assert [session.get(PantryItem, n).status for n in (1, 2, 3)] == [
            target,
            target,
            "active",
        ]
        assert all(session.get(PantryItem, n).snoozed_until is None for n in (1, 2))
        assert session.exec(select(PendingCorrection)).one().status == "stale"
        batch = load_batch(
            session, batch_id=batch_id, household_id=1, user_id=1, now=NOW
        )
        with pytest.raises(BatchError, match="closed"):
            change_selection(
                session, batch, version=version, action="apply", value=None, now=NOW
            )


def test_batch_owner_ttl_foreign_ids_and_empty_selection(sessions):
    with sessions() as session:
        batch = start(session)
        assert batch.id is not None
        for household, user in [(1, 2), (2, 1), (2, 3)]:
            with pytest.raises(BatchError, match="expired"):
                load_batch(
                    session,
                    batch_id=batch.id,
                    household_id=household,
                    user_id=user,
                    now=NOW,
                )
        with pytest.raises(BatchError, match="expired"):
            load_batch(
                session, batch_id=batch.id, household_id=1, user_id=1, now=NOW + TTL
            )
        with pytest.raises(BatchError, match="empty"):
            act(session, batch, "confirm", "eaten")
        assert batch.version == 0
        with pytest.raises(BatchError, match="invalid"):
            act(session, batch, "toggle", 3)
        assert batch.version == 0 and batch.selected_ids_json == "[]"


def test_batch_pagination_deselect_cancel_and_limit(sessions):
    with sessions() as session:
        for n in range(MAX_SELECTED):
            item(session, f"Food {n}")
        session.commit()
        batch = start(session)
        act(session, batch, "select_page")
        assert len(json.loads(batch.selected_ids_json)) == PAGE_SIZE
        act(session, batch, "page", 1)
        act(session, batch, "select_page")
        assert len(json.loads(batch.selected_ids_json)) == PAGE_SIZE * 2
        act(session, batch, "toggle", 1)
        assert 1 not in json.loads(batch.selected_ids_json)
        act(session, batch, "clear")
        assert batch.selected_ids_json == "[]"
        for page in range(8):
            act(session, batch, "page", page)
            act(session, batch, "select_page")
        act(session, batch, "page", 8)
        with pytest.raises(BatchError, match="limit"):
            act(session, batch, "select_page")
        assert len(json.loads(batch.selected_ids_json)) == 96
        act(session, batch, "cancel")
        assert all(
            row.status == "active" for row in session.exec(select(PantryItem)).all()
        )


def test_concurrent_stale_confirmation_and_changed_items_do_not_overwrite(sessions):
    with sessions() as session:
        batch = start(session)
        act(session, batch, "select_page")
        act(session, batch, "confirm", "tossed")
        batch_id = batch.id
        assert batch_id is not None
    with sessions() as first, sessions() as second:
        batch1 = load_batch(
            first, batch_id=batch_id, household_id=1, user_id=1, now=NOW
        )
        batch2 = load_batch(
            second, batch_id=batch_id, household_id=1, user_id=1, now=NOW
        )
        old_version = batch2.version
        act(first, batch1, "edit")
        act(first, batch1, "toggle", 2)
        with pytest.raises(BatchError, match="stale"):
            change_selection(
                second, batch2, version=old_version, action="apply", value=None, now=NOW
            )
        assert second.get(PantryItem, 1).status == "active"
    with sessions() as session:
        mark_eaten(session, household_id=1, item_id=1, today=TODAY)
        batch = load_batch(
            session, batch_id=batch_id, household_id=1, user_id=1, now=NOW
        )
        act(session, batch, "confirm", "tossed")
        act(session, batch, "apply")
        assert (batch.applied_count, batch.skipped_count) == (0, 1)
        assert session.get(PantryItem, 1).status == "eaten"


@pytest.mark.parametrize("target", ["eaten", "tossed", "removed"])
def test_batch_atomic_rollback_and_missing_selected_row(sessions, monkeypatch, target):
    import app.pantry_service as service

    with sessions() as session:
        batch = start(session)
        act(session, batch, "select_page")
        act(session, batch, "confirm", target)
        version = batch.version
        calls = 0
        original_expire = service.expire_for_item

        def fail(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated pending invalidation failure")
            return original_expire(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(service, "expire_for_item", fail)
            with pytest.raises(RuntimeError):
                act(session, batch, "apply")
        assert session.get(PantryItem, 1).status == "active"
        assert session.get(PantryItem, 2).status == "active"
        assert session.exec(select(PantryOutcome)).all() == []
        assert batch.status == "confirming" and batch.version == version
        session.delete(session.get(PantryItem, 2))
        session.commit()
        with pytest.raises(BatchError, match="invalid"):
            act(session, batch, "apply")
        assert session.get(PantryItem, 1).status == "active"


def test_expired_selection_ids_are_never_reused(sessions):
    with sessions() as session:
        first = start(session)
        old_id = first.id
        assert old_id is not None
        second = create_batch(
            session,
            household_id=1,
            user_id=1,
            sort_by="store",
            today=TODAY,
            now=NOW + TTL,
        )
        assert second.id is not None and second.id > old_id
        assert session.get(PantryBatch, old_id) is None


@pytest.mark.parametrize(
    "data",
    [
        "batch:1:0:confirm:active",
        "batch:1:0:apply:extra",
        "batch:0:0:apply",
        "batch:1:-1:apply",
        "batch:start:unknown",
        "batch:1:0:toggle:abc",
        "batch:1:0:toggle:" + "9" * 50,
    ],
)
def test_batch_callback_parser_rejects_malformed_data(data):
    with pytest.raises(CommandError):
        parse_callback_request(data)


async def test_telegram_batch_round_trip_and_ack_before_render(sessions):
    cb = MagicMock()
    cb.from_user.id = 1
    cb.message.chat.id = 1
    cb.message.chat.type = "private"
    events = []
    cb.answer = AsyncMock(side_effect=lambda *args, **kwargs: events.append("ack"))
    cb.message.edit_text = AsyncMock(
        side_effect=lambda *args, **kwargs: events.append("edit")
    )
    cb.message.answer = AsyncMock()

    async def press(data):
        events.clear()
        cb.data = data
        await handle_batch_callback(
            cb, session_factory=sessions, now_provider=lambda tz: NOW
        )
        assert events == ["ack", "edit"]
        return cb.message.edit_text.call_args.args[
            0
        ], cb.message.edit_text.call_args.kwargs["reply_markup"].inline_keyboard

    text, keyboard = await press("batch:start:store")
    assert "Unknown store" in text
    select = next(
        b.callback_data for row in keyboard for b in row if b.text == "Select this page"
    )
    text, keyboard = await press(select)
    eaten = next(
        b.callback_data
        for row in keyboard
        for b in row
        if "Eaten" in b.text or "Ate" in b.text
    )
    text, keyboard = await press(eaten)
    assert "Mark 2 selected" in text
    confirm = next(
        b.callback_data
        for row in keyboard
        for b in row
        if b.text == "Confirm status change"
    )
    text, keyboard = await press(confirm)
    assert "Updated 2 items to eaten" in text
    with sessions() as session:
        assert session.get(PantryItem, 1).status == "eaten"
    assert all(
        len(b.callback_data.encode("utf-8")) <= 64 for row in keyboard for b in row
    )
