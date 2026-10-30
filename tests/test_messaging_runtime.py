import hashlib
import hmac
import json
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image
from sqlmodel import select

from app.client_set import PerUserClients
from app.connection_admin import register_connection_routes
from app.llm import LLMResult, ParsedItem, ParseResult
from app.messaging.adapters.http import checked_image, download
from app.messaging.adapters.whatsapp import WhatsAppAdapter
from app.messaging.contracts import (
    ChatMessage,
    DeliveryRequest,
    DownloadedImage,
    ErrorCode,
    ImageReceived,
    MediaRef,
    MessagingError,
)
from app.messaging.identity_service import (
    observe_destination,
    revoke_membership,
    select_destination,
)
from app.messaging.queue_service import enqueue
from app.models import (
    DigestRun,
    MessagingConnection,
    MessagingInbox,
    MessagingOutbox,
    PantryItem,
    Receipt,
    User,
)
from tests.fakes import FakeLLMClient
from tests.test_messaging import NOW, event, member
from tests.test_messaging import state as state  # noqa: PLC0414 - import pytest fixture


@pytest.mark.asyncio
async def test_signed_hook_persists_before_ack_and_retries_deduplicate(state):
    app = web.Application()
    register_connection_routes(app, state)
    raw = json.dumps(
        {
            "team_id": "T1",
            "event_id": "Ev1",
            "event": {
                "type": "message",
                "channel_type": "im",
                "user": "U1",
                "channel": "D1",
                "text": "start",
                "ts": "1.234",
            },
        }
    ).encode()
    timestamp = str(int(NOW.timestamp()))
    headers = {
        "Content-Type": "application/json",
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": "v0="
        + hmac.new(
            b"signed", f"v0:{timestamp}:".encode() + raw, hashlib.sha256
        ).hexdigest(),
    }
    async with TestClient(TestServer(app)) as client:
        assert (
            await client.post("/hooks/slack/s", data=raw + b" ", headers=headers)
        ).status == 401
        for _ in range(2):
            assert (
                await client.post("/hooks/slack/s", data=raw, headers=headers)
            ).status == 200
        with state.session_factory() as session:
            assert len(session.exec(select(MessagingInbox)).all()) == 1
            assert session.get(MessagingConnection, "s").inbound_verified


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke_during_parse", [False, True])
async def test_native_photo_reuses_ingestion_without_telegram_object(
    state, monkeypatch, revoke_during_parse
):
    llm = FakeLLMClient(
        canned=LLMResult(
            parse=ParseResult(
                items=[
                    ParsedItem(
                        is_food=True,
                        name="Apple",
                        category="produce",
                        est_shelf_life_days=7,
                        confidence=0.9,
                    )
                ]
            )
        )
    )
    state.clients = PerUserClients(_image=llm)
    user = member(state)
    if revoke_during_parse:
        parse = llm.extract_items_from_image

        async def revoke_then_parse(*args, **kwargs):
            with state.session_factory() as session:
                current = session.get(User, user.id)
                assert current is not None
                revoke_membership(session, current)
                session.commit()
            return await parse(*args, **kwargs)

        monkeypatch.setattr(llm, "extract_items_from_image", revoke_then_parse)
    monkeypatch.setattr(
        state,
        "adapter",
        AsyncMock(
            return_value=SimpleNamespace(
                download_image=AsyncMock(
                    return_value=DownloadedImage(data=b"image", mime_type="image/png")
                )
            )
        ),
    )
    incoming = event(key="photo").model_copy(
        update={
            "content": ImageReceived(
                media=MediaRef(connection_id="s", external_media_id="F1")
            )
        }
    )
    await state.process(incoming, operation="photo")
    with state.session_factory() as session:
        receipts = session.exec(select(Receipt)).all()
        assert len(receipts) == (0 if revoke_during_parse else 1)
        if receipts:
            assert receipts[0].photo_file_id == "s:F1"
        assert len(session.exec(select(PantryItem)).all()) == (
            0 if revoke_during_parse else 1
        )
        assert llm.calls == [b"image"]


