from app.cook_models import (
    NutritionScore,
    Purpose,
    RecipeCandidate,
    RecipeCriteria,
    RecipeIngredient,
    SourcedRecipe,
)


def test_recipe_criteria_defaults():
    criteria = RecipeCriteria(include_ingredients=["egg"], purpose=Purpose.USE_IT_UP)
    assert criteria.meal_type is None
    assert criteria.cuisine is None
    assert criteria.offset == 0
    assert criteria.number == 6
    assert criteria.diet is None
    assert criteria.intolerances == []
    assert criteria.exclude_ingredients == []


def test_sourced_recipe_wraps_models():
    recipe = RecipeCandidate(
        title="Omelette",
        cuisine="french",
        ingredients=[RecipeIngredient(name="egg")],
        method_gist="beat & fry",
    )
    nutrition = NutritionScore(
        health_score=70,
        effort="easy",
        est_minutes=10,
        rationale="x",
    )
    sourced = SourcedRecipe(recipe=recipe, nutrition=nutrition, external_id="123")
    assert sourced.external_id == "123"
    assert sourced.recipe.title == "Omelette"
