import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.cook.models import NutritionScore, RecipeCandidate, RecipeIngredient, ScoredCandidate, SourcedRecipe
from app.models import Household, MealPlan, MealPlanEntry, PantryItem, User
from app.profile_service import FoodProfile
from app.week_composer import DaySpec


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


def _profile(**kw):
    base = dict(
        diet="none", exclusions=[], preferred_cuisines=[],
        max_cook_minutes=None, household_size=2, note="",
    )
    base.update(kw)
    return FoodProfile(**base)


class FakeRecipeSource:
    """Returns canned SourcedRecipes; records criteria it was called with."""

    def __init__(self, pages):
        self.pages = list(pages)   # one list[SourcedRecipe] per call
        self.calls = []

    def available(self):
        return True

    async def search(self, criteria, *, remaining_cost_micros=None):
        self.calls.append(criteria)
        return (self.pages.pop(0) if self.pages else []), 100


class FakeComposer:
    def __init__(self, specs=None, error=None):
        self.specs, self.error = specs, error

    async def compose(self, *, pantry, profile, days, today):
        if self.error:
            raise self.error
        return self.specs


def _sourced(title, *, ingredients, external_id, health=50, deliciousness=0.5):
    return SourcedRecipe(
        recipe=RecipeCandidate(
            title=title, cuisine="italian", source_url=f"https://recipes.test/{external_id}",
            ingredients=[RecipeIngredient(name=n) for n in ingredients],
            method_gist="Cook it.", deliciousness=deliciousness,
        ),
        nutrition=NutritionScore(health_score=health, effort="easy", est_minutes=20, rationale="x"),
        external_id=external_id,
    )


@pytest.fixture
def seeded_pantry(session_factory):
    today = date(2026, 7, 9)
    with session_factory() as db:
        for name, days in [
            ("yogurt", 1), ("chicken", 2), ("rice", 300), ("beans", 200), ("pasta", 250),
        ]:
            db.add(PantryItem(
                household_id=1, raw_name=name, normalized_name=name,
                category="produce", qty=1.0, purchased_on=today,
                shelf_life_days=days, shelf_life_source="llm", ingest_shelf_life_source="llm",
                expires_on=today + timedelta(days=days), status="active",
                created_via="receipt", created_at=datetime.now(timezone.utc),
            ))
        db.commit()
    return session_factory


@pytest.mark.asyncio
async def test_build_plan_allocates_sequentially(seeded_pantry):
    from app.plan_service import build_plan

    session_factory = seeded_pantry
    today = date(2026, 7, 9)
    day0_recipe = _sourced("Yogurt Rice Bowl", ingredients=["yogurt", "rice"], external_id="A")
    day1_recipe = _sourced("Chicken Stir Fry", ingredients=["chicken"], external_id="B")
    fake_source = FakeRecipeSource([[day0_recipe], [day1_recipe]])
    fake_composer = FakeComposer(specs=[
        DaySpec(day_index=0, cuisine="italian", purpose="use_it_up", feature_items=["yogurt"]),
        DaySpec(day_index=1, cuisine="asian", purpose="use_it_up", feature_items=["chicken"]),
    ])
    with session_factory() as session:
        plan, entries = await build_plan(
            session, household_id=1, days=2, profile=_profile(), composer=fake_composer,
            source=fake_source, today=today, chat_id=1, cost_ceiling_micros=1_000_000,
            created_at=datetime.now(timezone.utc),
        )
    assert len(entries) == 2
    assert "yogurt" not in fake_source.calls[1].include_ingredients
    assert entries[0].day_index == 0 and entries[1].date == today + timedelta(days=1)
    assert plan.status == "active"


@pytest.mark.asyncio
async def test_build_plan_dedups_recipes_across_days(seeded_pantry):
    from app.plan_service import build_plan

    session_factory = seeded_pantry
    today = date(2026, 7, 9)
    recipe_a = _sourced("Recipe A", ingredients=["yogurt"], external_id="A")
    recipe_a_dup = _sourced("Recipe A Dup", ingredients=["chicken"], external_id="A")
    recipe_b = _sourced("Recipe B", ingredients=["chicken"], external_id="B")
    fake_source = FakeRecipeSource([[recipe_a], [recipe_a_dup, recipe_b]])
    fake_composer = FakeComposer(specs=[
        DaySpec(day_index=0, cuisine="italian", feature_items=["yogurt"]),
        DaySpec(day_index=1, cuisine="asian", feature_items=["chicken"]),
    ])
    with session_factory() as session:
        plan, entries = await build_plan(
            session, household_id=1, days=2, profile=_profile(), composer=fake_composer,
            source=fake_source, today=today, chat_id=1, cost_ceiling_micros=1_000_000,
            created_at=datetime.now(timezone.utc),
        )
    assert len(entries) == 2
    day1_candidate = ScoredCandidate.model_validate_json(entries[1].recipe_json)
    assert day1_candidate.external_id == "B"


