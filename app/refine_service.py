from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Protocol

from sqlmodel import Session

from app.cache import put_cached
from app.models import PantryItem


SEARCH_MIN_CONFIDENCE = 0.7
SHELF_LIFE_DAYS_MIN = 1
SHELF_LIFE_DAYS_MAX = 730


@dataclass(frozen=True)
class ShelfLifeSearchResult:
    days: Optional[int]
    confidence: float
    cost_micros_usd: Optional[int]


class ShelfLifeSearchClient(Protocol):
    async def lookup_shelf_life(
        self, *, name: str, category: Optional[str]
    ) -> ShelfLifeSearchResult: ...


def resolve_search_days(result: ShelfLifeSearchResult) -> Optional[int]:
    if result.confidence < SEARCH_MIN_CONFIDENCE:
        return None
    if result.days is None:
        return None
    if not (SHELF_LIFE_DAYS_MIN <= result.days <= SHELF_LIFE_DAYS_MAX):
        return None
    return result.days


@dataclass(frozen=True)
class RefineResult:
    updated_ids: list[int]
    total_cost_micros: Optional[int]


async def refine_receipt_items(
    session: Session,
    search: ShelfLifeSearchClient,
    *,
    user_id: int,
    item_ids: list[int],
    today: date,
) -> RefineResult:
    from app.pantry_service import is_untouched  # local import to avoid circular at module level

    updated: list[int] = []
    total_cost = 0
    saw_cost = False
    for item_id in item_ids:
        item = session.get(PantryItem, item_id)
        if item is None or item.user_id != user_id or not is_untouched(item):
            continue
        result = await search.lookup_shelf_life(name=item.raw_name, category=item.category)
        if result.cost_micros_usd is not None:
            total_cost += result.cost_micros_usd
            saw_cost = True
        days = resolve_search_days(result)
        if days is None:
            continue
        item.shelf_life_days = days
        item.shelf_life_source = "websearch"
        item.expires_on = item.purchased_on + timedelta(days=days)
        session.add(item)
        put_cached(
            session, user_id, item.normalized_name,
            days=days, category=item.category, confidence=result.confidence,
            source="llm", commit=False,
        )
        assert item.id is not None
        updated.append(item.id)
    session.commit()
    return RefineResult(updated, total_cost if saw_cost else None)
