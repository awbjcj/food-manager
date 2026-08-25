from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import Session, select

from app import views
from app.models import PantryItem, User
from app.pantry_service import list_digest_due
from app.pending_service import sweep_expired
from app.renderer import build_digest_keyboard
from app.telegram_ui import to_aiogram_keyboard

log = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]
TodayProvider = Callable[[str], date]


@dataclass
class DigestPayload:
    user: User
    items: list[PantryItem]


def build_digest_payload(
    session: Session, *, user_id: int, today: date
) -> DigestPayload | None:
    user = session.get(User, user_id)
    if user is None:
        return None
    rows = list_digest_due(session, household_id=user.household_id, today=today)
    if not rows:
        return None
    return DigestPayload(user=user, items=rows)


async def send_digest_once(
    *,
    user_id: int,
    bot,
    session_factory: SessionFactory,
    today_provider: TodayProvider,
    translation_llm=None,
) -> bool:
    with session_factory() as session:
        user = session.get(User, user_id)
        if user is None:
            log.warning("digest_skip_unknown_user", extra={"user_id": user_id})
            return False
        if user.banned:
            log.info("digest_skip_banned_user", extra={"user_id": user_id})
            return False
        today = today_provider(user.tz)
        payload = build_digest_payload(session, user_id=user_id, today=today)
        if payload is None:
            log.info("digest_silent_day", extra={"user_id": user_id, "today": str(today)})
            user.last_digest_date = today
            session.add(user)
            session.commit()
            return False
        rendered = await views.digest(
            session,
            payload.items,
            user=user,
            today=today,
            translation_llm=translation_llm,
        )
        keyboard = build_digest_keyboard(
            rendered.rendered_items,
            has_more=rendered.has_more,
            today=today,
            lang=user.lang,
            names=rendered.names,
        )
        await bot.send_message(
            chat_id=payload.user.chat_id,
            text=rendered.text,
            reply_markup=to_aiogram_keyboard(keyboard),
        )
        log.info(
            "digest_sent",
            extra={"user_id": user_id, "items": rendered.rendered_count},
        )
        user.last_digest_date = today
        session.add(user)
        session.commit()
        return True


async def send_digest_with_retry(
    *,
    user_id: int,
    bot,
    session_factory: SessionFactory,
    today_provider: TodayProvider,
    retry_sleep_seconds: int = 60,
    translation_llm=None,
    on_final_failure: Callable[[int, Exception], Awaitable[None]] | None = None,
) -> None:
    try:
        await send_digest_once(
            user_id=user_id,
            bot=bot,
            session_factory=session_factory,
            today_provider=today_provider,
            translation_llm=translation_llm,
        )
        return
    except Exception as exc:  # noqa: BLE001 - digest send retries, must not crash the scheduler
        log.warning(
            "digest_send_failed",
            extra={
                "user_id": user_id,
                "error_class": type(exc).__name__,
                "attempt": 1,
                "will_retry": True,
            },
        )
    await asyncio.sleep(retry_sleep_seconds)
    try:
        await send_digest_once(
            user_id=user_id,
            bot=bot,
            session_factory=session_factory,
            today_provider=today_provider,
            translation_llm=translation_llm,
        )
    except Exception as exc:  # noqa: BLE001 - digest send retries, must not crash the scheduler
        log.warning(
            "digest_send_failed",
            extra={
                "user_id": user_id,
                "error_class": type(exc).__name__,
                "attempt": 2,
                "will_retry": False,
            },
        )
        if on_final_failure is not None:
            try:
                await on_final_failure(user_id, exc)
            except Exception:  # noqa: BLE001 - the hook is best-effort
                log.warning("digest_failure_hook_failed", extra={"user_id": user_id})


