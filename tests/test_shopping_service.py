from datetime import datetime, timezone

from sqlmodel import Session, SQLModel, create_engine, select

from app.cook.logic import missing_ingredients
from app.cook.models import RecipeIngredient
from app.models import Household, ShoppingList, User
from app.shopping_service import add_missing, check_off, list_pending


def _engine():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id is not None
        db.add(User(telegram_id=1, chat_id=1, household_id=household.id,
                    created_at=datetime.now(timezone.utc)))
        db.commit()
    return engine


def _now(minute=0):
    return datetime(2026, 5, 30, 12, minute, tzinfo=timezone.utc)


def test_missing_ingredients_returns_objects_not_in_pantry():
    ings = [RecipeIngredient(name="Pasta", qty=200, unit="g"),
            RecipeIngredient(name="Tomato")]
    missing = missing_ingredients(ingredients=ings, pantry_normalized=["tomato"])
    assert [m.name for m in missing] == ["Pasta"]


def test_add_missing_inserts_and_dedups():
    engine = _engine()
    with Session(engine) as db:
        ings = [RecipeIngredient(name="Pasta", qty=200, unit="g"),
                RecipeIngredient(name="Basil")]
        result = add_missing(db, household_id=1, ingredients=ings, now=_now())
        assert sorted(result.added) == ["Basil", "Pasta"]
        assert result.already == []
        # second add of Pasta is a dup (still pending)
        again = add_missing(db, household_id=1,
                            ingredients=[RecipeIngredient(name="pasta")], now=_now(1))
        assert again.added == []
        assert again.already == ["pasta"]
        rows = db.exec(select(ShoppingList)).all()
        assert len(rows) == 2
        pasta = next(r for r in rows if r.name_normalized == "pasta")
        assert pasta.qty == 200 and pasta.unit == "g"


def test_list_pending_excludes_bought_and_check_off_is_idempotent():
    engine = _engine()
    with Session(engine) as db:
        add_missing(db, household_id=1,
                    ingredients=[RecipeIngredient(name="Eggs")], now=_now())
        pending = list_pending(db, household_id=1)
        assert len(pending) == 1
        sid = pending[0].id
        assert sid is not None
        assert check_off(db, household_id=1, shopping_id=sid, now=_now(5)) is True
        assert list_pending(db, household_id=1) == []
        # idempotent: already bought
        assert check_off(db, household_id=1, shopping_id=sid, now=_now(6)) is False


def test_check_off_rejects_other_users_row():
    engine = _engine()
    with Session(engine) as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id is not None
        db.add(User(telegram_id=2, chat_id=2, household_id=household.id,
                    created_at=datetime.now(timezone.utc)))
        db.commit()
        add_missing(db, household_id=1,
                    ingredients=[RecipeIngredient(name="Milk")], now=_now())
        sid = list_pending(db, household_id=1)[0].id
        assert sid is not None
        assert check_off(db, household_id=2, shopping_id=sid, now=_now(5)) is False
