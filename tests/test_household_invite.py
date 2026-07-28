from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.bot import (
    authorize_and_get_user,
    handle_household,
    handle_invite,
    handle_join,
    handle_leave,
    handle_remove,
    handle_start,
    resolve_authorization,
)
from app.invite_service import (
    AlreadyMember,
    CannotRemoveSelf,
    InviteInvalid,
    MemberNotFound,
    NotOwner,
    OwnerCannotLeave,
    create_invite,
    leave_household,
    list_members,
    redeem_invite,
    remove_member,
)
from app.models import Household, HouseholdInvite, User


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------
@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        now = datetime.now(UTC)
        household = Household(created_at=now)
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id == 1
        db.add(
            User(
                telegram_id=1,
                chat_id=11,
                household_id=household.id,
                role="owner",
                created_at=now,
            )
        )
        db.commit()
        yield db


def _msg(text: str, *, user_id=1, chat_id=1, chat_type="private"):
    msg = MagicMock()
    msg.from_user = MagicMock(id=user_id)
    msg.chat = MagicMock(id=chat_id, type=chat_type)
    msg.text = text
    msg.answer = AsyncMock()
    return msg


def _reply(msg) -> str:
    return msg.answer.await_args.args[0]


def _now():
    return datetime.now(UTC)


# --------------------------------------------------------------------------
# invite_service unit tests
# --------------------------------------------------------------------------
def test_create_invite_returns_token_and_future_expiry(session):
    now = _now()
    result = create_invite(session, household_id=1, created_by=1, now=now)
    assert result.token
    assert result.expires_at > now.replace(tzinfo=None)
    stored = session.exec(select(HouseholdInvite)).one()
    assert stored.household_id == 1
    assert stored.created_by == 1
    assert stored.redeemed_by is None


def test_redeem_invite_creates_member_and_marks_single_use(session):
    inv = create_invite(session, household_id=1, created_by=1, now=_now())
    result = redeem_invite(
        session,
        token=inv.token,
        telegram_user_id=2,
        chat_id=22,
        now=_now(),
        tz="America/Detroit",
        digest_hour=8,
        llm_provider="anthropic",
    )
    assert result.household_id == 1
    joined = session.get(User, 2)
    assert joined is not None
    assert joined.household_id == 1
    assert joined.role == "member"
    stored = session.exec(select(HouseholdInvite)).one()
    assert stored.redeemed_by == 2
    assert stored.redeemed_at is not None


def test_redeem_invite_is_single_use(session):
    inv = create_invite(session, household_id=1, created_by=1, now=_now())
    redeem_invite(
        session,
        token=inv.token,
        telegram_user_id=2,
        chat_id=22,
        now=_now(),
        tz="America/Detroit",
        digest_hour=8,
        llm_provider="anthropic",
    )
    with pytest.raises(InviteInvalid):
        redeem_invite(
            session,
            token=inv.token,
            telegram_user_id=3,
            chat_id=33,
            now=_now(),
            tz="America/Detroit",
            digest_hour=8,
            llm_provider="anthropic",
        )


def test_redeem_invite_rejects_expired(session):
    past = _now() - timedelta(hours=48)
    inv = create_invite(session, household_id=1, created_by=1, now=past)
    with pytest.raises(InviteInvalid):
        redeem_invite(
            session,
            token=inv.token,
            telegram_user_id=2,
            chat_id=22,
            now=_now(),
            tz="America/Detroit",
            digest_hour=8,
            llm_provider="anthropic",
        )


def test_redeem_invite_rejects_unknown_token(session):
    with pytest.raises(InviteInvalid):
        redeem_invite(
            session,
            token="nope",
            telegram_user_id=2,
            chat_id=22,
            now=_now(),
            tz="America/Detroit",
            digest_hour=8,
            llm_provider="anthropic",
        )


def test_redeem_invite_rejects_existing_member(session):
    inv = create_invite(session, household_id=1, created_by=1, now=_now())
    with pytest.raises(AlreadyMember):
        redeem_invite(
            session,
            token=inv.token,
            telegram_user_id=1,
            chat_id=11,
            now=_now(),
            tz="America/Detroit",
            digest_hour=8,
            llm_provider="anthropic",
        )


