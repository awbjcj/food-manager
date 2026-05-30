import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from sqlmodel import SQLModel, Session, create_engine

from app.models import User


def _session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_user_has_profile_columns_with_defaults():
    with _session() as db:
        user = User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc))
        db.add(user)
        db.commit()
        db.refresh(user)
        assert user.diet == "none"
        assert user.exclusions_json == "[]"
        assert user.preferred_cuisines_json == "[]"
        assert user.max_cook_minutes is None
        assert user.household_size == 1
        assert user.profile_note == ""


def test_migration_0004_adds_profile_columns(tmp_path):
    db_path = tmp_path / "m.db"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**_env(), "DATABASE_PATH": str(db_path)},
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 0, result.stderr
    engine = sa.create_engine(f"sqlite:///{db_path}")
    cols = {c["name"] for c in sa.inspect(engine).get_columns("user")}
    assert {
        "diet",
        "exclusions_json",
        "preferred_cuisines_json",
        "max_cook_minutes",
        "household_size",
        "profile_note",
    } <= cols


def _env():
    import os

    return {k: v for k, v in os.environ.items()}
