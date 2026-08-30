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


from app.cook.cooked_service import (
    confirm,
    count_confirmed,
    list_history,
    load_sheet,
    open_cook_sheet,
    open_sheet,
    toggle,
)
from app.cook.models import (
    NutritionScore,
    RecipeCandidate,
    RecipeIngredient,
    ScoredCandidate,
)
from app.models import CookSession, MealPlan, MealPlanEntry, PantryItem

TODAY = date(2026, 8, 14)


def _candidate(*ingredients):
    return ScoredCandidate(
        recipe=RecipeCandidate(
            title="Chicken Tikka",
            cuisine="indian",
            source_url="https://recipes.test/10",
            ingredients=[RecipeIngredient(name=n) for n in ingredients],
            method_gist="Cook it.",
            deliciousness=0.5,
        ),
        nutrition=NutritionScore(health_score=50, effort="easy", est_minutes=20, rationale="x"),
        expiry_use=0.0,
        external_id="spoon:10",
        final_score=0.5,
    )


def _item(household_id, raw, normalized):
    return PantryItem(
        household_id=household_id,
        raw_name=raw,
        normalized_name=normalized,
        category="other",
        qty=1.0,
        purchased_on=TODAY,
        expires_on=date(2026, 8, 20),
        shelf_life_days=6,
        shelf_life_source="llm",
        ingest_shelf_life_source="llm",
        created_via="manual",
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def entry(session, household):
    plan = MealPlan(
        household_id=household.id,
        start_date=TODAY,
        days=3,
        status="active",
        chat_id=1,
        created_at=datetime.now(UTC),
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)
    assert plan.id is not None
    row = MealPlanEntry(
        plan_id=plan.id,
        day_index=0,
        date=TODAY,
        recipe_json=_candidate("chicken", "yogurt", "saffron").model_dump_json(),
        spec_json='{"day_index":0,"cuisine":"indian","purpose":"use_it_up","feature_items":[]}',
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_open_sheet_preselects_only_matching_pantry_rows(session, household, entry):
    session.add(_item(household.id, "Chicken Thighs", "chicken"))
    session.add(_item(household.id, "Greek Yogurt", "yogurt"))
    session.add(_item(household.id, "Bananas", "banana"))
    session.commit()
    sheet = open_sheet(session, household_id=household.id, entry=entry, today=TODAY)
    assert sorted(c.raw_name for c in sheet.candidates) == ["Chicken Thighs", "Greek Yogurt"]
    assert sheet.selected_ids == {c.item_id for c in sheet.candidates}
    assert sheet.recipe_title == "Chicken Tikka"


def test_open_sheet_is_idempotent_on_a_double_tap(session, household, entry):
    session.add(_item(household.id, "Chicken Thighs", "chicken"))
    session.commit()
    first = open_sheet(session, household_id=household.id, entry=entry, today=TODAY)
    second = open_sheet(session, household_id=household.id, entry=entry, today=TODAY)
    assert first.cooked_id == second.cooked_id
    assert session.query(CookedMeal).count() == 1


def test_open_cook_sheet_persists_candidates_without_a_plan_entry(session, household):
    session.add(_item(household.id, "Chicken Thighs", "chicken"))
    session.add(_item(household.id, "Greek Yogurt", "yogurt"))
    session.commit()
    cook = CookSession(
        household_id=household.id,
        status="done",
        candidates_json=f"[{_candidate('chicken', 'yogurt').model_dump_json()}]",
        chosen_index=0,
        chat_id=1,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
    )
    session.add(cook)
    session.commit()
    session.refresh(cook)

    sheet = open_cook_sheet(
        session, household_id=household.id, cook=cook, today=TODAY
    )
    assert sheet.recipe_title == "Chicken Tikka"
    assert sorted(c.raw_name for c in sheet.candidates) == [
        "Chicken Thighs",
        "Greek Yogurt",
    ]
    dropped = sheet.candidates[0].item_id
    toggled = toggle(
        session,
        household_id=household.id,
        cooked_id=sheet.cooked_id,
        item_id=dropped,
        today=TODAY,
    )
    assert toggled is not None
    assert dropped not in toggled.selected_ids
    assert {c.item_id for c in toggled.candidates} == {
        c.item_id for c in sheet.candidates
    }


def test_history_returns_confirmed_meals_newest_first(session, household):
    for day, title, confirmed in (
        (date(2026, 8, 12), "Older", datetime(2026, 8, 12, 20, tzinfo=UTC)),
        (date(2026, 8, 14), "Newest", datetime(2026, 8, 14, 20, tzinfo=UTC)),
        (date(2026, 8, 15), "Pending", None),
    ):
        session.add(
            CookedMeal(
                household_id=household.id,
                source="cook",
                recipe_key=title.lower(),
                recipe_title=title,
                cooked_on=day,
                confirmed_at=confirmed,
            )
        )
    session.commit()

    rows = list_history(session, household_id=household.id)

    assert [row.recipe_title for row in rows] == ["Newest", "Older"]


def test_toggle_round_trips_one_id(session, household, entry):
    session.add(_item(household.id, "Chicken Thighs", "chicken"))
    session.commit()
    sheet = open_sheet(session, household_id=household.id, entry=entry, today=TODAY)
    item_id = next(iter(sheet.selected_ids))
    off = toggle(session, household_id=household.id, cooked_id=sheet.cooked_id, item_id=item_id, today=TODAY)
    assert off is not None and item_id not in off.selected_ids
    on = toggle(session, household_id=household.id, cooked_id=sheet.cooked_id, item_id=item_id, today=TODAY)
    assert on is not None and item_id in on.selected_ids


def test_confirm_eats_checked_rows_and_stamps_the_record(session, household, entry):
    session.add(_item(household.id, "Chicken Thighs", "chicken"))
    session.add(_item(household.id, "Greek Yogurt", "yogurt"))
    session.commit()
    sheet = open_sheet(session, household_id=household.id, entry=entry, today=TODAY)
    drop = next(c.item_id for c in sheet.candidates if c.raw_name == "Greek Yogurt")
    toggle(session, household_id=household.id, cooked_id=sheet.cooked_id, item_id=drop, today=TODAY)
    result = confirm(
        session,
        household_id=household.id,
        cooked_id=sheet.cooked_id,
        today=TODAY,
        now=datetime.now(UTC),
    )
    assert result is not None
    assert result.eaten_names == ["Chicken Thighs"]
    statuses = {i.raw_name: i.status for i in session.query(PantryItem).all()}
    assert statuses == {"Chicken Thighs": "eaten", "Greek Yogurt": "active"}
    row = session.get(CookedMeal, sheet.cooked_id)
    assert row is not None and row.confirmed_at is not None


def test_confirm_with_nothing_checked_still_records_the_meal(session, household, entry):
    session.add(_item(household.id, "Chicken Thighs", "chicken"))
    session.commit()
    sheet = open_sheet(session, household_id=household.id, entry=entry, today=TODAY)
    for item_id in list(sheet.selected_ids):
        toggle(session, household_id=household.id, cooked_id=sheet.cooked_id, item_id=item_id, today=TODAY)
    result = confirm(
        session,
        household_id=household.id,
        cooked_id=sheet.cooked_id,
        today=TODAY,
        now=datetime.now(UTC),
    )
    assert result is not None and result.eaten_names == []
    assert session.query(PantryItem).first().status == "active"
    assert session.get(CookedMeal, sheet.cooked_id).confirmed_at is not None


def test_confirm_skips_rows_someone_else_already_marked(session, household, entry):
    item = _item(household.id, "Chicken Thighs", "chicken")
    session.add(item)
    session.commit()
    sheet = open_sheet(session, household_id=household.id, entry=entry, today=TODAY)
    item.status = "tossed"
    session.add(item)
    session.commit()
    result = confirm(
        session,
        household_id=household.id,
        cooked_id=sheet.cooked_id,
        today=TODAY,
        now=datetime.now(UTC),
    )
    assert result is not None
    assert result.eaten_names == []
    assert result.skipped == 1


def test_a_foreign_household_cannot_load_or_confirm(session, household, entry):
    session.add(_item(household.id, "Chicken Thighs", "chicken"))
    session.commit()
    sheet = open_sheet(session, household_id=household.id, entry=entry, today=TODAY)
    assert load_sheet(session, household_id=household.id + 999, cooked_id=sheet.cooked_id, today=TODAY) is None
    assert confirm(
        session,
        household_id=household.id + 999,
        cooked_id=sheet.cooked_id,
        today=TODAY,
        now=datetime.now(UTC),
    ) is None


def test_count_confirmed_ignores_unconfirmed_rows(session, household, entry):
    session.add(_item(household.id, "Chicken Thighs", "chicken"))
    session.commit()
    sheet = open_sheet(session, household_id=household.id, entry=entry, today=TODAY)
    assert count_confirmed(session, household_id=household.id, since=date(2026, 7, 1)) == 0
    confirm(
        session,
        household_id=household.id,
        cooked_id=sheet.cooked_id,
        today=TODAY,
        now=datetime.now(UTC),
    )
    assert count_confirmed(session, household_id=household.id, since=date(2026, 7, 1)) == 1
