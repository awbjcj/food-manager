from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlmodel import Session

from app.cache import get_cached, put_cached, write_user_correction
from app.frozen_shelf_life import storage_cache_key
from app.i18n import LANGS
from app.llm import TextLLMClient
from app.models import PantryItem, ShelfLifeCache
from app.normalization import normalize
from app.shelf_life_defaults import lookup_default
from app.shelf_life_search import ShelfLifeSearchClient, resolve_search_days
from app.storage_state import shelf_life_origin
from app.translation_service import upsert_name_translations


class CorrectPayload(BaseModel):
    kind: Literal["correct"] = "correct"
    diff: dict[str, dict[str, Any] | None]
    name_translations: dict[str, str] = Field(default_factory=dict)
    cache_action: Literal["move", "add_new", "leave"]
    rationale: str
    confidence: float
    back_computed_days: bool = False


class AddPayload(BaseModel):
    kind: Literal["add"] = "add"
    name: str
    category: str | None = None
    qty: float = Field(default=1.0, gt=0)
    unit: str | None = None
    shelf_life_days: int = Field(ge=1, le=730)
    expires_on: date
    shelf_life_source: Literal["user_correction", "cache", "manual_fallback", "llm", "websearch"]
    ingest_shelf_life_source: Literal[
        "manual_user_hint", "cache", "manual_fallback", "llm"
    ]
    explicit_user_expiry: bool
    estimated_shelf_life_days: int | None = Field(default=None, ge=1, le=730)
    confidence: float


@dataclass(frozen=True)
class AddProposal:
    payload: AddPayload
    cost_share: int | None


class NullDiff(Exception):
    """Raised when the LLM-parsed diff has no field changes."""


class ProposeCorrectError(Exception):
    """Raised when the LLM-parsed diff cannot be validated."""


CONSERVATIVE_FALLBACK_DAYS = 3


def _expiry_origin(item: PantryItem) -> date:
    return shelf_life_origin(item)


def _cache_key_for_item(item: PantryItem) -> str:
    return storage_cache_key(item.normalized_name, item.storage)


def correct_payload_to_json(payload: CorrectPayload) -> str:
    return payload.model_dump_json()


def add_payload_to_json(payload: AddPayload) -> str:
    return json.dumps({
        "kind": "add",
        "item": payload.model_dump(mode="json", exclude={"kind"}),
    })


def correct_payload_from_json(blob: str) -> CorrectPayload:
    return CorrectPayload.model_validate_json(blob)


def add_payload_from_json(blob: str) -> AddPayload:
    data = json.loads(blob)
    if data.get("kind") == "add" and "item" in data:
        return AddPayload.model_validate(data["item"])
    return AddPayload.model_validate(data)


def _snapshot(item: PantryItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "raw_name": item.raw_name,
        "normalized_name": item.normalized_name,
        "category": item.category,
        "qty": item.qty,
        "unit": item.unit,
        "purchased_on": item.purchased_on.isoformat(),
        "storage": item.storage,
        "stored_on": item.stored_on.isoformat() if item.stored_on else None,
        "shelf_life_days": item.shelf_life_days,
        "expires_on": item.expires_on.isoformat(),
        "status": item.status,
    }


def item_snapshot_to_json(item: PantryItem) -> str:
    return json.dumps(_snapshot(item), sort_keys=True)


def _cache_snapshot(row: ShelfLifeCache | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "normalized_name": row.normalized_name,
        "days": row.days,
        "category": row.category,
        "source": row.source,
        "confidence": row.confidence,
        "learned_at": row.learned_at.isoformat(),
    }


def _clean_name_translations(
    translations: dict[str, str],
    *,
    canonical_name: str,
) -> dict[str, str]:
    supported = set(LANGS)
    cleaned: dict[str, str] = {}
    for lang, value in translations.items():
        if lang not in supported:
            continue
        value = value.strip()
        if value:
            cleaned[lang] = value
    cleaned["en"] = canonical_name
    return cleaned


