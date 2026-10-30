from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from cryptography.fernet import Fernet
from sqlmodel import Session, col, create_engine, select

from app.client_set import EMPTY_CLIENTS
from app.connection_admin import register_connection_routes
from app.messaging.adapters.slack import SlackAdapter
from app.messaging.adapters.whatsapp import WhatsAppAdapter
from app.messaging.contracts import (
    Accepted,
    ActionButton,
    ActionInvoked,
    ActorRef,
    ChatMessage,
    ConversationRef,
    DeliveryRequest,
    DigestNotice,
    IncomingEvent,
    MessageRef,
    MessagingError,
    Suppressed,
    TextReceived,
)
from app.messaging.identity_service import (
    authorize,
    confirm_link,
    create_link,
    find_user,
    observe_destination,
    redeem_link,
    revoke_membership,
    select_destination,
)
from app.messaging.ingress import slack_events, verify_slack, verify_whatsapp
from app.messaging.queue_service import admit_event, enqueue, recover_interrupted
from app.messaging.runtime import MessagingRuntime
from app.models import (
    ExternalIdentity,
    MessagingConnection,
    MessagingInbox,
    MessagingOutbox,
    PantryItem,
    User,
)

NOW = datetime(2026, 10, 30, 14, tzinfo=UTC)


@pytest.fixture
def state(sqlite_db_path):
    engine = create_engine(f"sqlite:///{sqlite_db_path.as_posix()}")
    settings = SimpleNamespace(
        connection_secret_key=Fernet.generate_key().decode(),
        connection_admin_token="owner-token-" * 4,
        public_base_url="https://food.example",
        llm_provider="anthropic",
        effective_open_registration=True,
        hosted_features_enabled=True,
        has_credentials=lambda p: True,
        telegram_bot_token="",
        allowed_telegram_user_id=0,
    )
    runtime = MessagingRuntime(
        session_factory=lambda: Session(engine),
        settings=settings,
        clients=EMPTY_CLIENTS,
        now=lambda: NOW,
        http=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(500))
        ),
    )
    with runtime.session_factory() as session:
        for key, provider, account in [("s", "slack", "T1"), ("w", "whatsapp", "123")]:
            session.add(
                MessagingConnection(
                    id=key,
                    label=key,
                    provider=provider,
                    enabled=True,
                    state="ready",
                    external_account=account,
                    created_at=NOW,
                    settings_json=json.dumps(
                        {
                            "bootstrapUserId": "U1",
                            "businessAccountId": "999",
                            "phoneNumberId": "123",
                            "apiVersion": "v25.0",
                        }
                    ),
                    secret_ciphertext=runtime.secrets.encrypt(
                        {
                            "signingSecret": "signed",
                            "botToken": "bot-secret",
                            "accessToken": "secret",
                            "appSecret": "meta-secret",
                            "verifyToken": "verify",
                        }
                    ),
                )
            )
        session.commit()
    yield runtime
    engine.dispose()


def event(text="start", *, provider="s", user="U1", channel="D1", key="1"):
    return IncomingEvent(
        event_key=key,
        actor=ActorRef(connection_id=provider, external_user_id=user),
        conversation=ConversationRef(
            connection_id=provider, external_conversation_id=channel
        ),
        content=TextReceived(text=text),
        received_at=NOW,
    )


def member(runtime, incoming=None):
    incoming = incoming or event()
    with runtime.session_factory() as session:
        user = authorize(
            session,
            actor=incoming.actor,
            conversation=incoming.conversation,
            now=NOW,
            open_registration=True,
            hosted=True,
            default_provider="anthropic",
            create=True,
        )
        session.commit()
        session.refresh(user)
        return user


def test_identity_is_scoped_and_never_fabricates_telegram_id(state):
    first, second = member(state), member(state, event(provider="w"))
    assert first.id != second.id
    assert first.telegram_id is second.telegram_id is None
    with state.session_factory() as session:
        assert session.get(ExternalIdentity, ("s", "U1")).user_id == first.id
        assert session.get(ExternalIdentity, ("w", "U1")).user_id == second.id


def test_link_requires_original_account_confirmation_and_rejects_merge(state):
    user = member(state)
    with state.session_factory() as session:
        source = session.get(User, user.id)
        code = create_link(session, user=source, actor=event().actor, now=NOW)
        target = event(provider="w", user="1555555").actor
        redeem_link(session, token=code, actor=target, now=NOW)
        assert find_user(session, target) is None
        with pytest.raises(MessagingError):
            confirm_link(session, token=code, user=source, actor=target, now=NOW)
        confirm_link(session, token=code, user=source, actor=event().actor, now=NOW)
        linked = find_user(session, target)
        assert linked is not None and linked.id == source.id
        with pytest.raises(MessagingError):
            redeem_link(session, token=code, actor=target, now=NOW)
        session.commit()
    other = member(state, event(provider="w", user="occupied"))
    with state.session_factory() as session:
        code = create_link(
            session, user=session.get(User, user.id), actor=event().actor, now=NOW
        )
        with pytest.raises(MessagingError):
            redeem_link(
                session,
                token=code,
                actor=event(provider="w", user="occupied").actor,
                now=NOW,
            )
        assert other.id != user.id


