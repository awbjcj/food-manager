from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, Optional

from sqlmodel import Session, select

from app.cache import write_user_correction
from app.models import PantryItem, Receipt


ALLOWED_CATEGORIES = frozenset({
    "dairy", "produce", "meat", "seafood", "bakery",
    "pantry", "frozen", "beverage", "other",
})

Window = Literal["all", "week", "expired"]


@dataclass(frozen=True)
class ListFilter:
    category: Optional[str] = None
    window: Window = "all"

    @classmethod
    def default(cls) -> "ListFilter":
        return cls()


def list_active(session: Session, *, user_id: int, f: ListFilter, today: date) -> list[PantryItem]:
    query = select(PantryItem).where(
        PantryItem.user_id == user_id,
        PantryItem.status == "active",
    )
    if f.category is not None:
        query = query.where(PantryItem.category == f.category)
    if f.window == "week":
        query = query.where(
            PantryItem.expires_on >= today,
            PantryItem.expires_on <= today + timedelta(days=7),
        )
    elif f.window == "expired":
        query = query.where(PantryItem.expires_on < today)
    query = query.order_by(PantryItem.expires_on.asc())
    return list(session.exec(query).all())


def list_digest_due(session: Session, *, user_id: int, today: date) -> list[PantryItem]:
    query = (
        select(PantryItem)
        .where(PantryItem.user_id == user_id, PantryItem.status == "active")
        .where((PantryItem.snoozed_until.is_(None)) | (PantryItem.snoozed_until <= today))
        .where(PantryItem.expires_on <= today + timedelta(days=7))
        .order_by(PantryItem.expires_on.asc())
    )
    return list(session.exec(query).all())


class NotOwnerOrMissing(Exception):
    """Raised when an item lookup is missing or does not belong to user_id."""


@dataclass(frozen=True)
class MutationResult:
    applied: bool
    was_already: bool


def _load_owned(session: Session, *, user_id: int, item_id: int) -> PantryItem:
    pantry_item = session.get(PantryItem, item_id)
    if pantry_item is None or pantry_item.user_id != user_id:
        raise NotOwnerOrMissing(f"item {item_id}")
    return pantry_item


def _set_terminal(session: Session, pantry_item: PantryItem, status: str) -> MutationResult:
    if pantry_item.status != "active":
        return MutationResult(applied=False, was_already=True)
    pantry_item.status = status
    pantry_item.snoozed_until = None
    session.add(pantry_item)
    session.commit()
    return MutationResult(applied=True, was_already=False)


def mark_eaten(session: Session, *, user_id: int, item_id: int, today: date) -> MutationResult:
    return _set_terminal(session, _load_owned(session, user_id=user_id, item_id=item_id), "eaten")


def mark_tossed(session: Session, *, user_id: int, item_id: int, today: date) -> MutationResult:
    return _set_terminal(session, _load_owned(session, user_id=user_id, item_id=item_id), "tossed")


def mark_removed(session: Session, *, user_id: int, item_id: int, today: date) -> MutationResult:
    pantry_item = _load_owned(session, user_id=user_id, item_id=item_id)
    if pantry_item.status == "removed":
        return MutationResult(applied=False, was_already=True)
    pantry_item.status = "removed"
    pantry_item.snoozed_until = None
    session.add(pantry_item)
    session.commit()
    return MutationResult(applied=True, was_already=False)


SNOOZE_DAYS_DEFAULT = 2
SNOOZE_DAYS_MIN = 1
SNOOZE_DAYS_MAX = 30


def snooze_item(
    session: Session,
    *,
    user_id: int,
    item_id: int,
    today: date,
    days: int = SNOOZE_DAYS_DEFAULT,
) -> MutationResult:
    if days < SNOOZE_DAYS_MIN or days > SNOOZE_DAYS_MAX:
        raise ValueError(f"days must be in [{SNOOZE_DAYS_MIN}, {SNOOZE_DAYS_MAX}]")
    pantry_item = _load_owned(session, user_id=user_id, item_id=item_id)
    if pantry_item.status != "active":
        return MutationResult(applied=False, was_already=True)
    pantry_item.snoozed_until = today + timedelta(days=days)
    session.add(pantry_item)
    session.commit()
    return MutationResult(applied=True, was_already=False)


SHELF_LIFE_DAYS_MIN = 1
SHELF_LIFE_DAYS_MAX = 730


def correct_item(
    session: Session, *, user_id: int, item_id: int, days: int, today: date
) -> PantryItem:
    if days < SHELF_LIFE_DAYS_MIN or days > SHELF_LIFE_DAYS_MAX:
        raise ValueError(f"days must be in [{SHELF_LIFE_DAYS_MIN}, {SHELF_LIFE_DAYS_MAX}]")
    pantry_item = _load_owned(session, user_id=user_id, item_id=item_id)
    if pantry_item.status == "removed":
        raise ValueError("cannot correct a removed item")
    pantry_item.shelf_life_days = days
    pantry_item.shelf_life_source = "user_correction"
    pantry_item.expires_on = pantry_item.purchased_on + timedelta(days=days)
    session.add(pantry_item)
    write_user_correction(
        session,
        user_id,
        pantry_item.normalized_name,
        days=days,
        category=pantry_item.category,
    )
    session.commit()
    session.refresh(pantry_item)
    return pantry_item


@dataclass(frozen=True)
class Stats:
    receipt_count: int
    tracked_item_count: int
    removed_item_count: int
    cache_hit_percent: Optional[float]
    total_cost_micros_usd: int
    avg_cost_micros_usd: Optional[int]
    unknown_cost_receipt_count: int
    waste_rate_percent: Optional[float]


def compute_stats(session: Session, *, user_id: int, now: datetime) -> Stats:
    since = now - timedelta(days=30)
    receipts = list(
        session.exec(
            select(Receipt).where(
                Receipt.user_id == user_id,
                Receipt.scanned_at >= since,
            )
        ).all()
    )
    known_costs = [
        receipt.llm_cost_micros_usd
        for receipt in receipts
        if receipt.llm_cost_micros_usd is not None
    ]
    total_cost = sum(known_costs) if known_costs else 0
    avg_cost = total_cost // len(known_costs) if known_costs else None

    items_30d = list(
        session.exec(
            select(PantryItem).where(
                PantryItem.user_id == user_id,
                PantryItem.created_at >= since,
            )
        ).all()
    )
    tracked = [item for item in items_30d if item.status != "removed"]
    removed = [item for item in items_30d if item.status == "removed"]
    receipt_items = [
        item
        for item in items_30d
        if item.created_via == "receipt"
        and item.status != "removed"
        and item.ingest_shelf_life_source in ("cache", "llm")
    ]
    if receipt_items:
        hits = sum(1 for item in receipt_items if item.ingest_shelf_life_source == "cache")
        cache_hit_percent = hits * 100.0 / len(receipt_items)
    else:
        cache_hit_percent = None

    eaten = sum(1 for item in items_30d if item.status == "eaten")
    tossed = sum(1 for item in items_30d if item.status == "tossed")
    waste_rate = tossed * 100.0 / (eaten + tossed) if eaten + tossed else None

    return Stats(
        receipt_count=len(receipts),
        tracked_item_count=len(tracked),
        removed_item_count=len(removed),
        cache_hit_percent=cache_hit_percent,
        total_cost_micros_usd=total_cost,
        avg_cost_micros_usd=avg_cost,
        unknown_cost_receipt_count=sum(
            1 for receipt in receipts if receipt.llm_cost_micros_usd is None
        ),
        waste_rate_percent=waste_rate,
    )
