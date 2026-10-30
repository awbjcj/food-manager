from datetime import UTC, datetime

from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, col

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
    replace_automatic: bool = False,
) -> ShelfLifeCache:
    existing = get_cached(session, household_id, normalized_name)
    if existing is not None and not replace_automatic:
        return existing
    values = {
        "household_id": household_id, "normalized_name": normalized_name,
        "days": days, "category": category, "confidence": confidence,
        "learned_at": datetime.now(UTC), "source": source,
    }
    # A concurrent cache miss must not abort an otherwise valid receipt.
    session.flush()
    statement = insert(ShelfLifeCache).values(**values)
    if replace_automatic:
        statement = statement.on_conflict_do_update(
            index_elements=["household_id", "normalized_name"],
            set_={key: value for key, value in values.items()
                  if key not in {"household_id", "normalized_name"}},
            where=col(ShelfLifeCache.source) != "user_correction",
        )
    else:
        statement = statement.on_conflict_do_nothing(
            index_elements=["household_id", "normalized_name"],
        )
    session.connection().execute(statement)
    if commit:
        session.commit()
    row = session.get(ShelfLifeCache, (household_id, normalized_name))
    assert row is not None
    session.refresh(row)
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
    now = datetime.now(UTC)
    updates = {"days": days, "source": "user_correction", "confidence": 1.0, "learned_at": now}
    if category is not None:
        updates["category"] = category
    session.flush()
    session.connection().execute(
        insert(ShelfLifeCache).values(
            household_id=household_id, normalized_name=normalized_name,
            days=days, category=category, confidence=1.0,
            learned_at=now, source="user_correction",
        ).on_conflict_do_update(
            index_elements=["household_id", "normalized_name"], set_=updates,
        )
    )
    if commit:
        session.commit()
    row = session.get(ShelfLifeCache, (household_id, normalized_name))
    assert row is not None
    session.refresh(row)
    return row