def test_signatures_require_original_bytes_and_slack_freshness():
    raw = b'{"event":"hello"}'
    timestamp = str(int(NOW.timestamp()))
    signed = (
        "v0="
        + hmac.new(
            b"key", f"v0:{timestamp}:".encode() + raw, hashlib.sha256
        ).hexdigest()
    )
    headers = {"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": signed}
    verify_slack(raw, headers, secret="key", now=NOW)
    for altered, now in [(raw + b" ", NOW), (raw, NOW + timedelta(minutes=6))]:
        with pytest.raises(MessagingError):
            verify_slack(altered, headers, secret="key", now=now)
    meta = {
        "X-Hub-Signature-256": "sha256="
        + hmac.new(b"meta", raw, hashlib.sha256).hexdigest()
    }
    verify_whatsapp(raw, meta, secret="meta")
    with pytest.raises(MessagingError):
        verify_whatsapp(raw, meta, secret="wrong")


def test_duplicate_subscriptions_share_inbox_key_and_reject_other_team(state):
    payload = {
        "team_id": "T1",
        "event_id": "Ev1",
        "event": {
            "type": "message",
            "user": "U1",
            "channel": "D1",
            "ts": "123.456",
            "text": "pantry",
        },
    }
    first = slack_events(payload, connection_id="s", account="T1", now=NOW)[0]
    payload["event_id"] = "Ev2"
    second = slack_events(payload, connection_id="s", account="T1", now=NOW)[0]
    with state.session_factory() as session:
        assert admit_event(session, first)
        assert not admit_event(session, second)
        session.commit()
        assert len(session.exec(select(MessagingInbox)).all()) == 1
    with pytest.raises(MessagingError):
        slack_events(payload, connection_id="s", account="T2", now=NOW)


@pytest.mark.asyncio
async def test_native_slack_pantry_and_actions_use_shared_application(state):
    await state.process(event("start"), operation="start")
    with state.session_factory() as session:
        user = find_user(session, event().actor)
        assert user is not None
        item = PantryItem(
            household_id=user.household_id,
            raw_name="Apple",
            normalized_name="apple",
            category="produce",
            qty=1,
            unit="piece",
            purchased_on=NOW.date(),
            expires_on=NOW.date(),
            shelf_life_days=1,
            shelf_life_source="llm",
            ingest_shelf_life_source="llm",
            created_via="text",
            created_at=NOW,
        )
        session.add(item)
        session.commit()
    await state.process(event("pantry", key="2"), operation="pantry")
    with state.session_factory() as session:
        rows = session.exec(
            select(MessagingOutbox).where(
                col(MessagingOutbox.delivery_key).like("pantry:%")
            )
        ).all()
        assert rows
        request = DeliveryRequest.model_validate_json(rows[-1].request_json)
        assert isinstance(request.content, ChatMessage)
        assert "Apple" in request.content.text or "apple" in request.content.text
        assert request.content.buttons
        button = request.content.buttons[0]
        assert isinstance(button, ActionButton)
        token = button.token
    incoming = event(key="3").model_copy(
        update={
            "content": ActionInvoked(
                token=token,
                source=MessageRef(
                    conversation=event().conversation, external_message_id="12.1"
                ),
            )
        }
    )
    await state.process(incoming, operation="action")
    with state.session_factory() as session:
        assert session.exec(
            select(MessagingOutbox).where(
                col(MessagingOutbox.delivery_key).like("action:%")
            )
        ).first()


@pytest.mark.asyncio
async def test_revocation_suppresses_pending_delivery_and_restart_never_repeats_unknown(
    state,
):
    user = member(state)
    with state.session_factory() as session:
        user = session.get(User, user.id)
        outgoing = enqueue(
            session,
            DeliveryRequest(
                delivery_key="work",
                conversation=event().conversation,
                content=ChatMessage(text="private"),
            ),
            user=user,
            now=NOW,
        )
        revoke_membership(session, user)
        session.commit()
        key = outgoing.id
    await state.send_one(key)
    with state.session_factory() as session:
        assert session.get(MessagingOutbox, key).status == "suppressed"
        row = MessagingInbox(
            connection_id="s",
            event_key="paid",
            event_json=event().model_dump_json(),
            status="processing",
            created_at=NOW,
        )
        session.add(row)
        outgoing = enqueue(
            session,
            DeliveryRequest(
                delivery_key="uncertain",
                conversation=event().conversation,
                content=ChatMessage(text="sent?"),
            ),
            user=None,
            now=NOW,
        )
        outgoing.status = "sending"
        session.add(outgoing)
        session.commit()
        recover_interrupted(session, now=NOW)
        session.commit()
        assert session.get(MessagingInbox, row.id).status == "unknown"
        assert session.get(MessagingOutbox, outgoing.id).status == "unknown"


