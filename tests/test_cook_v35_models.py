import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import CookSession, Household, SavedRecipe, ShoppingList, User


def _engine():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        household = Household(created_at=datetime.now(UTC))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id is not None
        db.add(User(telegram_id=1, chat_id=1, household_id=household.id,
                    created_at=datetime.now(UTC)))
        db.commit()
    return engine


def test_cooksession_has_feedback_default_none():
    engine = _engine()
    now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
    with Session(engine) as db:
        db.add(CookSession(household_id=1, status="done", chat_id=1,
                           selected_item_ids="[]", created_at=now, expires_at=now))
        db.commit()
        row = db.exec(select(CookSession)).one()
        assert row.feedback == "none"
        assert row.feedback_at is None


def test_shopping_and_saved_tables_accept_rows():
    engine = _engine()
    now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
    with Session(engine) as db:
        db.add(ShoppingList(household_id=1, name_raw="Tomatoes", name_normalized="tomatoes",
                            qty=2.0, unit="kg", added_at=now))
        db.add(SavedRecipe(household_id=1, title="Pasta", cuisine="italian",
                           source_url="https://x", ingredients_json="[]",
                           method_gist="boil", saved_at=now))
        db.commit()
        assert db.exec(select(ShoppingList)).one().name_normalized == "tomatoes"
        assert db.exec(select(SavedRecipe)).one().title == "Pasta"


def test_migration_0006_creates_v35_schema(tmp_path):
    db_path = tmp_path / "m.db"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**dict(os.environ), "DATABASE_PATH": str(db_path)},
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1],
        check=False,
    )
    assert result.returncode == 0, result.stderr
    insp = sa.inspect(sa.create_engine(f"sqlite:///{db_path}"))
    names = insp.get_table_names()
    assert "shoppinglist" in names
    assert "savedrecipe" in names
    cook_cols = {c["name"] for c in insp.get_columns("cooksession")}
    assert {"feedback", "feedback_at"} <= cook_cols
