"""Progress acknowledgment seam: slow commands ack immediately, then the ack
message becomes the result.

A handler about to do slow LLM/network work calls `start_progress` first so the
user sees the bot react within a second instead of staring at a silent chat.
When the reply (or error message) is ready, `finish_progress` edits the ack in
place — the same edit-or-resend rule as `callback_dispatch` — or
`clear_progress` deletes the ack when results arrive as separate messages (the
/add proposal cards). Everything here is best-effort: a failed ack, edit, or
delete must never break the underlying command.
"""
from __future__ import annotations

import logging

from app.callback_dispatch import is_not_modified

log = logging.getLogger(__name__)


async def start_progress(msg, text: str):
    """Send the ack (plus a best-effort "typing" action); None if sending fails."""
    bot = getattr(msg, "bot", None)
    if bot is not None:
        try:
            await bot.send_chat_action(chat_id=msg.chat.id, action="typing")
        except Exception as exc:  # noqa: BLE001 - cosmetic only
            log.info(
                "progress_chat_action_failed",
                extra={"error_class": type(exc).__name__},
            )
    try:
        return await msg.answer(text)
    except Exception as exc:  # noqa: BLE001 - the ack is best-effort
        log.warning("progress_start_failed", extra={"error_class": type(exc).__name__})
        return None


async def finish_progress(progress, msg, text: str, keyboard=None):
    """Turn the ack into the final reply, falling back to a fresh message.

    Returns the message object carrying the final text so callers that later
    edit the reply (post-ingest refine) keep a valid message_id.
    """
    if progress is not None:
        try:
            await progress.edit_text(text, reply_markup=keyboard)
            return progress
        except Exception as exc:  # noqa: BLE001 - classified below
            if is_not_modified(exc):
                return progress
            log.info(
                "progress_edit_fallback", extra={"error_class": type(exc).__name__}
            )
    return await msg.answer(text, reply_markup=keyboard)


async def clear_progress(progress) -> None:
    """Delete the ack when results arrive as separate messages; best-effort."""
    if progress is None:
        return
    try:
        await progress.delete()
    except Exception as exc:  # noqa: BLE001 - cosmetic only
        log.info("progress_clear_failed", extra={"error_class": type(exc).__name__})
