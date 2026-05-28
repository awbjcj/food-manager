from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session

from app.models import CacheSource, ShelfLifeCache


def get_cached(
    session: Session, user_id: int, normalized_name: str
) -> Optional[ShelfLifeCache]:
    return session.get(ShelfLifeCache, (user_id, normalized_name))


def put_cached(
    session: Session,
    user_id: int,
    normalized_name: str,
    *,
    days: int,
    category: Optional[str],
    confidence: float,
    source: CacheSource = "llm",
    commit: bool = True,
) -> ShelfLifeCache:
    existing = get_cached(session, user_id, normalized_name)
    if existing is not None:
        return existing
    row = ShelfLifeCache(
        user_id=user_id,
        normalized_name=normalized_name,
        days=days,
        category=category,
        confidence=confidence,
        learned_at=datetime.now(timezone.utc),
        source=source,
    )
    session.add(row)
    if commit:
        session.commit()
        session.refresh(row)
    else:
        session.flush()
    return row


def write_user_correction(
    session: Session,
    user_id: int,
    normalized_name: str,
    *,
    days: int,
    category: Optional[str] = None,
    commit: bool = True,
) -> ShelfLifeCache:
    existing = get_cached(session, user_id, normalized_name)
    now = datetime.now(timezone.utc)
    if existing is None:
        row = ShelfLifeCache(
            user_id=user_id,
            normalized_name=normalized_name,
            days=days,
            category=category,
            confidence=1.0,
            learned_at=now,
            source="user_correction",
        )
        session.add(row)
    else:
        existing.days = days
        existing.source = "user_correction"
        existing.confidence = 1.0
        existing.learned_at = now
        if category is not None:
            existing.category = category
        row = existing
    if commit:
        session.commit()
        session.refresh(row)
    else:
        session.flush()
    return row
