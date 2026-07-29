"""Household invite + membership service.

Pure, session-first functions (mirroring ``pantry_service`` / ``shopping_service``)
that manage who belongs to a household. Sharing of pantry, shopping lists, and
preferences is automatic once two users share a ``household_id`` — every domain
table is keyed by ``household_id`` — so this module only governs *membership*:
issuing single-use invites, redeeming them, listing members, and leaving/removing.

Callers pass an explicit ``now`` (no internal clock reads) for deterministic tests.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.billing.entitlement import get_or_create_subscription
from app.models import HouseholdInvite, User

INVITE_TTL_HOURS = 24
# secrets.token_urlsafe(9) -> 12 url-safe chars: short enough to type by hand
# for /join, long enough (~72 bits) to be unguessable.
_TOKEN_BYTES = 9


class InviteError(Exception):
    """Base class for membership errors."""


class InviteInvalid(InviteError):
    """Token does not exist, is expired, or was already redeemed."""


class AlreadyMember(InviteError):
    """The redeeming Telegram user already belongs to a household."""


class NotOwner(InviteError):
    """Actor is not the owner of the target household."""


class OwnerCannotLeave(InviteError):
    """The owner cannot /leave their own household."""


class MemberNotFound(InviteError):
    """No such member in this household."""


class CannotRemoveSelf(InviteError):
    """Owner tried to /remove themselves."""


class HouseholdFull(InviteError):
    """The household is at its subscription seat cap."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        super().__init__(f"household is full ({cap})")


def _utc_naive(value: datetime) -> datetime:
    """Normalize to naive UTC so comparisons match SQLite-stored datetimes."""
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value


@dataclass(frozen=True)
class InviteResult:
    token: str
    expires_at: datetime


def create_invite(
    session: Session,
    *,
    household_id: int,
    created_by: int,
    now: datetime,
    ttl_hours: int = INVITE_TTL_HOURS,
    max_uses: int | None = 1,
) -> InviteResult:
    """Issue a fresh invite for ``household_id``.

    ``max_uses=1`` (default) is single-use; ``None`` is reusable until expiry
    (e.g. onboarding a whole family at once). Any member may invite (the bot
    handler enforces that the caller is a member); role is not checked here.
    """
    issued_at = _utc_naive(now)
    _require_free_seat(session, household_id=household_id, now=issued_at)
    expires_at = issued_at + timedelta(hours=ttl_hours)
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    invite = HouseholdInvite(
        household_id=household_id,
        token=token,
        created_by=created_by,
        created_at=issued_at,
        expires_at=expires_at,
        max_uses=max_uses,
    )
    session.add(invite)
    session.commit()
    return InviteResult(token=token, expires_at=expires_at)


@dataclass(frozen=True)
class RedeemResult:
    user: User
    household_id: int


def redeem_invite(
    session: Session,
    *,
    token: str,
    telegram_user_id: int,
    chat_id: int,
    now: datetime,
    tz: str,
    digest_hour: int,
    llm_provider: str,
) -> RedeemResult:
    """Redeem ``token``, creating a new member ``User`` in the invite's household.

    Raises ``AlreadyMember`` if the user is already known, ``InviteInvalid`` if
    the token is missing/expired/already-used. The new user's per-user settings
    (tz/digest_hour/llm_provider) come from the deployment defaults; shared
    household data is inherited automatically via ``household_id``.
    """
    moment = _utc_naive(now)

    if session.get(User, telegram_user_id) is not None:
        raise AlreadyMember()

    invite = session.exec(
        select(HouseholdInvite).where(HouseholdInvite.token == token)
    ).first()
    if invite is None:
        raise InviteInvalid()
    exhausted = invite.max_uses is not None and invite.uses >= invite.max_uses
    if exhausted or _utc_naive(invite.expires_at) <= moment:
        raise InviteInvalid()

    _require_free_seat(session, household_id=invite.household_id, now=moment)

    user = User(
        telegram_id=telegram_user_id,
        chat_id=chat_id,
        household_id=invite.household_id,
        tz=tz,
        digest_hour=digest_hour,
        llm_provider=llm_provider,
        role="member",
        created_at=moment,
    )
    invite.uses += 1
    invite.redeemed_by = telegram_user_id
    invite.redeemed_at = moment
    session.add(user)
    session.add(invite)
    session.commit()
    session.refresh(user)
    return RedeemResult(user=user, household_id=invite.household_id)


def seats_used(session: Session, *, household_id: int) -> int:
    return len(
        session.exec(select(User).where(User.household_id == household_id)).all()
    )


def _require_free_seat(session: Session, *, household_id: int, now: datetime) -> None:
    sub = get_or_create_subscription(session, household_id=household_id, now=now)
    if seats_used(session, household_id=household_id) >= sub.seat_cap:
        raise HouseholdFull(sub.seat_cap)


def _revoke_invites_from(session: Session, *, created_by: int) -> None:
    """Delete every invite a user created.

    Called when that user leaves / is removed so their links can't admit
    strangers after they no longer belong to the household — otherwise
    ``/remove`` and ``/leave`` would not fully revoke access for up to the TTL.
    Multi-use links stay usable after their first redemption, so we cannot
    filter on ``redeemed_by``; all of the user's invites are dropped.
    """
    invites = session.exec(
        select(HouseholdInvite).where(HouseholdInvite.created_by == created_by)
    ).all()
    for invite in invites:
        session.delete(invite)


@dataclass(frozen=True)
class Member:
    telegram_id: int
    role: str


def list_members(session: Session, *, household_id: int) -> list[Member]:
    """Return household members, owner first, then by telegram_id."""
    users = session.exec(
        select(User).where(User.household_id == household_id)
    ).all()
    ordered = sorted(users, key=lambda u: (u.role != "owner", u.telegram_id))
    return [Member(telegram_id=u.telegram_id, role=u.role) for u in ordered]


def remove_member(
    session: Session,
    *,
    household_id: int,
    actor_id: int,
    target_id: int,
) -> Member:
    """Owner-only removal of another member. The removed user is deauthorized
    (their ``User`` row is deleted); shared household data is untouched."""
    actor = session.get(User, actor_id)
    if actor is None or actor.household_id != household_id or actor.role != "owner":
        raise NotOwner()
    if target_id == actor_id:
        raise CannotRemoveSelf()
    target = session.get(User, target_id)
    if target is None or target.household_id != household_id:
        raise MemberNotFound()
    removed = Member(telegram_id=target.telegram_id, role=target.role)
    _revoke_invites_from(session, created_by=target.telegram_id)
    session.delete(target)
    session.commit()
    return removed


def leave_household(session: Session, *, telegram_user_id: int) -> None:
    """A member leaves the household and is deauthorized. The owner cannot leave
    (they must remove members / tear down instead)."""
    user = session.get(User, telegram_user_id)
    if user is None:
        raise MemberNotFound()
    if user.role == "owner":
        raise OwnerCannotLeave()
    _revoke_invites_from(session, created_by=telegram_user_id)
    session.delete(user)
    session.commit()
