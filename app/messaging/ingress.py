"""Provider authentication and bounded payload decoding, before durable admission."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

from app.messaging.contracts import (
    ActionInvoked,
    ActorRef,
    ConversationRef,
    ErrorCode,
    ImageReceived,
    IncomingEvent,
    MediaRef,
    MessageRef,
    MessagingError,
    TextReceived,
)

MAX_WEBHOOK_BYTES = 1024 * 1024


def verify_slack(raw: bytes, headers, *, secret: str, now: datetime) -> None:
    timestamp = headers.get("X-Slack-Request-Timestamp", "")
    try:
        age = abs(now.timestamp() - int(timestamp))
    except (ValueError, TypeError):
        age = 999999
    expected = (
        "v0="
        + hmac.new(
            secret.encode(), b"v0:" + timestamp.encode() + b":" + raw, hashlib.sha256
        ).hexdigest()
    )
    if (
        not secret
        or age > 300
        or not hmac.compare_digest(expected, headers.get("X-Slack-Signature", ""))
    ):
        raise MessagingError(ErrorCode.AUTH_FAILED, "Invalid Slack signature")


def verify_whatsapp(raw: bytes, headers, *, secret: str) -> None:
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(
        expected, headers.get("X-Hub-Signature-256", "")
    ):
        raise MessagingError(ErrorCode.AUTH_FAILED, "Invalid WhatsApp signature")


def _object(value) -> dict:
    if not isinstance(value, dict):
        raise MessagingError(ErrorCode.INVALID_INPUT, "Invalid provider payload")
    return value


def _string(value) -> str:
    if not isinstance(value, str) or not value or len(value) > 16000:
        raise MessagingError(ErrorCode.INVALID_INPUT, "Invalid provider field")
    return value


def slack_payload(raw: bytes, content_type: str) -> dict:
    if len(raw) > MAX_WEBHOOK_BYTES:
        raise MessagingError(ErrorCode.INVALID_INPUT, "Request is too large")
    try:
        if content_type == "application/x-www-form-urlencoded":
            values = parse_qs(raw.decode(), strict_parsing=True, max_num_fields=50)
            if any(len(v) != 1 for v in values.values()):
                raise ValueError("duplicate field")
            return (
                _object(json.loads(values["payload"][0]))
                if "payload" in values
                else {k: v[0] for k, v in values.items()}
            )
        return _object(json.loads(raw))
    except (ValueError, UnicodeError):
        raise MessagingError(ErrorCode.INVALID_INPUT, "Invalid Slack payload") from None


def slack_events(
    payload: dict,
    *,
    connection_id: str,
    account: str,
    now: datetime,
    bot_user_id: str = "",
) -> list[IncomingEvent]:
    reply_to = None
    team = payload.get("team_id") or _object(payload.get("team", {})).get("id")
    if team != account:
        raise MessagingError(
            ErrorCode.FORBIDDEN, "Workspace does not match this installation"
        )
    if payload.get("type") == "block_actions":
        user = _string(_object(payload.get("user")).get("id"))
        channel = _string(_object(payload.get("channel")).get("id"))
        container = _object(payload.get("container"))
        conversation = ConversationRef(
            connection_id=connection_id,
            external_conversation_id=channel,
            kind="direct" if channel.startswith("D") else "shared",
            thread_id=container.get("thread_ts"),
        )
        actions = payload.get("actions")
        if not isinstance(actions, list) or len(actions) != 1:
            raise MessagingError(ErrorCode.INVALID_INPUT, "Select one action")
        action = _object(actions[0])
        source = MessageRef(
            conversation=conversation,
            external_message_id=_string(container.get("message_ts")),
        )
        content = ActionInvoked(token=_string(action.get("value")), source=source)
        key = (
            "action:"
            + hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
    elif "command" in payload:
        user, channel = (
            _string(payload.get("user_id")),
            _string(payload.get("channel_id")),
        )
        if payload.get("command") != "/food":
            return []
        conversation = ConversationRef(
            connection_id=connection_id,
            external_conversation_id=channel,
            kind="direct" if channel.startswith("D") else "shared",
        )
        content = TextReceived(text=str(payload.get("text", "")))
        key = "command:" + _string(payload.get("trigger_id"))
    else:
        event = _object(payload.get("event", {}))
        if (
            event.get("type") not in {"message", "app_mention"}
            or event.get("bot_id")
            or event.get("user") == bot_user_id
            or event.get("subtype") not in {None, "file_share"}
        ):
            return []
        user, channel = _string(event.get("user")), _string(event.get("channel"))
        conversation = ConversationRef(
            connection_id=connection_id,
            external_conversation_id=channel,
            kind="direct"
            if event.get("channel_type") == "im" or channel.startswith("D")
            else "shared",
            thread_id=event.get("thread_ts"),
        )
        if event.get("thread_ts"):
            reply_to = MessageRef(
                conversation=conversation, external_message_id=event["thread_ts"]
            )
        files = event.get("files", [])
        key = "message:" + channel + ":" + _string(event.get("ts"))
        if files:
            if not isinstance(files, list) or len(files) > 10:
                raise MessagingError(ErrorCode.INVALID_INPUT, "Too many receipt images")
            result = []
            for file in files:
                file = _object(file)
                if file.get("mimetype") not in {
                    "image/jpeg",
                    "image/png",
                    "image/webp",
                }:
                    continue
                media_id = _string(file.get("id"))
                result.append(
                    IncomingEvent(
                        event_key=key + ":" + media_id,
                        actor=ActorRef(
                            connection_id=connection_id, external_user_id=user
                        ),
                        conversation=conversation,
                        received_at=now,
                        content=ImageReceived(
                            media=MediaRef(
                                connection_id=connection_id, external_media_id=media_id
                            )
                        ),
                    )
                )
            return result
        text = event.get("text", "")
        if bot_user_id:
            text = text.replace(f"<@{bot_user_id}>", "").strip()
        content = TextReceived(text=text or "help")
    return [
        IncomingEvent(
            event_key=key,
            actor=ActorRef(connection_id=connection_id, external_user_id=user),
            conversation=conversation,
            received_at=now,
            content=content,
            reply_to=reply_to,
        )
    ]


def whatsapp_events(
    payload: dict, *, connection_id: str, business_id: str, phone_id: str, now: datetime
) -> list[IncomingEvent]:
    if payload.get("object") != "whatsapp_business_account":
        raise MessagingError(ErrorCode.INVALID_INPUT, "Invalid webhook object")
    entries = payload.get("entry")
    if not isinstance(entries, list) or len(entries) > 100:
        raise MessagingError(ErrorCode.INVALID_INPUT, "Invalid webhook batch")
    results = []
    for entry in entries:
        entry = _object(entry)
        if entry.get("id") != business_id:
            raise MessagingError(ErrorCode.FORBIDDEN, "Wrong business account")
        changes = entry.get("changes", [])
        if not isinstance(changes, list) or len(changes) > 100:
            raise MessagingError(ErrorCode.INVALID_INPUT, "Invalid webhook changes")
        for change in changes:
            change = _object(change)
            if change.get("field") != "messages":
                continue
            value = _object(change.get("value"))
            if _object(value.get("metadata")).get("phone_number_id") != phone_id:
                raise MessagingError(ErrorCode.FORBIDDEN, "Wrong sender phone number")
            messages = value.get("messages", [])
            if not isinstance(messages, list) or len(messages) > 100:
                raise MessagingError(ErrorCode.INVALID_INPUT, "Invalid message batch")
            for message in messages:
                message = _object(message)
                user, key = _string(message.get("from")), _string(message.get("id"))
                try:
                    occurred = datetime.fromtimestamp(int(message["timestamp"]), UTC)
                except (KeyError, ValueError, TypeError, OSError, OverflowError):
                    raise MessagingError(
                        ErrorCode.INVALID_INPUT, "Invalid message timestamp"
                    ) from None
                if occurred > now + timedelta(minutes=2):
                    raise MessagingError(
                        ErrorCode.INVALID_INPUT, "Message timestamp is in the future"
                    )
                conversation = ConversationRef(
                    connection_id=connection_id, external_conversation_id=user
                )
                kind = message.get("type")
                if kind == "text":
                    content = TextReceived(
                        text=_string(_object(message.get("text")).get("body"))
                    )
                elif kind == "image":
                    image = _object(message.get("image"))
                    content = ImageReceived(
                        media=MediaRef(
                            connection_id=connection_id,
                            external_media_id=_string(image.get("id")),
                        ),
                        caption=image.get("caption"),
                    )
                elif kind == "interactive":
                    interactive = _object(message.get("interactive"))
                    reply = _object(interactive.get(interactive.get("type")))
                    source_id = _object(message.get("context", {})).get("id") or key
                    content = ActionInvoked(
                        token=_string(reply.get("id")),
                        source=MessageRef(
                            conversation=conversation, external_message_id=source_id
                        ),
                    )
                else:
                    continue
                results.append(
                    IncomingEvent(
                        event_key=key,
                        actor=ActorRef(
                            connection_id=connection_id, external_user_id=user
                        ),
                        conversation=conversation,
                        received_at=now,
                        occurred_at=occurred,
                        content=content,
                        reply_to=MessageRef(
                            conversation=conversation,
                            external_message_id=message["context"]["id"],
                        )
                        if isinstance(message.get("context"), dict)
                        and message["context"].get("id")
                        else None,
                    )
                )
    return results
