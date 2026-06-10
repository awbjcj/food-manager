from __future__ import annotations

import logging
from typing import Optional, Protocol

from app.cook_models import (
    NutritionScore,
    Purpose,
    RecipeCandidate,
    RecipeCriteria,
    RecipeIngredient,
    SourcedRecipe,
)
from app.profile_service import FoodProfile

log = logging.getLogger(__name__)

MEAL_TYPE_TO_SPOON = {
    "Dinner": "main course",
    "Lunch": "main course",
    "Breakfast": "breakfast",
    "Dessert": "dessert",
    "Snack": "snack",
}
_QUICK_MINUTES = 30
_PURPOSE_SORT = {
    Purpose.USE_IT_UP: "max-used-ingredients",
    Purpose.HEALTHY: "healthiness",
    Purpose.COMFORT: "popularity",
}


def build_criteria(
    *,
    include_ingredients: list[str],
    meal_type: Optional[str],
    cuisine: Optional[str],
    purpose: Purpose,
    profile: FoodProfile,
    offset: int,
    number: int = 6,
) -> RecipeCriteria:
    meal = None if meal_type in (None, "Surprise me") else meal_type
    cuisine_value = None if cuisine in (None, "Surprise me", "Any") else cuisine
    max_ready = _QUICK_MINUTES if purpose == Purpose.QUICK else profile.max_cook_minutes
    diet = None if profile.diet in (None, "", "none", "omnivore") else profile.diet
    return RecipeCriteria(
        include_ingredients=list(include_ingredients),
        purpose=purpose,
        meal_type=meal,
        cuisine=cuisine_value,
        diet=diet,
        intolerances=list(profile.exclusions),
        exclude_ingredients=list(profile.exclusions),
        max_ready_minutes=max_ready,
        number=number,
        offset=offset,
    )


def spoonacular_params(criteria: RecipeCriteria, *, api_key: str) -> dict:
    params: dict = {
        "apiKey": api_key,
        "number": criteria.number,
        "offset": criteria.offset,
        "addRecipeInformation": "true",
        "addRecipeNutrition": "true",
        "fillIngredients": "true",
    }
    if criteria.include_ingredients:
        params["includeIngredients"] = ",".join(criteria.include_ingredients)
    if criteria.meal_type:
        params["type"] = MEAL_TYPE_TO_SPOON.get(criteria.meal_type, "main course")
    if criteria.cuisine:
        params["cuisine"] = criteria.cuisine
    if criteria.diet:
        params["diet"] = criteria.diet
    if criteria.intolerances:
        params["intolerances"] = ",".join(criteria.intolerances)
    if criteria.exclude_ingredients:
        params["excludeIngredients"] = ",".join(criteria.exclude_ingredients)
    if criteria.max_ready_minutes:
        params["maxReadyTime"] = criteria.max_ready_minutes
    sort = _PURPOSE_SORT.get(criteria.purpose)
    if sort:
        params["sort"] = sort
    return params


_SPOON_URL = "https://api.spoonacular.com/recipes/complexSearch"


def _effort_for(minutes: Optional[int]) -> str:
    if minutes is None:
        return "medium"
    if minutes <= 20:
        return "easy"
    if minutes <= 45:
        return "medium"
    return "hard"


def _nutrition_rationale(raw: dict) -> str:
    nutrients = (raw.get("nutrition") or {}).get("nutrients") or []
    wanted = {"Calories": None, "Protein": None, "Fat": None, "Carbohydrates": None}
    for nutrient in nutrients:
        if nutrient.get("name") in wanted:
            wanted[nutrient["name"]] = (
                f"{round(nutrient.get('amount', 0))}{nutrient.get('unit', '')}"
            )
    parts = [f"{name} {value}" for name, value in wanted.items() if value]
    return ", ".join(parts) if parts else "nutrition from recipe database"


