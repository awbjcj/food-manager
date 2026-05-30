from __future__ import annotations

import asyncio
import json as _json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional, cast

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import update
from sqlmodel import Session

from app.commands import (
    CommandError,
    parse_callback,
    parse_digest_at,
    parse_item_id_arg,
    parse_llm_provider,
    parse_list_filter,
    parse_snooze_args,
    parse_tz,
)
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
from app.cook_models import ScoredCandidate
from app.cook_service import NotEnoughItems, run_cook
from app.cook_session_service import (
    create_cook_session,
    load_cook_session,
    mark_status,
    set_message_id as set_cook_message_id,
)
from app.ingest_service import DuplicateReceipt, ingest_photo
from app.llm import (
    LLMClient,
    LLMProviderNotConfigured,
    ProfileUpdateLLMClient,
    TextLLMClient,
)
from app.models import CookSession, PantryItem, User
from app.refine_service import run_receipt_refine
from app.pantry_service import (
    NotOwnerOrMissing,
    compute_stats,
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
    build_cook_alternatives_keyboard,
    build_cook_round_keyboard,
    build_digest_keyboard,
    build_undo_add_keyboard,
    build_undo_keyboard,
    render_add_diff,
    render_applied_add,
    render_applied_correction,
    render_correction_diff,
    render_cook_result,
    render_digest,
    render_ingest_reply,
    render_list,
    render_profile,
    render_stats,
    render_terminal_state,
    render_undo_result,
)
from app.profile_service import profile_from_user, update_profile_from_sentence

DEFAULT_TZ = "America/Detroit"
DEFAULT_DIGEST_HOUR = 8
DEFAULT_LLM_PROVIDER = "anthropic"
ALLOWED_TELEGRAM_USER_ID: int = 0
MEAL_TYPES = ["Dinner", "Lunch", "Breakfast", "Dessert", "Snack", "Surprise me"]
DEFAULT_CUISINES = ["Italian", "Mexican", "Chinese", "American", "Surprise me"]

_SessionFactory = Callable[[], Session]
NowProvider = Callable[[str], datetime]
log = logging.getLogger(__name__)


@dataclass
class AuthDecision:
    allowed: bool
    user: Optional[User]
    created: bool
    reason: str


def authorize_and_get_user(
    session: Session,
    *,
    allowed_user_id: int,
    telegram_user_id: int,
    chat_id: int,
    chat_type: str,
) -> AuthDecision:
    if telegram_user_id != allowed_user_id:
        return AuthDecision(False, None, False, "not authorized")
    if chat_type != "private":
        return AuthDecision(False, None, False, "this bot only works in private chat")

    existing = session.get(User, telegram_user_id)
    if existing is not None:
        return AuthDecision(True, existing, False, "ok")

    user = User(
        telegram_id=telegram_user_id,
        chat_id=chat_id,
        tz=DEFAULT_TZ,
        digest_hour=DEFAULT_DIGEST_HOUR,
        llm_provider=DEFAULT_LLM_PROVIDER,
        created_at=datetime.now(timezone.utc),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return AuthDecision(True, user, True, "created")


def _noop_user_created(user: User) -> None:
    pass


def _require_user(user: User | None) -> User:
    assert user is not None
    return user


def _available_llm_providers(
    llm: LLMClient, text_llm: TextLLMClient
) -> tuple[str, ...]:
    image_providers = set(getattr(llm, "available_providers", ("anthropic",)))
    text_providers = set(getattr(text_llm, "available_providers", ("anthropic",)))
    return tuple(sorted(image_providers & text_providers))


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


def _select_profile_llm(profile_llm: "ProfileUpdateLLMClient", provider: str):
    selector = getattr(profile_llm, "for_provider", None)
    return selector(provider) if callable(selector) else profile_llm


def _render_llm_status(user: User, llm: LLMClient, text_llm: TextLLMClient) -> str:
    available = _available_llm_providers(llm, text_llm)
    return (
        f"LLM provider: {user.llm_provider}\n"
        f"Available: {', '.join(available) if available else 'none'}\n"
        "Usage: /llm [anthropic|openai]"
    )


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


async def handle_start(
    msg,
    *,
    session_factory: _SessionFactory,
    on_user_created: Callable[[User], None],
) -> None:
    with session_factory() as session:
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
            "Pantry bot ready.\n"
            f"Timezone: {user.tz} (change with /tz <IANA>)\n"
            f"Daily digest hour: {user.digest_hour}:00 "
            "(change with /digest_at <0..23>)\n"
            "Type /help to see all commands."
        )


