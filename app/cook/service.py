from __future__ import annotations

import json
import logging
from datetime import date

from sqlmodel import Session

from app.cook.affinity import affinity, list_recent_signals, steering_summary
from app.cook.logic import (
    blended_score,
    expiry_utilization,
    shopping_list,
    violates_exclusions,
)
from app.cook.llm import SelectionLLMClient
from app.cook.models import Purpose, RecipeCandidate, ScoredCandidate
from app.cook.recipe_source import RecipeSource, build_criteria
from app.cook.session_service import accrue_cost
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


def _purpose_of(cook: CookSession) -> Purpose:
    try:
        return Purpose(cook.purpose) if cook.purpose else Purpose.SURPRISE
    except ValueError:
        return Purpose.SURPRISE


def _remaining_cost_budget(cook: CookSession) -> int:
    return max(0, COOK_COST_CEILING_MICROS - (cook.llm_cost_micros_usd or 0))


def _stored_candidates(cook: CookSession) -> list[ScoredCandidate]:
    try:
        raw_cards = json.loads(cook.candidates_json or "[]")
    except (TypeError, ValueError):
        return []
    out: list[ScoredCandidate] = []
    for card in raw_cards:
        try:
            out.append(ScoredCandidate.model_validate(card))
        except ValueError:
            continue
    return out


def _shown_external_ids(cook: CookSession) -> set[str]:
    return {card.external_id for card in _stored_candidates(cook) if card.external_id}


def _score_sourced(
    sourced: list, *, selected_items: list[PantryItem], today: date, signals
) -> list[ScoredCandidate]:
    urgent_names = [
        item.normalized_name
        for item in selected_items
        if (item.expires_on - today).days <= URGENT_DAYS
    ]
    scored: list[ScoredCandidate] = []
    for sourced_recipe in sourced:
        ingredient_names = _ingredient_names(sourced_recipe.recipe)
        expiry_use = expiry_utilization(
            recipe_names=ingredient_names, urgent_names=urgent_names
        )
        scored.append(
            ScoredCandidate(
                recipe=sourced_recipe.recipe,
                nutrition=sourced_recipe.nutrition,
                expiry_use=expiry_use,
                external_id=sourced_recipe.external_id,
                final_score=blended_score(
                    health_0_1=sourced_recipe.nutrition.health_score / 100.0,
                    expiry_use=expiry_use,
                    deliciousness=sourced_recipe.recipe.deliciousness,
                    affinity_0_1=affinity(
                        cuisine=sourced_recipe.recipe.cuisine,
                        ingredient_names=ingredient_names,
                        signals=signals,
                    ),
                ),
            )
        )
    scored.sort(key=lambda candidate: candidate.final_score, reverse=True)
    return scored


def _assign_shopping_list(
    scored: list[ScoredCandidate], *, pantry_normalized: list[str]
) -> None:
    if scored:
        scored[0].shopping_list = shopping_list(
            recipe_names=_ingredient_names(scored[0].recipe),
            pantry_normalized=pantry_normalized,
        )


