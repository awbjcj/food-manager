"""Transport-neutral interaction ports used by application workflows."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field, replace
from typing import Protocol

from app.renderer import CallbackButton


@dataclass(frozen=True)
class Keyboard:
    rows: list[list[CallbackButton]]


@dataclass(frozen=True)
class ReplyPrompt:
    pass


@dataclass(frozen=True)
class Document:
    data: bytes
    filename: str


def keyboard(rows):
    return Keyboard(rows)


class Messenger(Protocol):
    async def send_text(
        self, *, conversation_key: int, text: str, actions=None
    ) -> CommandContext: ...
    async def update_text(
        self, *, conversation_key: int, message_key: int, text: str, actions=None
    ) -> CommandContext | None: ...
    async def remove_message(
        self, *, conversation_key: int, message_key: int
    ) -> None: ...
    async def typing(self, *, conversation_key: int) -> None: ...
    async def send_document(
        self, *, conversation_key: int, document: Document, caption: str
    ) -> object: ...


class Acknowledge(Protocol):
    def __call__(self, text: str, *, alert: bool | None = None) -> Awaitable[None]: ...


@dataclass
class CommandContext:
    actor_id: int
    conversation_key: int
    message_key: int
    transport: Messenger
    text: str | None = None
    is_private: bool = True
    images: tuple[str, ...] = ()
    reply_to: CommandContext | None = None
    # Only set by the Telegram compatibility adapter, never by a shared command.
    telegram_identity: int | None = None
    authorization: object | None = field(default=None, repr=False)

    def with_text(self, text: str):
        return replace(self, text=text)

    async def reply(self, text: str, *, actions=None, **_kwargs):
        return await self.transport.send_text(
            conversation_key=self.conversation_key, text=text, actions=actions
        )

    async def edit(self, text: str, *, actions=None, **_kwargs):
        return await self.transport.update_text(
            conversation_key=self.conversation_key,
            message_key=self.message_key,
            text=text,
            actions=actions,
        )

    async def remove(self):
        return await self.transport.remove_message(
            conversation_key=self.conversation_key, message_key=self.message_key
        )

    async def reply_document(self, document: Document, caption="", **_kwargs):
        return await self.transport.send_document(
            conversation_key=self.conversation_key, document=document, caption=caption
        )


@dataclass
class ActionContext:
    actor_id: int
    data: str | None
    source: CommandContext | None
    acknowledge: Acknowledge = field(repr=False)
    telegram_identity: int | None = None
    authorization: object | None = field(default=None, repr=False)

    async def ack(self, text: str = "", *, alert: bool | None = None):
        return await self.acknowledge(text, alert=alert)