async def propose_correct(
    session: Session,
    *,
    llm: TextLLMClient,
    household_id: int,
    item: PantryItem,
    user_text: str,
    today: date,
) -> tuple[CorrectPayload, int | None]:
    cache_row = get_cached(session, household_id, _cache_key_for_item(item))
    diff, cost = await llm.parse_correct(
        item_snapshot=_snapshot(item),
        cache_snapshot=_cache_snapshot(cache_row),
        user_text=user_text,
        today=today,
    )

    if (
        diff.name is None
        and diff.category is None
        and diff.expires_on is None
        and diff.shelf_life_days is None
    ):
        raise NullDiff()

    diff_name_translations = {entry.lang: entry.text for entry in diff.name_translations}

    raw_new_name = diff.name.strip() if diff.name is not None else None
    if raw_new_name == "":
        raise ProposeCorrectError("name cannot be empty")
    corrected_name = (
        diff_name_translations.get("en", "").strip()
        if raw_new_name is not None
        else None
    )
    if raw_new_name is not None and not corrected_name:
        corrected_name = raw_new_name
    name_translations = (
        _clean_name_translations(
            diff_name_translations,
            canonical_name=corrected_name,
        )
        if corrected_name is not None and corrected_name != item.raw_name
        else {}
    )

    new_expires = diff.expires_on
    new_days = diff.shelf_life_days
    back_computed = False
    origin = _expiry_origin(item)

    if new_expires is not None and new_days is not None:
        new_expires = origin + timedelta(days=new_days)
    elif new_expires is not None:
        delta = (new_expires - origin).days
        if delta < 1 or delta > 730:
            raise ProposeCorrectError("expires_on out of range for shelf-life origin")
        new_days = delta
        back_computed = True
    elif new_days is not None:
        new_expires = origin + timedelta(days=new_days)

    payload_diff: dict[str, dict[str, Any] | None] = {
        "name": (
            {"old": item.raw_name, "new": corrected_name}
            if corrected_name is not None and corrected_name != item.raw_name
            else None
        ),
        "category": (
            {"old": item.category, "new": diff.category}
            if diff.category is not None and diff.category != item.category
            else None
        ),
        "expires_on": (
            {"old": item.expires_on.isoformat(), "new": new_expires.isoformat()}
            if new_expires is not None and new_expires != item.expires_on
            else None
        ),
        "shelf_life_days": (
            {"old": item.shelf_life_days, "new": new_days}
            if new_days is not None and new_days != item.shelf_life_days
            else None
        ),
    }

    if all(value is None for value in payload_diff.values()):
        raise NullDiff()

    return (
        CorrectPayload(
            diff=payload_diff,
            name_translations=name_translations,
            cache_action=diff.cache_action,
            rationale=diff.rationale,
            confidence=diff.confidence,
            back_computed_days=back_computed,
        ),
        cost,
    )


def apply_correct(
    session: Session,
    *,
    household_id: int,
    item: PantryItem,
    payload: CorrectPayload,
) -> None:
    old_normalized = item.normalized_name
    old_storage = item.storage

    name_change = payload.diff.get("name")
    category_change = payload.diff.get("category")
    expires_change = payload.diff.get("expires_on")
    days_change = payload.diff.get("shelf_life_days")

    if name_change is not None:
        item.raw_name = name_change["new"]
        item.normalized_name = normalize(name_change["new"])
    if category_change is not None:
        item.category = category_change["new"]
    if days_change is not None:
        new_days = int(days_change["new"])
        if not (1 <= new_days <= 730):
            raise ValueError(f"shelf_life_days {new_days} out of range [1, 730]")
        item.shelf_life_days = new_days
        item.shelf_life_source = "user_correction"
        item.expires_on = _expiry_origin(item) + timedelta(days=new_days)
    if expires_change is not None:
        item.expires_on = date.fromisoformat(expires_change["new"])
    if name_change is not None and payload.name_translations:
        upsert_name_translations(
            session,
            source_text=item.raw_name,
            translations=payload.name_translations,
        )
    session.add(item)

    new_normalized = item.normalized_name
    old_cache_key = storage_cache_key(old_normalized, old_storage)
    new_cache_key = storage_cache_key(new_normalized, item.storage)
    new_days = item.shelf_life_days
    new_category = item.category

    if payload.cache_action == "move":
        old_row = session.get(ShelfLifeCache, (household_id, old_cache_key))
        if old_row is not None and old_cache_key != new_cache_key:
            session.delete(old_row)
            session.flush()
        write_user_correction(
            session,
            household_id,
            new_cache_key,
            days=new_days,
            category=new_category,
            commit=False,
        )
    elif payload.cache_action == "add_new":
        write_user_correction(
            session,
            household_id,
            new_cache_key,
            days=new_days,
            category=new_category,
            commit=False,
        )
    elif payload.cache_action == "leave":
        if days_change is not None or (category_change is not None and name_change is None):
            write_user_correction(
                session,
                household_id,
                new_cache_key,
                days=new_days,
                category=new_category,
                commit=False,
            )
    session.flush()


