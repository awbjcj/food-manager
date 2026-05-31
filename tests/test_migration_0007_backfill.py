# tests/test_migration_0007_backfill.py
import sqlite3
import subprocess


def _run_to(db, monkeypatch, revision):
    monkeypatch.setenv("DATABASE_PATH", str(db))
    r = subprocess.run(["uv", "run", "alembic", "upgrade", revision],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_existing_user_and_rows_migrate_into_solo_household(tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    # Build the pre-0007 schema and seed one user + one pantry row + one cache row.
    _run_to(db, monkeypatch, "0006_cook_v35")
    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO user (telegram_id, chat_id, tz, digest_hour, llm_provider, "
        "diet, exclusions_json, preferred_cuisines_json, max_cook_minutes, "
        "household_size, profile_note, created_at) VALUES "
        "(7, 7, 'America/Detroit', 8, 'anthropic', 'vegan', '[\"nuts\"]', '[]', "
        "NULL, 2, 'note', '2026-05-01T00:00:00')"
    )
    con.execute(
        "INSERT INTO pantryitem (user_id, raw_name, normalized_name, category, qty, "
        "purchased_on, shelf_life_days, shelf_life_source, ingest_shelf_life_source, "
        "expires_on, status, created_via, created_at) VALUES "
        "(7, 'Milk', 'milk', 'dairy', 1.0, '2026-05-01', 5, 'llm', 'llm', "
        "'2026-05-06', 'active', 'receipt', '2026-05-01T00:00:00')"
    )
    con.execute(
        "INSERT INTO shelflifecache (user_id, normalized_name, days, confidence, "
        "learned_at, source) VALUES (7, 'milk', 5, 0.9, '2026-05-01T00:00:00', 'llm')"
    )
    con.commit()
    con.close()

    _run_to(db, monkeypatch, "head")

    con = sqlite3.connect(str(db))
    cur = con.cursor()
    hh = cur.execute("SELECT id, diet, exclusions_json, household_size, profile_note "
                     "FROM household").fetchall()
    assert len(hh) == 1
    hid, diet, excl, size, note = hh[0]
    assert (diet, excl, size, note) == ("vegan", '["nuts"]', 2, "note")
    assert cur.execute("SELECT household_id FROM user WHERE telegram_id=7").fetchone()[0] == hid
    user_cols = {r[1] for r in cur.execute("PRAGMA table_info('user')").fetchall()}
    assert "diet" not in user_cols and "user_id" not in user_cols
    assert cur.execute("SELECT household_id FROM pantryitem WHERE raw_name='Milk'").fetchone()[0] == hid
    assert cur.execute("SELECT household_id FROM shelflifecache WHERE normalized_name='milk'").fetchone()[0] == hid
    pcols = {r[1] for r in cur.execute("PRAGMA table_info('pantryitem')").fetchall()}
    assert "user_id" not in pcols and "household_id" in pcols
    con.close()
