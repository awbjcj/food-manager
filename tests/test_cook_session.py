from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys

import pytest
import sqlalchemy as sa
from sqlmodel import SQLModel, Session, create_engine

from app.cook_session_service import (
    COOK_TTL_MINUTES,
    accrue_cost,
    create_cook_session,
    load_cook_session,
    mark_status,
    sweep_expired_cooks,
)
from app.models import CookSession, User


def _session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    db = Session(engine)
    db.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
    db.commit()
    return db


def test_cook_session_row_persists():
    with _session() as db:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        row = CookSession(
            user_id=1, status="collecting", chat_id=1,
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
        now = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
        first = create_cook_session(db, user_id=1, chat_id=1, now=now)
        second = create_cook_session(db, user_id=1, chat_id=1, now=now)
        db.refresh(first)
        assert first.status == "cancelled"
        assert second.status == "collecting"
        assert load_cook_session(db, user_id=1, cook_id=second.id).id == second.id


def test_accrue_cost_sums():
    with _session() as db:
        now = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
        row = create_cook_session(db, user_id=1, chat_id=1, now=now)
        accrue_cost(db, cook=row, add_micros=100)
        accrue_cost(db, cook=row, add_micros=50)
        assert row.llm_cost_micros_usd == 150


def test_sweep_expires_old_collecting():
    with _session() as db:
        old = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
        row = create_cook_session(db, user_id=1, chat_id=1, now=old)
        swept = sweep_expired_cooks(db, now=old + timedelta(minutes=COOK_TTL_MINUTES + 1))
        db.refresh(row)
        assert swept == 1
        assert row.status == "expired"


def test_mark_status_rejects_invalid_status_without_mutating():
    with _session() as db:
        now = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
        row = create_cook_session(db, user_id=1, chat_id=1, now=now)

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
    )
    assert result.returncode == 0, result.stderr
    engine = sa.create_engine(f"sqlite:///{db_path}")
    assert "cooksession" in sa.inspect(engine).get_table_names()
