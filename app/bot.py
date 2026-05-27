from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlmodel import Session

from app.commands import (
    CommandError,
    parse_callback,
    parse_correct_args,
    parse_digest_at,
    parse_item_id_arg,
    parse_list_filter,
    parse_snooze_args,
    parse_tz,
)
from app.ingest_service import DuplicateReceipt, ingest_photo, ingest_text
from app.llm import LLMClient
from app.models import User
from app.pantry_service import (
    NotOwnerOrMissing,
    compute_stats,
    correct_item,
    list_active,
    list_digest_due,
    mark_eaten,
    mark_removed,
    mark_tossed,
    snooze_item,
)
from app.renderer import (
    CallbackButton,
    build_digest_keyboard,
    render_digest,
    render_ingest_reply,
    render_list,
    render_stats,
)


DEFAULT_TZ = "America/Detroit"
DEFAULT_DIGEST_HOUR = 8
ALLOWED_TELEGRAM_USER_ID: int = 0

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
        created_at=datetime.now(timezone.utc),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return AuthDecision(True, user, True, "created")


def _noop_user_created(user: User) -> None:
    pass


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
    if decision.created:
        on_user_created(decision.user)
    return decision.user


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
        if decision.created:
            on_user_created(decision.user)
        await msg.answer(
            "Pantry bot ready.\n"
            f"Timezone: {decision.user.tz} (change with /tz <IANA>)\n"
            f"Daily digest hour: {decision.user.digest_hour}:00 "
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
            list_filter = parse_list_filter((msg.text or "").split()[1:])
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        today = now_provider(user.tz).date()
        items = list_active(session, user_id=user.telegram_id, f=list_filter, today=today)
        await msg.answer(render_list(items, today=today))


async def handle_add(
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
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await msg.answer("usage: /add <items, separated, by, commas>")
            return
        summary = ingest_text(
            session,
            user_id=user.telegram_id,
            text=parts[1].strip(),
            today=now_provider(user.tz).date(),
        )
        lines = []
        if summary.inserted_count:
            lines.append(f"Added {summary.inserted_count} items:")
            lines.extend(
                f"  - #{item_id} {name}"
                for item_id, name in zip(summary.inserted_ids, summary.inserted_names)
            )
        if summary.failed_parts:
            lines.append("Couldn't add:")
            lines.extend(
                f"  - {raw!r}: {reason}"
                for raw, reason in zip(summary.failed_parts, summary.failed_reasons)
            )
        await msg.answer("\n".join(lines) or "nothing parsed")


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
        try:
            item_id = parse_item_id_arg(parts[1].strip())
            result = fn(
                session,
                user_id=user.telegram_id,
                item_id=item_id,
                today=now_provider(user.tz).date(),
            )
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        except NotOwnerOrMissing:
            await msg.answer(f"no item #{item_id}")
            return
        if result.applied:
            log.info(
                "item_action_applied",
                extra={"user_id": user.telegram_id, "item_id": item_id, "action": action_word},
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
        try:
            item_id, days = parse_snooze_args((msg.text or "").split()[1:])
            result = snooze_item(
                session,
                user_id=user.telegram_id,
                item_id=item_id,
                today=now_provider(user.tz).date(),
                days=days,
            )
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        except NotOwnerOrMissing:
            await msg.answer(f"no item #{item_id}")
            return
        except ValueError as exc:
            await msg.answer(str(exc))
            return
        if result.applied:
            log.info(
                "item_action_applied",
                extra={"user_id": user.telegram_id, "item_id": item_id, "action": "snooze"},
            )
            await msg.answer(f"#{item_id} snoozed for {days}d")
        else:
            await msg.answer(f"#{item_id} is not active")


async def handle_correct(
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
        try:
            item_id, days = parse_correct_args((msg.text or "").split()[1:])
            pantry_item = correct_item(
                session,
                user_id=user.telegram_id,
                item_id=item_id,
                days=days,
                today=now_provider(user.tz).date(),
            )
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        except NotOwnerOrMissing:
            await msg.answer(f"no item #{item_id}")
            return
        except ValueError as exc:
            await msg.answer(str(exc))
            return
        await msg.answer(
            f"#{item_id} {pantry_item.raw_name}: shelf life set to {days}d, "
            f"expires {pantry_item.expires_on}. Future estimates for "
            f"\"{pantry_item.normalized_name}\" will use this value."
        )


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


HELP_TEXT = (
    "Commands:\n"
    "  /start - setup status\n"
    "  /tz <IANA> - set timezone\n"
    "  /digest_at <0..23> - set digest hour\n"
    "  /list [category|week|expired] - show pantry\n"
    "  /add <items, by, commas> - manual entry; trailing `7d` sets shelf life\n"
    "  /ate <id> - mark eaten\n"
    "  /toss <id> - mark tossed\n"
    "  /snooze <id> [days=2] - suppress reminders 1..30d\n"
    "  /correct <id> <days> - fix shelf life and teach future estimates\n"
    "  /delete <id> - remove a wrong/duplicate import\n"
    "  /stats - last 30 days\n"
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
            summary = await ingest_photo(
                session,
                llm,
                user_id=user.telegram_id,
                photo_file_id=file_id,
                image_bytes=await photo_downloader(file_id),
                today=today,
            )
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
        await msg.answer(render_ingest_reply(summary, today=today))


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

        try:
            if action.verb == "ate":
                result = mark_eaten(
                    session, user_id=cb.from_user.id, item_id=action.item_id, today=today
                )
            elif action.verb == "toss":
                result = mark_tossed(
                    session, user_id=cb.from_user.id, item_id=action.item_id, today=today
                )
            elif action.verb == "snooze2":
                result = snooze_item(
                    session,
                    user_id=cb.from_user.id,
                    item_id=action.item_id,
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
                    "item_id": action.item_id,
                    "action": action.verb,
                },
            )
            await _refresh_digest_message(cb, session, user.telegram_id, today)
        await cb.answer(
            f"#{action.item_id} -> {action.verb}"
            if result.applied
            else f"#{action.item_id} already updated"
        )


async def _refresh_digest_message(cb, session, user_id: int, today) -> None:
    remaining = list_digest_due(session, user_id=user_id, today=today)
    if remaining:
        rendered = render_digest(remaining, today=today)
        keyboard = to_aiogram_keyboard(
            build_digest_keyboard(rendered.rendered_item_ids, has_more=rendered.has_more)
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
                InlineKeyboardButton(text=button.text, callback_data=button.callback_data)
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
    now_provider: NowProvider,
    on_user_created: Callable[[User], None],
    reschedule: Callable[[User], None],
) -> Dispatcher:
    dispatcher = Dispatcher()

    async def downloader(file_id: str) -> bytes:
        telegram_file = await bot.get_file(file_id)
        downloaded = await bot.download_file(telegram_file.file_path)
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
        await handle_digest_at(message, session_factory=session_factory, reschedule=reschedule)

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
            on_user_created=on_user_created,
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
            on_user_created=on_user_created,
        )

    async def on_stats(message):
        await handle_stats(
            message,
            session_factory=session_factory,
            now_provider=now_provider,
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
        )

    async def on_callback(callback):
        await handle_callback(callback, session_factory=session_factory, now_provider=now_provider)

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
    dispatcher.message.register(on_help, Command("help"))
    dispatcher.message.register(on_photo, F.photo)
    dispatcher.callback_query.register(on_callback)
    return dispatcher
