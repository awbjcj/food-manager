from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlmodel import Session

from app.application.context import Keyboard
from app.messaging.contracts import (
    ActionButton,
    ChatMessage,
    ErrorCode,
    LinkButton,
    MessagingError,
)
from app.messaging.identity_service import token_hash
from app.models import MessagingAction, User
from app.pending_service import utc_naive


def present(
    session: Session, *, user: User, conversation, text: str, actions, now: datetime
) -> ChatMessage:
    buttons = []
    if isinstance(actions, Keyboard):
        for row in actions.rows:
            for button in row:
                if button.url:
                    # Non-HTTPS deep links remain specific to the Telegram integration.
                    if button.url.startswith("https://"):
                        buttons.append(LinkButton(label=button.text, url=button.url))
                elif button.callback_data:
                    token = secrets.token_urlsafe(24)
                    session.add(
                        MessagingAction(
                            token_hash=token_hash(token),
                            connection_id=conversation.connection_id,
                            conversation_id=conversation.external_conversation_id,
                            thread_id=conversation.thread_id,
                            user_id=user.id,
                            household_id=user.household_id,
                            membership_generation=user.membership_generation,
                            command=button.callback_data,
                            expires_at=utc_naive(now) + timedelta(days=7),
                        )
                    )
                    buttons.append(ActionButton(label=button.text, token=token))
    return ChatMessage(text=text or " ", buttons=tuple(buttons), language=user.lang)


def resolve_action(
    session: Session, *, token: str, user: User, conversation, now: datetime
) -> str:
    row = session.get(MessagingAction, token_hash(token))
    if (
        row is None
        or row.expires_at <= utc_naive(now)
        or row.user_id != user.id
        or row.household_id != user.household_id
        or row.membership_generation != user.membership_generation
        or row.connection_id != conversation.connection_id
        or row.conversation_id != conversation.external_conversation_id
        or row.thread_id != conversation.thread_id
    ):
        raise MessagingError(
            ErrorCode.FORBIDDEN,
            "This action expired or belongs to another conversation. Open pantry again.",
        )
    return row.command