async def catch_up_missed_digests(
    *,
    session_factory: SessionFactory,
    send: Callable[..., Awaitable[None]],
    now_provider: Callable[[str], datetime],
    telegram_user_id: int | None = None,
) -> int:
    """Send digests missed while the process was down.

    A digest is missed when the user's digest hour has already passed today
    (their tz) and no digest run was recorded for today. Called once at
    startup after cron jobs are registered; a crash therefore delays the
    morning digest instead of losing it.
    """
    with session_factory() as session:
        statement = select(User)
        if telegram_user_id is not None:
            statement = statement.where(User.telegram_id == telegram_user_id)
        users = list(session.exec(statement).all())
    sent = 0
    for user in users:
        now = now_provider(user.tz)
        if now.hour < user.digest_hour:
            continue
        if user.last_digest_date is not None and user.last_digest_date >= now.date():
            continue
        log.info(
            "digest_catch_up",
            extra={"user_id": user.telegram_id, "today": str(now.date())},
        )
        await send(user.telegram_id)
        sent += 1
    return sent


def schedule_user_digest(
    scheduler: AsyncIOScheduler,
    user: User,
    *,
    send: Callable[..., Awaitable[None]],
) -> None:
    job_id = f"digest:{user.telegram_id}"
    if scheduler.get_job(job_id) is not None:
        try:
            scheduler.remove_job(job_id)
        except JobLookupError:
            pass
    scheduler.add_job(
        send,
        "cron",
        hour=user.digest_hour,
        minute=0,
        timezone=user.tz,
        args=[user.telegram_id],
        id=job_id,
        replace_existing=True,
    )


def unschedule_user_digest(scheduler: AsyncIOScheduler, telegram_id: int) -> None:
    """Remove a user's digest cron job (e.g. when they leave/are removed)."""
    job_id = f"digest:{telegram_id}"
    if scheduler.get_job(job_id) is not None:
        try:
            scheduler.remove_job(job_id)
        except JobLookupError:
            pass


def register_all_user_digests(
    scheduler: AsyncIOScheduler,
    *,
    session_factory: SessionFactory,
    send: Callable[..., Awaitable[None]],
    telegram_user_id: int | None = None,
) -> None:
    with session_factory() as session:
        statement = select(User)
        if telegram_user_id is not None:
            statement = statement.where(User.telegram_id == telegram_user_id)
        users = list(session.exec(statement).all())
    for user in users:
        schedule_user_digest(scheduler, user, send=send)


def _sweep_job(session_factory: SessionFactory) -> None:
    try:
        with session_factory() as session:
            swept = sweep_expired(session, now=datetime.now(UTC))
            if swept:
                log.info("pending_swept", extra={"count": swept})
    except Exception as exc:  # noqa: BLE001 - sweep job must not crash the scheduler
        log.warning(
            "pending_sweep_failed",
            extra={"error_class": type(exc).__name__},
        )


def register_sweep_expired_pendings(
    scheduler: AsyncIOScheduler, *, session_factory: SessionFactory
) -> None:
    scheduler.add_job(
        _sweep_job,
        "cron",
        minute="*/5",
        timezone="UTC",
        args=[session_factory],
        id="sweep_expired_pendings",
        replace_existing=True,
    )


def _cook_sweep_job(session_factory: SessionFactory) -> None:
    from app.cook import sweep_expired_cooks

    try:
        with session_factory() as session:
            swept = sweep_expired_cooks(session, now=datetime.now(UTC))
            if swept:
                log.info("cook_swept", extra={"count": swept})
    except Exception as exc:  # noqa: BLE001 - sweep job must not crash the scheduler
        log.warning(
            "cook_sweep_failed",
            extra={"error_class": type(exc).__name__},
        )


def register_sweep_expired_cooks(
    scheduler: AsyncIOScheduler, *, session_factory: SessionFactory
) -> None:
    scheduler.add_job(
        _cook_sweep_job,
        "cron",
        minute="*/5",
        timezone="UTC",
        args=[session_factory],
        id="sweep_expired_cooks",
        replace_existing=True,
    )
