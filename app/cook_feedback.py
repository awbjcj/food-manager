from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from sqlmodel import Session

from app.cook_models import ScoredCandidate
from app.models import CookSession
from app.pending_service import utc_naive

VALID_FEEDBACK = {"liked", "disliked"}


class FeedbackSignal(BaseModel):
    """The (cuisine, ingredients, verdict) tuple the v3.6 affinity term consumes."""

    cuisine: str
    ingredients: list[str]
    verdict: str


def set_feedback(
    session: Session, *, cook: CookSession, feedback: str, now: datetime
) -> None:
    if feedback not in VALID_FEEDBACK:
        raise ValueError(f"invalid feedback: {feedback!r}")
    cook.feedback = feedback
    cook.feedback_at = utc_naive(now)
    session.add(cook)
    session.commit()


def _chosen_candidate(cook: CookSession) -> Optional[ScoredCandidate]:
    try:
        raw = json.loads(cook.candidates_json or "[]")
    except (TypeError, ValueError):
        return None
    if not raw:
        return None
    index = cook.chosen_index or 0
    if index < 0 or index >= len(raw):
        index = 0
    try:
        return ScoredCandidate.model_validate(raw[index])
    except Exception:
        return None


def feedback_signal(cook: CookSession) -> Optional[FeedbackSignal]:
    if cook.feedback not in VALID_FEEDBACK:
        return None
    candidate = _chosen_candidate(cook)
    if candidate is None:
        return None
    return FeedbackSignal(
        cuisine=candidate.recipe.cuisine,
        ingredients=[i.name for i in candidate.recipe.ingredients],
        verdict=cook.feedback,
    )
