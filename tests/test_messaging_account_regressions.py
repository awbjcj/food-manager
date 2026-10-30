"""Cross-provider identity and scheduling regressions from the messaging review."""

import json
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import select

from app.invite_service import create_invite
from app.messaging.identity_service import find_user, telegram_user
from app.models import (
    ExternalIdentity,
    Household,
    HouseholdInvite,
    MessagingConnection,
    NotificationDestination,
    User,
)
from app.scheduler import schedule_user_digest
from tests.test_messaging import NOW, event, member
from tests.test_messaging import state as state  # noqa: PLC0414 - import pytest fixture


@pytest.fixture
def invitation(state):
    owner = member(state)
    with state.session_factory() as session:
        session.add(
            MessagingConnection(
                id="telegram-two",
                provider="telegram",
                label="Second Telegram bot",
                enabled=True,
                state="ready",
                external_account="12345",
                created_at=NOW,
                settings_json=json.dumps({"bootstrapUserId": "999"}),
            )
        )
        session.commit()
        invite = create_invite(
            session, household_id=owner.household_id, created_by=owner.id, now=NOW
        )
    return owner, invite.token


def existing_telegram_user(state, *, banned=False, active=False):
    with state.session_factory() as session:
        household = Household(created_at=NOW)
        session.add(household)
        session.flush()
        assert household.id is not None
        user = User(
            id=5001,
            telegram_id=42,
            chat_id=42,
            household_id=household.id,
            created_at=NOW,
            banned=banned,
            membership_active=active,
            membership_generation=7,
            lang="zh",
            tz="Europe/Paris",
            digest_hour=11,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("command", ["join", "start"])
async def test_another_telegram_bot_cannot_readmit_banned_identity(
    state, invitation, active, command
):
    _owner, token = invitation
    known = existing_telegram_user(state, banned=True, active=active)
    await state.process(
        event(f"{command} {token}", provider="telegram-two", user="42", channel="42"),
        operation="banned-join",
    )
    with state.session_factory() as session:
        users = session.exec(select(User)).all()
        assert len(users) == 2
        current = session.get(User, known.id)
        assert current is not None and current.banned
        assert current.household_id == known.household_id
        assert session.get(ExternalIdentity, ("telegram-two", "42")) is None
        invite = session.exec(
            select(HouseholdInvite).where(HouseholdInvite.token == token)
        ).one()
        assert invite.uses == 0


@pytest.mark.asyncio
async def test_another_telegram_bot_cannot_split_an_active_account(state, invitation):
    _owner, token = invitation
    known = existing_telegram_user(state, active=True)
    await state.process(
        event(f"join {token}", provider="telegram-two", user="42", channel="42"),
        operation="active-join",
    )
    with state.session_factory() as session:
        assert len(session.exec(select(User)).all()) == 2
        current = session.get(User, known.id)
        assert current is not None and current.household_id == known.household_id
        invite = session.exec(
            select(HouseholdInvite).where(HouseholdInvite.token == token)
        ).one()
        assert invite.uses == 0


@pytest.mark.asyncio
async def test_inactive_telegram_identity_rejoins_without_losing_preferences(
    state, invitation
):
    owner, token = invitation
    known = existing_telegram_user(state)
    incoming = event(f"join {token}", provider="telegram-two", user="42", channel="42")
    await state.process(incoming, operation="rejoin")
    with state.session_factory() as session:
        user = find_user(session, incoming.actor)
        assert user is not None and user.id == known.id
        assert user.telegram_id == 42 and user.chat_id == 42
        assert user.household_id == owner.household_id and user.membership_active
        assert (user.lang, user.tz, user.digest_hour, user.membership_generation) == (
            "zh",
            "Europe/Paris",
            11,
            7,
        )
        assert len(session.exec(select(User)).all()) == 2


@pytest.mark.asyncio
async def test_new_managed_telegram_member_keeps_canonical_identity(state, invitation):
    owner, token = invitation
    incoming = event(f"join {token}", provider="telegram-two", user="42", channel="42")
    await state.process(incoming, operation="new-join")
    with state.session_factory() as session:
        user = telegram_user(session, 42)
        assert user is not None and user.chat_id == 42
        linked = find_user(session, incoming.actor)
        assert linked is not None and linked.id == user.id
        assert user.household_id == owner.household_id and user.membership_active


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,actor,channel", [("s", "U1", "D1"), ("w", "15555555", "15555555")]
)
@pytest.mark.parametrize(
    "command,tz,hour,changed",
    [
        ("tz Europe/Paris", "Europe/Paris", 8, True),
        ("digest_at 11", "America/Detroit", 11, True),
        ("tz Not/A_Zone", "America/Detroit", 8, False),
        ("digest_at 25", "America/Detroit", 8, False),
    ],
)
async def test_shared_settings_update_the_existing_telegram_schedule(
    state, provider, actor, channel, command, tz, hour, changed
):
    user = member(state)
    with state.session_factory() as session:
        user = session.get(User, user.id)
        assert user is not None
        user.telegram_id = user.chat_id = 42
        session.add(user)
        session.add(
            ExternalIdentity(
                connection_id="w", external_user_id="15555555", user_id=user.id
            )
        )
        session.add(
            MessagingConnection(
                id="legacy-telegram",
                provider="telegram",
                label="Imported",
                enabled=True,
                created_at=NOW,
            )
        )
        session.add(
            NotificationDestination(
                connection_id="legacy-telegram",
                external_conversation_id="42",
                user_id=user.id,
                opted_in=True,
            )
        )
        session.commit()
        session.refresh(user)
    scheduler = AsyncIOScheduler()
    scheduler.start(paused=True)
    send = AsyncMock()
    calls = []

    def reschedule(updated):
        # The hook must observe already committed settings from a fresh session.
        with state.session_factory() as session:
            stored = session.get(User, updated.id)
            assert stored is not None and (stored.tz, stored.digest_hour) == (tz, hour)
        calls.append(updated.id)
        schedule_user_digest(scheduler, updated, send=send)

    state.reschedule = reschedule
    try:
        schedule_user_digest(scheduler, user, send=send)
        await state.process(
            event(command, provider=provider, user=actor, channel=channel),
            operation="settings",
        )
        job = scheduler.get_job(f"digest:{user.id}")
        assert job is not None
        fire = job.trigger.get_next_fire_time(None, NOW)
        assert fire is not None
        assert str(job.trigger.timezone) == tz
        assert fire.astimezone(ZoneInfo(tz)).hour == hour
        assert calls == ([user.id] if changed else [])
        with state.session_factory() as session:
            destinations = session.exec(
                select(NotificationDestination).where(
                    NotificationDestination.user_id == user.id
                )
            ).all()
            assert [d.connection_id for d in destinations if d.opted_in] == [
                "legacy-telegram"
            ]
    finally:
        scheduler.shutdown(wait=False)
    send.assert_not_awaited()
