"""Affinity (v5.3): the household's 👍/👎 history as a deterministic taste score.

`affinity` maps a candidate recipe against the recent feedback signals to a
[0, 1] term consumed by `blended_score`. It is pure and source-agnostic — a
Spoonacular result and an LLM result are taste-ranked identically. Dislikes
push the score down but can never hard-exclude a dish (exclusions are diet and
safety; dislikes are preference). With no signals the term is a constant 0.5,
so ranking is provably unchanged until feedback exists.

`steering_summary` turns the same signals into a short deterministic sentence
injected into the LLM-tail prompt only (the only source whose output can be
steered by a prompt); Spoonacular/TheMealDB ignore it entirely.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from sqlmodel import Session, select

from app.cook.feedback import VALID_FEEDBACK, FeedbackSignal, feedback_signal
from app.models import CookSession
from app.normalization import normalize

#: How many recent feedback signals shape the household's taste.
SIGNAL_WINDOW = 50


def list_recent_signals(
    session: Session, *, household_id: int, limit: int = SIGNAL_WINDOW
) -> list[FeedbackSignal]:
    rows = session.exec(
        select(CookSession)
        .where(
            CookSession.household_id == household_id,
            CookSession.feedback.in_(VALID_FEEDBACK),  # type: ignore[union-attr]
        )
        .order_by(CookSession.feedback_at.desc())  # type: ignore[union-attr]
        .limit(limit)
    ).all()
    signals = (feedback_signal(row) for row in rows)
    return [s for s in signals if s is not None]


def _similarity(
    *, cuisine: str, ingredients: set[str], signal: FeedbackSignal
) -> float:
    sim = 0.0
    signal_cuisine = normalize(signal.cuisine or "")
    if cuisine and signal_cuisine == cuisine:
        sim += 0.5
    signal_ings = {normalize(n) for n in signal.ingredients}
    union = ingredients | signal_ings
    if union:
        sim += 0.5 * (len(ingredients & signal_ings) / len(union))
    return sim


def affinity(
    *,
    cuisine: str | None,
    ingredient_names: Sequence[str],
    signals: Sequence[FeedbackSignal],
) -> float:
    """Taste score in [0, 1]; 0.5 = neutral (and the empty-signal constant)."""
    if not signals:
        return 0.5
    recipe_cuisine = normalize(cuisine or "")
    recipe_ings = {normalize(n) for n in ingredient_names}
    total = 0.0
    for signal in signals:
        sim = _similarity(
            cuisine=recipe_cuisine, ingredients=recipe_ings, signal=signal
        )
        total += sim if signal.verdict == "liked" else -sim
    mean = total / len(signals)  # in [-1, 1]
    # Clamp away from the extremes: a perfect-similarity dislike would otherwise
    # land exactly at 0.0, hard-excluding the dish rather than merely
    # suppressing it (dislikes are preference, not a safety exclusion).
    return max(0.05, min(1.0, (mean + 1.0) / 2.0))


def steering_summary(
    signals: Sequence[FeedbackSignal], *, max_chars: int = 200
) -> str:
    """Deterministic taste one-liner for LLM prompts; '' when no signals exist."""
    if not signals:
        return ""

    def top3(counter: Counter) -> list[str]:
        return [name for name, _ in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]

    liked_cuisines: Counter = Counter()
    liked_ings: Counter = Counter()
    disliked_cuisines: Counter = Counter()
    disliked_ings: Counter = Counter()
    for signal in signals:
        cuisines = liked_cuisines if signal.verdict == "liked" else disliked_cuisines
        ings = liked_ings if signal.verdict == "liked" else disliked_ings
        if signal.cuisine:
            cuisines[normalize(signal.cuisine)] += 1
        for name in signal.ingredients:
            ings[normalize(name)] += 1

    parts: list[str] = []
    if liked_cuisines or liked_ings:
        parts.append(
            "likes " + ", ".join(top3(liked_cuisines) + top3(liked_ings))
        )
    if disliked_cuisines or disliked_ings:
        parts.append(
            "dislikes " + ", ".join(top3(disliked_cuisines) + top3(disliked_ings))
        )
    text = "Household taste: " + "; ".join(parts) + "."
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(" ", 1)[0]
    return cut