@pytest.mark.asyncio
async def test_build_plan_composer_failure_uses_heuristic(seeded_pantry):
    from app.plan_service import build_plan

    session_factory = seeded_pantry
    today = date(2026, 7, 9)
    recipe = _sourced("Any", ingredients=["yogurt"], external_id="A")
    fake_source = FakeRecipeSource([[recipe]])
    fake_composer = FakeComposer(error=RuntimeError("agent down"))
    with session_factory() as session:
        plan, entries = await build_plan(
            session, household_id=1, days=1, profile=_profile(), composer=fake_composer,
            source=fake_source, today=today, chat_id=1, cost_ceiling_micros=1_000_000,
            created_at=datetime.now(timezone.utc),
        )
    assert len(entries) == 1
    # heuristic featured the two earliest-expiry items on day 0
    assert fake_source.calls[0].include_ingredients == ["yogurt", "chicken"]


@pytest.mark.asyncio
async def test_build_plan_respects_cost_ceiling(seeded_pantry):
    from app.plan_service import build_plan

    session_factory = seeded_pantry
    today = date(2026, 7, 9)
    r0 = _sourced("R0", ingredients=["yogurt"], external_id="A")
    r1 = _sourced("R1", ingredients=["chicken"], external_id="B")
    r2 = _sourced("R2", ingredients=["rice"], external_id="C")
    fake_source = FakeRecipeSource([[r0], [r1], [r2]])
    fake_composer = FakeComposer(specs=[
        DaySpec(day_index=0, feature_items=["yogurt"]),
        DaySpec(day_index=1, feature_items=["chicken"]),
        DaySpec(day_index=2, feature_items=["rice"]),
    ])
    with session_factory() as session:
        plan, entries = await build_plan(
            session, household_id=1, days=3, profile=_profile(), composer=fake_composer,
            source=fake_source, today=today, chat_id=1, cost_ceiling_micros=150,
            created_at=datetime.now(timezone.utc),
        )
    assert len(entries) < 3
    assert plan.status == "active"


@pytest.mark.asyncio
async def test_build_plan_raises_when_pantry_too_small(session_factory):
    from app.plan_service import NotEnoughItemsToPlan, build_plan

    today = date(2026, 7, 9)
    with session_factory() as db:
        db.add(PantryItem(
            household_id=1, raw_name="milk", normalized_name="milk",
            category="dairy", qty=1.0, purchased_on=today,
            shelf_life_days=7, shelf_life_source="llm", ingest_shelf_life_source="llm",
            expires_on=today + timedelta(days=7), status="active",
            created_via="receipt", created_at=datetime.now(timezone.utc),
        ))
        db.commit()
    fake_source = FakeRecipeSource([])
    fake_composer = FakeComposer(specs=[])
    with session_factory() as session, pytest.raises(NotEnoughItemsToPlan):
        await build_plan(
            session, household_id=1, days=1, profile=_profile(), composer=fake_composer,
            source=fake_source, today=today, chat_id=1, cost_ceiling_micros=1_000_000,
            created_at=datetime.now(timezone.utc),
        )


@pytest.mark.asyncio
async def test_swap_day_advances_offset_and_dedups(seeded_pantry):
    from app.plan_service import PLAN_PAGE, build_plan, swap_day

    session_factory = seeded_pantry
    today = date(2026, 7, 9)
    day0_recipe = _sourced("Original", ingredients=["yogurt"], external_id="A")
    fake_source = FakeRecipeSource([[day0_recipe]])
    fake_composer = FakeComposer(specs=[DaySpec(day_index=0, feature_items=["yogurt"])])
    with session_factory() as session:
        plan, entries = await build_plan(
            session, household_id=1, days=1, profile=_profile(), composer=fake_composer,
            source=fake_source, today=today, chat_id=1, cost_ceiling_micros=1_000_000,
            created_at=datetime.now(timezone.utc),
        )
        entry = entries[0]
        assert entry.id is not None

        fresh_recipe = _sourced("Fresh", ingredients=["chicken"], external_id="B")
        swap_source = FakeRecipeSource([[fresh_recipe]])
        updated = await swap_day(
            session, plan=plan, entry=entry, profile=_profile(), source=swap_source,
            today=today, cost_ceiling_micros=1_000_000,
        )
        assert updated is not None
        assert entry.search_offset == PLAN_PAGE
        assert swap_source.calls[0].offset == PLAN_PAGE
        new_candidate = ScoredCandidate.model_validate_json(updated.recipe_json)
        assert new_candidate.external_id == "B"


def test_aggregate_shopping_unions_and_dedups():
    from app.plan_service import aggregate_shopping

    e1 = MealPlanEntry(
        plan_id=1, day_index=0, date=date(2026, 7, 9), recipe_json="{}", spec_json="{}",
        shopping_json=json.dumps(["soy sauce", "rice"]),
    )
    e2 = MealPlanEntry(
        plan_id=1, day_index=1, date=date(2026, 7, 10), recipe_json="{}", spec_json="{}",
        shopping_json=json.dumps(["Rice", "tofu"]),
    )
    assert aggregate_shopping([e1, e2]) == ["soy sauce", "rice", "tofu"]


def test_cancel_active_plans_supersedes(session_factory):
    from app.plan_service import cancel_active_plans

    with session_factory() as db:
        plan = MealPlan(
            household_id=1, start_date=date(2026, 7, 9), days=3, status="active",
            chat_id=1, created_at=datetime.now(timezone.utc),
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)
        count = cancel_active_plans(db, household_id=1)
        assert count == 1
        db.refresh(plan)
        assert plan.status == "cancelled"