def test_list_members_orders_owner_first(session):
    inv = create_invite(session, household_id=1, created_by=1, now=_now())
    redeem_invite(
        session,
        token=inv.token,
        telegram_user_id=2,
        chat_id=22,
        now=_now(),
        tz="America/Detroit",
        digest_hour=8,
        llm_provider="anthropic",
    )
    members = list_members(session, household_id=1)
    assert [(m.telegram_id, m.role) for m in members] == [(1, "owner"), (2, "member")]


def test_remove_member_requires_owner(session):
    inv = create_invite(session, household_id=1, created_by=1, now=_now())
    redeem_invite(
        session,
        token=inv.token,
        telegram_user_id=2,
        chat_id=22,
        now=_now(),
        tz="America/Detroit",
        digest_hour=8,
        llm_provider="anthropic",
    )
    # member (2) cannot remove owner (1)
    with pytest.raises(NotOwner):
        remove_member(session, household_id=1, actor_id=2, target_id=1)
    # owner (1) removes member (2)
    removed = remove_member(session, household_id=1, actor_id=1, target_id=2)
    assert removed.telegram_id == 2
    assert session.get(User, 2) is None


def test_remove_member_self_and_missing(session):
    with pytest.raises(CannotRemoveSelf):
        remove_member(session, household_id=1, actor_id=1, target_id=1)
    with pytest.raises(MemberNotFound):
        remove_member(session, household_id=1, actor_id=1, target_id=999)


def test_leave_household_member_ok_owner_blocked(session):
    inv = create_invite(session, household_id=1, created_by=1, now=_now())
    redeem_invite(
        session,
        token=inv.token,
        telegram_user_id=2,
        chat_id=22,
        now=_now(),
        tz="America/Detroit",
        digest_hour=8,
        llm_provider="anthropic",
    )
    leave_household(session, telegram_user_id=2)
    assert session.get(User, 2) is None
    with pytest.raises(OwnerCannotLeave):
        leave_household(session, telegram_user_id=1)


def _add_member(session, telegram_id, *, role="member"):
    session.add(
        User(
            telegram_id=telegram_id,
            chat_id=telegram_id * 11,
            household_id=1,
            role=role,
            created_at=_now(),
        )
    )
    session.commit()


def test_remove_member_revokes_their_open_invites(session):
    # member 2 issues an invite, then is removed -> their open invite dies.
    _add_member(session, 2)
    inv = create_invite(session, household_id=1, created_by=2, now=_now())
    remove_member(session, household_id=1, actor_id=1, target_id=2)
    with pytest.raises(InviteInvalid):
        redeem_invite(
            session,
            token=inv.token,
            telegram_user_id=9,
            chat_id=99,
            now=_now(),
            tz="America/Detroit",
            digest_hour=8,
            llm_provider="anthropic",
        )


def test_leave_revokes_open_invites(session):
    _add_member(session, 2)
    inv = create_invite(session, household_id=1, created_by=2, now=_now())
    leave_household(session, telegram_user_id=2)
    with pytest.raises(InviteInvalid):
        redeem_invite(
            session,
            token=inv.token,
            telegram_user_id=9,
            chat_id=99,
            now=_now(),
            tz="America/Detroit",
            digest_hour=8,
            llm_provider="anthropic",
        )


def test_multi_use_invite_admits_several_until_expiry(session):
    inv = create_invite(
        session, household_id=1, created_by=1, now=_now(), max_uses=None
    )
    for uid in (2, 3, 4):
        result = redeem_invite(
            session,
            token=inv.token,
            telegram_user_id=uid,
            chat_id=uid * 11,
            now=_now(),
            tz="America/Detroit",
            digest_hour=8,
            llm_provider="anthropic",
        )
        assert result.household_id == 1
    assert {m.telegram_id for m in list_members(session, household_id=1)} == {
        1,
        2,
        3,
        4,
    }


def test_capped_invite_exhausts_after_max_uses(session):
    inv = create_invite(session, household_id=1, created_by=1, now=_now(), max_uses=2)
    redeem_invite(
        session,
        token=inv.token,
        telegram_user_id=2,
        chat_id=22,
        now=_now(),
        tz="America/Detroit",
        digest_hour=8,
        llm_provider="anthropic",
    )
    redeem_invite(
        session,
        token=inv.token,
        telegram_user_id=3,
        chat_id=33,
        now=_now(),
        tz="America/Detroit",
        digest_hour=8,
        llm_provider="anthropic",
    )
    with pytest.raises(InviteInvalid):
        redeem_invite(
            session,
            token=inv.token,
            telegram_user_id=4,
            chat_id=44,
            now=_now(),
            tz="America/Detroit",
            digest_hour=8,
            llm_provider="anthropic",
        )


