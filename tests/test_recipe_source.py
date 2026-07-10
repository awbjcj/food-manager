import json

import pytest

from app.cook.models import (
    NutritionScore,
    NutritionScores,
    Purpose,
    RecipeCandidate,
    RecipeCandidates,
    RecipeCriteria,
    RecipeIngredient,
    SourcedRecipe,
)
from app.profile_service import FoodProfile
from app.cook.recipe_source import (
    ChainedRecipeSource,
    LlmRecipeSource,
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


def test_build_criteria_quick_keeps_stricter_profile_limit():
    criteria = build_criteria(
        include_ingredients=["egg"],
        meal_type="Breakfast",
        cuisine="American",
        purpose=Purpose.QUICK,
        profile=_profile(max_cook_minutes=15),
        offset=0,
    )

    assert criteria.max_ready_minutes == 15


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


class _MealDbPageHttp:
    def __init__(self):
        self.calls = []

    async def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if "filter.php" in url:
            return _FakeResp(
                {"meals": [{"idMeal": "1"}, {"idMeal": "2"}, {"idMeal": "3"}]}
            )
        meal_id = params["i"]
        return _FakeResp(
            {
                "meals": [
                    {
                        "idMeal": meal_id,
                        "strMeal": f"Meal {meal_id}",
                        "strArea": "American",
                        "strSource": f"https://ex.com/{meal_id}",
                        "strInstructions": "Cook.",
                        "strIngredient1": "chicken",
                        "strMeasure1": "1",
                    }
                ]
            }
        )


class _FakeRecipeLLM:
    def __init__(self):
        self.calls = []

    async def fetch_recipes(self, *, prompt):
        self.calls.append(prompt)
        return RecipeCandidates(
            candidates=[
                RecipeCandidate(
                    title="LLM Stew",
                    cuisine="rustic",
                    ingredients=[RecipeIngredient(name="carrot")],
                    method_gist="simmer",
                )
            ]
        ), 1000


class _FakeNutritionLLM:
    def __init__(self):
        self.calls = []

    async def score(self, *, prompt):
        self.calls.append(prompt)
        return NutritionScores(
            scores=[
                NutritionScore(
                    health_score=55,
                    effort="medium",
                    est_minutes=40,
                    rationale="ok",
                )
            ]
        ), 500


class _StubSource:
    def __init__(self, recipes, cost=0, ok=True):
        self._recipes = recipes
        self._cost = cost
        self._ok = ok

    def available(self):
        return self._ok

    async def search(self, criteria, *, remaining_cost_micros=None):
        return list(self._recipes), self._cost


class _RaisingSource:
    def available(self):
        return True

    async def search(self, criteria, *, remaining_cost_micros=None):
        raise RuntimeError("source unavailable")


class _SequenceRecipeLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def fetch_recipes(self, *, prompt):
        self.calls.append(prompt)
        return self._responses.pop(0)


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


@pytest.mark.asyncio
async def test_themealdb_source_applies_offset_before_page_size():
    http = _MealDbPageHttp()
    source = TheMealDbSource(http=http)

    recipes, _ = await source.search(
        RecipeCriteria(
            include_ingredients=["chicken"],
            purpose=Purpose.USE_IT_UP,
            number=1,
            offset=1,
        )
    )

    assert [recipe.external_id for recipe in recipes] == ["mealdb:2"]
    lookup_ids = [
        params["i"] for url, params in http.calls if "lookup.php" in url
    ]
    assert lookup_ids == ["2"]


@pytest.mark.asyncio
async def test_llm_recipe_source_pairs_recipe_and_nutrition():
    source = LlmRecipeSource(
        recipe_llm=_FakeRecipeLLM(),
        nutrition_llm=_FakeNutritionLLM(),
    )
    recipes, cost = await source.search(
        RecipeCriteria(include_ingredients=["carrot"], purpose=Purpose.SURPRISE)
    )
    assert recipes[0].recipe.title == "LLM Stew"
    assert recipes[0].nutrition.health_score == 55
    assert cost == 1500


@pytest.mark.asyncio
async def test_llm_recipe_source_forwards_page_and_builds_stable_external_id():
    first_recipe = _FakeRecipeLLM()
    first_source = LlmRecipeSource(
        recipe_llm=first_recipe,
        nutrition_llm=_FakeNutritionLLM(),
    )
    criteria = RecipeCriteria(
        include_ingredients=["carrot"],
        purpose=Purpose.SURPRISE,
        number=4,
        offset=8,
    )

    first, _ = await first_source.search(criteria)
    second, _ = await LlmRecipeSource(
        recipe_llm=_FakeRecipeLLM(),
        nutrition_llm=_FakeNutritionLLM(),
    ).search(criteria)

    prompt = json.loads(first_recipe.calls[0])
    assert prompt["number"] == 4
    assert prompt["offset"] == 8
    assert first[0].external_id is not None
    assert first[0].external_id.startswith("llm:")
    assert first[0].external_id == second[0].external_id


@pytest.mark.asyncio
async def test_llm_recipe_source_includes_household_taste_when_steering_set():
    recipe_llm = _FakeRecipeLLM()
    source = LlmRecipeSource(recipe_llm=recipe_llm, nutrition_llm=_FakeNutritionLLM())
    criteria = RecipeCriteria(
        include_ingredients=["carrot"], purpose=Purpose.SURPRISE, steering="likes thai",
    )
    await source.search(criteria)
    prompt = json.loads(recipe_llm.calls[0])
    assert prompt["household_taste"] == "likes thai"


@pytest.mark.asyncio
async def test_llm_recipe_source_omits_household_taste_when_no_steering():
    recipe_llm = _FakeRecipeLLM()
    source = LlmRecipeSource(recipe_llm=recipe_llm, nutrition_llm=_FakeNutritionLLM())
    criteria = RecipeCriteria(include_ingredients=["carrot"], purpose=Purpose.SURPRISE)
    await source.search(criteria)
    prompt = json.loads(recipe_llm.calls[0])
    assert "household_taste" not in prompt


@pytest.mark.asyncio
async def test_llm_recipe_source_budget_stops_before_nutrition():
    nutrition = _FakeNutritionLLM()
    source = LlmRecipeSource(
        recipe_llm=_FakeRecipeLLM(),
        nutrition_llm=nutrition,
    )

    recipes, cost = await source.search(
        RecipeCriteria(include_ingredients=["carrot"], purpose=Purpose.SURPRISE),
        remaining_cost_micros=999,
    )

    assert recipes == []
    assert cost == 1000
    assert nutrition.calls == []


@pytest.mark.asyncio
async def test_llm_recipe_source_budget_stops_before_exclusion_regeneration():
    unsafe = RecipeCandidates(
        candidates=[
            RecipeCandidate(
                title="Peanut Stew",
                cuisine="rustic",
                ingredients=[RecipeIngredient(name="peanut")],
                method_gist="simmer",
            )
        ]
    )
    recipe_llm = _SequenceRecipeLLM([(unsafe, 1000), (unsafe, 1000)])
    nutrition = _FakeNutritionLLM()
    source = LlmRecipeSource(recipe_llm=recipe_llm, nutrition_llm=nutrition)

    recipes, cost = await source.search(
        RecipeCriteria(
            include_ingredients=["carrot"],
            purpose=Purpose.SURPRISE,
            exclude_ingredients=["peanut"],
        ),
        remaining_cost_micros=999,
    )

    assert recipes == []
    assert cost == 1000
    assert len(recipe_llm.calls) == 1
    assert nutrition.calls == []


@pytest.mark.asyncio
async def test_chain_uses_first_nonempty():
    first = _StubSource([], 0)
    second = _StubSource(
        [
            SourcedRecipe(
                recipe=RecipeCandidate(
                    title="B",
                    cuisine="x",
                    ingredients=[RecipeIngredient(name="y")],
                    method_gist="z",
                ),
                nutrition=NutritionScore(
                    health_score=1,
                    effort="easy",
                    est_minutes=1,
                    rationale="r",
                ),
            )
        ],
        7,
    )
    chain = ChainedRecipeSource([first, second])
    recipes, cost = await chain.search(
        RecipeCriteria(include_ingredients=["y"], purpose=Purpose.SURPRISE)
    )
    assert len(recipes) == 1
    assert recipes[0].recipe.title == "B"
    assert cost == 7


@pytest.mark.asyncio
async def test_chain_skips_unavailable():
    chain = ChainedRecipeSource([
        _StubSource([], ok=False),
        _StubSource([], ok=True),
    ])
    recipes, cost = await chain.search(
        RecipeCriteria(include_ingredients=["y"], purpose=Purpose.SURPRISE)
    )
    assert recipes == []
    assert cost is None


@pytest.mark.asyncio
async def test_chain_filters_unsafe_primary_and_uses_safe_fallback():
    unsafe = SourcedRecipe(
        recipe=RecipeCandidate(
            title="Peanut Dish",
            cuisine="x",
            ingredients=[RecipeIngredient(name="peanut")],
            method_gist="cook",
        ),
        nutrition=NutritionScore(
            health_score=50,
            effort="easy",
            est_minutes=10,
            rationale="r",
        ),
        external_id="unsafe",
    )
    safe = SourcedRecipe(
        recipe=RecipeCandidate(
            title="Safe Dish",
            cuisine="x",
            ingredients=[RecipeIngredient(name="carrot")],
            method_gist="cook",
        ),
        nutrition=NutritionScore(
            health_score=50,
            effort="easy",
            est_minutes=10,
            rationale="r",
        ),
        external_id="safe",
    )
    chain = ChainedRecipeSource([
        _StubSource([unsafe], cost=3),
        _StubSource([safe], cost=7),
    ])

    recipes, cost = await chain.search(
        RecipeCriteria(
            include_ingredients=["carrot"],
            purpose=Purpose.SURPRISE,
            exclude_ingredients=["peanut"],
        )
    )

    assert [recipe.external_id for recipe in recipes] == ["safe"]
    assert cost == 10


@pytest.mark.asyncio
async def test_chain_catches_source_exception_and_accumulates_empty_attempt_cost():
    safe = SourcedRecipe(
        recipe=RecipeCandidate(
            title="Safe Dish",
            cuisine="x",
            ingredients=[RecipeIngredient(name="carrot")],
            method_gist="cook",
        ),
        nutrition=NutritionScore(
            health_score=50,
            effort="easy",
            est_minutes=10,
            rationale="r",
        ),
        external_id="safe",
    )
    chain = ChainedRecipeSource([
        _StubSource([], cost=3),
        _RaisingSource(),
        _StubSource([safe], cost=7),
    ])

    recipes, cost = await chain.search(
        RecipeCriteria(include_ingredients=["carrot"], purpose=Purpose.SURPRISE)
    )

    assert [recipe.external_id for recipe in recipes] == ["safe"]
    assert cost == 10
