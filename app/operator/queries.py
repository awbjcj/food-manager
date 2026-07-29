from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, select

from app.billing.entitlement import (
    effective_tier,
    get_or_create_subscription,
    get_or_create_usage,
    roll_period_if_due,
)
from app.models import User


@dataclass(frozen=True)
class HouseholdReport:
    household_id: int
    tier: str
    status: str
    seat_cap: int
    members: tuple[int, ...]
    banned_members: tuple[int, ...]
    receipts_used: int
    actions_used: int
    cost_micros_used: int
    period_end: datetime


def describe_household(
    session: Session, *, telegram_id: int, now: datetime
) -> HouseholdReport | None:
    user = session.get(User, telegram_id)
    if user is None:
        return None
    sub = get_or_create_subscription(session, household_id=user.household_id, now=now)
    roll_period_if_due(session, sub=sub, now=now)
    usage = get_or_create_usage(
        session, household_id=user.household_id, period_start=sub.period_start
    )
    members = session.exec(
        select(User).where(User.household_id == user.household_id)
    ).all()
    report = HouseholdReport(
        user.household_id,
        effective_tier(sub),
        sub.status,
        sub.seat_cap,
        tuple(sorted(member.telegram_id for member in members)),
        tuple(sorted(member.telegram_id for member in members if member.banned)),
        usage.receipts_used,
        usage.actions_used,
        usage.cost_micros_used,
        sub.period_end,
    )
    session.commit()
    return report
