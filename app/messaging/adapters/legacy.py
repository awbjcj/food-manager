"""Compatibility entry points: convert real Telegram interactions to core ports.

Other providers instantiate core contexts directly and never emulate Telegram.
"""

from __future__ import annotations

from functools import wraps
from typing import Any

from aiogram.types import BufferedInputFile, ForceReply

from app.application.context import ActionContext, CommandContext, Keyboard, ReplyPrompt
from app.telegram_ui import to_aiogram_keyboard


def markup(actions):
    if isinstance(actions, Keyboard):
        return to_aiogram_keyboard(actions.rows)
    if isinstance(actions, ReplyPrompt):
        return ForceReply()
    return actions


class TelegramMessenger:
    def __init__(self, message=None, bot=None, *, actor_id=0):
        # The compatibility edge accepts aiogram and the existing Mini App transport.
        self.message: Any = message
        self.bot: Any = bot or getattr(message, "bot", None)
        self.actor_id = actor_id
        self.messages = {}
        if message is not None:
            self.messages[self.keys(message)] = message

    def keys(self, message):
        chat_id = getattr(getattr(message, "chat", None), "id", self.actor_id)
        message_id = getattr(message, "message_id", 0)
        return (
            chat_id if isinstance(chat_id, int) else self.actor_id,
            message_id if isinstance(message_id, int) else 0,
        )

    def context(self, message, *, actor_id=None, include_reply=True):
        chat_id, message_id = self.keys(message)
        if not hasattr(message, "chat") and self.message is not None:
            chat_id = self.keys(self.message)[0]
        self.messages[(chat_id, message_id)] = message
        chat_type = getattr(getattr(message, "chat", None), "type", "private")
        images = getattr(message, "photo", None)
        result = CommandContext(
            actor_id=self.actor_id if actor_id is None else actor_id,
            conversation_key=chat_id,
            message_key=message_id,
            text=getattr(message, "text", None),
            transport=self,
            is_private=chat_type == "private" if isinstance(chat_type, str) else True,
            images=tuple(p.file_id for p in images)
            if isinstance(images, (list, tuple))
            else (),
            telegram_identity=self.actor_id if actor_id is None else actor_id,
        )
        reply = getattr(message, "reply_to_message", None)
        if (
            include_reply
            and reply is not None
            and isinstance(getattr(reply, "text", None), str)
        ):
            result.reply_to = self.context(reply, include_reply=False)
        return result

    async def send_text(self, *, conversation_key, text, actions=None):
        if self.message is not None and self.keys(self.message)[0] == conversation_key:
            sent = await self.message.answer(
                text,
                **({"reply_markup": markup(actions)} if actions is not None else {}),
            )
        else:
            sent = await self.bot.send_message(
                chat_id=conversation_key, text=text, reply_markup=markup(actions)
            )
        return self.context(sent)

    async def update_text(self, *, conversation_key, message_key, text, actions=None):
        old = self.messages.get((conversation_key, message_key))
        if old is not None:
            await old.edit_text(text, reply_markup=markup(actions))
            return self.context(old)
        result = await self.bot.edit_message_text(
            chat_id=conversation_key,
            message_id=message_key,
            text=text,
            reply_markup=markup(actions),
        )
        return self.context(result) if not isinstance(result, bool) else None

    async def remove_message(self, *, conversation_key, message_key):
        old = self.messages.get((conversation_key, message_key))
        if old is not None:
            await old.delete()

    async def typing(self, *, conversation_key):
        if self.bot:
            await self.bot.send_chat_action(chat_id=conversation_key, action="typing")

    async def send_document(self, *, conversation_key, document, caption):
        return await self.message.answer_document(
            BufferedInputFile(document.data, filename=document.filename),
            caption=caption,
        )


def command_context(message):
    if isinstance(message, CommandContext):
        return message
    actor_id = getattr(getattr(message, "from_user", None), "id", 0)
    transport = TelegramMessenger(message, actor_id=actor_id)
    return transport.context(message, actor_id=actor_id)


def action_context(callback):
    if isinstance(callback, ActionContext):
        return callback
    source = getattr(callback, "message", None)
    actor_id = getattr(getattr(callback, "from_user", None), "id", 0)
    transport = TelegramMessenger(source, actor_id=actor_id)

    async def ack(text, *, alert=None):
        if alert is not None:
            await callback.answer(text, show_alert=alert)
        elif text:
            await callback.answer(text)
        else:
            await callback.answer()

    return ActionContext(
        actor_id=actor_id,
        data=getattr(callback, "data", None),
        source=transport.context(source) if source is not None else None,
        acknowledge=ack,
        telegram_identity=actor_id,
    )


def message_entry(function):
    @wraps(function)
    async def invoke(message, *args, **kwargs):
        if (
            kwargs.get("bot") is not None
            and not isinstance(kwargs["bot"], TelegramMessenger)
            and not isinstance(message, CommandContext)
        ):
            kwargs["bot"] = TelegramMessenger(bot=kwargs["bot"])
        return await function(command_context(message), *args, **kwargs)

    return invoke


def action_entry(function):
    @wraps(function)
    async def invoke(callback, *args, **kwargs):
        if (
            kwargs.get("bot") is not None
            and not isinstance(kwargs["bot"], TelegramMessenger)
            and not isinstance(callback, ActionContext)
        ):
            kwargs["bot"] = TelegramMessenger(bot=kwargs["bot"])
        return await function(action_context(callback), *args, **kwargs)

    return invoke
