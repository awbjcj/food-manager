from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from aiogram import Bot
from sqlmodel import Session

from app.commands import (
    CommandError,
    parse_digest_at,
    parse_invite_mode,
    parse_invite_token,
    parse_lang,
    parse_member_id,
    parse_tz,
)
from app.i18n import DEFAULT_LANG, LANGS, t
import app.handler_support as handler_support
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
from app.models import User

DEFAULT_TZ = "America/Detroit"
DEFAULT_DIGEST_HOUR = 8
DEFAULT_LLM_PROVIDER = "anthropic"

_SessionFactory = Callable[[], Session]
log = logging.getLogger(__name__)


_noop_user_created = handler_support.noop_user_created
_require_user = handler_support.require_user
_request = handler_support.request


def _start_token(text: str | None) -> Optional[str]:
    """Extract a deep-link payload from ``/start <token>`` (None if absent)."""
    parts = (text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        return parts[1].strip()
    return None


async def _notify_household_join(
    bot, session: Session, *, household_id: int, joiner_id: int
) -> None:
    """Best-effort DM to existing members announcing a new joiner.

    A blocked/unreachable chat must never break the join, so each send is
    guarded; the joiner themselves is skipped."""
    if bot is None:
        return
    for member in list_members(session, household_id=household_id):
        if member.telegram_id == joiner_id:
            continue
        member_user = session.get(User, member.telegram_id)
        if member_user is None:
            continue
        try:
            await bot.send_message(
                member_user.chat_id,
                t("household.member_joined", member_user.lang, id=joiner_id),
            )
        except Exception:
            log.warning(
                "member_join_notify_failed",
                extra={"telegram_user_id": member.telegram_id},
            )


async def _try_redeem_invite(
    msg,
    session: Session,
    *,
    token: str,
    on_user_created: Callable[[User], None],
    bot=None,
) -> bool:
    """Attempt to join a household via ``token``. Returns True if the message
    was handled (joined, or a join-specific error was reported)."""
    if msg.chat.type != "private":
        await msg.answer("this bot only works in private chat")
        return True
    try:
        result = redeem_invite(
            session,
            token=token,
            telegram_user_id=msg.from_user.id,
            chat_id=msg.chat.id,
            now=datetime.now(timezone.utc),
            tz=DEFAULT_TZ,
            digest_hour=DEFAULT_DIGEST_HOUR,
            llm_provider=DEFAULT_LLM_PROVIDER,
        )
    except AlreadyMember:
        # The sender already has a row, so honour their language preference.
        existing = session.get(User, msg.from_user.id)
        lang = existing.lang if existing is not None else DEFAULT_LANG
        await msg.answer(t("join.already_member", lang))
        return True
    except InviteInvalid:
        # No User row yet for a newcomer, so fall back to the default language.
        await msg.answer(t("join.invalid", DEFAULT_LANG))
        return True
    on_user_created(result.user)
    await msg.answer(t("join.success", result.user.lang))
    await _notify_household_join(
        bot, session, household_id=result.household_id, joiner_id=msg.from_user.id
    )
    return True


async def handle_start(
    msg,
    *,
    session_factory: _SessionFactory,
    on_user_created: Callable[[User], None],
    bot=None,
) -> None:
    with session_factory() as session:
        token = _start_token(msg.text)
        # A newcomer (no User row yet) tapping an invite deep-link redeems it;
        # existing members ignore any token and get the normal start message
        # (unlike /join, which tells an existing member they're already in a
        # household — a deep-link re-tap should just be a friendly no-op).
        if token is not None and session.get(User, msg.from_user.id) is None:
            await _try_redeem_invite(
                msg, session, token=token, on_user_created=on_user_created, bot=bot
            )
            return
        decision = handler_support.authorize_and_get_user(
            session,
            allowed_user_id=handler_support.ALLOWED_TELEGRAM_USER_ID,
            telegram_user_id=msg.from_user.id,
            chat_id=msg.chat.id,
            chat_type=msg.chat.type,
        )
        if not decision.allowed:
            log.info(
                "unauthorized_update_rejected",
                extra={"telegram_user_id": msg.from_user.id, "chat_id": msg.chat.id},
            )
            await msg.answer(decision.reason)
            return
        user = _require_user(decision.user)
        if decision.created:
            on_user_created(user)
        await msg.answer(
            t("start.ready", user.lang, tz=user.tz, digest_hour=user.digest_hour)
        )
        await msg.answer(t("start.tour", user.lang))


async def handle_invite(
    msg,
    *,
    session_factory: _SessionFactory,
    bot: Bot,
    on_user_created: Callable[[User], None] = _noop_user_created,
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
    ) as ctx:
        if ctx is None:
            return
        session, user = ctx.session, ctx.user
        try:
            max_uses = parse_invite_mode(list((msg.text or "").split()[1:]))
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        result = create_invite(
            session,
            household_id=user.household_id,
            created_by=user.telegram_id,
            now=datetime.now(timezone.utc),
            max_uses=max_uses,
        )
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start={result.token}"
        key = "invite.created" if max_uses == 1 else "invite.created_reusable"
        await msg.answer(t(key, user.lang, link=link, code=result.token))


async def handle_join(
    msg,
    *,
    session_factory: _SessionFactory,
    on_user_created: Callable[[User], None] = _noop_user_created,
    bot=None,
) -> None:
    with session_factory() as session:
        try:
            token = parse_invite_token(list((msg.text or "").split()[1:]))
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        # redeem_invite raises AlreadyMember if the sender already has a row,
        # so existing members who /join are told to /leave first.
        await _try_redeem_invite(
            msg, session, token=token, on_user_created=on_user_created, bot=bot
        )


async def handle_household(
    msg,
    *,
    session_factory: _SessionFactory,
    on_user_created: Callable[[User], None] = _noop_user_created,
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
    ) as ctx:
        if ctx is None:
            return
        session, user = ctx.session, ctx.user
        members = list_members(session, household_id=user.household_id)
        lines = [t("household.title", user.lang, n=len(members))]
        for member in members:
            role_label = t(f"household.role.{member.role}", user.lang)
            you = (
                t("household.you", user.lang)
                if member.telegram_id == user.telegram_id
                else ""
            )
            lines.append(f"  {member.telegram_id} - {role_label}{you}")
        await msg.answer("\n".join(lines))


async def handle_leave(
    msg,
    *,
    session_factory: _SessionFactory,
    unschedule: Callable[[int], None],
    on_user_created: Callable[[User], None] = _noop_user_created,
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
    ) as ctx:
        if ctx is None:
            return
        session, user = ctx.session, ctx.user
        # Capture before leave_household deletes the row — `user` is detached
        # after the delete+commit, so reading user.* below would raise.
        lang = user.lang
        telegram_id = user.telegram_id
        try:
            leave_household(session, telegram_user_id=telegram_id)
        except OwnerCannotLeave:
            await msg.answer(t("leave.owner", lang))
            return
        unschedule(telegram_id)
        await msg.answer(t("leave.success", lang))


async def handle_remove(
    msg,
    *,
    session_factory: _SessionFactory,
    unschedule: Callable[[int], None],
    on_user_created: Callable[[User], None] = _noop_user_created,
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
    ) as ctx:
        if ctx is None:
            return
        session, user = ctx.session, ctx.user
        lang = user.lang
        try:
            target_id = parse_member_id(list((msg.text or "").split()[1:]))
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        try:
            removed = remove_member(
                session,
                household_id=user.household_id,
                actor_id=user.telegram_id,
                target_id=target_id,
            )
        except NotOwner:
            await msg.answer(t("remove.not_owner", lang))
            return
        except CannotRemoveSelf:
            await msg.answer(t("remove.self", lang))
            return
        except MemberNotFound:
            await msg.answer(t("remove.not_found", lang, id=target_id))
            return
        unschedule(removed.telegram_id)
        await msg.answer(t("remove.success", lang, id=removed.telegram_id))


async def handle_tz(
    msg, *, session_factory: _SessionFactory, reschedule: Callable[[User], None]
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=reschedule,
    ) as ctx:
        if ctx is None:
            return
        session, user = ctx.session, ctx.user
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await msg.answer("usage: /tz <IANA timezone>")
            return
        try:
            tz = parse_tz(parts[1].strip())
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        user.tz = tz
        session.add(user)
        session.commit()
        reschedule(user)
        await msg.answer(f"timezone set to {tz}")


async def handle_lang(
    msg,
    *,
    session_factory: _SessionFactory,
    on_user_created: Callable[[User], None] = _noop_user_created,
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
    ) as ctx:
        if ctx is None:
            return
        session, user = ctx.session, ctx.user
        try:
            lang = parse_lang(list((msg.text or "").split()[1:]))
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        if lang is None:
            await msg.answer(
                t("lang.current", user.lang, lang=user.lang, choices="|".join(LANGS))
            )
            return
        user.lang = lang
        session.add(user)
        session.commit()
        await msg.answer(t("lang.set", lang, lang=lang))


async def handle_digest_at(
    msg, *, session_factory: _SessionFactory, reschedule: Callable[[User], None]
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=reschedule,
    ) as ctx:
        if ctx is None:
            return
        session, user = ctx.session, ctx.user
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await msg.answer("usage: /digest_at <hour 0..23>")
            return
        try:
            hour = parse_digest_at(parts[1].strip())
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        user.digest_hour = hour
        session.add(user)
        session.commit()
        reschedule(user)
        await msg.answer(f"digest hour set to {hour}:00 in {user.tz}")
