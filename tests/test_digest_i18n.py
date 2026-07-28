"""Tests for i18n-aware daily digest (Task 18)."""
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import Household, PantryItem, User
from app.scheduler import send_digest_once
from tests.fakes import FakeTranslationLLM


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    return eng


def _make_session_factory(eng):
    def factory():
        return Session(eng)
    return factory


def _seed_zh_user(eng):
    """Seed a zh-lang user with one active PantryItem 'Milk' due today."""
    today = date(2026, 5, 26)
    with Session(eng) as db:
        household = Household(created_at=datetime.now(UTC))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id is not None
        user = User(
            telegram_id=42,
            chat_id=999,
            household_id=household.id,
            lang="zh",
            created_at=datetime.now(UTC),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        item = PantryItem(
            household_id=household.id,
            raw_name="Milk",
            normalized_name="milk",
            category="dairy",
            qty=1.0,
            unit="gal",
            purchased_on=today,
            shelf_life_days=0,
            shelf_life_source="llm",
            ingest_shelf_life_source="llm",
            expires_on=today,  # due exactly today
            status="active",
            created_via="manual",
            created_at=datetime.now(UTC),
        )
        db.add(item)
        db.commit()
        uid = user.telegram_id
    return uid


def _seed_en_user(eng):
    """Seed a default (en) user with one active PantryItem 'Milk' due today."""
    today = date(2026, 5, 26)
    with Session(eng) as db:
        household = Household(created_at=datetime.now(UTC))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id is not None
        user = User(
            telegram_id=7,
            chat_id=888,
            household_id=household.id,
            created_at=datetime.now(UTC),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        item = PantryItem(
            household_id=household.id,
            raw_name="Milk",
            normalized_name="milk",
            category="dairy",
            qty=1.0,
            unit="gal",
            purchased_on=today,
            shelf_life_days=0,
            shelf_life_source="llm",
            ingest_shelf_life_source="llm",
            expires_on=today,
            status="active",
            created_via="manual",
            created_at=datetime.now(UTC),
        )
        db.add(item)
        db.commit()
        uid = user.telegram_id
    return uid


@pytest.mark.asyncio
async def test_digest_translated_for_zh_user(engine):
    user_id = _seed_zh_user(engine)
    fake = FakeTranslationLLM(table={"Milk": "牛奶"})
    bot = MagicMock()
    bot.send_message = AsyncMock()

    sent = await send_digest_once(
        user_id=user_id,
        bot=bot,
        session_factory=_make_session_factory(engine),
        today_provider=lambda tz: date(2026, 5, 26),
        translation_llm=fake,
    )

    assert sent is True
    text = bot.send_message.call_args.kwargs["text"]
    assert "牛奶" in text, f"Expected Chinese name in digest text, got: {text!r}"


@pytest.mark.asyncio
async def test_digest_english_user_unchanged(engine):
    """English user: no translation_llm needed; 'Milk' appears as-is, no crash."""
    user_id = _seed_en_user(engine)
    bot = MagicMock()
    bot.send_message = AsyncMock()

    sent = await send_digest_once(
        user_id=user_id,
        bot=bot,
        session_factory=_make_session_factory(engine),
        today_provider=lambda tz: date(2026, 5, 26),
        # no translation_llm — must not crash
    )

    assert sent is True
    text = bot.send_message.call_args.kwargs["text"]
    assert "Milk" in text, f"Expected English name in digest text, got: {text!r}"
