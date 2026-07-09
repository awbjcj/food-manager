"""Callback dispatch seam: the one owner of how a button tap is acknowledged
and how its result is shown.

Two rules live here so no individual handler has to re-derive them (the bug this
module fixes was each handler swallowing edit failures and acknowledging late):

1. Acknowledge first. Telegram expires a callback query quickly; answering before
   any slow work (translation, web search) keeps the button from "doing nothing".
2. Edit, or resend. An in-place edit can fail because the card is too old or
   because the content is identical. "Not modified" is success; any other failure
   falls back to sending the view as a fresh message so the flow never dead-ends.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def is_not_modified(exc: Exception) -> bool:
    # aiogram raises TelegramBadRequest("... message is not modified ...") when the
    # new text+keyboard equal the current ones. That is a no-op, i.e. success.
    return "not modified" in str(exc).lower()


async def answer(cb, text: str = "", *, show_alert: bool = False) -> None:
    """Acknowledge the callback query; never let a failed ack raise."""
    try:
        await cb.answer(text, show_alert=show_alert)
    except Exception as exc:  # noqa: BLE001 - acking is best-effort
        log.warning("callback_answer_failed", extra={"error_class": type(exc).__name__})


async def edit_or_resend(cb, text: str, keyboard=None) -> None:
    """Render the next view in place, falling back to a fresh message.

    Treats an identical-content edit as success and a genuine edit failure as a
    signal to resend, so tapping a button always lands the user on the new view.
    """
    message = getattr(cb, "message", None)
    if message is None:
        return
    try:
        await message.edit_text(text, reply_markup=keyboard)
        return
    except Exception as exc:  # noqa: BLE001 - classified below
        if is_not_modified(exc):
            return
        log.info(
            "callback_edit_resend",
            extra={"error_class": type(exc).__name__},
        )
    try:
        await message.answer(text, reply_markup=keyboard)
    except Exception as exc:  # noqa: BLE001 - last resort
        log.warning(
            "callback_resend_failed", extra={"error_class": type(exc).__name__}
        )
