from __future__ import annotations

import json
import logging
from datetime import date

from sqlmodel import Session

from app.cook_logic import (
    blended_score,
    expiry_utilization,
    shopping_list,
    violates_exclusions,
)
from app.cook_llm import NutritionLLMClient, RecipeLLMClient, SelectionLLMClient
from app.cook_models import RecipeCandidate, ScoredCandidate
from app.cook_session_service import accrue_cost
from app.models import CookSession, PantryItem
from app.pantry_service import ListFilter, list_active
from app.profile_service import FoodProfile

log = logging.getLogger(__name__)

MIN_USABLE_ITEMS = 3
URGENT_DAYS = 5
COOK_COST_CEILING_MICROS = 100_000


class NotEnoughItems(Exception):
    pass


def _over_ceiling(cook: CookSession) -> bool:
    return (cook.llm_cost_micros_usd or 0) > COOK_COST_CEILING_MICROS


def _ingredient_names(candidate: RecipeCandidate) -> list[str]:
    return [ingredient.name for ingredient in candidate.ingredients]


def _item_payload(item: PantryItem, *, today: date) -> dict:
    return {
        "id": item.id,
        "name": item.normalized_name,
        "raw_name": item.raw_name,
        "category": item.category,
        "qty": item.qty,
        "unit": item.unit,
        "expires_on": item.expires_on.isoformat(),
        "days_to_expiry": (item.expires_on - today).days,
    }


def _prompt_json(**payload) -> str:
    return json.dumps(payload, sort_keys=True)


def _safe_candidates(
    candidates: list[RecipeCandidate], *, profile: FoodProfile
) -> list[RecipeCandidate]:
    return [
        candidate
        for candidate in candidates
        if not violates_exclusions(
            _ingredient_names(candidate),
            exclusions=profile.exclusions,
        )
    ]


async def run_cook(
    session: Session,
    *,
    cook: CookSession,
    profile: FoodProfile,
    selection_llm: SelectionLLMClient,
    recipe_llm: RecipeLLMClient,
    nutrition_llm: NutritionLLMClient,
    today: date,
) -> list[ScoredCandidate]:
    active_items = [
        item
        for item in list_active(session, user_id=cook.user_id, f=ListFilter.default(), today=today)
        if item.expires_on >= today
    ]
    if len(active_items) < MIN_USABLE_ITEMS:
        raise NotEnoughItems("not enough active pantry items to cook")

    item_by_id = {item.id: item for item in active_items if item.id is not None}
    profile_json = profile.model_dump()
    selection_prompt = _prompt_json(
        items=[_item_payload(item, today=today) for item in active_items],
        meal_type=cook.meal_type,
        cuisine=cook.cuisine,
        profile=profile_json,
        today=today.isoformat(),
    )
    selected, selection_cost = await selection_llm.select_items(prompt=selection_prompt)
    accrue_cost(session, cook=cook, add_micros=selection_cost)

    selected_ids = [item_id for item_id in selected.item_ids if item_id in item_by_id]
    if not selected_ids:
        selected_ids = list(item_by_id)
    cook.selected_item_ids = json.dumps(selected_ids)
    session.add(cook)
    session.commit()

    if _over_ceiling(cook):
        log.warning("cook_cost_ceiling_after_selection", extra={"cook_id": cook.id})
        return []

    selected_items = [item_by_id[item_id] for item_id in selected_ids]
    recipe_prompt = _prompt_json(
        ingredients=[_item_payload(item, today=today) for item in selected_items],
        meal_type=cook.meal_type,
        cuisine=cook.cuisine,
        profile=profile_json,
        today=today.isoformat(),
    )
    recipes, recipe_cost = await recipe_llm.fetch_recipes(prompt=recipe_prompt)
    accrue_cost(session, cook=cook, add_micros=recipe_cost)
    if _over_ceiling(cook):
        log.warning("cook_cost_ceiling_after_recipe", extra={"cook_id": cook.id})
        return []

    safe_candidates = _safe_candidates(recipes.candidates, profile=profile)

    if recipes.candidates and not safe_candidates:
        violated_ingredients = sorted(
            {
                name
                for candidate in recipes.candidates
                for name in _ingredient_names(candidate)
                if violates_exclusions([name], exclusions=profile.exclusions)
            }
        )
        regenerate_prompt = _prompt_json(
            ingredients=[_item_payload(item, today=today) for item in selected_items],
            meal_type=cook.meal_type,
            cuisine=cook.cuisine,
            profile=profile_json,
            must_avoid=profile.exclusions,
            violated_ingredients=violated_ingredients,
            today=today.isoformat(),
        )
        regenerated, regenerate_cost = await recipe_llm.fetch_recipes(
            prompt=regenerate_prompt
        )
        accrue_cost(session, cook=cook, add_micros=regenerate_cost)
        safe_candidates = _safe_candidates(regenerated.candidates, profile=profile)

    if not safe_candidates:
        return []

    if _over_ceiling(cook):
        log.warning("cook_cost_ceiling_after_regenerate", extra={"cook_id": cook.id})
        return []

    nutrition_prompt = _prompt_json(
        candidates=[candidate.model_dump() for candidate in safe_candidates],
        meal_type=cook.meal_type,
        cuisine=cook.cuisine,
        profile=profile_json,
    )
    nutrition, nutrition_cost = await nutrition_llm.score(prompt=nutrition_prompt)
    accrue_cost(session, cook=cook, add_micros=nutrition_cost)

    urgent_names = [
        item.normalized_name
        for item in selected_items
        if (item.expires_on - today).days <= URGENT_DAYS
    ]
    pantry_normalized = [item.normalized_name for item in active_items]
    if len(nutrition.scores) != len(safe_candidates):
        log.warning(
            "cook_nutrition_count_mismatch",
            extra={
                "cook_id": cook.id,
                "candidates": len(safe_candidates),
                "scores": len(nutrition.scores),
            },
        )
    scored: list[ScoredCandidate] = []
    for candidate, nutrition_score in zip(safe_candidates, nutrition.scores):
        ingredient_names = _ingredient_names(candidate)
        expiry_use = expiry_utilization(
            recipe_names=ingredient_names,
            urgent_names=urgent_names,
        )
        scored.append(
            ScoredCandidate(
                recipe=candidate,
                nutrition=nutrition_score,
                expiry_use=expiry_use,
                final_score=blended_score(
                    health_0_1=nutrition_score.health_score / 100.0,
                    expiry_use=expiry_use,
                    deliciousness=candidate.deliciousness,
                ),
            )
        )

    scored.sort(key=lambda candidate: candidate.final_score, reverse=True)
    if scored:
        scored[0].shopping_list = shopping_list(
            recipe_names=_ingredient_names(scored[0].recipe),
            pantry_normalized=pantry_normalized,
        )

    cook.candidates_json = json.dumps([candidate.model_dump() for candidate in scored])
    cook.chosen_index = 0
    session.add(cook)
    session.commit()
    return scored
