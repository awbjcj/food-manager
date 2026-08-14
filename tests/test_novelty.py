from datetime import UTC, date, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.cook.logic import BLEND_WEIGHTS, blended_score
from app.cook.models import (
    NutritionScore,
    RecipeCandidate,
    RecipeIngredient,
    ScoredCandidate,
)
from app.cook.novelty import (
    NOVELTY_WINDOW_DAYS,
    list_recent_cooks,
    novelty,
    recipe_key,
)
from app.models import CookedMeal, Household

TODAY = date(2026, 8, 14)


def _cooked(household_id, key, *, days_ago, confirmed=True):
    return CookedMeal(
        household_id=household_id,
        source="plan",
        recipe_key=key,
        recipe_title=key,
        cooked_on=date.fromordinal(TODAY.toordinal() - days_ago),
        confirmed_at=datetime.now(UTC) if confirmed else None,
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def household_id(session):
    hh = Household(created_at=datetime.now(UTC))
    session.add(hh)
    session.commit()
    session.refresh(hh)
    assert hh.id is not None
    return hh.id


def test_never_cooked_is_full_novelty():
    assert novelty("spoon:10", [], TODAY) == 1.0


def test_same_day_repeat_hits_the_floor_but_never_zero():
    score = novelty("spoon:10", [_cooked(1, "spoon:10", days_ago=0)], TODAY)
    assert score == 0.05
    assert score > 0.0


def test_novelty_recovers_fully_at_the_window_edge():
    cooks = [_cooked(1, "spoon:10", days_ago=NOVELTY_WINDOW_DAYS)]
    assert novelty("spoon:10", cooks, TODAY) == 1.0


def test_novelty_uses_the_most_recent_cook():
    cooks = [
        _cooked(1, "spoon:10", days_ago=20),
        _cooked(1, "spoon:10", days_ago=2),
    ]
    assert novelty("spoon:10", cooks, TODAY) == pytest.approx(2 / NOVELTY_WINDOW_DAYS)


def test_other_recipes_do_not_suppress_this_one():
    assert novelty("spoon:10", [_cooked(1, "spoon:99", days_ago=1)], TODAY) == 1.0


def test_list_recent_cooks_ignores_unconfirmed_and_stale(session, household_id):
    session.add(_cooked(household_id, "a", days_ago=1))
    session.add(_cooked(household_id, "b", days_ago=1, confirmed=False))
    session.add(_cooked(household_id, "c", days_ago=NOVELTY_WINDOW_DAYS + 5))
    session.commit()
    keys = {row.recipe_key for row in list_recent_cooks(session, household_id=household_id, today=TODAY)}
    assert keys == {"a"}


def test_list_recent_cooks_is_household_scoped(session, household_id):
    session.add(_cooked(household_id, "mine", days_ago=1))
    session.add(_cooked(household_id + 999, "theirs", days_ago=1))
    session.commit()
    keys = {row.recipe_key for row in list_recent_cooks(session, household_id=household_id, today=TODAY)}
    assert keys == {"mine"}


def test_recipe_key_prefers_external_id():
    candidate = ScoredCandidate(
        recipe=RecipeCandidate(
            title="Chicken Tikka",
            cuisine="indian",
            source_url="https://recipes.test/10",
            ingredients=[RecipeIngredient(name="chicken")],
            method_gist="Cook it.",
            deliciousness=0.5,
        ),
        nutrition=NutritionScore(health_score=50, effort="easy", est_minutes=20, rationale="x"),
        expiry_use=0.0,
        external_id="spoon:10",
        final_score=0.5,
    )
    assert recipe_key(candidate) == "spoon:10"
    assert recipe_key(candidate.model_copy(update={"external_id": None})) == "chicken tikka"


def test_weights_still_sum_to_one():
    assert abs(sum(BLEND_WEIGHTS.values()) - 1.0) < 1e-9


def test_blended_score_uses_the_novelty_term():
    base = {
        "health_0_1": 0.5,
        "expiry_use": 0.5,
        "deliciousness": 0.5,
        "affinity_0_1": 0.5,
    }
    assert blended_score(**base, novelty_0_1=1.0) > blended_score(**base, novelty_0_1=0.05)


def test_novelty_defaults_to_the_cold_start_value():
    base = {
        "health_0_1": 0.5,
        "expiry_use": 0.5,
        "deliciousness": 0.5,
        "affinity_0_1": 0.5,
    }
    assert blended_score(**base) == blended_score(**base, novelty_0_1=1.0)
