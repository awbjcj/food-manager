"""Deployment owner console and signed messaging ingress. No household authority."""

from __future__ import annotations

import hmac
import json
import secrets
from collections import defaultdict, deque
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from aiohttp import web
from pydantic import ValidationError
from sqlmodel import select

from app.messaging.adapters.http import json_request
from app.messaging.connection_service import (
    ConnectionInput,
    ConnectionPatch,
    create_connection,
    public_connection,
    require_connection,
    set_verified_account,
)
from app.messaging.contracts import (
    ChatMessage,
    ConversationRef,
    DeliveryRequest,
    ErrorCode,
    MessagingError,
)
from app.messaging.identity_service import token_hash
from app.messaging.ingress import (
    MAX_WEBHOOK_BYTES,
    slack_events,
    slack_payload,
    verify_slack,
    verify_whatsapp,
    whatsapp_events,
)
from app.messaging.queue_service import enqueue
from app.models import (
    ConnectionAdminSession,
    ConnectionOAuthState,
    MessagingOutbox,
)
from app.pending_service import utc_naive

COOKIE = "fm_connections"
SCOPES = "chat:write,files:read,im:history,mpim:history,channels:history,groups:history,app_mentions:read,commands"


@web.middleware
async def connection_errors(request, handler):
    if not request.path.startswith(("/admin/", "/hooks/")):
        return await handler(request)
    try:
        response = await handler(request)
    except MessagingError as exc:
        status = {
            ErrorCode.AUTH_FAILED: 401,
            ErrorCode.FORBIDDEN: 403,
            ErrorCode.NOT_FOUND: 404,
            ErrorCode.CONFLICT: 409,
            ErrorCode.RATE_LIMITED: 429,
            ErrorCode.TEMPORARY_FAILURE: 503,
        }.get(exc.code, 400)
        response = web.json_response(
            {"error": {"code": exc.code.value, "message": exc.message}}, status=status
        )
    except (ValidationError, ValueError, TypeError, KeyError):
        response = web.json_response(
            {"error": {"code": "INVALID_INPUT", "message": "Invalid request fields"}},
            status=400,
        )
    except web.HTTPException:
        raise
    except Exception:  # noqa: BLE001 - redact provider and credential details
        # Never expose provider responses, credentials, callback URLs, or tokens.
        response = web.json_response(
            {
                "error": {
                    "code": "TEMPORARY_FAILURE",
                    "message": "Request could not be completed",
                }
            },
            status=503,
        )
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        }
    )
    return response


