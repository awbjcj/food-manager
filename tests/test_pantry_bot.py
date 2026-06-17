"""Tests for the interactive /pantry command."""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

import app.bot as bot_mod
from app.models import Household, NameTranslation, PantryItem, User
from tests.fakes import FakeTranslationLLM


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    def make():
        return Session(engine)

    with make() as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id is not None
        db.add(
            User(
                telegram_id=1,
                chat_id=99,
                household_id=household.id,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    return make


def _now(tz: str) -> datetime:
    return datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)


def _msg(text: str):
    msg = MagicMock()
    msg.from_user = MagicMock(id=1)
    msg.chat = MagicMock(id=99, type="private")
    msg.text = text
    msg.answer = AsyncMock()
    return msg


def _cb(data: str):
    cb = MagicMock()
    cb.from_user = MagicMock(id=1)
    cb.data = data
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    return cb


def _active_item(session_factory, *, expires_in_days: int = 3, status: str = "active") -> int:
    today = date(2026, 6, 14)
    with session_factory() as db:
        user = db.get(User, 1)
        assert user is not None
        item = PantryItem(
            household_id=user.household_id,
            raw_name="Milk",
            normalized_name="milk",
            category="dairy",
            qty=1.0,
            unit=None,
            purchased_on=today,
            shelf_life_days=7,
            shelf_life_source="llm",
            ingest_shelf_life_source="llm",
            expires_on=today + timedelta(days=expires_in_days),
            status=status,
            created_via="manual",
            created_at=datetime.now(timezone.utc),
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        assert item.id is not None
        return item.id


def _keyboard_data(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]


@pytest.mark.asyncio
async def test_item_list_all_callback_refreshes_full_pantry(session_factory):
    cb = _cb("item:list:all")

    with patch.object(bot_mod, "_refresh_pantry_message", new_callable=AsyncMock) as refresh:
        await bot_mod.handle_item_callback(
            cb,
            session_factory=session_factory,
            now_provider=_now,
        )

    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_item_list_callback_refreshes_digest(session_factory):
    cb = _cb("item:list")

    with patch.object(bot_mod, "_refresh_digest_message", new_callable=AsyncMock) as refresh:
        await bot_mod.handle_item_callback(
            cb,
            session_factory=session_factory,
            now_provider=_now,
        )

    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_item_open_all_renders_card_with_full_pantry_back_button(session_factory):
    item_id = _active_item(session_factory)
    cb = _cb(f"item:open:{item_id}:all")

    await bot_mod.handle_item_callback(
        cb,
        session_factory=session_factory,
        now_provider=_now,
    )

    markup = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[-1][0].callback_data == "item:list:all"


@pytest.mark.asyncio
async def test_item_open_callback_does_not_call_translation_llm_on_cache_miss(session_factory):
    item_id = _active_item(session_factory)
    with session_factory() as db:
        user = db.get(User, 1)
        assert user is not None
        user.lang = "zh"
        db.add(user)
        db.commit()
    cb = _cb(f"item:open:{item_id}")
    fake = FakeTranslationLLM(table={"Milk": "牛奶"})

    await bot_mod.handle_item_callback(
        cb,
        session_factory=session_factory,
        now_provider=_now,
        translation_llm=fake,
    )

    assert fake.calls == []
    assert "Milk" in cb.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_item_open_callback_uses_cached_translation(session_factory):
    item_id = _active_item(session_factory)
    with session_factory() as db:
        user = db.get(User, 1)
        assert user is not None
        user.lang = "zh"
        db.add(user)
        db.add(NameTranslation(lang="zh", source_text="Milk", translated_text="牛奶"))
        db.commit()
    cb = _cb(f"item:open:{item_id}")
    fake = FakeTranslationLLM(table={"Milk": "LLM milk"})

    await bot_mod.handle_item_callback(
        cb,
        session_factory=session_factory,
        now_provider=_now,
        translation_llm=fake,
    )

    assert fake.calls == []
    assert "牛奶" in cb.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_full_pantry_item_action_refreshes_full_pantry(session_factory):
    item_id = _active_item(session_factory, expires_in_days=30)
    cb = _cb(f"act:ate:{item_id}:all")

    with (
        patch.object(bot_mod, "_refresh_pantry_message", new_callable=AsyncMock) as pantry_refresh,
        patch.object(bot_mod, "_refresh_digest_message", new_callable=AsyncMock) as digest_refresh,
    ):
        await bot_mod.handle_callback(
            cb,
            session_factory=session_factory,
            now_provider=_now,
        )

    pantry_refresh.assert_awaited_once()
    digest_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_pantry_all_mode_sends_interactive_full_pantry(session_factory):
    _active_item(session_factory, expires_in_days=30)
    msg = _msg("/pantry")

    await bot_mod.handle_pantry(
        msg,
        session_factory=session_factory,
        now_provider=_now,
    )

    msg.answer.assert_awaited_once()
    text = msg.answer.await_args.args[0]
    assert "Milk" in text
    datas = _keyboard_data(msg.answer.await_args.kwargs["reply_markup"])
    assert any(data.endswith(":all") for data in datas)


@pytest.mark.asyncio
async def test_pantry_digest_mode_sends_interactive_digest(session_factory):
    _active_item(session_factory, expires_in_days=2)
    msg = _msg("/pantry digest")

    await bot_mod.handle_pantry(
        msg,
        session_factory=session_factory,
        now_provider=_now,
    )

    msg.answer.assert_awaited_once()
    datas = _keyboard_data(msg.answer.await_args.kwargs["reply_markup"])
    assert any(data.startswith("item:open:") for data in datas)
    assert not any(data.endswith(":all") for data in datas)


@pytest.mark.asyncio
async def test_pantry_item_id_mode_sends_item_card(session_factory):
    item_id = _active_item(session_factory)
    msg = _msg(f"/pantry {item_id}")

    await bot_mod.handle_pantry(
        msg,
        session_factory=session_factory,
        now_provider=_now,
    )

    msg.answer.assert_awaited_once()
    markup = msg.answer.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[-1][0].callback_data == "item:list:all"


@pytest.mark.asyncio
async def test_pantry_invalid_arg_replies_with_usage(session_factory):
    msg = _msg("/pantry nonsense")

    await bot_mod.handle_pantry(
        msg,
        session_factory=session_factory,
        now_provider=_now,
    )

    msg.answer.assert_awaited_once()
    assert "usage" in msg.answer.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_pantry_invalid_arg_uses_user_language(session_factory):
    with session_factory() as db:
        user = db.get(User, 1)
        assert user is not None
        user.lang = "zh"
        db.add(user)
        db.commit()
    msg = _msg("/pantry nonsense")

    await bot_mod.handle_pantry(
        msg,
        session_factory=session_factory,
        now_provider=_now,
    )

    msg.answer.assert_awaited_once()
    text = msg.answer.await_args.args[0]
    assert "用法" in text
    assert "/pantry" in text


@pytest.mark.asyncio
async def test_pantry_missing_item_id_replies_no_item(session_factory):
    msg = _msg("/pantry 9999")

    await bot_mod.handle_pantry(
        msg,
        session_factory=session_factory,
        now_provider=_now,
    )

    msg.answer.assert_awaited_once()
    assert "#9999" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_pantry_inactive_item_id_replies_status(session_factory):
    item_id = _active_item(session_factory, status="eaten")
    msg = _msg(f"/pantry {item_id}")

    await bot_mod.handle_pantry(
        msg,
        session_factory=session_factory,
        now_provider=_now,
    )

    msg.answer.assert_awaited_once()
    text = msg.answer.await_args.args[0]
    assert f"#{item_id}" in text
    assert "eaten" in text


@pytest.mark.asyncio
async def test_pantry_all_empty_sends_all_clear(session_factory):
    msg = _msg("/pantry")

    await bot_mod.handle_pantry(
        msg,
        session_factory=session_factory,
        now_provider=_now,
    )

    msg.answer.assert_awaited_once()
    assert "pantry" in msg.answer.await_args.args[0].lower()
