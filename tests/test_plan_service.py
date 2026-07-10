from datetime import date, datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import Household, User


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    def make():
        return Session(engine)

    with make() as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        db.add(
            User(
                telegram_id=1,
                chat_id=1,
                household_id=household.id,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    return make


def test_meal_plan_models_roundtrip(session_factory):
    from app.models import MealPlan, MealPlanEntry

    with session_factory() as db:
        plan = MealPlan(
            household_id=1, start_date=date(2026, 7, 9), days=3,
            chat_id=1, created_at=datetime.now(timezone.utc),
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)
        assert plan.status == "draft" and plan.cost_micros_usd == 0
        entry = MealPlanEntry(
            plan_id=plan.id, day_index=0, date=date(2026, 7, 9),
            recipe_json="{}", spec_json="{}",
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        assert entry.search_offset == 0 and entry.shopping_json == "[]"


def test_plan_cost_ceiling_setting(monkeypatch):
    from app.settings import Settings

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert Settings().plan_cost_ceiling_micros == 150_000  # type: ignore[call-arg]
    monkeypatch.setenv("PLAN_COST_CEILING_MICROS", "999")
    assert Settings().plan_cost_ceiling_micros == 999  # type: ignore[call-arg]