def test_multi_use_invite_revoked_when_creator_leaves(session):
    _add_member(session, 2)
    inv = create_invite(
        session, household_id=1, created_by=2, now=_now(), max_uses=None
    )
    # one redemption, link still has uses left
    redeem_invite(
        session,
        token=inv.token,
        telegram_user_id=5,
        chat_id=55,
        now=_now(),
        tz="America/Detroit",
        digest_hour=8,
        llm_provider="anthropic",
    )
    leave_household(session, telegram_user_id=2)
    with pytest.raises(InviteInvalid):
        redeem_invite(
            session,
            token=inv.token,
            telegram_user_id=6,
            chat_id=66,
            now=_now(),
            tz="America/Detroit",
            digest_hour=8,
            llm_provider="anthropic",
        )


def test_revoke_leaves_other_members_invites_intact(session):
    # Removing member 2 must not touch invites created by the owner.
    _add_member(session, 2)
    owner_inv = create_invite(session, household_id=1, created_by=1, now=_now())
    remove_member(session, household_id=1, actor_id=1, target_id=2)
    result = redeem_invite(
        session,
        token=owner_inv.token,
        telegram_user_id=9,
        chat_id=99,
        now=_now(),
        tz="America/Detroit",
        digest_hour=8,
        llm_provider="anthropic",
    )
    assert result.household_id == 1


# --------------------------------------------------------------------------
# Authorization gate
# --------------------------------------------------------------------------
def test_resolve_authorization_admits_member_not_bootstrap(session):
    # add a member who is NOT the bootstrap id
    session.add(
        User(
            telegram_id=2, chat_id=22, household_id=1, role="member", created_at=_now()
        )
    )
    session.commit()
    status = resolve_authorization(session, allowed_user_id=1, telegram_user_id=2)
    assert status.allowed is True
    assert status.user is not None
    assert status.is_bootstrap is False


def test_resolve_authorization_rejects_stranger(session):
    status = resolve_authorization(session, allowed_user_id=1, telegram_user_id=999)
    assert status.allowed is False
    assert status.user is None


def test_authorize_admits_second_household_member(session):
    session.add(
        User(
            telegram_id=2, chat_id=22, household_id=1, role="member", created_at=_now()
        )
    )
    session.commit()
    decision = authorize_and_get_user(
        session,
        allowed_user_id=1,
        telegram_user_id=2,
        chat_id=22,
        chat_type="private",
    )
    assert decision.allowed is True
    assert decision.user is not None
    assert decision.user.household_id == 1


def test_authorize_rejects_stranger_without_invite(session):
    decision = authorize_and_get_user(
        session,
        allowed_user_id=1,
        telegram_user_id=777,
        chat_id=77,
        chat_type="private",
    )
    assert decision.allowed is False
    assert decision.reason == "not authorized"


# --------------------------------------------------------------------------
# Bot handler tests
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_handle_invite_replies_with_link_and_code(session, monkeypatch):
    monkeypatch.setattr("app.handler_support.ALLOWED_TELEGRAM_USER_ID", 1)
    bot = MagicMock()
    bot.get_me = AsyncMock(return_value=MagicMock(username="PantryBot"))
    msg = _msg("/invite", user_id=1)
    await handle_invite(msg, session_factory=lambda: session, bot=bot)
    reply = _reply(msg)
    token = session.exec(select(HouseholdInvite)).one().token
    assert token in reply
    assert f"https://t.me/PantryBot?start={token}" in reply


@pytest.mark.asyncio
async def test_handle_invite_by_non_owner_member(session, monkeypatch):
    # Policy: any member may invite (not just the owner).
    monkeypatch.setattr("app.handler_support.ALLOWED_TELEGRAM_USER_ID", 1)
    session.add(
        User(
            telegram_id=2, chat_id=22, household_id=1, role="member", created_at=_now()
        )
    )
    session.commit()
    bot = MagicMock()
    bot.get_me = AsyncMock(return_value=MagicMock(username="PantryBot"))
    msg = _msg("/invite", user_id=2, chat_id=22)
    await handle_invite(msg, session_factory=lambda: session, bot=bot)
    stored = session.exec(select(HouseholdInvite)).one()
    assert stored.created_by == 2
    assert stored.token in _reply(msg)


