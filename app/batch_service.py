"""Persisted, versioned batch selections and atomic pantry status changes."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import cast

from sqlalchemy import update
from sqlmodel import Session, col, select

from app.models import PantryBatch, PantryItem
from app.pantry_service import ListFilter, PantrySort, list_active
from app.pending_service import expire_for_item, utc_naive

PAGE_SIZE = 12
MAX_SELECTED = 100
TTL = timedelta(minutes=30)
TARGET_STATUSES = frozenset({"eaten", "tossed", "removed"})


class BatchError(ValueError):
    """A localized catalog key describing a rejected action."""


def create_batch(
    session: Session,
    *,
    household_id: int,
    user_id: int,
    sort_by: PantrySort,
    today: date,
    now: datetime,
) -> PantryBatch:
    now = utc_naive(now)
    # Bound storage for these short-lived workflows; old buttons show expired.
    old = session.exec(
        select(PantryBatch).where(
            PantryBatch.user_id == user_id, col(PantryBatch.expires_at) <= now
        )
    ).all()
    for row in old:
        session.delete(row)
    items = list_active(
        session,
        household_id=household_id,
        f=ListFilter.default(),
        today=today,
        sort_by=sort_by,
    )
    batch = PantryBatch(
        household_id=household_id,
        user_id=user_id,
        sort_by=sort_by,
        candidate_ids_json=json.dumps([item.id for item in items]),
        created_at=now,
        expires_at=now + TTL,
    )
    session.add(batch)
    session.commit()
    session.refresh(batch)
    return batch


def load_batch(
    session: Session,
    *,
    batch_id: int,
    household_id: int,
    user_id: int,
    now: datetime,
) -> PantryBatch:
    batch = session.get(PantryBatch, batch_id)
    if (
        batch is None
        or batch.household_id != household_id
        or batch.user_id != user_id
        or batch.expires_at <= utc_naive(now)
    ):
        raise BatchError("batch.expired")
    return batch


def batch_items(
    session: Session, batch: PantryBatch, *, selected_only=False
) -> list[PantryItem]:
    ids: list[int] = json.loads(
        batch.selected_ids_json if selected_only else batch.candidate_ids_json
    )
    if not ids:
        return []
    rows = session.exec(
        select(PantryItem).where(
            PantryItem.household_id == batch.household_id, col(PantryItem.id).in_(ids)
        )
    ).all()
    by_id = {row.id: row for row in rows}
    return [by_id[item_id] for item_id in ids if item_id in by_id]


def change_selection(
    session: Session,
    batch: PantryBatch,
    *,
    version: int,
    action: str,
    value: str | None,
    now: datetime,
) -> PantryBatch:
    """Compare-and-swap before writes; item changes and batch result commit once.

    SQLite's writer lock prevents concurrent applies from overwriting a status
    changed by another batch. Every callback carries the version it displayed.
    """
    if batch.status in {"applied", "cancelled"}:
        raise BatchError("batch.closed")
    claimed = session.connection().execute(
        update(PantryBatch)
        .where(
            col(PantryBatch.id) == batch.id,
            col(PantryBatch.version) == version,
            col(PantryBatch.expires_at) > utc_naive(now),
            col(PantryBatch.status).in_(["selecting", "confirming"]),
        )
        .values(version=version + 1)
    )
    if claimed.rowcount != 1:
        session.rollback()
        raise BatchError("batch.stale")
    session.refresh(batch)
    try:
        selected: set[int] = set(json.loads(batch.selected_ids_json))
        candidates = batch_items(
            session, batch, selected_only=batch.status == "confirming"
        )
        if action == "cancel":
            batch.status = "cancelled"
        elif action == "page":
            page = int(value or "-1")
            if page < 0 or page >= max(
                1, (len(candidates) + PAGE_SIZE - 1) // PAGE_SIZE
            ):
                raise BatchError("batch.invalid")
            batch.page = page
        elif action == "edit" and batch.status == "confirming":
            batch.status, batch.target_status, batch.page = "selecting", None, 0
        elif action == "apply" and batch.status == "confirming":
            if not selected or batch.target_status not in TARGET_STATUSES:
                raise BatchError("batch.invalid")
            # Refresh all rows after acquiring the write lock, then validate all
            # selected IDs before changing any item. Missing/foreign IDs abort.
            session.expire_all()
            rows = batch_items(session, batch, selected_only=True)
            if {row.id for row in rows} != selected:
                raise BatchError("batch.invalid")
            changed = 0
            for item in rows:
                if item.status != "active":
                    continue
                item.status = cast(str, batch.target_status)
                item.snoozed_until = None
                assert item.id is not None
                expire_for_item(
                    session, household_id=batch.household_id, item_id=item.id
                )
                session.add(item)
                changed += 1
            batch.status = "applied"
            batch.applied_count, batch.skipped_count = changed, len(selected) - changed
        elif batch.status == "selecting":
            if action == "clear":
                selected.clear()
            elif action == "toggle":
                item_id = int(value or "-1")
                if item_id in selected:
                    selected.remove(item_id)
                elif any(
                    item.id == item_id and item.status == "active"
                    for item in candidates
                ):
                    selected.add(item_id)
                else:
                    raise BatchError("batch.invalid")
            elif action == "select_page":
                selected.update(
                    item.id
                    for item in candidates[
                        batch.page * PAGE_SIZE : (batch.page + 1) * PAGE_SIZE
                    ]
                    if item.id is not None and item.status == "active"
                )
            elif action == "confirm" and value in TARGET_STATUSES:
                if not selected:
                    raise BatchError("batch.empty")
                batch.status, batch.target_status, batch.page = "confirming", value, 0
            else:
                raise BatchError("batch.invalid")
            if len(selected) > MAX_SELECTED:
                raise BatchError("batch.limit")
            # Preserve the displayed sort order on the confirmation screen.
            batch.selected_ids_json = json.dumps(
                [
                    item_id
                    for item_id in json.loads(batch.candidate_ids_json)
                    if item_id in selected
                ]
            )
        else:
            raise BatchError("batch.invalid")
        session.add(batch)
        session.commit()
        session.refresh(batch)
        return batch
    except Exception:
        session.rollback()
        raise
