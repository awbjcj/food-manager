# tests/test_household_models.py
from datetime import UTC, datetime

from sqlmodel import Session, SQLModel, create_engine

from app.models import (
    CookSession,
    Household,
    PantryItem,
    PendingCorrection,
    Receipt,
    SavedRecipe,
    ShelfLifeCache,
    ShoppingList,
    User,
)


def _engine():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return engine


def test_household_owns_user_and_holds_profile():
    engine = _engine()
    with Session(engine) as db:
        hh = Household(name="Smiths", diet="vegetarian", created_at=datetime.now(UTC))
        db.add(hh)
        db.commit()
        db.refresh(hh)
        assert hh.id is not None
        db.add(User(telegram_id=1, chat_id=1, household_id=hh.id,
                    created_at=datetime.now(UTC)))
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


def test_shared_tables_are_household_keyed():
    for model in (Receipt, PantryItem, PendingCorrection, CookSession,
                  ShoppingList, SavedRecipe):
        cols = set(model.model_fields)
        assert "household_id" in cols, model.__name__
        assert "user_id" not in cols, model.__name__
    # ShelfLifeCache: composite PK now (household_id, normalized_name)
    assert "household_id" in set(ShelfLifeCache.model_fields)
    assert "user_id" not in set(ShelfLifeCache.model_fields)
