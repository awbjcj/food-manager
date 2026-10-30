from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.messaging.adapters.http import MAX_IMAGE_BYTES, checked_image
from app.messaging.contracts import (
    Accepted,
    ActionButton,
    DigestNotice,
    ErrorCode,
    MessageRef,
    MessagingError,
)


class TelegramAdapter:
    def __init__(self, *, connection_id: str, bot: Bot):
        self.connection_id, self.bot = connection_id, bot

    async def identity(self):
        result = await self.bot.get_me()
        return str(result.id), {"botUsername": result.username}

    async def deliver(self, request):
        if request.conversation.connection_id != self.connection_id:
            raise MessagingError(ErrorCode.FORBIDDEN, "Wrong connection")
        message = (
            request.content.localized_message
            if isinstance(request.content, DigestNotice)
            else request.content
        )
        buttons = [
            InlineKeyboardButton(text=b.label, callback_data="mp:" + b.token)
            if isinstance(b, ActionButton)
            else InlineKeyboardButton(text=b.label, url=b.url)
            for b in message.buttons
        ]
        keyboard = (
            InlineKeyboardMarkup(
                inline_keyboard=[buttons[i : i + 2] for i in range(0, len(buttons), 2)]
            )
            if buttons
            else None
        )
        chunks = [message.text[i : i + 3000] for i in range(0, len(message.text), 3000)]
        refs = []
        try:
            for index, text in enumerate(chunks):
                kwargs = {
                    "chat_id": request.conversation.external_conversation_id,
                    "text": text,
                    "reply_markup": keyboard if index == len(chunks) - 1 else None,
                }
                result = None
                if request.replace and len(chunks) == 1:
                    try:
                        result = await self.bot.edit_message_text(
                            **kwargs,
                            message_id=int(request.replace.external_message_id),
                        )
                    except TelegramBadRequest as exc:
                        if "message is not modified" in str(exc).lower():
                            return Accepted(messages=(request.replace,))
                        # Only a definite obsolete/uneditable target permits resend.
                        if not any(
                            s in str(exc).lower()
                            for s in (
                                "message to edit not found",
                                "message can't be edited",
                            )
                        ):
                            raise
                if result is None:
                    result = await self.bot.send_message(
                        **kwargs,
                        message_thread_id=int(request.conversation.thread_id)
                        if request.conversation.thread_id
                        else None,
                    )
                if isinstance(result, bool):
                    refs.append(request.replace)
                else:
                    refs.append(
                        MessageRef(
                            conversation=request.conversation,
                            external_message_id=str(result.message_id),
                        )
                    )
        except TelegramRetryAfter as exc:
            raise MessagingError(
                ErrorCode.DELIVERY_UNKNOWN if refs else ErrorCode.RATE_LIMITED,
                "Telegram rate limit",
                retry_after_seconds=exc.retry_after,
            ) from None
        except TelegramForbiddenError:
            raise MessagingError(
                ErrorCode.FORBIDDEN, "Telegram destination is unavailable"
            ) from None
        except TelegramNetworkError:
            raise MessagingError(
                ErrorCode.DELIVERY_UNKNOWN, "Telegram send could not be confirmed"
            ) from None
        except TelegramBadRequest:
            raise MessagingError(
                ErrorCode.INVALID_INPUT, "Telegram rejected the message"
            ) from None
        return Accepted(messages=tuple(refs))

    async def download_image(self, media):
        if media.connection_id != self.connection_id:
            raise MessagingError(ErrorCode.FORBIDDEN, "Wrong connection")
        file = await self.bot.get_file(media.external_media_id)
        if not file.file_path or (file.file_size and file.file_size > MAX_IMAGE_BYTES):
            raise MessagingError(
                ErrorCode.INVALID_INPUT, "Receipt image is unavailable or too large"
            )
        downloaded = await self.bot.download_file(file.file_path, timeout=20)
        if downloaded is None:
            raise MessagingError(ErrorCode.NOT_FOUND, "Receipt image is unavailable")
        return checked_image(downloaded.read(MAX_IMAGE_BYTES + 1))
