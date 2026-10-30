"""Runtime composition; legacy Telegram remains compatible during migration."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aiohttp import web
from sqlmodel import col, select

from app.messaging.connection_service import set_verified_account
from app.messaging.contracts import (
    ActorRef,
    ConversationRef,
    IncomingEvent,
    TextReceived,
)
from app.models import (
    ExternalIdentity,
    MessagingConnection,
    NotificationDestination,
    User,
)


def import_legacy(runtime):
    """Credentials stay in the environment; only references are persisted."""
    with runtime.session_factory() as session:
        row = session.get(MessagingConnection, "legacy-telegram")
        if row is None:
            row = MessagingConnection(
                id="legacy-telegram",
                provider="telegram",
                label="Telegram (environment)",
                credential_source="environment",
                created_at=runtime.now(),
            )
        config = json.loads(row.settings_json)
        config.update(
            {
                "bootstrapUserId": str(runtime.settings.allowed_telegram_user_id),
                "mode": "polling",
            }
        )
        row.settings_json = json.dumps(config)
        session.add(row)
        session.flush()
        for user in session.exec(
            select(User).where(col(User.telegram_id).is_not(None))
        ).all():
            key = (row.id, str(user.telegram_id))
            if session.get(ExternalIdentity, key) is None:
                session.add(
                    ExternalIdentity(
                        connection_id=key[0], external_user_id=key[1], user_id=user.id
                    )
                )
            destination = session.exec(
                select(NotificationDestination).where(
                    NotificationDestination.connection_id == row.id,
                    NotificationDestination.external_conversation_id
                    == str(user.chat_id),
                )
            ).first()
            if destination is None and user.chat_id is not None:
                session.add(
                    NotificationDestination(
                        connection_id=row.id,
                        external_conversation_id=str(user.chat_id),
                        user_id=user.id,
                        opted_in=user.membership_active and not user.banned,
                    )
                )
        session.commit()


def verify_legacy(runtime, identity):
    with runtime.session_factory() as session:
        row = session.get(MessagingConnection, "legacy-telegram")
        first_check = row.last_checked_at is None
        set_verified_account(
            session, row=row, external_account=str(identity.id), now=runtime.now()
        )
        # Existing Telegram is already operated by the owner through its env
        # configuration. New managed connections require staged verification.
        if first_check:
            row.enabled = True
        row.state = "ready"
        row.inbound_verified = True
        session.add(row)
        session.commit()


async def relay_legacy(runtime, message):
    event = IncomingEvent(
        event_key=f"message:{message.chat.id}:{message.message_id}",
        actor=ActorRef(
            connection_id="legacy-telegram", external_user_id=str(message.from_user.id)
        ),
        conversation=ConversationRef(
            connection_id="legacy-telegram",
            external_conversation_id=str(message.chat.id),
            kind="direct" if message.chat.type == "private" else "shared",
        ),
        content=TextReceived(text=message.text),
        received_at=runtime.now(),
    )
    runtime.admit([event])


async def serve_connections(runtime, *, available_providers=()):
    from app.webapp import build_web_app

    app = build_web_app(
        session_factory=runtime.session_factory,
        bot_token="",
        payments=None,
        billing_enabled=False,
        available_providers=available_providers,
        bot_username=None,
        static_dir=Path("web/dist"),
        clients=runtime.clients,
        hosted_features_enabled=runtime.settings.hosted_features_enabled,
        messaging_runtime=runtime,
    )
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", runtime.settings.port).start()
    await runtime.start()
    try:
        await asyncio.Event().wait()
    finally:
        await runtime.close()
        await runner.cleanup()
