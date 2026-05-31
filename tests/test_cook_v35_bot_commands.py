import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from sqlmodel import Session, SQLModel, create_engine

import app.bot as bot_mod
from app.bot import handle_favorites, handle_shopping
from app.cook_models import RecipeCandidate, RecipeIngredient
from app.favorites_service import save_candidate
from app.models import Household, User
from app.shopping_service import add_missing


class _Msg:
    def __init__(self, text="/shopping", user_id=1, chat_id=1):
        self.text = text
        self.from_user = type("U", (), {"id": user_id})
        self.chat = type("C", (), {"id": chat_id, "type": "private"})
        self.answer = AsyncMock(return_value=type("S", (), {"message_id": 99}))


def _engine_with_user():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        db.add(User(telegram_id=1, chat_id=1, household_id=household.id,
                    created_at=datetime.now(timezone.utc)))
        db.commit()
    return engine


def _NOW(tz):
    return datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)


def test_shopping_lists_pending_with_buttons(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    with Session(engine) as db:
        add_missing(db, household_id=1, ingredients=[RecipeIngredient(name="Eggs")], now=_NOW("x"))
    asyncio.run(handle_shopping(
        _Msg(), session_factory=lambda: Session(engine), now_provider=_NOW))


def test_shopping_handler_renders_items(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    with Session(engine) as db:
        add_missing(db, household_id=1, ingredients=[RecipeIngredient(name="Eggs")], now=_NOW("x"))
    msg = _Msg()
    asyncio.run(handle_shopping(
        msg, session_factory=lambda: Session(engine), now_provider=_NOW))
    msg.answer.assert_awaited()
    assert msg.answer.await_args is not None
    text = msg.answer.await_args.args[0]
    assert "Eggs" in text
    keyboard = msg.answer.await_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].callback_data.startswith("shopdone:")


def test_favorites_handler_renders_saved(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    with Session(engine) as db:
        save_candidate(db, household_id=1, candidate=RecipeCandidate(
            title="Pasta", cuisine="italian", source_url="u",
            ingredients=[RecipeIngredient(name="pasta")], method_gist="boil"),
            now=_NOW("x"))
    msg = _Msg(text="/favorites")
    asyncio.run(handle_favorites(
        msg, session_factory=lambda: Session(engine)))
    msg.answer.assert_awaited()
    assert msg.answer.await_args is not None
    text = msg.answer.await_args.args[0]
    assert "Pasta" in text
    keyboard = msg.answer.await_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].callback_data.startswith("favcook:")


def test_favorites_empty_state(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    msg = _Msg(text="/favorites")
    asyncio.run(handle_favorites(msg, session_factory=lambda: Session(engine)))
    assert msg.answer.await_args is not None
    text = msg.answer.await_args.args[0]
    assert "no saved" in text.lower()