@pytest.mark.asyncio
async def test_handle_invite_family_creates_reusable(session, monkeypatch):
    monkeypatch.setattr("app.handler_support.ALLOWED_TELEGRAM_USER_ID", 1)
    bot = MagicMock()
    bot.get_me = AsyncMock(return_value=MagicMock(username="PantryBot"))
    msg = _msg("/invite family", user_id=1)
    await handle_invite(msg, session_factory=lambda: session, bot=bot)
    stored = session.exec(select(HouseholdInvite)).one()
    assert stored.max_uses is None
    assert "reusable" in _reply(msg).lower()


@pytest.mark.asyncio
async def test_handle_invite_rejects_bad_mode(session, monkeypatch):
    monkeypatch.setattr("app.handler_support.ALLOWED_TELEGRAM_USER_ID", 1)
    bot = MagicMock()
    bot.get_me = AsyncMock(return_value=MagicMock(username="PantryBot"))
    msg = _msg("/invite everyone", user_id=1)
    await handle_invite(msg, session_factory=lambda: session, bot=bot)
    assert "usage" in _reply(msg).lower()
    assert session.exec(select(HouseholdInvite)).all() == []


@pytest.mark.asyncio
async def test_handle_join_notifies_existing_members(session, monkeypatch):
    monkeypatch.setattr("app.handler_support.ALLOWED_TELEGRAM_USER_ID", 1)
    session.add(
        User(
            telegram_id=2, chat_id=22, household_id=1, role="member", created_at=_now()
        )
    )
    session.commit()
    inv = create_invite(session, household_id=1, created_by=1, now=_now())
    bot = MagicMock()
    bot.send_message = AsyncMock()
    msg = _msg(f"/join {inv.token}", user_id=3, chat_id=33)
    await handle_join(msg, session_factory=lambda: session, bot=bot)
    # existing members 1 (chat 11) and 2 (chat 22) are notified, joiner 3 is not
    notified = {c.args[0] for c in bot.send_message.await_args_list}
    assert notified == {11, 22}
    assert "3" in bot.send_message.await_args_list[0].args[1]


@pytest.mark.asyncio
async def test_handle_join_redeems_invite(session, monkeypatch):
    monkeypatch.setattr("app.handler_support.ALLOWED_TELEGRAM_USER_ID", 1)
    monkeypatch.setattr("app.handler_support.DEFAULT_LLM_PROVIDER", "deepseek")
    inv = create_invite(session, household_id=1, created_by=1, now=_now())
    created = MagicMock()
    msg = _msg(f"/join {inv.token}", user_id=2, chat_id=22)
    await handle_join(
        msg,
        session_factory=lambda: session,
        on_user_created=created,
    )
    joined = session.get(User, 2)
    assert joined is not None and joined.household_id == 1 and joined.role == "member"
    assert joined.llm_provider == "deepseek"
    created.assert_called_once()


@pytest.mark.asyncio
async def test_handle_join_bad_code_reports_invalid(session, monkeypatch):
    monkeypatch.setattr("app.handler_support.ALLOWED_TELEGRAM_USER_ID", 1)
    msg = _msg("/join bogus", user_id=2, chat_id=22)
    await handle_join(msg, session_factory=lambda: session)
    assert "invalid" in _reply(msg).lower()
    assert session.get(User, 2) is None


@pytest.mark.asyncio
async def test_handle_join_expired_token_reports_invalid(session, monkeypatch):
    monkeypatch.setattr("app.handler_support.ALLOWED_TELEGRAM_USER_ID", 1)
    inv = create_invite(
        session, household_id=1, created_by=1, now=_now() - timedelta(hours=48)
    )
    msg = _msg(f"/join {inv.token}", user_id=2, chat_id=22)
    await handle_join(msg, session_factory=lambda: session)
    assert "invalid" in _reply(msg).lower()
    assert session.get(User, 2) is None


@pytest.mark.asyncio
async def test_handle_join_already_member_reports(session, monkeypatch):
    monkeypatch.setattr("app.handler_support.ALLOWED_TELEGRAM_USER_ID", 1)
    inv = create_invite(session, household_id=1, created_by=1, now=_now())
    # owner (already a member) tries to /join their own household
    msg = _msg(f"/join {inv.token}", user_id=1)
    await handle_join(msg, session_factory=lambda: session)
    assert "already" in _reply(msg).lower()


