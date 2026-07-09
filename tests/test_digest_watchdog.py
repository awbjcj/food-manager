from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import Household, PantryItem, User
from app.scheduler import catch_up_missed_digests, send_digest_once

TODAY = date(2026, 7, 8)


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
                chat_id=1,
                household_id=household.id,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    return make


def _seed_due_item(session_factory) -> None:
    with session_factory() as db:
        user = db.get(User, 1)
        assert user is not None
        db.add(
            PantryItem(
                household_id=user.household_id,
                raw_name="milk",
                normalized_name="milk",
                category="dairy",
                qty=1.0,
                purchased_on=TODAY - timedelta(days=1),
                shelf_life_days=2,
                shelf_life_source="llm",
                ingest_shelf_life_source="llm",
                expires_on=TODAY + timedelta(days=1),
                status="active",
                created_via="manual",
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()


@pytest.mark.asyncio
async def test_send_digest_marks_last_digest_date(session_factory):
    _seed_due_item(session_factory)
    sent = await send_digest_once(
        user_id=1,
        bot=AsyncMock(),
        session_factory=session_factory,
        today_provider=lambda tz: TODAY,
    )
    assert sent is True
    with session_factory() as db:
        assert db.get(User, 1).last_digest_date == TODAY


@pytest.mark.asyncio
async def test_silent_day_also_marks_last_digest_date(session_factory):
    sent = await send_digest_once(
        user_id=1,
        bot=AsyncMock(),
        session_factory=session_factory,
        today_provider=lambda tz: TODAY,
    )
    assert sent is False
    with session_factory() as db:
        assert db.get(User, 1).last_digest_date == TODAY


@pytest.mark.asyncio
async def test_catch_up_sends_missed_digest(session_factory):
    send = AsyncMock()
    count = await catch_up_missed_digests(
        session_factory=session_factory,
        send=send,
        now_provider=lambda tz: datetime(2026, 7, 8, 9, 30),  # past digest_hour 8
    )
    assert count == 1
    send.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_catch_up_skips_before_digest_hour(session_factory):
    send = AsyncMock()
    count = await catch_up_missed_digests(
        session_factory=session_factory,
        send=send,
        now_provider=lambda tz: datetime(2026, 7, 8, 7, 0),
    )
    assert count == 0
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_catch_up_skips_already_sent_today(session_factory):
    with session_factory() as db:
        user = db.get(User, 1)
        user.last_digest_date = date(2026, 7, 8)
        db.add(user)
        db.commit()
    send = AsyncMock()
    count = await catch_up_missed_digests(
        session_factory=session_factory,
        send=send,
        now_provider=lambda tz: datetime(2026, 7, 8, 9, 30),
    )
    assert count == 0
    send.assert_not_awaited()
