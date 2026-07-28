from __future__ import annotations

import hashlib
import json as _json
import logging
from typing import Protocol

from app.cook.logic import violates_exclusions
from app.cook.models import (
    Effort,
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
    meal_type: str | None,
    cuisine: str | None,
    purpose: Purpose,
    profile: FoodProfile,
    offset: int,
    number: int = 6,
    steering: str | None = None,
) -> RecipeCriteria:
    meal = None if meal_type in (None, "Surprise me") else meal_type
    cuisine_value = None if cuisine in (None, "Surprise me", "Any") else cuisine
    max_ready = profile.max_cook_minutes
    if purpose == Purpose.QUICK:
        max_ready = (
            _QUICK_MINUTES
            if max_ready is None
            else min(_QUICK_MINUTES, max_ready)
        )
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
        steering=steering,
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


def _effort_for(minutes: int | None) -> Effort:
    if minutes is None:
        return "medium"
    if minutes <= 20:
        return "easy"
    if minutes <= 45:
        return "medium"
    return "hard"


def _nutrition_rationale(raw: dict) -> str:
    nutrients = (raw.get("nutrition") or {}).get("nutrients") or []
    wanted: dict[str, str | None] = {
        "Calories": None,
        "Protein": None,
        "Fat": None,
        "Carbohydrates": None,
    }
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
            image_url=raw.get("image"),
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
        self,
        criteria: RecipeCriteria,
        *,
        remaining_cost_micros: int | None = None,
    ) -> tuple[list[SourcedRecipe], int | None]: ...


class SpoonacularSource:
    def __init__(self, *, http, api_key: str | None, timeout: float = 12.0):
        self._http = http
        self._api_key = api_key
        self._timeout = timeout

    def available(self) -> bool:
        return bool(self._api_key)

    async def search(
        self,
        criteria: RecipeCriteria,
        *,
        remaining_cost_micros: int | None = None,
    ) -> tuple[list[SourcedRecipe], int | None]:
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
        except Exception as exc:  # noqa: BLE001 - one source failing must not break the chain
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
        self,
        criteria: RecipeCriteria,
        *,
        remaining_cost_micros: int | None = None,
    ) -> tuple[list[SourcedRecipe], int | None]:
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
            page = meals[criteria.offset : criteria.offset + criteria.number]
            for stub in page:
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
        except Exception as exc:  # noqa: BLE001 - one source failing must not break the chain
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
            image_url=meal.get("strMealThumb"),
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


