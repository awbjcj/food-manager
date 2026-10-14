"""Read-path contracts and deterministic guards against ORM over-fetching."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from app.cook.novelty import count_confirmed
from app.models import (
    CookedMeal,
    CookSession,
    Household,
    PantryItem,
    PendingCorrection,
    Receipt,
)
from app.pantry_service import Stats, TextLLMCost, active_pantry_names, compute_stats

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)
SINCE = NOW - timedelta(days=30)
TODAY = NOW.date()


@pytest.fixture
def engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(Household(id=i, created_at=NOW) for i in (1, 2))
        session.commit()
    yield engine
    engine.dispose()


def _item(name="milk", **overrides):
    return PantryItem(
        **{
            "household_id": 1,
            "raw_name": name,
            "normalized_name": name,
            "purchased_on": TODAY,
            "shelf_life_days": 7,
            "shelf_life_source": "llm",
            "ingest_shelf_life_source": "llm",
            "expires_on": TODAY,
            "created_via": "receipt",
            "created_at": SINCE,
            **overrides,
        }
    )


def _receipt(key, cost, **overrides):
    return Receipt(
        **{
            "household_id": 1,
            "photo_file_id": key,
            "purchase_date": TODAY,
            "purchase_date_source": "receipt",
            "scanned_at": SINCE,
            "llm_cost_micros_usd": cost,
            **overrides,
        }
    )


def _pending(action, cost, **overrides):
    return PendingCorrection(
        **{
            "household_id": 1,
            "action_type": action,
            "proposed_json": "{}",
            "chat_id": 1,
            "created_at": SINCE,
            "expires_at": NOW,
            "llm_cost_micros_usd": cost,
            **overrides,
        }
    )


def _cook(feedback="none", cost=None, **overrides):
    return CookSession(
        **{
            "household_id": 1,
            "chat_id": 1,
            "created_at": SINCE,
            "expires_at": NOW,
            "feedback": feedback,
            "llm_cost_micros_usd": cost,
            **overrides,
        }
    )


def _meal(**overrides):
    return CookedMeal(
        **{
            "household_id": 1,
            "recipe_key": "meal",
            "recipe_title": "Meal",
            "cooked_on": SINCE.date(),
            "confirmed_at": NOW,
            **overrides,
        }
    )


def test_stats_preserves_empty_and_unknown_costs(engine):
    with Session(engine) as session:
        empty = Stats(0, 0, 0, None, 0, None, 0, None)
        assert compute_stats(session, household_id=1, now=NOW) == empty
        session.add(_receipt("unknown", None))
        # Query must autoflush caller-owned pending writes without committing.
        assert compute_stats(session, household_id=1, now=NOW) == Stats(
            1,
            0,
            0,
            None,
            0,
            None,
            1,
            None,
        )
        session.rollback()
        assert compute_stats(session, household_id=1, now=NOW) == empty


def test_stats_preserves_filters_costs_and_rounding(engine):
    before = SINCE - timedelta(microseconds=1)
    with Session(engine) as session:
        session.add_all(
            [
                _receipt("paid", 5),
                _receipt("free", 0),
                _receipt("unknown", None),
                _receipt("old", 100, scanned_at=before),
                _receipt("foreign", 100, household_id=2),
                _item(ingest_shelf_life_source="cache"),
                _item(status="eaten"),
                _item(status="tossed"),
                _item(status="removed", ingest_shelf_life_source="cache"),
                _item(created_via="manual", ingest_shelf_life_source="cache"),
                _item(ingest_shelf_life_source="user_correction"),
                _item(created_at=before),
                _item(household_id=2),
                _pending("correct", 5, status="stale"),
                _pending("correct", None),
                _pending("add", 0, status="applied"),
                _pending("add", None),
                _pending("other", 999),
                _pending("add", 100, created_at=before),
                _pending("correct", 100, household_id=2),
                _cook("liked", 5),
                _cook("disliked", None),
                _cook(cost=0),
                _cook("liked", 100, created_at=before),
                _cook("liked", 100, household_id=2),
                _meal(),
                _meal(confirmed_at=None),
                _meal(cooked_on=SINCE.date() - timedelta(days=1)),
                _meal(household_id=2),
            ]
        )
        session.commit()
        assert compute_stats(session, household_id=1, now=NOW) == Stats(
            receipt_count=3,
            tracked_item_count=5,
            removed_item_count=1,
            cache_hit_percent=100.0 / 3,
            total_cost_micros_usd=5,
            avg_cost_micros_usd=2,
            unknown_cost_receipt_count=1,
            waste_rate_percent=50.0,
            cook_cost_micros_usd=5,
            cook_count=3,
            cook_feedback_count=2,
            cook_liked_count=1,
            meals_cooked_count=1,
            text_llm=TextLLMCost(2, 5, 1, 2, 0, 1),
        )
        assert count_confirmed(session, household_id=1, since=SINCE.date()) == 1


@pytest.mark.parametrize("cost", [0, 2**53 + 1])
def test_receipt_costs_keep_integer_precision_and_zero_is_known(engine, cost):
    with Session(engine) as session:
        session.add_all([_receipt("known", cost), _receipt("unknown", None)])
        stats = compute_stats(session, household_id=1, now=NOW)
        assert stats.total_cost_micros_usd == cost
        assert stats.avg_cost_micros_usd == cost
        assert stats.unknown_cost_receipt_count == 1


def test_active_names_preserves_duplicates_expiry_order_and_scope(engine):
    with Session(engine) as session:
        session.add_all(
            [
                _item("later", expires_on=TODAY + timedelta(days=1)),
                _item("milk", snoozed_until=TODAY + timedelta(days=2)),
                _item("milk", storage="frozen"),
                _item("expired", expires_on=TODAY - timedelta(days=1)),
                _item("removed", status="removed"),
                _item("eaten", status="eaten"),
                _item("foreign", household_id=2),
            ]
        )
        session.commit()
        assert active_pantry_names(session, household_id=1, today=TODAY) == [
            "milk",
            "milk",
            "later",
        ]
        assert active_pantry_names(session, household_id=3, today=TODAY) == []


@pytest.mark.parametrize("size", [1, 40])
def test_summary_reads_do_not_materialize_history_models(engine, size):
    with Session(engine) as session:
        for i in range(size):
            session.add_all(
                [
                    _receipt(str(i), i),
                    _item(),
                    _pending("add", i),
                    _cook(),
                    _meal(),
                ]
            )
        session.commit()

    queries = []

    def record_query(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(engine, "before_cursor_execute", record_query)
    try:
        # A cold session prevents the identity map from hiding model loads.
        with Session(engine) as session:
            loaded = []
            event.listen(
                session, "loaded_as_persistent", lambda _s, row: loaded.append(row)
            )
            stats = compute_stats(session, household_id=1, now=NOW)
            assert stats.tracked_item_count == stats.meals_cooked_count == size
            assert len(queries) <= 5
            queries.clear()
            assert (
                active_pantry_names(session, household_id=1, today=TODAY)
                == ["milk"] * size
            )
            assert len(queries) == 1
            assert loaded == []
    finally:
        event.remove(engine, "before_cursor_execute", record_query)
