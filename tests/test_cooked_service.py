from datetime import UTC, date, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import CookedMeal, Household, User


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def household(session):
    hh = Household(created_at=datetime.now(UTC))
    session.add(hh)
    session.commit()
    session.refresh(hh)
    assert hh.id is not None
    session.add(
        User(
            telegram_id=1,
            chat_id=1,
            household_id=hh.id,
            digest_hour=8,
            created_at=datetime.now(UTC),
        )
    )
    session.commit()
    return hh


def test_cooked_meal_defaults_to_unconfirmed(session, household):
    row = CookedMeal(
        household_id=household.id,
        source="plan",
        recipe_key="spoon:10",
        recipe_title="Chicken Tikka",
        cooked_on=date(2026, 8, 14),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    assert row.confirmed_at is None
    assert row.selection_json == "[]"
    assert row.plan_entry_id is None
