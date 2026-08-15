from datetime import UTC, date, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import Household, MealPlan, MealPlanEntry, PantryItem
from app.plan_service import tonight_entry
from app.renderer import render_digest

TODAY = date(2026, 8, 14)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def household_id(session):
    hh = Household(created_at=datetime.now(UTC))
    session.add(hh)
    session.commit()
    session.refresh(hh)
    assert hh.id is not None
    return hh.id


def _item(household_id):
    return PantryItem(
        household_id=household_id,
        raw_name="Milk",
        normalized_name="milk",
        category="dairy",
        qty=1.0,
        purchased_on=TODAY,
        expires_on=date(2026, 8, 16),
        shelf_life_days=2,
        shelf_life_source="llm",
        ingest_shelf_life_source="llm",
        created_via="manual",
        created_at=datetime.now(UTC),
    )


def _plan(session, household_id, *, status="active", entry_date=TODAY):
    plan = MealPlan(
        household_id=household_id,
        start_date=TODAY,
        days=3,
        status=status,
        chat_id=1,
        created_at=datetime.now(UTC),
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)
    assert plan.id is not None
    entry = MealPlanEntry(
        plan_id=plan.id,
        day_index=0,
        date=entry_date,
        recipe_json='{"recipe":{"title":"Chicken Tikka","cuisine":"indian","source_url":"https://x.test/1","ingredients":[],"method_gist":"Cook.","deliciousness":0.5},"nutrition":{"health_score":50,"effort":"easy","est_minutes":20,"rationale":"x"},"expiry_use":0.0,"external_id":"spoon:10","final_score":0.5}',
        spec_json='{"day_index":0,"cuisine":"indian","purpose":"use_it_up","feature_items":[]}',
    )
    session.add(entry)
    session.commit()
    return plan


def test_tonight_entry_finds_todays_day(session, household_id):
    _plan(session, household_id)
    entry = tonight_entry(session, household_id=household_id, today=TODAY)
    assert entry is not None and entry.day_index == 0


def test_tonight_entry_ignores_cancelled_plans(session, household_id):
    _plan(session, household_id, status="cancelled")
    assert tonight_entry(session, household_id=household_id, today=TODAY) is None


def test_tonight_entry_ignores_other_days(session, household_id):
    _plan(session, household_id, entry_date=date(2026, 8, 15))
    assert tonight_entry(session, household_id=household_id, today=TODAY) is None


def test_tonight_entry_is_household_scoped(session, household_id):
    _plan(session, household_id)
    assert tonight_entry(session, household_id=household_id + 999, today=TODAY) is None


def test_digest_without_tonight_is_byte_identical(session, household_id):
    items = [_item(household_id)]
    assert render_digest(items, today=TODAY).text == render_digest(
        items, today=TODAY, tonight=None
    ).text


def test_digest_appends_the_tonight_line(session, household_id):
    rendered = render_digest([_item(household_id)], today=TODAY, tonight="Chicken Tikka")
    assert rendered.text.endswith("\n🍽 Tonight: Chicken Tikka")