def map_spoonacular(payload: dict) -> list[SourcedRecipe]:
    out: list[SourcedRecipe] = []
    for raw in payload.get("results", []):
        ingredients = [
            RecipeIngredient(
                name=ingredient.get("name", ""),
                qty=ingredient.get("amount"),
                unit=ingredient.get("unit") or None,
            )
            for ingredient in (raw.get("extendedIngredients") or [])
            if ingredient.get("name")
        ]
        steps = [
            step.get("step", "")
            for block in (raw.get("analyzedInstructions") or [])
            for step in (block.get("steps") or [])
        ]
        method = " ".join(steps).strip()[:500] or "See source for full method."
        cuisines = raw.get("cuisines") or []
        minutes = raw.get("readyInMinutes")
        recipe = RecipeCandidate(
            title=raw.get("title", "Untitled"),
            cuisine=cuisines[0] if cuisines else "various",
            source_url=raw.get("sourceUrl"),
            ingredients=ingredients or [RecipeIngredient(name="(see source)")],
            method_gist=method,
            deliciousness=max(
                0.0,
                min(
                    1.0,
                    (50.0 if raw.get("spoonacularScore") is None else raw["spoonacularScore"])
                    / 100.0,
                ),
            ),
        )
        nutrition = NutritionScore(
            health_score=int(50 if raw.get("healthScore") is None else raw["healthScore"]),
            effort=_effort_for(minutes),
            est_minutes=int(minutes) if minutes else 30,
            rationale=_nutrition_rationale(raw),
        )
        raw_id = raw.get("id")
        out.append(
            SourcedRecipe(
                recipe=recipe,
                nutrition=nutrition,
                external_id=None if raw_id is None else str(raw_id),
            )
        )
    return out


class RecipeSource(Protocol):
    def available(self) -> bool: ...
    async def search(
        self, criteria: RecipeCriteria
    ) -> tuple[list[SourcedRecipe], Optional[int]]: ...


class SpoonacularSource:
    def __init__(self, *, http, api_key: Optional[str], timeout: float = 12.0):
        self._http = http
        self._api_key = api_key
        self._timeout = timeout

    def available(self) -> bool:
        return bool(self._api_key)

    async def search(
        self, criteria: RecipeCriteria
    ) -> tuple[list[SourcedRecipe], Optional[int]]:
        if not self._api_key:
            return [], None
        try:
            response = await self._http.get(
                _SPOON_URL,
                params=spoonacular_params(criteria, api_key=self._api_key),
                timeout=self._timeout,
            )
            response.raise_for_status()
            return map_spoonacular(response.json()), None
        except Exception as exc:
            log.warning(
                "spoonacular_search_failed",
                extra={"error_class": type(exc).__name__},
            )
            return [], None


_MEALDB_FILTER_URL = "https://www.themealdb.com/api/json/v1/1/filter.php"
_MEALDB_LOOKUP_URL = "https://www.themealdb.com/api/json/v1/1/lookup.php"


def _mealdb_ingredients(meal: dict) -> list[RecipeIngredient]:
    out: list[RecipeIngredient] = []
    for index in range(1, 21):
        name = (meal.get(f"strIngredient{index}") or "").strip()
        if not name:
            continue
        measure = (meal.get(f"strMeasure{index}") or "").strip() or None
        out.append(RecipeIngredient(name=name, unit=measure))
    return out


class TheMealDbSource:
    def __init__(self, *, http, timeout: float = 12.0):
        self._http = http
        self._timeout = timeout

    def available(self) -> bool:
        return True

    async def search(
        self, criteria: RecipeCriteria
    ) -> tuple[list[SourcedRecipe], Optional[int]]:
        if not criteria.include_ingredients:
            return [], None
        try:
            main = criteria.include_ingredients[0]
            filtered = await self._http.get(
                _MEALDB_FILTER_URL,
                params={"i": main},
                timeout=self._timeout,
            )
            filtered.raise_for_status()
            meals = (filtered.json() or {}).get("meals") or []
            out: list[SourcedRecipe] = []
            for stub in meals[: criteria.number]:
                lookup = await self._http.get(
                    _MEALDB_LOOKUP_URL,
                    params={"i": stub["idMeal"]},
                    timeout=self._timeout,
                )
                lookup.raise_for_status()
                detail = ((lookup.json() or {}).get("meals") or [None])[0]
                if not detail:
                    continue
                out.append(self._to_sourced(detail))
            return out, None
        except Exception as exc:
            log.warning(
                "themealdb_search_failed",
                extra={"error_class": type(exc).__name__},
            )
            return [], None

    def _to_sourced(self, meal: dict) -> SourcedRecipe:
        recipe = RecipeCandidate(
            title=meal.get("strMeal", "Untitled"),
            cuisine=meal.get("strArea") or "various",
            source_url=meal.get("strSource") or meal.get("strYoutube"),
            ingredients=_mealdb_ingredients(meal)
            or [RecipeIngredient(name="(see source)")],
            method_gist=(meal.get("strInstructions") or "").strip()[:500]
            or "See source.",
            deliciousness=0.5,
        )
        nutrition = NutritionScore(
            health_score=50,
            effort="medium",
            est_minutes=30,
            rationale="nutrition unavailable",
        )
        return SourcedRecipe(
            recipe=recipe,
            nutrition=nutrition,
            external_id=f"mealdb:{meal.get('idMeal')}",
        )
