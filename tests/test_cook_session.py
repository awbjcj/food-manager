import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlmodel import Session, SQLModel, create_engine

from app.cook.session_service import (
    COOK_TTL_MINUTES,
    accrue_cost,
    create_cook_session,
    load_cook_session,
    mark_status,
    sweep_expired_cooks,
)
from app.models import CookSession, Household, User


def _session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    db = Session(engine)
    household = Household(created_at=datetime.now(UTC))
    db.add(household)
    db.commit()
    db.refresh(household)
    assert household.id is not None
    db.add(User(telegram_id=1, chat_id=1, household_id=household.id, created_at=datetime.now(UTC)))
    db.commit()
    return db


def test_cook_session_row_persists():
    with _session() as db:
        now = datetime.now(UTC).replace(tzinfo=None)
        row = CookSession(
            household_id=1, status="collecting", chat_id=1,
            selected_item_ids="[]", created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        assert row.id is not None
        assert row.status == "collecting"
        assert row.meal_type is None


def test_create_supersedes_previous_active():
    with _session() as db:
        now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
        first = create_cook_session(db, household_id=1, chat_id=1, now=now)
        second = create_cook_session(db, household_id=1, chat_id=1, now=now)
        db.refresh(first)
        assert first.status == "cancelled"
        assert second.status == "collecting"
        assert second.id is not None
        loaded = load_cook_session(db, household_id=1, cook_id=second.id)
        assert loaded is not None and loaded.id == second.id


def test_accrue_cost_sums():
    with _session() as db:
        now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
        row = create_cook_session(db, household_id=1, chat_id=1, now=now)
        accrue_cost(db, cook=row, add_micros=100)
        accrue_cost(db, cook=row, add_micros=50)
        assert row.llm_cost_micros_usd == 150


def test_sweep_expires_old_collecting():
    with _session() as db:
        old = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
        row = create_cook_session(db, household_id=1, chat_id=1, now=old)
        swept = sweep_expired_cooks(db, now=old + timedelta(minutes=COOK_TTL_MINUTES + 1))
        db.refresh(row)
        assert swept == 1
        assert row.status == "expired"


def test_mark_status_rejects_invalid_status_without_mutating():
    with _session() as db:
        now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
        row = create_cook_session(db, household_id=1, chat_id=1, now=now)

        with pytest.raises(ValueError):
            mark_status(db, cook=row, status="bogus")

        assert row.status == "collecting"
        db.refresh(row)
        assert row.status == "collecting"


def test_migration_0005_creates_cooksession(tmp_path):
    db_path = tmp_path / "m.db"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**dict(os.environ), "DATABASE_PATH": str(db_path)},
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1],
        check=False,
    )
    assert result.returncode == 0, result.stderr
    engine = sa.create_engine(f"sqlite:///{db_path}")
    assert "cooksession" in sa.inspect(engine).get_table_names()


def test_stats_counts_cook_cost():
    from app.pantry_service import compute_stats

    with _session() as db:
        now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
        db.add(CookSession(household_id=1, status="done", chat_id=1, selected_item_ids="[]",
                           llm_cost_micros_usd=500, created_at=now,
                           expires_at=now))
        db.commit()
        stats = compute_stats(db, household_id=1, now=datetime(2026, 5, 30, 13, 0, tzinfo=UTC))
        assert stats.cook_cost_micros_usd == 500
        assert stats.cook_count == 1