@pytest.mark.asyncio
async def test_digest_is_once_per_local_day_and_selected_destination(state):
    user = member(state)
    with state.session_factory() as session:
        user = session.get(User, user.id)
        user.tz, user.digest_hour = "UTC", 8
        session.add(user)
        destination = observe_destination(
            session, user=user, conversation=event().conversation, now=NOW
        )
        assert destination is not None
        select_destination(session, user=user, destination=destination, enabled=True)
        session.add(
            PantryItem(
                household_id=user.household_id,
                raw_name="Apple",
                normalized_name="apple",
                category="produce",
                qty=1,
                purchased_on=NOW.date(),
                expires_on=NOW.date(),
                shelf_life_days=1,
                shelf_life_source="llm",
                ingest_shelf_life_source="llm",
                created_via="manual",
                created_at=NOW,
            )
        )
        session.commit()
    await state.prepare_digests()
    await state.prepare_digests()
    with state.session_factory() as session:
        assert len(session.exec(select(DigestRun)).all()) == 1
        rows = session.exec(select(MessagingOutbox)).all()
        assert len(rows) == 1 and rows[0].connection_id == "s"


@pytest.mark.asyncio
async def test_sender_rate_limit_retries_but_unknown_is_terminal(state, monkeypatch):
    with state.session_factory() as session:
        row = enqueue(
            session,
            DeliveryRequest(
                delivery_key="retry",
                conversation=event().conversation,
                content=ChatMessage(text="hello"),
            ),
            user=None,
            now=NOW,
        )
        session.commit()
        key = row.id
    sender = AsyncMock(
        side_effect=MessagingError(
            ErrorCode.RATE_LIMITED, "limited", retry_after_seconds=45
        )
    )
    monkeypatch.setattr(
        state, "adapter", AsyncMock(return_value=SimpleNamespace(deliver=sender))
    )
    await state.send_one(key)
    with state.session_factory() as session:
        row = session.get(MessagingOutbox, key)
        assert row.status == "retry" and row.next_attempt_at == (
            NOW + timedelta(seconds=45)
        ).replace(tzinfo=None)
    sender.side_effect = MessagingError(ErrorCode.DELIVERY_UNKNOWN, "uncertain")
    await state.send_one(key)
    await state.send_one(key)
    assert sender.await_count == 2
    with state.session_factory() as session:
        assert session.get(MessagingOutbox, key).status == "unknown"


def test_media_validation_decodes_and_rejects_signature_only():
    buffer = BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, format="PNG")
    assert checked_image(buffer.getvalue()).mime_type == "image/png"
    with pytest.raises(MessagingError):
        checked_image(b"\x89PNG\r\n\x1a\n" + b"untrusted")


