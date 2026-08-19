"""Telegram Mini App HTTP surface for account and subscription management."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import web
from sqlmodel import Session, func, select

from app.billing.entitlement import effective_tier, get_or_create_subscription
from app.billing.meter import snapshot
from app.billing.plans import SKUS, TIERS, sku_for
from app.i18n import LANGS
from app.models import Household, Subscription, User
from app.webapp_auth import MiniAppAuthError, MiniAppIdentity, validate_init_data

log = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]


class MiniAppApi:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        bot_token: str,
        payments,
        billing_enabled: bool,
        available_providers: Sequence[str],
        bot_username: str | None,
    ) -> None:
        self.session_factory = session_factory
        self.bot_token = bot_token
        self.payments = payments
        self.billing_enabled = billing_enabled
        self.available_providers = tuple(available_providers)
        self.bot_username = bot_username

    def identity(self, request: web.Request) -> MiniAppIdentity:
        header = request.headers.get("Authorization", "")
        raw = header[4:] if header.lower().startswith("tma ") else ""
        if not raw:
            raw = request.headers.get("X-Telegram-Init-Data", "")
        return validate_init_data(raw, bot_token=self.bot_token)

    def authorized_user(
        self, session: Session, identity: MiniAppIdentity
    ) -> User:
        user = session.get(User, identity.telegram_id)
        if user is None or user.banned:
            raise MiniAppAuthError("user is not authorized")
        return user

    async def account(self, request: web.Request) -> web.Response:
        identity = self.identity(request)
        with self.session_factory() as session:
            user = self.authorized_user(session, identity)
            household = session.get(Household, user.household_id)
            if household is None:
                raise web.HTTPNotFound(text="household not found")
            now = datetime.now(ZoneInfo(user.tz))
            quota = snapshot(session, household_id=user.household_id, now=now)
            sub = get_or_create_subscription(
                session, household_id=user.household_id, now=now
            )
            members = session.exec(
                select(func.count()).select_from(User).where(
                    User.household_id == user.household_id
                )
            ).one()
            session.commit()
            return web.json_response(
                {
                    "user": {
                        "telegramId": user.telegram_id,
                        "name": identity.display_name or f"User {user.telegram_id}",
                        "role": user.role,
                        "language": user.lang,
                        "timeZone": user.tz,
                        "digestHour": user.digest_hour,
                        "provider": user.llm_provider,
                    },
                    "household": {
                        "name": household.name,
                        "members": members,
                        "seatCap": sub.seat_cap,
                    },
                    "plan": {
                        "tier": effective_tier(sub),
                        "status": sub.status,
                        "periodEnd": sub.period_end.isoformat(),
                        "renews": bool(
                            sub.telegram_charge_id
                            and not sub.cancel_at_period_end
                            and sub.status == "active"
                        ),
                        "canManage": user.role == "owner",
                    },
                    "quota": {
                        "receiptsUsed": quota.receipts_used,
                        "receiptsLimit": quota.receipts_limit,
                        "actionsUsed": quota.actions_used,
                        "actionsLimit": quota.actions_limit,
                    },
                    "plans": [
                        {
                            "code": "free",
                            "title": "Free",
                            "stars": 0,
                            "receipts": TIERS["free"].receipts,
                            "actions": TIERS["free"].actions,
                            "seats": TIERS["free"].seats,
                            "kind": "tier",
                        },
                        *[
                            {
                                "code": sku.code,
                                "title": sku.title,
                                "stars": sku.stars,
                                "description": sku.description,
                                "kind": sku.kind,
                                "receipts": (
                                    TIERS[sku.grants_tier].receipts
                                    if sku.grants_tier
                                    else sku.grants_receipts
                                ),
                                "actions": (
                                    TIERS[sku.grants_tier].actions
                                    if sku.grants_tier
                                    else sku.grants_actions
                                ),
                                "seats": (
                                    TIERS[sku.grants_tier].seats
                                    if sku.grants_tier
                                    else None
                                ),
                            }
                            for sku in SKUS.values()
                        ],
                    ],
                    "availableProviders": list(self.available_providers),
                    "botUsername": self.bot_username,
                    "billingEnabled": self.billing_enabled,
                }
            )

    async def update_account(self, request: web.Request) -> web.Response:
        identity = self.identity(request)
        body = await request.json()
        with self.session_factory() as session:
            user = self.authorized_user(session, identity)
            household = session.get(Household, user.household_id)
            if household is None:
                raise web.HTTPNotFound(text="household not found")
            name = str(body.get("householdName", "")).strip()
            if user.role == "owner" and name:
                if len(name) > 80:
                    raise web.HTTPBadRequest(text="household name is too long")
                household.name = name
                session.add(household)
            try:
                hour = int(body["digestHour"])
                if not 0 <= hour <= 23:
                    raise ValueError
            except (KeyError, TypeError, ValueError) as exc:
                raise web.HTTPBadRequest(text="invalid digest hour") from exc
            time_zone = str(body.get("timeZone", ""))
            try:
                ZoneInfo(time_zone)
            except ZoneInfoNotFoundError as exc:
                raise web.HTTPBadRequest(text="invalid time zone") from exc
            language = str(body.get("language", ""))
            provider = str(body.get("provider", ""))
            if language not in LANGS:
                raise web.HTTPBadRequest(text="invalid language")
            if provider not in self.available_providers:
                raise web.HTTPBadRequest(text="provider is not available")
            user.digest_hour = hour
            user.tz = time_zone
            user.lang = language
            user.llm_provider = provider
            session.add(user)
            session.commit()
        return web.json_response({"ok": True})

    async def checkout(self, request: web.Request) -> web.Response:
        identity = self.identity(request)
        if not self.billing_enabled or self.payments is None:
            raise web.HTTPServiceUnavailable(text="payments are unavailable")
        body = await request.json()
        sku = sku_for(str(body.get("sku", "")))
        if sku is None:
            raise web.HTTPBadRequest(text="unknown plan")
        with self.session_factory() as session:
            user = self.authorized_user(session, identity)
            household_id = user.household_id
            if sku.kind == "subscription":
                sub = session.get(Subscription, household_id)
                if sub and effective_tier(sub) == "family" and sub.telegram_charge_id:
                    raise web.HTTPConflict(text="household already subscribes")
        url = await self.payments.create_checkout(
            sku=sku, household_id=household_id
        )
        return web.json_response({"invoiceUrl": url})

    async def cancel_subscription(self, request: web.Request) -> web.Response:
        identity = self.identity(request)
        if not self.billing_enabled or self.payments is None:
            raise web.HTTPServiceUnavailable(text="payments are unavailable")
        with self.session_factory() as session:
            user = self.authorized_user(session, identity)
            if user.role != "owner":
                raise web.HTTPForbidden(text="only the household owner can cancel")
            sub = session.get(Subscription, user.household_id)
            if (
                sub is None
                or not sub.telegram_charge_id
                or not sub.payer_telegram_id
                or effective_tier(sub) != "family"
            ):
                raise web.HTTPConflict(text="no active subscription")
            ok = await self.payments.cancel_subscription(
                user_id=sub.payer_telegram_id,
                charge_id=sub.telegram_charge_id,
            )
            if not ok:
                raise web.HTTPBadGateway(text="Telegram did not cancel renewal")
            sub.cancel_at_period_end = True
            sub.updated_at = datetime.now(UTC).replace(tzinfo=None)
            session.add(sub)
            session.commit()
        return web.json_response({"ok": True})


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except MiniAppAuthError as exc:
        return web.json_response({"error": str(exc)}, status=401)
    except web.HTTPException as exc:
        return web.json_response({"error": exc.text or exc.reason}, status=exc.status)
    except Exception:
        log.exception("mini_app_request_failed", extra={"path": request.path})
        return web.json_response({"error": "request failed"}, status=500)


def build_web_app(
    *,
    session_factory: SessionFactory,
    bot_token: str,
    payments,
    billing_enabled: bool,
    available_providers: Sequence[str],
    bot_username: str | None,
    static_dir: Path,
) -> web.Application:
    api = MiniAppApi(
        session_factory=session_factory,
        bot_token=bot_token,
        payments=payments,
        billing_enabled=billing_enabled,
        available_providers=available_providers,
        bot_username=bot_username,
    )
    app = web.Application(middlewares=[error_middleware])

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    app.router.add_get("/healthz", health)
    app.router.add_get("/api/account", api.account)
    app.router.add_patch("/api/account", api.update_account)
    app.router.add_post("/api/checkout", api.checkout)
    app.router.add_post("/api/subscription/cancel", api.cancel_subscription)
    if static_dir.exists():
        assets = static_dir / "assets"
        if assets.exists():
            app.router.add_static("/assets/", assets)

        async def spa(_request: web.Request) -> web.FileResponse:
            return web.FileResponse(static_dir / "index.html")

        app.router.add_get("/", spa)
        app.router.add_get("/{tail:(?!api/|healthz).+}", spa)
    return app
