"""Single-process connection runtime and durable application delivery."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message
from sqlalchemy import event as sa_event
from sqlmodel import col, select

from app.application.context import ActionContext, CommandContext, Document, Keyboard
from app.callback_dispatch import apply
from app.callbacks import CallbackContext
from app.callbacks.routes import build_callback_registry
from app.commands import CommandError, parse_callback_request
from app.messaging.action_service import present, resolve_action
from app.messaging.adapters.slack import SlackAdapter
from app.messaging.adapters.telegram import TelegramAdapter
from app.messaging.adapters.whatsapp import WhatsAppAdapter
from app.messaging.connection_service import (
    SecretStore,
    require_connection,
    set_verified_account,
)
from app.messaging.contracts import (
    Accepted,
    ActionInvoked,
    ActorRef,
    ChatMessage,
    ConversationRef,
    DeliveryRequest,
    DigestNotice,
    ErrorCode,
    ImageReceived,
    IncomingEvent,
    MediaRef,
    MessageRef,
    MessagingError,
    TextReceived,
)
from app.messaging.identity_service import (
    AuthorizationStamp,
    authorize,
    observe_destination,
    stamped_user,
)
from app.messaging.queue_service import admit_event, enqueue, recover_interrupted
from app.models import (
    DigestRun,
    MessagingAddress,
    MessagingConnection,
    MessagingInbox,
    MessagingMessage,
    MessagingOperation,
    MessagingOutbox,
    NotificationDestination,
    User,
)
from app.pending_service import utc_naive

log = logging.getLogger(__name__)


def address_ref(row: MessagingAddress) -> ConversationRef:
    return ConversationRef.model_validate(
        {
            "connection_id": row.connection_id,
            "external_conversation_id": row.conversation_id,
            "kind": row.kind,
            "thread_id": row.thread_id or None,
        }
    )


def address_for(session, conversation: ConversationRef):
    row = session.exec(
        select(MessagingAddress).where(
            MessagingAddress.connection_id == conversation.connection_id,
            MessagingAddress.conversation_id == conversation.external_conversation_id,
            MessagingAddress.thread_id == (conversation.thread_id or ""),
        )
    ).first()
    if row is None:
        row = MessagingAddress(
            connection_id=conversation.connection_id,
            conversation_id=conversation.external_conversation_id,
            thread_id=conversation.thread_id or "",
            kind=conversation.kind,
        )
        session.add(row)
        session.flush()
    return row


class OutboxMessenger:
    def __init__(
        self,
        runtime,
        session,
        *,
        user: User,
        stamp: AuthorizationStamp,
        incoming: IncomingEvent,
        operation: str,
    ):
        self.runtime, self.session, self.user, self.stamp = (
            runtime,
            session,
            user,
            stamp,
        )
        self.incoming, self.operation, self.sequence = incoming, operation, 0

    def context(self, row: MessagingMessage):
        address = self.session.get(MessagingAddress, row.address_id)
        return CommandContext(
            actor_id=self.user.id,
            conversation_key=row.address_id,
            message_key=row.id,
            transport=self,
            text=row.text,
            is_private=address.kind == "direct",
            authorization=self.stamp,
        )

    async def send_text(self, *, conversation_key, text, actions=None):
        address = self.session.get(MessagingAddress, conversation_key)
        if address is None or address_ref(address) != self.incoming.conversation:
            raise MessagingError(
                ErrorCode.FORBIDDEN, "Workflow cannot send to another conversation"
            )
        self.sequence += 1
        now = self.runtime.now()
        content = present(
            self.session,
            user=self.user,
            conversation=self.incoming.conversation,
            text=text,
            actions=actions,
            now=now,
        )
        request = DeliveryRequest(
            delivery_key=f"{self.operation}:{self.sequence}",
            conversation=self.incoming.conversation,
            content=content,
        )
        outbox = enqueue(self.session, request, user=self.user, now=now)
        row = MessagingMessage(
            address_id=conversation_key, text=text, outbox_id=outbox.id
        )
        self.session.add(row)
        self.session.commit()
        return self.context(row)

    async def update_text(self, *, conversation_key, message_key, text, actions=None):
        row = self.session.get(MessagingMessage, message_key, populate_existing=True)
        if row is None or row.address_id != conversation_key:
            return await self.send_text(
                conversation_key=conversation_key, text=text, actions=actions
            )
        address = self.session.get(MessagingAddress, conversation_key)
        if address is None or address_ref(address) != self.incoming.conversation:
            raise MessagingError(
                ErrorCode.FORBIDDEN, "Workflow cannot edit another conversation"
            )
        now = self.runtime.now()
        content = present(
            self.session,
            user=self.user,
            conversation=self.incoming.conversation,
            text=text,
            actions=actions,
            now=now,
        )
        previous = (
            self.session.get(MessagingOutbox, row.outbox_id, populate_existing=True)
            if row.outbox_id
            else None
        )
        if previous and previous.status in {"pending", "retry"}:
            original = DeliveryRequest.model_validate_json(previous.request_json)
            previous.request_json = original.model_copy(
                update={"content": content}
            ).model_dump_json()
            self.session.add(previous)
        else:
            self.sequence += 1
            request = DeliveryRequest(
                delivery_key=f"{self.operation}:{self.sequence}",
                conversation=self.incoming.conversation,
                content=content,
                replace=MessageRef(
                    conversation=self.incoming.conversation,
                    external_message_id=row.external_message_id,
                )
                if row.external_message_id
                else None,
            )
            row.outbox_id = enqueue(self.session, request, user=self.user, now=now).id
        row.text = text
        self.session.add(row)
        self.session.commit()
        return self.context(row)

    async def remove_message(self, *, conversation_key, message_key):
        row = self.session.get(MessagingMessage, message_key)
        if row and row.address_id == conversation_key and row.outbox_id:
            outbox = self.session.get(MessagingOutbox, row.outbox_id)
            if outbox and outbox.status == "pending":
                outbox.status = "suppressed"
                self.session.add(outbox)
                self.session.commit()

    async def typing(self, *, conversation_key):
        return None

    async def send_document(
        self, *, conversation_key, document: Document, caption: str
    ):
        # Text export remains usable on providers without a document send port.
        return await self.send_text(
            conversation_key=conversation_key,
            text=f"{caption}\n{document.data.decode('utf-8')}",
        )


class MessagingRuntime:
    def __init__(
        self,
        *,
        session_factory,
        settings,
        clients,
        recipe_sources=(),
        intent_agent=None,
        composer=None,
        reschedule: Callable[[User], None] | None = None,
        http=None,
        now=lambda: datetime.now(UTC),
    ):
        self.session_factory, self.settings, self.clients = (
            session_factory,
            settings,
            clients,
        )
        self.recipe_sources, self.intent_agent, self.composer = (
            recipe_sources,
            intent_agent,
            composer,
        )
        self.secrets = SecretStore(settings.connection_secret_key)
        self.http = http or httpx.AsyncClient(timeout=20)
        self.owns_http = http is None
        self.now = now
        self.reschedule = reschedule or (lambda _user: None)
        self.bots: dict[str, tuple[int, Bot]] = {}
        self.tasks = []
        self.polling = {}
        self.closed = False
        self.last_digest_check = None

    def credentials(self, connection):
        if connection.credential_source == "environment":
            if connection.provider == "telegram":
                return {"botToken": self.settings.telegram_bot_token}
            raise MessagingError(
                ErrorCode.AUTH_FAILED, "Unknown environment credential source"
            )
        return self.secrets.decrypt(connection.secret_ciphertext)

    async def adapter(self, connection, *, destination=None):
        credentials = self.credentials(connection)
        config = json.loads(connection.settings_json)
        if connection.provider == "telegram":
            old = self.bots.get(connection.id)
            if old is None or old[0] != connection.revision:
                if old:
                    await old[1].session.close()
                    self.bots.pop(connection.id, None)
                bot = Bot(credentials["botToken"])
                try:
                    if (
                        connection.external_account
                        and str((await bot.get_me()).id) != connection.external_account
                    ):
                        raise MessagingError(
                            ErrorCode.CONFLICT,
                            "Credentials belong to another Telegram bot",
                        )
                except BaseException:
                    await bot.session.close()
                    raise
                self.bots[connection.id] = (connection.revision, bot)
            return TelegramAdapter(
                connection_id=connection.id, bot=self.bots[connection.id][1]
            )
        if connection.provider == "slack":
            if not credentials.get("botToken"):
                raise MessagingError(
                    ErrorCode.AUTH_FAILED, "Install the Slack app first"
                )
            return SlackAdapter(
                connection_id=connection.id,
                token=credentials["botToken"],
                http=self.http,
            )
        return WhatsAppAdapter(
            connection_id=connection.id,
            token=credentials["accessToken"],
            config=config,
            http=self.http,
            now=self.now(),
            last_customer_message_at=destination.last_customer_message_at
            if destination
            else (
                datetime.fromisoformat(config["testMessageAt"])
                if config.get("testMessageAt")
                else None
            ),
            opted_in=destination.opted_in if destination else False,
        )

    async def check_connection(self, connection_id):
        with self.session_factory() as session:
            row = require_connection(session, connection_id)
            revision = row.revision
            adapter = await self.adapter(row)
        account, metadata = await adapter.identity()
        with self.session_factory() as session:
            row = require_connection(session, connection_id)
            if row.revision != revision:
                raise MessagingError(
                    ErrorCode.CONFLICT, "Connection changed during validation"
                )
            set_verified_account(
                session, row=row, external_account=account, now=self.now()
            )
            config = json.loads(row.settings_json)
            if (
                config.get("botUserId")
                and metadata.get("botUserId") != config["botUserId"]
            ):
                raise MessagingError(
                    ErrorCode.CONFLICT,
                    "Credentials belong to another Slack bot; create a new connection",
                )
            config.update(metadata)
            row.settings_json = json.dumps(config)
            session.add(row)
            session.commit()
        return [
            {
                "code": "CREDENTIALS",
                "status": "PASS",
                "message": "Provider account verified",
            }
        ]

    def admit(self, events):
        with self.session_factory() as session:
            for event in events:
                row = require_connection(session, event.actor.connection_id)
                config = json.loads(row.settings_json)
                if (
                    event.conversation.kind == "direct"
                    and event.actor.external_user_id == config.get("bootstrapUserId")
                ):
                    row.inbound_verified = True
                    config["testConversation"] = event.conversation.model_dump()
                    config["testMessageAt"] = max(
                        config.get("testMessageAt", ""),
                        (event.occurred_at or event.received_at).isoformat(),
                    )
                    row.settings_json = json.dumps(config)
                    row.state = "ready" if row.external_account else row.state
                    session.add(row)
                if row.enabled:
                    admit_event(session, event)
            session.commit()

    def delivery_statuses(self, connection_id, payload):
        """Apply authenticated, scope-checked Meta delivery receipts monotonically."""
        with self.session_factory() as session:
            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    for status in change.get("value", {}).get("statuses", []):
                        if not isinstance(status, dict) or status.get("status") not in {
                            "sent",
                            "delivered",
                            "read",
                            "failed",
                        }:
                            continue
                        rows = session.exec(
                            select(MessagingOutbox).where(
                                MessagingOutbox.connection_id == connection_id,
                                col(MessagingOutbox.status).in_(
                                    ["accepted", "delivered", "read"]
                                ),
                            )
                        ).all()
                        for row in rows:
                            request = DeliveryRequest.model_validate_json(
                                row.request_json
                            )
                            if (
                                status.get("recipient_id")
                                != request.conversation.external_conversation_id
                            ):
                                continue
                            if not any(
                                ref.get("external_message_id") == status.get("id")
                                for ref in json.loads(row.receipts_json)
                            ):
                                continue
                            rank = {
                                "sent": 0,
                                "accepted": 0,
                                "failed": 1,
                                "delivered": 2,
                                "read": 3,
                            }
                            if rank[status["status"]] > rank[row.status]:
                                row.status = status["status"]
                                if row.status == "failed":
                                    row.error_code = "PROVIDER_DELIVERY_FAILED"
                                session.add(row)
                                for run in session.exec(
                                    select(DigestRun).where(
                                        DigestRun.delivery_id == row.id
                                    )
                                ).all():
                                    run.status = row.status
                                    session.add(run)
            session.commit()

    async def start(self):
        with self.session_factory() as session:
            recover_interrupted(session, now=self.now())
            session.commit()
        self.tasks = [
            asyncio.create_task(self.worker()),
            asyncio.create_task(self.sender()),
            asyncio.create_task(self.supervise()),
        ]

    async def close(self):
        self.closed = True
        tasks = self.tasks + list(self.polling.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for _, bot in self.bots.values():
            await bot.session.close()
        if self.owns_http:
            await self.http.aclose()

    async def supervise(self):
        while not self.closed:
            with self.session_factory() as session:
                rows = list(
                    session.exec(
                        select(MessagingConnection).where(
                            MessagingConnection.provider == "telegram"
                        )
                    ).all()
                )
            active = {
                r.id
                for r in rows
                if (r.enabled or r.state == "awaiting_inbound_verification")
                and r.id != "legacy-telegram"
            }
            for key in list(self.polling):
                if key not in active:
                    self.polling.pop(key).cancel()
            for key in active:
                if key not in self.polling or self.polling[key].done():
                    self.polling[key] = asyncio.create_task(self.poll_telegram(key))
            now = self.now()
            if (
                self.last_digest_check is None
                or now - self.last_digest_check >= timedelta(minutes=1)
            ):
                try:
                    await self.prepare_digests()
                except Exception as exc:  # noqa: BLE001 - isolate worker failures; redact external details
                    log.warning(
                        "messaging_digest_failed",
                        extra={"error_class": type(exc).__name__},
                    )
                self.last_digest_check = now
            await asyncio.sleep(2)

    async def poll_telegram(self, connection_id):
        offset = None
        while not self.closed:
            try:
                with self.session_factory() as session:
                    connection = require_connection(session, connection_id)
                    if (
                        not connection.enabled
                        and connection.state != "awaiting_inbound_verification"
                    ):
                        return
                    adapter = await self.adapter(connection)
                    assert isinstance(adapter, TelegramAdapter)
                updates = await adapter.bot.get_updates(
                    offset=offset,
                    timeout=20,
                    allowed_updates=["message", "callback_query"],
                )
                for update in updates:
                    message = update.message or (
                        update.callback_query.message if update.callback_query else None
                    )
                    actor = (
                        update.callback_query.from_user
                        if update.callback_query
                        else (
                            message.from_user if isinstance(message, Message) else None
                        )
                    )
                    if not isinstance(message, Message) or actor is None:
                        if update.callback_query:
                            try:
                                await adapter.bot.answer_callback_query(
                                    update.callback_query.id,
                                    text="Open /pantry for a fresh view",
                                )
                            except TelegramAPIError:
                                pass
                        offset = update.update_id + 1
                        continue
                    conversation = ConversationRef(
                        connection_id=connection_id,
                        external_conversation_id=str(message.chat.id),
                        kind="direct" if message.chat.type == "private" else "shared",
                        thread_id=str(message.message_thread_id)
                        if getattr(message, "message_thread_id", None)
                        else None,
                    )
                    if update.callback_query:
                        token = update.callback_query.data or ""
                        if not token.startswith("mp:"):
                            try:
                                await adapter.bot.answer_callback_query(
                                    update.callback_query.id,
                                    text="Open /pantry for a fresh view",
                                )
                            except TelegramAPIError:
                                pass  # Expired acknowledgments do not block polling.
                            offset = update.update_id + 1
                            continue
                        content = ActionInvoked(
                            token=token[3:],
                            source=MessageRef(
                                conversation=conversation,
                                external_message_id=str(message.message_id),
                            ),
                        )
                    elif message.photo:
                        content = ImageReceived(
                            media=MediaRef(
                                connection_id=connection_id,
                                external_media_id=message.photo[-1].file_id,
                            )
                        )
                    elif message.text:
                        content = TextReceived(text=message.text)
                    else:
                        offset = update.update_id + 1
                        continue
                    event = IncomingEvent(
                        event_key=str(update.update_id),
                        actor=ActorRef(
                            connection_id=connection_id, external_user_id=str(actor.id)
                        ),
                        conversation=conversation,
                        content=content,
                        received_at=self.now(),
                        reply_to=MessageRef(
                            conversation=conversation,
                            external_message_id=str(
                                message.reply_to_message.message_id
                            ),
                        )
                        if message.reply_to_message
                        else None,
                    )
                    self.admit([event])
                    if update.callback_query:
                        try:
                            await adapter.bot.answer_callback_query(
                                update.callback_query.id
                            )
                        except TelegramAPIError:
                            pass
                    offset = update.update_id + 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - isolate worker failures; redact external details
                log.warning(
                    "messaging_poll_failed",
                    extra={
                        "connection_id": connection_id,
                        "error_class": type(exc).__name__,
                    },
                )
                await asyncio.sleep(5)

    async def worker(self):
        while not self.closed:
            job = None
            with self.session_factory() as session:
                row = session.exec(
                    select(MessagingInbox)
                    .where(MessagingInbox.status == "pending")
                    .order_by(col(MessagingInbox.created_at))
                    .limit(1)
                ).first()
                if row:
                    row.status, row.attempts = "processing", row.attempts + 1
                    session.add(row)
                    session.commit()
                    job = (row.id, row.event_json)
            if job is None:
                await asyncio.sleep(0.25)
                continue
            key, payload = job
            try:
                await self.process(
                    IncomingEvent.model_validate_json(payload), operation=key
                )
                status, code = "complete", None
            except asyncio.CancelledError:
                raise
            except MessagingError as exc:
                status, code = "failed", exc.code.value
            except Exception as exc:  # noqa: BLE001 - isolate worker failures; redact external details
                log.warning(
                    "messaging_command_failed",
                    extra={"event_id": key, "error_class": type(exc).__name__},
                )
                status, code = "unknown", "DELIVERY_UNKNOWN"
            with self.session_factory() as session:
                row = session.get(MessagingInbox, key)
                row.status, row.error_code = status, code
                session.add(row)
                session.commit()

    async def process(self, incoming: IncomingEvent, *, operation: str):
        from app.application.messaging_commands import run_identity_command
        from app.bot import _MESSAGE_COMMANDS
        from app.commands import parse_correct_reply_marker
        from app.handlers.meta import handle_nl_message, handle_photo
        from app.handlers.pantry import handle_correct_reply

        now = self.now()
        with self.session_factory() as session:
            if await run_identity_command(self, session, incoming, operation=operation):
                return
            if not self.settings.has_credentials(self.settings.llm_provider):
                enqueue(
                    session,
                    DeliveryRequest(
                        delivery_key=f"{operation}:setup",
                        conversation=incoming.conversation,
                        content=ChatMessage(
                            text="The deployment owner must configure an LLM provider and restart before food workflows are available."
                        ),
                    ),
                    user=None,
                    now=now,
                )
                session.commit()
                return
            raw = (
                incoming.content.text.strip()
                if isinstance(incoming.content, TextReceived)
                else ""
            )
            name = (
                raw.split(maxsplit=1)[0].lstrip("/").split("@")[0].lower()
                if raw
                else ""
            )
            try:
                user = authorize(
                    session,
                    actor=incoming.actor,
                    conversation=incoming.conversation,
                    now=now,
                    open_registration=self.settings.effective_open_registration,
                    hosted=self.settings.hosted_features_enabled,
                    default_provider=self.settings.llm_provider,
                    create=name == "start",
                )
            except MessagingError as exc:
                enqueue(
                    session,
                    DeliveryRequest(
                        delivery_key=f"{operation}:denied",
                        conversation=incoming.conversation,
                        content=ChatMessage(text=exc.message),
                    ),
                    user=None,
                    now=now,
                )
                session.commit()
                return
            destination = observe_destination(
                session,
                user=user,
                conversation=incoming.conversation,
                now=incoming.occurred_at or incoming.received_at,
            )
            stamp = AuthorizationStamp(
                user.id, user.household_id, user.membership_generation
            )
            address = address_for(session, incoming.conversation)
            session.commit()

            def verify_commit(db):
                # Use a fresh SQL read instead of a cached ORM identity after slow work.
                current = db.exec(
                    select(
                        User.banned,
                        User.membership_active,
                        User.household_id,
                        User.membership_generation,
                    ).where(User.id == stamp.user_id)
                ).first()
                if current != (False, True, stamp.household_id, stamp.generation):
                    raise MessagingError(
                        ErrorCode.FORBIDDEN, "Household membership changed"
                    )
                if db.get(MessagingOperation, operation) is None:
                    db.add(
                        MessagingOperation(
                            key=operation,
                            result_json='{"state":"started"}',
                            created_at=utc_naive(now),
                        )
                    )

            sa_event.listen(session, "before_commit", verify_commit)
            messenger = OutboxMessenger(
                self,
                session,
                user=user,
                stamp=stamp,
                incoming=incoming,
                operation=operation,
            )
            message = CommandContext(
                actor_id=user.id,
                conversation_key=address.id,
                message_key=0,
                transport=messenger,
                text=raw,
                is_private=incoming.conversation.kind == "direct",
                authorization=stamp,
            )
            if incoming.reply_to:
                original = session.exec(
                    select(MessagingMessage)
                    .join(MessagingAddress)
                    .where(
                        MessagingAddress.connection_id == incoming.actor.connection_id,
                        MessagingAddress.conversation_id
                        == incoming.conversation.external_conversation_id,
                        MessagingMessage.external_message_id
                        == incoming.reply_to.external_message_id,
                    )
                ).first()
                if original:
                    original_delivery = (
                        session.get(MessagingOutbox, original.outbox_id)
                        if original.outbox_id
                        else None
                    )
                    if (
                        original_delivery
                        and original_delivery.household_id == user.household_id
                        and original_delivery.membership_generation
                        == user.membership_generation
                    ):
                        message.reply_to = messenger.context(original)
            deferred = []

            @contextmanager
            def shared_session():
                yield session

            async def download_image(key):
                if not isinstance(incoming.content, ImageReceived):
                    raise MessagingError(
                        ErrorCode.INVALID_INPUT, "No image in this event"
                    )
                connection = require_connection(session, incoming.actor.connection_id)
                adapter = await self.adapter(connection, destination=destination)
                image = await adapter.download_image(incoming.content.media)
                return image.data

            deps = {
                "session_factory": shared_session,
                "clients": self.clients,
                "now_provider": lambda tz: self.now().astimezone(ZoneInfo(tz)),
                "on_user_created": lambda _: None,
                "reschedule": self.reschedule,
                "unschedule": lambda _: None,
                "translation_llm": self.clients.translation,
                "recipe_sources": self.recipe_sources,
                "intent_agent": self.intent_agent,
                "composer": self.composer,
                "bot": messenger,
                "spawn": deferred.append,
                "photo_downloader": download_image,
            }
            commands = {
                name: (fn, args)
                for name, fn, args in _MESSAGE_COMMANDS
                if fn.__module__
                not in {"app.handlers.household", "app.handlers.billing"}
            }
            try:
                if isinstance(incoming.content, ImageReceived):
                    message.images = (
                        f"{incoming.actor.connection_id}:{incoming.content.media.external_media_id}",
                    )
                    await handle_photo(
                        message,
                        **{
                            k: deps[k]
                            for k in (
                                "session_factory",
                                "now_provider",
                                "clients",
                                "photo_downloader",
                                "translation_llm",
                                "bot",
                                "spawn",
                            )
                        },
                    )
                elif isinstance(incoming.content, ActionInvoked):
                    command = resolve_action(
                        session,
                        token=incoming.content.token,
                        user=user,
                        conversation=incoming.conversation,
                        now=now,
                    )
                    source = session.exec(
                        select(MessagingMessage).where(
                            MessagingMessage.address_id == address.id,
                            MessagingMessage.external_message_id
                            == incoming.content.source.external_message_id,
                        )
                    ).first()
                    if source:
                        message = messenger.context(source)

                    async def acknowledge(text, *, alert: bool | None = None):
                        if text:
                            await message.reply(text)

                    action = ActionContext(
                        actor_id=user.id,
                        data=command,
                        source=message,
                        acknowledge=acknowledge,
                        authorization=stamp,
                    )
                    context = CallbackContext(
                        callback=action,
                        session_factory=shared_session,
                        now_provider=deps["now_provider"],
                        clients=self.clients,
                        translation_llm=self.clients.translation,
                        bot=messenger,
                        spawn=deferred.append,
                        recipe_sources=self.recipe_sources,
                    )
                    result = await build_callback_registry().dispatch(
                        parse_callback_request(command), context
                    )
                    await apply(action, result)
                elif (
                    message.reply_to
                    and parse_correct_reply_marker(message.reply_to.text) is not None
                ):
                    await handle_correct_reply(
                        message,
                        **{
                            k: deps[k]
                            for k in ("session_factory", "now_provider", "clients")
                        },
                    )
                elif name in commands:
                    handler, dependencies = commands[name]
                    message.text = (
                        "/"
                        + name
                        + (
                            " " + raw.split(maxsplit=1)[1]
                            if len(raw.split(maxsplit=1)) == 2
                            else ""
                        )
                    )
                    await handler(message, **{k: deps[k] for k in dependencies})
                elif self.intent_agent and not raw.startswith("/"):
                    await handle_nl_message(
                        message,
                        **{
                            k: deps[k]
                            for k in (
                                "session_factory",
                                "now_provider",
                                "clients",
                                "intent_agent",
                                "translation_llm",
                                "composer",
                                "recipe_sources",
                            )
                        },
                    )
                else:
                    await message.reply(
                        "Use pantry, add, cook, plan, shopping, favorites, prefs, quota, lang, or reminders on/off."
                    )
                for work in deferred:
                    await work
                session.commit()
            except (MessagingError, CommandError) as exc:
                session.rollback()
                if stamped_user(session, stamp):
                    await message.reply(
                        exc.message if isinstance(exc, MessagingError) else str(exc)
                    )
            finally:
                for work in deferred:
                    if getattr(work, "cr_frame", None) is not None:
                        work.close()
                sa_event.remove(session, "before_commit", verify_commit)

    async def sender(self):
        while not self.closed:
            with self.session_factory() as session:
                row = session.exec(
                    select(MessagingOutbox)
                    .where(
                        col(MessagingOutbox.status).in_(["pending", "retry"]),
                        (col(MessagingOutbox.next_attempt_at).is_(None))
                        | (
                            col(MessagingOutbox.next_attempt_at)
                            <= utc_naive(self.now())
                        ),
                    )
                    .order_by(col(MessagingOutbox.created_at))
                    .limit(1)
                ).first()
                key = row.id if row else None
            if key is None:
                await asyncio.sleep(0.25)
                continue
            try:
                await self.send_one(key)
            except Exception as exc:  # noqa: BLE001 - isolate worker failures; redact external details
                log.warning(
                    "messaging_sender_failed", extra={"error_class": type(exc).__name__}
                )
                await asyncio.sleep(2)

    async def send_one(self, key):
        with self.session_factory() as session:
            row = session.get(MessagingOutbox, key)
            if row is None or row.status not in {"pending", "retry"}:
                return
            connection = require_connection(session, row.connection_id)
            request = DeliveryRequest.model_validate_json(row.request_json)
            if not connection.enabled and not row.delivery_key.startswith(
                "admin-test:"
            ):
                row.status, row.error_code = "suppressed", "FORBIDDEN"
                session.add(row)
                session.commit()
                return
            if (
                row.user_id is not None
                and stamped_user(
                    session,
                    AuthorizationStamp(
                        row.user_id, row.household_id, row.membership_generation
                    ),
                )
                is None
            ):
                row.status, row.error_code = "suppressed", "FORBIDDEN"
                session.add(row)
                session.commit()
                return
            destination = session.exec(
                select(NotificationDestination).where(
                    NotificationDestination.connection_id == row.connection_id,
                    NotificationDestination.external_conversation_id
                    == request.conversation.external_conversation_id,
                )
            ).first()
            if isinstance(request.content, DigestNotice) and (
                destination is None
                or not destination.opted_in
                or destination.user_id != row.user_id
            ):
                row.status, row.error_code = "suppressed", "opted_out"
                session.add(row)
                session.commit()
                return
            row.status, row.attempts = "sending", row.attempts + 1
            session.add(row)
            session.commit()
            try:
                revision = connection.revision
                adapter = await self.adapter(connection, destination=destination)
                session.expire_all()
                if connection.revision != revision or (
                    not connection.enabled
                    and not row.delivery_key.startswith("admin-test:")
                ):
                    raise MessagingError(
                        ErrorCode.FORBIDDEN, "Connection changed before delivery"
                    )
                if (
                    row.user_id is not None
                    and stamped_user(
                        session,
                        AuthorizationStamp(
                            row.user_id, row.household_id, row.membership_generation
                        ),
                    )
                    is None
                ):
                    raise MessagingError(
                        ErrorCode.FORBIDDEN, "Membership changed before delivery"
                    )
                result = await adapter.deliver(request)
                if isinstance(result, Accepted):
                    row.status, row.error_code = "accepted", None
                    row.receipts_json = json.dumps(
                        [r.model_dump() for r in result.messages]
                    )
                    message = session.exec(
                        select(MessagingMessage).where(
                            MessagingMessage.outbox_id == key
                        )
                    ).first()
                    if message:
                        message.external_message_id = result.messages[
                            -1
                        ].external_message_id
                        session.add(message)
                else:
                    row.status, row.error_code = "suppressed", result.reason
            except MessagingError as exc:
                row.error_code = exc.code.value
                if (
                    exc.code in {ErrorCode.RATE_LIMITED, ErrorCode.TEMPORARY_FAILURE}
                    and row.attempts < 4
                ):
                    row.status = "retry"
                    row.next_attempt_at = utc_naive(self.now()) + timedelta(
                        seconds=max(
                            1, min(exc.retry_after_seconds or 30 * row.attempts, 3600)
                        )
                    )
                else:
                    row.status = (
                        "unknown"
                        if exc.code == ErrorCode.DELIVERY_UNKNOWN
                        else "failed"
                    )
                if exc.code == ErrorCode.AUTH_FAILED:
                    connection.state, connection.last_error_code = (
                        "degraded",
                        exc.code.value,
                    )
                    session.add(connection)
            except Exception as exc:  # noqa: BLE001 - isolate worker failures; redact external details
                log.warning(
                    "messaging_send_failed",
                    extra={"delivery_id": key, "error_class": type(exc).__name__},
                )
                row.status, row.error_code = "unknown", "DELIVERY_UNKNOWN"
            session.add(row)
            for run in session.exec(
                select(DigestRun).where(DigestRun.delivery_id == key)
            ).all():
                run.status = row.status
                session.add(run)
            session.commit()

    async def prepare_digests(self):
        from app import views
        from app.pantry_service import list_digest_due
        from app.renderer import build_digest_keyboard

        with self.session_factory() as session:
            destinations = session.exec(
                select(NotificationDestination).where(
                    NotificationDestination.opted_in == True
                )
            ).all()
            for destination in destinations:
                # Existing Telegram cron keeps its compatibility behavior until
                # the user explicitly selects a connection-managed destination.
                if destination.connection_id == "legacy-telegram":
                    continue
                user = session.get(User, destination.user_id)
                if user is None or user.banned or not user.membership_active:
                    continue
                local = self.now().astimezone(ZoneInfo(user.tz))
                if local.hour < user.digest_hour or session.get(
                    DigestRun, (user.id, local.date())
                ):
                    continue
                if user.last_digest_date and user.last_digest_date >= local.date():
                    continue
                items = list_digest_due(
                    session, household_id=user.household_id, today=local.date()
                )
                run = DigestRun(
                    user_id=user.id,
                    local_date=local.date(),
                    status="silent" if not items else "prepared",
                )
                session.add(run)
                if items:
                    view = await views.digest(
                        session,
                        items,
                        user=user,
                        today=local.date(),
                        translation_llm=self.clients.translation,
                    )
                    conversation = ConversationRef(
                        connection_id=destination.connection_id,
                        external_conversation_id=destination.external_conversation_id,
                    )
                    content = present(
                        session,
                        user=user,
                        conversation=conversation,
                        text=view.text,
                        actions=Keyboard(
                            build_digest_keyboard(
                                view.rendered_items,
                                has_more=view.has_more,
                                today=local.date(),
                                lang=user.lang,
                                names=view.names,
                            )
                        ),
                        now=self.now(),
                    )
                    delivery = enqueue(
                        session,
                        DeliveryRequest(
                            delivery_key=f"digest:{user.id}:{local.date()}",
                            conversation=conversation,
                            content=DigestNotice(
                                localized_message=content,
                                due_count=len(items),
                                language=user.lang,
                            ),
                        ),
                        user=user,
                        now=self.now(),
                    )
                    run.delivery_id = delivery.id
                user.last_digest_date = local.date()
                session.add(user)
                session.add(run)
                session.commit()