async def propose_add(
    session: Session,
    *,
    llm: TextLLMClient,
    household_id: int,
    user_text: str,
    today: date,
    tz: str,
    search: ShelfLifeSearchClient | None = None,
) -> tuple[list[AddProposal], int | None]:
    items, total_cost = await llm.parse_add(user_text=user_text, today=today, tz=tz)
    if not items:
        return [], total_cost

    if total_cost is None:
        shares: list[int | None] = [None] * len(items)
    else:
        base = total_cost // len(items)
        remainder = total_cost - base * len(items)
        shares = [base + remainder if index == 0 else base for index in range(len(items))]

    proposals: list[AddProposal] = []
    for parsed, cost_share in zip(items, shares):
        normalized = normalize(parsed.name)
        category = parsed.category
        search_cost: int | None = None

        if parsed.explicit_user_expiry:
            if parsed.shelf_life_days is not None:
                days = parsed.shelf_life_days
                expires_on = today + timedelta(days=days)
            elif parsed.expires_on is not None:
                expires_on = parsed.expires_on
                days = max(1, min(730, (expires_on - today).days))
            else:
                days = CONSERVATIVE_FALLBACK_DAYS
                expires_on = today + timedelta(days=days)
            shelf_life_source = "user_correction"
            ingest_source = "manual_user_hint"
        else:
            cached = get_cached(session, household_id, normalized)
            if cached is not None:
                days = cached.days
                shelf_life_source = "cache"
                ingest_source = "cache"
                category = category or cached.category
            else:
                searched = None
                if search is not None:
                    search_result = await search.lookup_shelf_life(name=parsed.name, category=category)
                    search_cost = search_result.cost_micros_usd
                    searched = resolve_search_days(search_result)
                if searched is not None:
                    days = searched
                    shelf_life_source = "websearch"
                    ingest_source = "llm"
                    put_cached(
                        session, household_id, normalized, days=days,
                        category=category, confidence=0.9, source="llm", commit=False,
                    )
                else:
                    default = lookup_default(normalized)
                    if default is not None:
                        days = default.days
                        shelf_life_source = "manual_fallback"
                        ingest_source = "manual_fallback"
                        category = category or default.category
                    elif parsed.estimated_shelf_life_days is not None:
                        days = parsed.estimated_shelf_life_days
                        shelf_life_source = "llm"
                        ingest_source = "llm"
                    else:
                        days = CONSERVATIVE_FALLBACK_DAYS
                        shelf_life_source = "manual_fallback"
                        ingest_source = "manual_fallback"
            expires_on = today + timedelta(days=days)

        combined_cost = cost_share if search_cost is None else (
            search_cost if cost_share is None else cost_share + search_cost
        )
        proposals.append(
            AddProposal(
                payload=AddPayload(
                    name=parsed.name,
                    category=category,
                    qty=parsed.qty,
                    unit=parsed.unit,
                    shelf_life_days=days,
                    expires_on=expires_on,
                    shelf_life_source=shelf_life_source,
                    ingest_shelf_life_source=ingest_source,
                    explicit_user_expiry=parsed.explicit_user_expiry,
                    estimated_shelf_life_days=parsed.estimated_shelf_life_days,
                    confidence=parsed.confidence,
                ),
                cost_share=combined_cost,
            )
        )
    return proposals, total_cost


def apply_add(
    session: Session,
    *,
    household_id: int,
    payload: AddPayload,
    today: date,
) -> int:
    normalized = normalize(payload.name)
    item = PantryItem(
        household_id=household_id,
        raw_name=payload.name,
        normalized_name=normalized,
        category=payload.category,
        qty=payload.qty,
        unit=payload.unit,
        purchased_on=today,
        shelf_life_days=payload.shelf_life_days,
        shelf_life_source=payload.shelf_life_source,
        ingest_shelf_life_source=payload.ingest_shelf_life_source,
        expires_on=payload.expires_on,
        status="active",
        created_via="manual",
        source_receipt_id=None,
        created_at=datetime.now(UTC),
    )
    session.add(item)
    session.flush()
    assert item.id is not None

    if payload.shelf_life_source == "user_correction":
        write_user_correction(
            session,
            household_id,
            normalized,
            days=payload.shelf_life_days,
            category=payload.category,
            commit=False,
        )
    session.flush()
    return item.id
