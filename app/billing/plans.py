from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

PlanTier = Literal["free", "family"]
OpClass = Literal["receipt", "cook", "plan", "edit", "chat", "search"]
PaymentKind = Literal["subscription", "topup", "refund", "grant"]

PERIOD_DAYS = 30
STARS_SUBSCRIPTION_PERIOD = 2_592_000
MAX_SUBSCRIPTION_STARS = 10_000

ACTION_WEIGHTS: Mapping[str, int] = {
    "chat": 1,
    "edit": 1,
    "search": 2,
    "cook": 10,
    "plan": 25,
}
PROVIDER_UNIT_MULTIPLIER: Mapping[str, int] = {
    "gemini": 1,
    "deepseek": 1,
    "openai": 3,
    "anthropic": 3,
}


@dataclass(frozen=True)
class TierLimits:
    tier: PlanTier
    receipts: int
    actions: int
    seats: int
    cost_breaker_micros: int


TIERS: Mapping[str, TierLimits] = {
    "free": TierLimits("free", 5, 30, 2, 250_000),
    "family": TierLimits("family", 100, 300, 10, 2_500_000),
}


@dataclass(frozen=True)
class Sku:
    code: str
    title: str
    description: str
    stars: int
    kind: PaymentKind
    subscription_period: int | None = None
    grants_tier: PlanTier | None = None
    grants_receipts: int = 0
    grants_actions: int = 0
    grants_cost_micros: int = 0


SKUS: Mapping[str, Sku] = {
    "family_monthly": Sku(
        "family_monthly",
        "Family plan",
        "100 receipts, 300 AI actions, up to 10 members, per 30 days.",
        500,
        "subscription",
        subscription_period=STARS_SUBSCRIPTION_PERIOD,
        grants_tier="family",
    ),
    "topup_receipts_50": Sku(
        "topup_receipts_50",
        "+50 receipts",
        "50 extra receipts, usable until your current period ends.",
        250,
        "topup",
        grants_receipts=50,
        grants_cost_micros=1_250_000,
    ),
    "topup_actions_150": Sku(
        "topup_actions_150",
        "+150 AI actions",
        "150 extra AI actions, usable until your current period ends.",
        250,
        "topup",
        grants_actions=150,
        grants_cost_micros=1_250_000,
    ),
}


def limits_for(tier: str) -> TierLimits:
    return TIERS.get(tier, TIERS["free"])


def units_for(op: OpClass, provider: str) -> int:
    return ACTION_WEIGHTS.get(op, 0) * PROVIDER_UNIT_MULTIPLIER.get(provider, 3)


def sku_for(code: str) -> Sku | None:
    return SKUS.get(code)
