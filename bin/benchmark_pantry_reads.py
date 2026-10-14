"""Benchmark pantry summary reads using only synthetic, in-memory SQLite data.

Run from the repository root: uv run python -m bin.benchmark_pantry_reads
Seven measured calls use fresh sessions; fixture creation is excluded.
Timing is informational. Tests enforce query and ORM-materialization budgets.
"""

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from statistics import median
from time import perf_counter

from sqlalchemy import event, insert
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
from app.pantry_service import Stats, active_pantry_names, compute_stats


def main() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    now = datetime(2026, 10, 14, 12, tzinfo=UTC)
    today = now.date()
    n = 2000
    with engine.begin() as conn:
        conn.execute(insert(Household), [{"id": 1, "created_at": now}])
        conn.execute(
            insert(Receipt),
            [
                {
                    "household_id": 1,
                    "photo_file_id": str(i),
                    "purchase_date": today,
                    "purchase_date_source": "receipt",
                    "scanned_at": now,
                    "llm_cost_micros_usd": i % 3 or None,
                }
                for i in range(n)
            ],
        )
        conn.execute(
            insert(PantryItem),
            [
                {
                    "household_id": 1,
                    "raw_name": "food " + str(i),
                    "normalized_name": "food " + str(i),
                    "purchased_on": today,
                    "shelf_life_days": 7,
                    "shelf_life_source": "llm",
                    "ingest_shelf_life_source": "llm",
                    "expires_on": today + timedelta(days=1 if i % 2 else -1),
                    "created_via": "receipt",
                    "created_at": now,
                }
                for i in range(n)
            ],
        )
        conn.execute(
            insert(PendingCorrection),
            [
                {
                    "household_id": 1,
                    "action_type": "add" if i % 2 else "correct",
                    "proposed_json": json.dumps("x" * 4096),
                    "llm_cost_micros_usd": i % 3 or None,
                    "chat_id": 1,
                    "created_at": now,
                    "expires_at": now + timedelta(minutes=10),
                }
                for i in range(n)
            ],
        )
        conn.execute(
            insert(CookSession),
            [
                {
                    "household_id": 1,
                    "chat_id": 1,
                    "created_at": now,
                    "expires_at": now + timedelta(minutes=10),
                    "candidates_json": json.dumps("x" * 16384),
                    "llm_cost_micros_usd": i % 3 or None,
                }
                for i in range(n)
            ],
        )
        conn.execute(
            insert(CookedMeal),
            [
                {
                    "household_id": 1,
                    "recipe_key": str(i),
                    "recipe_title": "meal " + str(i),
                    "cooked_on": today,
                    "confirmed_at": now if i % 2 else None,
                    "selection_json": json.dumps(list(range(100))),
                }
                for i in range(n)
            ],
        )

    loads = Counter()
    selects = []

    @event.listens_for(Session, "loaded_as_persistent")
    def loaded(session, instance):
        loads[type(instance).__name__] += 1

    @event.listens_for(engine, "before_cursor_execute")
    def executed(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    checks = (
        ("compute_stats", lambda s: compute_stats(s, household_id=1, now=now)),
        (
            "count_confirmed",
            lambda s: count_confirmed(
                s, household_id=1, since=today - timedelta(days=30)
            ),
        ),
        (
            "active_pantry_names",
            lambda s: active_pantry_names(s, household_id=1, today=today),
        ),
    )
    print(
        json.dumps(
            {
                "fixture": {
                    "rows_per_table": n,
                    "tables": [
                        "Receipt",
                        "PantryItem",
                        "PendingCorrection",
                        "CookSession",
                        "CookedMeal",
                    ],
                    "active_expired": n // 2,
                    "confirmed_meals": n // 2,
                    "pending_payload_chars": 4096,
                    "cook_payload_chars": 16384,
                },
                "samples_per_function": 7,
            }
        )
    )
    for name, fn in checks:
        with Session(engine) as session:
            result = fn(session)
        timings = []
        for _ in range(7):
            loads.clear()
            selects.clear()
            with Session(engine) as session:
                begin = perf_counter()
                result = fn(session)
                timings.append((perf_counter() - begin) * 1000)
        if name == "compute_stats":
            assert isinstance(result, Stats)
            assert (
                result.receipt_count,
                result.tracked_item_count,
                result.meals_cooked_count,
            ) == (n, n, n // 2)
            summary = {
                "receipt_count": result.receipt_count,
                "tracked_item_count": result.tracked_item_count,
                "meals_cooked_count": result.meals_cooked_count,
            }
        elif name == "active_pantry_names":
            assert isinstance(result, list)
            assert result == ["food " + str(i) for i in range(n) if i % 2]
            summary = {"name_count": len(result)}
        else:
            assert result == n // 2
            summary = {"confirmed_count": result}
        print(
            json.dumps(
                {
                    "function": name,
                    "orm_loads": dict(loads),
                    "select_count": len(selects),
                    "median_ms": round(median(timings), 3),
                    "result": summary,
                }
            )
        )
    event.remove(Session, "loaded_as_persistent", loaded)
    event.remove(engine, "before_cursor_execute", executed)
    engine.dispose()


if __name__ == "__main__":
    main()
