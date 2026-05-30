from app.cook_models import (
    NutritionScore,
    RecipeCandidate,
    RecipeIngredient,
    ScoredCandidate,
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