class LlmRecipeSource:
    """Last-resort source: the existing recipe LLM plus nutrition LLM."""

    def __init__(self, *, recipe_llm, nutrition_llm):
        self._recipe_llm = recipe_llm
        self._nutrition_llm = nutrition_llm

    def available(self) -> bool:
        return self._recipe_llm is not None and self._nutrition_llm is not None

    async def search(
        self,
        criteria: RecipeCriteria,
        *,
        remaining_cost_micros: int | None = None,
    ) -> tuple[list[SourcedRecipe], int | None]:
        if not self.available():
            return [], None
        payload: dict = {
            "ingredients": criteria.include_ingredients,
            "meal_type": criteria.meal_type,
            "cuisine": criteria.cuisine,
            "purpose": criteria.purpose.value,
            "must_avoid": criteria.exclude_ingredients,
            "diet": criteria.diet,
            "max_ready_minutes": criteria.max_ready_minutes,
            "number": criteria.number,
            "offset": criteria.offset,
        }
        if criteria.steering:
            payload["household_taste"] = criteria.steering
        prompt = _json.dumps(payload, sort_keys=True)
        total: int | None = None
        recipes, recipe_cost = await self._recipe_llm.fetch_recipes(prompt=prompt)
        total = _add_known_cost(total, recipe_cost)
        if _over_budget(total, remaining_cost_micros):
            return [], total
        candidates = [
            candidate
            for candidate in recipes.candidates
            if not violates_exclusions(
                [ingredient.name for ingredient in candidate.ingredients],
                exclusions=criteria.exclude_ingredients,
            )
        ]
        if recipes.candidates and not candidates and criteria.exclude_ingredients:
            violated = sorted(
                {
                    ingredient.name
                    for candidate in recipes.candidates
                    for ingredient in candidate.ingredients
                    if violates_exclusions(
                        [ingredient.name],
                        exclusions=criteria.exclude_ingredients,
                    )
                }
            )
            regenerate_payload: dict = {
                "ingredients": criteria.include_ingredients,
                "meal_type": criteria.meal_type,
                "cuisine": criteria.cuisine,
                "purpose": criteria.purpose.value,
                "must_avoid": criteria.exclude_ingredients,
                "violated_ingredients": violated,
                "diet": criteria.diet,
                "max_ready_minutes": criteria.max_ready_minutes,
                "number": criteria.number,
                "offset": criteria.offset,
            }
            if criteria.steering:
                regenerate_payload["household_taste"] = criteria.steering
            regenerated, regen_cost = await self._recipe_llm.fetch_recipes(
                prompt=_json.dumps(
                    regenerate_payload,
                    sort_keys=True,
                )
            )
            total = _add_known_cost(total, regen_cost)
            if _over_budget(total, remaining_cost_micros):
                return [], total
            candidates = [
                candidate
                for candidate in regenerated.candidates
                if not violates_exclusions(
                    [ingredient.name for ingredient in candidate.ingredients],
                    exclusions=criteria.exclude_ingredients,
                )
            ]
        if not candidates:
            return [], total
        nutrition, nutrition_cost = await self._nutrition_llm.score(
            prompt=_json.dumps(
                {"candidates": [candidate.model_dump() for candidate in candidates]},
                sort_keys=True,
            )
        )
        total = _add_known_cost(total, nutrition_cost)
        out: list[SourcedRecipe] = []
        for candidate, score in zip(candidates, nutrition.scores):
            out.append(
                SourcedRecipe(
                    recipe=candidate,
                    nutrition=score,
                    external_id=_llm_external_id(candidate),
                )
            )
        return out, total


class ChainedRecipeSource:
    def __init__(self, sources: list):
        self._sources = sources

    def available(self) -> bool:
        return any(source.available() for source in self._sources)

    async def search(
        self,
        criteria: RecipeCriteria,
        *,
        remaining_cost_micros: int | None = None,
    ) -> tuple[list[SourcedRecipe], int | None]:
        total: int | None = None
        for source in self._sources:
            if not source.available():
                continue
            source_budget = _remaining_budget(remaining_cost_micros, total)
            try:
                recipes, cost = await source.search(
                    criteria,
                    remaining_cost_micros=source_budget,
                )
            except Exception as exc:  # noqa: BLE001 - one source failing must not break the chain
                log.warning(
                    "recipe_source_failed",
                    extra={
                        "source": type(source).__name__,
                        "error_class": type(exc).__name__,
                    },
                )
                continue
            total = _add_known_cost(total, cost)
            safe = [
                recipe
                for recipe in recipes
                if not violates_exclusions(
                    [ingredient.name for ingredient in recipe.recipe.ingredients],
                    exclusions=criteria.exclude_ingredients,
                )
            ]
            if safe:
                return safe, total
            if _over_budget(total, remaining_cost_micros):
                break
        return [], total


def _add_known_cost(total: int | None, cost: int | None) -> int | None:
    if not cost:
        return total
    return (total or 0) + cost


def _over_budget(cost: int | None, limit: int | None) -> bool:
    return limit is not None and cost is not None and cost > limit


def _remaining_budget(limit: int | None, spent: int | None) -> int | None:
    if limit is None:
        return None
    return max(0, limit - (spent or 0))


def _llm_external_id(candidate: RecipeCandidate) -> str:
    payload = {
        "title": candidate.title.strip().casefold(),
        "source_url": candidate.source_url or "",
        "ingredients": sorted(
            ingredient.name.strip().casefold() for ingredient in candidate.ingredients
        ),
    }
    digest = hashlib.sha256(
        _json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:20]
    return f"llm:{digest}"
