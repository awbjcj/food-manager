from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session

from app.billing.entitlement import (
    effective_tier,
    get_or_create_subscription,
    get_or_create_usage,
    roll_period_if_due,
)
from app.billing.plans import OpClass, limits_for, units_for

BILLING_ENABLED = False
METERING_ENABLED = True


@dataclass(frozen=True)
class QuotaSnapshot:
    receipts_used: int
    receipts_limit: int
    actions_used: int
    actions_limit: int
    per_op: Mapping[str, int]
    period_end: datetime
    tier: str


@dataclass(frozen=True)
class Admission:
    allowed: bool
    reason: str
    degrade: bool
    snapshot: QuotaSnapshot


def _resolve(session: Session, household_id: int, now: datetime):
    sub = get_or_create_subscription(session, household_id=household_id, now=now)
    roll_period_if_due(session, sub=sub, now=now)
    usage = get_or_create_usage(
        session, household_id=household_id, period_start=sub.period_start
    )
    return sub, usage, limits_for(effective_tier(sub))


def _snapshot(sub, usage, limits) -> QuotaSnapshot:
    return QuotaSnapshot(
        usage.receipts_used,
        limits.receipts + usage.receipts_granted,
        usage.actions_used,
        limits.actions + usage.actions_granted,
        {
            "cook": usage.cook_used,
            "plan": usage.plan_used,
            "edit": usage.edit_used,
            "chat": usage.chat_used,
            "search": usage.search_used,
        },
        sub.period_end,
        effective_tier(sub),
    )


def snapshot(session: Session, *, household_id: int, now: datetime) -> QuotaSnapshot:
    sub, usage, limits = _resolve(session, household_id, now)
    session.flush()
    return _snapshot(sub, usage, limits)


def admit(
    session: Session,
    *,
    household_id: int,
    op: OpClass,
    provider: str,
    now: datetime,
) -> Admission:
    if not METERING_ENABLED:
        return Admission(
            True,
            "metering_disabled",
            False,
            QuotaSnapshot(0, 0, 0, 0, {}, now, "local"),
        )
    sub, usage, limits = _resolve(session, household_id, now)
    current = _snapshot(sub, usage, limits)
    session.flush()
    if not BILLING_ENABLED:
        return Admission(True, "billing_disabled", False, current)
    degrade = op != "receipt"
    if op == "receipt" and usage.receipts_used >= current.receipts_limit:
        return Admission(False, "receipts_exhausted", False, current)
    if (
        op != "receipt"
        and usage.actions_used + units_for(op, provider) > current.actions_limit
    ):
        return Admission(False, "actions_exhausted", degrade, current)
    if usage.cost_micros_used >= limits.cost_breaker_micros + usage.cost_micros_granted:
        return Admission(False, "cost_breaker", degrade, current)
    return Admission(True, "ok", False, current)


_RAW_COUNTER = {
    "cook": "cook_used",
    "plan": "plan_used",
    "edit": "edit_used",
    "chat": "chat_used",
    "search": "search_used",
}


def commit(
    session: Session,
    *,
    household_id: int,
    op: OpClass,
    provider: str,
    cost_micros: int | None,
    now: datetime,
) -> None:
    if cost_micros is not None and cost_micros < 0:
        raise ValueError("cost_micros must be non-negative")
    if not METERING_ENABLED:
        return
    _sub, usage, _limits = _resolve(session, household_id, now)
    if op == "receipt":
        usage.receipts_used += 1
    else:
        counter = _RAW_COUNTER[op]
        setattr(usage, counter, getattr(usage, counter) + 1)
        usage.actions_used += units_for(op, provider)
    usage.cost_micros_used += cost_micros or 0
    session.add(usage)
    # Metering is appended after domain services that may already have committed
    # and refreshed their objects. Preserve those loaded values for the rest of
    # the handler instead of expiring the caller's entire identity map again.
    expire_on_commit = session.expire_on_commit
    session.expire_on_commit = False
    try:
        session.commit()
    finally:
        session.expire_on_commit = expire_on_commit


METERED_OPS: Mapping[str, OpClass] = {
    "photo": "receipt",
    "correct_reply": "edit",
    "nl_text": "chat",
    "add": "edit",
    "correct": "edit",
    "prefs": "edit",
    "cook": "cook",
    "plan": "plan",
    "cook_more": "cook",
    "cook_pick": "cook",
    "fav_cook": "cook",
    "plan_swap": "plan",
    "freeze": "search",
    "fridge": "search",
}
UNMETERED = frozenset(
    {
        "apply",
        "ate",
        "billing",
        "bind",
        "buy",
        "calendar",
        "cancel",
        "cook_dislike",
        "cook_adjust",
        "cook_alt",
        "cook_like",
        "cook_more_back",
        "cook_more_opts",
        "cook_save",
        "cook_shop",
        "cook_cooked",
        "cooked_confirm",
        "cooked_none",
        "cooked_toggle",
        "delete",
        "digest_at",
        "favorites",
        "help",
        "household",
        "history",
        "invite",
        "item_corr",
        "item_ctext",
        "item_list",
        "item_nudge",
        "item_open",
        "item_rm",
        "item_rmok",
        "join",
        "lang",
        "leave",
        "list",
        "llm",
        "pantry",
        "plan_cancel",
        "plan_cooked",
        "plan_shop",
        "quota",
        "remove",
        "shop_done",
        "shopping",
        "show_all",
        "snooze",
        "snooze2",
        "start",
        "stats",
        "toss",
        "tz",
        "undo_add",
        "undo_receipt",
    }
)
