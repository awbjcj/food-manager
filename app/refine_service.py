from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session

from app.cache import put_cached
from app.models import PantryItem, Receipt

# The shelf-life search contract now lives in its own module. Re-exported here so
# existing `from app.refine_service import ShelfLifeSearchResult, ...` callers and
# tests keep working; new code should import from app.shelf_life_search.
from app.shelf_life_search import (  # noqa: F401
    SEARCH_MIN_CONFIDENCE,
    SHELF_LIFE_DAYS_MAX,
    SHELF_LIFE_DAYS_MIN,
    ShelfLifeSearchClient,
    ShelfLifeSearchResult,
    resolve_search_days,
)


@dataclass(frozen=True)
class RefineResult:
    updated_ids: list[int]
    total_cost_micros: Optional[int]


async def refine_receipt_items(
    session: Session,
    search: ShelfLifeSearchClient,
    *,
    household_id: int,
    item_ids: list[int],
    today: date,
) -> RefineResult:
    from app.pantry_service import is_untouched  # local import to avoid circular at module level

    # Snapshot phase: pick the items eligible at the start and capture the plain
    # values the search needs, so the concurrent phase never touches the Session
    # (SQLAlchemy sessions are not safe to use across concurrent coroutines).
    # Items already touched here are skipped before any search is fired, so they
    # cost nothing.
    snapshots: list[tuple[int, str, Optional[str]]] = []
    for item_id in item_ids:
        item = session.get(PantryItem, item_id)
        if item is None or item.household_id != household_id or not is_untouched(item):
            continue
        if item.storage == "frozen":
            continue
        snapshots.append((item_id, item.raw_name, item.category))

    if not snapshots:
        session.commit()
        return RefineResult([], None)

    # Fan-out phase: one shelf-life lookup per eligible item, concurrently, so a
    # multi-item receipt costs ~one round-trip of latency instead of N.
    results = await asyncio.gather(
        *(
            search.lookup_shelf_life(name=name, category=category)
            for _item_id, name, category in snapshots
        )
    )

    # Apply phase: sequential and single-session. Re-check is_untouched so a user
    # edit committed during the search is never clobbered; accrue search cost even
    # when the write is skipped, matching the pre-concurrency behaviour.
    updated: list[int] = []
    total_cost = 0
    saw_cost = False
    for (item_id, _name, _category), result in zip(snapshots, results):
        if result.cost_micros_usd is not None:
            total_cost += result.cost_micros_usd
            saw_cost = True
        days = resolve_search_days(result)
        if days is None:
            continue
        item = session.get(PantryItem, item_id)
        if item is None:
            continue
        session.refresh(item)            # pick up any change committed during the await
        if not is_untouched(item):       # user acted on it mid-search -> don't clobber
            continue
        item.shelf_life_days = days
        item.shelf_life_source = "websearch"
        item.expires_on = item.purchased_on + timedelta(days=days)
        session.add(item)
        put_cached(
            session, household_id, item.normalized_name,
            days=days, category=item.category, confidence=result.confidence,
            source="llm", commit=False,
        )
        assert item.id is not None
        updated.append(item.id)
    session.commit()
    return RefineResult(updated, total_cost if saw_cost else None)


def _accrue_receipt_cost(session, receipt_id, add_micros):
    if not add_micros:
        return
    receipt = session.get(Receipt, receipt_id)
    if receipt is None:
        return
    receipt.llm_cost_micros_usd = (receipt.llm_cost_micros_usd or 0) + add_micros
    session.add(receipt)
    session.commit()


def _refresh_summary_from_db(session, summary):
    for idx, item_id in enumerate(summary.inserted_item_ids):
        item = session.get(PantryItem, item_id)
        if item is None:
            continue
        summary.inserted_item_expires_on[idx] = item.expires_on
        summary.inserted_item_shelf_life_days[idx] = item.shelf_life_days


async def run_receipt_refine(
    session_factory,
    search: ShelfLifeSearchClient,
    *,
    item_ids: list[int],
    summary,
    household_id: int,
    receipt_id: int,
    today: date,
) -> frozenset:
    with session_factory() as session:
        result = await refine_receipt_items(
            session, search, household_id=household_id, item_ids=item_ids, today=today,
        )
        receipt_exists = session.get(Receipt, receipt_id) is not None
        if receipt_exists:
            _accrue_receipt_cost(session, receipt_id, result.total_cost_micros)
        if not result.updated_ids or not receipt_exists:
            # No updates, or the receipt was fully undone (deleted) while the
            # web search was in flight — suppress the edit so we don't resurrect
            # the "Undone" message with a live Undo button.
            return frozenset()
        _refresh_summary_from_db(session, summary)
        return frozenset(result.updated_ids)


log = logging.getLogger(__name__)

SEARCH_SYSTEM_PROMPT = (
    "You research how long a grocery item stays good under normal home storage. "
    "Use web search to verify. Then reply with ONLY a JSON object: "
    '{"days": <int 1..730>, "confidence": <float 0..1>}. '
    "Use conservative estimates. No prose."
)


class AnthropicSearchClient(ShelfLifeSearchClient):
    def __init__(self, sdk, model: str, *, max_uses: int = 2):
        self._sdk = sdk
        self._model = model
        self._max_uses = max_uses

    async def lookup_shelf_life(self, *, name: str, category: Optional[str]) -> ShelfLifeSearchResult:
        from app.llm import _PRICE_MICROS_PER_TOKEN_BY_MODEL  # local import avoids cycle
        prompt = f"Item: {name}" + (f" (category: {category})" if category else "")
        try:
            msg = await self._sdk.messages.create(
                model=self._model,
                max_tokens=512,
                system=SEARCH_SYSTEM_PROMPT,
                tools=[{"type": "web_search_20250305", "name": "web_search",
                        "max_uses": self._max_uses}],
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            log.warning("search_transport_failed", extra={"error_class": type(exc).__name__})
            return ShelfLifeSearchResult(days=None, confidence=0.0, cost_micros_usd=None)

        price = _PRICE_MICROS_PER_TOKEN_BY_MODEL.get(self._model)
        cost = None
        usage = getattr(msg, "usage", None)
        if price is not None and usage is not None:
            cost = usage.input_tokens * price["input"] + usage.output_tokens * price["output"]

        text = "".join(
            b.text for b in msg.content if getattr(b, "type", None) == "text"
        ).strip()
        try:
            data = json.loads(text[text.index("{"): text.rindex("}") + 1])
            return ShelfLifeSearchResult(
                days=int(data["days"]), confidence=float(data["confidence"]),
                cost_micros_usd=cost,
            )
        except Exception as exc:
            log.warning("search_parse_failed", extra={"error_class": type(exc).__name__})
            return ShelfLifeSearchResult(days=None, confidence=0.0, cost_micros_usd=cost)
