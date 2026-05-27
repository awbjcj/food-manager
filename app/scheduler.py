from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from typing import Awaitable, Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError
from sqlmodel import Session, select

from app.bot import to_aiogram_keyboard
from app.models import PantryItem, User
from app.pantry_service import list_digest_due
from app.renderer import build_digest_keyboard, render_digest


log = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]
TodayProvider = Callable[[str], date]


@dataclass
class DigestPayload:
    user: User
    items: list[PantryItem]


def build_digest_payload(
    session: Session, *, user_id: int, today: date
) -> Optional[DigestPayload]:
    user = session.get(User, user_id)
    if user is None:
        return None
    rows = list_digest_due(session, user_id=user_id, today=today)
    if not rows:
        return None
    return DigestPayload(user=user, items=rows)


async def send_digest_once(
    *,
    user_id: int,
    bot,
    session_factory: SessionFactory,
    today_provider: TodayProvider,
) -> bool:
    with session_factory() as session:
        user = session.get(User, user_id)
        if user is None:
            log.warning("digest_skip_unknown_user", extra={"user_id": user_id})
            return False
        today = today_provider(user.tz)
        payload = build_digest_payload(session, user_id=user_id, today=today)
        if payload is None:
            log.info("digest_silent_day", extra={"user_id": user_id, "today": str(today)})
            return False
        rendered = render_digest(payload.items, today=today)
        keyboard = build_digest_keyboard(
            rendered.rendered_item_ids, has_more=rendered.has_more
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
        return True


async def send_digest_with_retry(
    *,
    user_id: int,
    bot,
    session_factory: SessionFactory,
    today_provider: TodayProvider,
    retry_sleep_seconds: int = 60,
) -> None:
    try:
        await send_digest_once(
            user_id=user_id,
            bot=bot,
            session_factory=session_factory,
            today_provider=today_provider,
        )
        return
    except Exception as exc:
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
        )
    except Exception as exc:
        log.warning(
            "digest_send_failed",
            extra={
                "user_id": user_id,
                "error_class": type(exc).__name__,
                "attempt": 2,
                "will_retry": False,
            },
        )


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


def register_all_user_digests(
    scheduler: AsyncIOScheduler,
    *,
    session_factory: SessionFactory,
    send: Callable[..., Awaitable[None]],
) -> None:
    with session_factory() as session:
        users = list(session.exec(select(User)).all())
    for user in users:
        schedule_user_digest(scheduler, user, send=send)
