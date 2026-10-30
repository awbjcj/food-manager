import sqlite3
import subprocess
import sys
from datetime import UTC, datetime

from sqlalchemy import text
from sqlmodel import Session

from app.db import make_engine
from app.models import Household, PaymentEvent, User


def test_identity_upgrade_preserves_ledger_and_guards_post_migration_writes(
    tmp_path, monkeypatch
):
    path = tmp_path / "identity.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))

    def migrate(direction, revision, *, success=True):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", direction, revision],
            capture_output=True,
            text=True,
            check=False,
        )
        assert (result.returncode == 0) == success, result.stderr
        return result

    migrate("upgrade", "0023_pantry_outcome")
    engine = make_engine(str(path))
    with Session(engine) as session:
        session.add(Household(id=1, created_at=datetime(2026, 10, 1, tzinfo=UTC)))
        session.commit()
        session.execute(
            text("""INSERT INTO user (telegram_id,chat_id,household_id,tz,digest_hour,llm_provider,lang,role,banned,created_at)
            VALUES (42,42,1,'UTC',8,'anthropic','zh','owner',0,'2026-10-01 00:00:00')""")
        )
        session.add(
            PaymentEvent(
                household_id=1,
                telegram_charge_id="original-charge",
                kind="topup",
                sku="receipt-pack",
                stars=42,
                payer_telegram_id=42,
                payload_json='{"unaltered":"historical"}',
                created_at=datetime(2026, 10, 1, tzinfo=UTC),
            )
        )
        session.commit()
    with sqlite3.connect(path) as db:
        ledger_before = db.execute("SELECT * FROM paymentevent").fetchall()
    migrate("upgrade", "head")
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT id,telegram_id,chat_id,lang FROM user"
        ).fetchall() == [(42, 42, 42, "zh")]
        assert db.execute(
            "SELECT connection_id,external_user_id,user_id FROM externalidentity"
        ).fetchall() == [("legacy-telegram", "42", 42)]
        assert db.execute(
            "SELECT user_id,external_conversation_id,opted_in FROM notificationdestination"
        ).fetchall() == [(42, "42", 1)]
        assert db.execute("SELECT * FROM paymentevent").fetchall() == ledger_before
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    migrate("downgrade", "0023_pantry_outcome")
    migrate("upgrade", "head")
    with Session(engine) as session:
        session.add(
            User(
                household_id=1,
                role="member",
                created_at=datetime(2026, 10, 1, tzinfo=UTC),
            )
        )
        session.commit()
    result = migrate("downgrade", "0023_pantry_outcome", success=False)
    assert "restore database and application together" in result.stderr
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM user").fetchone()[0] == 2
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE name='messagingmessage'"
        ).fetchone()
        assert db.execute("SELECT * FROM paymentevent").fetchall() == ledger_before
    engine.dispose()
