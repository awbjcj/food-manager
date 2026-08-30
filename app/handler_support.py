from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlmodel import Session

from app.client_set import PerUserClients
from app.group_service import get_group_binding
from app.household_service import provision_solo_household, restore_household_for_user
from app.models import Household, User
from app.providers import ALL_PROVIDERS, supports

DEFAULT_TZ = "America/Detroit"
DEFAULT_DIGEST_HOUR = 8
DEFAULT_LLM_PROVIDER = "anthropic"
ALLOWED_TELEGRAM_USER_ID: int = 0
OPEN_REGISTRATION: bool = False
# ``bin.run`` sets this from Settings. True preserves the established behavior
# for directly constructed handlers/tests; the shipped local configuration
# explicitly sets it false before polling begins.
MULTI_TENANT_ENABLED: bool = True

SessionFactory = Callable[[], Session]
NowProvider = Callable[[str], datetime]
log = logging.getLogger(__name__)


@dataclass
class AuthDecision:
    allowed: bool
    user: User | None
    created: bool
    reason: str
    household: Household | None = None


@dataclass
class AuthStatus:
    allowed: bool
    user: User | None
    is_bootstrap: bool


def resolve_authorization(
    session: Session,
    *,
    allowed_user_id: int,
    telegram_user_id: int,
    open_registration: bool = False,
    multi_tenant_enabled: bool = True,
) -> AuthStatus:
    existing = session.get(User, telegram_user_id)
    if existing is not None:
        if not multi_tenant_enabled and telegram_user_id != allowed_user_id:
            return AuthStatus(False, None, is_bootstrap=False)
        if existing.banned:
            return AuthStatus(False, None, is_bootstrap=False)
        return AuthStatus(True, existing, is_bootstrap=False)
    if (
        multi_tenant_enabled and open_registration
    ) or telegram_user_id == allowed_user_id:
        return AuthStatus(True, None, is_bootstrap=True)
    return AuthStatus(False, None, is_bootstrap=False)


def authorize_and_get_user(
    session: Session,
    *,
    allowed_user_id: int,
    telegram_user_id: int,
    chat_id: int,
    chat_type: str,
    open_registration: bool | None = None,
    multi_tenant_enabled: bool = True,
) -> AuthDecision:
    status = resolve_authorization(
        session,
        allowed_user_id=allowed_user_id,
        telegram_user_id=telegram_user_id,
        open_registration=OPEN_REGISTRATION
        if open_registration is None
        else open_registration,
        multi_tenant_enabled=multi_tenant_enabled,
    )
    if not status.allowed:
        return AuthDecision(False, None, False, "not authorized")
    if chat_type in {"group", "supergroup"}:
        if not multi_tenant_enabled:
            return AuthDecision(False, None, False, "group chats require hosted mode")
        if status.user is None:
            return AuthDecision(
                False, None, False, "join a household in private chat before using /bind"
            )
        binding = get_group_binding(session, chat_id=chat_id)
        if binding is None:
            return AuthDecision(False, None, False, "this group is not bound; use /bind")
        if binding.household_id != status.user.household_id:
            return AuthDecision(
                False, None, False, "you are not a member of this group's household"
            )
        household = session.get(Household, status.user.household_id)
        if household is None:
            household = restore_household_for_user(
                session, status.user, created_at=datetime.now(UTC)
            )
        return AuthDecision(True, status.user, False, "ok", household=household)
    if chat_type != "private":
        return AuthDecision(False, None, False, "unsupported chat type")

    if status.user is not None:
        household = session.get(Household, status.user.household_id)
        if household is None:
            household = restore_household_for_user(
                session, status.user, created_at=datetime.now(UTC)
            )
        return AuthDecision(True, status.user, False, "ok", household=household)

    now = datetime.now(UTC)
    user = User(
        telegram_id=telegram_user_id,
        chat_id=chat_id,
        household_id=0,
        tz=DEFAULT_TZ,
        digest_hour=DEFAULT_DIGEST_HOUR,
        llm_provider=DEFAULT_LLM_PROVIDER,
        role="owner",
        created_at=now,
    )
    household = provision_solo_household(session, user, created_at=now)
    return AuthDecision(True, user, True, "created", household=household)


def noop_user_created(user: User) -> None:
    pass


def require_user(user: User | None) -> User:
    assert user is not None
    return user


def require_today(today: date | None) -> date:
    assert today is not None
    return today


def available_llm_providers(clients: PerUserClients) -> tuple[str, ...]:
    return clients.available_text_providers


def render_llm_status(user: User, clients: PerUserClients) -> str:
    available = available_llm_providers(clients)
    lines = [
        f"LLM provider: {user.llm_provider}",
        f"Available: {', '.join(available) if available else 'none'}",
    ]
    image_incapable = [
        provider for provider in available if not supports(provider, "image")
    ]
    if image_incapable:
        verb = "is" if len(image_incapable) == 1 else "are"
        lines.append(
            f"Note: {', '.join(image_incapable)} {verb} image-incapable; "
            "photos use a capable provider."
        )
    lines.append(f"Usage: /llm [{'|'.join(ALL_PROVIDERS)}]")
    return "\n".join(lines)


def authorized_callback_user(session: Session, telegram_id: int) -> User | None:
    status = resolve_authorization(
        session,
        allowed_user_id=ALLOWED_TELEGRAM_USER_ID,
        telegram_user_id=telegram_id,
        open_registration=OPEN_REGISTRATION,
        multi_tenant_enabled=MULTI_TENANT_ENABLED,
    )
    return status.user if status.allowed else None


def authorized_callback_query_user(session: Session, callback) -> User | None:
    message = getattr(callback, "message", None)
    chat = getattr(message, "chat", None)
    chat_type = getattr(chat, "type", None)
    if chat_type not in {"group", "supergroup"}:
        chat_type = "private"
    chat_id = getattr(chat, "id", callback.from_user.id)
    decision = authorize_and_get_user(
        session,
        allowed_user_id=ALLOWED_TELEGRAM_USER_ID,
        telegram_user_id=callback.from_user.id,
        chat_id=chat_id,
        chat_type=chat_type,
        open_registration=OPEN_REGISTRATION,
        multi_tenant_enabled=MULTI_TENANT_ENABLED,
    )
    return decision.user if decision.allowed else None


async def guard(
    msg,
    session: Session,
    *,
    on_user_created: Callable[[User], None] = noop_user_created,
) -> User | None:
    decision = authorize_and_get_user(
        session,
        allowed_user_id=ALLOWED_TELEGRAM_USER_ID,
        telegram_user_id=msg.from_user.id,
        chat_id=msg.chat.id,
        chat_type=msg.chat.type,
        multi_tenant_enabled=MULTI_TENANT_ENABLED,
    )
    if not decision.allowed:
        log.info(
            "unauthorized_update_rejected",
            extra={"telegram_user_id": msg.from_user.id, "chat_id": msg.chat.id},
        )
        await msg.answer(decision.reason)
        return None
    user = require_user(decision.user)
    if decision.created:
        on_user_created(user)
    return user


@dataclass
class RequestContext:
    session: Session
    user: User
    today: date | None


@asynccontextmanager
async def request(
    msg,
    *,
    session_factory: SessionFactory,
    on_user_created: Callable[[User], None] = noop_user_created,
    now_provider: NowProvider | None = None,
) -> AsyncIterator[RequestContext | None]:
    with session_factory() as session:
        user = await guard(msg, session, on_user_created=on_user_created)
        if user is None:
            yield None
            return
        today = now_provider(user.tz).date() if now_provider is not None else None
        yield RequestContext(session=session, user=user, today=today)
