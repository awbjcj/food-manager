"""Deterministic, rebuildable taste profiles from dated pantry outcomes.

These are soft preferences, separate from explicit dietary restrictions. Old
terminal items without dated outcomes cannot establish early eating/tossing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from sqlmodel import Session, col, select

from app.models import PantryOutcome
from app.normalization import normalize


@dataclass(frozen=True)
class FoodPreference:
    name: str
    score: float  # [-1, 1], positive = liked, negative = disliked
    eaten_count: int
    tossed_count: int
    evidence_count: int


@dataclass(frozen=True)
class PreferenceProfile:
    foods: tuple[FoodPreference, ...] = ()
    history_count: int = 0

    @property
    def liked(self) -> tuple[FoodPreference, ...]:
        return tuple(
            sorted(
                (food for food in self.foods if food.score > 0),
                key=lambda food: (-food.score, food.name),
            )
        )

    @property
    def disliked(self) -> tuple[FoodPreference, ...]:
        return tuple(
            sorted(
                (food for food in self.foods if food.score < 0),
                key=lambda food: (food.score, food.name),
            )
        )


def outcome_signal(outcome: PantryOutcome) -> float:
    """Earlier consumption = more remaining shelf life = stronger liking."""
    duration = (outcome.expires_on - outcome.origin_on).days
    remaining = (outcome.expires_on - outcome.occurred_on).days
    if duration <= 0 or remaining <= 0 or outcome.occurred_on < outcome.origin_on:
        return 0.0
    if outcome.status == "tossed":
        return -1.0
    if outcome.status == "eaten":
        return min(1.0, remaining / duration)
    return 0.0


def build_preference_profile(
    session: Session,
    *,
    household_id: int,
    user_id: int | None = None,
) -> PreferenceProfile:
    """Omit user_id to pool all household actions; include it for personal taste."""
    query = select(PantryOutcome).where(PantryOutcome.household_id == household_id)
    if user_id is not None:
        query = query.where(PantryOutcome.user_id == user_id)
    outcomes = session.exec(query.order_by(col(PantryOutcome.item_id))).all()
    grouped: dict[str, list[PantryOutcome]] = defaultdict(list)
    for outcome in outcomes:
        name = normalize(outcome.normalized_name)
        if name:
            grouped[name].append(outcome)
    foods = []
    for name, rows in sorted(grouped.items()):
        signals = [outcome_signal(row) for row in rows]
        evidence = sum(signal != 0 for signal in signals)
        foods.append(
            FoodPreference(
                name=name,
                # Two neutral prior observations keep one action from dominating.
                score=sum(signals) / (evidence + 2),
                eaten_count=sum(row.status == "eaten" for row in rows),
                tossed_count=sum(row.status == "tossed" for row in rows),
                evidence_count=evidence,
            )
        )
    return PreferenceProfile(foods=tuple(foods), history_count=len(outcomes))


def ingredient_preference(
    ingredient_names: Sequence[str],
    profile: PreferenceProfile,
) -> float:
    names = {normalize(name) for name in ingredient_names}
    matched = [
        food.score
        for food in profile.foods
        if food.name in names and food.evidence_count
    ]
    return sum(matched) / len(matched) if matched else 0.0


def preference_summary(profile: PreferenceProfile) -> str:
    """Canonical English food names, supplied as taste hints rather than exclusions."""
    parts = []
    for label, foods in (("likes", profile.liked), ("dislikes", profile.disliked)):
        if foods:
            parts.append(label + " " + ", ".join(food.name[:60] for food in foods[:3]))
    return "Food history: " + "; ".join(parts) + "." if parts else ""
