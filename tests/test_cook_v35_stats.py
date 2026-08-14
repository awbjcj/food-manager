from datetime import UTC, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import CookSession, Household, User
from app.pantry_service import Stats, compute_stats
from app.renderer import render_stats


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


def test_compute_stats_counts_feedback():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
    with Session(engine) as db:
        household = Household(created_at=datetime.now(UTC))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id is not None
        db.add(User(telegram_id=1, chat_id=1, household_id=household.id, created_at=datetime.now(UTC)))
        for fb in ("liked", "liked", "disliked", "none"):
            db.add(CookSession(household_id=household.id, status="done", chat_id=1, selected_item_ids="[]",
                               feedback=fb, created_at=now, expires_at=now))
        db.commit()
        stats = compute_stats(db, household_id=household.id,
                              now=datetime(2026, 5, 30, 13, 0, tzinfo=UTC))
        assert stats.cook_feedback_count == 3  # liked+liked+disliked
        assert stats.cook_liked_count == 2


def test_render_stats_shows_cooked_line():
    stats = Stats(
        receipt_count=0, tracked_item_count=0, removed_item_count=0,
        cache_hit_percent=None, total_cost_micros_usd=0, avg_cost_micros_usd=None,
        unknown_cost_receipt_count=0, waste_rate_percent=None,
        cook_cost_micros_usd=0, cook_count=4,
        cook_feedback_count=3, cook_liked_count=2,
    )
    text = render_stats(stats)
    assert "Cooked: 3" in text
    assert "liked 2" in text


def test_stats_counts_confirmed_cooked_meals(session, household_id):
    from datetime import UTC, date, datetime

    from app.models import CookedMeal
    from app.pantry_service import compute_stats

    now = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
    session.add(
        CookedMeal(
            household_id=household_id,
            source="plan",
            recipe_key="spoon:10",
            recipe_title="Chicken Tikka",
            cooked_on=date(2026, 8, 13),
            confirmed_at=now,
        )
    )
    session.add(
        CookedMeal(
            household_id=household_id,
            source="plan",
            recipe_key="spoon:20",
            recipe_title="Korma",
            cooked_on=date(2026, 8, 13),
        )
    )
    session.commit()
    stats = compute_stats(session, household_id=household_id, now=now)
    assert stats.meals_cooked_count == 1


def test_stats_renders_the_meals_cooked_line():
    from app.i18n import t

    assert t("stats.meals_cooked", "en", count=3) == "  Meals cooked: 3"
