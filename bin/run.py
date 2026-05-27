"""Runtime entry point.

Startup order:
1. load settings
2. open DB engine + session factory
3. pre-migration backup if DB file already exists
4. alembic upgrade head
5. construct Bot + Anthropic SDK + LLMClient
6. register per-user digest jobs
7. start AsyncIOScheduler
8. start dispatcher polling
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import app.bot as bot_mod
from app.backup import BackupError, pre_migration_backup
from app.bot import build_dispatcher
from app.db import make_engine, make_session_factory
from app.llm import AnthropicLLMClient
from app.scheduler import (
    register_all_user_digests,
    schedule_user_digest,
    send_digest_with_retry,
)
from app.settings import Settings


def _configure_logging(env: str, level: str) -> None:
    fmt = (
        "%(asctime)s %(levelname)s %(name)s %(message)s"
        if env != "prod"
        else '{"ts":"%(asctime)s","lvl":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'
    )
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        stream=sys.stdout,
    )


async def _amain(settings: Settings) -> None:
    log = logging.getLogger("food-manager")
    log.info("startup_begin")

    engine = make_engine(settings.database_path)
    session_factory = make_session_factory(engine)

    if Path(settings.database_path).exists():
        try:
            backup_path = pre_migration_backup(settings.database_path, keep=5)
            log.info("pre_migration_backup_ok", extra={"path": backup_path})
        except BackupError as exc:
            log.error("pre_migration_backup_failed", extra={"error": str(exc)})
            raise SystemExit(2) from exc

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={
            "DATABASE_PATH": settings.database_path,
            **{key: value for key, value in __import__("os").environ.items()},
        },
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("migration_failed", extra={"stderr": result.stderr})
        raise SystemExit(3)
    log.info("migration_ok")

    bot = Bot(token=settings.telegram_bot_token)
    sdk = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    llm = AnthropicLLMClient(sdk=sdk, model=settings.anthropic_model)
    bot_mod.ALLOWED_TELEGRAM_USER_ID = settings.allowed_telegram_user_id

    scheduler = AsyncIOScheduler()

    async def send(user_id: int) -> None:
        await send_digest_with_retry(
            user_id=user_id,
            bot=bot,
            session_factory=session_factory,
            today_provider=lambda tz: datetime.now(ZoneInfo(tz)).date(),
        )

    register_all_user_digests(scheduler, session_factory=session_factory, send=send)

    def reschedule(user) -> None:
        schedule_user_digest(scheduler, user, send=send)

    dispatcher = build_dispatcher(
        bot=bot,
        session_factory=session_factory,
        llm=llm,
        now_provider=lambda tz: datetime.now(ZoneInfo(tz)),
        on_user_created=reschedule,
        reschedule=reschedule,
    )

    scheduler.start()
    log.info("scheduler_started")
    log.info("polling_start")
    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    _configure_logging(settings.env, settings.log_level)
    asyncio.run(_amain(settings))


if __name__ == "__main__":
    main()
