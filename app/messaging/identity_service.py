"""Resolve provider identities without granting membership from installation."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel import Session, col, select

from app.household_service import provision_solo_household
from app.messaging.contracts import ActorRef, ConversationRef, ErrorCode, MessagingError
from app.models import (
    ConversationBinding,
    ExternalIdentity,
    IdentityLink,
    MessagingConnection,
    MessagingOutbox,
    NotificationDestination,
    User,
)
from app.pending_service import utc_naive


@dataclass(frozen=True)
class AuthorizationStamp:
    user_id: int
    household_id: int
    generation: int


def stamped_user(session: Session, stamp: object) -> User | None:
    if not isinstance(stamp, AuthorizationStamp):
        return None
    user = session.get(User, stamp.user_id, populate_existing=True)
    if (
        user is None
        or user.banned
        or not user.membership_active
        or user.household_id != stamp.household_id
        or user.membership_generation != stamp.generation
    ):
        return None
    return user


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def telegram_user(session: Session, telegram_id: int) -> User | None:
    return session.exec(select(User).where(User.telegram_id == telegram_id)).first()


def find_user(session: Session, actor: ActorRef) -> User | None:
    identity = session.get(
        ExternalIdentity, (actor.connection_id, actor.external_user_id)
    )
    return session.get(User, identity.user_id) if identity else None


def authorize(
    session: Session,
    *,
    actor: ActorRef,
    conversation: ConversationRef,
    now: datetime,
    open_registration: bool,
    hosted: bool,
    default_provider: str,
    create: bool = False,
) -> User:
    connection = session.get(MessagingConnection, actor.connection_id)
    if connection is None or not connection.enabled:
        raise MessagingError(ErrorCode.FORBIDDEN, "Connection is disabled")
    settings = json.loads(connection.settings_json)
    bootstrap = actor.external_user_id == settings.get("bootstrapUserId")
    user = find_user(session, actor)
    if user is None and connection.provider == "telegram":
        try:
            user = telegram_user(session, int(actor.external_user_id))
        except ValueError:
            pass
        if user is not None:
            session.add(
                ExternalIdentity(
                    connection_id=actor.connection_id,
                    external_user_id=actor.external_user_id,
                    user_id=user.id,
                )
            )
            session.flush()
    if user and user.banned:
        raise MessagingError(ErrorCode.FORBIDDEN, "Account is unavailable")
    if not hosted and not bootstrap:
        # Explicitly linked identities of the deployment's bootstrap user work
        # in local mode; unrelated existing memberships do not.
        owner_identity = session.get(
            ExternalIdentity, (actor.connection_id, settings.get("bootstrapUserId", ""))
        )
        linked_bootstrap = user and owner_identity and owner_identity.user_id == user.id
        if not linked_bootstrap:
            bootstrap_users = set()
            for configured in session.exec(select(MessagingConnection)).all():
                external = json.loads(configured.settings_json).get(
                    "bootstrapUserId", ""
                )
                identity = session.get(ExternalIdentity, (configured.id, external))
                if identity:
                    bootstrap_users.add(identity.user_id)
            if user is None or user.id not in bootstrap_users:
                raise MessagingError(ErrorCode.FORBIDDEN, "This deployment is private")
    if user is None:
        if (
            not create
            or conversation.kind != "direct"
            or not (bootstrap or (hosted and open_registration))
        ):
            raise MessagingError(
                ErrorCode.FORBIDDEN,
                "Start or join a household in a direct conversation",
            )
        user = User(
            household_id=0,
            role="owner",
            llm_provider=default_provider,
            created_at=utc_naive(now),
        )
        if connection.provider == "telegram":
            user.telegram_id = int(actor.external_user_id)
            user.chat_id = int(conversation.external_conversation_id)
        provision_solo_household(session, user, created_at=utc_naive(now))
        session.add(
            ExternalIdentity(
                connection_id=actor.connection_id,
                external_user_id=actor.external_user_id,
                user_id=user.id,
            )
        )
        session.flush()
    if not user.membership_active:
        raise MessagingError(ErrorCode.FORBIDDEN, "Join a household before continuing")
    if conversation.kind == "shared":
        binding = session.get(
            ConversationBinding,
            (actor.connection_id, conversation.external_conversation_id),
        )
        if not hosted or binding is None or binding.household_id != user.household_id:
            raise MessagingError(
                ErrorCode.FORBIDDEN, "This conversation is not bound to your household"
            )
    return user


def observe_destination(
    session: Session, *, user: User, conversation: ConversationRef, now: datetime
):
    if conversation.kind != "direct":
        return None
    destination = session.exec(
        select(NotificationDestination).where(
            NotificationDestination.connection_id == conversation.connection_id,
            NotificationDestination.external_conversation_id
            == conversation.external_conversation_id,
        )
    ).first()
    if destination is None:
        destination = NotificationDestination(
            user_id=user.id,
            connection_id=conversation.connection_id,
            external_conversation_id=conversation.external_conversation_id,
        )
    if destination.user_id != user.id:
        raise MessagingError(
            ErrorCode.FORBIDDEN, "Destination belongs to another account"
        )
    destination.last_customer_message_at = max(
        destination.last_customer_message_at or utc_naive(now), utc_naive(now)
    )
    session.add(destination)
    session.flush()
    return destination


def select_destination(
    session: Session, *, user: User, destination: NotificationDestination, enabled: bool
):
    if destination.user_id != user.id:
        raise MessagingError(
            ErrorCode.FORBIDDEN, "Destination belongs to another account"
        )
    if enabled:
        for row in session.exec(
            select(NotificationDestination).where(
                NotificationDestination.user_id == user.id
            )
        ).all():
            row.opted_in = False
            session.add(row)
    destination.opted_in = enabled
    session.add(destination)
    session.flush()


def revoke_membership(session: Session, user: User) -> None:
    user.membership_active = False
    user.membership_generation += 1
    session.add(user)
    for row in session.exec(
        select(NotificationDestination).where(
            NotificationDestination.user_id == user.id
        )
    ).all():
        row.opted_in = False
        session.add(row)
    for row in session.exec(
        select(MessagingOutbox).where(
            MessagingOutbox.user_id == user.id,
            col(MessagingOutbox.status).in_(["pending", "retry"]),
        )
    ).all():
        row.status, row.error_code = "suppressed", "FORBIDDEN"
        session.add(row)


def create_link(session: Session, *, user: User, actor: ActorRef, now: datetime) -> str:
    token = secrets.token_urlsafe(24)
    session.add(
        IdentityLink(
            token_hash=token_hash(token),
            user_id=user.id,
            origin_connection_id=actor.connection_id,
            expires_at=utc_naive(now) + timedelta(minutes=10),
        )
    )
    session.flush()
    return token


def redeem_link(
    session: Session, *, token: str, actor: ActorRef, now: datetime
) -> None:
    link = session.get(IdentityLink, token_hash(token))
    if (
        link is None
        or link.used
        or link.expires_at <= utc_naive(now)
        or link.target_external_user_id
    ):
        raise MessagingError(ErrorCode.INVALID_INPUT, "Link is expired or already used")
    source = session.get(User, link.user_id)
    if source is None or source.banned or not source.membership_active:
        raise MessagingError(ErrorCode.FORBIDDEN, "Account is unavailable")
    existing = find_user(session, actor)
    if existing is not None and existing.id != link.user_id:
        raise MessagingError(ErrorCode.CONFLICT, "This identity already has an account")
    link.target_connection_id, link.target_external_user_id = (
        actor.connection_id,
        actor.external_user_id,
    )
    session.add(link)
    session.flush()


def confirm_link(
    session: Session, *, token: str, user: User, actor: ActorRef, now: datetime
) -> None:
    link = session.get(IdentityLink, token_hash(token))
    if (
        link is None
        or link.used
        or link.expires_at <= utc_naive(now)
        or link.user_id != user.id
        or link.origin_connection_id != actor.connection_id
        or not link.target_connection_id
        or not link.target_external_user_id
    ):
        raise MessagingError(
            ErrorCode.INVALID_INPUT, "Link is not ready for confirmation"
        )
    target = ActorRef(
        connection_id=link.target_connection_id,
        external_user_id=link.target_external_user_id,
    )
    existing = find_user(session, target)
    if existing and existing.id != user.id:
        raise MessagingError(ErrorCode.CONFLICT, "This identity already has an account")
    if existing is None:
        session.add(
            ExternalIdentity(
                connection_id=target.connection_id,
                external_user_id=target.external_user_id,
                user_id=user.id,
            )
        )
    connection = session.get(MessagingConnection, target.connection_id)
    if connection and connection.provider == "telegram":
        telegram_id = int(target.external_user_id)
        if user.telegram_id is not None and user.telegram_id != telegram_id:
            raise MessagingError(
                ErrorCode.CONFLICT,
                "This account already has a different Telegram identity",
            )
        occupant = telegram_user(session, telegram_id)
        if occupant and occupant.id != user.id:
            raise MessagingError(
                ErrorCode.CONFLICT, "This Telegram identity already has an account"
            )
        user.telegram_id, user.chat_id = telegram_id, telegram_id
        session.add(user)
    link.used = True
    session.add(link)
    session.flush()
