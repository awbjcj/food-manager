from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.cache import get_cached, put_cached
from app.frozen_shelf_life import resolve_frozen_days
from app.llm import LLMClient, ParsedItem
from app.models import PantryItem, Receipt
from app.normalization import normalize
from app.shelf_life_search import ShelfLifeSearchClient
from app.shopping_service import check_off_purchased_names


@dataclass(frozen=True)
class ShelfLifeDecision:
    days: int
    source: Literal["cache", "llm"]
    cache_was_hit: bool


CONFIDENCE_FOR_CACHE_WRITE = 0.6
CONFIDENCE_MIN_FOR_INSERT = 0.3
PURCHASE_DATE_MIN_CONFIDENCE = 0.7


def compute_shelf_life(
    session: Session, *, household_id: int, parsed: ParsedItem
) -> ShelfLifeDecision:
    normalized_name = normalize(parsed.name)
    cached = get_cached(session, household_id, normalized_name)
    if cached is not None:
        return ShelfLifeDecision(days=cached.days, source="cache", cache_was_hit=True)
    if parsed.confidence >= CONFIDENCE_FOR_CACHE_WRITE:
        put_cached(
            session,
            household_id,
            normalized_name,
            days=parsed.est_shelf_life_days,
            category=parsed.category,
            confidence=parsed.confidence,
            source="llm",
            commit=False,
        )
    return ShelfLifeDecision(
        days=parsed.est_shelf_life_days,
        source="llm",
        cache_was_hit=False,
    )


class DuplicateReceipt(Exception):
    """Raised when (household_id, photo_file_id) already has a Receipt row."""


@dataclass
class IngestSummary:
    receipt_id: int | None
    inserted_food_count: int
    inserted_item_ids: list[int] = field(default_factory=list)
    inserted_item_names: list[str] = field(default_factory=list)
    inserted_item_expires_on: list[date] = field(default_factory=list)
    inserted_item_shelf_life_days: list[int] = field(default_factory=list)
    skipped_non_food_count: int = 0
    skipped_low_confidence_count: int = 0
    skipped_low_confidence_names: list[str] = field(default_factory=list)
    low_confidence_inserted_ids: list[int] = field(default_factory=list)
    skipped_excluded_count: int = 0
    skipped_excluded_names: list[str] = field(default_factory=list)
    uncached_item_ids: list[int] = field(default_factory=list)
    purchase_date: date | None = None
    purchase_date_assumed: bool = False
    cost_micros_usd: int | None = None
    shopping_checked_names: list[str] = field(default_factory=list)


async def ingest_photo(
    session: Session,
    llm: LLMClient,
    *,
    household_id: int,
    photo_file_id: str,
    image_bytes: bytes,
    today: date,
    search: ShelfLifeSearchClient | None = None,
) -> IngestSummary:
    existing = session.exec(
        select(Receipt).where(
            Receipt.household_id == household_id,
            Receipt.photo_file_id == photo_file_id,
        )
    ).first()
    if existing is not None:
        raise DuplicateReceipt(f"Receipt already logged (id={existing.id})")

    llm_result = await llm.extract_items_from_image(image_bytes)
    parsed_receipt = llm_result.parse
    if (
        parsed_receipt.purchase_date is not None
        and parsed_receipt.purchase_date_confidence >= PURCHASE_DATE_MIN_CONFIDENCE
    ):
        purchase_date = parsed_receipt.purchase_date
        purchase_date_source = "receipt"
        purchase_date_assumed = False
    else:
        purchase_date = today
        purchase_date_source = "scan_fallback"
        purchase_date_assumed = True

    summary = IngestSummary(
        receipt_id=None,
        inserted_food_count=0,
        purchase_date=purchase_date,
        purchase_date_assumed=purchase_date_assumed,
        cost_micros_usd=llm_result.cost_micros_usd,
    )

    to_insert: list[tuple[ParsedItem, bool]] = []
    for parsed_item in parsed_receipt.items:
        if not parsed_item.track_worthy:
            summary.skipped_excluded_count += 1
            summary.skipped_excluded_names.append(parsed_item.name)
            continue
        if not parsed_item.is_food:
            summary.skipped_non_food_count += 1
            continue
        if parsed_item.confidence < CONFIDENCE_MIN_FOR_INSERT:
            summary.skipped_low_confidence_count += 1
            summary.skipped_low_confidence_names.append(parsed_item.name)
            continue
        to_insert.append((parsed_item, parsed_item.confidence < CONFIDENCE_FOR_CACHE_WRITE))

    if not to_insert:
        return summary

    try:
        scanned_at = datetime.now(UTC)
        receipt = Receipt(
            household_id=household_id,
            photo_file_id=photo_file_id,
            purchase_date=purchase_date,
            purchase_date_source=purchase_date_source,
            scanned_at=scanned_at,
            llm_cost_micros_usd=llm_result.cost_micros_usd,
        )
        session.add(receipt)
        session.flush()

        for parsed_item, is_low_confidence in to_insert:
            normalized_name = normalize(parsed_item.name)
            if parsed_item.frozen:
                frozen = await resolve_frozen_days(
                    session,
                    household_id=household_id,
                    normalized_name=normalized_name,
                    food_name=parsed_item.name,
                    search=search,
                )
                shelf_life_days = frozen.days
                shelf_life_source = "cache" if frozen.cache_was_hit else frozen.source
                ingest_source = "cache" if frozen.cache_was_hit else "llm"
                storage = "frozen"
                stored_on = purchase_date
                track_uncached = False
            else:
                decision = compute_shelf_life(
                    session,
                    household_id=household_id,
                    parsed=parsed_item,
                )
                shelf_life_days = decision.days
                shelf_life_source = "cache" if decision.cache_was_hit else "llm"
                ingest_source = shelf_life_source
                storage = "default"
                stored_on = None
                track_uncached = not decision.cache_was_hit
            pantry_item = PantryItem(
                household_id=household_id,
                raw_name=parsed_item.name,
                normalized_name=normalized_name,
                category=parsed_item.category,
                qty=parsed_item.qty,
                unit=parsed_item.unit,
                purchased_on=purchase_date,
                shelf_life_days=shelf_life_days,
                shelf_life_source=shelf_life_source,
                ingest_shelf_life_source=ingest_source,
                expires_on=(stored_on or purchase_date)
                + timedelta(days=shelf_life_days),
                status="active",
                created_via="receipt",
                storage=storage,
                stored_on=stored_on,
                source_receipt_id=receipt.id,
                created_at=datetime.now(UTC),
            )
            session.add(pantry_item)
            session.flush()
            assert pantry_item.id is not None
            summary.inserted_item_ids.append(pantry_item.id)
            summary.inserted_item_names.append(pantry_item.raw_name)
            summary.inserted_item_expires_on.append(pantry_item.expires_on)
            summary.inserted_item_shelf_life_days.append(pantry_item.shelf_life_days)
            if is_low_confidence:
                summary.low_confidence_inserted_ids.append(pantry_item.id)
            if track_uncached:
                summary.uncached_item_ids.append(pantry_item.id)
            summary.inserted_food_count += 1
        summary.shopping_checked_names = check_off_purchased_names(
            session,
            household_id=household_id,
            names=summary.inserted_item_names,
            now=scanned_at,
            commit=False,
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        raise DuplicateReceipt(f"Receipt already logged (concurrent insert for file_id={photo_file_id})")
    except Exception:
        session.rollback()
        raise

    summary.receipt_id = receipt.id
    return summary
