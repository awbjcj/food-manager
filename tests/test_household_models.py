# tests/test_household_models.py
from datetime import datetime, timezone

from sqlmodel import Session, SQLModel, create_engine

from app.models import Household, User


def _engine():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return engine


def test_household_owns_user_and_holds_profile():
    engine = _engine()
    with Session(engine) as db:
        hh = Household(name="Smiths", diet="vegetarian", created_at=datetime.now(timezone.utc))
        db.add(hh)
        db.commit()
        db.refresh(hh)
        assert hh.id is not None
        db.add(User(telegram_id=1, chat_id=1, household_id=hh.id,
                    created_at=datetime.now(timezone.utc)))
        db.commit()
        loaded = db.get(User, 1)
        assert loaded is not None and loaded.household_id == hh.id


def test_user_no_longer_has_profile_fields():
    # profile fields moved to Household
    assert not hasattr(User(telegram_id=1, chat_id=1, household_id=1,
                            created_at=datetime.now(timezone.utc)), "diet")