class ConnectionAdmin:
    def __init__(self, runtime):
        self.runtime = runtime
        self.settings = runtime.settings
        self.attempts = defaultdict(deque)

    def public(self, row):
        return public_connection(
            row, environment_credentials_present=bool(self.settings.telegram_bot_token)
        )

    def throttle(self, key, *, limit=10, seconds=60):
        now = self.runtime.now().timestamp()
        entries = self.attempts[key]
        while entries and entries[0] < now - seconds:
            entries.popleft()
        if len(entries) >= limit:
            raise MessagingError(ErrorCode.RATE_LIMITED, "Try again later")
        entries.append(now)
        if len(self.attempts) > 4096:
            for old in list(self.attempts):
                if not self.attempts[old] or self.attempts[old][-1] < now - 3600:
                    self.attempts.pop(old, None)

    def origin(self, request):
        expected = self.settings.public_base_url.rstrip("/")
        if not expected or request.headers.get("Origin") != expected:
            raise MessagingError(ErrorCode.FORBIDDEN, "Request origin is not allowed")

    def authenticate(self, request, session, *, csrf=True):
        configured = self.settings.connection_admin_token
        row = session.get(
            ConnectionAdminSession, token_hash(request.cookies.get(COOKIE, ""))
        )
        if (
            not configured
            or row is None
            or row.expires_at <= utc_naive(self.runtime.now())
            or not hmac.compare_digest(row.authority_hash, token_hash(configured))
        ):
            raise MessagingError(
                ErrorCode.AUTH_FAILED, "Sign in as the deployment owner"
            )
        if csrf:
            self.origin(request)
            if not hmac.compare_digest(
                row.csrf_hash, token_hash(request.headers.get("X-CSRF-Token", ""))
            ):
                raise MessagingError(ErrorCode.FORBIDDEN, "Invalid request token")
        return row

    async def body(self, request):
        raw = bytearray()
        async for chunk in request.content.iter_chunked(65536):
            raw.extend(chunk)
            if len(raw) > MAX_WEBHOOK_BYTES:
                raise MessagingError(ErrorCode.INVALID_INPUT, "Request is too large")
        return bytes(raw)

    async def page(self, request):
        if (
            not self.settings.connection_admin_token
            or not self.settings.public_base_url
        ):
            raise web.HTTPNotFound()
        response = web.Response(
            text=Path(__file__)
            .with_name("connection_admin.html")
            .read_text(encoding="utf-8"),
            content_type="text/html",
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    async def script(self, request):
        return web.Response(
            text=Path(__file__)
            .with_name("connection_admin.js")
            .read_text(encoding="utf-8"),
            content_type="application/javascript",
        )

    async def login(self, request):
        self.origin(request)
        self.throttle("login:all", limit=50, seconds=300)
        self.throttle("login:" + (request.remote or "unknown"), limit=10, seconds=300)
        value = json.loads(await self.body(request))
        token = value.get("token", "")
        if (
            not self.settings.connection_admin_token
            or not isinstance(token, str)
            or not hmac.compare_digest(token, self.settings.connection_admin_token)
        ):
            raise MessagingError(
                ErrorCode.AUTH_FAILED, "Invalid deployment owner token"
            )
        cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.runtime.session_factory() as session:
            for expired in session.exec(
                select(ConnectionAdminSession).where(
                    ConnectionAdminSession.expires_at <= utc_naive(self.runtime.now())
                )
            ).all():
                session.delete(expired)
            session.add(
                ConnectionAdminSession(
                    token_hash=token_hash(cookie),
                    csrf_hash=token_hash(csrf),
                    authority_hash=token_hash(token),
                    expires_at=utc_naive(self.runtime.now()) + timedelta(hours=8),
                )
            )
            session.commit()
        response = web.json_response({"csrfToken": csrf})
        response.set_cookie(
            COOKIE,
            cookie,
            secure=True,
            httponly=True,
            samesite="Lax",
            path="/admin/",
            max_age=28800,
        )
        return response

    async def logout(self, request):
        with self.runtime.session_factory() as session:
            row = self.authenticate(request, session)
            session.delete(row)
            session.commit()
        response = web.json_response({"ok": True})
        response.del_cookie(COOKIE, path="/admin/")
        return response

    async def connections(self, request):
        from app.models import MessagingConnection

        with self.runtime.session_factory() as session:
            self.authenticate(request, session, csrf=request.method != "GET")
            if request.method == "POST":
                value = ConnectionInput.model_validate_json(await self.body(request))
                row = create_connection(
                    session,
                    value=value,
                    secrets=self.runtime.secrets,
                    now=self.runtime.now(),
                )
                session.commit()
                return web.json_response(self.public(row), status=201)
            cursor = request.query.get("cursor", "")
            query = (
                select(MessagingConnection)
                .where(MessagingConnection.id > cursor)
                .order_by(MessagingConnection.id)
                .limit(51)
            )
            if not self.settings.telegram_bot_token:
                query = query.where(
                    (MessagingConnection.credential_source != "environment")
                    | (MessagingConnection.external_account != None)
                )
            rows = session.exec(query).all()
            return web.json_response(
                {
                    "items": [self.public(r) for r in rows[:50]],
                    "nextCursor": rows[49].id if len(rows) > 50 else None,
                }
            )

    def revision(self, request, row):
        if request.headers.get("If-Match") != str(row.revision):
            raise MessagingError(
                ErrorCode.CONFLICT, "Connection changed; refresh and retry"
            )

    async def patch(self, request):
        with self.runtime.session_factory() as session:
            self.authenticate(request, session)
            row = require_connection(session, request.match_info["id"])
            self.revision(request, row)
            patch = ConnectionPatch.model_validate_json(await self.body(request))
            if any(getattr(patch, key) is None for key in patch.model_fields_set):
                raise MessagingError(
                    ErrorCode.INVALID_INPUT,
                    "Fields cannot be null; omit unchanged fields",
                )
            if patch.label is not None:
                row.label = patch.label
            if patch.credentials is not None or patch.settings is not None:
                if row.credential_source == "environment":
                    raise MessagingError(
                        ErrorCode.CONFLICT,
                        "Update environment credentials in deployment settings",
                    )
                previous = json.loads(row.settings_json)
                # Keep provider-verified metadata separate from editable config.
                fields = {
                    "telegram": {"bootstrapUserId", "mode"},
                    "slack": {"bootstrapUserId", "mode"},
                    "whatsapp": {
                        "bootstrapUserId",
                        "businessAccountId",
                        "phoneNumberId",
                        "apiVersion",
                        "digestTemplates",
                    },
                }[row.provider]
                config = (
                    patch.settings
                    if patch.settings is not None
                    else {k: v for k, v in previous.items() if k in fields}
                )
                credentials = self.runtime.credentials(row)
                original_client_id = credentials.get("clientId")
                if patch.credentials is not None:
                    credentials.update(
                        {k: v.get_secret_value() for k, v in patch.credentials.items()}
                    )
                value = ConnectionInput.model_validate(
                    {
                        "provider": row.provider,
                        "label": row.label,
                        "settings": config,
                        "credentials": credentials,
                    }
                )
                if (
                    row.external_account
                    and row.provider == "slack"
                    and credentials.get("clientId") != original_client_id
                ):
                    raise MessagingError(
                        ErrorCode.CONFLICT,
                        "Create a new connection for another Slack app",
                    )
                if (
                    row.external_account
                    and row.provider == "whatsapp"
                    and value.settings["phoneNumberId"] != row.external_account
                ):
                    raise MessagingError(
                        ErrorCode.CONFLICT,
                        "Create a new connection for another phone number",
                    )
                if (
                    row.external_account
                    and row.provider == "whatsapp"
                    and value.settings["businessAccountId"]
                    != previous["businessAccountId"]
                ):
                    raise MessagingError(
                        ErrorCode.CONFLICT,
                        "Create a new connection for another business account",
                    )
                row.settings_json = json.dumps(
                    {
                        **value.settings,
                        **{
                            k: previous[k]
                            for k in ("appId", "botUserId", "botId")
                            if k in previous
                        },
                    }
                )
                row.secret_ciphertext = self.runtime.secrets.encrypt(credentials)
                row.enabled, row.inbound_verified, row.state = (
                    False,
                    False,
                    "unconfigured",
                )
            if patch.enabled is not None:
                if patch.enabled and (
                    row.state != "ready"
                    or not row.inbound_verified
                    or not row.external_account
                ):
                    raise MessagingError(
                        ErrorCode.CONFLICT,
                        "Validate credentials and send a message from the bootstrap account first",
                    )
                row.enabled = patch.enabled
            row.revision += 1
            session.add(row)
            session.commit()
            return web.json_response(self.public(row))

    async def check(self, request):
        with self.runtime.session_factory() as session:
            self.authenticate(request, session)
            row = require_connection(session, request.match_info["id"])
            self.revision(request, row)
        self.throttle("check:" + row.id, limit=5)
        checks = await self.runtime.check_connection(row.id)
        with self.runtime.session_factory() as session:
            row = require_connection(session, row.id)
            return web.json_response({"connection": self.public(row), "checks": checks})

    async def instructions(self, request):
        with self.runtime.session_factory() as session:
            self.authenticate(request, session, csrf=False)
            row = require_connection(session, request.match_info["id"])
            base = self.settings.public_base_url.rstrip("/")
            hook = f"{base}/hooks/{row.provider}/{row.id}"
            result: dict[str, Any] = {
                "webhookUrl": hook,
                "oauthRedirectUrl": f"{base}/admin/connections/oauth/slack/callback",
            }
            if row.provider == "slack":
                result["manifest"] = {
                    "display_information": {"name": "Food Manager"},
                    "features": {
                        "bot_user": {
                            "display_name": "Food Manager",
                            "always_online": False,
                        },
                        "slash_commands": [
                            {
                                "command": "/food",
                                "url": hook,
                                "description": "Manage your pantry",
                                "should_escape": False,
                            }
                        ],
                    },
                    "oauth_config": {
                        "redirect_urls": [result["oauthRedirectUrl"]],
                        "scopes": {"bot": SCOPES.split(",")},
                    },
                    "settings": {
                        "event_subscriptions": {
                            "request_url": hook,
                            "bot_events": [
                                "message.im",
                                "message.mpim",
                                "app_mention",
                                "message.channels",
                                "message.groups",
                            ],
                        },
                        "interactivity": {"is_enabled": True, "request_url": hook},
                        "socket_mode_enabled": False,
                    },
                }
            return web.json_response(result)

    async def oauth_start(self, request):
        with self.runtime.session_factory() as session:
            admin = self.authenticate(request, session)
            row = require_connection(session, request.match_info["id"])
            self.revision(request, row)
            if row.provider != "slack" or row.credential_source != "managed":
                raise MessagingError(
                    ErrorCode.INVALID_INPUT,
                    "OAuth is only available for managed Slack connections",
                )
            creds = self.runtime.credentials(row)
            state = secrets.token_urlsafe(32)
            session.add(
                ConnectionOAuthState(
                    token_hash=token_hash(state),
                    session_hash=admin.token_hash,
                    connection_id=row.id,
                    revision=row.revision,
                    expires_at=utc_naive(self.runtime.now()) + timedelta(minutes=10),
                )
            )
            session.commit()
            url = "https://slack.com/oauth/v2/authorize?" + urlencode(
                {
                    "client_id": creds["clientId"],
                    "scope": SCOPES,
                    "state": state,
                    "redirect_uri": self.settings.public_base_url.rstrip("/")
                    + "/admin/connections/oauth/slack/callback",
                }
            )
            return web.json_response({"authorizationUrl": url})

    async def oauth_callback(self, request):
        with self.runtime.session_factory() as session:
            admin = self.authenticate(request, session, csrf=False)
            state = session.get(
                ConnectionOAuthState, token_hash(request.query.get("state", ""))
            )
            if (
                state is None
                or state.session_hash != admin.token_hash
                or state.expires_at <= utc_naive(self.runtime.now())
            ):
                raise MessagingError(
                    ErrorCode.AUTH_FAILED,
                    "OAuth state expired or belongs to another session",
                )
            row = require_connection(session, state.connection_id)
            revision = state.revision
            session.delete(state)
            session.commit()  # Consume once, including denied or failed exchanges.
            if revision != row.revision:
                raise MessagingError(
                    ErrorCode.CONFLICT, "Connection changed during installation"
                )
            code = request.query.get("code")
            if request.query.get("error") or not code or len(code) > 4096:
                raise MessagingError(
                    ErrorCode.INVALID_INPUT, "Slack installation was not completed"
                )
            creds = self.runtime.credentials(row)
            key = row.id
        result = await json_request(
            self.runtime.http,
            "POST",
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": creds["clientId"],
                "client_secret": creds["clientSecret"],
                "code": code,
                "redirect_uri": self.settings.public_base_url.rstrip("/")
                + "/admin/connections/oauth/slack/callback",
            },
        )
        if (
            result.get("ok") is not True
            or result.get("token_type") != "bot"
            or not result.get("access_token")
            or result.get("is_enterprise_install")
            or not result.get("team", {}).get("id")
            or not set(SCOPES.split(",")).issubset(
                set(result.get("scope", "").split(","))
            )
        ):
            raise MessagingError(
                ErrorCode.AUTH_FAILED,
                "Slack did not grant the required workspace bot permissions",
            )
        with self.runtime.session_factory() as session:
            row = require_connection(session, key)
            if row.revision != revision:
                raise MessagingError(
                    ErrorCode.CONFLICT, "Connection changed during installation"
                )
            set_verified_account(
                session,
                row=row,
                external_account=result["team"]["id"],
                now=self.runtime.now(),
            )
            creds["botToken"] = result["access_token"]
            row.secret_ciphertext = self.runtime.secrets.encrypt(creds)
            config = json.loads(row.settings_json)
            config.update(
                {
                    "botUserId": result.get("bot_user_id", ""),
                    "appId": result.get("app_id", ""),
                }
            )
            row.settings_json, row.enabled, row.revision = (
                json.dumps(config),
                False,
                row.revision + 1,
            )
            session.add(row)
            session.commit()
        raise web.HTTPFound("/admin/connections")

    async def test_send(self, request):
        with self.runtime.session_factory() as session:
            self.authenticate(request, session)
            row = require_connection(session, request.match_info["id"])
            self.revision(request, row)
            config = json.loads(row.settings_json)
            if not row.inbound_verified or "testConversation" not in config:
                raise MessagingError(
                    ErrorCode.CONFLICT,
                    "Send a direct message from the bootstrap account first",
                )
            self.throttle("test:" + row.id, limit=3)
            key = request.headers.get("Idempotency-Key", "")
            if not 16 <= len(key) <= 128:
                raise MessagingError(
                    ErrorCode.INVALID_INPUT,
                    "Provide a unique Idempotency-Key of 16 to 128 characters",
                )
            delivery = enqueue(
                session,
                DeliveryRequest(
                    delivery_key=f"admin-test:{row.id}:{key}",
                    conversation=ConversationRef.model_validate(
                        config["testConversation"]
                    ),
                    content=ChatMessage(text="Food Manager connection test succeeded."),
                ),
                user=None,
                now=self.runtime.now(),
            )
            session.commit()
            return web.json_response(
                {"deliveryId": delivery.id, "status": delivery.status}, status=202
            )

    async def delivery(self, request):
        with self.runtime.session_factory() as session:
            self.authenticate(request, session, csrf=False)
            row = session.get(MessagingOutbox, request.match_info["delivery"])
            if row is None or row.connection_id != request.match_info["id"]:
                raise MessagingError(ErrorCode.NOT_FOUND, "Delivery not found")
            return web.json_response(
                {
                    "deliveryId": row.id,
                    "status": row.status,
                    "errorCode": row.error_code,
                }
            )

    async def hook(self, request):
        with self.runtime.session_factory() as session:
            row = require_connection(session, request.match_info["id"])
            if row.provider != request.match_info["provider"] or row.provider not in {
                "slack",
                "whatsapp",
            }:
                raise MessagingError(ErrorCode.NOT_FOUND, "Webhook not found")
            credentials, config = (
                self.runtime.credentials(row),
                json.loads(row.settings_json),
            )
        if request.method == "GET":
            if (
                row.provider != "whatsapp"
                or request.query.get("hub.mode") != "subscribe"
                or not hmac.compare_digest(
                    request.query.get("hub.verify_token", ""),
                    credentials["verifyToken"],
                )
            ):
                raise MessagingError(
                    ErrorCode.AUTH_FAILED, "Invalid verification request"
                )
            challenge = request.query.get("hub.challenge", "")
            if not challenge.isdigit() or len(challenge) > 128:
                raise MessagingError(
                    ErrorCode.INVALID_INPUT, "Invalid verification challenge"
                )
            return web.Response(text=challenge)
        raw = await self.body(request)
        now = self.runtime.now()
        if row.provider == "slack":
            verify_slack(
                raw, request.headers, secret=credentials["signingSecret"], now=now
            )
            payload = slack_payload(raw, request.content_type)
            if payload.get("type") == "url_verification":
                return web.json_response({"challenge": payload["challenge"]})
            if (
                config.get("appId")
                and payload.get("api_app_id", config["appId"]) != config["appId"]
            ):
                raise MessagingError(ErrorCode.FORBIDDEN, "Wrong Slack app")
            if not row.external_account:
                raise MessagingError(ErrorCode.CONFLICT, "Install Slack first")
            events = slack_events(
                payload,
                connection_id=row.id,
                account=row.external_account,
                now=now,
                bot_user_id=config.get("botUserId", ""),
            )
        else:
            verify_whatsapp(raw, request.headers, secret=credentials["appSecret"])
            events = whatsapp_events(
                json.loads(raw),
                connection_id=row.id,
                business_id=config["businessAccountId"],
                phone_id=config["phoneNumberId"],
                now=now,
            )
        # Admission completes before acknowledging provider retries. No LLM work
        # runs in the webhook request. SQLite transactions are deliberately short.
        self.runtime.admit(events)
        if row.provider == "whatsapp":
            self.runtime.delivery_statuses(row.id, json.loads(raw))
        return web.json_response({"ok": True})


def register_connection_routes(app, runtime):
    admin = ConnectionAdmin(runtime)
    app.middlewares.insert(0, connection_errors)
    app.router.add_get("/admin/connections", admin.page)
    app.router.add_get("/admin/connections.js", admin.script)
    app.router.add_post("/admin/api/session", admin.login)
    app.router.add_delete("/admin/api/session", admin.logout)
    app.router.add_get("/admin/api/connections", admin.connections)
    app.router.add_post("/admin/api/connections", admin.connections)
    app.router.add_patch("/admin/api/connections/{id}", admin.patch)
    app.router.add_post("/admin/api/connections/{id}/check", admin.check)
    app.router.add_get("/admin/api/connections/{id}/instructions", admin.instructions)
    app.router.add_post("/admin/api/connections/{id}/oauth", admin.oauth_start)
    app.router.add_get("/admin/connections/oauth/slack/callback", admin.oauth_callback)
    app.router.add_post("/admin/api/connections/{id}/test", admin.test_send)
    app.router.add_get(
        "/admin/api/connections/{id}/deliveries/{delivery}", admin.delivery
    )
    app.router.add_get("/hooks/{provider}/{id}", admin.hook)
    app.router.add_post("/hooks/{provider}/{id}", admin.hook)
