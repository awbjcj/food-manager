from datetime import date, datetime, timezone

from sqlmodel import Session, SQLModel, create_engine

from app.cook_models import RecipeCandidate, RecipeIngredient
from app.favorites_service import (
    list_saved,
    load_saved,
    recipe_from_saved,
    recook_shopping_list,
    save_candidate,
)
from app.models import Household, PantryItem, User


def _engine():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        db.add(User(telegram_id=1, chat_id=1, household_id=household.id,
                    created_at=datetime.now(timezone.utc)))
        db.commit()
    return engine


def _candidate(title="Pasta", url="https://x"):
    return RecipeCandidate(
        title=title, cuisine="italian", source_url=url,
        ingredients=[RecipeIngredient(name="pasta", qty=200, unit="g"),
                     RecipeIngredient(name="tomato")],
        method_gist="boil", deliciousness=0.7,
    )


def _now():
    return datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)


def test_save_candidate_dedups_by_title_and_url():
    engine = _engine()
    with Session(engine) as db:
        first = save_candidate(db, household_id=1, candidate=_candidate(), now=_now())
        assert first.saved is True and first.duplicate is False
        dup = save_candidate(db, household_id=1, candidate=_candidate(), now=_now())
        assert dup.saved is False and dup.duplicate is True
        assert len(list_saved(db, household_id=1)) == 1


def test_recipe_from_saved_roundtrips_ingredients():
    engine = _engine()
    with Session(engine) as db:
        save_candidate(db, household_id=1, candidate=_candidate(), now=_now())
        saved = list_saved(db, household_id=1)[0]
        recipe = recipe_from_saved(saved)
        assert recipe.title == "Pasta"
        assert [i.name for i in recipe.ingredients] == ["pasta", "tomato"]
        assert recipe.ingredients[0].qty == 200


def test_recook_shopping_list_diffs_against_current_pantry():
    engine = _engine()
    today = date(2026, 5, 30)
    with Session(engine) as db:
        db.add(PantryItem(
            household_id=1, raw_name="Tomato", normalized_name="tomato", category="produce",
            qty=1.0, purchased_on=today, shelf_life_days=5, shelf_life_source="llm",
            ingest_shelf_life_source="llm", expires_on=date(2026, 6, 5), status="active",
            created_via="receipt", created_at=datetime.now(timezone.utc)))
        db.commit()
        save_candidate(db, household_id=1, candidate=_candidate(), now=_now())
        saved = list_saved(db, household_id=1)[0]
        missing = recook_shopping_list(db, household_id=1, saved=saved, today=today)
        assert missing == ["pasta"]  # tomato is in pantry


def test_recook_shopping_list_empty_when_fully_stocked():
    engine = _engine()
    today = date(2026, 5, 30)
    with Session(engine) as db:
        for name in ("pasta", "tomato"):
            db.add(PantryItem(
                household_id=1, raw_name=name, normalized_name=name, category="pantry",
                qty=1.0, purchased_on=today, shelf_life_days=5, shelf_life_source="llm",
                ingest_shelf_life_source="llm", expires_on=date(2026, 6, 5),
                status="active", created_via="receipt",
                created_at=datetime.now(timezone.utc)))
        db.commit()
        save_candidate(db, household_id=1, candidate=_candidate(), now=_now())
        saved = list_saved(db, household_id=1)[0]
        assert recook_shopping_list(db, household_id=1, saved=saved, today=today) == []


def test_load_saved_scopes_to_user():
    engine = _engine()
    with Session(engine) as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        db.add(User(telegram_id=2, chat_id=2, household_id=household.id,
                    created_at=datetime.now(timezone.utc)))
        db.commit()
        save_candidate(db, household_id=1, candidate=_candidate(), now=_now())
        rid = list_saved(db, household_id=1)[0].id
        assert rid is not None
        assert load_saved(db, household_id=2, recipe_id=rid) is None
        assert load_saved(db, household_id=1, recipe_id=rid) is not None
