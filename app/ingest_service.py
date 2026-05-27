from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.cache import get_cached, put_cached, write_user_correction
from app.llm import LLMClient, ParsedItem
from app.models import PantryItem, Receipt
from app.normalization import normalize
from app.shelf_life_defaults import lookup_default


@dataclass(frozen=True)
class ShelfLifeDecision:
    days: int
    source: Literal["cache", "llm"]
    cache_was_hit: bool


CONFIDENCE_FOR_CACHE_WRITE = 0.6
CONFIDENCE_MIN_FOR_INSERT = 0.3
PURCHASE_DATE_MIN_CONFIDENCE = 0.7


def compute_shelf_life(
    session: Session, *, user_id: int, parsed: ParsedItem
) -> ShelfLifeDecision:
    normalized_name = normalize(parsed.name)
    cached = get_cached(session, user_id, normalized_name)
    if cached is not None:
        return ShelfLifeDecision(days=cached.days, source="cache", cache_was_hit=True)
    if parsed.confidence >= CONFIDENCE_FOR_CACHE_WRITE:
        put_cached(
            session,
            user_id,
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
    """Raised when (user_id, photo_file_id) already has a Receipt row."""


@dataclass
class IngestSummary:
    receipt_id: Optional[int]
    inserted_food_count: int
    inserted_item_ids: list[int] = field(default_factory=list)
    inserted_item_names: list[str] = field(default_factory=list)
    inserted_item_expires_on: list[date] = field(default_factory=list)
    inserted_item_shelf_life_days: list[int] = field(default_factory=list)
    skipped_non_food_count: int = 0
    skipped_low_confidence_count: int = 0
    skipped_low_confidence_names: list[str] = field(default_factory=list)
    low_confidence_inserted_ids: list[int] = field(default_factory=list)
    purchase_date: Optional[date] = None
    purchase_date_assumed: bool = False
    cost_micros_usd: Optional[int] = None


async def ingest_photo(
    session: Session,
    llm: LLMClient,
    *,
    user_id: int,
    photo_file_id: str,
    image_bytes: bytes,
    today: date,
) -> IngestSummary:
    existing = session.exec(
        select(Receipt).where(
            Receipt.user_id == user_id,
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
        receipt = Receipt(
            user_id=user_id,
            photo_file_id=photo_file_id,
            purchase_date=purchase_date,
            purchase_date_source=purchase_date_source,
            scanned_at=datetime.now(timezone.utc),
            llm_cost_micros_usd=llm_result.cost_micros_usd,
        )
        session.add(receipt)
        session.flush()

        for parsed_item, is_low_confidence in to_insert:
            decision = compute_shelf_life(
                session,
                user_id=user_id,
                parsed=parsed_item,
            )
            normalized_name = normalize(parsed_item.name)
            pantry_item = PantryItem(
                user_id=user_id,
                raw_name=parsed_item.name,
                normalized_name=normalized_name,
                category=parsed_item.category,
                qty=parsed_item.qty,
                unit=parsed_item.unit,
                purchased_on=purchase_date,
                shelf_life_days=decision.days,
                shelf_life_source="cache" if decision.cache_was_hit else "llm",
                ingest_shelf_life_source="cache" if decision.cache_was_hit else "llm",
                expires_on=purchase_date + timedelta(days=decision.days),
                status="active",
                created_via="receipt",
                source_receipt_id=receipt.id,
                created_at=datetime.now(timezone.utc),
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
            summary.inserted_food_count += 1
        session.commit()
    except IntegrityError:
        session.rollback()
        raise DuplicateReceipt(f"Receipt already logged (concurrent insert for file_id={photo_file_id})")
    except Exception:
        session.rollback()
        raise

    summary.receipt_id = receipt.id
    return summary


@dataclass
class TextIngestSummary:
    inserted_count: int = 0
    inserted_ids: list[int] = field(default_factory=list)
    inserted_names: list[str] = field(default_factory=list)
    failed_parts: list[str] = field(default_factory=list)
    failed_reasons: list[str] = field(default_factory=list)


_HINT_RE = re.compile(r"\s+(\d+)\s*d\s*$", flags=re.IGNORECASE)
_QTY_PREFIX_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*"
    r"(gal|gallon|gallons|oz|lb|lbs|g|kg|ml|l|ct|count|pk|pack|bunch|dozen)?\s+",
    flags=re.IGNORECASE,
)
_DOZEN_PREFIX_RE = re.compile(r"^\s*dozen\s+", flags=re.IGNORECASE)


def _parse_text_part(raw: str) -> tuple[str, Optional[int], float, Optional[str], Optional[str]]:
    text = raw.strip()
    hint_days = None
    hint_match = _HINT_RE.search(text)
    if hint_match:
        days = int(hint_match.group(1))
        if not 1 <= days <= 730:
            return text[:hint_match.start()].strip(), None, 1.0, None, "shelf life days must be 1..730"
        hint_days = days
        text = text[:hint_match.start()].rstrip()

    qty = 1.0
    unit: Optional[str] = None
    dozen_match = _DOZEN_PREFIX_RE.match(text)
    qty_match = None if dozen_match else _QTY_PREFIX_RE.match(text)
    if dozen_match:
        qty = 12.0
        unit = "ct"
        text = text[dozen_match.end():]
    elif qty_match:
        qty = float(qty_match.group(1))
        unit = qty_match.group(2).lower() if qty_match.group(2) else None
        if unit == "dozen":
            qty *= 12
            unit = "ct"
        text = text[qty_match.end():]

    return text.strip(), hint_days, qty, unit, None


def ingest_text(
    session: Session, *, user_id: int, text: str, today: date
) -> TextIngestSummary:
    summary = TextIngestSummary()
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if not parts:
        summary.failed_parts.append(text)
        summary.failed_reasons.append("empty")
        return summary

    for raw_part in parts:
        item_text, hint_days, qty, unit, parse_error = _parse_text_part(raw_part)
        if parse_error is not None:
            summary.failed_parts.append(raw_part)
            summary.failed_reasons.append(parse_error)
            continue
        if not item_text:
            summary.failed_parts.append(raw_part)
            summary.failed_reasons.append("empty after stripping hint/qty")
            continue

        normalized_name = normalize(item_text)
        category = None
        if hint_days is not None:
            days = hint_days
            shelf_life_source = "user_correction"
            ingest_source = "manual_user_hint"
            cached = write_user_correction(session, user_id, normalized_name, days=days)
            category = cached.category
        else:
            cached = get_cached(session, user_id, normalized_name)
            if cached is not None:
                days = cached.days
                shelf_life_source = "cache"
                ingest_source = "cache"
                category = cached.category
            else:
                default = lookup_default(normalized_name)
                if default is None:
                    summary.failed_parts.append(raw_part)
                    summary.failed_reasons.append(
                        "no cache, no default; add `7d` hint or use /correct after adding"
                    )
                    continue
                days = default.days
                shelf_life_source = "manual_fallback"
                ingest_source = "manual_fallback"
                category = default.category

        pantry_item = PantryItem(
            user_id=user_id,
            raw_name=item_text,
            normalized_name=normalized_name,
            category=category,
            qty=qty,
            unit=unit,
            purchased_on=today,
            shelf_life_days=days,
            shelf_life_source=shelf_life_source,
            ingest_shelf_life_source=ingest_source,
            expires_on=today + timedelta(days=days),
            status="active",
            created_via="manual",
            source_receipt_id=None,
            created_at=datetime.now(timezone.utc),
        )
        session.add(pantry_item)
        session.flush()
        assert pantry_item.id is not None
        summary.inserted_ids.append(pantry_item.id)
        summary.inserted_names.append(pantry_item.raw_name)
        summary.inserted_count += 1
    session.commit()
    return summary
