from __future__ import annotations

import asyncio
import json as _json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import AsyncIterator, Awaitable, Callable, Optional, cast

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import update
from sqlmodel import Session

from app.commands import (
    CommandError,
    parse_callback,
    parse_correct_reply_marker,
    parse_digest_at,
    parse_invite_mode,
    parse_invite_token,
    parse_item_callback,
    parse_item_id_arg,
    parse_lang,
    parse_llm_provider,
    parse_list_filter,
    parse_member_id,
    parse_pantry_arg,
    parse_snooze_args,
    parse_tz,
)
from app.i18n import DEFAULT_LANG, LANGS, t
from app.correction_service import (
    NullDiff,
    ProposeCorrectError,
    add_payload_from_json,
    add_payload_to_json,
    apply_add,
    apply_correct,
    correct_payload_from_json,
    correct_payload_to_json,
    item_snapshot_to_json,
    propose_add,
    propose_correct,
)
from app.cook import (
    NotEnoughItems,
    ScoredCandidate,
    create_cook_session,
    list_saved,
    load_cook_session,
    load_saved,
    mark_status,
    missing_ingredients,
    recipe_from_saved,
    recook_shopping_list,
    run_cook,
    run_cook_more,
    save_candidate,
    set_feedback,
    set_message_id as set_cook_message_id,
)
from app.cook.recipe_source import ChainedRecipeSource, LlmRecipeSource
from app.shopping_service import add_missing, check_off, list_pending
from app.ingest_service import DuplicateReceipt, ingest_photo
from app.llm import (
    LLMClient,
    LLMProviderNotConfigured,
    ProfileUpdateLLMClient,
    TextLLMClient,
)
from app.providers import ALL_PROVIDERS, supports
from app.household_service import (
    provision_solo_household,
    restore_household_for_user,
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
from app.models import CookSession, Household, PantryItem, User
from app.refine_service import run_receipt_refine
from app.shelf_life_search import ShelfLifeSearchClient
from app.storage_state import shelf_life_origin
from app.callback_dispatch import answer as dispatch_answer, edit_or_resend
from app.progress import clear_progress, finish_progress, start_progress
from app.pantry_service import (
    ListFilter,
    NotOwnerOrMissing,
    active_pantry_names,
    compute_nudge_days,
    compute_stats,
    correct_item,
    move_to_storage,
    list_active,
    list_digest_due,
    mark_eaten,
    mark_removed,
    mark_tossed,
    snooze_item,
    undo_add,
    undo_receipt,
)
from app.pending_service import (
    create_pending,
    expire_for_item,
    load_pending,
    mark_applied,
    mark_cancelled,
    set_message_id,
    utc_naive,
)
from app.renderer import (
    CallbackButton,
    build_apply_cancel_keyboard,
    build_correct_menu_keyboard,
    build_cook_result_keyboard,
    build_cook_round_keyboard,
    PURPOSE_OPTIONS,
    build_digest_keyboard,
    build_favorites_keyboard,
    build_item_card_keyboard,
    build_remove_confirm_keyboard,
    build_shopping_keyboard,
    build_undo_add_keyboard,
    build_undo_keyboard,
    render_add_diff,
    render_applied_add,
    render_applied_correction,
    render_correct_menu,
    render_correction_diff,
    render_cook_result,
    render_digest,
    render_favorites,
    render_ingest_reply,
    render_item_card,
    render_list,
    render_profile,
    render_recook,
    render_remove_confirm,
    render_shopping_list,
    render_stats,
    render_terminal_state,
    render_undo_result,
)
from app.profile_service import profile_from_household, update_profile_from_sentence
from app.translation_service import cached_name_translations, translate_texts

DEFAULT_TZ = "America/Detroit"
DEFAULT_DIGEST_HOUR = 8
DEFAULT_LLM_PROVIDER = "anthropic"
ALLOWED_TELEGRAM_USER_ID: int = 0
MEAL_TYPES = ["Dinner", "Lunch", "Breakfast", "Dessert", "Snack", "Surprise me"]
DEFAULT_CUISINES = ["Italian", "Mexican", "Chinese", "American", "Surprise me"]
SPOONACULAR_CUISINES = [
    "African", "Asian", "American", "British", "Cajun", "Caribbean", "Chinese",
    "Eastern European", "European", "French", "German", "Greek", "Indian", "Irish",
    "Italian", "Japanese", "Jewish", "Korean", "Latin American", "Mediterranean",
    "Mexican", "Middle Eastern", "Nordic", "Southern", "Spanish", "Thai", "Vietnamese",
]

_SessionFactory = Callable[[], Session]
NowProvider = Callable[[str], datetime]
log = logging.getLogger(__name__)


@dataclass
class AuthDecision:
    allowed: bool
    user: Optional[User]
    created: bool
    reason: str
    household: Optional[Household] = None


@dataclass
class AuthStatus:
    """Result of the single membership gate.

    ``allowed`` is True for a known member (any household) or the bootstrap
    owner on first contact. ``user`` is the existing row, or None when the
    caller is the bootstrap owner who has not been provisioned yet.
    """

    allowed: bool
    user: Optional[User]
    is_bootstrap: bool


def resolve_authorization(
    session: Session,
    *,
    allowed_user_id: int,
    telegram_user_id: int,
) -> AuthStatus:
    """Single source of truth for "may this Telegram id use the bot?".

    A user is authorized if they already have a ``User`` row (i.e. they are a
    member of some household), or if they are the configured bootstrap owner
    making first contact. Everyone else is rejected here; the only other way in
    is redeeming an invite (see ``redeem_invite`` / ``/join`` / ``/start <token>``).
    """
    existing = session.get(User, telegram_user_id)
    if existing is not None:
        return AuthStatus(True, existing, is_bootstrap=False)
    if telegram_user_id == allowed_user_id:
        return AuthStatus(True, None, is_bootstrap=True)
    return AuthStatus(False, None, is_bootstrap=False)


def authorize_and_get_user(
    session: Session,
    *,
    allowed_user_id: int,
    telegram_user_id: int,
    chat_id: int,
    chat_type: str,
) -> AuthDecision:
    status = resolve_authorization(
        session,
        allowed_user_id=allowed_user_id,
        telegram_user_id=telegram_user_id,
    )
    if not status.allowed:
        return AuthDecision(False, None, False, "not authorized")
    if chat_type != "private":
        return AuthDecision(False, None, False, "this bot only works in private chat")

    if status.user is not None:
        existing = status.user
        household = session.get(Household, existing.household_id)
        if household is None:
            household = restore_household_for_user(
                session, existing, created_at=datetime.now(timezone.utc)
            )
        return AuthDecision(True, existing, False, "ok", household=household)

    # Bootstrap owner, first contact: provision a fresh solo household.
    now = datetime.now(timezone.utc)
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


def _noop_user_created(user: User) -> None:
    pass


def _require_user(user: User | None) -> User:
    assert user is not None
    return user


def _require_today(today: date | None) -> date:
    assert today is not None
    return today


def _available_llm_providers(
    llm: LLMClient, text_llm: TextLLMClient
) -> tuple[str, ...]:
    # A provider is selectable if it can serve the core text tasks (corrections,
    # /add, profile, cook, translation) — the floor every provider must meet.
    # Image extraction and web search fall back to a capable provider when the
    # chosen one lacks them, so they are not required for selectability. (The
    # ``llm`` image selector is accepted for call-site symmetry.)
    return tuple(sorted(getattr(text_llm, "available_providers", ("anthropic",))))


def _select_llm_client(llm: LLMClient, provider: str) -> LLMClient:
    selector = getattr(llm, "for_provider", None)
    if callable(selector):
        return cast(LLMClient, selector(provider))
    return llm


def _select_text_llm_client(text_llm: TextLLMClient, provider: str) -> TextLLMClient:
    selector = getattr(text_llm, "for_provider", None)
    if callable(selector):
        return cast(TextLLMClient, selector(provider))
    return text_llm


def _select_profile_llm(
    profile_llm: "ProfileUpdateLLMClient", provider: str
) -> "ProfileUpdateLLMClient":
    selector = getattr(profile_llm, "for_provider", None)
    return cast("ProfileUpdateLLMClient", selector(provider)) if callable(selector) else profile_llm


async def _translate_for_render(session, *, lang, texts, translation_llm):
    if lang == "en" or translation_llm is None:
        return {}
    return await translate_texts(session, [x for x in texts if x], lang=lang, llm=translation_llm)


def _cached_names_for_render(session, *, lang, texts):
    return cached_name_translations(session, [x for x in texts if x], lang=lang)


def _cook_card_texts(cards) -> list[str]:
    texts: list[str] = []
    for card in cards:
        recipe = card.recipe
        texts.append(recipe.title)
        texts.append(recipe.cuisine)
        texts.append(recipe.method_gist)
        texts.extend(ing.name for ing in recipe.ingredients)
        texts.extend(getattr(card, "shopping_list", None) or [])
    return texts


def _render_llm_status(user: User, llm: LLMClient, text_llm: TextLLMClient) -> str:
    available = _available_llm_providers(llm, text_llm)
    lines = [
        f"LLM provider: {user.llm_provider}",
        f"Available: {', '.join(available) if available else 'none'}",
    ]
    text_only = [p for p in available if not supports(p, "image")]
    if text_only:
        verb = "is" if len(text_only) == 1 else "are"
        lines.append(
            f"Note: {', '.join(text_only)} {verb} text-only; photos & web "
            "search use a capable provider."
        )
    lines.append(f"Usage: /llm [{'|'.join(ALL_PROVIDERS)}]")
    return "\n".join(lines)


def _authorized_callback_user(session: Session, telegram_id: int) -> User | None:
    """Membership gate for callback queries (shared with ``_guard``/``handle_start``
    via ``resolve_authorization``). Returns the member ``User`` or None if the
    sender is not a member of any household."""
    status = resolve_authorization(
        session,
        allowed_user_id=ALLOWED_TELEGRAM_USER_ID,
        telegram_user_id=telegram_id,
    )
    if not status.allowed:
        return None
    return status.user  # None only for a bootstrap owner with no row yet


async def _guard(
    msg,
    session: Session,
    *,
    on_user_created: Callable[[User], None] = _noop_user_created,
) -> User | None:
    decision = authorize_and_get_user(
        session,
        allowed_user_id=ALLOWED_TELEGRAM_USER_ID,
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
        return None
    user = _require_user(decision.user)
    if decision.created:
        on_user_created(user)
    return user


@dataclass
class _RequestContext:
    """What every authorized command needs before it does domain work.

    ``today`` is the sender's local date, resolved once here; it is ``None`` only
    for handlers that do not need it (those open the request without a clock).
    """

    session: Session
    user: User
    today: Optional[date]


@asynccontextmanager
async def _request(
    msg,
    *,
    session_factory: _SessionFactory,
    on_user_created: Callable[[User], None] = _noop_user_created,
    now_provider: Optional[NowProvider] = None,
) -> AsyncIterator[Optional["_RequestContext"]]:
    """Open a session, authorize the sender, and resolve today in one place.

    Yields a ``_RequestContext`` for an authorized member, or ``None`` when the
    sender was rejected (the handler should just return). This is the one home for
    the per-request preamble every command used to repeat: the session lifetime,
    the single auth gate (``_guard``), and the timezone->today rule.
    """
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None:
            yield None
            return
        today = now_provider(user.tz).date() if now_provider is not None else None
        yield _RequestContext(session=session, user=user, today=today)


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
        decision = authorize_and_get_user(
            session,
            allowed_user_id=ALLOWED_TELEGRAM_USER_ID,
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


async def handle_list(
    msg,
    *,
    session_factory: _SessionFactory,
    now_provider: NowProvider,
    on_user_created: Callable[[User], None] = _noop_user_created,
    translation_llm=None,
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
        now_provider=now_provider,
    ) as ctx:
        if ctx is None:
            return
        session, user, today = ctx.session, ctx.user, _require_today(ctx.today)
        try:
            list_filter = parse_list_filter(list((msg.text or "").split()[1:]))
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        items = list_active(
            session, household_id=user.household_id, f=list_filter, today=today
        )
        names = await _translate_for_render(
            session,
            lang=user.lang,
            texts=[i.raw_name for i in items],
            translation_llm=translation_llm,
        )
        await msg.answer(render_list(items, today=today, lang=user.lang, names=names))


async def handle_pantry(
    msg,
    *,
    session_factory: _SessionFactory,
    now_provider: NowProvider,
    on_user_created: Callable[[User], None] = _noop_user_created,
    translation_llm=None,
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
        now_provider=now_provider,
    ) as ctx:
        if ctx is None:
            return
        session, user, today = ctx.session, ctx.user, _require_today(ctx.today)
        parts = (msg.text or "").split(maxsplit=1)
        args = parts[1].split() if len(parts) == 2 else []
        try:
            mode = parse_pantry_arg(args)
        except CommandError:
            await msg.answer(t("pantry.usage", user.lang))
            return

        if isinstance(mode, int):
            item = session.get(PantryItem, mode)
            if item is None or item.household_id != user.household_id:
                await msg.answer(t("pantry.no_item", user.lang, id=mode))
                return
            if item.status != "active":
                await msg.answer(
                    t("pantry.item_inactive", user.lang, id=mode, status=item.status)
                )
                return
            names = await _translate_for_render(
                session,
                lang=user.lang,
                texts=[item.raw_name],
                translation_llm=translation_llm,
            )
            await msg.answer(
                render_item_card(item, today=today, lang=user.lang, names=names),
                reply_markup=to_aiogram_keyboard(
                    build_item_card_keyboard(item, lang=user.lang, back_to="all")
                ),
            )
            return

        if mode == "digest":
            items = list_digest_due(session, household_id=user.household_id, today=today)
            back_to = "digest"
            cap = 10
            empty_key = "digest.pantry_clear"
        else:
            items = list_active(
                session,
                household_id=user.household_id,
                f=ListFilter.default(),
                today=today,
            )
            back_to = "all"
            cap = None
            empty_key = "pantry.all_clear"

        names = await _translate_for_render(
            session,
            lang=user.lang,
            texts=[i.raw_name for i in items],
            translation_llm=translation_llm,
        )
        rendered = render_digest(items, today=today, lang=user.lang, names=names, cap=cap)
        if not rendered.text:
            await msg.answer(t(empty_key, user.lang))
            return
        keyboard = to_aiogram_keyboard(
            build_digest_keyboard(
                rendered.rendered_items,
                has_more=rendered.has_more,
                today=today,
                lang=user.lang,
                names=names,
                back_to=back_to,
            )
        )
        await msg.answer(rendered.text, reply_markup=keyboard)


async def handle_add(
    msg,
    *,
    session_factory: _SessionFactory,
    now_provider: NowProvider,
    text_llm: TextLLMClient,
    on_user_created: Callable[[User], None] = _noop_user_created,
    search: ShelfLifeSearchClient | None = None,
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
        now_provider=now_provider,
    ) as ctx:
        if ctx is None:
            return
        session, user, today = ctx.session, ctx.user, _require_today(ctx.today)
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await msg.answer("usage: /add <free text - name, category, expiry>")
            return
        progress = await start_progress(msg, t("progress.parsing_add", user.lang))
        try:
            selected_text_llm = _select_text_llm_client(text_llm, user.llm_provider)
            selected_search = _select_search(search, user.llm_provider)
            proposals, _ = await propose_add(
                session,
                llm=selected_text_llm,
                household_id=user.household_id,
                user_text=parts[1].strip(),
                today=today,
                tz=user.tz,
                search=selected_search,
            )
        except LLMProviderNotConfigured:
            await finish_progress(
                progress,
                msg,
                f"LLM provider {user.llm_provider!r} is not configured. Use /llm.",
            )
            return
        except Exception as exc:
            log.warning(
                "add_propose_failed",
                extra={
                    "user_id": user.telegram_id,
                    "error_class": type(exc).__name__,
                },
            )
            await finish_progress(
                progress, msg, "couldn't parse that add - try simpler wording"
            )
            return
        if not proposals:
            await finish_progress(
                progress, msg, "usage: /add <free text - name, category, expiry>"
            )
            return

        await clear_progress(progress)
        for proposal in proposals:
            pending = create_pending(
                session,
                household_id=user.household_id,
                action_type="add",
                item_id=None,
                proposed_json=add_payload_to_json(proposal.payload),
                snapshot_json=None,
                cost_micros_usd=proposal.cost_share,
                chat_id=msg.chat.id,
                now=datetime.now(timezone.utc),
            )
            assert pending.id is not None
            text = render_add_diff(
                pending_id=pending.id, payload=proposal.payload, lang=user.lang
            )
            keyboard = to_aiogram_keyboard(
                build_apply_cancel_keyboard(pending_id=pending.id, lang=user.lang)
            )
            try:
                sent = await msg.answer(text, reply_markup=keyboard)
            except Exception as exc:
                log.warning(
                    "add_send_failed",
                    extra={"pending_id": pending.id, "error_class": type(exc).__name__},
                )
                mark_cancelled(session, pending=pending)
                session.commit()
                continue
            set_message_id(session, pending=pending, message_id=sent.message_id)


async def _terminal_cmd(
    msg,
    session_factory,
    now_provider,
    *,
    fn,
    action_word,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
        now_provider=now_provider,
    ) as ctx:
        if ctx is None:
            return
        session, user, today = ctx.session, ctx.user, _require_today(ctx.today)
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await msg.answer(f"usage: /{action_word} <item_id>")
            return
        item_id: int
        try:
            item_id = parse_item_id_arg(parts[1].strip())
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        try:
            result = fn(
                session,
                household_id=user.household_id,
                item_id=item_id,
                today=today,
            )
        except NotOwnerOrMissing:
            await msg.answer(f"no item #{item_id}")
            return
        if result.applied:
            log.info(
                "item_action_applied",
                extra={
                    "user_id": user.telegram_id,
                    "item_id": item_id,
                    "action": action_word,
                },
            )
            await msg.answer(f"#{item_id} marked {action_word}")
        elif result.was_already:
            await msg.answer(f"#{item_id} was already non-active")


async def handle_ate(
    msg,
    *,
    session_factory,
    now_provider,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    await _terminal_cmd(
        msg,
        session_factory,
        now_provider,
        fn=mark_eaten,
        action_word="ate",
        on_user_created=on_user_created,
    )


async def handle_toss(
    msg,
    *,
    session_factory,
    now_provider,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    await _terminal_cmd(
        msg,
        session_factory,
        now_provider,
        fn=mark_tossed,
        action_word="toss",
        on_user_created=on_user_created,
    )


async def handle_delete(
    msg,
    *,
    session_factory,
    now_provider,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    await _terminal_cmd(
        msg,
        session_factory,
        now_provider,
        fn=mark_removed,
        action_word="delete",
        on_user_created=on_user_created,
    )


async def handle_snooze(
    msg,
    *,
    session_factory,
    now_provider,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
        now_provider=now_provider,
    ) as ctx:
        if ctx is None:
            return
        session, user, today = ctx.session, ctx.user, _require_today(ctx.today)
        item_id: int
        try:
            item_id, days = parse_snooze_args(list((msg.text or "").split()[1:]))
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        try:
            result = snooze_item(
                session,
                household_id=user.household_id,
                item_id=item_id,
                today=today,
                days=days,
            )
        except NotOwnerOrMissing:
            await msg.answer(f"no item #{item_id}")
            return
        except ValueError as exc:
            await msg.answer(str(exc))
            return
        if result.applied:
            log.info(
                "item_action_applied",
                extra={
                    "user_id": user.telegram_id,
                    "item_id": item_id,
                    "action": "snooze",
                },
            )
            await msg.answer(f"#{item_id} snoozed for {days}d")
        else:
            await msg.answer(f"#{item_id} is not active")


async def _propose_and_send_correction(
    msg,
    *,
    session: Session,
    user: User,
    item: PantryItem,
    user_text: str,
    today: date,
    text_llm: TextLLMClient,
) -> None:
    assert item.id is not None
    item_id = item.id
    try:
        selected_text_llm = _select_text_llm_client(text_llm, user.llm_provider)
        payload, cost = await propose_correct(
            session,
            llm=selected_text_llm,
            household_id=user.household_id,
            item=item,
            user_text=user_text,
            today=today,
        )
    except LLMProviderNotConfigured:
        await msg.answer(
            f"LLM provider {user.llm_provider!r} is not configured. Use /llm."
        )
        return
    except NullDiff:
        await msg.answer("no changes detected")
        return
    except ProposeCorrectError as exc:
        await msg.answer(str(exc))
        return
    except Exception as exc:
        log.warning(
            "correction_propose_failed",
            extra={
                "user_id": user.telegram_id,
                "item_id": item_id,
                "error_class": type(exc).__name__,
            },
        )
        await msg.answer("couldn't parse that correction - try simpler wording")
        return

    pending = create_pending(
        session,
        household_id=user.household_id,
        action_type="correct",
        item_id=item_id,
        proposed_json=correct_payload_to_json(payload),
        snapshot_json=item_snapshot_to_json(item),
        cost_micros_usd=cost,
        chat_id=msg.chat.id,
        now=datetime.now(timezone.utc),
    )
    assert pending.id is not None
    text = render_correction_diff(
        pending_id=pending.id,
        payload=payload,
        item_id=item_id,
        item_raw_name=item.raw_name,
        lang=user.lang,
    )
    keyboard = to_aiogram_keyboard(
        build_apply_cancel_keyboard(pending_id=pending.id, lang=user.lang)
    )
    try:
        sent = await msg.answer(text, reply_markup=keyboard)
    except Exception as exc:
        log.warning(
            "correct_send_failed",
            extra={"pending_id": pending.id, "error_class": type(exc).__name__},
        )
        mark_cancelled(session, pending=pending)
        session.commit()
        return
    set_message_id(session, pending=pending, message_id=sent.message_id)


async def handle_correct(
    msg,
    *,
    session_factory,
    now_provider,
    text_llm: TextLLMClient,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
        now_provider=now_provider,
    ) as ctx:
        if ctx is None:
            return
        session, user, today = ctx.session, ctx.user, _require_today(ctx.today)
        parts = (msg.text or "").split(maxsplit=2)
        if len(parts) < 3 or not parts[2].strip():
            await msg.answer("usage: /correct <item_id> <free text>")
            return
        try:
            item_id = parse_item_id_arg(parts[1].strip())
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        item = session.get(PantryItem, item_id)
        if item is None or item.household_id != user.household_id:
            await msg.answer(f"no item #{item_id}")
            return
        if item.status != "active":
            await msg.answer(f"#{item_id} is {item.status}; cannot correct")
            return
        await _propose_and_send_correction(
            msg,
            session=session,
            user=user,
            item=item,
            user_text=parts[2].strip(),
            today=today,
            text_llm=text_llm,
        )


async def handle_correct_reply(
    msg,
    *,
    session_factory,
    now_provider,
    text_llm: TextLLMClient,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    marker_text = getattr(getattr(msg, "reply_to_message", None), "text", None)
    item_id = parse_correct_reply_marker(marker_text)
    if item_id is None:
        return

    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
        now_provider=now_provider,
    ) as ctx:
        if ctx is None:
            return
        session, user, today = ctx.session, ctx.user, _require_today(ctx.today)
        item = session.get(PantryItem, item_id)
        if item is None or item.household_id != user.household_id:
            await msg.answer(f"no item #{item_id}")
            return
        if item.status != "active":
            await msg.answer(f"#{item_id} is {item.status}; cannot correct")
            return
        user_text = (msg.text or "").strip()
        if not user_text:
            await msg.answer("reply with the correction text")
            return
        await _propose_and_send_correction(
            msg,
            session=session,
            user=user,
            item=item,
            user_text=user_text,
            today=today,
            text_llm=text_llm,
        )


async def handle_stats(
    msg,
    *,
    session_factory,
    now_provider,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
    ) as ctx:
        if ctx is None:
            return
        session, user = ctx.session, ctx.user
        now = now_provider(user.tz)
        stats = compute_stats(
            session,
            household_id=user.household_id,
            now=now.astimezone(timezone.utc),
        )
        await msg.answer(render_stats(stats, lang=user.lang))


async def handle_shopping(
    msg,
    *,
    session_factory: _SessionFactory,
    now_provider: NowProvider,
    on_user_created: Callable[[User], None] = _noop_user_created,
    translation_llm=None,
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
    ) as ctx:
        if ctx is None:
            return
        session, user = ctx.session, ctx.user
        items = list_pending(session, household_id=user.household_id)
        names = await _translate_for_render(
            session,
            lang=user.lang,
            texts=[i.name_raw for i in items],
            translation_llm=translation_llm,
        )
        keyboard = (
            to_aiogram_keyboard(
                build_shopping_keyboard([i.id for i in items if i.id], lang=user.lang)
            )
            if items
            else None
        )
        await msg.answer(render_shopping_list(items, lang=user.lang, names=names), reply_markup=keyboard)


async def handle_favorites(
    msg,
    *,
    session_factory: _SessionFactory,
    on_user_created: Callable[[User], None] = _noop_user_created,
    translation_llm=None,
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
    ) as ctx:
        if ctx is None:
            return
        session, user = ctx.session, ctx.user
        recipes = list_saved(session, household_id=user.household_id)
        names = await _translate_for_render(
            session,
            lang=user.lang,
            texts=[r.title for r in recipes] + [r.cuisine for r in recipes],
            translation_llm=translation_llm,
        )
        keyboard = (
            to_aiogram_keyboard(
                build_favorites_keyboard(
                    [r.id for r in recipes if r.id], lang=user.lang
                )
            )
            if recipes
            else None
        )
        await msg.answer(render_favorites(recipes, lang=user.lang, names=names), reply_markup=keyboard)


async def handle_cook(
    msg,
    *,
    session_factory: _SessionFactory,
    now_provider: NowProvider,
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
        now = now_provider(user.tz)
        cook = create_cook_session(
            session,
            household_id=user.household_id,
            chat_id=msg.chat.id,
            now=now.astimezone(timezone.utc),
        )
        assert cook.id is not None
        keyboard = to_aiogram_keyboard(
            build_cook_round_keyboard(cook.id, MEAL_TYPES, round_name="meal")
        )
        sent = await msg.answer("What are you cooking?", reply_markup=keyboard)
        set_cook_message_id(session, cook=cook, message_id=sent.message_id)


async def handle_llm(
    msg,
    *,
    session_factory,
    llm: LLMClient,
    text_llm: TextLLMClient,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
    ) as ctx:
        if ctx is None:
            return
        session, user = ctx.session, ctx.user
        try:
            provider = parse_llm_provider((msg.text or "").split()[1:])
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        if provider is None:
            await msg.answer(_render_llm_status(user, llm, text_llm))
            return
        available = _available_llm_providers(llm, text_llm)
        if provider not in available:
            await msg.answer(
                f"LLM provider {provider!r} is not configured. "
                f"Available: {', '.join(available) if available else 'none'}"
            )
            return
        user.llm_provider = provider
        session.add(user)
        session.commit()
        await msg.answer(f"LLM provider set to {provider}")


async def handle_prefs(
    msg,
    *,
    session_factory,
    profile_llm: ProfileUpdateLLMClient,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
    ) as ctx:
        if ctx is None:
            return
        session, user = ctx.session, ctx.user
        household = session.get(Household, user.household_id)
        if household is None:
            await msg.answer("couldn't load your household profile")
            return
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await msg.answer(render_profile(profile_from_household(household), lang=user.lang))
            return
        try:
            selected = _select_profile_llm(profile_llm, user.llm_provider)
            profile, _ = await update_profile_from_sentence(
                session, llm=selected, household=household, sentence=parts[1].strip(),
            )
        except LLMProviderNotConfigured:
            await msg.answer(
                f"LLM provider {user.llm_provider!r} is not configured. Use /llm."
            )
            return
        except Exception as exc:
            log.warning(
                "prefs_update_failed",
                extra={
                    "user_id": user.telegram_id,
                    "error_class": type(exc).__name__,
                },
            )
            await msg.answer("couldn't update your profile - try simpler wording")
            return
        await msg.answer(t("prefs.updated", user.lang) + "\n\n" + render_profile(profile, lang=user.lang))


HELP_TEXT = t("help.body", "en")


async def handle_help(
    msg,
    *,
    session_factory,
    on_user_created: Callable[[User], None] = _noop_user_created,
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
    ) as ctx:
        if ctx is None:
            return
        user = ctx.user
    await msg.answer(t("help.body", user.lang))


async def handle_photo(
    msg,
    *,
    session_factory,
    now_provider,
    llm: LLMClient,
    photo_downloader: Callable[[str], Awaitable[bytes]],
    on_user_created: Callable[[User], None] = _noop_user_created,
    search: ShelfLifeSearchClient | None = None,
    spawn=None,
    bot=None,
    translation_llm=None,
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
        now_provider=now_provider,
    ) as ctx:
        if ctx is None:
            return
        session, user, today = ctx.session, ctx.user, _require_today(ctx.today)
        if not msg.photo:
            await msg.answer("send a photo of a receipt")
            return
        file_id = msg.photo[-1].file_id
        log.info(
            "receipt_ingest_started",
            extra={"user_id": user.telegram_id, "photo_file_id": file_id},
        )
        progress = await start_progress(msg, t("progress.reading_receipt", user.lang))
        try:
            selected_llm = _select_llm_client(llm, user.llm_provider)
            selected_search = _select_search(search, user.llm_provider)
            summary = await ingest_photo(
                session,
                selected_llm,
                household_id=user.household_id,
                photo_file_id=file_id,
                image_bytes=await photo_downloader(file_id),
                today=today,
                search=selected_search,
            )
        except LLMProviderNotConfigured:
            await finish_progress(
                progress,
                msg,
                f"LLM provider {user.llm_provider!r} is not configured. Use /llm.",
            )
            return
        except DuplicateReceipt:
            await finish_progress(progress, msg, "this receipt was already logged")
            return
        except Exception as exc:
            log.warning(
                "receipt_ingest_failed",
                extra={
                    "user_id": user.telegram_id,
                    "photo_file_id": file_id,
                    "error_class": type(exc).__name__,
                },
            )
            await finish_progress(
                progress,
                msg,
                "couldn't read that one - try a clearer photo or /add <items> manually",
            )
            return
        log.info(
            "receipt_ingest_succeeded",
            extra={
                "user_id": user.telegram_id,
                "receipt_id": summary.receipt_id,
                "inserted_food_count": summary.inserted_food_count,
            },
        )
        user_lang = user.lang
        names = await _translate_for_render(
            session,
            lang=user_lang,
            texts=list(summary.inserted_item_names),
            translation_llm=translation_llm,
        )
        keyboard = (
            to_aiogram_keyboard(
                build_undo_keyboard(receipt_id=summary.receipt_id, lang=user_lang)
            )
            if summary.receipt_id is not None and summary.inserted_food_count
            else None
        )
        refine_household_id = user.household_id
        refine_search = selected_search
        sent = await finish_progress(
            progress,
            msg,
            render_ingest_reply(summary, today=today, lang=user_lang, names=names),
            keyboard,
        )

    if (
        refine_search is not None
        and spawn is not None
        and bot is not None
        and summary.receipt_id is not None
        and summary.uncached_item_ids
    ):
        chat_id = msg.chat.id
        message_id = sent.message_id
        receipt_id = summary.receipt_id
        item_ids = list(summary.uncached_item_ids)

        async def _run_refine():
            refined = await run_receipt_refine(
                session_factory,
                refine_search,
                item_ids=item_ids,
                summary=summary,
                household_id=refine_household_id,
                receipt_id=receipt_id,
                today=today,
            )
            if not refined:
                return
            text = render_ingest_reply(
                summary,
                today=today,
                refined_ids=refined,
                lang=user_lang,
                names=names,
            )
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=to_aiogram_keyboard(
                        build_undo_keyboard(receipt_id=receipt_id, lang=user_lang)
                    ),
                )
            except Exception as exc:
                log.warning(
                    "refine_edit_failed", extra={"error_class": type(exc).__name__}
                )

        spawn(_run_refine())


def _cuisine_options(household: Household) -> list[str]:
    try:
        prefs = _json.loads(household.preferred_cuisines_json or "[]")
    except (TypeError, ValueError):
        prefs = []
    options = [str(c).title() for c in prefs if str(c).strip()]
    if not options:
        options = list(DEFAULT_CUISINES)
    elif "Surprise me" not in options:
        options = options[:4] + ["Surprise me"]
    return options[:5]


def _cuisine_round_keyboard(
    cook_id: int, options: list[str], *, lang: str = "en"
) -> list[list[CallbackButton]]:
    rows = build_cook_round_keyboard(cook_id, options, round_name="cuisine")
    rows.append(
        [
            CallbackButton(
                text=t("btn.more_cuisines", lang),
                callback_data=f"cookmore:{cook_id}:cuisine_full",
            )
        ]
    )
    return rows


def _select_cook(client, provider: str):
    selector = getattr(client, "for_provider", None)
    return selector(provider) if callable(selector) else client


def _select_search(
    search: ShelfLifeSearchClient | None, provider: str
) -> ShelfLifeSearchClient | None:
    """Resolve the per-user web-search client (with capability fallback).

    ``search`` may be a ``SearchProviderSelector``, a bare client, or None (in
    tests); only the selector exposes ``for_provider``, so the others pass
    through unchanged.
    """
    selector = getattr(search, "for_provider", None)
    if callable(selector):
        return cast(ShelfLifeSearchClient, selector(provider))
    return search


async def _safe_edit_cb(cb, text: str, keyboard=None) -> bool:
    try:
        await cb.message.edit_text(text, reply_markup=keyboard)
        return True
    except Exception as exc:
        log.warning("cook_edit_failed", extra={"error_class": type(exc).__name__})
        return False


async def _safe_edit_bot(
    bot, *, chat_id: int, message_id: int | None, text: str, keyboard=None
) -> None:
    if bot is None or message_id is None:
        return
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=keyboard,
        )
    except Exception as exc:
        log.warning("cook_edit_failed", extra={"error_class": type(exc).__name__})


async def handle_cook_callback(
    cb,
    *,
    session_factory: _SessionFactory,
    now_provider: NowProvider,
    selection_llm,
    recipe_llm,
    nutrition_llm,
    spawn,
    bot,
    translation_llm=None,
    recipe_sources=(),
) -> None:
    try:
        action = parse_callback(cb.data or "")
    except CommandError:
        await cb.answer("unrecognized action")
        return
    if (
        action.verb
        not in ("cook_pick", "cook_alt", "cook_more_opts", "cook_more", "cook_adjust")
        or action.item_id is None
    ):
        await cb.answer("unrecognized action")
        return

    with session_factory() as session:
        user = _authorized_callback_user(session, cb.from_user.id)
        if user is None:
            log.info(
                "unauthorized_update_rejected",
                extra={"telegram_user_id": cb.from_user.id},
            )
            await cb.answer("not authorized", show_alert=False)
            return
        cook = load_cook_session(
            session, household_id=user.household_id, cook_id=action.item_id
        )
        if cook is None or cook.status not in ("collecting", "ready", "done"):
            await cb.answer("this cook session expired - start a new /cook")
            return
        now = utc_naive(now_provider(user.tz))
        if cook.expires_at <= now:
            cook.status = "expired"
            session.add(cook)
            session.commit()
            await cb.answer("this cook session expired - start a new /cook")
            return

        if action.verb == "cook_alt":
            try:
                raw_cards = _json.loads(cook.candidates_json or "[]")
                cards = [
                    ScoredCandidate.model_validate(card) for card in raw_cards
                ]
            except (TypeError, ValueError):
                cards = []
            assert cook.id is not None
            names = await _translate_for_render(
                session,
                lang=user.lang,
                texts=_cook_card_texts(cards),
                translation_llm=translation_llm,
            )
            await _safe_edit_cb(
                cb,
                render_cook_result(
                    cards, show_alternatives=True, lang=user.lang, names=names
                ),
                to_aiogram_keyboard(
                    build_cook_result_keyboard(
                        cook.id, has_alternatives=False, lang=user.lang
                    )
                ),
            )
            await cb.answer("showing alternatives")
            return

        if action.verb == "cook_more":
            if cook.status != "done":
                await cb.answer("cook is still in progress")
                return
            assert cook.id is not None
            claim = session.exec(
                update(CookSession)
                .where(
                    CookSession.id == cook.id,  # type: ignore[arg-type]
                    CookSession.household_id == user.household_id,  # type: ignore[arg-type]
                    CookSession.status == "done",  # type: ignore[arg-type]
                )
                .values(status="ready")
            )
            session.commit()
            if claim.rowcount == 0:
                await cb.answer("already searching")
                return
            await cb.answer()
            cook = load_cook_session(
                session, household_id=user.household_id, cook_id=cook.id
            )
            if cook is None:
                return
            try:
                household = session.get(Household, user.household_id)
                if household is None:
                    cards = []
                else:
                    profile = profile_from_household(household)
                    today = now_provider(user.tz).date()
                    selected_recipe_llm = _select_cook(recipe_llm, user.llm_provider)
                    selected_nutrition_llm = _select_cook(
                        nutrition_llm, user.llm_provider
                    )
                    source = ChainedRecipeSource(
                        [
                            *recipe_sources,
                            LlmRecipeSource(
                                recipe_llm=selected_recipe_llm,
                                nutrition_llm=selected_nutrition_llm,
                            ),
                        ]
                    )
                    cards = await run_cook_more(
                        session,
                        cook=cook,
                        profile=profile,
                        source=source,
                        today=today,
                    )
                if not cards:
                    await _safe_edit_cb(cb, t("cook.no_more", user.lang))
                else:
                    names = await _translate_for_render(
                        session,
                        lang=user.lang,
                        texts=_cook_card_texts(cards),
                        translation_llm=translation_llm,
                    )
                    await _safe_edit_cb(
                        cb,
                        render_cook_result(
                            cards, show_alternatives=False, lang=user.lang, names=names
                        ),
                        to_aiogram_keyboard(
                            build_cook_result_keyboard(
                                cook.id, has_alternatives=len(cards) > 1, lang=user.lang
                            )
                        ),
                    )
            finally:
                cook.status = "done"
                session.add(cook)
                session.commit()
            return

        if action.verb == "cook_adjust":
            if cook.status != "done":
                await cb.answer("cook is still in progress")
                return
            assert cook.id is not None
            claim = session.exec(
                update(CookSession)
                .where(
                    CookSession.id == cook.id,  # type: ignore[arg-type]
                    CookSession.household_id == user.household_id,  # type: ignore[arg-type]
                    CookSession.status == "done",  # type: ignore[arg-type]
                )
                .values(status="collecting", cuisine=None, purpose=None, search_offset=0)
            )
            session.commit()
            if claim.rowcount == 0:
                await cb.answer("already cooking")
                return
            household = session.get(Household, user.household_id)
            if household is None:
                await cb.answer("couldn't load your household profile")
                return
            await _safe_edit_cb(
                cb,
                "Which cuisine?",
                to_aiogram_keyboard(
                    _cuisine_round_keyboard(
                        cook.id, _cuisine_options(household), lang=user.lang
                    )
                ),
            )
            await cb.answer()
            return

        if action.verb == "cook_more_opts":
            if (
                cook.status != "collecting"
                or cook.meal_type is None
                or cook.cuisine is not None
                or action.round_name != "cuisine_full"
            ):
                await cb.answer("unrecognized action")
                return
            assert cook.id is not None
            rows = build_cook_round_keyboard(
                cook.id, [*SPOONACULAR_CUISINES, "Surprise me"], round_name="cuisine_full"
            )
            await _safe_edit_cb(cb, "Which cuisine?", to_aiogram_keyboard(rows))
            await cb.answer()
            return

        option_index = action.option_index
        if option_index is None:
            await cb.answer("unrecognized action")
            return

        household = session.get(Household, user.household_id)
        if household is None:
            await cb.answer("couldn't load your household profile")
            return

        if cook.meal_type is None:
            if action.round_name == "cuisine":
                await cb.answer("unrecognized action")
                return
            if option_index < 0 or option_index >= len(MEAL_TYPES):
                await cb.answer("unrecognized action")
                return
            assert cook.id is not None
            keyboard = to_aiogram_keyboard(
                _cuisine_round_keyboard(
                    cook.id, _cuisine_options(household), lang=user.lang
                )
            )
            if not await _safe_edit_cb(
                cb,
                "Which cuisine?",
                keyboard,
            ):
                await cb.answer("couldn't update this cook session - try /cook again")
                return
            cook.meal_type = MEAL_TYPES[option_index]
            session.add(cook)
            session.commit()
            await cb.answer()
            return

        if action.round_name == "meal":
            await cb.answer("already answered")
            return

        if cook.cuisine is None:
            if action.round_name not in ("cuisine", "cuisine_full"):
                await cb.answer("unrecognized action")
                return
            cuisine_options = (
                _cuisine_options(household)
                if action.round_name == "cuisine"
                else [*SPOONACULAR_CUISINES, "Surprise me"]
            )
            if option_index < 0 or option_index >= len(cuisine_options):
                await cb.answer("unrecognized action")
                return
            chosen_cuisine = cuisine_options[option_index]
            result = session.exec(
                update(CookSession)
                .where(
                    CookSession.id == cook.id,  # type: ignore[arg-type]
                    CookSession.household_id == user.household_id,  # type: ignore[arg-type]
                    CookSession.status == "collecting",  # type: ignore[arg-type]
                    CookSession.meal_type.is_not(None),  # type: ignore[union-attr]
                    CookSession.cuisine.is_(None),  # type: ignore[union-attr]
                )
                .values(cuisine=chosen_cuisine)
            )
            session.commit()
            if result.rowcount == 0:
                await cb.answer("already cooking")
                return
            assert cook.id is not None
            keyboard = to_aiogram_keyboard(
                build_cook_round_keyboard(
                    cook.id,
                    [t(key, user.lang) for _code, key in PURPOSE_OPTIONS],
                    round_name="purpose",
                )
            )
            await _safe_edit_cb(cb, t("cook.round.purpose", user.lang), keyboard)
            await cb.answer()
            return

        if cook.purpose is not None:
            await cb.answer()
            return

        if action.round_name != "purpose":
            await cb.answer("unrecognized action")
            return

        if option_index < 0 or option_index >= len(PURPOSE_OPTIONS):
            await cb.answer("unrecognized action")
            return
        chosen_purpose = PURPOSE_OPTIONS[option_index][0]
        result = session.exec(
            update(CookSession)
            .where(
                CookSession.id == cook.id,  # type: ignore[arg-type]
                CookSession.household_id == user.household_id,  # type: ignore[arg-type]
                CookSession.status == "collecting",  # type: ignore[arg-type]
                CookSession.cuisine.is_not(None),  # type: ignore[union-attr]
                CookSession.purpose.is_(None),  # type: ignore[union-attr]
            )
            .values(purpose=chosen_purpose, status="ready")
        )
        session.commit()
        if result.rowcount == 0:
            await cb.answer("already cooking")
            return
        assert cook.id is not None
        cook = load_cook_session(
            session, household_id=user.household_id, cook_id=cook.id
        )
        if cook is None:
            await cb.answer("this cook session expired - start a new /cook")
            return
        await _safe_edit_cb(cb, "Thinking...")
        await cb.answer()
        user_id = user.telegram_id
        household_id = user.household_id
        user_tz = user.tz
        cook_id = cook.id

    if cook_id is None:
        return
    spawn(
        run_cook_and_render(
            session_factory,
            user_id=user_id,
            household_id=household_id,
            user_tz=user_tz,
            cook_id=cook_id,
            selection_llm=selection_llm,
            recipe_llm=recipe_llm,
            nutrition_llm=nutrition_llm,
            now_provider=now_provider,
            bot=bot,
            translation_llm=translation_llm,
            recipe_sources=recipe_sources,
        )
    )


async def run_cook_and_render(
    session_factory: _SessionFactory,
    *,
    user_id: int,
    household_id: int,
    user_tz: str,
    cook_id: int,
    selection_llm,
    recipe_llm,
    nutrition_llm,
    now_provider: NowProvider,
    bot,
    translation_llm=None,
    recipe_sources=(),
) -> None:
    with session_factory() as session:
        user = session.get(User, user_id)
        if user is None:
            return
        cook = load_cook_session(
            session, household_id=household_id, cook_id=cook_id
        )
        if cook is None or cook.status != "ready":
            return
        household = session.get(Household, household_id)
        if household is None:
            mark_status(session, cook=cook, status="cancelled")
            await _safe_edit_bot(
                bot,
                chat_id=cook.chat_id,
                message_id=cook.message_id,
                text="Couldn't load your household profile - try /cook again.",
            )
            return
        profile = profile_from_household(household)
        chat_id = cook.chat_id
        message_id = cook.message_id
        today = now_provider(user_tz).date()
        selected_selection_llm = _select_cook(selection_llm, user.llm_provider)
        selected_recipe_llm = _select_cook(recipe_llm, user.llm_provider)
        selected_nutrition_llm = _select_cook(nutrition_llm, user.llm_provider)
        source = ChainedRecipeSource([
            *recipe_sources,
            LlmRecipeSource(
                recipe_llm=selected_recipe_llm, nutrition_llm=selected_nutrition_llm
            ),
        ])
        try:
            cards = await run_cook(
                session,
                cook=cook,
                profile=profile,
                selection_llm=selected_selection_llm,  # type: ignore[arg-type]
                source=source,
                today=today,
            )
        except NotEnoughItems:
            mark_status(session, cook=cook, status="cancelled")
            await _safe_edit_bot(
                bot,
                chat_id=chat_id,
                message_id=message_id,
                text="Not enough usable items - send a receipt or /add a few things.",
            )
            return
        except Exception as exc:
            log.warning(
                "cook_pipeline_failed", extra={"error_class": type(exc).__name__}
            )
            mark_status(session, cook=cook, status="cancelled")
            await _safe_edit_bot(
                bot,
                chat_id=chat_id,
                message_id=message_id,
                text="Couldn't build a recipe right now - try /cook again.",
            )
            return

        mark_status(session, cook=cook, status="done")
        names = await _translate_for_render(
            session,
            lang=user.lang,
            texts=_cook_card_texts(cards),
            translation_llm=translation_llm,
        )
        text = render_cook_result(
            cards, show_alternatives=False, lang=user.lang, names=names
        )
        keyboard = (
            to_aiogram_keyboard(
                build_cook_result_keyboard(
                    cook_id, has_alternatives=len(cards) > 1, lang=user.lang
                )
            )
            if cards
            else None
        )
        await _safe_edit_bot(
            bot, chat_id=chat_id, message_id=message_id, text=text, keyboard=keyboard
        )


async def handle_item_callback(
    cb, *, session_factory, now_provider, translation_llm=None
) -> None:
    # Acknowledge first, render after: every branch answers the callback before
    # any translation or edit so the button never appears to "do nothing", and
    # all rendering goes through edit_or_resend so a failed in-place edit falls
    # back to a fresh message instead of being silently swallowed.
    try:
        action = parse_item_callback(cb.data)
    except CommandError:
        await dispatch_answer(cb, "unrecognized action")
        return

    with session_factory() as session:
        user = _authorized_callback_user(session, cb.from_user.id)
        if user is None:
            await dispatch_answer(cb, "not authorized")
            return
        today = now_provider(user.tz).date()

        async def refresh() -> None:
            await _refresh_digest_message(
                cb, session, user.household_id, today,
                lang=user.lang, translation_llm=translation_llm,
            )

        async def refresh_for_origin() -> None:
            if action.back_to == "all":
                await _refresh_pantry_message(
                    cb, session, user.household_id, today,
                    lang=user.lang, translation_llm=translation_llm,
                )
                return
            await refresh()

        def item_names(item) -> dict:
            return _cached_names_for_render(
                session, lang=user.lang, texts=[item.raw_name]
            )

        if action.kind == "list":
            await dispatch_answer(cb)
            await refresh_for_origin()
            return

        item_id = action.item_id
        assert item_id is not None
        item = session.get(PantryItem, item_id)
        if item is None or item.household_id != user.household_id:
            await dispatch_answer(cb, "item not found")
            await refresh_for_origin()
            return
        if item.status != "active":
            await dispatch_answer(cb, f"#{item_id} already updated")
            await refresh_for_origin()
            return

        if action.kind == "nudge":
            assert action.nudge_code is not None
            new_days = compute_nudge_days(
                current_days=item.shelf_life_days,
                origin=shelf_life_origin(item),
                today=today,
                code=action.nudge_code,
            )
            item = correct_item(
                session, household_id=user.household_id,
                item_id=item_id, days=new_days, today=today,
            )
            await dispatch_answer(cb, "updated")
            names = item_names(item)
            await edit_or_resend(
                cb,
                render_correct_menu(item, today=today, lang=user.lang, names=names),
                to_aiogram_keyboard(build_correct_menu_keyboard(item_id, lang=user.lang)),
            )
            return

        if action.kind == "rmok":
            mark_removed(
                session, household_id=user.household_id,
                item_id=item_id, today=today,
            )
            await dispatch_answer(cb, "removed")
            await refresh()
            return

        if action.kind == "ctext":
            await dispatch_answer(cb)
            names = item_names(item)
            display_name = names.get(item.raw_name, item.raw_name)
            prompt = t(
                "correct.freetext_prompt", user.lang,
                id=item_id, name=display_name,
            )
            try:
                await cb.message.answer(prompt, reply_markup=ForceReply())
            except Exception as exc:
                log.warning(
                    "correct_prompt_failed",
                    extra={"error_class": type(exc).__name__},
                )
            return

        # Pure view changes: acknowledge, then render the requested screen.
        await dispatch_answer(cb)
        names = item_names(item)
        if action.kind == "open":
            await edit_or_resend(
                cb,
                render_item_card(item, today=today, lang=user.lang, names=names),
                to_aiogram_keyboard(
                    build_item_card_keyboard(
                        item,
                        lang=user.lang,
                        back_to=action.back_to,
                    )
                ),
            )
        elif action.kind == "corr":
            await edit_or_resend(
                cb,
                render_correct_menu(item, today=today, lang=user.lang, names=names),
                to_aiogram_keyboard(build_correct_menu_keyboard(item_id, lang=user.lang)),
            )
        elif action.kind == "rm":
            await edit_or_resend(
                cb,
                render_remove_confirm(item, lang=user.lang, names=names),
                to_aiogram_keyboard(build_remove_confirm_keyboard(item_id, lang=user.lang)),
            )


async def handle_callback(
    cb,
    *,
    session_factory,
    now_provider,
    translation_llm=None,
    search: ShelfLifeSearchClient | None = None,
) -> None:
    try:
        action = parse_callback(cb.data)
    except CommandError:
        await cb.answer("unrecognized action")
        return

    with session_factory() as session:
        user = _authorized_callback_user(session, cb.from_user.id)
        if user is None:
            log.info(
                "unauthorized_update_rejected",
                extra={"telegram_user_id": cb.from_user.id},
            )
            await cb.answer("not authorized", show_alert=False)
            return
        today = now_provider(user.tz).date()

        async def refresh_items_for_origin() -> None:
            if action.back_to == "all":
                await _refresh_pantry_message(
                    cb,
                    session,
                    user.household_id,
                    today,
                    lang=user.lang,
                    translation_llm=translation_llm,
                )
                return
            await _refresh_digest_message(
                cb,
                session,
                user.household_id,
                today,
                lang=user.lang,
                translation_llm=translation_llm,
            )

        if action.verb in ("cook_like", "cook_dislike"):
            cook_id = action.item_id
            assert cook_id is not None
            cook = load_cook_session(
                session, household_id=user.household_id, cook_id=cook_id
            )
            if cook is None or cook.status != "done":
                await cb.answer("this cook session expired - start a new /cook")
                return
            verdict = "liked" if action.verb == "cook_like" else "disliked"
            set_feedback(session, cook=cook, feedback=verdict, now=now_provider(user.tz))
            await cb.answer("got it 👍" if verdict == "liked" else "noted 👎")
            return

        if action.verb in ("cook_save", "cook_shop"):
            cook_id = action.item_id
            assert cook_id is not None
            cook = load_cook_session(
                session, household_id=user.household_id, cook_id=cook_id
            )
            if cook is None or cook.status != "done":
                await cb.answer("this cook session expired - start a new /cook")
                return
            try:
                raw_cards = _json.loads(cook.candidates_json or "[]")
                cards = [ScoredCandidate.model_validate(c) for c in raw_cards]
            except (TypeError, ValueError):
                cards = []
            if not cards:
                await cb.answer("nothing to use here")
                return
            index = cook.chosen_index or 0
            if index < 0 or index >= len(cards):
                index = 0
            candidate = cards[index].recipe
            if action.verb == "cook_save":
                result = save_candidate(
                    session, household_id=user.household_id, candidate=candidate,
                    now=now_provider(user.tz),
                )
                await cb.answer("already saved" if result.duplicate else "saved ★")
                return
            pantry = active_pantry_names(
                session, household_id=user.household_id, today=today
            )
            missing = missing_ingredients(
                ingredients=candidate.ingredients, pantry_normalized=pantry
            )
            add_result = add_missing(
                session, household_id=user.household_id, ingredients=missing,
                now=now_provider(user.tz),
            )
            if add_result.added:
                await cb.answer(f"added {len(add_result.added)} to shopping list")
            elif add_result.already:
                await cb.answer("already on your list")
            else:
                await cb.answer("you have everything!")
            return

        if action.verb == "shop_done":
            shopping_id = action.item_id
            assert shopping_id is not None
            ok = check_off(
                session, household_id=user.household_id, shopping_id=shopping_id,
                now=now_provider(user.tz),
            )
            remaining = list_pending(session, household_id=user.household_id)
            shop_names = await _translate_for_render(
                session,
                lang=user.lang,
                texts=[i.name_raw for i in remaining],
                translation_llm=translation_llm,
            )
            keyboard = (
                to_aiogram_keyboard(
                    build_shopping_keyboard(
                        [i.id for i in remaining if i.id], lang=user.lang
                    )
                )
                if remaining
                else None
            )
            try:
                await cb.message.edit_text(
                    render_shopping_list(remaining, lang=user.lang, names=shop_names),
                    reply_markup=keyboard,
                )
            except Exception as exc:
                log.warning("shopping_edit_failed", extra={"error_class": type(exc).__name__})
            await cb.answer("bought ✓" if ok else "already done")
            return

        if action.verb == "fav_cook":
            recipe_id = action.item_id
            assert recipe_id is not None
            saved = load_saved(
                session, household_id=user.household_id, recipe_id=recipe_id
            )
            if saved is None:
                await cb.answer("not found")
                return
            shopping = recook_shopping_list(
                session, household_id=user.household_id, saved=saved, today=today
            )
            recipe = recipe_from_saved(saved)
            recook_texts = [
                recipe.title,
                recipe.cuisine,
                recipe.method_gist,
                *(i.name for i in recipe.ingredients),
                *shopping,
            ]
            recook_names = await _translate_for_render(
                session,
                lang=user.lang,
                texts=recook_texts,
                translation_llm=translation_llm,
            )
            await cb.message.answer(
                render_recook(
                    recipe, shopping=shopping, lang=user.lang, names=recook_names
                )
            )
            await cb.answer("here's the plan")
            return

        if action.verb == "show_all":
            rows = list_digest_due(session, household_id=user.household_id, today=today)
            if not rows:
                await cb.answer("nothing due")
                return
            row_names = _cached_names_for_render(
                session,
                lang=user.lang,
                texts=[i.raw_name for i in rows],
            )
            rendered = render_digest(
                rows, today=today, lang=user.lang, names=row_names, cap=None
            )
            keyboard = to_aiogram_keyboard(
                build_digest_keyboard(
                    rendered.rendered_items,
                    has_more=False,
                    today=today,
                    lang=user.lang,
                    names=row_names,
                )
            )
            await _safe_edit_cb(cb, rendered.text, keyboard)
            await cb.answer()
            return

        if action.verb in ("apply", "cancel"):
            pending_id = action.item_id
            assert pending_id is not None
            await _handle_pending_callback(
                cb,
                session=session,
                today=today,
                household_id=user.household_id,
                pending_id=pending_id,
                verb=action.verb,
                lang=user.lang,
            )
            return

        if action.verb in ("undo_receipt", "undo_add"):
            target_id = action.item_id
            assert target_id is not None
            now = datetime.now(timezone.utc)
            if action.verb == "undo_receipt":
                result = undo_receipt(
                    session,
                    household_id=user.household_id,
                    receipt_id=target_id,
                    now=now,
                )
            else:
                result = undo_add(
                    session,
                    household_id=user.household_id,
                    item_id=target_id,
                    now=now,
                )
            try:
                await cb.message.edit_text(
                    render_undo_result(result, lang=user.lang)
                )
            except Exception as exc:
                log.warning(
                    "undo_edit_failed", extra={"error_class": type(exc).__name__}
                )
            await cb.answer("undone" if result.removed_ids else "nothing undone")
            return

        item_id = action.item_id
        assert item_id is not None
        try:
            if action.verb == "ate":
                result = mark_eaten(
                    session,
                    household_id=user.household_id,
                    item_id=item_id,
                    today=today,
                )
            elif action.verb == "toss":
                result = mark_tossed(
                    session,
                    household_id=user.household_id,
                    item_id=item_id,
                    today=today,
                )
            elif action.verb == "snooze2":
                result = snooze_item(
                    session,
                    household_id=user.household_id,
                    item_id=item_id,
                    today=today,
                    days=2,
                )
            elif action.verb in ("freeze", "fridge"):
                result = await move_to_storage(
                    session,
                    household_id=user.household_id,
                    item_id=item_id,
                    state="frozen" if action.verb == "freeze" else "fridge",
                    today=today,
                    search=_select_search(search, user.llm_provider),
                )
            else:
                await dispatch_answer(cb, "unrecognized action")
                return
        except NotOwnerOrMissing:
            await dispatch_answer(cb, "item not found")
            await refresh_items_for_origin()
            return
        if result.applied:
            log.info(
                "item_action_applied",
                extra={
                    "user_id": cb.from_user.id,
                    "item_id": item_id,
                    "action": action.verb,
                },
            )
        # Acknowledge before the (slow) translate+render in the refresh.
        await dispatch_answer(
            cb,
            f"#{item_id} -> {action.verb}"
            if result.applied
            else f"#{item_id} already updated",
        )
        await refresh_items_for_origin()


async def _handle_pending_callback(
    cb,
    *,
    session: Session,
    today: date,
    household_id: int,
    pending_id: int,
    verb: str,
    lang: str = "en",
) -> None:
    pending = load_pending(session, household_id=household_id, pending_id=pending_id)
    if pending is None:
        await cb.answer("not found")
        return

    now = utc_naive(datetime.now(timezone.utc))
    if pending.status != "pending" or pending.expires_at <= now:
        terminal = pending.status if pending.status != "pending" else "expired"
        if terminal == "expired" and pending.status == "pending":
            pending.status = "expired"
            session.add(pending)
            session.commit()
        try:
            await cb.message.edit_text(render_terminal_state(terminal, lang=lang))
        except Exception as exc:
            log.warning(
                "pending_message_edit_failed",
                extra={"error_class": type(exc).__name__},
            )
        await cb.answer(f"already {terminal}")
        return

    if verb == "cancel":
        mark_cancelled(session, pending=pending)
        session.commit()
        try:
            await cb.message.edit_text(render_terminal_state("cancelled", lang=lang))
        except Exception as exc:
            log.warning(
                "pending_message_edit_failed",
                extra={"error_class": type(exc).__name__},
            )
        await cb.answer("cancelled")
        return

    if pending.action_type == "correct":
        payload = correct_payload_from_json(pending.proposed_json)
        assert pending.item_id is not None
        item = session.get(PantryItem, pending.item_id)
        if item is None:
            mark_cancelled(session, pending=pending)
            session.commit()
            try:
                await cb.message.edit_text("Item gone - proposal cancelled.")
            except Exception as exc:
                log.warning(
                    "pending_message_edit_failed",
                    extra={"error_class": type(exc).__name__},
                )
            await cb.answer("item gone")
            return
        if item.status != "active":
            mark_cancelled(session, pending=pending)
            session.commit()
            try:
                await cb.message.edit_text(
                    "Item is no longer active - proposal cancelled."
                )
            except Exception as exc:
                log.warning(
                    "pending_message_edit_failed",
                    extra={"error_class": type(exc).__name__},
                )
            await cb.answer("item no longer active")
            return
        assert item.id is not None
        expire_for_item(
            session,
            household_id=household_id,
            item_id=item.id,
            exclude_pending_id=pending.id,
        )
        apply_correct(session, household_id=household_id, item=item, payload=payload)
        mark_applied(session, pending=pending)
        session.commit()
        try:
            await cb.message.edit_text(
                render_applied_correction(
                    item_id=item.id, payload=payload, lang=lang
                )
            )
        except Exception as exc:
            log.warning(
                "pending_message_edit_failed",
                extra={"error_class": type(exc).__name__},
            )
        log.info(
            "item_action_applied",
            extra={
                "user_id": cb.from_user.id,
                "item_id": item.id,
                "action": "correct",
            },
        )
        await cb.answer("applied")
        return

    if pending.action_type != "add":
        log.warning(
            "unknown_pending_action_type", extra={"action_type": pending.action_type}
        )
        await cb.answer("unknown action")
        return
    payload = add_payload_from_json(pending.proposed_json)
    new_id = apply_add(session, household_id=household_id, payload=payload, today=today)
    mark_applied(session, pending=pending)
    session.commit()
    try:
        await cb.message.edit_text(
            render_applied_add(item_id=new_id, payload=payload, lang=lang),
            reply_markup=to_aiogram_keyboard(
                build_undo_add_keyboard(item_id=new_id, lang=lang)
            ),
        )
    except Exception as exc:
        log.warning(
            "pending_message_edit_failed",
            extra={"error_class": type(exc).__name__},
        )
    log.info(
        "item_action_applied",
        extra={"user_id": cb.from_user.id, "item_id": new_id, "action": "add"},
    )
    await cb.answer("added")


async def _refresh_digest_message(
    cb, session, household_id: int, today: date, *, lang: str = "en", translation_llm=None
) -> None:
    remaining = list_digest_due(session, household_id=household_id, today=today)
    if remaining:
        names = _cached_names_for_render(
            session,
            lang=lang,
            texts=[i.raw_name for i in remaining],
        )
        rendered = render_digest(remaining, today=today, lang=lang, names=names)
        keyboard = to_aiogram_keyboard(
            build_digest_keyboard(
                rendered.rendered_items,
                has_more=rendered.has_more,
                today=today,
                lang=lang,
                names=names,
            )
        )
        await edit_or_resend(cb, rendered.text, keyboard)
        return
    await edit_or_resend(cb, t("digest.pantry_clear", lang))


async def _refresh_pantry_message(
    cb, session, household_id: int, today: date, *, lang: str = "en", translation_llm=None
) -> None:
    remaining = list_active(
        session,
        household_id=household_id,
        f=ListFilter.default(),
        today=today,
    )
    if remaining:
        names = _cached_names_for_render(
            session,
            lang=lang,
            texts=[i.raw_name for i in remaining],
        )
        rendered = render_digest(remaining, today=today, lang=lang, names=names, cap=None)
        keyboard = to_aiogram_keyboard(
            build_digest_keyboard(
                rendered.rendered_items,
                has_more=False,
                today=today,
                lang=lang,
                names=names,
                back_to="all",
            )
        )
        await edit_or_resend(cb, rendered.text, keyboard)
        return
    await edit_or_resend(cb, t("pantry.all_clear", lang))


def to_aiogram_keyboard(rows: list[list[CallbackButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=button.text, callback_data=button.callback_data
                )
                for button in row
            ]
            for row in rows
        ]
    )


# The command roster: each row is (command name, handler, the injected deps the
# handler needs). `build_dispatcher` binds the live deps and registers each row,
# so adding a command is one line here instead of a forwarding closure plus a
# separate `register` call. The two non-command message routes (correction reply,
# photo) and the callback router are wired explicitly below because their triggers
# and dispatch differ.
_MESSAGE_COMMANDS: tuple[tuple[str, Callable[..., Awaitable[None]], tuple[str, ...]], ...] = (
    ("start", handle_start, ("session_factory", "on_user_created", "bot")),
    ("invite", handle_invite, ("session_factory", "bot", "on_user_created")),
    ("join", handle_join, ("session_factory", "on_user_created", "bot")),
    ("household", handle_household, ("session_factory", "on_user_created")),
    ("leave", handle_leave, ("session_factory", "unschedule", "on_user_created")),
    ("remove", handle_remove, ("session_factory", "unschedule", "on_user_created")),
    ("tz", handle_tz, ("session_factory", "reschedule")),
    ("lang", handle_lang, ("session_factory", "on_user_created")),
    ("digest_at", handle_digest_at, ("session_factory", "reschedule")),
    ("list", handle_list, ("session_factory", "now_provider", "on_user_created", "translation_llm")),
    ("pantry", handle_pantry, ("session_factory", "now_provider", "on_user_created", "translation_llm")),
    ("add", handle_add, ("session_factory", "now_provider", "text_llm", "on_user_created", "search")),
    ("ate", handle_ate, ("session_factory", "now_provider", "on_user_created")),
    ("toss", handle_toss, ("session_factory", "now_provider", "on_user_created")),
    ("delete", handle_delete, ("session_factory", "now_provider", "on_user_created")),
    ("snooze", handle_snooze, ("session_factory", "now_provider", "on_user_created")),
    ("correct", handle_correct, ("session_factory", "now_provider", "text_llm", "on_user_created")),
    ("stats", handle_stats, ("session_factory", "now_provider", "on_user_created")),
    ("cook", handle_cook, ("session_factory", "now_provider", "on_user_created")),
    ("shopping", handle_shopping, ("session_factory", "now_provider", "on_user_created", "translation_llm")),
    ("favorites", handle_favorites, ("session_factory", "on_user_created", "translation_llm")),
    ("llm", handle_llm, ("session_factory", "llm", "text_llm", "on_user_created")),
    ("prefs", handle_prefs, ("session_factory", "profile_llm", "on_user_created")),
    ("help", handle_help, ("session_factory", "on_user_created")),
)


def build_dispatcher(
    *,
    bot: Bot,
    session_factory: _SessionFactory,
    llm: LLMClient,
    text_llm: TextLLMClient,
    profile_llm: ProfileUpdateLLMClient,
    now_provider: NowProvider,
    on_user_created: Callable[[User], None],
    reschedule: Callable[[User], None],
    unschedule: Callable[[int], None] = lambda _telegram_id: None,
    search: ShelfLifeSearchClient | None = None,
    selection_llm=None,
    recipe_llm=None,
    nutrition_llm=None,
    translation_llm=None,
    alerter=None,
    recipe_sources=(),
) -> Dispatcher:
    dispatcher = Dispatcher()

    async def downloader(file_id: str) -> bytes:
        telegram_file = await bot.get_file(file_id)
        if telegram_file.file_path is None:
            raise RuntimeError("telegram file path missing")
        downloaded = await bot.download_file(telegram_file.file_path)
        if downloaded is None:
            raise RuntimeError("telegram download returned no file object")
        return downloaded.read()

    deps = {
        "session_factory": session_factory,
        "now_provider": now_provider,
        "on_user_created": on_user_created,
        "reschedule": reschedule,
        "unschedule": unschedule,
        "llm": llm,
        "text_llm": text_llm,
        "profile_llm": profile_llm,
        "search": search,
        "translation_llm": translation_llm,
        "bot": bot,
        "photo_downloader": downloader,
        "spawn": asyncio.create_task,
    }

    def _bind(handler, *dep_names):
        kwargs = {name: deps[name] for name in dep_names}

        async def _registered(event):
            await handler(event, **kwargs)

        return _registered

    for _name, _handler, _dep_names in _MESSAGE_COMMANDS:
        dispatcher.message.register(_bind(_handler, *_dep_names), Command(_name))

    dispatcher.message.register(
        _bind(
            handle_correct_reply,
            "session_factory",
            "now_provider",
            "text_llm",
            "on_user_created",
        ),
        F.text & F.reply_to_message,
    )
    dispatcher.message.register(
        _bind(
            handle_photo,
            "session_factory",
            "now_provider",
            "llm",
            "photo_downloader",
            "on_user_created",
            "search",
            "spawn",
            "bot",
            "translation_llm",
        ),
        F.photo,
    )

    async def on_callback(callback):
        if (callback.data or "").startswith(
            ("cookpick:", "cookalt:", "cookmore:", "cookmore2:", "cookadj:")
        ):
            if selection_llm is None or recipe_llm is None or nutrition_llm is None:
                await callback.answer(
                    "cook is not configured yet", show_alert=False
                )
                return
            await handle_cook_callback(
                callback,
                session_factory=session_factory,
                now_provider=now_provider,
                selection_llm=selection_llm,
                recipe_llm=recipe_llm,
                nutrition_llm=nutrition_llm,
                spawn=asyncio.create_task,
                bot=bot,
                translation_llm=translation_llm,
                recipe_sources=recipe_sources,
            )
            return
        if (callback.data or "").startswith("item:"):
            await handle_item_callback(
                callback,
                session_factory=session_factory,
                now_provider=now_provider,
                translation_llm=translation_llm,
            )
            return
        await handle_callback(
            callback,
            session_factory=session_factory,
            now_provider=now_provider,
            translation_llm=translation_llm,
            search=search,
        )

    async def on_error(event) -> bool:
        exc = event.exception
        log.error(
            "unhandled_update_error",
            extra={"error_class": type(exc).__name__, "error": str(exc)},
        )
        if alerter is not None:
            await alerter.alert("handler_error", f"{type(exc).__name__}: {exc}")
        return True  # handled: aiogram must not escalate

    dispatcher.errors.register(on_error)
    dispatcher.callback_query.register(on_callback)
    return dispatcher
