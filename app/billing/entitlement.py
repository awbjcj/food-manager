from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from app.billing.plans import PERIOD_DAYS, Sku, limits_for
from app.models import QuotaUsage, Subscription

PERIOD = timedelta(days=PERIOD_DAYS)


def utc_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def effective_tier(sub: Subscription) -> str:
    return sub.tier if sub.status == "active" else "free"


def get_or_create_subscription(
    session: Session, *, household_id: int, now: datetime
) -> Subscription:
    sub = session.get(Subscription, household_id)
    if sub is not None:
        return sub
    moment = utc_naive(now)
    sub = Subscription(
        household_id=household_id,
        period_start=moment,
        period_end=moment + PERIOD,
        created_at=moment,
        updated_at=moment,
    )
    session.add(sub)
    session.flush()
    return sub


def get_or_create_usage(
    session: Session, *, household_id: int, period_start: datetime
) -> QuotaUsage:
    start = utc_naive(period_start)
    usage = session.get(QuotaUsage, (household_id, start))
    if usage is not None:
        return usage
    usage = QuotaUsage(household_id=household_id, period_start=start)
    session.add(usage)
    session.flush()
    return usage


def roll_period_if_due(
    session: Session, *, sub: Subscription, now: datetime
) -> Subscription:
    moment = utc_naive(now)
    end = utc_naive(sub.period_end)
    if moment < end:
        return sub
    if sub.tier != "free" and sub.telegram_charge_id is not None:
        sub.status = "expired"
        sub.seat_cap = limits_for("free").seats
        skipped = (moment - end) // PERIOD
        sub.period_start = end + PERIOD * skipped
        sub.period_end = sub.period_start + PERIOD
    else:
        skipped = (moment - end) // PERIOD
        sub.period_start = end + PERIOD * skipped
        sub.period_end = sub.period_start + PERIOD
    sub.updated_at = moment
    session.add(sub)
    session.flush()
    return sub


def apply_subscription(
    session: Session,
    *,
    household_id: int,
    sku: Sku,
    charge_id: str,
    payer_telegram_id: int,
    expires_at: datetime,
    now: datetime,
) -> Subscription:
    moment = utc_naive(now)
    sub = get_or_create_subscription(session, household_id=household_id, now=moment)
    was_paid = sub.telegram_charge_id is not None
    tier = sku.grants_tier or "free"
    sub.tier = tier
    sub.status = "active"
    if not was_paid:
        sub.telegram_charge_id = charge_id
    sub.payer_telegram_id = payer_telegram_id
    sub.period_end = utc_naive(expires_at)
    sub.period_start = sub.period_end - PERIOD
    sub.seat_cap = limits_for(tier).seats
    sub.updated_at = moment
    session.add(sub)
    session.flush()
    return sub


def apply_topup(
    session: Session, *, household_id: int, sku: Sku, now: datetime
) -> QuotaUsage:
    sub = get_or_create_subscription(session, household_id=household_id, now=now)
    roll_period_if_due(session, sub=sub, now=now)
    usage = get_or_create_usage(
        session, household_id=household_id, period_start=sub.period_start
    )
    usage.receipts_granted += sku.grants_receipts
    usage.actions_granted += sku.grants_actions
    usage.cost_micros_granted += sku.grants_cost_micros
    session.add(usage)
    session.flush()
    return usage


def apply_refund(session: Session, *, household_id: int, now: datetime) -> Subscription:
    sub = get_or_create_subscription(session, household_id=household_id, now=now)
    sub.status = "cancelled"
    sub.seat_cap = limits_for("free").seats
    sub.updated_at = utc_naive(now)
    session.add(sub)
    session.flush()
    return sub


def revoke_topup(
    session: Session, *, household_id: int, sku: Sku, now: datetime
) -> QuotaUsage:
    """Remove one top-up's remaining period grants without changing the tier."""
    sub = get_or_create_subscription(session, household_id=household_id, now=now)
    roll_period_if_due(session, sub=sub, now=now)
    usage = get_or_create_usage(
        session, household_id=household_id, period_start=sub.period_start
    )
    usage.receipts_granted = max(0, usage.receipts_granted - sku.grants_receipts)
    usage.actions_granted = max(0, usage.actions_granted - sku.grants_actions)
    usage.cost_micros_granted = max(
        0, usage.cost_micros_granted - sku.grants_cost_micros
    )
    session.add(usage)
    session.flush()
    return usage
