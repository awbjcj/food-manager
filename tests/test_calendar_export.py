from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.calendar_export import build_plan_calendar
from app.cook.models import (
    NutritionScore,
    RecipeCandidate,
    RecipeIngredient,
    ScoredCandidate,
)
from app.handlers.plan import handle_calendar
from app.models import Household, MealPlan, MealPlanEntry, User


def _candidate(title: str = "Pasta; Primavera") -> ScoredCandidate:
    return ScoredCandidate(
        recipe=RecipeCandidate(
            title=title,
            cuisine="italian",
            source_url="https://recipes.test/pasta",
            ingredients=[RecipeIngredient(name="pasta"), RecipeIngredient(name="peas")],
            method_gist="Boil, then serve.",
            deliciousness=0.8,
        ),
        nutrition=NutritionScore(
            health_score=70, effort="easy", est_minutes=25, rationale="balanced"
        ),
        expiry_use=0.5,
        final_score=0.7,
    )


def _plan_and_entry() -> tuple[MealPlan, MealPlanEntry]:
    plan = MealPlan(
        id=3,
        household_id=1,
        start_date=date(2026, 8, 31),
        days=3,
        status="active",
        chat_id=1,
        created_at=datetime(2026, 8, 29, 12, tzinfo=UTC),
    )
    entry = MealPlanEntry(
        id=7,
        plan_id=3,
        day_index=0,
        date=date(2026, 8, 31),
        recipe_json=_candidate().model_dump_json(),
        spec_json="{}",
    )
    return plan, entry


def test_build_plan_calendar_emits_all_day_event_and_escapes_text():
    plan, entry = _plan_and_entry()

    payload = build_plan_calendar(plan, [entry])

    assert payload.startswith("BEGIN:VCALENDAR\r\n")
    assert "DTSTART;VALUE=DATE:20260831\r\n" in payload
    assert "DTEND;VALUE=DATE:20260901\r\n" in payload
    assert "SUMMARY:Pasta\\; Primavera\r\n" in payload
    assert "UID:meal-plan-3-7@food-manager\r\n" in payload
    assert "URL:https://recipes.test/pasta\r\n" in payload
    assert payload.endswith("END:VCALENDAR\r\n")


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        household = Household(created_at=datetime.now(UTC))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id is not None
        db.add(
            User(
                telegram_id=1,
                chat_id=1,
                household_id=household.id,
                created_at=datetime.now(UTC),
            )
        )
        plan, entry = _plan_and_entry()
        plan.id = None
        plan.household_id = household.id
        db.add(plan)
        db.commit()
        db.refresh(plan)
        assert plan.id is not None
        entry.id = None
        entry.plan_id = plan.id
        db.add(entry)
        db.commit()
    return lambda: Session(engine)


@pytest.mark.asyncio
async def test_calendar_command_sends_ics_document(session_factory, monkeypatch):
    from app import handler_support

    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    msg = SimpleNamespace(
        text="/calendar",
        from_user=SimpleNamespace(id=1),
        chat=SimpleNamespace(id=1, type="private"),
        answer=AsyncMock(),
        answer_document=AsyncMock(),
    )

    await handle_calendar(msg, session_factory=session_factory)

    document = msg.answer_document.await_args.args[0]
    assert document.filename == "meal-plan-2026-08-31.ics"
    assert b"BEGIN:VCALENDAR" in document.data
    assert msg.answer_document.await_args.kwargs["caption"] == "Calendar exported."
