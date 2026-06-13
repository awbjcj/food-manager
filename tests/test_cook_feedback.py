import json
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.cook.feedback import feedback_signal, set_feedback
from app.cook.models import (
    NutritionScore,
    RecipeCandidate,
    RecipeIngredient,
    ScoredCandidate,
)
from app.models import CookSession, Household, User


def _scored(title="Pasta", cuisine="italian", ingredients=("pasta", "tomato")):
    rec = RecipeCandidate(
        title=title, cuisine=cuisine, source_url="https://x",
        ingredients=[RecipeIngredient(name=n) for n in ingredients],
        method_gist="boil", deliciousness=0.7,
    )
    nut = NutritionScore(health_score=80, effort="easy", est_minutes=20, rationale="ok")
    return ScoredCandidate(recipe=rec, nutrition=nut, expiry_use=0.5, final_score=0.7)


def _engine_with_done_cook(candidates):
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    now = datetime(2026, 5, 30, 12, 0)
    with Session(engine) as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id is not None
        db.add(User(telegram_id=1, chat_id=1, household_id=household.id,
                    created_at=datetime.now(timezone.utc)))
        db.add(CookSession(
            household_id=1, status="done", chat_id=1, selected_item_ids="[]",
            candidates_json=json.dumps([c.model_dump() for c in candidates]),
            chosen_index=0, created_at=now, expires_at=now,
        ))
        db.commit()
    return engine


def test_set_feedback_records_verdict_and_timestamp():
    engine = _engine_with_done_cook([_scored()])
    now = datetime(2026, 5, 30, 12, 5, tzinfo=timezone.utc)
    with Session(engine) as db:
        cook = db.exec(__import__("sqlmodel").select(CookSession)).one()
        set_feedback(db, cook=cook, feedback="liked", now=now)
        db.refresh(cook)
        assert cook.feedback == "liked"
        assert cook.feedback_at is not None
        assert cook.feedback_at.tzinfo is None  # stored UTC-naive


def test_set_feedback_rejects_bad_value():
    engine = _engine_with_done_cook([_scored()])
    now = datetime(2026, 5, 30, 12, 5, tzinfo=timezone.utc)
    with Session(engine) as db:
        cook = db.exec(__import__("sqlmodel").select(CookSession)).one()
        with pytest.raises(ValueError):
            set_feedback(db, cook=cook, feedback="meh", now=now)
        db.refresh(cook)
        assert cook.feedback == "none"


def test_feedback_signal_reconstructs_for_v36():
    engine = _engine_with_done_cook([_scored(cuisine="thai", ingredients=("tofu", "basil"))])
    now = datetime(2026, 5, 30, 12, 5, tzinfo=timezone.utc)
    with Session(engine) as db:
        cook = db.exec(__import__("sqlmodel").select(CookSession)).one()
        set_feedback(db, cook=cook, feedback="liked", now=now)
        signal = feedback_signal(cook)
        assert signal is not None
        assert signal.cuisine == "thai"
        assert signal.ingredients == ["tofu", "basil"]
        assert signal.verdict == "liked"


def test_feedback_signal_none_when_no_feedback():
    engine = _engine_with_done_cook([_scored()])
    with Session(engine) as db:
        cook = db.exec(__import__("sqlmodel").select(CookSession)).one()
        assert feedback_signal(cook) is None
