import pytest

from app.cook_models import Purpose, RecipeCriteria
from app.profile_service import FoodProfile
from app.recipe_source import (
    MEAL_TYPE_TO_SPOON,
    SpoonacularSource,
    TheMealDbSource,
    build_criteria,
    map_spoonacular,
    spoonacular_params,
)


_CANNED = {
    "results": [
        {
            "id": 111,
            "title": "Spinach Omelette",
            "sourceUrl": "https://ex.com/omelette",
            "cuisines": ["French"],
            "readyInMinutes": 15,
            "spoonacularScore": 82.0,
            "healthScore": 64,
            "extendedIngredients": [
                {"name": "egg", "amount": 2, "unit": ""},
                {"name": "spinach", "amount": 50, "unit": "g"},
            ],
            "analyzedInstructions": [
                {"steps": [{"step": "Beat eggs."}, {"step": "Fry with spinach."}]}
            ],
            "nutrition": {
                "nutrients": [
                    {"name": "Calories", "amount": 220, "unit": "kcal"},
                    {"name": "Protein", "amount": 14, "unit": "g"},
                ]
            },
        }
    ]
}

_MEALDB_FILTER = {"meals": [{"idMeal": "52772"}]}
_MEALDB_LOOKUP = {
    "meals": [
        {
            "idMeal": "52772",
            "strMeal": "Teriyaki Chicken",
            "strArea": "Japanese",
            "strSource": "https://ex.com/teriyaki",
            "strInstructions": "Marinate. Grill.",
            "strIngredient1": "chicken",
            "strMeasure1": "2",
            "strIngredient2": "soy",
            "strMeasure2": "1 tbsp",
            "strIngredient3": "",
            "strMeasure3": "",
        }
    ]
}


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


def test_map_spoonacular():
    out = map_spoonacular(_CANNED)
    assert len(out) == 1
    sourced = out[0]
    assert sourced.external_id == "111"
    assert sourced.recipe.title == "Spinach Omelette"
    assert sourced.recipe.source_url == "https://ex.com/omelette"
    assert sourced.recipe.cuisine == "French"
    assert abs(sourced.recipe.deliciousness - 0.82) < 1e-6
    assert sourced.nutrition.health_score == 64
    assert sourced.nutrition.est_minutes == 15
    assert sourced.nutrition.effort == "easy"
    assert "220" in sourced.nutrition.rationale


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")


class _FakeHttp:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self._response


class _SeqHttp:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        for fragment, payload in self.routes:
            if fragment in url:
                return _FakeResp(payload)
        return _FakeResp({"meals": None})


@pytest.mark.asyncio
async def test_spoonacular_source_search_maps_and_counts():
    source = SpoonacularSource(http=_FakeHttp(_FakeResp(_CANNED)), api_key="K")
    criteria = RecipeCriteria(include_ingredients=["egg"], purpose=Purpose.USE_IT_UP)
    recipes, cost = await source.search(criteria)
    assert len(recipes) == 1
    assert recipes[0].recipe.title == "Spinach Omelette"
    assert cost is None


@pytest.mark.asyncio
async def test_spoonacular_source_http_error_yields_empty():
    source = SpoonacularSource(http=_FakeHttp(_FakeResp({}, status=500)), api_key="K")
    recipes, cost = await source.search(
        RecipeCriteria(include_ingredients=["egg"], purpose=Purpose.USE_IT_UP)
    )
    assert recipes == []
    assert cost is None


@pytest.mark.asyncio
async def test_spoonacular_source_no_key_unavailable():
    source = SpoonacularSource(http=_FakeHttp(_FakeResp(_CANNED)), api_key=None)
    assert source.available() is False
    recipes, cost = await source.search(
        RecipeCriteria(include_ingredients=["egg"], purpose=Purpose.USE_IT_UP)
    )
    assert recipes == []
    assert cost is None


@pytest.mark.asyncio
async def test_themealdb_source_maps_recipe_without_nutrition():
    http = _SeqHttp([("filter.php", _MEALDB_FILTER), ("lookup.php", _MEALDB_LOOKUP)])
    source = TheMealDbSource(http=http)
    recipes, cost = await source.search(
        RecipeCriteria(include_ingredients=["chicken"], purpose=Purpose.USE_IT_UP)
    )
    assert recipes
    assert recipes[0].recipe.title == "Teriyaki Chicken"
    assert recipes[0].recipe.source_url == "https://ex.com/teriyaki"
    assert "chicken" in [ingredient.name for ingredient in recipes[0].recipe.ingredients]
    assert recipes[0].nutrition.rationale == "nutrition unavailable"
    assert cost is None


@pytest.mark.asyncio
async def test_themealdb_empty_yields_empty():
    http = _SeqHttp([("filter.php", {"meals": None})])
    source = TheMealDbSource(http=http)
    recipes, cost = await source.search(
        RecipeCriteria(include_ingredients=["unobtainium"], purpose=Purpose.USE_IT_UP)
    )
    assert recipes == []
    assert cost is None
