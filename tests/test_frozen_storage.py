import sqlite3
import subprocess
from datetime import date, datetime, timezone

import pytest
from sqlmodel import SQLModel, Session, create_engine

from app.models import Household, PantryItem, User


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        hh = Household(created_at=datetime.now(timezone.utc))
        db.add(hh)
        db.commit()
        db.refresh(hh)
        assert hh.id is not None
        db.add(User(
            telegram_id=1,
            chat_id=1,
            household_id=hh.id,
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
        yield db


def test_pantryitem_storage_defaults(session):
    today = date(2026, 6, 8)
    item = PantryItem(
        household_id=1,
        raw_name="Chicken",
        normalized_name="chicken",
        category="meat",
        qty=1.0,
        purchased_on=today,
        shelf_life_days=2,
        shelf_life_source="llm",
        ingest_shelf_life_source="llm",
        expires_on=today,
        created_via="receipt",
        created_at=datetime.now(timezone.utc),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    assert item.storage == "default"
    assert item.frozen_on is None


def test_migration_0012_adds_frozen_columns(tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    con = sqlite3.connect(str(db))
    cols = {row[1] for row in con.execute("PRAGMA table_info('pantryitem')").fetchall()}
    con.close()
    assert "storage" in cols
    assert "frozen_on" in cols
