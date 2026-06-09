"""Resolve frozen-storage shelf life for a food item."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from sqlmodel import Session

from app.cache import get_cached, put_cached
from app.refine_service import ShelfLifeSearchClient, resolve_search_days

FROZEN_DEFAULT_DAYS = 90

FrozenSource = Literal["cache", "frozen_foodkeeper", "frozen_llm", "frozen_default"]

# Curated USDA FoodKeeper freezer-storage times, in days. Keyed by normalized
# food name and intentionally conservative where FoodKeeper gives a range.
_FOODKEEPER_FREEZER_DAYS: dict[str, int] = {
    "chicken": 270,
    "chicken breast": 270,
    "turkey": 270,
    "ground beef": 120,
    "beef": 240,
    "steak": 300,
    "pork": 180,
    "pork chops": 180,
    "bacon": 30,
    "sausage": 60,
    "ham": 60,
    "fish": 180,
    "salmon": 90,
    "shrimp": 270,
    "bread": 90,
    "butter": 270,
    "milk": 90,
    "cheese": 180,
    "ice cream": 60,
    "peas": 300,
    "vegetables": 300,
    "berries": 300,
    "fruit": 300,
    "hash browns": 365,
    "pizza": 60,
}


def frozen_cache_key(normalized_name: str) -> str:
    return f"frozen {_canonical_frozen_name(normalized_name)}"


def storage_cache_key(normalized_name: str, storage: str) -> str:
    if storage == "frozen":
        return frozen_cache_key(normalized_name)
    return normalized_name


def _canonical_frozen_name(normalized_name: str) -> str:
    value = normalized_name.strip()
    while value.startswith("frozen "):
        value = value[len("frozen "):].strip()
    return value


def _frozen_search_name(food_name: str) -> str:
    value = food_name.strip()
    if value.lower().startswith("frozen "):
        return value
    return f"frozen {value}"


def lookup_foodkeeper(normalized_name: str) -> Optional[int]:
    return _FOODKEEPER_FREEZER_DAYS.get(_canonical_frozen_name(normalized_name))


@dataclass(frozen=True)
class FrozenDecision:
    days: int
    source: FrozenSource
    cache_was_hit: bool


async def resolve_frozen_days(
    session: Session,
    *,
    household_id: int,
    normalized_name: str,
    food_name: str,
    search: Optional[ShelfLifeSearchClient] = None,
) -> FrozenDecision:
    key = frozen_cache_key(normalized_name)

    cached = get_cached(session, household_id, key)
    if cached is not None:
        return FrozenDecision(days=cached.days, source="cache", cache_was_hit=True)

    table_days = lookup_foodkeeper(normalized_name)
    if table_days is not None:
        put_cached(
            session,
            household_id,
            key,
            days=table_days,
            category=None,
            confidence=0.9,
            source="llm",
            commit=False,
        )
        return FrozenDecision(
            days=table_days,
            source="frozen_foodkeeper",
            cache_was_hit=False,
        )

    if search is not None:
        try:
            result = await search.lookup_shelf_life(
                name=_frozen_search_name(food_name),
                category=None,
            )
        except Exception:
            result = None
        days = resolve_search_days(result) if result is not None else None
        if days is not None:
            put_cached(
                session,
                household_id,
                key,
                days=days,
                category=None,
                confidence=result.confidence,
                source="llm",
                commit=False,
            )
            return FrozenDecision(
                days=days,
                source="frozen_llm",
                cache_was_hit=False,
            )

    put_cached(
        session,
        household_id,
        key,
        days=FROZEN_DEFAULT_DAYS,
        category=None,
        confidence=0.0,
        source="llm",
        commit=False,
    )
    return FrozenDecision(
        days=FROZEN_DEFAULT_DAYS,
        source="frozen_default",
        cache_was_hit=False,
    )
