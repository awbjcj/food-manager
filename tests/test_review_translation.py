from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app import handler_support
from app.billing import meter
from app.billing.entitlement import get_or_create_subscription, get_or_create_usage
from app.billing.plans import limits_for, units_for
from app.callbacks.batch import handle_batch_callback
from app.client_set import PerUserClients
from app.handlers.meta import handle_prefs
from app.handlers.pantry import handle_pantry
from app.models import (
    Household,
    NameTranslation,
    PantryBatch,
    PantryItem,
    QuotaUsage,
    User,
)
from app.pantry_service import mark_eaten
from app.profile_service import FoodProfile
from tests.fakes import FakeProfileLLMClient

TODAY = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


@pytest.fixture
def sessions(monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    monkeypatch.setattr(handler_support, "MULTI_TENANT_ENABLED", True)
    monkeypatch.setattr(meter, "BILLING_ENABLED", True)
    monkeypatch.setattr(meter, "METERING_ENABLED", True)
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Household(id=1, created_at=NOW))
        db.commit()
        db.add(User(telegram_id=1, chat_id=1, household_id=1, lang="zh", created_at=NOW))
        db.commit()
        for n in range(16):
            db.add(PantryItem(household_id=1, raw_name=f"Pear {n}", normalized_name=f"pear {n}",
                purchased_on=TODAY, shelf_life_days=7, shelf_life_source="llm",
                ingest_shelf_life_source="llm", expires_on=TODAY + timedelta(days=7),
                created_via="manual", created_at=NOW))
        db.commit()
        mark_eaten(db, household_id=1, item_id=1, user_id=1, today=TODAY)
    yield lambda: Session(engine)
    engine.dispose()


def message(text):
    return SimpleNamespace(text=text, from_user=SimpleNamespace(id=1),
        chat=SimpleNamespace(id=1, type="private"), answer=AsyncMock())


def translator(*, malformed=False, fails=False):
    async def translate(*, texts, lang):
        if fails:
            raise RuntimeError("simulated provider failure")
        return ([] if malformed else [f"translated-{text}" for text in texts]), 1234
    return SimpleNamespace(translate=AsyncMock(side_effect=translate))


def set_exhausted(sessions, reason):
    with sessions() as db:
        sub = get_or_create_subscription(db, household_id=1, now=NOW)
        usage = get_or_create_usage(db, household_id=1, period_start=sub.period_start)
        limits = limits_for("free")
        if reason == "actions":
            assert limits.actions is not None
            usage.actions_used = limits.actions
        else:
            assert limits.cost_breaker_micros is not None
            usage.cost_micros_used = limits.cost_breaker_micros
        db.add(usage)
        db.commit()


@pytest.mark.parametrize("command", ["/prefs", "/prefs vegetarian"])
@pytest.mark.parametrize("reason", ["actions", "cost"])
async def test_denied_preferences_never_call_live_translation(sessions, command, reason):
    set_exhausted(sessions, reason)
    llm = translator()
    msg = message(command)
    await handle_prefs(msg, session_factory=sessions, now_provider=lambda _: NOW,
                       clients=PerUserClients.for_tests(translation=llm))
    llm.translate.assert_not_awaited()
    assert "pear 0" in msg.answer.await_args.args[0]


@pytest.mark.parametrize("malformed,fails,expected_cost", [
    (False, False, 1234), (True, False, 1234), (False, True, 0),
])
async def test_preferences_translation_records_attempts_and_returned_cost(
    sessions, malformed, fails, expected_cost,
):
    llm = translator(malformed=malformed, fails=fails)
    msg = message("/prefs")
    await handle_prefs(msg, session_factory=sessions, now_provider=lambda _: NOW,
                       clients=PerUserClients.for_tests(translation=llm))
    assert llm.translate.await_count == 1
    with sessions() as db:
        usage = db.exec(select(QuotaUsage)).one()
        assert usage.actions_used == units_for("edit", "anthropic")
        assert usage.cost_micros_used == expected_cost
        if malformed or fails:
            assert not db.exec(select(NameTranslation)).all()
    if not malformed and not fails:
        await handle_prefs(msg, session_factory=sessions, now_provider=lambda _: NOW,
                           clients=PerUserClients.for_tests(translation=llm))
        assert llm.translate.await_count == 1
        with sessions() as db:
            assert db.exec(select(QuotaUsage)).one().cost_micros_used == 1234


async def test_successful_profile_update_meters_the_following_translation(sessions):
    llm = translator()
    msg = message("/prefs vegetarian")
    await handle_prefs(msg, session_factory=sessions, now_provider=lambda _: NOW,
        clients=PerUserClients.for_tests(translation=llm,
            profile=FakeProfileLLMClient(canned=(FoodProfile(), 321))))
    assert llm.translate.await_count == 1
    with sessions() as db:
        usage = db.exec(select(QuotaUsage)).one()
        assert usage.actions_used == 2 * units_for("edit", "anthropic")
        assert usage.cost_micros_used == 1555


async def test_english_preference_display_does_not_consume_quota(sessions):
    with sessions() as db:
        user = db.get(User, 1)
        user.lang = "en"
        db.add(user)
        db.commit()
    llm = translator()
    await handle_prefs(message("/prefs"), session_factory=sessions, now_provider=lambda _: NOW,
                       clients=PerUserClients.for_tests(translation=llm))
    llm.translate.assert_not_awaited()
    with sessions() as db:
        assert not db.exec(select(QuotaUsage)).all()


@pytest.mark.parametrize("lang", ["zh", "fr", "es"])
async def test_batch_command_warms_names_once_for_cache_only_page_navigation(sessions, lang):
    with sessions() as db:
        user = db.get(User, 1)
        user.lang = lang
        db.add(user)
        db.commit()
    llm = translator()
    msg = message("/pantry batch")
    await handle_pantry(msg, session_factory=sessions, now_provider=lambda _: NOW,
                        translation_llm=llm)
    assert llm.translate.await_count == 1
    assert "translated-Pear" in msg.answer.await_args.args[0]
    markup = msg.answer.await_args.kwargs["reply_markup"]
    next_page = next(b.callback_data for row in markup.inline_keyboard for b in row if ":page:1" in b.callback_data)
    cb = MagicMock()
    cb.data = next_page
    cb.from_user.id = 1
    cb.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    await handle_batch_callback(cb, session_factory=sessions, now_provider=lambda _: NOW,
                                translation_llm=llm)
    assert cb.message.edit_text.await_args is not None
    assert "translated-Pear" in cb.message.edit_text.await_args.args[0]
    assert llm.translate.await_count == 1


@pytest.mark.parametrize("denied", [False, True])
async def test_batch_entry_from_old_card_acknowledges_before_translation(sessions, denied):
    if denied:
        set_exhausted(sessions, "actions")
    cb = MagicMock()
    cb.data = "batch:start:receipt"
    cb.from_user.id = 1
    cb.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    llm = translator()
    original = llm.translate.side_effect

    async def observed(**kwargs):
        assert cb.answer.await_count == 1
        return await original(**kwargs)
    llm.translate.side_effect = observed
    await handle_batch_callback(cb, session_factory=sessions, now_provider=lambda _: NOW,
                                translation_llm=llm)
    assert llm.translate.await_count == (0 if denied else 1)
    with sessions() as db:
        assert len(db.exec(select(PantryBatch)).all()) == 1
