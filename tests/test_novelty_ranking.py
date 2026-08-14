from datetime import UTC, date, datetime

from app.cook.models import (
    NutritionScore,
    RecipeCandidate,
    RecipeIngredient,
    SourcedRecipe,
)
from app.models import CookedMeal
from app.plan_service import _pick

TODAY = date(2026, 8, 14)


def _sourced(title, external_id, *, ingredients=("chicken",)):
    return SourcedRecipe(
        recipe=RecipeCandidate(
            title=title,
            cuisine="indian",
            source_url=f"https://recipes.test/{external_id}",
            ingredients=[RecipeIngredient(name=n) for n in ingredients],
            method_gist="Cook it.",
            deliciousness=0.5,
        ),
        nutrition=NutritionScore(health_score=50, effort="easy", est_minutes=20, rationale="x"),
        external_id=external_id,
    )


def test_recently_cooked_candidate_loses_to_an_equal_fresh_one():
    cooked_yesterday = [
        CookedMeal(
            household_id=1,
            source="plan",
            recipe_key="spoon:10",
            recipe_title="Tikka",
            cooked_on=date(2026, 8, 13),
            confirmed_at=datetime.now(UTC),
        )
    ]
    picked = _pick(
        [_sourced("Tikka", "spoon:10"), _sourced("Korma", "spoon:20")],
        exclusions=[],
        taken_ids=set(),
        urgent_names=[],
        signals=[],
        cooks=cooked_yesterday,
        today=TODAY,
    )
    assert picked is not None
    assert picked.recipe.title == "Korma"


def test_with_no_cook_history_ranking_is_unchanged():
    picked = _pick(
        [_sourced("Tikka", "spoon:10"), _sourced("Korma", "spoon:20")],
        exclusions=[],
        taken_ids=set(),
        urgent_names=[],
        signals=[],
        cooks=[],
        today=TODAY,
    )
    assert picked is not None
    assert picked.recipe.title == "Tikka"  # source order preserved on a tie
