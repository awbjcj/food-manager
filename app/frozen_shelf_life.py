"""Resolve storage-specific shelf life (fridge / frozen) for a food item.

One resolver, parameterised by Storage State. Each non-default state carries its
own curated USDA FoodKeeper table, cache-key prefix, and search phrasing, then
shares the same cache -> table -> web-search -> default fallback chain.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlmodel import Session

from app.cache import get_cached, put_cached
from app.shelf_life_search import ShelfLifeSearchClient, resolve_search_days

FROZEN_DEFAULT_DAYS = 90
FRIDGE_DEFAULT_DAYS = 7

StorageState = Literal["fridge", "frozen"]
StorageSource = Literal[
    "cache",
    "frozen_foodkeeper",
    "frozen_llm",
    "frozen_default",
    "fridge_foodkeeper",
    "fridge_llm",
    "fridge_default",
]

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

# Curated USDA FoodKeeper refrigerator-storage times, in days. Conservative;
# these are the durations a food keeps once chilled, replacing the counter/pantry
# default shelf life used while the item sat out.
_FOODKEEPER_FRIDGE_DAYS: dict[str, int] = {
    "chicken": 2,
    "chicken breast": 2,
    "turkey": 2,
    "ground beef": 2,
    "beef": 5,
    "steak": 5,
    "pork": 5,
    "pork chops": 5,
    "bacon": 7,
    "sausage": 2,
    "ham": 7,
    "fish": 2,
    "salmon": 2,
    "shrimp": 2,
    "milk": 7,
    "eggs": 35,
    "butter": 30,
    "cheese": 14,
    "yogurt": 14,
    "bread": 14,
    "berries": 7,
    "strawberries": 7,
    "fruit": 14,
    "apples": 30,
    "carrots": 28,
    "broccoli": 5,
    "lettuce": 7,
    "spinach": 7,
    "tomatoes": 7,
    "vegetables": 7,
    "leftovers": 4,
}


@dataclass(frozen=True)
class _StateConfig:
    prefix: str  # cache-key + search-phrase prefix, e.g. "frozen" / "refrigerated"
    table: dict[str, int]
    default_days: int
    source_foodkeeper: StorageSource
    source_llm: StorageSource
    source_default: StorageSource


_STATE_CONFIG: dict[str, _StateConfig] = {
    "frozen": _StateConfig(
        prefix="frozen",
        table=_FOODKEEPER_FREEZER_DAYS,
        default_days=FROZEN_DEFAULT_DAYS,
        source_foodkeeper="frozen_foodkeeper",
        source_llm="frozen_llm",
        source_default="frozen_default",
    ),
    "fridge": _StateConfig(
        prefix="refrigerated",
        table=_FOODKEEPER_FRIDGE_DAYS,
        default_days=FRIDGE_DEFAULT_DAYS,
        source_foodkeeper="fridge_foodkeeper",
        source_llm="fridge_llm",
        source_default="fridge_default",
    ),
}

# Cache keys use a stable, search-independent prefix so a state's learned days
# never collide with the default (counter/pantry) shelf-life cache entry.
_CACHE_PREFIX: dict[str, str] = {"frozen": "frozen", "fridge": "fridge"}


def _strip_prefixes(value: str) -> str:
    value = value.strip()
    changed = True
    while changed:
        changed = False
        for token in ("frozen ", "refrigerated ", "fridge ", "chilled "):
            if value.startswith(token):
                value = value[len(token):].strip()
                changed = True
    return value


def _canonical_name(normalized_name: str) -> str:
    return _strip_prefixes(normalized_name)


def storage_cache_key(normalized_name: str, storage: str) -> str:
    prefix = _CACHE_PREFIX.get(storage)
    if prefix is None:
        return normalized_name
    return f"{prefix} {_canonical_name(normalized_name)}"


@dataclass(frozen=True)
class StorageDecision:
    days: int
    source: StorageSource
    cache_was_hit: bool


async def resolve_storage_days(
    session: Session,
    *,
    household_id: int,
    state: StorageState,
    normalized_name: str,
    food_name: str,
    search: ShelfLifeSearchClient | None = None,
) -> StorageDecision:
    """cache -> curated FoodKeeper table -> web search -> conservative default."""
    config = _STATE_CONFIG[state]
    key = storage_cache_key(normalized_name, state)

    cached = get_cached(session, household_id, key)
    if cached is not None:
        return StorageDecision(days=cached.days, source="cache", cache_was_hit=True)

    table_days = config.table.get(_canonical_name(normalized_name))
    if table_days is not None:
        put_cached(
            session, household_id, key,
            days=table_days, category=None, confidence=0.9,
            source="llm", commit=False,
        )
        return StorageDecision(
            days=table_days, source=config.source_foodkeeper, cache_was_hit=False
        )

    if search is not None:
        try:
            result = await search.lookup_shelf_life(
                name=_search_name(food_name, config.prefix), category=None
            )
        except Exception:  # noqa: BLE001 - search is best-effort, falls back to default
            result = None
        days = resolve_search_days(result) if result is not None else None
        if days is not None:
            assert result is not None
            put_cached(
                session, household_id, key,
                days=days, category=None, confidence=result.confidence,
                source="llm", commit=False,
            )
            return StorageDecision(
                days=days, source=config.source_llm, cache_was_hit=False
            )

    put_cached(
        session, household_id, key,
        days=config.default_days, category=None, confidence=0.0,
        source="llm", commit=False,
    )
    return StorageDecision(
        days=config.default_days, source=config.source_default, cache_was_hit=False
    )


async def resolve_frozen_days(
    session: Session,
    *,
    household_id: int,
    normalized_name: str,
    food_name: str,
    search: ShelfLifeSearchClient | None = None,
) -> StorageDecision:
    return await resolve_storage_days(
        session,
        household_id=household_id,
        state="frozen",
        normalized_name=normalized_name,
        food_name=food_name,
        search=search,
    )


def _search_name(food_name: str, prefix: str) -> str:
    value = food_name.strip()
    if value.lower().startswith(prefix):
        return value
    return f"{prefix} {value}"
