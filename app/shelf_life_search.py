"""Shelf-life web search: the contract for looking up a food's shelf life online.

This is the interface (`ShelfLifeSearchClient`) and the value type
(`ShelfLifeSearchResult`) plus the rule that turns a raw search result into a
trusted number of days (`resolve_search_days`). It lives apart from
`refine_service` — the implementation that *drives* a search over a receipt — so
the several consumers that only need the contract (correction, frozen/fridge
resolution, ingest, pantry) depend on the seam, not on the refinement driver.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

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
    """The trusted day count from a search result, or None to keep the estimate.

    Rejects low-confidence answers and out-of-range day counts so a noisy search
    never overwrites a reasonable estimate.
    """
    if result.confidence < SEARCH_MIN_CONFIDENCE:
        return None
    if result.days is None:
        return None
    if not (SHELF_LIFE_DAYS_MIN <= result.days <= SHELF_LIFE_DAYS_MAX):
        return None
    return result.days
