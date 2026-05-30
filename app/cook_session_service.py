from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.models import CookSession
from app.pending_service import utc_naive


COOK_TTL_MINUTES = 10
ALLOWED_COOK_STATUSES = {"collecting", "ready", "done", "cancelled", "expired"}


def supersede_active(session: Session, *, user_id: int) -> int:
    rows = list(
        session.exec(
            select(CookSession).where(
                CookSession.user_id == user_id,
                CookSession.status.in_(("collecting", "ready")),  # type: ignore[attr-defined]
            )
        ).all()
    )
    for row in rows:
        row.status = "cancelled"
        session.add(row)
    if rows:
        session.flush()
    return len(rows)


def create_cook_session(
    session: Session, *, user_id: int, chat_id: int, now: datetime
) -> CookSession:
    supersede_active(session, user_id=user_id)
    created = utc_naive(now)
    row = CookSession(
        user_id=user_id,
        status="collecting",
        chat_id=chat_id,
        selected_item_ids="[]",
        created_at=created,
        expires_at=created + timedelta(minutes=COOK_TTL_MINUTES),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def load_cook_session(
    session: Session, *, user_id: int, cook_id: int
) -> Optional[CookSession]:
    row = session.get(CookSession, cook_id)
    if row is None or row.user_id != user_id:
        return None
    return row


def set_message_id(session: Session, *, cook: CookSession, message_id: int) -> None:
    cook.message_id = message_id
    session.add(cook)
    session.commit()


def accrue_cost(
    session: Session, *, cook: CookSession, add_micros: Optional[int]
) -> None:
    if not add_micros:
        return
    cook.llm_cost_micros_usd = (cook.llm_cost_micros_usd or 0) + add_micros
    session.add(cook)
    session.commit()


def mark_status(session: Session, *, cook: CookSession, status: str) -> None:
    if status not in ALLOWED_COOK_STATUSES:
        raise ValueError(f"Invalid cook status: {status}")
    cook.status = status
    session.add(cook)
    session.commit()


def sweep_expired_cooks(session: Session, *, now: datetime) -> int:
    now = utc_naive(now)
    rows = list(
        session.exec(
            select(CookSession).where(
                CookSession.status.in_(("collecting", "ready")),  # type: ignore[attr-defined]
                CookSession.expires_at < now,
            )
        ).all()
    )
    for row in rows:
        row.status = "expired"
        session.add(row)
    if rows:
        session.commit()
    return len(rows)