@pytest.mark.asyncio
async def test_handle_start_with_token_redeems(session, monkeypatch):
    monkeypatch.setattr("app.handler_support.ALLOWED_TELEGRAM_USER_ID", 1)
    inv = create_invite(session, household_id=1, created_by=1, now=_now())
    created = MagicMock()
    msg = _msg(f"/start {inv.token}", user_id=3, chat_id=33)
    await handle_start(
        msg,
        session_factory=lambda: session,
        on_user_created=created,
    )
    joined = session.get(User, 3)
    assert joined is not None and joined.household_id == 1
    created.assert_called_once()


@pytest.mark.asyncio
async def test_handle_household_lists_members(session, monkeypatch):
    monkeypatch.setattr("app.handler_support.ALLOWED_TELEGRAM_USER_ID", 1)
    session.add(
        User(
            telegram_id=2, chat_id=22, household_id=1, role="member", created_at=_now()
        )
    )
    session.commit()
    msg = _msg("/household", user_id=1)
    await handle_household(msg, session_factory=lambda: session)
    reply = _reply(msg)
    assert "1" in reply and "2" in reply
    assert "owner" in reply and "member" in reply


@pytest.mark.asyncio
async def test_handle_household_solo_owner_marks_you(session, monkeypatch):
    monkeypatch.setattr("app.handler_support.ALLOWED_TELEGRAM_USER_ID", 1)
    msg = _msg("/household", user_id=1)
    await handle_household(msg, session_factory=lambda: session)
    reply = _reply(msg)
    assert "(1)" in reply  # one member
    assert "(you)" in reply
    assert "owner" in reply


@pytest.mark.asyncio
async def test_handle_leave_member_unschedules(session, monkeypatch):
    monkeypatch.setattr("app.handler_support.ALLOWED_TELEGRAM_USER_ID", 1)
    session.add(
        User(
            telegram_id=2, chat_id=22, household_id=1, role="member", created_at=_now()
        )
    )
    session.commit()
    unschedule = MagicMock()
    msg = _msg("/leave", user_id=2, chat_id=22)
    await handle_leave(msg, session_factory=lambda: session, unschedule=unschedule)
    assert session.get(User, 2) is None
    unschedule.assert_called_once_with(2)


@pytest.mark.asyncio
async def test_handle_leave_owner_blocked(session, monkeypatch):
    monkeypatch.setattr("app.handler_support.ALLOWED_TELEGRAM_USER_ID", 1)
    unschedule = MagicMock()
    msg = _msg("/leave", user_id=1)
    await handle_leave(msg, session_factory=lambda: session, unschedule=unschedule)
    assert session.get(User, 1) is not None
    unschedule.assert_not_called()
    assert "owner" in _reply(msg).lower()


@pytest.mark.asyncio
async def test_handle_remove_owner_removes_member(session, monkeypatch):
    monkeypatch.setattr("app.handler_support.ALLOWED_TELEGRAM_USER_ID", 1)
    session.add(
        User(
            telegram_id=2, chat_id=22, household_id=1, role="member", created_at=_now()
        )
    )
    session.commit()
    unschedule = MagicMock()
    msg = _msg("/remove 2", user_id=1)
    await handle_remove(msg, session_factory=lambda: session, unschedule=unschedule)
    assert session.get(User, 2) is None
    unschedule.assert_called_once_with(2)


@pytest.mark.asyncio
async def test_handle_remove_non_owner_rejected(session, monkeypatch):
    monkeypatch.setattr("app.handler_support.ALLOWED_TELEGRAM_USER_ID", 1)
    session.add(
        User(
            telegram_id=2, chat_id=22, household_id=1, role="member", created_at=_now()
        )
    )
    session.add(
        User(
            telegram_id=3, chat_id=33, household_id=1, role="member", created_at=_now()
        )
    )
    session.commit()
    unschedule = MagicMock()
    msg = _msg("/remove 3", user_id=2, chat_id=22)
    await handle_remove(msg, session_factory=lambda: session, unschedule=unschedule)
    assert session.get(User, 3) is not None  # not removed
    unschedule.assert_not_called()
