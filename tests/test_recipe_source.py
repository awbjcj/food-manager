from app.cook_models import Purpose, RecipeCriteria
from app.profile_service import FoodProfile
from app.recipe_source import MEAL_TYPE_TO_SPOON, build_criteria, spoonacular_params


def _profile(**kwargs):
    base = {
        "diet": "none",
        "exclusions": [],
        "preferred_cuisines": [],
        "max_cook_minutes": None,
        "household_size": 2,
        "note": "",
    }
    base.update(kwargs)
    return FoodProfile(**base)


def test_build_criteria_maps_purpose_and_profile():
    criteria = build_criteria(
        include_ingredients=["chicken", "spinach"],
        meal_type="Dinner",
        cuisine="Italian",
        purpose=Purpose.QUICK,
        profile=_profile(exclusions=["peanut"]),
        offset=6,
    )
    assert criteria.include_ingredients == ["chicken", "spinach"]
    assert criteria.meal_type == "Dinner"
    assert criteria.cuisine == "Italian"
    assert criteria.max_ready_minutes == 30
    assert "peanut" in criteria.exclude_ingredients
    assert criteria.offset == 6


def test_build_criteria_surprise_clears_filters():
    criteria = build_criteria(
        include_ingredients=["egg"],
        meal_type="Surprise me",
        cuisine="Surprise me",
        purpose=Purpose.SURPRISE,
        profile=_profile(),
        offset=0,
    )
    assert criteria.cuisine is None
    assert criteria.meal_type is None
    assert criteria.max_ready_minutes is None


def test_spoonacular_params():
    criteria = RecipeCriteria(
        include_ingredients=["chicken", "spinach"],
        purpose=Purpose.USE_IT_UP,
        meal_type="Dinner",
        cuisine="Italian",
        diet="vegetarian",
        intolerances=["peanut"],
        exclude_ingredients=["peanut"],
        number=6,
        offset=6,
    )
    params = spoonacular_params(criteria, api_key="K")
    assert params["includeIngredients"] == "chicken,spinach"
    assert params["type"] == MEAL_TYPE_TO_SPOON["Dinner"] == "main course"
    assert params["cuisine"] == "Italian"
    assert params["diet"] == "vegetarian"
    assert params["intolerances"] == "peanut"
    assert params["sort"] == "max-used-ingredients"
    assert params["number"] == 6
    assert params["offset"] == 6
    assert params["addRecipeNutrition"] == "true"
    assert params["addRecipeInformation"] == "true"
    assert params["apiKey"] == "K"
