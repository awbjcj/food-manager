import sqlite3
import subprocess


def test_alembic_upgrade_creates_all_tables(tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))

    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    con = sqlite3.connect(str(db))
    cur = con.cursor()
    tables = {
        row[0]
        for row in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "household",
        "user",
        "receipt",
        "pantryitem",
        "shelflifecache",
        "pendingcorrection",
        "groupbinding",
        "pantrybatch",
    }.issubset(tables)

    user_columns = {
        row[1]
        for row in cur.execute("PRAGMA table_info('user')").fetchall()
    }
    assert "llm_provider" in user_columns
    receipt_columns = {row[1] for row in cur.execute("PRAGMA table_info('receipt')")}
    assert "store_name" in receipt_columns

    group_foreign_keys = {
        (row[3], row[2], row[4])
        for row in cur.execute("PRAGMA foreign_key_list('groupbinding')").fetchall()
    }
    assert ("household_id", "household", "id") in group_foreign_keys
    assert ("bound_by_user_id", "user", "telegram_id") in group_foreign_keys

    indexes = {
        row[1]: bool(row[2])
        for row in cur.execute("PRAGMA index_list('receipt')").fetchall()
    }
    unique_columns = {
        tuple(row[2] for row in cur.execute(f"PRAGMA index_info('{name}')").fetchall())
        for name, is_unique in indexes.items()
        if is_unique
    }
    assert ("household_id", "photo_file_id") in unique_columns

    pantry_indexes = {
        row[0]
        for row in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='pantryitem'"
        ).fetchall()
    }
    assert "ix_pantry_household_status_expires" in pantry_indexes
    assert "ix_pantry_household_status_category_expires" in pantry_indexes
    assert "ix_pantry_source_receipt" in pantry_indexes

    pending_indexes = {
        row[0]
        for row in cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='pendingcorrection'"
        ).fetchall()
    }
    assert "ix_pending_household_status_created" in pending_indexes
    assert "ix_pending_item" in pending_indexes
    con.close()


def test_store_batch_upgrade_preserves_existing_receipt_and_downgrades(tmp_path, monkeypatch):
    import sys

    db = tmp_path / "legacy.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))

    def migrate(direction, revision):
        result = subprocess.run([sys.executable, "-m", "alembic", direction, revision],
                                capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr

    migrate("upgrade", "0021_group_binding")
    with sqlite3.connect(db) as con:
        con.execute("""INSERT INTO receipt
            (id, household_id, photo_file_id, purchase_date, purchase_date_source, scanned_at)
            VALUES (1, 1, 'old-receipt', '2026-09-22', 'receipt', '2026-09-23 12:00:00')""")
    migrate("upgrade", "head")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT photo_file_id, purchase_date, store_name FROM receipt").fetchone() == (
            "old-receipt", "2026-09-22", None)
        assert con.execute("SELECT count(*) FROM pantrybatch").fetchone() == (0,)
        assert "AUTOINCREMENT" in con.execute(
            "SELECT sql FROM sqlite_master WHERE name='pantrybatch'").fetchone()[0]
    migrate("downgrade", "0021_group_binding")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT photo_file_id, purchase_date FROM receipt").fetchone() == (
            "old-receipt", "2026-09-22")
        assert "store_name" not in {row[1] for row in con.execute("PRAGMA table_info('receipt')")}
    migrate("upgrade", "head")


def test_preference_migration_preserves_undated_history_and_reverses(tmp_path, monkeypatch):
    import sys
    from datetime import UTC, date, datetime

    from sqlmodel import Session, select

    from app.db import make_engine
    from app.models import Household, PantryItem, PantryOutcome, User
    from app.pantry_service import mark_eaten

    db_path = tmp_path / "preferences-migration.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    def migrate(direction, revision):
        result = subprocess.run([sys.executable, "-m", "alembic", direction, revision],
                                capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr

    migrate("upgrade", "0022_receipt_store_batch")
    engine = make_engine(str(db_path))
    with Session(engine) as db:
        now = datetime(2026, 10, 1, tzinfo=UTC)
        db.add(Household(id=1, created_at=now))
        db.commit()
        db.add(User(telegram_id=1, chat_id=1, household_id=1, created_at=now))
        for item_id, status in ((1, "eaten"), (2, "tossed"), (3, "active")):
            db.add(PantryItem(
                id=item_id, household_id=1, raw_name="apple", normalized_name="apple",
                purchased_on=date(2026, 10, 1), shelf_life_days=10,
                shelf_life_source="llm", ingest_shelf_life_source="llm",
                expires_on=date(2026, 10, 11), status=status,
                created_via="manual", created_at=now,
            ))
        db.commit()
    migrate("upgrade", "head")
    with Session(engine) as db:
        assert db.exec(select(PantryOutcome)).all() == []
        items = [db.get(PantryItem, n) for n in (1, 2, 3)]
        assert all(item is not None for item in items)
        assert [item.status for item in items if item is not None] == ["eaten", "tossed", "active"]
        mark_eaten(db, household_id=1, item_id=3, today=date(2026, 10, 2), user_id=1)
        outcome = db.get(PantryOutcome, 3)
        assert outcome is not None and outcome.user_id == 1
    migrate("downgrade", "0022_receipt_store_batch")
    with sqlite3.connect(db_path) as con:
        assert "pantryoutcome" not in {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert con.execute("SELECT status FROM pantryitem ORDER BY id").fetchall() == [
            ("eaten",), ("tossed",), ("eaten",)]
    migrate("upgrade", "head")
    with Session(engine) as db:
        assert db.exec(select(PantryOutcome)).all() == []
    engine.dispose()
