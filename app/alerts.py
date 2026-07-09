"""Owner alerting: silent failures become a Telegram DM to the bootstrap owner.

The bot's operator is the household owner and the bot's one reliable channel is
Telegram itself, so operational alerts (unhandled handler errors, digest
delivery failures, polling crashes) are DMs to ALLOWED_TELEGRAM_USER_ID rather
than an external monitoring stack. Alerts are rate-limited per event name so an
error loop cannot flood the chat, and delivery failures are swallowed —
alerting must never take the bot down.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

log = logging.getLogger(__name__)

#: Default seconds between alerts for the same event name.
DEFAULT_MIN_INTERVAL_SECONDS = 300.0


class OwnerAlerter:
    def __init__(
        self,
        bot,
        chat_id: int,
        *,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._min_interval = min_interval_seconds
        self._clock = clock
        self._last_sent: dict[str, float] = {}

    async def alert(self, event: str, detail: str) -> bool:
        """DM the owner about `event`; True iff a message was actually sent."""
        now = self._clock()
        last = self._last_sent.get(event)
        if last is not None and now - last < self._min_interval:
            log.info("owner_alert_suppressed", extra={"event": event})
            return False
        self._last_sent[event] = now
        try:
            await self._bot.send_message(
                chat_id=self._chat_id, text=f"⚠️ {event}: {detail}"
            )
            return True
        except Exception as exc:  # noqa: BLE001 - alerting is best-effort
            log.warning(
                "owner_alert_failed",
                extra={"event": event, "error_class": type(exc).__name__},
            )
            return False
