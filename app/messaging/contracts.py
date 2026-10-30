"""Validated values crossing the messaging/application boundary.

SDK objects, credentials and raw webhook bodies never cross this boundary.
External identifiers remain strings; a Slack timestamp is never a float.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Value(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class MessagingProvider(StrEnum):
    TELEGRAM = "telegram"
    SLACK = "slack"
    WHATSAPP = "whatsapp"


Identifier = Annotated[str, Field(min_length=1, max_length=512)]


class ActorRef(Value):
    connection_id: Identifier
    external_user_id: Identifier


class ConversationRef(Value):
    connection_id: Identifier
    external_conversation_id: Identifier
    kind: Literal["direct", "shared"] = "direct"
    thread_id: Identifier | None = None


class MessageRef(Value):
    conversation: ConversationRef
    external_message_id: Identifier


class MediaRef(Value):
    connection_id: Identifier
    external_media_id: Identifier


class TextReceived(Value):
    kind: Literal["text"] = "text"
    text: str = Field(max_length=16000)


class ImageReceived(Value):
    kind: Literal["image"] = "image"
    media: MediaRef
    caption: str | None = Field(default=None, max_length=16000)


class ActionInvoked(Value):
    kind: Literal["action"] = "action"
    token: str = Field(min_length=1, max_length=128)
    source: MessageRef


class IncomingEvent(Value):
    event_key: Identifier
    actor: ActorRef
    conversation: ConversationRef
    received_at: datetime
    occurred_at: datetime | None = None
    content: Annotated[
        TextReceived | ImageReceived | ActionInvoked, Field(discriminator="kind")
    ]
    reply_to: MessageRef | None = None

    @model_validator(mode="after")
    def same_connection(self):
        connection_id = self.conversation.connection_id
        if self.actor.connection_id != connection_id:
            raise ValueError("actor and conversation must share a connection")
        if (
            isinstance(self.content, ImageReceived)
            and self.content.media.connection_id != connection_id
        ):
            raise ValueError("media belongs to another connection")
        if (
            isinstance(self.content, ActionInvoked)
            and self.content.source.conversation != self.conversation
        ):
            raise ValueError("action belongs to another conversation")
        if self.reply_to and self.reply_to.conversation != self.conversation:
            raise ValueError("reply belongs to another conversation")
        if self.received_at.tzinfo is None:
            raise ValueError("received_at requires a timezone")
        if self.occurred_at is not None and self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at requires a timezone")
        return self


class ActionButton(Value):
    kind: Literal["action"] = "action"
    label: str = Field(min_length=1, max_length=200)
    token: str = Field(min_length=1, max_length=128)


class LinkButton(Value):
    kind: Literal["link"] = "link"
    label: str = Field(min_length=1, max_length=200)
    url: str = Field(pattern=r"^https://", max_length=2048)


class ChatMessage(Value):
    kind: Literal["message"] = "message"
    text: str = Field(min_length=1, max_length=100000)
    language: str = Field(default="en", max_length=16)
    buttons: tuple[
        Annotated[ActionButton | LinkButton, Field(discriminator="kind")], ...
    ] = ()


class DigestNotice(Value):
    kind: Literal["digest"] = "digest"
    localized_message: ChatMessage
    due_count: int = Field(ge=0)
    language: str


class DeliveryRequest(Value):
    delivery_key: Identifier
    conversation: ConversationRef
    content: Annotated[ChatMessage | DigestNotice, Field(discriminator="kind")]
    replace: MessageRef | None = None

    @model_validator(mode="after")
    def same_conversation(self):
        if self.replace and self.replace.conversation != self.conversation:
            raise ValueError("replacement belongs to another conversation")
        return self


class Accepted(Value):
    messages: tuple[MessageRef, ...] = Field(min_length=1)


class Suppressed(Value):
    reason: Literal["opted_out", "template_required", "destination_unavailable"]


class DownloadedImage(Value):
    data: bytes
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    AUTH_FAILED = "AUTH_FAILED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    TEMPORARY_FAILURE = "TEMPORARY_FAILURE"
    DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"
    UNSUPPORTED = "UNSUPPORTED"


class MessagingError(Exception):
    def __init__(
        self, code: ErrorCode, message: str, *, retry_after_seconds: int | None = None
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after_seconds = retry_after_seconds


class MessagingAdapter(Protocol):
    async def deliver(self, request: DeliveryRequest) -> Accepted | Suppressed: ...
    async def download_image(self, media: MediaRef) -> DownloadedImage: ...
