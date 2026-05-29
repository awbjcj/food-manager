from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

from sqlmodel import Session, col, select

from app.cache import write_user_correction
from app.models import PantryItem, PendingCorrection, Receipt
from app.pending_service import expire_for_item


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
            col(PantryItem.expires_on) >= today,
            col(PantryItem.expires_on) <= today + timedelta(days=7),
        )
    elif f.window == "expired":
        query = query.where(col(PantryItem.expires_on) < today)
    query = query.order_by(col(PantryItem.expires_on).asc())
    return list(session.exec(query).all())


def list_digest_due(session: Session, *, user_id: int, today: date) -> list[PantryItem]:
    query = (
        select(PantryItem)
        .where(PantryItem.user_id == user_id, PantryItem.status == "active")
        .where(
            col(PantryItem.snoozed_until).is_(None)
            | (col(PantryItem.snoozed_until) <= today)
        )
        .where(col(PantryItem.expires_on) <= today + timedelta(days=7))
        .order_by(col(PantryItem.expires_on).asc())
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
    assert pantry_item.id is not None
    expire_for_item(session, user_id=pantry_item.user_id, item_id=pantry_item.id)
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
    assert pantry_item.id is not None
    expire_for_item(session, user_id=pantry_item.user_id, item_id=pantry_item.id)
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
    assert pantry_item.id is not None
    expire_for_item(session, user_id=pantry_item.user_id, item_id=pantry_item.id)
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
    assert pantry_item.id is not None
    expire_for_item(session, user_id=pantry_item.user_id, item_id=pantry_item.id)
    session.add(pantry_item)
    write_user_correction(
        session,
        user_id,
        pantry_item.normalized_name,
        days=days,
        category=pantry_item.category,
        commit=False,
    )
    session.commit()
    session.refresh(pantry_item)
    return pantry_item


@dataclass(frozen=True)
class TextLLMCost:
    correction_proposal_count: int
    correction_cost_micros: int
    correction_unknown_cost_count: int
    add_proposal_count: int
    add_cost_micros: int
    add_unknown_cost_count: int


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
    text_llm: TextLLMCost = TextLLMCost(
        correction_proposal_count=0,
        correction_cost_micros=0,
        correction_unknown_cost_count=0,
        add_proposal_count=0,
        add_cost_micros=0,
        add_unknown_cost_count=0,
    )


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

    pending_rows = list(
        session.exec(
            select(PendingCorrection).where(
                PendingCorrection.user_id == user_id,
                PendingCorrection.created_at >= since,
            )
        ).all()
    )
    correction_rows = [row for row in pending_rows if row.action_type == "correct"]
    add_rows = [row for row in pending_rows if row.action_type == "add"]
    text_llm = TextLLMCost(
        correction_proposal_count=len(correction_rows),
        correction_cost_micros=sum(row.llm_cost_micros_usd or 0 for row in correction_rows),
        correction_unknown_cost_count=sum(
            1 for row in correction_rows if row.llm_cost_micros_usd is None
        ),
        add_proposal_count=len(add_rows),
        add_cost_micros=sum(row.llm_cost_micros_usd or 0 for row in add_rows),
        add_unknown_cost_count=sum(
            1 for row in add_rows if row.llm_cost_micros_usd is None
        ),
    )

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
        text_llm=text_llm,
    )


UNDO_TTL_MINUTES = 10


def is_untouched(item: PantryItem) -> bool:
    # "untouched" = still active, not snoozed, not shelf-life-corrected.
    # NOTE: a name-only /correct does not flip shelf_life_source, so such an
    # item is still treated as untouched (accepted limitation).
    return (
        item.status == "active"
        and item.snoozed_until is None
        and item.shelf_life_source != "user_correction"
    )


def _is_undoable_add(item: PantryItem) -> bool:
    # A manually-added item is undoable while it is still active and not
    # snoozed. Unlike receipt items (always born "cache"/"llm"), a manual
    # /add with an explicit expiry is *born* with shelf_life_source ==
    # "user_correction", so that source must NOT count as "touched" here --
    # otherwise the Undo button on such an /add would never remove the item.
    return item.status == "active" and item.snoozed_until is None


def _skip_reason(item: PantryItem) -> str:
    if item.status != "active":
        return item.status
    if item.snoozed_until is not None:
        return "snoozed"
    return "corrected"


@dataclass(frozen=True)
class UndoResult:
    removed_ids: list[int]
    skipped: list[tuple[int, str]]
    receipt_deleted: bool
    expired: bool


def _expired(reference: datetime, now: datetime) -> bool:
    ref = reference if reference.tzinfo else reference.replace(tzinfo=timezone.utc)
    return (now - ref) > timedelta(minutes=UNDO_TTL_MINUTES)


def undo_receipt(
    session: Session, *, user_id: int, receipt_id: int, now: datetime
) -> UndoResult:
    receipt = session.get(Receipt, receipt_id)
    if receipt is None or receipt.user_id != user_id:
        return UndoResult([], [], False, expired=False)
    if _expired(receipt.scanned_at, now):
        return UndoResult([], [], False, expired=True)

    items = list(session.exec(
        select(PantryItem).where(
            PantryItem.user_id == user_id,
            PantryItem.source_receipt_id == receipt_id,
        )
    ).all())
    removed_ids: list[int] = []
    skipped: list[tuple[int, str]] = []
    for item in items:
        assert item.id is not None
        if is_untouched(item):
            item.status = "removed"
            item.snoozed_until = None
            expire_for_item(session, user_id=user_id, item_id=item.id)
            removed_ids.append(item.id)
        else:
            skipped.append((item.id, _skip_reason(item)))
        session.add(item)

    full = not skipped
    if full:
        for item in items:
            item.source_receipt_id = None
            session.add(item)
        session.flush()
        session.delete(receipt)
    session.commit()
    return UndoResult(removed_ids, skipped, receipt_deleted=full, expired=False)


def undo_add(
    session: Session, *, user_id: int, item_id: int, now: datetime
) -> UndoResult:
    item = session.get(PantryItem, item_id)
    if item is None or item.user_id != user_id:
        return UndoResult([], [], False, expired=False)
    if _expired(item.created_at, now):
        return UndoResult([], [], False, expired=True)
    assert item.id is not None
    if not _is_undoable_add(item):
        return UndoResult([], [(item.id, _skip_reason(item))], False, expired=False)
    item.status = "removed"
    item.snoozed_until = None
    expire_for_item(session, user_id=user_id, item_id=item.id)
    session.add(item)
    session.commit()
    return UndoResult([item.id], [], receipt_deleted=False, expired=False)
