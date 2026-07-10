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


@pytest.mark.asyncio
async def test_mark_unique_match_applies(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    item_id = _seed_item(session_factory, "yogurt")
    msg = _nl_msg("ate the yogurt")
    await bot_mod.handle_nl_message(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 7, 9, tzinfo=timezone.utc),
        intent_agent=FakeIntentAgentSelector(
            intent=NLIntent(kind="mark", mark_action="ate", item_name="yogurt")
        ),
        text_llm=None,
    )
    assert "Ate" in _final_text(msg)
    with session_factory() as db:
        assert db.get(PantryItem, item_id).status == "eaten"


@pytest.mark.asyncio
async def test_mark_ambiguous_shows_picker(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    a = _seed_item(session_factory, "whole milk")
    b = _seed_item(session_factory, "oat milk")
    msg = _nl_msg("finished the milk")
    await bot_mod.handle_nl_message(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 7, 9, tzinfo=timezone.utc),
        intent_agent=FakeIntentAgentSelector(
            intent=NLIntent(kind="mark", mark_action="ate", item_name="milk")
        ),
        text_llm=None,
    )
    assert "Which one?" in _final_text(msg)
    with session_factory() as db:  # nothing applied
        assert db.get(PantryItem, a).status == "active"
        assert db.get(PantryItem, b).status == "active"


@pytest.mark.asyncio
async def test_mark_no_match_replies_not_found(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    msg = _nl_msg("ate the caviar")
    await bot_mod.handle_nl_message(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 7, 9, tzinfo=timezone.utc),
        intent_agent=FakeIntentAgentSelector(
            intent=NLIntent(kind="mark", mark_action="ate", item_name="caviar")
        ),
        text_llm=None,
    )
    assert "couldn't find" in _final_text(msg).lower()


@pytest.mark.asyncio
async def test_add_routes_raw_text_to_add_flow(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    captured = {}

    async def fake_add_flow(msg, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(bot_mod, "_run_add_flow", fake_add_flow)
    msg = _nl_msg("bought milk and two avocados")
    await bot_mod.handle_nl_message(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 7, 9, tzinfo=timezone.utc),
        intent_agent=FakeIntentAgentSelector(intent=NLIntent(kind="add")),
        text_llm="TEXT_LLM",
    )
    assert captured["raw_text"] == "bought milk and two avocados"
    assert captured["text_llm"] == "TEXT_LLM"


@pytest.mark.asyncio
async def test_shelf_life_question_answers(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    msg = _nl_msg("how long does whole milk keep?")
    await bot_mod.handle_nl_message(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 7, 9, tzinfo=timezone.utc),
        intent_agent=FakeIntentAgentSelector(
            intent=NLIntent(kind="shelf_life_question", food="whole milk")
        ),
        text_llm=None,
    )
    assert "7" in _final_text(msg)


@pytest.mark.asyncio
async def test_pantry_query_renders_digest_view(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    _seed_item(session_factory, "spinach")  # seeded due within the 7-day window
    msg = _nl_msg("what's expiring?")
    await bot_mod.handle_nl_message(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 7, 9, tzinfo=timezone.utc),
        intent_agent=FakeIntentAgentSelector(intent=NLIntent(kind="pantry_query")),
        text_llm=None,
    )
    assert "spinach" in _final_text(msg)


@pytest.mark.asyncio
async def test_pantry_query_empty_replies_clear(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    msg = _nl_msg("what's in my pantry?")
    await bot_mod.handle_nl_message(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 7, 9, tzinfo=timezone.utc),
        intent_agent=FakeIntentAgentSelector(intent=NLIntent(kind="pantry_query")),
        text_llm=None,
    )
    from app.i18n import t

    assert _final_text(msg) == t("digest.pantry_clear", "en")


@pytest.mark.asyncio
async def test_start_sends_ready_then_tour(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    msg = _nl_msg("/start")
    await bot_mod.handle_start(
        msg, session_factory=session_factory, on_user_created=lambda user: None
    )
    assert msg.answer.await_count == 2
    tour = msg.answer.await_args_list[1].args[0]
    assert "bought milk" in tour and "/pantry" in tour


@pytest.mark.asyncio
async def test_help_shows_overview_with_topic_buttons(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    msg = _nl_msg("/help")
    await bot_mod.handle_help(msg, session_factory=session_factory)
    text = msg.answer.await_args.args[0]
    assert "bought milk" in text  # NL example present
    keyboard = msg.answer.await_args.kwargs["reply_markup"]
    datas = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert datas == ["help:pantry", "help:cook", "help:household", "help:settings"]
