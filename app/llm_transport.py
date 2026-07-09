"""LLM transport retry: the one place that knows how a provider call is retried.

Every provider client (receipt parse, text parse, cook pipeline, translation)
issues an `await sdk...create(...)` that can fail with a transient transport
error. The retry policy — how many attempts, how long to back off, and which
exceptions are even worth retrying — lived copy-pasted in six methods across
`llm.py` and `cook_llm.py`, each free to drift. It now lives here.

The seam is `with_transport_retry`: a caller hands over the single network call
and a log-event name; the loop owns attempts, exponential backoff, and the
final-attempt logging. The `classify` hook is what lets one loop serve two
policies — receipt/text/translation calls retry on *any* exception (the
historical behaviour their tests pin), while the cook OpenAI path retries only
classified transport errors via `is_retryable_transport_error`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

#: Default attempts before giving up, matching the historical per-client loops.
DEFAULT_ATTEMPTS = 3


def is_retryable_transport_error(exc: Exception) -> bool:
    """True for errors worth retrying: timeouts, connection drops, 5xx/rate-limit.

    Used as the `classify` hook for providers (OpenAI cook path) that should let
    genuine client errors (bad request, schema) fail fast instead of burning
    retries on them.
    """
    name = type(exc).__name__.lower()
    return isinstance(exc, (TimeoutError, ConnectionError)) or any(
        token in name
        for token in (
            "timeout",
            "connection",
            "rate_limit",
            "ratelimit",
            "apistatus",
            "internalserver",
            "servererror",
            "serviceunavailable",
        )
    )


async def with_transport_retry(
    make_call: Callable[[], Awaitable[T]],
    *,
    log_event: str,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    attempts: int = DEFAULT_ATTEMPTS,
    classify: Optional[Callable[[Exception], bool]] = None,
    clock: Callable[[], float] = time.monotonic,
) -> T:
    """Run `make_call`, retrying transient failures with exponential backoff.

    `log_event` is the structured-log stem: a retry logs ``f"{log_event}_retrying"``
    and the final failure logs ``f"{log_event}_final"`` (preserving the existing
    event names so log-based alerting keeps working). On success, logs
    ``f"{log_event}_timing"`` with `duration_ms`/`attempts`. `clock` feeds the
    success timing log; inject a fake in tests.
    """
    started = clock()
    for attempt in range(attempts):
        try:
            result = await make_call()
        except Exception as exc:
            if classify is not None and not classify(exc):
                raise
            if attempt == attempts - 1:
                log.warning(
                    f"{log_event}_final", extra={"error_class": type(exc).__name__}
                )
                raise
            log.warning(
                f"{log_event}_retrying", extra={"error_class": type(exc).__name__}
            )
            await sleep(2**attempt)
            continue
        log.info(
            f"{log_event}_timing",
            extra={
                "duration_ms": int((clock() - started) * 1000),
                "attempts": attempt + 1,
            },
        )
        return result
    raise RuntimeError("unreachable")
