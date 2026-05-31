from datetime import date, datetime, timezone

from sqlmodel import Session, SQLModel, create_engine

from app.models import Household, PantryItem
from app.pantry_service import ListFilter, list_active


def _engine_two_households():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        for name in ("A", "B"):
            db.add(Household(name=name, created_at=datetime.now(timezone.utc)))
        db.commit()
    return engine


def test_list_active_is_isolated_per_household():
    engine = _engine_two_households()
    today = date(2026, 5, 30)
    with Session(engine) as db:
        db.add(PantryItem(
            household_id=1,
            raw_name="A-milk",
            normalized_name="milk",
            category="dairy",
            qty=1.0,
            purchased_on=today,
            shelf_life_days=5,
            shelf_life_source="llm",
            ingest_shelf_life_source="llm",
            expires_on=date(2026, 6, 5),
            status="active",
            created_via="receipt",
            created_at=datetime.now(timezone.utc),
        ))
        db.add(PantryItem(
            household_id=2,
            raw_name="B-eggs",
            normalized_name="eggs",
            category="dairy",
            qty=1.0,
            purchased_on=today,
            shelf_life_days=5,
            shelf_life_source="llm",
            ingest_shelf_life_source="llm",
            expires_on=date(2026, 6, 5),
            status="active",
            created_via="receipt",
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
        a = list_active(db, household_id=1, f=ListFilter.default(), today=today)
        b = list_active(db, household_id=2, f=ListFilter.default(), today=today)
    assert [item.raw_name for item in a] == ["A-milk"]
    assert [item.raw_name for item in b] == ["B-eggs"]