async def run_cook(
    session: Session,
    *,
    cook: CookSession,
    profile: FoodProfile,
    selection_llm: SelectionLLMClient,
    source: RecipeSource,
    today: date,
) -> list[ScoredCandidate]:
    active_items = [
        item
        for item in list_active(
            session, household_id=cook.household_id, f=ListFilter.default(), today=today
        )
        if item.expires_on >= today
    ]
    if len(active_items) < MIN_USABLE_ITEMS:
        raise NotEnoughItems("not enough active pantry items to cook")

    item_by_id = {item.id: item for item in active_items if item.id is not None}
    stored_ids = [
        item_id
        for item_id in json.loads(cook.selected_item_ids or "[]")
        if item_id in item_by_id
    ]
    if stored_ids:
        selected_ids = stored_ids
    else:
        profile_json = profile.model_dump()
        selection_prompt = _prompt_json(
            items=[_item_payload(item, today=today) for item in active_items],
            meal_type=cook.meal_type,
            cuisine=cook.cuisine,
            profile=profile_json,
            purpose=_purpose_of(cook).value,
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
    signals = list_recent_signals(session, household_id=cook.household_id)
    criteria = build_criteria(
        include_ingredients=[item.normalized_name for item in selected_items],
        meal_type=cook.meal_type,
        cuisine=cook.cuisine,
        purpose=_purpose_of(cook),
        profile=profile,
        offset=cook.search_offset or 0,
        steering=steering_summary(signals) or None,
    )
    sourced, source_cost = await source.search(
        criteria, remaining_cost_micros=_remaining_cost_budget(cook)
    )
    accrue_cost(session, cook=cook, add_micros=source_cost)

    safe = [
        sourced_recipe
        for sourced_recipe in sourced
        if not violates_exclusions(
            _ingredient_names(sourced_recipe.recipe), exclusions=profile.exclusions
        )
    ]
    if not safe:
        return []

    pantry_normalized = [item.normalized_name for item in active_items]
    scored = _score_sourced(safe, selected_items=selected_items, today=today, signals=signals)
    _assign_shopping_list(scored, pantry_normalized=pantry_normalized)

    cook.candidates_json = json.dumps([candidate.model_dump() for candidate in scored])
    cook.chosen_index = 0
    session.add(cook)
    session.commit()
    return scored


async def run_cook_more(
    session: Session,
    *,
    cook: CookSession,
    profile: FoodProfile,
    source: RecipeSource,
    today: date,
) -> list[ScoredCandidate]:
    """Regenerate: page past already-shown results, keeping old cards on failure."""
    if _over_ceiling(cook):
        log.warning("cook_cost_ceiling_before_more", extra={"cook_id": cook.id})
        return []

    active_items = [
        item
        for item in list_active(
            session, household_id=cook.household_id, f=ListFilter.default(), today=today
        )
        if item.expires_on >= today
    ]
    item_by_id = {item.id: item for item in active_items if item.id is not None}
    selected_ids = json.loads(cook.selected_item_ids or "[]")
    selected_items = [item_by_id[item_id] for item_id in selected_ids if item_id in item_by_id]
    if not selected_items:
        return []

    cook.search_offset = (cook.search_offset or 0) + 6
    signals = list_recent_signals(session, household_id=cook.household_id)
    criteria = build_criteria(
        include_ingredients=[item.normalized_name for item in selected_items],
        meal_type=cook.meal_type,
        cuisine=cook.cuisine,
        purpose=_purpose_of(cook),
        profile=profile,
        offset=cook.search_offset,
        steering=steering_summary(signals) or None,
    )
    sourced, cost = await source.search(
        criteria, remaining_cost_micros=_remaining_cost_budget(cook)
    )
    accrue_cost(session, cook=cook, add_micros=cost)
    session.add(cook)
    session.commit()

    if _over_ceiling(cook):
        log.warning("cook_cost_ceiling_after_more_search", extra={"cook_id": cook.id})
        return []

    shown = _shown_external_ids(cook)
    fresh = [
        sourced_recipe
        for sourced_recipe in sourced
        if (sourced_recipe.external_id is None or sourced_recipe.external_id not in shown)
        and not violates_exclusions(
            _ingredient_names(sourced_recipe.recipe), exclusions=profile.exclusions
        )
    ]
    if not fresh:
        return []

    pantry_normalized = [item.normalized_name for item in active_items]
    scored = _score_sourced(fresh, selected_items=selected_items, today=today, signals=signals)
    _assign_shopping_list(scored, pantry_normalized=pantry_normalized)

    cook.candidates_json = json.dumps(
        [candidate.model_dump() for candidate in [*scored, *_stored_candidates(cook)]]
    )
    cook.chosen_index = 0
    session.add(cook)
    session.commit()
    return scored