async def handle_tz(
    msg, *, session_factory: _SessionFactory, reschedule: Callable[[User], None]
) -> None:
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=reschedule)
        if user is None:
            return
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


async def handle_digest_at(
    msg, *, session_factory: _SessionFactory, reschedule: Callable[[User], None]
) -> None:
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=reschedule)
        if user is None:
            return
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
) -> None:
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None:
            return
        try:
            list_filter = parse_list_filter(list((msg.text or "").split()[1:]))
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        today = now_provider(user.tz).date()
        items = list_active(
            session, user_id=user.telegram_id, f=list_filter, today=today
        )
        await msg.answer(render_list(items, today=today))


async def handle_add(
    msg,
    *,
    session_factory: _SessionFactory,
    now_provider: NowProvider,
    text_llm: TextLLMClient,
    on_user_created: Callable[[User], None] = _noop_user_created,
    search=None,
) -> None:
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None:
            return
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await msg.answer("usage: /add <free text - name, category, expiry>")
            return
        today = now_provider(user.tz).date()
        try:
            selected_text_llm = _select_text_llm_client(text_llm, user.llm_provider)
            proposals, _ = await propose_add(
                session,
                llm=selected_text_llm,
                user_id=user.telegram_id,
                user_text=parts[1].strip(),
                today=today,
                tz=user.tz,
                search=search,
            )
        except LLMProviderNotConfigured:
            await msg.answer(
                f"LLM provider {user.llm_provider!r} is not configured. Use /llm."
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
            await msg.answer("couldn't parse that add - try simpler wording")
            return
        if not proposals:
            await msg.answer("usage: /add <free text - name, category, expiry>")
            return

        for proposal in proposals:
            pending = create_pending(
                session,
                user_id=user.telegram_id,
                action_type="add",
                item_id=None,
                proposed_json=add_payload_to_json(proposal.payload),
                snapshot_json=None,
                cost_micros_usd=proposal.cost_share,
                chat_id=msg.chat.id,
                now=datetime.now(timezone.utc),
            )
            assert pending.id is not None
            text = render_add_diff(pending_id=pending.id, payload=proposal.payload)
            keyboard = to_aiogram_keyboard(
                build_apply_cancel_keyboard(pending_id=pending.id)
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
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None:
            return
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
                user_id=user.telegram_id,
                item_id=item_id,
                today=now_provider(user.tz).date(),
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
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None:
            return
        item_id: int
        try:
            item_id, days = parse_snooze_args(list((msg.text or "").split()[1:]))
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        try:
            result = snooze_item(
                session,
                user_id=user.telegram_id,
                item_id=item_id,
                today=now_provider(user.tz).date(),
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


async def handle_correct(
    msg,
    *,
    session_factory,
    now_provider,
    text_llm: TextLLMClient,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None:
            return
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
        if item is None or item.user_id != user.telegram_id:
            await msg.answer(f"no item #{item_id}")
            return
        if item.status != "active":
            await msg.answer(f"#{item_id} is {item.status}; cannot correct")
            return
        today = now_provider(user.tz).date()
        try:
            selected_text_llm = _select_text_llm_client(text_llm, user.llm_provider)
            payload, cost = await propose_correct(
                session,
                llm=selected_text_llm,
                user_id=user.telegram_id,
                item=item,
                user_text=parts[2].strip(),
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
            user_id=user.telegram_id,
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
        )
        keyboard = to_aiogram_keyboard(
            build_apply_cancel_keyboard(pending_id=pending.id)
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


async def handle_stats(
    msg,
    *,
    session_factory,
    now_provider,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None:
            return
        now = now_provider(user.tz)
        stats = compute_stats(
            session,
            user_id=user.telegram_id,
            now=now.astimezone(timezone.utc),
        )
        await msg.answer(render_stats(stats))


async def handle_cook(
    msg,
    *,
    session_factory: _SessionFactory,
    now_provider: NowProvider,
    on_user_created: Callable[[User], None] = _noop_user_created,
) -> None:
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None:
            return
        now = now_provider(user.tz)
        cook = create_cook_session(
            session,
            user_id=user.telegram_id,
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
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None:
            return
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
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None:
            return
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await msg.answer(render_profile(profile_from_user(user)))
            return
        try:
            selected = _select_profile_llm(profile_llm, user.llm_provider)
            profile, _ = await update_profile_from_sentence(
                session, llm=selected, user=user, sentence=parts[1].strip(),
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
        await msg.answer("Updated.\n\n" + render_profile(profile))


HELP_TEXT = (
    "Commands:\n"
    "  /start - setup status\n"
    "  /tz <IANA> - set timezone\n"
    "  /digest_at <0..23> - set digest hour\n"
    "  /list [category|week|expired] - show pantry\n"
    "  /add <free text> - propose new items in natural language.\n"
    "      Replies with a diff per item; tap Apply or Cancel.\n"
    "      Proposals expire after 10 min.\n"
    "  /ate <id> - mark eaten\n"
    "  /toss <id> - mark tossed\n"
    "  /snooze <id> [days=2] - suppress reminders 1..30d\n"
    "  /correct <id> <free text> - propose a correction in natural\n"
    "      language (name, category, expires, days). Replies with a\n"
    "      diff; tap Apply or Cancel. Proposal expires after 10 min.\n"
    "  /delete <id> - remove a wrong/duplicate import\n"
    "  /stats - last 30 days\n"
    "  /llm [anthropic|openai] - show or switch LLM provider\n"
    "  /prefs [sentence] - show or update your food profile\n"
    "  /cook - get a recipe from your pantry\n"
    "  /help - this message\n"
    "Send a receipt photo to log it."
)


async def handle_help(
    msg,
    *,
    session_factory,
    on_user_created: Callable[[User], None] = _noop_user_created,
) -> None:
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None:
            return
    await msg.answer(HELP_TEXT)


async def handle_photo(
    msg,
    *,
    session_factory,
    now_provider,
    llm: LLMClient,
    photo_downloader: Callable[[str], Awaitable[bytes]],
    on_user_created: Callable[[User], None] = _noop_user_created,
    search=None,
    spawn=None,
    bot=None,
) -> None:
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None:
            return
        if not msg.photo:
            await msg.answer("send a photo of a receipt")
            return
        file_id = msg.photo[-1].file_id
        today = now_provider(user.tz).date()
        log.info(
            "receipt_ingest_started",
            extra={"user_id": user.telegram_id, "photo_file_id": file_id},
        )
        try:
            selected_llm = _select_llm_client(llm, user.llm_provider)
            summary = await ingest_photo(
                session,
                selected_llm,
                user_id=user.telegram_id,
                photo_file_id=file_id,
                image_bytes=await photo_downloader(file_id),
                today=today,
            )
        except LLMProviderNotConfigured:
            await msg.answer(
                f"LLM provider {user.llm_provider!r} is not configured. Use /llm."
            )
            return
        except DuplicateReceipt:
            await msg.answer("this receipt was already logged")
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
            await msg.answer(
                "couldn't read that one - try a clearer photo or /add <items> manually"
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
        keyboard = (
            to_aiogram_keyboard(build_undo_keyboard(receipt_id=summary.receipt_id))
            if summary.receipt_id is not None and summary.inserted_food_count
            else None
        )
        refine_user_id = user.telegram_id
        sent = await msg.answer(
            render_ingest_reply(summary, today=today), reply_markup=keyboard
        )

    if (
        search is not None
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
                search,
                item_ids=item_ids,
                summary=summary,
                user_id=refine_user_id,
                receipt_id=receipt_id,
                today=today,
            )
            if not refined:
                return
            text = render_ingest_reply(summary, today=today, refined_ids=refined)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=to_aiogram_keyboard(
                        build_undo_keyboard(receipt_id=receipt_id)
                    ),
                )
            except Exception as exc:
                log.warning(
                    "refine_edit_failed", extra={"error_class": type(exc).__name__}
                )

        spawn(_run_refine())


def _cuisine_options(user: User) -> list[str]:
    try:
        prefs = _json.loads(user.preferred_cuisines_json or "[]")
    except (TypeError, ValueError):
        prefs = []
    options = [str(c).title() for c in prefs if str(c).strip()]
    if not options:
        options = list(DEFAULT_CUISINES)
    elif "Surprise me" not in options:
        options = options[:4] + ["Surprise me"]
    return options[:5]


def _select_cook(client, provider: str):
    selector = getattr(client, "for_provider", None)
    return selector(provider) if callable(selector) else client


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
) -> None:
    if cb.from_user.id != ALLOWED_TELEGRAM_USER_ID:
        log.info(
            "unauthorized_update_rejected",
            extra={"telegram_user_id": cb.from_user.id},
        )
        await cb.answer("not authorized", show_alert=False)
        return
    try:
        action = parse_callback(cb.data or "")
    except CommandError:
        await cb.answer("unrecognized action")
        return
    if action.verb not in ("cook_pick", "cook_alt") or action.item_id is None:
        await cb.answer("unrecognized action")
        return

    with session_factory() as session:
        user = session.get(User, cb.from_user.id)
        if user is None:
            await cb.answer("not configured")
            return
        cook = load_cook_session(
            session, user_id=user.telegram_id, cook_id=action.item_id
        )
        if cook is None or cook.status not in ("collecting", "ready", "done"):
            await cb.answer("this cook session expired - start a new /cook")
            return
        if cook.status in ("collecting", "ready"):
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
            await _safe_edit_cb(cb, render_cook_result(cards, show_alternatives=True))
            await cb.answer("showing alternatives")
            return

        option_index = action.option_index
        if option_index is None:
            await cb.answer("unrecognized action")
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
                build_cook_round_keyboard(
                    cook.id, _cuisine_options(user), round_name="cuisine"
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

        if cook.cuisine is not None:
            await cb.answer()
            return

        if action.round_name != "cuisine":
            await cb.answer("unrecognized action")
            return

        cuisine_options = _cuisine_options(user)
        if option_index < 0 or option_index >= len(cuisine_options):
            await cb.answer("unrecognized action")
            return
        chosen_cuisine = cuisine_options[option_index]
        result = session.exec(
            update(CookSession)
            .where(
                CookSession.id == cook.id,
                CookSession.user_id == user.telegram_id,
                CookSession.status == "collecting",
                CookSession.meal_type.is_not(None),  # type: ignore[union-attr]
                CookSession.cuisine.is_(None),  # type: ignore[union-attr]
            )
            .values(cuisine=chosen_cuisine, status="ready")
        )
        session.commit()
        if result.rowcount == 0:
            await cb.answer("already cooking")
            return
        cook = load_cook_session(session, user_id=user.telegram_id, cook_id=cook.id)
        if cook is None:
            await cb.answer("this cook session expired - start a new /cook")
            return
        await _safe_edit_cb(cb, "Thinking...")
        await cb.answer()
        user_id = user.telegram_id
        user_tz = user.tz
        cook_id = cook.id

    if cook_id is None:
        return
    spawn(
        run_cook_and_render(
            session_factory,
            user_id=user_id,
            user_tz=user_tz,
            cook_id=cook_id,
            selection_llm=selection_llm,
            recipe_llm=recipe_llm,
            nutrition_llm=nutrition_llm,
            now_provider=now_provider,
            bot=bot,
        )
    )


async def run_cook_and_render(
    session_factory: _SessionFactory,
    *,
    user_id: int,
    user_tz: str,
    cook_id: int,
    selection_llm,
    recipe_llm,
    nutrition_llm,
    now_provider: NowProvider,
    bot,
) -> None:
    with session_factory() as session:
        cook = load_cook_session(session, user_id=user_id, cook_id=cook_id)
        if cook is None or cook.status != "ready":
            return
        user = session.get(User, user_id)
        if user is None:
            return
        profile = profile_from_user(user)
        chat_id = cook.chat_id
        message_id = cook.message_id
        today = now_provider(user_tz).date()
        selected_selection_llm = _select_cook(selection_llm, user.llm_provider)
        selected_recipe_llm = _select_cook(recipe_llm, user.llm_provider)
        selected_nutrition_llm = _select_cook(nutrition_llm, user.llm_provider)
        try:
            cards = await run_cook(
                session,
                cook=cook,
                profile=profile,
                selection_llm=selected_selection_llm,
                recipe_llm=selected_recipe_llm,
                nutrition_llm=selected_nutrition_llm,
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
        text = render_cook_result(cards, show_alternatives=False)
        keyboard = (
            to_aiogram_keyboard(build_cook_alternatives_keyboard(cook_id))
            if len(cards) > 1
            else None
        )
        await _safe_edit_bot(
            bot, chat_id=chat_id, message_id=message_id, text=text, keyboard=keyboard
        )


async def handle_callback(cb, *, session_factory, now_provider) -> None:
    if cb.from_user.id != ALLOWED_TELEGRAM_USER_ID:
        log.info(
            "unauthorized_update_rejected",
            extra={"telegram_user_id": cb.from_user.id},
        )
        await cb.answer("not authorized", show_alert=False)
        return
    try:
        action = parse_callback(cb.data)
    except CommandError:
        await cb.answer("unrecognized action")
        return

    with session_factory() as session:
        user = session.get(User, cb.from_user.id)
        if user is None:
            await cb.answer("not configured")
            return
        today = now_provider(user.tz).date()
        if action.verb == "show_all":
            rows = list_digest_due(session, user_id=user.telegram_id, today=today)
            if not rows:
                await cb.answer("nothing due")
                return
            await cb.message.answer(render_list(rows, today=today))
            await cb.answer("sent full digest list")
            return

        if action.verb in ("apply", "cancel"):
            pending_id = action.item_id
            assert pending_id is not None
            await _handle_pending_callback(
                cb,
                session=session,
                today=today,
                pending_id=pending_id,
                verb=action.verb,
            )
            return

        if action.verb in ("undo_receipt", "undo_add"):
            target_id = action.item_id
            assert target_id is not None
            now = datetime.now(timezone.utc)
            if action.verb == "undo_receipt":
                result = undo_receipt(
                    session, user_id=user.telegram_id, receipt_id=target_id, now=now
                )
            else:
                result = undo_add(
                    session, user_id=user.telegram_id, item_id=target_id, now=now
                )
            try:
                await cb.message.edit_text(render_undo_result(result))
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
                    session, user_id=cb.from_user.id, item_id=item_id, today=today
                )
            elif action.verb == "toss":
                result = mark_tossed(
                    session, user_id=cb.from_user.id, item_id=item_id, today=today
                )
            elif action.verb == "snooze2":
                result = snooze_item(
                    session,
                    user_id=cb.from_user.id,
                    item_id=item_id,
                    today=today,
                    days=2,
                )
            else:
                await cb.answer("unrecognized action")
                return
        except NotOwnerOrMissing:
            await cb.answer("item not found")
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
            await _refresh_digest_message(cb, session, user.telegram_id, today)
        await cb.answer(
            f"#{item_id} -> {action.verb}"
            if result.applied
            else f"#{item_id} already updated"
        )


async def _handle_pending_callback(
    cb, *, session: Session, today, pending_id: int, verb: str
) -> None:
    pending = load_pending(session, user_id=cb.from_user.id, pending_id=pending_id)
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
            await cb.message.edit_text(render_terminal_state(terminal))
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
            await cb.message.edit_text(render_terminal_state("cancelled"))
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
            user_id=cb.from_user.id,
            item_id=item.id,
            exclude_pending_id=pending.id,
        )
        apply_correct(session, user_id=cb.from_user.id, item=item, payload=payload)
        mark_applied(session, pending=pending)
        session.commit()
        try:
            await cb.message.edit_text(
                render_applied_correction(item_id=item.id, payload=payload)
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
    new_id = apply_add(session, user_id=cb.from_user.id, payload=payload, today=today)
    mark_applied(session, pending=pending)
    session.commit()
    try:
        await cb.message.edit_text(
            render_applied_add(item_id=new_id, payload=payload),
            reply_markup=to_aiogram_keyboard(build_undo_add_keyboard(item_id=new_id)),
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


async def _refresh_digest_message(cb, session, user_id: int, today) -> None:
    remaining = list_digest_due(session, user_id=user_id, today=today)
    if remaining:
        rendered = render_digest(remaining, today=today)
        keyboard = to_aiogram_keyboard(
            build_digest_keyboard(
                rendered.rendered_item_ids, has_more=rendered.has_more
            )
        )
        try:
            await cb.message.edit_text(rendered.text, reply_markup=keyboard)
        except Exception as exc:
            log.warning(
                "digest_edit_failed",
                extra={"error_class": type(exc).__name__},
            )
        return
    try:
        await cb.message.edit_text("Pantry is clear for the next 7 days.")
    except Exception as exc:
        log.warning(
            "digest_edit_failed",
            extra={"error_class": type(exc).__name__},
        )


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
    search=None,
    selection_llm=None,
    recipe_llm=None,
    nutrition_llm=None,
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

    async def on_start(message):
        await handle_start(
            message,
            session_factory=session_factory,
            on_user_created=on_user_created,
        )

    async def on_tz(message):
        await handle_tz(message, session_factory=session_factory, reschedule=reschedule)

    async def on_digest_at(message):
        await handle_digest_at(
            message, session_factory=session_factory, reschedule=reschedule
        )

    async def on_list(message):
        await handle_list(
            message,
            session_factory=session_factory,
            now_provider=now_provider,
            on_user_created=on_user_created,
        )

    async def on_add(message):
        await handle_add(
            message,
            session_factory=session_factory,
            now_provider=now_provider,
            text_llm=text_llm,
            on_user_created=on_user_created,
            search=search,
        )

    async def on_ate(message):
        await handle_ate(
            message,
            session_factory=session_factory,
            now_provider=now_provider,
            on_user_created=on_user_created,
        )

    async def on_toss(message):
        await handle_toss(
            message,
            session_factory=session_factory,
            now_provider=now_provider,
            on_user_created=on_user_created,
        )

    async def on_delete(message):
        await handle_delete(
            message,
            session_factory=session_factory,
            now_provider=now_provider,
            on_user_created=on_user_created,
        )

    async def on_snooze(message):
        await handle_snooze(
            message,
            session_factory=session_factory,
            now_provider=now_provider,
            on_user_created=on_user_created,
        )

    async def on_correct(message):
        await handle_correct(
            message,
            session_factory=session_factory,
            now_provider=now_provider,
            text_llm=text_llm,
            on_user_created=on_user_created,
        )

    async def on_stats(message):
        await handle_stats(
            message,
            session_factory=session_factory,
            now_provider=now_provider,
            on_user_created=on_user_created,
        )

    async def on_cook(message):
        await handle_cook(
            message,
            session_factory=session_factory,
            now_provider=now_provider,
            on_user_created=on_user_created,
        )

    async def on_llm(message):
        await handle_llm(
            message,
            session_factory=session_factory,
            llm=llm,
            text_llm=text_llm,
            on_user_created=on_user_created,
        )

    async def on_prefs(message):
        await handle_prefs(
            message,
            session_factory=session_factory,
            profile_llm=profile_llm,
            on_user_created=on_user_created,
        )

    async def on_help(message):
        await handle_help(
            message,
            session_factory=session_factory,
            on_user_created=on_user_created,
        )

    async def on_photo(message):
        await handle_photo(
            message,
            session_factory=session_factory,
            now_provider=now_provider,
            llm=llm,
            photo_downloader=downloader,
            on_user_created=on_user_created,
            search=search,
            spawn=asyncio.create_task,
            bot=bot,
        )

    async def on_callback(callback):
        if (callback.data or "").startswith(("cookpick:", "cookalt:")):
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
            )
            return
        await handle_callback(
            callback, session_factory=session_factory, now_provider=now_provider
        )

    dispatcher.message.register(on_start, Command("start"))
    dispatcher.message.register(on_tz, Command("tz"))
    dispatcher.message.register(on_digest_at, Command("digest_at"))
    dispatcher.message.register(on_list, Command("list"))
    dispatcher.message.register(on_add, Command("add"))
    dispatcher.message.register(on_ate, Command("ate"))
    dispatcher.message.register(on_toss, Command("toss"))
    dispatcher.message.register(on_delete, Command("delete"))
    dispatcher.message.register(on_snooze, Command("snooze"))
    dispatcher.message.register(on_correct, Command("correct"))
    dispatcher.message.register(on_stats, Command("stats"))
    dispatcher.message.register(on_cook, Command("cook"))
    dispatcher.message.register(on_llm, Command("llm"))
    dispatcher.message.register(on_prefs, Command("prefs"))
    dispatcher.message.register(on_help, Command("help"))
    dispatcher.message.register(on_photo, F.photo)
    dispatcher.callback_query.register(on_callback)
    return dispatcher
