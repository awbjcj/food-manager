from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from app import handler_support
from app.billing.entitlement import (
    apply_subscription,
    apply_topup,
    effective_tier,
    get_or_create_subscription,
    utc_naive,
)
from app.billing.ledger import record_payment
from app.billing.meter import snapshot
from app.billing.payment import parse_payload
from app.billing.plans import SKUS, sku_for
from app.billing.render import render_quota
from app.handler_support import NowProvider
from app.i18n import t
from app.models import Household, Subscription, User
from app.renderer import CallbackButton
from app.telegram_ui import to_aiogram_keyboard

log = logging.getLogger(__name__)
_request = handler_support.request
_noop_user_created = handler_support.noop_user_created


async def handle_quota(
    msg,
    *,
    session_factory,
    now_provider: NowProvider,
    on_user_created: Callable[[User], None] = _noop_user_created,
    hosted_features_enabled: bool = True,
):
    if not hosted_features_enabled:
        await msg.answer(t("hosted_only", "en"))
        return
    async with _request(
        msg, session_factory=session_factory, on_user_created=on_user_created
    ) as ctx:
        if ctx is None:
            return
        now = now_provider(ctx.user.tz)
        card = snapshot(ctx.session, household_id=ctx.user.household_id, now=now)
        days = max(0, (card.period_end - utc_naive(now)).days)
        await msg.answer(render_quota(card, days_left=days, lang=ctx.user.lang))


async def handle_buy(
    msg,
    *,
    session_factory,
    now_provider: NowProvider,
    payments=None,
    on_user_created: Callable[[User], None] = _noop_user_created,
    hosted_features_enabled: bool = True,
):
    if not hosted_features_enabled:
        await msg.answer(t("hosted_only", "en"))
        return
    del now_provider
    async with _request(
        msg, session_factory=session_factory, on_user_created=on_user_created
    ) as ctx:
        if ctx is None:
            return
        if payments is None:
            await msg.answer(t("billing.payments_unavailable", ctx.user.lang))
            return
        try:
            rows = []
            for sku in SKUS.values():
                url = await payments.create_checkout(
                    sku=sku, household_id=ctx.user.household_id
                )
                title = t(f"billing.sku.{sku.code}", ctx.user.lang)
                rows.append(
                    [CallbackButton(text=f"{title} - {sku.stars} Stars", url=url)]
                )
            await msg.answer(
                t("billing.buy_choose", ctx.user.lang),
                reply_markup=to_aiogram_keyboard(rows),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "buy_checkout_failed", extra={"error_class": type(exc).__name__}
            )
            await msg.answer(t("billing.payments_unavailable", ctx.user.lang))


def _validate_checkout(
    query,
    session,
    *,
    payer_id: int | None = None,
    allow_existing_subscription: bool = False,
):
    sku_code, household_id = parse_payload(query.invoice_payload)
    sku = sku_for(sku_code)
    if sku is None or query.currency != "XTR" or query.total_amount != sku.stars:
        raise ValueError("invoice does not match SKU")
    payer = session.get(User, payer_id if payer_id is not None else query.from_user.id)
    if payer is None or payer.banned or payer.household_id != household_id:
        raise ValueError("payer is not eligible for household")
    if session.get(Household, household_id) is None:
        raise ValueError("unknown household")
    if sku.kind == "subscription":
        sub = session.get(Subscription, household_id)
        if (
            not allow_existing_subscription
            and sub is not None
            and effective_tier(sub) == "family"
            and sub.telegram_charge_id
        ):
            raise ValueError("already subscribed")
    return sku, household_id, payer


async def handle_pre_checkout(query, *, session_factory):
    lang = "en"
    try:
        with session_factory() as session:
            _sku, _household_id, payer = _validate_checkout(query, session)
            lang = payer.lang
    except Exception as exc:  # noqa: BLE001
        log.warning("pre_checkout_declined", extra={"error_class": type(exc).__name__})
        await query.answer(
            ok=False, error_message=t("billing.purchase_unavailable", lang)
        )
        return
    await query.answer(ok=True)


async def handle_successful_payment(msg, *, session_factory, now_provider: NowProvider):
    payment = msg.successful_payment
    try:
        with session_factory() as session:
            sku, household_id, payer = _validate_checkout(
                payment,
                session,
                payer_id=msg.from_user.id,
                allow_existing_subscription=True,
            )
            recurring = bool(payment.is_recurring)
            if recurring != (sku.kind == "subscription"):
                raise ValueError("recurrence does not match SKU")
            expires = None
            if recurring:
                if payment.subscription_expiration_date is None:
                    raise ValueError("subscription expiration is required")
                expires = datetime.fromtimestamp(
                    payment.subscription_expiration_date, UTC
                )
            now = now_provider(payer.tz)
            if expires is not None and expires <= now:
                raise ValueError("subscription expiration must be in the future")
            if not record_payment(
                session,
                household_id=household_id,
                charge_id=payment.telegram_payment_charge_id,
                kind=sku.kind,
                sku=sku.code,
                stars=payment.total_amount,
                payer_telegram_id=payer.telegram_id,
                payload_json=payment.invoice_payload,
                now=now,
            ):
                session.rollback()
                return
            if recurring:
                assert expires is not None
                apply_subscription(
                    session,
                    household_id=household_id,
                    sku=sku,
                    charge_id=payment.telegram_payment_charge_id,
                    payer_telegram_id=payer.telegram_id,
                    expires_at=expires,
                    now=now,
                )
                response = t("billing.subscription_active", payer.lang)
            else:
                apply_topup(session, household_id=household_id, sku=sku, now=now)
                response = t("billing.topup_active", payer.lang)
            session.commit()
    except IntegrityError:
        return
    except Exception as exc:  # noqa: BLE001
        log.error(
            "successful_payment_failed",
            extra={"error_class": type(exc).__name__, "error": str(exc)},
        )
        return
    await msg.answer(response)


async def handle_billing(
    msg,
    *,
    session_factory,
    now_provider: NowProvider,
    on_user_created: Callable[[User], None] = _noop_user_created,
    hosted_features_enabled: bool = True,
) -> None:
    if not hosted_features_enabled:
        await msg.answer(t("hosted_only", "en"))
        return
    async with _request(
        msg, session_factory=session_factory, on_user_created=on_user_created
    ) as ctx:
        if ctx is None:
            return
        now = now_provider(ctx.user.tz)
        sub = get_or_create_subscription(
            ctx.session, household_id=ctx.user.household_id, now=now
        )
        if effective_tier(sub) == "free":
            text = t("billing.plan_free", ctx.user.lang)
        else:
            days = max(0, (sub.period_end - utc_naive(now)).days)
            key = (
                "billing.plan_family_cancelled"
                if sub.cancel_at_period_end
                else "billing.plan_family"
            )
            text = t(key, ctx.user.lang, days=days)
        ctx.session.commit()
        await msg.answer(text)


COMMANDS = (
    (
        "quota",
        handle_quota,
        (
            "session_factory",
            "now_provider",
            "on_user_created",
            "hosted_features_enabled",
        ),
    ),
    (
        "buy",
        handle_buy,
        (
            "session_factory",
            "now_provider",
            "payments",
            "on_user_created",
            "hosted_features_enabled",
        ),
    ),
    (
        "billing",
        handle_billing,
        (
            "session_factory",
            "now_provider",
            "on_user_created",
            "hosted_features_enabled",
        ),
    ),
)
