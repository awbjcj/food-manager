from datetime import datetime, timezone

from sqlmodel import Session, SQLModel, create_engine

from app.models import CookSession, Household, User
from app.pantry_service import Stats, compute_stats
from app.renderer import render_stats


def test_compute_stats_counts_feedback():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    now = datetime(2026, 5, 30, 12, 0)
    with Session(engine) as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        db.add(User(telegram_id=1, chat_id=1, household_id=household.id, created_at=datetime.now(timezone.utc)))
        for fb in ("liked", "liked", "disliked", "none"):
            db.add(CookSession(household_id=household.id, status="done", chat_id=1, selected_item_ids="[]",
                               feedback=fb, created_at=now, expires_at=now))
        db.commit()
        stats = compute_stats(db, household_id=household.id,
                              now=datetime(2026, 5, 30, 13, 0, tzinfo=timezone.utc))
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
