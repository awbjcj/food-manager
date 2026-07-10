from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

import app.bot as bot_mod
from app.models import Household, PantryItem, User
from app.nl_intent import NLIntent


class FakeIntentAgentSelector:
    """Duck-types IntentAgentSelector: for_provider() returns a canned parser."""

    def __init__(self, intent=None, error: Exception | None = None):
        self.intent = intent
        self.error = error

    def for_provider(self, provider):
        return self

    async def parse(self, text, *, today, pantry_names):
        if self.error is not None:
            raise self.error
        return self.intent


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
        db.add(
            User(
                telegram_id=1,
                chat_id=1,
                household_id=household.id,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    return make


def _seed_item(session_factory, name, item_id=None):
    with session_factory() as db:
        today = datetime(2026, 7, 9).date()
        item = PantryItem(
            household_id=1,
            raw_name=name,
            normalized_name=name,
            category="produce",
            qty=1.0,
            purchased_on=today,
            shelf_life_days=7,
            shelf_life_source="llm",
            ingest_shelf_life_source="llm",
            expires_on=today + timedelta(days=7),
            status="active",
            created_via="receipt",
            created_at=datetime.now(timezone.utc),
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item.id


def _nl_msg(text: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=1),
        chat=SimpleNamespace(id=1, type="private"),
        answer=AsyncMock(
            return_value=SimpleNamespace(
                message_id=9, edit_text=AsyncMock(), delete=AsyncMock()
            )
        ),
        photo=None,
        reply_to_message=None,
        bot=None,
    )


def _final_text(msg) -> str:
    ack = msg.answer.return_value
    if ack.edit_text.await_args is not None:
        return ack.edit_text.await_args.args[0]
    return msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_unknown_intent_replies_hint(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    msg = _nl_msg("blah blah")
    await bot_mod.handle_nl_message(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 7, 9, tzinfo=timezone.utc),
        intent_agent=FakeIntentAgentSelector(intent=NLIntent(kind="unknown")),
        text_llm=None,
    )
    assert "didn't catch that" in _final_text(msg)


@pytest.mark.asyncio
async def test_agent_failure_degrades_to_hint(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    msg = _nl_msg("hello")
    await bot_mod.handle_nl_message(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 7, 9, tzinfo=timezone.utc),
        intent_agent=FakeIntentAgentSelector(error=RuntimeError("provider down")),
        text_llm=None,
    )
    assert "didn't catch that" in _final_text(msg)


@pytest.mark.asyncio
async def test_commands_and_empty_text_are_ignored(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    for text in ("/typo", ""):
        msg = _nl_msg(text)
        await bot_mod.handle_nl_message(
            msg,
            session_factory=session_factory,
            now_provider=lambda tz: datetime(2026, 7, 9, tzinfo=timezone.utc),
            intent_agent=FakeIntentAgentSelector(intent=NLIntent(kind="unknown")),
            text_llm=None,
        )
        msg.answer.assert_not_awaited()