def test_destination_selection_and_old_messages_do_not_extend_window(state):
    user = member(state)
    with state.session_factory() as session:
        user = session.get(User, user.id)
        one = observe_destination(
            session, user=user, conversation=event().conversation, now=NOW
        )
        two = observe_destination(
            session,
            user=user,
            conversation=event(provider="w", channel="123").conversation,
            now=NOW,
        )
        assert one is not None and two is not None
        select_destination(session, user=user, destination=one, enabled=True)
        select_destination(session, user=user, destination=two, enabled=True)
        assert two.opted_in and not one.opted_in
        observe_destination(
            session,
            user=user,
            conversation=event(provider="w", channel="123").conversation,
            now=NOW - timedelta(days=1),
        )
        assert two.last_customer_message_at == NOW.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_whatsapp_window_optin_templates_and_accepted_reference():
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        adapter = WhatsAppAdapter(
            connection_id="w",
            token="secret",
            config={"apiVersion": "v25.0", "phoneNumberId": "123"},
            http=http,
            now=NOW,
            last_customer_message_at=NOW - timedelta(hours=25),
            opted_in=True,
        )
        request = DeliveryRequest(
            delivery_key="digest",
            conversation=event(provider="w").conversation,
            content=DigestNotice(
                localized_message=ChatMessage(text="2 items due"),
                due_count=2,
                language="en",
            ),
        )
        result = await adapter.deliver(request)
        assert isinstance(result, Suppressed) and result.reason == "template_required"
        assert not calls
        adapter.config["digestTemplates"] = [
            {"name": "pantry_reminder", "language": "en_US"}
        ]
        assert isinstance(await adapter.deliver(request), Accepted)
        assert calls[0]["type"] == "template"
        adapter.opted_in = False
        result = await adapter.deliver(request)
        assert isinstance(result, Suppressed) and result.reason == "opted_out"
        adapter.opted_in, adapter.last_customer_message_at = (
            True,
            NOW - timedelta(hours=23),
        )
        await adapter.deliver(request)
        assert calls[-1]["type"] == "text"


@pytest.mark.asyncio
async def test_slack_unknown_send_is_not_fallback_resend():
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("private provider data", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as http:
        adapter = SlackAdapter(connection_id="s", token="secret", http=http)
        conversation = event().conversation
        request = DeliveryRequest(
            delivery_key="edit",
            conversation=conversation,
            content=ChatMessage(text="Updated"),
            replace=MessageRef(conversation=conversation, external_message_id="1.2"),
        )
        with pytest.raises(MessagingError) as result:
            await adapter.deliver(request)
        assert result.value.code == "DELIVERY_UNKNOWN"
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_admin_auth_csrf_write_only_credentials_and_revision(state):
    app = web.Application()
    register_connection_routes(app, state)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/admin/api/connections")
        assert response.status == 401
        response = await client.post(
            "/admin/api/session",
            json={"token": state.settings.connection_admin_token},
            headers={"Origin": "https://evil.example"},
        )
        assert response.status == 403
        response = await client.post(
            "/admin/api/session",
            json={"token": state.settings.connection_admin_token},
            headers={"Origin": "https://food.example"},
        )
        assert response.status == 200
        csrf = (await response.json())["csrfToken"]
        cookie = response.cookies["fm_connections"]
        assert cookie["httponly"] and cookie["secure"] and cookie["samesite"] == "Lax"
        headers = {
            "Cookie": f"fm_connections={cookie.value}",
            "Origin": "https://food.example",
        }
        value = {
            "provider": "telegram",
            "label": "Kitchen",
            "settings": {"bootstrapUserId": "1234"},
            "credentials": {"botToken": "1234:do-not-leak"},
        }
        response = await client.post(
            "/admin/api/connections", json=value, headers=headers
        )
        assert response.status == 403
        headers["X-CSRF-Token"] = csrf
        response = await client.post(
            "/admin/api/connections", json=value, headers=headers
        )
        assert response.status == 201
        data = await response.json()
        assert "do-not-leak" not in json.dumps(data)
        with state.session_factory() as session:
            row = session.get(MessagingConnection, data["id"])
            assert "do-not-leak" not in row.secret_ciphertext
            assert (
                state.secrets.decrypt(row.secret_ciphertext)["botToken"]
                == value["credentials"]["botToken"]
            )
        response = await client.patch(
            "/admin/api/connections/" + data["id"],
            json={"enabled": True},
            headers=headers,
        )
        assert response.status == 409
        response = await client.patch(
            "/admin/api/connections/" + data["id"],
            json={"enabled": True},
            headers={**headers, "If-Match": str(data["revision"])},
        )
        assert response.status == 409
        state.settings.connection_admin_token = "rotated" * 10
        assert (
            await client.get("/admin/api/connections", headers=headers)
        ).status == 401