@pytest.mark.asyncio
async def test_media_download_refuses_redirects_and_non_provider_hosts():
    calls = []

    def redirect(request):
        calls.append(request)
        return httpx.Response(
            302, headers={"Location": "https://attacker.example/image"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as http:
        with pytest.raises(MessagingError):
            await download(
                http,
                "https://attacker.example/image",
                token="secret",
                hosts=("files.slack.com",),
            )
        assert not calls
        with pytest.raises(MessagingError):
            await download(
                http,
                "https://files.slack.com/image",
                token="secret",
                hosts=("files.slack.com",),
            )
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_whatsapp_checks_approved_template_shape():
    def respond(request):
        if "phone_numbers" in str(request.url):
            return httpx.Response(200, json={"data": [{"id": "123"}]})
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "name": "reminder",
                        "language": "en_US",
                        "status": "APPROVED",
                        "components": [
                            {
                                "type": "BODY",
                                "text": "You have {{1}} items due. Reply to open your pantry.",
                            }
                        ],
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        adapter = WhatsAppAdapter(
            connection_id="w",
            token="secret",
            config={
                "apiVersion": "v25.0",
                "phoneNumberId": "123",
                "businessAccountId": "999",
                "digestTemplates": [{"name": "reminder", "language": "en_US"}],
            },
            http=http,
            now=NOW,
        )
        assert (await adapter.identity())[0] == "123"
        adapter.config["digestTemplates"][0]["language"] = "fr"
        with pytest.raises(MessagingError):
            await adapter.identity()


@pytest.mark.asyncio
async def test_slack_oauth_state_is_session_bound_and_single_use(state):
    from urllib.parse import parse_qs, urlsplit

    from app.connection_admin import SCOPES
    from app.messaging.identity_service import token_hash
    from app.models import ConnectionAdminSession

    with state.session_factory() as session:
        row = session.get(MessagingConnection, "s")
        row.secret_ciphertext = state.secrets.encrypt(
            {"clientId": "client", "clientSecret": "private", "signingSecret": "signed"}
        )
        session.add(row)
        session.add(
            ConnectionAdminSession(
                token_hash=token_hash("session-one"),
                csrf_hash=token_hash("csrf"),
                authority_hash=token_hash(state.settings.connection_admin_token),
                expires_at=(NOW + timedelta(hours=1)).replace(tzinfo=None),
            )
        )
        session.add(
            ConnectionAdminSession(
                token_hash=token_hash("session-two"),
                csrf_hash=token_hash("csrf"),
                authority_hash=token_hash(state.settings.connection_admin_token),
                expires_at=(NOW + timedelta(hours=1)).replace(tzinfo=None),
            )
        )
        session.commit()
    calls = []

    def exchange(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "token_type": "bot",
                "access_token": "never-return-this",
                "team": {"id": "T1"},
                "scope": SCOPES,
                "bot_user_id": "UBOT",
                "app_id": "APP1",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(exchange)) as http:
        state.http = http
        app = web.Application()
        register_connection_routes(app, state)
        async with TestClient(TestServer(app)) as client:
            headers = {
                "Cookie": "fm_connections=session-one",
                "X-CSRF-Token": "csrf",
                "Origin": "https://food.example",
                "If-Match": "1",
            }
            start = await client.post("/admin/api/connections/s/oauth", headers=headers)
            assert start.status == 200
            query = parse_qs(urlsplit((await start.json())["authorizationUrl"]).query)
            callback = (
                "/admin/connections/oauth/slack/callback?state="
                + query["state"][0]
                + "&code=temporary"
            )
            assert (
                await client.get(
                    callback,
                    headers={"Cookie": "fm_connections=session-two"},
                    allow_redirects=False,
                )
            ).status == 401
            response = await client.get(
                callback, headers=headers, allow_redirects=False
            )
            assert response.status == 302
            assert "never-return-this" not in await response.text()
            assert len(calls) == 1
            assert (
                await client.get(callback, headers=headers, allow_redirects=False)
            ).status == 401
            with state.session_factory() as session:
                row = session.get(MessagingConnection, "s")
                assert not row.enabled and row.revision == 2
                assert (
                    state.secrets.decrypt(row.secret_ciphertext)["botToken"]
                    == "never-return-this"
                )


@pytest.mark.asyncio
async def test_environment_token_cannot_send_as_another_telegram_bot(
    state, monkeypatch
):
    state.settings.telegram_bot_token = "12345:placeholder"
    bot = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(id=99)),
        session=SimpleNamespace(close=AsyncMock()),
    )
    monkeypatch.setattr("app.messaging.runtime.Bot", lambda token: bot)
    connection = MessagingConnection(
        id="legacy-telegram",
        provider="telegram",
        label="Imported",
        credential_source="environment",
        external_account="42",
        created_at=NOW,
    )
    with pytest.raises(MessagingError) as rejected:
        await state.adapter(connection)
    assert rejected.value.code == ErrorCode.CONFLICT
    assert not state.bots
    bot.session.close.assert_awaited_once()
