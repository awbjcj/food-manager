from app.cook_models import (
    NutritionScore,
    RecipeCandidate,
    RecipeIngredient,
    ScoredCandidate,
)
from app.cook_logic import (
    BLEND_WEIGHTS,
    blended_score,
    expiry_utilization,
    shopping_list,
    violates_exclusions,
)


def test_recipe_candidate_validates():
    c = RecipeCandidate(
        title="Tomato Pasta",
        cuisine="italian",
        source_url="https://x/y",
        ingredients=[
            RecipeIngredient(name="tomato", qty=2, unit="ct"),
            RecipeIngredient(name="pasta"),
        ],
        method_gist="Boil pasta, make sauce.",
        deliciousness=0.8,
    )
    assert c.ingredients[1].qty is None
    assert 0.0 <= c.deliciousness <= 1.0


def test_violates_exclusions_matches_normalized_substring():
    assert violates_exclusions(["peanut butter", "jam"], exclusions=["peanut"])
    assert not violates_exclusions(["almond butter"], exclusions=["peanut"])


def test_expiry_utilization_fraction_of_urgent_items_used():
    # urgent item names: tomato (1d), spinach (2d); recipe uses tomato only
    used = expiry_utilization(
        recipe_names=["tomato", "pasta"],
        urgent_names=["tomato", "spinach"],
    )
    assert used == 0.5


def test_blended_score_weights():
    score = blended_score(health_0_1=1.0, expiry_use=0.0, deliciousness=0.0)
    assert abs(score - BLEND_WEIGHTS["health"]) < 1e-9


def test_shopping_list_excludes_pantry_items():
    missing = shopping_list(
        recipe_names=["tomato", "pasta", "basil"],
        pantry_normalized=["tomato", "basil"],
    )
    assert missing == ["pasta"]
