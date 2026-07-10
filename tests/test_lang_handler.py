from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

import app.bot as bot_mod
from app.models import Household, User


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    def make():
        return Session(engine)

    with make() as db:
        hh = Household(created_at=datetime.now(timezone.utc))
        db.add(hh)
        db.commit()
        db.refresh(hh)
        assert hh.id is not None
        db.add(User(telegram_id=1, chat_id=99, household_id=hh.id,
                    created_at=datetime.now(timezone.utc)))
        db.commit()
    return make


def _msg(text: str):
    msg = MagicMock()
    msg.from_user = MagicMock(id=1)
    msg.chat = MagicMock(id=99, type="private")
    msg.text = text
    msg.answer = AsyncMock()
    return msg


async def test_lang_sets_and_confirms_in_new_language(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    msg = _msg("/lang zh")
    await bot_mod.handle_lang(msg, session_factory=session_factory)
    answer_text = msg.answer.call_args.args[0]
    assert "语言已设置为" in answer_text
    with session_factory() as db:
        assert db.get(User, 1).lang == "zh"


async def test_lang_no_arg_shows_current(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    msg = _msg("/lang")
    await bot_mod.handle_lang(msg, session_factory=session_factory)
    answer_text = msg.answer.call_args.args[0]
    assert "en" in answer_text  # current language is still the default


async def test_lang_rejects_unknown(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    msg = _msg("/lang klingon")
    await bot_mod.handle_lang(msg, session_factory=session_factory)
    answer_text = msg.answer.call_args.args[0]
    assert "usage" in answer_text.lower()
    with session_factory() as db:
        assert db.get(User, 1).lang == "en"  # unchanged


async def test_help_zh_returns_translated_text(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    # Set user lang to zh first
    with session_factory() as db:
        user = db.get(User, 1)
        user.lang = "zh"
        db.add(user)
        db.commit()
    msg = _msg("/help")
    await bot_mod.handle_help(msg, session_factory=session_factory)
    answer_text = msg.answer.call_args.args[0]
    # zh help should contain Chinese characters
    assert "命令" in answer_text
    assert "/help" in answer_text
    from app.i18n import t

    assert "/lang" in t("help.topic.settings", "zh")


async def test_start_zh_returns_translated_text(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    # Set user lang to zh first
    with session_factory() as db:
        user = db.get(User, 1)
        user.lang = "zh"
        db.add(user)
        db.commit()
    msg = _msg("/start")
    await bot_mod.handle_start(
        msg, session_factory=session_factory, on_user_created=lambda u: None
    )
    answer_text = msg.answer.call_args.args[0]
    # zh start should contain Chinese characters and the timezone placeholder filled
    assert "已就绪" in answer_text
    assert "/help" in answer_text
