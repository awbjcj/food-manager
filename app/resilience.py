"""Polling resilience: a crashed long-poll restarts itself with backoff.

ADR 0001 commits to one long-running process; when Telegram long-polling dies
(network flap, aiogram internal error) the process should heal itself instead
of exiting and waiting for a human. `run_with_restart` re-enters the polling
coroutine with exponential backoff and resets the backoff after a stable run.
Cancellation and KeyboardInterrupt propagate so a deliberate shutdown stays a
shutdown; an outer supervisor (docs/operations.md) remains the last line of
defence against hard process death.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

#: Backoff doubles from 1s up to this cap.
MAX_BACKOFF_SECONDS = 300.0
#: A run lasting at least this long resets the backoff to 1s.
STABLE_RUN_SECONDS = 600.0


async def run_with_restart(
    start: Callable[[], Awaitable[None]],
    *,
    on_crash: Callable[[Exception], Awaitable[None]] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    max_restarts: int | None = None,
) -> None:
    """Run `start()` until it returns cleanly, restarting after crashes.

    `max_restarts` exists for tests; production passes None (restart forever).
    """
    backoff = 1.0
    restarts = 0
    while True:
        began = clock()
        try:
            await start()
            return  # clean return = deliberate shutdown
        except Exception as exc:
            if clock() - began >= STABLE_RUN_SECONDS:
                backoff = 1.0
            log.error(
                "polling_crashed",
                extra={
                    "error_class": type(exc).__name__,
                    "backoff_seconds": backoff,
                },
            )
            if on_crash is not None:
                try:
                    await on_crash(exc)
                except Exception:  # noqa: BLE001 - alerting is best-effort
                    log.warning("polling_crash_hook_failed")
            restarts += 1
            if max_restarts is not None and restarts > max_restarts:
                raise
            await sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
