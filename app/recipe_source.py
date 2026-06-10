from __future__ import annotations

import logging
from typing import Optional

from app.cook_models import Purpose, RecipeCriteria
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
