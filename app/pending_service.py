from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from app.models import PendingCorrection


PENDING_TTL_MINUTES = 10


class PendingNotPending(Exception):
    """Raised when a terminal transition is attempted on a non-pending row."""


def utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def create_pending(
    session: Session,
    *,
    user_id: int,
    action_type: str,
    item_id: Optional[int],
    proposed_json: str,
    snapshot_json: Optional[str],
    cost_micros_usd: Optional[int],
    chat_id: int,
    now: datetime,
) -> PendingCorrection:
    created_at = utc_naive(now)
    pending = PendingCorrection(
        user_id=user_id,
        action_type=action_type,
        item_id=item_id,
        proposed_json=proposed_json,
        original_snapshot_json=snapshot_json,
        llm_cost_micros_usd=cost_micros_usd,
        chat_id=chat_id,
        message_id=None,
        status="pending",
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=PENDING_TTL_MINUTES),
    )
    session.add(pending)
    session.commit()
    session.refresh(pending)
    return pending


def load_pending(
    session: Session, *, user_id: int, pending_id: int
) -> Optional[PendingCorrection]:
    pending = session.get(PendingCorrection, pending_id)
    if pending is None or pending.user_id != user_id:
        return None
    return pending


def set_message_id(
    session: Session, *, pending: PendingCorrection, message_id: int
) -> None:
    pending.message_id = message_id
    session.add(pending)
    session.commit()


def _set_terminal(
    session: Session, *, pending: PendingCorrection, status: str
) -> None:
    if pending.status != "pending":
        raise PendingNotPending(pending.status)
    pending.status = status
    session.add(pending)
    session.flush()


def mark_applied(session: Session, *, pending: PendingCorrection) -> None:
    _set_terminal(session, pending=pending, status="applied")


def mark_cancelled(session: Session, *, pending: PendingCorrection) -> None:
    _set_terminal(session, pending=pending, status="cancelled")


def expire_for_item(
    session: Session,
    *,
    user_id: int,
    item_id: int,
    exclude_pending_id: Optional[int] = None,
) -> int:
    query = select(PendingCorrection).where(
        PendingCorrection.user_id == user_id,
        PendingCorrection.item_id == item_id,
        PendingCorrection.status == "pending",
    )
    if exclude_pending_id is not None:
        query = query.where(PendingCorrection.id != exclude_pending_id)
    rows = list(session.exec(query).all())
    for row in rows:
        row.status = "stale"
        session.add(row)
    if rows:
        session.flush()
    return len(rows)


def sweep_expired(session: Session, *, now: datetime) -> int:
    now = utc_naive(now)
    rows = list(
        session.exec(
            select(PendingCorrection).where(
                PendingCorrection.status == "pending",
                PendingCorrection.expires_at < now,
            )
        ).all()
    )
    for row in rows:
        row.status = "expired"
        session.add(row)
    if rows:
        session.commit()
    return len(rows)
