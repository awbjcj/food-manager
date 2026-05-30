from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys

import sqlalchemy as sa
from sqlmodel import SQLModel, Session, create_engine

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
