"""Novelty (v5.5): don't serve the same dish twice in a fortnight.

`novelty` maps a candidate recipe against the household's recently *cooked*
meals to a [0, 1] term consumed by `blended_score`. It keys on confirmed
`CookedMeal` rows — meals the household actually made — rather than merely
proposed recipes, because punishing a dish nobody got around to cooking is
wrong. Like `affinity`, a repeat is soft-suppressed and never hard-excluded:
the 0.05 floor keeps a thin pantry from returning nothing at all.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from sqlmodel import Session, select

from app.models import CookedMeal
from app.normalization import normalize

#: How long a cooked dish stays suppressed, and how far back the read reaches.
#: One constant governs both: a cook older than the ramp cannot change a score.
NOVELTY_WINDOW_DAYS = 21

#: Floor mirroring `affinity`: discouraged, never forbidden.
NOVELTY_FLOOR = 0.05


def recipe_key(candidate) -> str:
    """Stable dedup identity for a ScoredCandidate."""
    return candidate.external_id or normalize(candidate.recipe.title)


def list_recent_cooks(
    session: Session, *, household_id: int, today: date
) -> list[CookedMeal]:
    """Confirmed cooks inside the novelty window, most recent first."""
    since = today - timedelta(days=NOVELTY_WINDOW_DAYS)
    rows = session.exec(
        select(CookedMeal)
        .where(
            CookedMeal.household_id == household_id,
            CookedMeal.confirmed_at.is_not(None),  # type: ignore[union-attr]
            CookedMeal.cooked_on >= since,
        )
        .order_by(CookedMeal.cooked_on.desc())  # type: ignore[union-attr]
    ).all()
    return list(rows)


def novelty(key: str, cooks: Sequence[CookedMeal], today: date) -> float:
    """1.0 when unseen in the window; ramps back up from the floor otherwise."""
    gaps = [
        (today - row.cooked_on).days for row in cooks if row.recipe_key == key
    ]
    if not gaps:
        return 1.0
    days_since = min(gaps)  # most recent cook governs
    return max(NOVELTY_FLOOR, min(1.0, days_since / NOVELTY_WINDOW_DAYS))


def count_confirmed(session: Session, *, household_id: int, since: date) -> int:
    """Confirmed `CookedMeal` rows on/after `since` — lives here (not
    `cooked_service`) so `pantry_service.compute_stats` can import it without
    a circular import (`cooked_service` itself imports from `pantry_service`).
    """
    rows = session.exec(
        select(CookedMeal).where(
            CookedMeal.household_id == household_id,
            CookedMeal.confirmed_at.is_not(None),  # type: ignore[union-attr]
            CookedMeal.cooked_on >= since,
        )
    ).all()
    return len(list(rows))
