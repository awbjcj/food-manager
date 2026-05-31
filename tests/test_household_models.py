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
    _MOVED = {"diet", "exclusions_json", "preferred_cuisines_json",
              "max_cook_minutes", "household_size", "profile_note"}
    user_fields = set(User.model_fields)
    assert _MOVED.isdisjoint(user_fields), f"still on User: {_MOVED & user_fields}"
    hh_fields = set(Household.model_fields)
    assert _MOVED.issubset(hh_fields), f"missing from Household: {_MOVED - hh_fields}"
