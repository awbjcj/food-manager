from datetime import UTC, datetime

from sqlmodel import Session

from app.models import CacheSource, ShelfLifeCache


def get_cached(
    session: Session, household_id: int, normalized_name: str
) -> ShelfLifeCache | None:
    return session.get(ShelfLifeCache, (household_id, normalized_name))


def put_cached(
    session: Session,
    household_id: int,
    normalized_name: str,
    *,
    days: int,
    category: str | None,
    confidence: float,
    source: CacheSource = "llm",
    commit: bool = True,
) -> ShelfLifeCache:
    existing = get_cached(session, household_id, normalized_name)
    if existing is not None:
        return existing
    row = ShelfLifeCache(
        household_id=household_id,
        normalized_name=normalized_name,
        days=days,
        category=category,
        confidence=confidence,
        learned_at=datetime.now(UTC),
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
    household_id: int,
    normalized_name: str,
    *,
    days: int,
    category: str | None = None,
    commit: bool = True,
) -> ShelfLifeCache:
    existing = get_cached(session, household_id, normalized_name)
    now = datetime.now(UTC)
    if existing is None:
        row = ShelfLifeCache(
            household_id=household_id,
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
