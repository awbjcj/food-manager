"""Operator-only household tier administration.

The public interface targets a Telegram user because that is the identifier an
operator has during support. Entitlements remain household-scoped internally,
matching quota pooling everywhere else in the application.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel import Session

from app.billing.entitlement import get_or_create_subscription, utc_naive
from app.billing.ledger import record_payment
from app.billing.plans import PERIOD_DAYS, TIERS, PlanTier, limits_for
from app.models import User


class TierAdminError(ValueError):
    """Base error safe for an operator handler to translate into a reply."""


class UserNotFound(TierAdminError):
    pass


class PaidSubscriptionConflict(TierAdminError):
    pass


@dataclass(frozen=True)
class TierChange:
    telegram_id: int
    household_id: int
    previous_tier: str
    tier: PlanTier
    changed: bool


def set_user_tier(
    session: Session,
    *,
    telegram_id: int,
    tier: PlanTier,
    operator_telegram_id: int,
    now: datetime,
    duration_days: int = PERIOD_DAYS,
) -> TierChange:
    """Set the tier for the target user's whole household and audit the change.

    ``unlimited`` is persistent but its usage counters still roll every 30 days.
    Operator-comped ``family`` access lasts ``duration_days``. A live paid
    subscription must be refunded/cancelled through its payment rail first so a
    database-only tier change cannot leave the customer being charged.
    """
    if tier not in TIERS:
        raise TierAdminError(f"unknown tier {tier}")
    if duration_days < 1:
        raise TierAdminError("duration_days must be positive")

    user = session.get(User, telegram_id)
    if user is None:
        raise UserNotFound(f"no user {telegram_id}")
    moment = utc_naive(now)
    sub = get_or_create_subscription(
        session, household_id=user.household_id, now=moment
    )
    previous_tier = sub.tier if sub.status == "active" else "free"
    if previous_tier == tier:
        return TierChange(telegram_id, user.household_id, previous_tier, tier, False)
    if sub.status == "active" and sub.telegram_charge_id is not None:
        raise PaidSubscriptionConflict(
            "active paid subscription; refund or cancel it before changing the tier"
        )

    sub.tier = tier
    sub.status = "active"
    sub.telegram_charge_id = None
    sub.payer_telegram_id = None
    sub.cancel_at_period_end = False
    sub.period_start = moment
    sub.period_end = moment + timedelta(
        days=duration_days if tier == "family" else PERIOD_DAYS
    )
    sub.seat_cap = limits_for(tier).seats
    sub.updated_at = moment
    session.add(sub)
    record_payment(
        session,
        household_id=user.household_id,
        charge_id=f"tier:{uuid.uuid4()}",
        kind="grant",
        sku=f"operator_tier_{tier}",
        stars=0,
        payer_telegram_id=operator_telegram_id,
        payload_json=json.dumps(
            {
                "target_telegram_id": telegram_id,
                "previous_tier": previous_tier,
                "tier": tier,
                "duration_days": duration_days if tier == "family" else None,
            },
            separators=(",", ":"),
        ),
        now=moment,
    )
    session.commit()
    return TierChange(telegram_id, user.household_id, previous_tier, tier, True)
