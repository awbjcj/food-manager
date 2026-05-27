# Food Manager v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a single-user Telegram bot that ingests grocery receipt photos via a vision LLM, tracks expiry, and sends a daily digest with one-tap action buttons — deployable as one long-running Python process.

**Architecture:** One process. aiogram (long-polling) + APScheduler (`AsyncIOScheduler`) share the event loop. SQLModel over SQLite on a mounted volume; Alembic for migrations. One primary vision call to Claude per receipt, with bounded retries for transport/schema failures, returns structured JSON validated by Pydantic. Two thin Protocols (`LLMClient`, `BotClient`) exist solely so tests inject fakes — no other abstractions.

**Tech Stack:** Python 3.12 / uv · aiogram 3.x · SQLModel + SQLAlchemy 2.x (sync sessions) · Alembic · APScheduler 3.x · Anthropic Python SDK · pydantic-settings · pytest + pytest-asyncio.

**Reference spec:** `docs/superpowers/specs/2026-05-26-food-manager-v1-design.md` — every locked decision, table column, command, and confidence threshold cited below comes from there.

**Conventions used throughout this plan:**
- File paths are repo-relative from `D:/Fun/food-manager/`.
- Tests use `pytest`; run with `uv run pytest <path>`.
- Commits use Conventional Commits (`feat:`, `test:`, `chore:`, etc.) with the trailing `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` line.
- TDD discipline is mandatory: a failing test exists and is observed to fail before any implementation step.
- After each task completes, run the full test suite (`uv run pytest`) to verify no regressions before committing.

---

## Phase 0 — Project scaffolding

Goal: a uv-managed Python project that boots, runs `pytest`, and has secrets loading working.

### Task 0.1 — Initialize project skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `README.md`
- Create: `app/__init__.py` (empty)
- Create: `tests/__init__.py` (empty)

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "food-manager"
version = "0.1.0"
description = "Single-user grocery pantry + expiry-reminder Telegram bot"
requires-python = ">=3.12"
dependencies = [
    "aiogram>=3.10,<4.0",
    "anthropic>=0.39,<1.0",
    "apscheduler>=3.10,<4.0",
    "alembic>=1.13,<2.0",
    "pydantic>=2.7,<3.0",
    "pydantic-settings>=2.4,<3.0",
    "sqlmodel>=0.0.22",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "pytest-mock>=3.14",
    "freezegun>=1.5",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.uv]
managed = true
```

- [ ] **Step 2: Create `.gitignore`**

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
.uv-cache/
.pytest_cache/
*.egg-info/

# Secrets & local state
.env
food.db
food.db*

# SQLite migration backups
*.db.backup-*

# Private test fixtures
tests/fixtures/private_receipts/

# IDE
.vscode/
.idea/
*.swp
```

- [ ] **Step 3: Create `.env.example`**

```
TELEGRAM_BOT_TOKEN=
ALLOWED_TELEGRAM_USER_ID=
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-4-6
DATABASE_PATH=./food.db
LOG_LEVEL=INFO
ENV=dev
```

- [ ] **Step 4: Create minimal `README.md`**

```markdown
# food-manager

Single-user Telegram bot for grocery pantry tracking and expiry reminders.

See `docs/superpowers/specs/2026-05-26-food-manager-v1-design.md` for the spec.

## Quickstart (local dev)

1. `uv sync`
2. Copy `.env.example` to `.env`; fill in `TELEGRAM_BOT_TOKEN`, `ALLOWED_TELEGRAM_USER_ID`, `ANTHROPIC_API_KEY`.
3. `uv run alembic upgrade head`
4. `uv run python bin/run.py`

## Tests

`uv run pytest`
```

- [ ] **Step 5: Create the two empty `__init__.py` files**

```bash
mkdir -p app tests
touch app/__init__.py tests/__init__.py
```

- [ ] **Step 6: Sync deps and verify `pytest` runs (no tests yet, exit 5 is expected)**

```bash
uv sync
uv run pytest
```

Expected: `pytest` reports "no tests ran" (exit code 5). That's success.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .gitignore .env.example README.md app/__init__.py tests/__init__.py
git commit -m "chore: scaffold uv project with deps and pytest config

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 0.2 — Settings module with TDD

**Files:**
- Create: `app/settings.py`
- Create: `tests/test_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_settings.py
import os
import pytest
from app.settings import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "12345")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("DATABASE_PATH", "./food.db")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("ENV", "dev")

    s = Settings()

    assert s.telegram_bot_token == "test-token"
    assert s.allowed_telegram_user_id == 12345
    assert s.anthropic_api_key == "test-api-key"
    assert s.anthropic_model == "claude-sonnet-4-6"
    assert s.database_path == "./food.db"
    assert s.log_level == "INFO"
    assert s.env == "dev"


def test_settings_missing_required_raises(monkeypatch):
    for k in ("TELEGRAM_BOT_TOKEN", "ALLOWED_TELEGRAM_USER_ID",
              "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(Exception):
        Settings()
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_settings.py -v`
Expected: ImportError on `from app.settings import Settings`.

- [ ] **Step 3: Implement `Settings`**

```python
# app/settings.py
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    allowed_telegram_user_id: int = Field(alias="ALLOWED_TELEGRAM_USER_ID")
    anthropic_api_key: str = Field(alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-4-6", alias="ANTHROPIC_MODEL")
    database_path: str = Field(default="./food.db", alias="DATABASE_PATH")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    env: str = Field(default="dev", alias="ENV")
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_settings.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/settings.py tests/test_settings.py
git commit -m "feat(settings): pydantic-settings loader with required + defaults

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 1 — Data model + migrations

Goal: SQLModel definitions match spec §5; Alembic upgrades a temp DB cleanly with all tables, indexes, and uniqueness constraints.

### Task 1.1 — Define SQLModel models

**Files:**
- Create: `app/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from datetime import date, datetime, timezone
import pytest
from sqlmodel import SQLModel, Session, create_engine, select
from app.models import (
    User, PantryItem, Receipt, ShelfLifeCache, Category,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_insert_user_and_pantry_item(session):
    user = User(telegram_id=42, chat_id=42, tz="America/Detroit",
                digest_hour=8, created_at=datetime.now(timezone.utc))
    session.add(user)
    session.commit()

    receipt = Receipt(
        user_id=42, photo_file_id="abc",
        purchase_date=date(2026, 5, 26),
        purchase_date_source="receipt",
        scanned_at=datetime.now(timezone.utc),
        llm_cost_micros_usd=18000,
    )
    session.add(receipt)
    session.commit()

    item = PantryItem(
        user_id=42, raw_name="Whole Milk 1 gal", normalized_name="whole milk",
        category="dairy", qty=1.0, unit="gal",
        purchased_on=date(2026, 5, 26),
        shelf_life_days=7,
        shelf_life_source="llm",
        ingest_shelf_life_source="llm",
        expires_on=date(2026, 6, 2),
        status="active", created_via="receipt",
        source_receipt_id=receipt.id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(item)
    session.commit()

    rows = session.exec(select(PantryItem)).all()
    assert len(rows) == 1
    assert rows[0].normalized_name == "whole milk"


def test_shelf_life_cache_composite_pk(session):
    user = User(telegram_id=42, chat_id=42, created_at=datetime.now(timezone.utc))
    session.add(user); session.commit()

    row = ShelfLifeCache(
        user_id=42, normalized_name="whole milk", days=7,
        category="dairy", confidence=0.9,
        learned_at=datetime.now(timezone.utc), source="llm",
    )
    session.add(row); session.commit()

    fetched = session.get(ShelfLifeCache, (42, "whole milk"))
    assert fetched is not None
    assert fetched.days == 7
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_models.py -v`
Expected: ImportError on `from app.models import ...`.

- [ ] **Step 3: Implement `app/models.py`**

```python
# app/models.py
from __future__ import annotations
from datetime import date, datetime
from typing import Literal, Optional

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel


Category = Literal[
    "dairy", "produce", "meat", "seafood", "bakery",
    "pantry", "frozen", "beverage", "other",
]

Status = Literal["active", "eaten", "tossed", "removed"]
ShelfLifeSource = Literal["cache", "llm", "manual_fallback", "user_correction"]
IngestShelfLifeSource = Literal["cache", "llm", "manual_fallback", "manual_user_hint"]
CreatedVia = Literal["receipt", "manual"]
PurchaseDateSource = Literal["receipt", "scan_fallback"]
CacheSource = Literal["llm", "user_correction"]


class User(SQLModel, table=True):
    telegram_id: int = Field(primary_key=True)
    chat_id: int
    tz: str = "America/Detroit"
    digest_hour: int = 8
    created_at: datetime


class Receipt(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("user_id", "photo_file_id", name="uq_receipt_user_photo"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.telegram_id", index=True)
    photo_file_id: str
    purchase_date: date
    purchase_date_source: str
    scanned_at: datetime
    llm_cost_micros_usd: Optional[int] = None


class PantryItem(SQLModel, table=True):
    __table_args__ = (
        Index("ix_pantry_user_status_expires", "user_id", "status", "expires_on"),
        Index("ix_pantry_user_status_category_expires",
              "user_id", "status", "category", "expires_on"),
        Index("ix_pantry_source_receipt", "source_receipt_id"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.telegram_id", index=True)
    raw_name: str
    normalized_name: str = Field(index=True)
    category: Optional[str] = Field(default=None, index=True)
    qty: float = 1.0
    unit: Optional[str] = None
    purchased_on: date
    shelf_life_days: int
    shelf_life_source: str
    ingest_shelf_life_source: str
    expires_on: date
    status: str = "active"
    snoozed_until: Optional[date] = None
    created_via: str
    source_receipt_id: Optional[int] = Field(default=None, foreign_key="receipt.id")
    created_at: datetime


class ShelfLifeCache(SQLModel, table=True):
    user_id: int = Field(foreign_key="user.telegram_id", primary_key=True)
    normalized_name: str = Field(primary_key=True)
    days: int
    category: Optional[str] = None
    confidence: float
    learned_at: datetime
    source: str = "llm"
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_models.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_models.py
git commit -m "feat(models): SQLModel tables for User, Receipt, PantryItem, ShelfLifeCache

- per-user composite PK on ShelfLifeCache
- (user_id, photo_file_id) unique on Receipt
- digest/list/category compound indexes on PantryItem
- explicit allowed-value aliases/constants per spec §5; SQLModel table
  columns use mappable `str` fields

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 1.2 — DB session factory (sync)

**Files:**
- Create: `app/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
from app.db import make_engine, make_session_factory


def test_make_engine_returns_sqlite_engine(tmp_path):
    db = tmp_path / "t.db"
    engine = make_engine(str(db))
    assert engine.url.database == str(db)


def test_session_factory_yields_session(tmp_path):
    db = tmp_path / "t.db"
    engine = make_engine(str(db))
    SessionFactory = make_session_factory(engine)
    with SessionFactory() as s:
        assert s is not None
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_db.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `app/db.py`**

```python
# app/db.py
from pathlib import Path
from sqlalchemy import Engine
from sqlmodel import Session, create_engine


def make_engine(database_path: str) -> Engine:
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        f"sqlite:///{database_path}",
        echo=False,
        connect_args={"check_same_thread": False},
    )


def make_session_factory(engine: Engine):
    def factory() -> Session:
        return Session(engine)
    return factory
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_db.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat(db): SQLite engine + session factory helpers

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 1.3 — Alembic initial migration

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/0001_initial.py`
- Create: `tests/test_migrations.py`

- [ ] **Step 1: Initialize Alembic skeleton**

```bash
uv run alembic init -t generic migrations
```

This generates `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, and an empty `migrations/versions/`.

- [ ] **Step 2: Edit `alembic.ini` so `sqlalchemy.url` is overridden in `env.py`**

In `alembic.ini`, change `sqlalchemy.url = driver://user:pass@localhost/dbname` to `sqlalchemy.url =` (blank — overridden programmatically).

- [ ] **Step 3: Replace `migrations/env.py` content**

```python
# migrations/env.py
import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel
from alembic import context

import app.models  # noqa: F401  ensure models register on metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

db_path = os.environ.get("DATABASE_PATH", "./food.db")
config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"},
                      render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Generate the initial migration**

```bash
uv run alembic revision --autogenerate -m "initial"
```

This creates `migrations/versions/<hash>_initial.py`. Rename the file to `0001_initial.py` and edit the top of the file so `revision = "0001_initial"` and `down_revision = None`.

- [ ] **Step 5: Write the migration test**

```python
# tests/test_migrations.py
import subprocess
import sqlite3
from pathlib import Path


def test_alembic_upgrade_creates_all_tables(tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))

    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    con = sqlite3.connect(str(db))
    cur = con.cursor()
    tables = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert {"user", "receipt", "pantryitem", "shelflifecache"}.issubset(tables)

    indexes = {r[1]: bool(r[2]) for r in cur.execute(
        "PRAGMA index_list('receipt')"
    ).fetchall()}
    unique_indexes = [name for name, is_unique in indexes.items() if is_unique]
    unique_columns = {
        tuple(r[2] for r in cur.execute(f"PRAGMA index_info('{name}')").fetchall())
        for name in unique_indexes
    }
    assert ("user_id", "photo_file_id") in unique_columns

    pantry_indexes = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='pantryitem'"
    ).fetchall()}
    assert "ix_pantry_user_status_expires" in pantry_indexes
    assert "ix_pantry_user_status_category_expires" in pantry_indexes
    assert "ix_pantry_source_receipt" in pantry_indexes
    con.close()
```

- [ ] **Step 6: Run the migration test, expect PASS**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: 1 passed.

If autogenerate missed the named PantryItem indexes, hand-edit `migrations/versions/0001_initial.py` to add them. Do not assert the SQLite auto-index name for `Receipt(user_id, photo_file_id)`; SQLite may expose that unique constraint as `sqlite_autoindex_*`.

- [ ] **Step 7: Commit**

```bash
git add alembic.ini migrations/ tests/test_migrations.py
git commit -m "feat(db): alembic initial migration with named indexes/constraints

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 2 — Normalization, cache, shelf-life defaults

Goal: pure, fast, deterministic helpers around the cache lookup path. These have zero external deps and form the high-leverage testable core.

### Task 2.1 — `normalize()` with TODO scaffold

**Files:**
- Create: `app/normalization.py`
- Create: `tests/test_normalization.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_normalization.py
import pytest
from app.normalization import normalize, ALIASES


@pytest.mark.parametrize("raw,expected", [
    ("Whole Milk 1 gal", "whole milk"),
    ("WHOLE MILK", "whole milk"),
    ("  whole   milk  ", "whole milk"),
    ("Bananas, 6 ct", "bananas"),
    ("Sliced Bread 24 oz", "sliced bread"),
    ("Greek Yogurt 32 oz", "greek yogurt"),
    ("Organic Whole Milk 1 gal", "whole milk"),
    ("Frozen Peas 12 oz", "frozen peas"),
])
def test_normalize_baseline_rules(raw, expected):
    assert normalize(raw) == expected


def test_aliases_dict_exists():
    assert isinstance(ALIASES, dict)
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_normalization.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `app/normalization.py`**

```python
# app/normalization.py
"""Convert raw LLM item names to canonical cache keys.

TODO(user): tune the rules in `normalize()` and seed `ALIASES`. See
spec §10 — this function determines cache hit rate over time. Examples
to honor (already covered by tests):

  - lowercase + collapse whitespace
  - strip trailing size/qty suffixes (`1 gal`, `12 oz`, `6 ct`, `dozen`)
  - drop marketing adjectives that don't change shelf life
    (`organic`, `fresh`, `large`, `family size`)
  - PRESERVE form/state words that DO change shelf life
    (`frozen`, `cut`, `sliced`, `cooked`, `raw`)
  - apply ALIASES last
"""

import re

ALIASES: dict[str, str] = {
    # TODO(user): seed from your typical receipt vocabulary, e.g.:
    # "whl mlk": "whole milk",
}

_ADJECTIVES_TO_STRIP = {
    "organic", "fresh", "large", "small", "medium",
    "family", "size", "natural", "premium",
}

_SIZE_SUFFIX = re.compile(
    r"\s*(?:\d+(?:\.\d+)?\s*"
    r"(?:gal|gallon|gallons|oz|lb|lbs|g|kg|ml|l|ct|count|pk|pack|bunch)"
    r"|dozen)\s*$",
    flags=re.IGNORECASE,
)


def normalize(raw: str) -> str:
    s = raw.lower().strip()
    s = s.replace(",", " ")
    while True:
        new = _SIZE_SUFFIX.sub("", s).strip()
        if new == s:
            break
        s = new
    tokens = [t for t in re.split(r"\s+", s) if t]
    tokens = [t for t in tokens if t not in _ADJECTIVES_TO_STRIP]
    s = " ".join(tokens)
    s = re.sub(r"\s+", " ", s).strip()
    return ALIASES.get(s, s)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_normalization.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add app/normalization.py tests/test_normalization.py
git commit -m "feat(normalization): baseline normalize() + ALIASES TODO scaffold

Covers the spec §10 rules: lowercase, collapse whitespace, strip
size/qty suffixes, drop marketing adjectives, preserve form/state
words, apply user-curated ALIASES last.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2.2 — Cache read/write with user-correction priority

**Files:**
- Create: `app/cache.py`
- Create: `tests/test_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cache.py
from datetime import datetime, timezone
import pytest
from sqlmodel import SQLModel, Session, create_engine
from app.models import User, ShelfLifeCache
from app.cache import get_cached, put_cached, write_user_correction


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def test_get_returns_none_when_missing(session):
    assert get_cached(session, 1, "whole milk") is None


def test_put_then_get(session):
    put_cached(session, 1, "whole milk", days=7,
               category="dairy", confidence=0.9, source="llm")
    row = get_cached(session, 1, "whole milk")
    assert row is not None
    assert row.days == 7 and row.source == "llm"


def test_put_does_not_overwrite_existing_llm_value(session):
    put_cached(session, 1, "whole milk", days=7,
               category="dairy", confidence=0.9, source="llm")
    put_cached(session, 1, "whole milk", days=10,
               category="dairy", confidence=0.7, source="llm")
    assert get_cached(session, 1, "whole milk").days == 7


def test_user_correction_overwrites_llm(session):
    put_cached(session, 1, "whole milk", days=7,
               category="dairy", confidence=0.9, source="llm")
    write_user_correction(session, 1, "whole milk", days=5)
    row = get_cached(session, 1, "whole milk")
    assert row.days == 5 and row.source == "user_correction"


def test_cache_is_user_scoped(session):
    session.add(User(telegram_id=2, chat_id=2,
                     created_at=datetime.now(timezone.utc)))
    session.commit()
    put_cached(session, 1, "whole milk", days=7,
               category="dairy", confidence=0.9, source="llm")
    assert get_cached(session, 2, "whole milk") is None
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_cache.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `app/cache.py`**

```python
# app/cache.py
from datetime import datetime, timezone
from typing import Optional
from sqlmodel import Session
from app.models import ShelfLifeCache, CacheSource


def get_cached(session: Session, user_id: int,
               normalized_name: str) -> Optional[ShelfLifeCache]:
    return session.get(ShelfLifeCache, (user_id, normalized_name))


def put_cached(session: Session, user_id: int, normalized_name: str, *,
               days: int, category: Optional[str], confidence: float,
               source: CacheSource = "llm") -> ShelfLifeCache:
    """Insert if missing; never overwrite an existing LLM-sourced value
    with another LLM-sourced value. Use write_user_correction() for that.
    """
    existing = get_cached(session, user_id, normalized_name)
    if existing is not None:
        return existing
    row = ShelfLifeCache(
        user_id=user_id, normalized_name=normalized_name,
        days=days, category=category, confidence=confidence,
        learned_at=datetime.now(timezone.utc), source=source,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def write_user_correction(session: Session, user_id: int,
                          normalized_name: str, *, days: int,
                          category: Optional[str] = None) -> ShelfLifeCache:
    existing = get_cached(session, user_id, normalized_name)
    now = datetime.now(timezone.utc)
    if existing is None:
        row = ShelfLifeCache(
            user_id=user_id, normalized_name=normalized_name,
            days=days, category=category, confidence=1.0,
            learned_at=now, source="user_correction",
        )
        session.add(row)
    else:
        existing.days = days
        existing.source = "user_correction"
        existing.confidence = 1.0
        existing.learned_at = now
        if category is not None:
            existing.category = category
        row = existing
    session.commit()
    session.refresh(row)
    return row
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_cache.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/cache.py tests/test_cache.py
git commit -m "feat(cache): per-user shelf-life cache with user-correction priority

Per spec §6.2: first LLM value sticks until a user correction lands;
user corrections always overwrite and bump learned_at. Cache is
user-scoped so future multi-user does not cross-contaminate kitchens.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2.3 — Conservative shelf-life defaults for `/add`

**Files:**
- Create: `app/shelf_life_defaults.py`
- Create: `tests/test_shelf_life_defaults.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shelf_life_defaults.py
from app.shelf_life_defaults import lookup_default


def test_exact_name_hit_returns_days_and_category():
    res = lookup_default("whole milk")
    assert res is not None and res.days == 7 and res.category == "dairy"


def test_category_hit_returns_conservative_days():
    res = lookup_default("kefir lime leaves")
    assert res is None  # name unknown, no category info either


def test_unknown_returns_none():
    assert lookup_default("blahblahblah") is None
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_shelf_life_defaults.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `app/shelf_life_defaults.py`**

```python
# app/shelf_life_defaults.py
"""Conservative fallback values for manual /add when the cache misses
and the user provides no explicit shelf-life hint.

TODO(user): per spec §10.3, expand both maps to reflect your kitchen.
Keep these CONSERVATIVE — they should under-estimate rather than
over-estimate. Real values get learned via /correct.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DefaultEntry:
    days: int
    category: Optional[str]


_EXACT: dict[str, DefaultEntry] = {
    "whole milk": DefaultEntry(7, "dairy"),
    "milk": DefaultEntry(7, "dairy"),
    "eggs": DefaultEntry(21, "dairy"),
    "butter": DefaultEntry(30, "dairy"),
    "yogurt": DefaultEntry(14, "dairy"),
    "bread": DefaultEntry(5, "bakery"),
    "bananas": DefaultEntry(5, "produce"),
    "apples": DefaultEntry(21, "produce"),
    "chicken": DefaultEntry(2, "meat"),
    "ground beef": DefaultEntry(2, "meat"),
    "salmon": DefaultEntry(2, "seafood"),
}


def lookup_default(normalized_name: str) -> Optional[DefaultEntry]:
    return _EXACT.get(normalized_name)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_shelf_life_defaults.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/shelf_life_defaults.py tests/test_shelf_life_defaults.py
git commit -m "feat(shelf_life_defaults): conservative fallbacks for /add cache misses

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 3 — LLM client

Goal: a thin, testable wrapper around Anthropic's vision call. Production calls Claude; tests inject a fake. Schema enforcement + bounded retry sit in one place.

### Task 3.1 — `LLMClient` Protocol + `FakeLLMClient`

**Files:**
- Create: `app/llm.py`
- Create: `tests/fakes.py`
- Create: `tests/test_llm_protocol.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_protocol.py
from app.llm import LLMClient, ParseResult, ParsedItem, LLMResult
from tests.fakes import FakeLLMClient


def test_fake_returns_canned_result():
    canned = ParseResult(
        purchase_date=None,
        purchase_date_confidence=0.0,
        items=[ParsedItem(
            is_food=True, name="Whole Milk 1 gal", qty=1.0, unit="gal",
            category="dairy", est_shelf_life_days=7, confidence=0.95,
        )],
    )
    fake: LLMClient = FakeLLMClient(canned=LLMResult(
        parse=canned, cost_micros_usd=15000,
    ))
    import asyncio
    result = asyncio.run(fake.extract_items_from_image(b"fake-bytes"))
    assert result.parse.items[0].name == "Whole Milk 1 gal"
    assert result.cost_micros_usd == 15000
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_llm_protocol.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `app/llm.py` (Protocol + Pydantic models only — real client in Task 3.2)**

```python
# app/llm.py
from __future__ import annotations
from datetime import date
from typing import Any, Literal, Optional, Protocol

from pydantic import BaseModel, Field


Category = Literal[
    "dairy", "produce", "meat", "seafood", "bakery",
    "pantry", "frozen", "beverage", "other",
]


class ParsedItem(BaseModel):
    is_food: bool
    name: str
    qty: float = 1.0
    unit: Optional[str] = None
    category: Optional[Category] = None
    est_shelf_life_days: int = Field(ge=1, le=730)
    confidence: float = Field(ge=0.0, le=1.0)


class ParseResult(BaseModel):
    purchase_date: Optional[date] = None
    purchase_date_confidence: float = 0.0
    items: list[ParsedItem]


class LLMResult(BaseModel):
    parse: ParseResult
    cost_micros_usd: Optional[int] = None
    provider_usage: Optional[dict[str, Any]] = None


class LLMClient(Protocol):
    async def extract_items_from_image(self, image_bytes: bytes) -> LLMResult: ...
```

- [ ] **Step 4: Implement `tests/fakes.py`**

```python
# tests/fakes.py
from dataclasses import dataclass, field
from typing import Iterator, Optional
from app.llm import LLMClient, LLMResult


@dataclass
class FakeLLMClient:
    canned: Optional[LLMResult] = None
    canned_sequence: Optional[Iterator[LLMResult]] = None
    calls: list[bytes] = field(default_factory=list)
    raise_n_times: int = 0
    _raises: int = 0

    async def extract_items_from_image(self, image_bytes: bytes) -> LLMResult:
        self.calls.append(image_bytes)
        if self._raises < self.raise_n_times:
            self._raises += 1
            raise RuntimeError("simulated LLM failure")
        if self.canned_sequence is not None:
            return next(self.canned_sequence)
        assert self.canned is not None
        return self.canned
```

- [ ] **Step 5: Run tests, expect PASS**

Run: `uv run pytest tests/test_llm_protocol.py -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add app/llm.py tests/fakes.py tests/test_llm_protocol.py
git commit -m "feat(llm): LLMClient Protocol + Pydantic schemas + FakeLLMClient

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3.2 — `AnthropicLLMClient` real implementation

**Files:**
- Modify: `app/llm.py` — append the real client class
- Create: `tests/test_llm_anthropic.py`

- [ ] **Step 1: Write the failing test (uses a stubbed SDK)**

```python
# tests/test_llm_anthropic.py
import base64
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.llm import AnthropicLLMClient, LLMResult


class _StubMessage:
    def __init__(self, text: str, usage: dict):
        self.content = [MagicMock(type="text", text=text)]
        self.usage = MagicMock(input_tokens=usage["in"], output_tokens=usage["out"])


@pytest.mark.asyncio
async def test_anthropic_client_parses_text_response():
    fake_text = json.dumps({
        "purchase_date": "2026-05-26",
        "purchase_date_confidence": 0.9,
        "items": [{
            "is_food": True, "name": "Whole Milk 1 gal",
            "qty": 1.0, "unit": "gal", "category": "dairy",
            "est_shelf_life_days": 7, "confidence": 0.95,
        }],
    })

    sdk = MagicMock()
    sdk.messages.create = AsyncMock(
        return_value=_StubMessage(fake_text, {"in": 1500, "out": 220})
    )

    client = AnthropicLLMClient(sdk=sdk, model="claude-sonnet-4-6")
    res: LLMResult = await client.extract_items_from_image(b"\xff\xd8jpeg-bytes")

    assert res.parse.items[0].name == "Whole Milk 1 gal"
    assert res.cost_micros_usd is not None and res.cost_micros_usd > 0

    args, kwargs = sdk.messages.create.call_args
    assert kwargs["model"] == "claude-sonnet-4-6"
    msg_content = kwargs["messages"][0]["content"]
    assert msg_content[0]["type"] == "image"
    assert msg_content[0]["source"]["data"] == base64.b64encode(b"\xff\xd8jpeg-bytes").decode()


@pytest.mark.asyncio
async def test_anthropic_client_retries_on_malformed_json():
    bad = "not json at all"
    good = json.dumps({
        "purchase_date": None,
        "purchase_date_confidence": 0.0,
        "items": [{"is_food": True, "name": "X", "qty": 1.0,
                   "unit": None, "category": "other",
                   "est_shelf_life_days": 5, "confidence": 0.9}],
    })
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(side_effect=[
        _StubMessage(bad, {"in": 100, "out": 10}),
        _StubMessage(good, {"in": 100, "out": 10}),
    ])
    client = AnthropicLLMClient(sdk=sdk, model="claude-sonnet-4-6")
    res = await client.extract_items_from_image(b"img")
    assert res.parse.items[0].name == "X"
    assert sdk.messages.create.call_count == 2


@pytest.mark.asyncio
async def test_anthropic_client_gives_up_after_one_correction():
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(side_effect=[
        _StubMessage("garbage", {"in": 1, "out": 1}),
        _StubMessage("still garbage", {"in": 1, "out": 1}),
    ])
    client = AnthropicLLMClient(sdk=sdk, model="claude-sonnet-4-6")
    with pytest.raises(Exception):
        await client.extract_items_from_image(b"img")
    assert sdk.messages.create.call_count == 2


@pytest.mark.asyncio
async def test_anthropic_client_retries_empty_items_once():
    empty = json.dumps({
        "purchase_date": None,
        "purchase_date_confidence": 0.0,
        "items": [],
    })
    good = json.dumps({
        "purchase_date": None,
        "purchase_date_confidence": 0.0,
        "items": [{"is_food": True, "name": "Milk", "qty": 1.0,
                   "unit": None, "category": "dairy",
                   "est_shelf_life_days": 7, "confidence": 0.9}],
    })
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(side_effect=[
        _StubMessage(empty, {"in": 100, "out": 10}),
        _StubMessage(good, {"in": 100, "out": 10}),
    ])
    client = AnthropicLLMClient(sdk=sdk, model="claude-sonnet-4-6")
    res = await client.extract_items_from_image(b"img")
    assert res.parse.items[0].name == "Milk"
    assert sdk.messages.create.call_count == 2


@pytest.mark.asyncio
async def test_anthropic_client_retries_transport_errors_twice():
    good = json.dumps({
        "purchase_date": None,
        "purchase_date_confidence": 0.0,
        "items": [{"is_food": True, "name": "Milk", "qty": 1.0,
                   "unit": None, "category": "dairy",
                   "est_shelf_life_days": 7, "confidence": 0.9}],
    })
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(side_effect=[
        RuntimeError("temporary"),
        RuntimeError("still temporary"),
        _StubMessage(good, {"in": 100, "out": 10}),
    ])
    sleep = AsyncMock()
    client = AnthropicLLMClient(sdk=sdk, model="claude-sonnet-4-6", sleep=sleep)
    res = await client.extract_items_from_image(b"img")
    assert res.parse.items[0].name == "Milk"
    assert sdk.messages.create.call_count == 3
    assert sleep.await_count == 2


@pytest.mark.asyncio
async def test_unknown_model_cost_is_unavailable():
    good = json.dumps({
        "purchase_date": None,
        "purchase_date_confidence": 0.0,
        "items": [{"is_food": True, "name": "Milk", "qty": 1.0,
                   "unit": None, "category": "dairy",
                   "est_shelf_life_days": 7, "confidence": 0.9}],
    })
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(
        return_value=_StubMessage(good, {"in": 100, "out": 10})
    )
    client = AnthropicLLMClient(sdk=sdk, model="future-model")
    res = await client.extract_items_from_image(b"img")
    assert res.cost_micros_usd is None
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_llm_anthropic.py -v`
Expected: ImportError on `AnthropicLLMClient`.

- [ ] **Step 3: Append `AnthropicLLMClient` to `app/llm.py`**

```python
# append at end of app/llm.py
import asyncio
import base64
import json
import logging
from typing import Any

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You parse grocery receipt photos.
Return ONLY valid JSON matching the schema. No prose.

Receipt-level fields:
  - purchase_date: YYYY-MM-DD date shown on the receipt, or null if unreadable
  - purchase_date_confidence: 0.0-1.0 how sure you are about purchase_date

Return all recognizable purchased line items, excluding store metadata,
subtotals, totals, taxes, discounts, coupons, and payment lines. For each
returned line item:
  - is_food: true if this is a pantry-relevant food item, false for purchased
    non-food items such as paper towels or bags
  - name: clean human-readable name ("Whole Milk 1 gal"), expand abbreviations
  - qty:  display-oriented purchased quantity (1.0 if ambiguous)
  - unit: "gal"|"lb"|"oz"|"g"|"kg"|"ml"|"l"|"ct"|"bunch"|"each"|null
  - category: "dairy"|"produce"|"meat"|"seafood"|"bakery"|"pantry"|"frozen"|"beverage"|"other"
  - est_shelf_life_days: integer 1..730. Conservative estimates. Examples:
        whole milk = 7, fresh chicken = 2, bananas = 5,
        canned beans = 365, fresh bread = 4, eggs = 28
  - confidence: 0.0–1.0

TODO(user): tune the example shelf-life values above to your kitchen.
"""


# Anthropic message pricing (micros USD per token), keyed by model.
# TODO(user): update if pricing changes. Used only for advisory /stats accounting.
_PRICE_MICROS_PER_TOKEN_BY_MODEL = {
    "claude-sonnet-4-6": {"input": 3, "output": 15},
}


def _extract_json_text(message) -> str:
    parts = [b.text for b in message.content if getattr(b, "type", None) == "text"]
    return "".join(parts).strip()


def _cost_micros(message, model: str) -> int | None:
    price = _PRICE_MICROS_PER_TOKEN_BY_MODEL.get(model)
    if price is None:
        return None
    u = getattr(message, "usage", None)
    if u is None:
        return None
    try:
        return (u.input_tokens * price["input"]
                + u.output_tokens * price["output"])
    except Exception:
        return None


class AnthropicLLMClient:
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._sdk = sdk
        self._model = model
        self._sleep = sleep

    async def _create_message(self, user_content):
        for attempt in range(3):
            try:
                return await self._sdk.messages.create(
                    model=self._model, max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_content}],
                )
            except Exception as e:
                if attempt == 2:
                    log.warning("llm_transport_failed_final",
                                extra={"error_class": type(e).__name__})
                    raise
                log.warning("llm_transport_failed_retrying",
                            extra={"error_class": type(e).__name__})
                await self._sleep(2 ** attempt)

    async def extract_items_from_image(self, image_bytes: bytes) -> LLMResult:
        b64 = base64.b64encode(image_bytes).decode()
        user_content = [
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": "Parse this receipt."},
        ]

        total_cost = 0
        unknown_cost = False
        for attempt in (0, 1):
            msg = await self._create_message(user_content)
            c = _cost_micros(msg, self._model)
            if c is not None:
                total_cost += c
            else:
                unknown_cost = True

            text = _extract_json_text(msg)
            try:
                data = json.loads(text)
                parse = ParseResult.model_validate(data)
                if not parse.items:
                    raise ValueError("empty items")
                return LLMResult(parse=parse,
                                 cost_micros_usd=None if unknown_cost else total_cost)
            except Exception as e:
                if attempt == 1:
                    log.warning("llm_json_validation_failed_final",
                                extra={"error_class": type(e).__name__})
                    raise
                user_content = [
                    *user_content,
                    {"type": "text",
                     "text": (f"Your last response did not match the schema "
                              f"(error: {type(e).__name__}). "
                              f"Return ONLY valid JSON matching the schema.")},
                ]
        raise RuntimeError("unreachable")
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_llm_anthropic.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add app/llm.py tests/test_llm_anthropic.py
git commit -m "feat(llm): AnthropicLLMClient with bounded transport and schema retries

Primary vision call returns structured JSON parsed against ParseResult.
Transport/API failures retry twice with backoff. Malformed, invalid, or
empty-item output retries once with a correction message appended. Cost
is advisory and unavailable for unknown model pricing.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 4 — Ingest service

Goal: the orchestrator that turns a photo (or text) into committed `PantryItem` rows. Embeds the confidence-tier policy from spec §6.3 and the duplicate-receipt guard.

### Task 4.1 — `compute_shelf_life()` decision

**Files:**
- Create: `app/ingest_service.py`
- Create: `tests/test_compute_shelf_life.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compute_shelf_life.py
from datetime import datetime, timezone
import pytest
from sqlmodel import SQLModel, Session, create_engine
from app.models import User, ShelfLifeCache
from app.cache import put_cached, write_user_correction
from app.llm import ParsedItem
from app.ingest_service import compute_shelf_life


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1,
                   created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def _item(name="Whole Milk 1 gal", days=7, conf=0.95):
    return ParsedItem(is_food=True, name=name, qty=1.0, unit="gal",
                      category="dairy", est_shelf_life_days=days,
                      confidence=conf)


def test_miss_uses_llm_and_writes_cache(session):
    d = compute_shelf_life(session, user_id=1, parsed=_item())
    assert d.days == 7 and d.source == "llm" and d.cache_was_hit is False
    from app.cache import get_cached
    assert get_cached(session, 1, "whole milk").days == 7


def test_hit_returns_cached_value(session):
    put_cached(session, 1, "whole milk", days=10,
               category="dairy", confidence=0.9, source="llm")
    d = compute_shelf_life(session, user_id=1, parsed=_item(days=7))
    assert d.days == 10 and d.source == "cache" and d.cache_was_hit is True


def test_user_correction_outranks_new_llm_estimate(session):
    write_user_correction(session, 1, "whole milk", days=5)
    d = compute_shelf_life(session, user_id=1, parsed=_item(days=7))
    assert d.days == 5 and d.source == "cache"


def test_medium_confidence_does_not_write_cache(session):
    d = compute_shelf_life(session, user_id=1, parsed=_item(conf=0.5))
    assert d.days == 7 and d.source == "llm" and d.cache_was_hit is False
    from app.cache import get_cached
    assert get_cached(session, 1, "whole milk") is None


def test_high_confidence_writes_cache(session):
    compute_shelf_life(session, user_id=1, parsed=_item(conf=0.8))
    from app.cache import get_cached
    assert get_cached(session, 1, "whole milk") is not None
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_compute_shelf_life.py -v`
Expected: ImportError on `app.ingest_service`.

- [ ] **Step 3: Implement `app/ingest_service.py` (just the decision helper for now)**

```python
# app/ingest_service.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
from sqlmodel import Session
from app.cache import get_cached, put_cached
from app.llm import ParsedItem
from app.normalization import normalize


@dataclass(frozen=True)
class ShelfLifeDecision:
    days: int
    source: Literal["cache", "llm"]
    cache_was_hit: bool


CONFIDENCE_FOR_CACHE_WRITE = 0.6


def compute_shelf_life(session: Session, *, user_id: int,
                       parsed: ParsedItem) -> ShelfLifeDecision:
    norm = normalize(parsed.name)
    cached = get_cached(session, user_id, norm)
    if cached is not None:
        return ShelfLifeDecision(days=cached.days, source="cache",
                                 cache_was_hit=True)
    if parsed.confidence >= CONFIDENCE_FOR_CACHE_WRITE:
        put_cached(session, user_id, norm,
                   days=parsed.est_shelf_life_days,
                   category=parsed.category,
                   confidence=parsed.confidence,
                   source="llm")
    return ShelfLifeDecision(days=parsed.est_shelf_life_days,
                             source="llm", cache_was_hit=False)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_compute_shelf_life.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/ingest_service.py tests/test_compute_shelf_life.py
git commit -m "feat(ingest): compute_shelf_life() with confidence-tier cache policy

Per spec §6.2 + §6.3: cache hit wins always; cache write only at
confidence >= 0.6; user_correction has already been baked into the
cache lookup priority.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4.2 — `ingest_photo()` full flow

**Files:**
- Modify: `app/ingest_service.py` — add `ingest_photo()` and `IngestSummary`
- Create: `tests/test_ingest_photo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ingest_photo.py
from datetime import date, datetime, timezone
import pytest
from sqlmodel import SQLModel, Session, create_engine, select
from app.models import User, PantryItem, Receipt
from app.llm import ParseResult, ParsedItem, LLMResult
from app.ingest_service import ingest_photo, DuplicateReceipt
from tests.fakes import FakeLLMClient


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1,
                   created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def _result(items, *, purchase_date=date(2026, 5, 26),
            purchase_date_confidence=0.9, cost=18000):
    return LLMResult(parse=ParseResult(
        purchase_date=purchase_date,
        purchase_date_confidence=purchase_date_confidence,
        items=items,
    ), cost_micros_usd=cost)


def _food(name="Whole Milk 1 gal", days=7, conf=0.95, is_food=True):
    return ParsedItem(is_food=is_food, name=name, qty=1.0, unit="gal",
                      category="dairy", est_shelf_life_days=days,
                      confidence=conf)


@pytest.mark.asyncio
async def test_happy_path_inserts_items_and_receipt(session):
    llm = FakeLLMClient(canned=_result([_food(), _food(name="Bananas", days=5)]))
    today = date(2026, 5, 26)
    summary = await ingest_photo(
        session, llm, user_id=1, photo_file_id="fid-abc",
        image_bytes=b"jpg", today=today,
    )
    assert summary.inserted_food_count == 2
    assert summary.receipt_id is not None
    assert summary.purchase_date == date(2026, 5, 26)
    assert summary.purchase_date_assumed is False
    items = session.exec(select(PantryItem)).all()
    assert {i.normalized_name for i in items} == {"whole milk", "bananas"}


@pytest.mark.asyncio
async def test_duplicate_receipt_blocked(session):
    llm = FakeLLMClient(canned=_result([_food()]))
    await ingest_photo(session, llm, user_id=1,
                       photo_file_id="fid-x", image_bytes=b"jpg",
                       today=date(2026, 5, 26))
    with pytest.raises(DuplicateReceipt):
        await ingest_photo(session, llm, user_id=1,
                           photo_file_id="fid-x", image_bytes=b"jpg",
                           today=date(2026, 5, 26))


@pytest.mark.asyncio
async def test_non_food_dropped(session):
    llm = FakeLLMClient(canned=_result([
        _food(),
        _food(name="Paper Towels", is_food=False),
    ]))
    summary = await ingest_photo(session, llm, user_id=1,
                                 photo_file_id="fid", image_bytes=b"jpg",
                                 today=date(2026, 5, 26))
    assert summary.inserted_food_count == 1
    assert summary.skipped_non_food_count == 1


@pytest.mark.asyncio
async def test_low_confidence_skipped(session):
    llm = FakeLLMClient(canned=_result([
        _food(),
        _food(name="Mystery Item", conf=0.2),
    ]))
    summary = await ingest_photo(session, llm, user_id=1,
                                 photo_file_id="fid", image_bytes=b"jpg",
                                 today=date(2026, 5, 26))
    assert summary.inserted_food_count == 1
    assert summary.skipped_low_confidence_count == 1
    assert "Mystery Item" in summary.skipped_low_confidence_names


@pytest.mark.asyncio
async def test_medium_confidence_inserted_but_marked(session):
    llm = FakeLLMClient(canned=_result([_food(conf=0.45)]))
    summary = await ingest_photo(session, llm, user_id=1,
                                 photo_file_id="fid", image_bytes=b"jpg",
                                 today=date(2026, 5, 26))
    assert summary.inserted_food_count == 1
    assert len(summary.low_confidence_inserted_ids) == 1


@pytest.mark.asyncio
async def test_missing_purchase_date_uses_scan_date(session):
    llm = FakeLLMClient(canned=_result(
        [_food()], purchase_date=None, purchase_date_confidence=0.0))
    summary = await ingest_photo(session, llm, user_id=1,
                                 photo_file_id="fid", image_bytes=b"jpg",
                                 today=date(2026, 5, 26))
    assert summary.purchase_date == date(2026, 5, 26)
    assert summary.purchase_date_assumed is True


@pytest.mark.asyncio
async def test_zero_food_items_no_receipt_created(session):
    llm = FakeLLMClient(canned=_result([_food(is_food=False)]))
    summary = await ingest_photo(session, llm, user_id=1,
                                 photo_file_id="fid", image_bytes=b"jpg",
                                 today=date(2026, 5, 26))
    assert summary.receipt_id is None
    assert summary.inserted_food_count == 0
    rec = session.exec(select(Receipt)).first()
    assert rec is None
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_ingest_photo.py -v`
Expected: ImportError on `ingest_photo`, `DuplicateReceipt`.

- [ ] **Step 3: Extend `app/ingest_service.py`**

```python
# add to app/ingest_service.py

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional
from sqlmodel import Session, select
from app.llm import LLMClient, ParsedItem
from app.models import PantryItem, Receipt


class DuplicateReceipt(Exception):
    """Raised when (user_id, photo_file_id) already has a Receipt row."""


@dataclass
class IngestSummary:
    receipt_id: Optional[int]
    inserted_food_count: int
    inserted_item_ids: list[int] = field(default_factory=list)
    inserted_item_names: list[str] = field(default_factory=list)
    inserted_item_expires_on: list[date] = field(default_factory=list)
    inserted_item_shelf_life_days: list[int] = field(default_factory=list)
    skipped_non_food_count: int = 0
    skipped_low_confidence_count: int = 0
    skipped_low_confidence_names: list[str] = field(default_factory=list)
    low_confidence_inserted_ids: list[int] = field(default_factory=list)
    purchase_date: Optional[date] = None
    purchase_date_assumed: bool = False
    cost_micros_usd: Optional[int] = None


CONFIDENCE_MIN_FOR_INSERT = 0.3
PURCHASE_DATE_MIN_CONFIDENCE = 0.7


async def ingest_photo(session: Session, llm: LLMClient, *,
                       user_id: int, photo_file_id: str,
                       image_bytes: bytes, today: date) -> IngestSummary:
    # Duplicate guard BEFORE any LLM cost.
    existing = session.exec(
        select(Receipt).where(
            Receipt.user_id == user_id,
            Receipt.photo_file_id == photo_file_id,
        )
    ).first()
    if existing is not None:
        raise DuplicateReceipt(f"Receipt already logged (id={existing.id})")

    llm_result = await llm.extract_items_from_image(image_bytes)
    parse = llm_result.parse

    # Resolve purchase date with fallback.
    if (parse.purchase_date is not None
            and parse.purchase_date_confidence >= PURCHASE_DATE_MIN_CONFIDENCE):
        purchase_date = parse.purchase_date
        purchase_date_source = "receipt"
        purchase_date_assumed = False
    else:
        purchase_date = today
        purchase_date_source = "scan_fallback"
        purchase_date_assumed = True

    summary = IngestSummary(
        receipt_id=None, inserted_food_count=0,
        purchase_date=purchase_date,
        purchase_date_assumed=purchase_date_assumed,
        cost_micros_usd=llm_result.cost_micros_usd,
    )

    # Partition items per spec §6.3.
    to_insert: list[tuple[ParsedItem, bool]] = []
    for item in parse.items:
        if not item.is_food:
            summary.skipped_non_food_count += 1
            continue
        if item.confidence < CONFIDENCE_MIN_FOR_INSERT:
            summary.skipped_low_confidence_count += 1
            summary.skipped_low_confidence_names.append(item.name)
            continue
        is_low_conf = item.confidence < CONFIDENCE_FOR_CACHE_WRITE
        to_insert.append((item, is_low_conf))

    if not to_insert:
        return summary

    try:
        # Atomic write: Receipt + PantryItems + cache rows.
        receipt = Receipt(
            user_id=user_id, photo_file_id=photo_file_id,
            purchase_date=purchase_date,
            purchase_date_source=purchase_date_source,
            scanned_at=datetime.now(timezone.utc),
            llm_cost_micros_usd=llm_result.cost_micros_usd,
        )
        session.add(receipt)
        session.flush()  # populate receipt.id without committing

        for item, is_low_conf in to_insert:
            decision = compute_shelf_life(session, user_id=user_id, parsed=item)
            # Per spec: medium confidence items are inserted but DO NOT
            # create a new LLM cache row; the cache write was suppressed
            # inside compute_shelf_life (confidence < 0.6).
            norm = normalize(item.name)
            pi = PantryItem(
                user_id=user_id,
                raw_name=item.name,
                normalized_name=norm,
                category=item.category,
                qty=item.qty,
                unit=item.unit,
                purchased_on=purchase_date,
                shelf_life_days=decision.days,
                shelf_life_source="cache" if decision.cache_was_hit else "llm",
                ingest_shelf_life_source="cache" if decision.cache_was_hit else "llm",
                expires_on=_add_days(purchase_date, decision.days),
                status="active",
                created_via="receipt",
                source_receipt_id=receipt.id,
                created_at=datetime.now(timezone.utc),
            )
            session.add(pi)
            session.flush()
            summary.inserted_item_ids.append(pi.id)
            summary.inserted_item_names.append(pi.raw_name)
            summary.inserted_item_expires_on.append(pi.expires_on)
            summary.inserted_item_shelf_life_days.append(pi.shelf_life_days)
            if is_low_conf:
                summary.low_confidence_inserted_ids.append(pi.id)
            summary.inserted_food_count += 1

        session.commit()
    except Exception:
        session.rollback()
        raise
    summary.receipt_id = receipt.id
    return summary


def _add_days(d: date, n: int) -> date:
    from datetime import timedelta
    return d + timedelta(days=n)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_ingest_photo.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add app/ingest_service.py tests/test_ingest_photo.py
git commit -m "feat(ingest): ingest_photo() with duplicate guard + confidence tiers

Per spec §6: duplicate-receipt check BEFORE any LLM cost; atomic
write of Receipt + PantryItem rows; purchase_date fallback to scan
date when LLM confidence < 0.7; non-food and confidence<0.3 dropped
with summary fields surfaced for the bot reply.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4.3 — `ingest_text()` for `/add`

**Files:**
- Modify: `app/ingest_service.py` — add `ingest_text()` + helpers
- Create: `tests/test_ingest_text.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ingest_text.py
from datetime import date, datetime, timezone
import pytest
from sqlmodel import SQLModel, Session, create_engine, select
from app.models import User, PantryItem
from app.ingest_service import ingest_text


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1,
                   created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def test_known_item_uses_default(session):
    summary = ingest_text(session, user_id=1,
                          text="whole milk", today=date(2026, 5, 26))
    assert summary.inserted_count == 1
    item = session.exec(select(PantryItem)).first()
    assert item.normalized_name == "whole milk"
    assert item.shelf_life_days == 7
    assert item.shelf_life_source == "manual_fallback"
    assert item.created_via == "manual"


def test_explicit_hint_overrides_default(session):
    summary = ingest_text(session, user_id=1,
                          text="whole milk 5d", today=date(2026, 5, 26))
    item = session.exec(select(PantryItem)).first()
    assert item.shelf_life_days == 5
    assert item.shelf_life_source == "user_correction"
    assert item.ingest_shelf_life_source == "manual_user_hint"
    from app.cache import get_cached
    assert get_cached(session, 1, "whole milk").days == 5


def test_unknown_item_with_no_hint_reports_failure(session):
    summary = ingest_text(session, user_id=1,
                          text="dragonfruit", today=date(2026, 5, 26))
    assert summary.inserted_count == 0
    assert len(summary.failed_parts) == 1


def test_multiple_items_separated_by_commas(session):
    summary = ingest_text(session, user_id=1,
                          text="whole milk 7d, bananas, dragonfruit",
                          today=date(2026, 5, 26))
    assert summary.inserted_count == 2
    assert summary.failed_parts == ["dragonfruit"]


def test_invalid_explicit_hint_reports_failure(session):
    summary = ingest_text(session, user_id=1,
                          text="whole milk 999d",
                          today=date(2026, 5, 26))
    assert summary.inserted_count == 0
    assert summary.failed_parts == ["whole milk 999d"]
    assert "1..730" in summary.failed_reasons[0]
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_ingest_text.py -v`
Expected: ImportError on `ingest_text`.

- [ ] **Step 3: Append to `app/ingest_service.py`**

```python
# add to app/ingest_service.py

import re
from app.cache import write_user_correction
from app.shelf_life_defaults import lookup_default


@dataclass
class TextIngestSummary:
    inserted_count: int = 0
    inserted_ids: list[int] = field(default_factory=list)
    inserted_names: list[str] = field(default_factory=list)
    failed_parts: list[str] = field(default_factory=list)
    failed_reasons: list[str] = field(default_factory=list)


_HINT_RE = re.compile(r"\s+(\d+)\s*d\s*$", flags=re.IGNORECASE)
_QTY_PREFIX_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*"
    r"(gal|gallon|gallons|oz|lb|lbs|g|kg|ml|l|ct|count|pk|pack|bunch|dozen)?\s+",
    flags=re.IGNORECASE,
)


def _parse_text_part(raw: str) -> tuple[str, Optional[int], float, Optional[str], Optional[str]]:
    """Return (item_text_without_hint, hint_days_or_None, qty, unit_or_None, error)."""
    s = raw.strip()
    hint = None
    m = _HINT_RE.search(s)
    if m:
        d = int(m.group(1))
        if 1 <= d <= 730:
            hint = d
            s = s[:m.start()].rstrip()
        else:
            return s[:m.start()].strip(), None, 1.0, None, "shelf life days must be 1..730"

    qty = 1.0
    unit: Optional[str] = None
    m2 = _QTY_PREFIX_RE.match(s)
    if m2:
        try:
            qty = float(m2.group(1))
        except ValueError:
            qty = 1.0
        unit = m2.group(2).lower() if m2.group(2) else None
        if unit == "dozen":
            qty = qty * 12
            unit = "ct"
        s = s[m2.end():]

    return s.strip(), hint, qty, unit, None


def ingest_text(session: Session, *, user_id: int, text: str,
                today: date) -> TextIngestSummary:
    summary = TextIngestSummary()
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        summary.failed_parts.append(text)
        summary.failed_reasons.append("empty")
        return summary

    for raw in parts:
        item_text, hint_days, qty, unit, parse_error = _parse_text_part(raw)
        if parse_error is not None:
            summary.failed_parts.append(raw)
            summary.failed_reasons.append(parse_error)
            continue
        if not item_text:
            summary.failed_parts.append(raw)
            summary.failed_reasons.append("empty after stripping hint/qty")
            continue
        norm = normalize(item_text)
        if hint_days is not None:
            days = hint_days
            sl_source = "user_correction"
            ingest_source = "manual_user_hint"
            write_user_correction(session, user_id, norm, days=days)
        else:
            cached = get_cached(session, user_id, norm)
            if cached is not None:
                days = cached.days
                sl_source = "cache"
                ingest_source = "cache"
            else:
                default = lookup_default(norm)
                if default is None:
                    summary.failed_parts.append(raw)
                    summary.failed_reasons.append(
                        "no cache, no default; add `7d` hint or use /correct after adding"
                    )
                    continue
                days = default.days
                sl_source = "manual_fallback"
                ingest_source = "manual_fallback"
        pi = PantryItem(
            user_id=user_id, raw_name=item_text, normalized_name=norm,
            category=(get_cached(session, user_id, norm).category
                      if get_cached(session, user_id, norm) is not None
                      else (lookup_default(norm).category
                            if lookup_default(norm) is not None else None)),
            qty=qty, unit=unit,
            purchased_on=today,
            shelf_life_days=days,
            shelf_life_source=sl_source,
            ingest_shelf_life_source=ingest_source,
            expires_on=_add_days(today, days),
            status="active",
            created_via="manual",
            source_receipt_id=None,
            created_at=datetime.now(timezone.utc),
        )
        session.add(pi)
        session.flush()
        summary.inserted_ids.append(pi.id)
        summary.inserted_names.append(pi.raw_name)
        summary.inserted_count += 1
    session.commit()
    return summary
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_ingest_text.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/ingest_service.py tests/test_ingest_text.py
git commit -m "feat(ingest): ingest_text() for /add with optional 7d hints

Per spec §7.4 /add semantics: comma-separated parts parsed
independently, trailing 'Nd' hint sets shelf life and writes a
user_correction cache row, otherwise cache-first then
shelf_life_defaults; unknown items reported with actionable reason.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 5 — Pantry service

Goal: all non-ingest mutations and queries. Encapsulates idempotency and ownership.

### Task 5.1 — `list_active()` with filters

**Files:**
- Create: `app/pantry_service.py`
- Create: `tests/test_pantry_list.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pantry_list.py
from datetime import date, datetime, timedelta, timezone
import pytest
from sqlmodel import SQLModel, Session, create_engine
from app.models import User, PantryItem
from app.pantry_service import (
    list_active, list_digest_due, ListFilter, ALLOWED_CATEGORIES,
)


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1,
                   created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def _item(session, name, days_from_today, *, today=date(2026, 5, 26),
          status="active", category="produce", snoozed_until=None):
    pi = PantryItem(
        user_id=1, raw_name=name, normalized_name=name.lower(),
        category=category, qty=1.0, unit=None,
        purchased_on=today, shelf_life_days=days_from_today,
        shelf_life_source="llm", ingest_shelf_life_source="llm",
        expires_on=today + timedelta(days=days_from_today),
        status=status, snoozed_until=snoozed_until,
        created_via="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(pi); session.commit(); session.refresh(pi); return pi


def test_default_returns_all_active_sorted_by_expiry(session):
    _item(session, "C", 5)
    _item(session, "A", 1)
    _item(session, "B", 3)
    _item(session, "Z (eaten)", 2, status="eaten")
    res = list_active(session, user_id=1,
                      f=ListFilter.default(),
                      today=date(2026, 5, 26))
    assert [r.raw_name for r in res] == ["A", "B", "C"]


def test_filter_by_category(session):
    _item(session, "milk", 3, category="dairy")
    _item(session, "apple", 5, category="produce")
    res = list_active(session, user_id=1,
                      f=ListFilter(category="dairy"),
                      today=date(2026, 5, 26))
    assert [r.raw_name for r in res] == ["milk"]


def test_filter_week(session):
    _item(session, "in", 5)
    _item(session, "out", 9)
    res = list_active(session, user_id=1,
                      f=ListFilter(window="week"),
                      today=date(2026, 5, 26))
    assert [r.raw_name for r in res] == ["in"]


def test_filter_expired(session):
    _item(session, "old", -2)
    _item(session, "fresh", 5)
    res = list_active(session, user_id=1,
                      f=ListFilter(window="expired"),
                      today=date(2026, 5, 26))
    assert [r.raw_name for r in res] == ["old"]


def test_snoozed_items_visible_in_list(session):
    today = date(2026, 5, 26)
    _item(session, "snoozed", 3, today=today, snoozed_until=today + timedelta(days=5))
    res = list_active(session, user_id=1,
                      f=ListFilter.default(), today=today)
    assert [r.raw_name for r in res] == ["snoozed"]


def test_allowed_categories_match_spec():
    assert "dairy" in ALLOWED_CATEGORIES
    assert "beverage" in ALLOWED_CATEGORIES
    assert "wine" not in ALLOWED_CATEGORIES


def test_digest_due_excludes_snoozed_but_includes_expired(session):
    today = date(2026, 5, 26)
    _item(session, "expired", -1, today=today)
    _item(session, "due", 3, today=today)
    _item(session, "future", 8, today=today)
    _item(session, "snoozed", 3, today=today,
          snoozed_until=today + timedelta(days=2))
    res = list_digest_due(session, user_id=1, today=today)
    assert [r.raw_name for r in res] == ["expired", "due"]
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_pantry_list.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `app/pantry_service.py` (list section)**

```python
# app/pantry_service.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, Optional
from sqlmodel import Session, select
from app.models import PantryItem


ALLOWED_CATEGORIES = frozenset({
    "dairy", "produce", "meat", "seafood", "bakery",
    "pantry", "frozen", "beverage", "other",
})

Window = Literal["all", "week", "expired"]


@dataclass(frozen=True)
class ListFilter:
    category: Optional[str] = None
    window: Window = "all"

    @classmethod
    def default(cls) -> "ListFilter":
        return cls()


def list_active(session: Session, *, user_id: int, f: ListFilter,
                today: date) -> list[PantryItem]:
    q = select(PantryItem).where(
        PantryItem.user_id == user_id,
        PantryItem.status == "active",
    )
    if f.category is not None:
        q = q.where(PantryItem.category == f.category)
    if f.window == "week":
        q = q.where(PantryItem.expires_on >= today,
                    PantryItem.expires_on <= today + timedelta(days=7))
    elif f.window == "expired":
        q = q.where(PantryItem.expires_on < today)
    q = q.order_by(PantryItem.expires_on.asc())
    return list(session.exec(q).all())


def list_digest_due(session: Session, *, user_id: int,
                    today: date) -> list[PantryItem]:
    """Items visible in the scheduled digest and [show all] follow-up."""
    q = (
        select(PantryItem)
        .where(PantryItem.user_id == user_id,
               PantryItem.status == "active")
        .where(
            (PantryItem.snoozed_until.is_(None))
            | (PantryItem.snoozed_until <= today)
        )
        .where(PantryItem.expires_on <= today + timedelta(days=7))
        .order_by(PantryItem.expires_on.asc())
    )
    return list(session.exec(q).all())
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_pantry_list.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add app/pantry_service.py tests/test_pantry_list.py
git commit -m "feat(pantry): list_active() with category/week/expired filters

Per spec §7.4 /list: defaults to active items sorted by expiry,
filters by category or window. Snoozed items remain visible in
explicit list queries even when reminders are suppressed.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5.2 — Mutations (`ate`, `toss`, `snooze`, `delete`) with idempotency

**Files:**
- Modify: `app/pantry_service.py` — append mutation helpers
- Create: `tests/test_pantry_mutations.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pantry_mutations.py
from datetime import date, datetime, timedelta, timezone
import pytest
from sqlmodel import SQLModel, Session, create_engine
from app.models import User, PantryItem
from app.pantry_service import (
    mark_eaten, mark_tossed, mark_removed, snooze_item,
    MutationResult, NotOwnerOrMissing,
)


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        for uid in (1, 2):
            s.add(User(telegram_id=uid, chat_id=uid,
                       created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def _item(session, *, user_id=1, status="active", snoozed_until=None):
    pi = PantryItem(
        user_id=user_id, raw_name="X", normalized_name="x",
        category="other", qty=1.0, unit=None,
        purchased_on=date(2026, 5, 26),
        shelf_life_days=3,
        shelf_life_source="llm", ingest_shelf_life_source="llm",
        expires_on=date(2026, 5, 29),
        status=status, snoozed_until=snoozed_until,
        created_via="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(pi); session.commit(); session.refresh(pi); return pi


def test_mark_eaten_active(session):
    pi = _item(session)
    res = mark_eaten(session, user_id=1, item_id=pi.id, today=date(2026,5,26))
    assert res.applied is True
    session.refresh(pi)
    assert pi.status == "eaten"


def test_mark_eaten_idempotent_on_already_eaten(session):
    pi = _item(session, status="eaten")
    res = mark_eaten(session, user_id=1, item_id=pi.id, today=date(2026,5,26))
    assert res.applied is False
    assert res.was_already is True


def test_snooze_clears_on_eaten(session):
    pi = _item(session, snoozed_until=date(2026, 6, 1))
    mark_eaten(session, user_id=1, item_id=pi.id, today=date(2026,5,26))
    session.refresh(pi)
    assert pi.snoozed_until is None


def test_snooze_default_2_days(session):
    pi = _item(session)
    res = snooze_item(session, user_id=1, item_id=pi.id,
                      today=date(2026, 5, 26))
    session.refresh(pi)
    assert pi.snoozed_until == date(2026, 5, 28)
    assert res.applied is True


def test_snooze_custom_days_within_range(session):
    pi = _item(session)
    snooze_item(session, user_id=1, item_id=pi.id,
                today=date(2026, 5, 26), days=10)
    session.refresh(pi)
    assert pi.snoozed_until == date(2026, 6, 5)


def test_snooze_out_of_range_rejected(session):
    pi = _item(session)
    with pytest.raises(ValueError):
        snooze_item(session, user_id=1, item_id=pi.id,
                    today=date(2026, 5, 26), days=0)
    with pytest.raises(ValueError):
        snooze_item(session, user_id=1, item_id=pi.id,
                    today=date(2026, 5, 26), days=31)


def test_snooze_on_non_active_rejected(session):
    pi = _item(session, status="eaten")
    res = snooze_item(session, user_id=1, item_id=pi.id,
                      today=date(2026, 5, 26))
    assert res.applied is False
    assert res.was_already is True


def test_delete_marks_removed(session):
    pi = _item(session)
    res = mark_removed(session, user_id=1, item_id=pi.id, today=date(2026,5,26))
    assert res.applied is True
    session.refresh(pi)
    assert pi.status == "removed"


def test_other_user_cannot_mutate(session):
    pi = _item(session, user_id=1)
    with pytest.raises(NotOwnerOrMissing):
        mark_eaten(session, user_id=2, item_id=pi.id, today=date(2026,5,26))
    with pytest.raises(NotOwnerOrMissing):
        snooze_item(session, user_id=2, item_id=pi.id, today=date(2026,5,26))


def test_missing_item_raises_not_owner_or_missing(session):
    with pytest.raises(NotOwnerOrMissing):
        mark_eaten(session, user_id=1, item_id=999, today=date(2026,5,26))
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_pantry_mutations.py -v`
Expected: ImportError.

- [ ] **Step 3: Append to `app/pantry_service.py`**

```python
# add to app/pantry_service.py
from dataclasses import dataclass


class NotOwnerOrMissing(Exception):
    """Raised when an item lookup is missing or does not belong to user_id."""


@dataclass(frozen=True)
class MutationResult:
    applied: bool
    was_already: bool


def _load_owned(session: Session, *, user_id: int,
                item_id: int) -> PantryItem:
    pi = session.get(PantryItem, item_id)
    if pi is None or pi.user_id != user_id:
        raise NotOwnerOrMissing(f"item {item_id}")
    return pi


def _set_terminal(session: Session, pi: PantryItem,
                  status: str) -> MutationResult:
    if pi.status != "active":
        return MutationResult(applied=False, was_already=True)
    pi.status = status
    pi.snoozed_until = None
    session.add(pi); session.commit()
    return MutationResult(applied=True, was_already=False)


def mark_eaten(session: Session, *, user_id: int, item_id: int,
               today: date) -> MutationResult:
    pi = _load_owned(session, user_id=user_id, item_id=item_id)
    return _set_terminal(session, pi, "eaten")


def mark_tossed(session: Session, *, user_id: int, item_id: int,
                today: date) -> MutationResult:
    pi = _load_owned(session, user_id=user_id, item_id=item_id)
    return _set_terminal(session, pi, "tossed")


def mark_removed(session: Session, *, user_id: int, item_id: int,
                 today: date) -> MutationResult:
    pi = _load_owned(session, user_id=user_id, item_id=item_id)
    if pi.status == "removed":
        return MutationResult(applied=False, was_already=True)
    pi.status = "removed"
    pi.snoozed_until = None
    session.add(pi); session.commit()
    return MutationResult(applied=True, was_already=False)


SNOOZE_DAYS_DEFAULT = 2
SNOOZE_DAYS_MIN = 1
SNOOZE_DAYS_MAX = 30


def snooze_item(session: Session, *, user_id: int, item_id: int,
                today: date, days: int = SNOOZE_DAYS_DEFAULT) -> MutationResult:
    if days < SNOOZE_DAYS_MIN or days > SNOOZE_DAYS_MAX:
        raise ValueError(f"days must be in [{SNOOZE_DAYS_MIN}, {SNOOZE_DAYS_MAX}]")
    pi = _load_owned(session, user_id=user_id, item_id=item_id)
    if pi.status != "active":
        return MutationResult(applied=False, was_already=True)
    pi.snoozed_until = today + timedelta(days=days)
    session.add(pi); session.commit()
    return MutationResult(applied=True, was_already=False)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_pantry_mutations.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add app/pantry_service.py tests/test_pantry_mutations.py
git commit -m "feat(pantry): mark_eaten/tossed/removed + snooze with idempotency + auth

Per spec §7.3: terminal status transitions clear snoozed_until;
repeat actions on already-terminal items are harmless no-ops;
snooze accepted only on active items; cross-user mutation rejected
via NotOwnerOrMissing. Snooze range 1..30, default 2.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5.3 — `/correct` (item + cache)

**Files:**
- Modify: `app/pantry_service.py` — append `correct_item()`
- Create: `tests/test_pantry_correct.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pantry_correct.py
from datetime import date, datetime, timezone
import pytest
from sqlmodel import SQLModel, Session, create_engine
from app.models import User, PantryItem
from app.cache import get_cached
from app.pantry_service import correct_item, NotOwnerOrMissing


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1,
                   created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def _item(session, status="active"):
    pi = PantryItem(
        user_id=1, raw_name="Whole Milk 1 gal", normalized_name="whole milk",
        category="dairy", qty=1.0, unit="gal",
        purchased_on=date(2026, 5, 26),
        shelf_life_days=7,
        shelf_life_source="llm", ingest_shelf_life_source="llm",
        expires_on=date(2026, 6, 2),
        status=status, created_via="receipt", source_receipt_id=None,
        created_at=datetime.now(timezone.utc),
    )
    session.add(pi); session.commit(); session.refresh(pi); return pi


def test_correct_updates_item_and_writes_cache(session):
    pi = _item(session)
    correct_item(session, user_id=1, item_id=pi.id, days=5,
                 today=date(2026, 5, 30))
    session.refresh(pi)
    assert pi.shelf_life_days == 5
    assert pi.shelf_life_source == "user_correction"
    assert pi.expires_on == date(2026, 5, 31)  # purchased_on + 5d
    cached = get_cached(session, 1, "whole milk")
    assert cached.days == 5 and cached.source == "user_correction"


def test_correct_rejected_on_removed_item(session):
    pi = _item(session, status="removed")
    with pytest.raises(ValueError):
        correct_item(session, user_id=1, item_id=pi.id, days=5,
                     today=date(2026, 5, 30))


def test_correct_allowed_on_eaten_and_tossed(session):
    for s in ("eaten", "tossed"):
        pi = _item(session, status=s)
        correct_item(session, user_id=1, item_id=pi.id, days=4,
                     today=date(2026, 5, 30))
        session.refresh(pi)
        assert pi.shelf_life_days == 4


def test_correct_rejects_out_of_range(session):
    pi = _item(session)
    for bad in (0, 731):
        with pytest.raises(ValueError):
            correct_item(session, user_id=1, item_id=pi.id, days=bad,
                         today=date(2026, 5, 30))


def test_correct_rejects_other_user(session):
    pi = _item(session)
    with pytest.raises(NotOwnerOrMissing):
        correct_item(session, user_id=2, item_id=pi.id, days=5,
                     today=date(2026, 5, 30))
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_pantry_correct.py -v`
Expected: ImportError on `correct_item`.

- [ ] **Step 3: Append to `app/pantry_service.py`**

```python
# add to app/pantry_service.py
from app.cache import write_user_correction


SHELF_LIFE_DAYS_MIN = 1
SHELF_LIFE_DAYS_MAX = 730


def correct_item(session: Session, *, user_id: int, item_id: int,
                 days: int, today: date) -> PantryItem:
    if days < SHELF_LIFE_DAYS_MIN or days > SHELF_LIFE_DAYS_MAX:
        raise ValueError(f"days must be in [{SHELF_LIFE_DAYS_MIN}, "
                         f"{SHELF_LIFE_DAYS_MAX}]")
    pi = _load_owned(session, user_id=user_id, item_id=item_id)
    if pi.status == "removed":
        raise ValueError("cannot correct a removed item")
    pi.shelf_life_days = days
    pi.shelf_life_source = "user_correction"
    pi.expires_on = pi.purchased_on + timedelta(days=days)
    session.add(pi)
    write_user_correction(session, user_id, pi.normalized_name,
                          days=days, category=pi.category)
    session.commit()
    session.refresh(pi)
    return pi
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_pantry_correct.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/pantry_service.py tests/test_pantry_correct.py
git commit -m "feat(pantry): correct_item() updates item + writes user-correction cache row

Per spec §7.4: /correct re-derives expires_on from purchased_on,
marks shelf_life_source as user_correction, and writes the cache
row so future ingests learn from it. Allowed on active/eaten/tossed,
rejected on removed (removed = wrong import, shouldn't teach cache).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5.4 — `/stats` computation

**Files:**
- Modify: `app/pantry_service.py` — append `compute_stats()`
- Create: `tests/test_pantry_stats.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pantry_stats.py
from datetime import date, datetime, timedelta, timezone
import pytest
from sqlmodel import SQLModel, Session, create_engine
from app.models import User, PantryItem, Receipt
from app.pantry_service import compute_stats


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1,
                   created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def _r(session, days_ago, cost):
    base = datetime(2026, 5, 26, tzinfo=timezone.utc)
    r = Receipt(user_id=1, photo_file_id=f"f-{days_ago}-{cost}",
                purchase_date=date(2026, 5, 26),
                purchase_date_source="receipt",
                scanned_at=base - timedelta(days=days_ago),
                llm_cost_micros_usd=cost)
    session.add(r); session.commit(); session.refresh(r); return r


def _pi(session, ingest_source, *, status="active", receipt_id=None,
        created_via="receipt"):
    pi = PantryItem(
        user_id=1, raw_name="X", normalized_name="x",
        category="other", qty=1.0, unit=None,
        purchased_on=date(2026, 5, 26),
        shelf_life_days=3,
        shelf_life_source="llm",
        ingest_shelf_life_source=ingest_source,
        expires_on=date(2026, 5, 29),
        status=status,
        created_via=created_via,
        source_receipt_id=receipt_id,
        created_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
    )
    session.add(pi); session.commit(); return pi


def test_stats_basic(session):
    r1 = _r(session, 1, 15000)
    r2 = _r(session, 2, None)
    _pi(session, "llm", receipt_id=r1.id)
    _pi(session, "cache", receipt_id=r1.id)
    _pi(session, "llm", status="removed", receipt_id=r2.id)
    _pi(session, "manual_fallback", created_via="manual")
    _pi(session, "llm", status="tossed", receipt_id=r1.id)
    _pi(session, "cache", status="eaten", receipt_id=r1.id)
    stats = compute_stats(session, user_id=1,
                          now=datetime(2026, 5, 26, tzinfo=timezone.utc))
    assert stats.receipt_count == 2
    assert stats.tracked_item_count == 5  # all but removed
    assert stats.removed_item_count == 1
    # cache-hit % computed over receipt-ingested non-removed items only:
    # 4 items qualify (excluding removed + manual); 2 cache, 2 llm → 50.0
    assert stats.cache_hit_percent == pytest.approx(50.0)
    assert stats.total_cost_micros_usd == 15000
    assert stats.avg_cost_micros_usd == 15000
    assert stats.unknown_cost_receipt_count == 1
    assert stats.waste_rate_percent == pytest.approx(50.0)  # 1 tossed / (1 eaten + 1 tossed)
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_pantry_stats.py -v`
Expected: ImportError.

- [ ] **Step 3: Append to `app/pantry_service.py`**

```python
# add to app/pantry_service.py
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Stats:
    receipt_count: int
    tracked_item_count: int
    removed_item_count: int
    cache_hit_percent: Optional[float]
    total_cost_micros_usd: int
    avg_cost_micros_usd: Optional[int]
    unknown_cost_receipt_count: int
    waste_rate_percent: Optional[float]


def compute_stats(session: Session, *, user_id: int,
                  now: datetime) -> Stats:
    since = now - timedelta(days=30)
    receipts = list(session.exec(
        select(Receipt).where(
            Receipt.user_id == user_id,
            Receipt.scanned_at >= since,
        )
    ).all())
    receipt_count = len(receipts)
    known_costs = [r.llm_cost_micros_usd for r in receipts
                   if r.llm_cost_micros_usd is not None]
    total_cost = sum(known_costs) if known_costs else 0
    avg_cost = (total_cost // len(known_costs)) if known_costs else None
    unknown_cost = sum(1 for r in receipts if r.llm_cost_micros_usd is None)

    items_30d = list(session.exec(
        select(PantryItem).where(
            PantryItem.user_id == user_id,
            PantryItem.created_at >= since,
        )
    ).all())
    tracked = [i for i in items_30d if i.status != "removed"]
    removed = [i for i in items_30d if i.status == "removed"]

    receipt_items_non_removed = [
        i for i in items_30d
        if i.created_via == "receipt" and i.status != "removed"
        and i.ingest_shelf_life_source in ("cache", "llm")
    ]
    if receipt_items_non_removed:
        hits = sum(1 for i in receipt_items_non_removed
                   if i.ingest_shelf_life_source == "cache")
        cache_hit_percent = hits * 100.0 / len(receipt_items_non_removed)
    else:
        cache_hit_percent = None

    eaten = sum(1 for i in items_30d if i.status == "eaten")
    tossed = sum(1 for i in items_30d if i.status == "tossed")
    waste_rate = (tossed * 100.0 / (eaten + tossed)) if (eaten + tossed) else None

    return Stats(
        receipt_count=receipt_count,
        tracked_item_count=len(tracked),
        removed_item_count=len(removed),
        cache_hit_percent=cache_hit_percent,
        total_cost_micros_usd=total_cost,
        avg_cost_micros_usd=avg_cost,
        unknown_cost_receipt_count=unknown_cost,
        waste_rate_percent=waste_rate,
    )
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_pantry_stats.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add app/pantry_service.py tests/test_pantry_stats.py
git commit -m "feat(pantry): compute_stats() for /stats per spec §7.4

30-day window. Cache-hit % computed over receipt-ingested non-removed
items only via ingest_shelf_life_source. Cost stats use known-cost
receipts only and surface unknown-cost count separately.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 6 — Renderer

Goal: pure text/keyboard formatters. No Telegram dependency, no DB access — they take typed inputs and return strings + keyboard markup descriptors.

### Task 6.1 — `render_ingest_reply()`

**Files:**
- Create: `app/renderer.py`
- Create: `tests/test_renderer_ingest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_renderer_ingest.py
from datetime import date
from app.ingest_service import IngestSummary
from app.renderer import render_ingest_reply


def _summary(**kw):
    base = dict(
        receipt_id=1, inserted_food_count=2,
        inserted_item_ids=[42, 43],
        inserted_item_names=["Whole Milk 1 gal", "Bananas"],
        inserted_item_expires_on=[date(2026, 5, 31), date(2026, 5, 29)],
        inserted_item_shelf_life_days=[7, 3],
        skipped_non_food_count=0,
        skipped_low_confidence_count=0,
        skipped_low_confidence_names=[],
        low_confidence_inserted_ids=[],
        purchase_date=date(2026, 5, 26),
        purchase_date_assumed=False,
        cost_micros_usd=18000,
    )
    base.update(kw)
    return IngestSummary(**base)


def test_basic_reply_lists_items_with_ids():
    text = render_ingest_reply(_summary(), today=date(2026, 5, 26))
    assert "Logged 2 items" in text
    assert "#42" in text and "Whole Milk 1 gal" in text
    assert "exp May 31" in text and "(7d)" in text
    assert "$0.018" in text


def test_reply_notes_assumed_purchase_date():
    text = render_ingest_reply(
        _summary(purchase_date=date(2026, 5, 26), purchase_date_assumed=True),
        today=date(2026, 5, 26),
    )
    assert "Purchase date assumed" in text


def test_reply_notes_low_confidence_insertions():
    text = render_ingest_reply(
        _summary(low_confidence_inserted_ids=[42]),
        today=date(2026, 5, 26),
    )
    assert "Low confidence" in text
    assert "#42" in text


def test_reply_notes_skipped_unclear():
    text = render_ingest_reply(
        _summary(skipped_low_confidence_count=2,
                 skipped_low_confidence_names=["MysteryA", "MysteryB"]),
        today=date(2026, 5, 26),
    )
    assert "skipped 2 unclear" in text.lower()
    assert "MysteryA" in text


def test_reply_unavailable_cost():
    text = render_ingest_reply(
        _summary(cost_micros_usd=None), today=date(2026, 5, 26))
    assert "Cost: unavailable" in text


def test_reply_zero_items():
    text = render_ingest_reply(
        IngestSummary(receipt_id=None, inserted_food_count=0,
                      purchase_date=date(2026, 5, 26),
                      purchase_date_assumed=False,
                      cost_micros_usd=2000),
        today=date(2026, 5, 26),
    )
    assert "no food" in text.lower()
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_renderer_ingest.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `app/renderer.py`**

```python
# app/renderer.py
from __future__ import annotations
from datetime import date
from app.ingest_service import IngestSummary


def _fmt_date(d: date) -> str:
    return d.strftime("%b %-d") if hasattr(d, "strftime") else str(d)


def _fmt_cost(micros: int | None) -> str:
    if micros is None:
        return "Cost: unavailable"
    return f"Cost: ${micros / 1_000_000:.3f}"


def render_ingest_reply(s: IngestSummary, *, today: date) -> str:
    lines: list[str] = []
    if s.inserted_food_count == 0:
        if s.skipped_low_confidence_count:
            lines.append(
                f"😕 No clear food items found "
                f"(skipped {s.skipped_low_confidence_count} unclear items)."
            )
        else:
            lines.append("😕 No food items found in this receipt.")
        lines.append(_fmt_cost(s.cost_micros_usd))
        return "\n".join(lines)

    lines.append(f"✅ Logged {s.inserted_food_count} items from this receipt:")
    for iid, name, exp, dl in zip(
        s.inserted_item_ids, s.inserted_item_names,
        s.inserted_item_expires_on, s.inserted_item_shelf_life_days,
    ):
        lines.append(f"   • #{iid} {name} — exp {_fmt_date(exp)} ({dl}d)")

    if s.purchase_date is not None and s.purchase_date != today:
        lines.append(f"Purchase date: {_fmt_date(s.purchase_date)}")
    if s.purchase_date_assumed:
        lines.append(f"Purchase date assumed: {_fmt_date(s.purchase_date)}")

    if s.low_confidence_inserted_ids:
        ids = ", ".join(f"#{i}" for i in s.low_confidence_inserted_ids[:5])
        more = "" if len(s.low_confidence_inserted_ids) <= 5 else " ..."
        lines.append(f"Low confidence: {ids}{more} — review with /correct or /delete")

    if s.skipped_low_confidence_count:
        names = ", ".join(s.skipped_low_confidence_names[:3])
        more = "" if len(s.skipped_low_confidence_names) <= 3 else ", ..."
        lines.append(
            f"(skipped {s.skipped_low_confidence_count} unclear items: {names}{more})"
        )

    lines.append(_fmt_cost(s.cost_micros_usd))
    return "\n".join(lines)
```

> NOTE: `strftime("%b %-d")` is POSIX-only. On Windows use `"%b %#d"`. Pick whichever your dev/prod OS supports and adjust the test if needed. The test asserts `"May 31"` which works for either format token; ensure the helper returns that exact text in the target environment.

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_renderer_ingest.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add app/renderer.py tests/test_renderer_ingest.py
git commit -m "feat(renderer): render_ingest_reply() per spec §6.4

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6.2 — `render_digest()` with buckets + 20-cap

**Files:**
- Modify: `app/renderer.py` — append digest renderer + keyboard builder
- Create: `tests/test_renderer_digest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_renderer_digest.py
from datetime import date, datetime, timedelta, timezone
from app.models import PantryItem
from app.renderer import render_digest, build_digest_keyboard, DigestRender


def _item(name, expires_on, item_id=None):
    return PantryItem(
        id=item_id, user_id=1, raw_name=name,
        normalized_name=name.lower(), category="other",
        qty=1.0, unit=None,
        purchased_on=date(2026, 5, 20),
        shelf_life_days=(expires_on - date(2026, 5, 20)).days,
        shelf_life_source="llm", ingest_shelf_life_source="llm",
        expires_on=expires_on,
        status="active", created_via="manual",
        created_at=datetime.now(timezone.utc),
    )


def test_render_digest_buckets():
    today = date(2026, 5, 27)
    items = [
        _item("Spinach", today - timedelta(days=1), item_id=41),
        _item("Whole Milk 1 gal", today, item_id=42),
        _item("Bananas", today, item_id=43),
        _item("Sliced Bread", today + timedelta(days=1), item_id=44),
        _item("Greek Yogurt", today + timedelta(days=5), item_id=45),
    ]
    r: DigestRender = render_digest(items, today=today)
    text = r.text
    assert "Expired (1)" in text
    assert "#41 Spinach" in text
    assert "Today (2)" in text
    assert "Tomorrow (1)" in text
    assert "This week (1)" in text


def test_render_digest_truncates_at_20():
    today = date(2026, 5, 27)
    items = [
        _item(f"Item {i}", today, item_id=100 + i) for i in range(25)
    ]
    r = render_digest(items, today=today)
    assert r.rendered_count == 20
    assert "5 more" in r.text
    # keyboard has 20 rows of action buttons + 1 [show all] row
    kb = build_digest_keyboard(r.rendered_item_ids,
                               has_more=r.has_more)
    assert len(kb) == 21


def test_keyboard_button_callback_data_shape():
    today = date(2026, 5, 27)
    items = [_item("X", today, item_id=42)]
    r = render_digest(items, today=today)
    kb = build_digest_keyboard(r.rendered_item_ids, has_more=False)
    row = kb[0]
    assert len(row) == 3
    cb = {b.callback_data for b in row}
    assert cb == {"act:ate:42", "act:toss:42", "act:snooze2:42"}
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_renderer_digest.py -v`
Expected: ImportError.

- [ ] **Step 3: Append to `app/renderer.py`**

```python
# add to app/renderer.py
from dataclasses import dataclass, field
from datetime import timedelta
from typing import List


@dataclass
class CallbackButton:
    text: str
    callback_data: str


@dataclass
class DigestRender:
    text: str
    rendered_item_ids: list[int] = field(default_factory=list)
    rendered_count: int = 0
    total_count: int = 0
    has_more: bool = False


DIGEST_CAP = 20


def render_digest(items: list, *, today: date) -> DigestRender:
    """Build digest text + the list of item IDs that have inline buttons.

    `items` must be ordered by expires_on ascending (caller's responsibility).
    Empty list ⇒ empty DigestRender (caller should skip sending).
    """
    total = len(items)
    if total == 0:
        return DigestRender(text="", rendered_count=0,
                            total_count=0, has_more=False)

    capped = items[:DIGEST_CAP]
    has_more = total > DIGEST_CAP

    buckets = {"expired": [], "today": [], "tomorrow": [], "this_week": []}
    for it in capped:
        if it.expires_on < today:
            buckets["expired"].append(it)
        elif it.expires_on == today:
            buckets["today"].append(it)
        elif it.expires_on == today + timedelta(days=1):
            buckets["tomorrow"].append(it)
        else:
            buckets["this_week"].append(it)

    weekday_short = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu",
                     4: "Fri", 5: "Sat", 6: "Sun"}

    def _line(it) -> str:
        delta = (it.expires_on - today).days
        if delta < 0:
            d_abs = -delta
            tag = f"({d_abs}d ago)" if d_abs > 1 else "(yesterday)"
            return f"   • #{it.id} {it.raw_name} {tag}"
        if delta == 0 or delta == 1:
            return f"   • #{it.id} {it.raw_name}"
        return f"   • #{it.id} {it.raw_name} — {weekday_short[it.expires_on.weekday()]}"

    lines = [f"🍅 Pantry digest — {weekday_short[today.weekday()]} "
             f"{_fmt_date(today)}", ""]
    for key, header, emoji in (
        ("expired", "Expired", "❗"),
        ("today", "Today", "🔥"),
        ("tomorrow", "Tomorrow", "📅"),
        ("this_week", "This week", "📆"),
    ):
        if buckets[key]:
            lines.append(f"{emoji} {header} ({len(buckets[key])})")
            for it in buckets[key]:
                lines.append(_line(it))
            lines.append("")

    if has_more:
        omitted = total - DIGEST_CAP
        lines.append(f"… and {omitted} more — tap [show all]")

    text = "\n".join(line for line in lines if line is not None).rstrip()
    return DigestRender(
        text=text,
        rendered_item_ids=[it.id for it in capped],
        rendered_count=len(capped), total_count=total, has_more=has_more,
    )


def build_digest_keyboard(item_ids: list[int], *,
                          has_more: bool) -> list[list[CallbackButton]]:
    rows: list[list[CallbackButton]] = []
    for iid in item_ids:
        rows.append([
            CallbackButton(text="✓ Ate", callback_data=f"act:ate:{iid}"),
            CallbackButton(text="🗑 Tossed", callback_data=f"act:toss:{iid}"),
            CallbackButton(text="⏰ Remind +2d", callback_data=f"act:snooze2:{iid}"),
        ])
    if has_more:
        rows.append([CallbackButton(text="show all", callback_data="show:all")])
    return rows
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_renderer_digest.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/renderer.py tests/test_renderer_digest.py
git commit -m "feat(renderer): render_digest() + build_digest_keyboard() per spec §7.3

Buckets into expired/today/tomorrow/this_week, caps at 20 items,
emits a [show all] button when truncated. Callback data shape is
act:{verb}:{item_id} for bot handlers to parse.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6.3 — `render_list()` and `render_stats()`

**Files:**
- Modify: `app/renderer.py` — append list & stats renderers
- Create: `tests/test_renderer_list_stats.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_renderer_list_stats.py
from datetime import date, datetime, timezone
from app.models import PantryItem
from app.pantry_service import Stats
from app.renderer import render_list, render_stats


def _pi(name, exp, iid):
    return PantryItem(
        id=iid, user_id=1, raw_name=name, normalized_name=name.lower(),
        category="dairy", qty=1.0, unit=None,
        purchased_on=date(2026, 5, 26),
        shelf_life_days=(exp - date(2026, 5, 26)).days,
        shelf_life_source="llm", ingest_shelf_life_source="llm",
        expires_on=exp, status="active", created_via="manual",
        created_at=datetime.now(timezone.utc),
    )


def test_render_list_basic():
    text = render_list([_pi("Milk", date(2026, 5, 30), 1)],
                       today=date(2026, 5, 26))
    assert "#1 Milk" in text
    assert "May 30" in text


def test_render_list_empty():
    assert "no items" in render_list([], today=date(2026, 5, 26)).lower()


def test_render_stats_full():
    s = Stats(
        receipt_count=5, tracked_item_count=42, removed_item_count=2,
        cache_hit_percent=72.5, total_cost_micros_usd=92000,
        avg_cost_micros_usd=18400, unknown_cost_receipt_count=0,
        waste_rate_percent=18.2,
    )
    text = render_stats(s)
    assert "Receipts: 5" in text
    assert "Tracked items: 42" in text
    assert "72.5%" in text
    assert "$0.092" in text


def test_render_stats_no_data():
    s = Stats(receipt_count=0, tracked_item_count=0, removed_item_count=0,
              cache_hit_percent=None, total_cost_micros_usd=0,
              avg_cost_micros_usd=None, unknown_cost_receipt_count=0,
              waste_rate_percent=None)
    text = render_stats(s)
    assert "—" in text  # placeholder for None values
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_renderer_list_stats.py -v`
Expected: ImportError.

- [ ] **Step 3: Append to `app/renderer.py`**

```python
# add to app/renderer.py
from app.pantry_service import Stats


def render_list(items: list, *, today: date) -> str:
    if not items:
        return "no items match this filter"
    lines = []
    for it in items:
        delta = (it.expires_on - today).days
        if delta < 0:
            tag = f"expired {-delta}d ago"
        elif delta == 0:
            tag = "expires today"
        elif delta == 1:
            tag = "expires tomorrow"
        else:
            tag = f"expires {_fmt_date(it.expires_on)} ({delta}d)"
        lines.append(f"#{it.id} {it.raw_name} — {tag}")
    return "\n".join(lines)


def _fmt_cost_short(micros: int | None) -> str:
    if micros is None:
        return "—"
    return f"${micros / 1_000_000:.3f}"


def render_stats(s: Stats) -> str:
    chp = "—" if s.cache_hit_percent is None else f"{s.cache_hit_percent:.1f}%"
    wp = "—" if s.waste_rate_percent is None else f"{s.waste_rate_percent:.1f}%"
    avg = "—" if s.avg_cost_micros_usd is None else f"${s.avg_cost_micros_usd / 1_000_000:.3f}"
    return "\n".join([
        "📊 Last 30 days",
        f"Receipts: {s.receipt_count} (unknown-cost: {s.unknown_cost_receipt_count})",
        f"Tracked items: {s.tracked_item_count}",
        f"Removed (wrong import): {s.removed_item_count}",
        f"Cache hit rate: {chp}",
        f"LLM spend: total {_fmt_cost_short(s.total_cost_micros_usd) if s.total_cost_micros_usd else '$0.000'}  avg {avg} / receipt",
        f"Waste rate: {wp}",
    ])
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_renderer_list_stats.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/renderer.py tests/test_renderer_list_stats.py
git commit -m "feat(renderer): render_list + render_stats

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 7 — Bot wiring

Goal: aiogram routers that translate Telegram updates into service calls. Authorization + private-chat-only enforced at the router level so no handler has to remember.

### Task 7.1 — Authorization guard + user auto-create

**Files:**
- Create: `app/bot.py`
- Create: `tests/test_bot_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bot_auth.py
from datetime import datetime, timezone
import pytest
from sqlmodel import SQLModel, Session, create_engine
from app.models import User
from app.bot import authorize_and_get_user, AuthDecision


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def test_unauthorized_user_rejected(session):
    decision = authorize_and_get_user(
        session, allowed_user_id=1, telegram_user_id=99,
        chat_id=99, chat_type="private",
    )
    assert decision.allowed is False
    assert decision.user is None
    assert "not authorized" in decision.reason.lower()


def test_authorized_in_private_chat_auto_creates(session):
    decision = authorize_and_get_user(
        session, allowed_user_id=1, telegram_user_id=1,
        chat_id=1, chat_type="private",
    )
    assert decision.allowed is True
    assert decision.user is not None
    assert decision.user.tz == "America/Detroit"
    assert decision.user.digest_hour == 8


def test_authorized_in_group_rejected(session):
    decision = authorize_and_get_user(
        session, allowed_user_id=1, telegram_user_id=1,
        chat_id=-100, chat_type="group",
    )
    assert decision.allowed is False
    assert "private" in decision.reason.lower()


def test_existing_user_returned_unchanged(session):
    session.add(User(telegram_id=1, chat_id=1, tz="UTC",
                     digest_hour=20, created_at=datetime.now(timezone.utc)))
    session.commit()
    decision = authorize_and_get_user(
        session, allowed_user_id=1, telegram_user_id=1,
        chat_id=1, chat_type="private",
    )
    assert decision.user.tz == "UTC"
    assert decision.user.digest_hour == 20
    assert decision.created is False
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_bot_auth.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `app/bot.py` (auth section only)**

```python
# app/bot.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from sqlmodel import Session
from app.models import User


DEFAULT_TZ = "America/Detroit"
DEFAULT_DIGEST_HOUR = 8


@dataclass
class AuthDecision:
    allowed: bool
    user: Optional[User]
    created: bool
    reason: str


def authorize_and_get_user(session: Session, *,
                           allowed_user_id: int,
                           telegram_user_id: int,
                           chat_id: int,
                           chat_type: str) -> AuthDecision:
    if telegram_user_id != allowed_user_id:
        return AuthDecision(allowed=False, user=None, created=False,
                            reason="not authorized")
    if chat_type != "private":
        return AuthDecision(allowed=False, user=None, created=False,
                            reason="this bot only works in private chat")

    existing = session.get(User, telegram_user_id)
    if existing is not None:
        return AuthDecision(allowed=True, user=existing, created=False,
                            reason="ok")
    user = User(
        telegram_id=telegram_user_id, chat_id=chat_id,
        tz=DEFAULT_TZ, digest_hour=DEFAULT_DIGEST_HOUR,
        created_at=datetime.now(timezone.utc),
    )
    session.add(user); session.commit(); session.refresh(user)
    return AuthDecision(allowed=True, user=user, created=True,
                        reason="created")
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_bot_auth.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/bot.py tests/test_bot_auth.py
git commit -m "feat(bot): authorize_and_get_user() guards + auto-creates User row

Per spec §7.4: rejects unauthorized telegram IDs, rejects group
chats, auto-creates the User row on first authorized private-chat
interaction with America/Detroit + hour=8 defaults.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7.2 — Command parsers (`/tz`, `/digest_at`, `/list`, `/snooze`, `/correct`, `/add`)

**Files:**
- Create: `app/commands.py`
- Create: `tests/test_commands.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_commands.py
import pytest
from app.commands import (
    parse_tz, parse_digest_at, parse_item_id_arg, parse_snooze_args,
    parse_correct_args, parse_list_filter, CommandError,
)
from app.pantry_service import ListFilter


def test_parse_tz_valid():
    assert parse_tz("America/Detroit") == "America/Detroit"


def test_parse_tz_rejects_alias():
    with pytest.raises(CommandError):
        parse_tz("EST")


def test_parse_digest_at_valid():
    assert parse_digest_at("0") == 0
    assert parse_digest_at("23") == 23


def test_parse_digest_at_rejects_out_of_range():
    for v in ("-1", "24", "8.5", "x"):
        with pytest.raises(CommandError):
            parse_digest_at(v)


def test_parse_item_id_accepts_hash_or_plain():
    assert parse_item_id_arg("42") == 42
    assert parse_item_id_arg("#42") == 42
    with pytest.raises(CommandError):
        parse_item_id_arg("xx")


def test_parse_snooze_args_default_days():
    assert parse_snooze_args(["42"]) == (42, 2)


def test_parse_snooze_args_custom():
    assert parse_snooze_args(["#42", "5"]) == (42, 5)


def test_parse_snooze_args_invalid():
    with pytest.raises(CommandError):
        parse_snooze_args([])
    with pytest.raises(CommandError):
        parse_snooze_args(["42", "5", "extra"])


def test_parse_correct_args():
    assert parse_correct_args(["#42", "5"]) == (42, 5)
    with pytest.raises(CommandError):
        parse_correct_args(["42", "0"])
    with pytest.raises(CommandError):
        parse_correct_args(["42", "731"])


def test_parse_list_filter_default():
    assert parse_list_filter([]) == ListFilter.default()


def test_parse_list_filter_category():
    assert parse_list_filter(["dairy"]) == ListFilter(category="dairy")


def test_parse_list_filter_window():
    assert parse_list_filter(["week"]) == ListFilter(window="week")
    assert parse_list_filter(["expired"]) == ListFilter(window="expired")


def test_parse_list_filter_unknown_token():
    with pytest.raises(CommandError):
        parse_list_filter(["unknownthing"])
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_commands.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `app/commands.py`**

```python
# app/commands.py
from __future__ import annotations
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from app.pantry_service import (
    ALLOWED_CATEGORIES, ListFilter, SHELF_LIFE_DAYS_MIN, SHELF_LIFE_DAYS_MAX,
    SNOOZE_DAYS_MIN, SNOOZE_DAYS_MAX, SNOOZE_DAYS_DEFAULT,
)


class CommandError(Exception):
    pass


def parse_tz(arg: str) -> str:
    try:
        ZoneInfo(arg)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise CommandError(
            f"unknown IANA timezone {arg!r}. Examples: "
            f"America/Detroit, America/New_York"
        )
    return arg


def parse_digest_at(arg: str) -> int:
    try:
        h = int(arg)
    except ValueError:
        raise CommandError("digest_at expects an integer hour 0..23")
    if h < 0 or h > 23:
        raise CommandError("digest_at expects an integer hour 0..23")
    return h


def parse_item_id_arg(arg: str) -> int:
    s = arg.lstrip("#")
    try:
        return int(s)
    except ValueError:
        raise CommandError(f"expected item id like 42 or #42, got {arg!r}")


def parse_snooze_args(args: list[str]) -> tuple[int, int]:
    if not args or len(args) > 2:
        raise CommandError("usage: /snooze <item_id> [days]")
    item_id = parse_item_id_arg(args[0])
    if len(args) == 1:
        return item_id, SNOOZE_DAYS_DEFAULT
    try:
        days = int(args[1])
    except ValueError:
        raise CommandError("days must be an integer")
    if days < SNOOZE_DAYS_MIN or days > SNOOZE_DAYS_MAX:
        raise CommandError(
            f"days must be in [{SNOOZE_DAYS_MIN}, {SNOOZE_DAYS_MAX}]"
        )
    return item_id, days


def parse_correct_args(args: list[str]) -> tuple[int, int]:
    if len(args) != 2:
        raise CommandError("usage: /correct <item_id> <shelf_life_days>")
    item_id = parse_item_id_arg(args[0])
    try:
        days = int(args[1])
    except ValueError:
        raise CommandError("shelf_life_days must be an integer")
    if days < SHELF_LIFE_DAYS_MIN or days > SHELF_LIFE_DAYS_MAX:
        raise CommandError(
            f"shelf_life_days must be in [{SHELF_LIFE_DAYS_MIN}, "
            f"{SHELF_LIFE_DAYS_MAX}]"
        )
    return item_id, days


def parse_list_filter(args: list[str]) -> ListFilter:
    if not args:
        return ListFilter.default()
    if len(args) > 1:
        raise CommandError("usage: /list [category|week|expired]")
    tok = args[0].lower()
    if tok in {"week", "expired"}:
        return ListFilter(window=tok)
    if tok in ALLOWED_CATEGORIES:
        return ListFilter(category=tok)
    raise CommandError(
        f"unknown /list filter {tok!r}. Try a category "
        f"({', '.join(sorted(ALLOWED_CATEGORIES))}) or 'week' / 'expired'."
    )
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_commands.py -v`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add app/commands.py tests/test_commands.py
git commit -m "feat(bot): pure command-arg parsers with validation

All slash-command argument validation lives in pure functions so
the aiogram handlers stay thin and these parse rules are easy to
unit-test.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7.3 — Callback parser + verb dispatcher

**Files:**
- Modify: `app/commands.py` — append callback parsing
- Create: `tests/test_callback_parser.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_callback_parser.py
import pytest
from app.commands import parse_callback, CallbackAction, CommandError


def test_parse_ate():
    a = parse_callback("act:ate:42")
    assert a == CallbackAction(verb="ate", item_id=42)


def test_parse_toss():
    a = parse_callback("act:toss:5")
    assert a == CallbackAction(verb="toss", item_id=5)


def test_parse_snooze2():
    a = parse_callback("act:snooze2:9")
    assert a == CallbackAction(verb="snooze2", item_id=9)


def test_parse_show_all_is_distinct():
    a = parse_callback("show:all")
    assert a == CallbackAction(verb="show_all", item_id=None)


def test_unknown_verb_raises():
    with pytest.raises(CommandError):
        parse_callback("act:nope:1")
    with pytest.raises(CommandError):
        parse_callback("garbage")
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_callback_parser.py -v`
Expected: ImportError.

- [ ] **Step 3: Append to `app/commands.py`**

```python
# add to app/commands.py
from dataclasses import dataclass
from typing import Literal, Optional

Verb = Literal["ate", "toss", "snooze2", "show_all"]


@dataclass(frozen=True)
class CallbackAction:
    verb: Verb
    item_id: Optional[int]


def parse_callback(data: str) -> CallbackAction:
    if data == "show:all":
        return CallbackAction(verb="show_all", item_id=None)
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "act":
        raise CommandError(f"unrecognized callback data {data!r}")
    verb = parts[1]
    if verb not in ("ate", "toss", "snooze2"):
        raise CommandError(f"unknown verb {verb!r}")
    try:
        item_id = int(parts[2])
    except ValueError:
        raise CommandError(f"bad item id {parts[2]!r}")
    return CallbackAction(verb=verb, item_id=item_id)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_callback_parser.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/commands.py tests/test_callback_parser.py
git commit -m "feat(bot): typed callback data parser for inline buttons

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7.4 — Wire aiogram handlers

**Files:**
- Modify: `app/bot.py` — append router with handlers + `build_dispatcher()`
- Create: `tests/test_bot_handlers_smoke.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bot_handlers_smoke.py
"""Smoke tests for handler wiring. We don't run aiogram's full update
pipeline; instead we instantiate handler callables and call them with
faked Message/CallbackQuery objects to confirm orchestration works."""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlmodel import SQLModel, Session, create_engine
from app.models import User, PantryItem
from app.bot import (
    handle_start, handle_list, handle_ate, handle_help,
    handle_callback, _SessionFactory,
)


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1,
                   created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def _msg(text: str, *, user_id=1, chat_id=1, chat_type="private"):
    m = MagicMock()
    m.from_user = MagicMock(id=user_id)
    m.chat = MagicMock(id=chat_id, type=chat_type)
    m.text = text
    m.answer = AsyncMock()
    return m


def _cb(data: str, *, user_id=1):
    c = MagicMock()
    c.from_user = MagicMock(id=user_id)
    c.data = data
    c.answer = AsyncMock()
    c.message = MagicMock()
    c.message.answer = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_start_replies_with_setup_text(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    factory: _SessionFactory = lambda: session
    msg = _msg("/start")
    await handle_start(msg, session_factory=factory, on_user_created=lambda u: None)
    msg.answer.assert_awaited()
    text = msg.answer.await_args.args[0]
    assert "America/Detroit" in text
    assert "/tz" in text


@pytest.mark.asyncio
async def test_list_empty(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    factory: _SessionFactory = lambda: session
    msg = _msg("/list")
    await handle_list(msg, session_factory=factory,
                      now_provider=lambda tz: datetime(2026, 5, 26, tzinfo=timezone.utc))
    msg.answer.assert_awaited()
    assert "no items" in msg.answer.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_list_auto_create_schedules_digest(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    session.delete(session.get(User, 1)); session.commit()
    created = []
    factory: _SessionFactory = lambda: session
    msg = _msg("/list")
    await handle_list(msg, session_factory=factory,
                      now_provider=lambda tz: datetime(2026, 5, 26, tzinfo=timezone.utc),
                      on_user_created=created.append)
    assert created and created[0].tz == "America/Detroit"


@pytest.mark.asyncio
async def test_ate_command_marks_item(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    pi = PantryItem(
        user_id=1, raw_name="X", normalized_name="x",
        category="other", qty=1.0, unit=None,
        purchased_on=date(2026,5,26), shelf_life_days=2,
        shelf_life_source="llm", ingest_shelf_life_source="llm",
        expires_on=date(2026,5,28), status="active",
        created_via="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(pi); session.commit(); session.refresh(pi)
    factory: _SessionFactory = lambda: session

    msg = _msg(f"/ate {pi.id}")
    await handle_ate(msg, session_factory=factory,
                     now_provider=lambda tz: datetime(2026,5,26, tzinfo=timezone.utc))
    session.refresh(pi)
    assert pi.status == "eaten"


@pytest.mark.asyncio
async def test_help_lists_commands(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    factory: _SessionFactory = lambda: session
    msg = _msg("/help")
    await handle_help(msg, session_factory=factory)
    text = msg.answer.await_args.args[0]
    for cmd in ("/list", "/add", "/correct", "/delete", "/stats", "/snooze"):
        assert cmd in text


@pytest.mark.asyncio
async def test_unauthorized_user_rejected(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    factory: _SessionFactory = lambda: session
    msg = _msg("/list", user_id=99)
    await handle_list(msg, session_factory=factory,
                      now_provider=lambda tz: datetime(2026,5,26, tzinfo=timezone.utc))
    text = msg.answer.await_args.args[0].lower()
    assert "not authorized" in text or "private" in text


@pytest.mark.asyncio
async def test_show_all_callback_sends_due_items_followup(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    for i in range(25):
        session.add(PantryItem(
            user_id=1, raw_name=f"Item {i}", normalized_name=f"item {i}",
            category="other", qty=1.0, unit=None,
            purchased_on=date(2026,5,26), shelf_life_days=2,
            shelf_life_source="llm", ingest_shelf_life_source="llm",
            expires_on=date(2026,5,28), status="active",
            created_via="manual",
            created_at=datetime.now(timezone.utc),
        ))
    session.commit()
    factory: _SessionFactory = lambda: session
    cb = _cb("show:all")
    await handle_callback(cb, session_factory=factory,
                          now_provider=lambda tz: datetime(2026,5,26, tzinfo=timezone.utc))
    cb.message.answer.assert_awaited_once()
    assert "Item 24" in cb.message.answer.await_args.args[0]
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_bot_handlers_smoke.py -v`
Expected: ImportError on `handle_start`, etc.

- [ ] **Step 3: Append handlers + `build_dispatcher` to `app/bot.py`**

```python
# add to app/bot.py
from __future__ import annotations
from datetime import datetime, timezone
import logging
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo
from sqlmodel import Session
from app.commands import (
    CommandError, parse_callback, parse_correct_args, parse_digest_at,
    parse_item_id_arg, parse_list_filter, parse_snooze_args, parse_tz,
)
from app.ingest_service import (
    DuplicateReceipt, ingest_photo, ingest_text,
)
from app.llm import LLMClient
from app.models import User, PantryItem
from app.pantry_service import (
    compute_stats, correct_item, list_active, list_digest_due, mark_eaten,
    mark_removed, mark_tossed, snooze_item, NotOwnerOrMissing,
)
from app.renderer import (
    build_digest_keyboard, render_ingest_reply, render_list, render_stats,
)


_SessionFactory = Callable[[], Session]
NowProvider = Callable[[str], datetime]   # str = tz name


# Patched at runtime from settings; tests override via monkeypatch.
ALLOWED_TELEGRAM_USER_ID: int = 0
log = logging.getLogger(__name__)


def _now_in(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def _today_in(tz: str):
    return _now_in(tz).date()


def _noop_user_created(user: User) -> None:
    pass


async def _guard(msg, session: Session, *,
                 on_user_created: Callable[[User], None] = _noop_user_created) -> User | None:
    decision = authorize_and_get_user(
        session,
        allowed_user_id=ALLOWED_TELEGRAM_USER_ID,
        telegram_user_id=msg.from_user.id,
        chat_id=msg.chat.id,
        chat_type=msg.chat.type,
    )
    if not decision.allowed:
        log.info("unauthorized_update_rejected",
                 extra={"telegram_user_id": msg.from_user.id,
                        "chat_id": msg.chat.id})
        await msg.answer(decision.reason)
        return None
    if decision.created:
        on_user_created(decision.user)
    return decision.user


# ─── Commands ───────────────────────────────────────────────────────────

async def handle_start(msg, *, session_factory: _SessionFactory,
                       on_user_created: Callable[[User], None]) -> None:
    with session_factory() as session:
        decision = authorize_and_get_user(
            session,
            allowed_user_id=ALLOWED_TELEGRAM_USER_ID,
            telegram_user_id=msg.from_user.id,
            chat_id=msg.chat.id,
            chat_type=msg.chat.type,
        )
        if not decision.allowed:
            log.info("unauthorized_update_rejected",
                     extra={"telegram_user_id": msg.from_user.id,
                            "chat_id": msg.chat.id})
            await msg.answer(decision.reason); return
        if decision.created:
            on_user_created(decision.user)
        await msg.answer(
            f"👋 Pantry bot ready.\n"
            f"Timezone: {decision.user.tz} (change with /tz <IANA>)\n"
            f"Daily digest hour: {decision.user.digest_hour}:00 "
            f"(change with /digest_at <0..23>)\n"
            f"Type /help to see all commands."
        )


async def handle_tz(msg, *, session_factory: _SessionFactory,
                    reschedule: Callable[[User], None]) -> None:
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=reschedule)
        if user is None: return
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await msg.answer("usage: /tz <IANA timezone>"); return
        try:
            tz = parse_tz(parts[1].strip())
        except CommandError as e:
            await msg.answer(str(e)); return
        user.tz = tz; session.add(user); session.commit()
        reschedule(user)
        await msg.answer(f"timezone set to {tz}")


async def handle_digest_at(msg, *, session_factory: _SessionFactory,
                           reschedule: Callable[[User], None]) -> None:
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=reschedule)
        if user is None: return
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await msg.answer("usage: /digest_at <hour 0..23>"); return
        try:
            h = parse_digest_at(parts[1].strip())
        except CommandError as e:
            await msg.answer(str(e)); return
        user.digest_hour = h; session.add(user); session.commit()
        reschedule(user)
        await msg.answer(f"digest hour set to {h}:00 in {user.tz}")


async def handle_list(msg, *, session_factory: _SessionFactory,
                      now_provider: NowProvider,
                      on_user_created: Callable[[User], None] = _noop_user_created) -> None:
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None: return
        parts = (msg.text or "").split()
        try:
            f = parse_list_filter(parts[1:])
        except CommandError as e:
            await msg.answer(str(e)); return
        today = now_provider(user.tz).date()
        items = list_active(session, user_id=user.telegram_id, f=f, today=today)
        await msg.answer(render_list(items, today=today))


async def handle_add(msg, *, session_factory: _SessionFactory,
                     now_provider: NowProvider,
                     on_user_created: Callable[[User], None] = _noop_user_created) -> None:
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None: return
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await msg.answer("usage: /add <items, separated, by, commas>"); return
        today = now_provider(user.tz).date()
        summary = ingest_text(session, user_id=user.telegram_id,
                              text=parts[1].strip(), today=today)
        lines = []
        if summary.inserted_count:
            lines.append(f"✅ Added {summary.inserted_count} items:")
            for iid, name in zip(summary.inserted_ids, summary.inserted_names):
                lines.append(f"   • #{iid} {name}")
        if summary.failed_parts:
            lines.append("⚠️ Couldn't add:")
            for raw, why in zip(summary.failed_parts, summary.failed_reasons):
                lines.append(f"   • {raw!r}: {why}")
        await msg.answer("\n".join(lines) or "nothing parsed")


async def _terminal_cmd(msg, session_factory, now_provider, *, fn, action_word,
                        on_user_created: Callable[[User], None] = _noop_user_created):
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None: return
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await msg.answer(f"usage: /{action_word} <item_id>"); return
        try:
            iid = parse_item_id_arg(parts[1].strip())
        except CommandError as e:
            await msg.answer(str(e)); return
        try:
            today = now_provider(user.tz).date()
            res = fn(session, user_id=user.telegram_id,
                     item_id=iid, today=today)
        except NotOwnerOrMissing:
            await msg.answer(f"no item #{iid}"); return
        if res.applied:
            log.info("item_action_applied",
                     extra={"user_id": user.telegram_id,
                            "item_id": iid, "action": action_word})
            await msg.answer(f"#{iid} marked {action_word}")
        elif res.was_already:
            await msg.answer(f"#{iid} was already non-active")


async def handle_ate(msg, *, session_factory, now_provider,
                     on_user_created: Callable[[User], None] = _noop_user_created):
    await _terminal_cmd(msg, session_factory, now_provider,
                        fn=mark_eaten, action_word="ate",
                        on_user_created=on_user_created)


async def handle_toss(msg, *, session_factory, now_provider,
                      on_user_created: Callable[[User], None] = _noop_user_created):
    await _terminal_cmd(msg, session_factory, now_provider,
                        fn=mark_tossed, action_word="toss",
                        on_user_created=on_user_created)


async def handle_delete(msg, *, session_factory, now_provider,
                        on_user_created: Callable[[User], None] = _noop_user_created):
    await _terminal_cmd(msg, session_factory, now_provider,
                        fn=mark_removed, action_word="delete",
                        on_user_created=on_user_created)


async def handle_snooze(msg, *, session_factory, now_provider,
                        on_user_created: Callable[[User], None] = _noop_user_created):
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None: return
        parts = (msg.text or "").split()
        try:
            iid, days = parse_snooze_args(parts[1:])
        except CommandError as e:
            await msg.answer(str(e)); return
        try:
            today = now_provider(user.tz).date()
            res = snooze_item(session, user_id=user.telegram_id,
                              item_id=iid, today=today, days=days)
        except NotOwnerOrMissing:
            await msg.answer(f"no item #{iid}"); return
        except ValueError as e:
            await msg.answer(str(e)); return
        if res.applied:
            log.info("item_action_applied",
                     extra={"user_id": user.telegram_id,
                            "item_id": iid, "action": "snooze"})
            await msg.answer(f"#{iid} snoozed for {days}d")
        else:
            await msg.answer(f"#{iid} is not active")


async def handle_correct(msg, *, session_factory, now_provider,
                         on_user_created: Callable[[User], None] = _noop_user_created):
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None: return
        parts = (msg.text or "").split()
        try:
            iid, days = parse_correct_args(parts[1:])
        except CommandError as e:
            await msg.answer(str(e)); return
        try:
            today = now_provider(user.tz).date()
            pi = correct_item(session, user_id=user.telegram_id,
                              item_id=iid, days=days, today=today)
        except NotOwnerOrMissing:
            await msg.answer(f"no item #{iid}"); return
        except ValueError as e:
            await msg.answer(str(e)); return
        await msg.answer(
            f"#{iid} {pi.raw_name}: shelf life set to {days}d, "
            f"expires {pi.expires_on}. Future estimates for "
            f"\"{pi.normalized_name}\" will use this value."
        )


async def handle_stats(msg, *, session_factory, now_provider,
                       on_user_created: Callable[[User], None] = _noop_user_created):
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None: return
        now = now_provider(user.tz)
        stats = compute_stats(session, user_id=user.telegram_id,
                              now=now.astimezone(timezone.utc))
        await msg.answer(render_stats(stats))


HELP_TEXT = (
    "Commands:\n"
    "  /start — setup status\n"
    "  /tz <IANA> — set timezone\n"
    "  /digest_at <0..23> — set digest hour\n"
    "  /list [category|week|expired] — show pantry\n"
    "  /add <items, by, commas> — manual entry; trailing `7d` sets shelf life\n"
    "  /ate <id> — mark eaten\n"
    "  /toss <id> — mark tossed\n"
    "  /snooze <id> [days=2] — suppress reminders 1..30d\n"
    "  /correct <id> <days> — fix shelf life AND teach future estimates\n"
    "  /delete <id> — remove a wrong/duplicate import (does not teach cache)\n"
    "  /stats — last 30 days\n"
    "  /help — this message\n"
    "Send a receipt photo to log it."
)


async def handle_help(msg, *, session_factory,
                      on_user_created: Callable[[User], None] = _noop_user_created) -> None:
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None: return
    await msg.answer(HELP_TEXT)


async def handle_photo(msg, *, session_factory, now_provider,
                       llm: LLMClient,
                       photo_downloader: Callable[[str], Awaitable[bytes]],
                       on_user_created: Callable[[User], None] = _noop_user_created) -> None:
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None: return
        # aiogram: largest photo is last in .photo array
        if not msg.photo:
            await msg.answer("send a photo of a receipt"); return
        file_id = msg.photo[-1].file_id
        today = now_provider(user.tz).date()
        log.info("receipt_ingest_started",
                 extra={"user_id": user.telegram_id, "photo_file_id": file_id})
        try:
            image_bytes = await photo_downloader(file_id)
            summary = await ingest_photo(
                session, llm, user_id=user.telegram_id,
                photo_file_id=file_id, image_bytes=image_bytes, today=today,
            )
        except DuplicateReceipt:
            await msg.answer("this receipt was already logged"); return
        except Exception as e:
            log.warning("receipt_ingest_failed",
                        extra={"user_id": user.telegram_id,
                               "photo_file_id": file_id,
                               "error_class": type(e).__name__})
            await msg.answer(
                "couldn't read that one — try a clearer photo or "
                "/add <items> manually"
            ); return
        log.info("receipt_ingest_succeeded",
                 extra={"user_id": user.telegram_id,
                        "receipt_id": summary.receipt_id,
                        "inserted_food_count": summary.inserted_food_count})
        await msg.answer(render_ingest_reply(summary, today=today))


async def handle_callback(cb, *, session_factory, now_provider) -> None:
    if cb.from_user.id != ALLOWED_TELEGRAM_USER_ID:
        log.info("unauthorized_update_rejected",
                 extra={"telegram_user_id": cb.from_user.id})
        await cb.answer("not authorized", show_alert=False); return
    try:
        action = parse_callback(cb.data)
    except CommandError:
        await cb.answer("unrecognized action"); return
    with session_factory() as session:
        user = session.get(User, cb.from_user.id)
        if user is None:
            await cb.answer("not configured"); return
        today = now_provider(user.tz).date()
        if action.verb == "show_all":
            rows = list_digest_due(session, user_id=user.telegram_id, today=today)
            if not rows:
                await cb.answer("nothing due"); return
            await cb.message.answer(render_list(rows, today=today))
            await cb.answer("sent full digest list")
            return
        try:
            if action.verb == "ate":
                res = mark_eaten(session, user_id=cb.from_user.id,
                                 item_id=action.item_id, today=today)
            elif action.verb == "toss":
                res = mark_tossed(session, user_id=cb.from_user.id,
                                  item_id=action.item_id, today=today)
            elif action.verb == "snooze2":
                res = snooze_item(session, user_id=cb.from_user.id,
                                  item_id=action.item_id, today=today, days=2)
        except NotOwnerOrMissing:
            await cb.answer("item not found"); return
        if res.applied:
            log.info("item_action_applied",
                     extra={"user_id": cb.from_user.id,
                            "item_id": action.item_id,
                            "action": action.verb})
        msg_text = (f"#{action.item_id} → {action.verb}"
                    if res.applied else f"#{action.item_id} already updated")
        await cb.answer(msg_text)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_bot_handlers_smoke.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add app/bot.py tests/test_bot_handlers_smoke.py
git commit -m "feat(bot): aiogram handlers for all spec §7.4 commands + photo + callback

Thin shims over parsers (app.commands) and services. Each handler
guards via authorize_and_get_user before any mutation; callback
handler additionally short-circuits when from_user.id mismatches
ALLOWED_TELEGRAM_USER_ID.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7.5 — Register aiogram routes in `build_dispatcher()`

**Files:**
- Modify: `app/bot.py` — append `build_dispatcher()`

- [ ] **Step 1: Append wiring (no test — integration covered by manual run + Task 9)**

```python
# add to app/bot.py
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)
from app.renderer import CallbackButton


def to_aiogram_keyboard(rows: list[list[CallbackButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b.text, callback_data=b.callback_data) for b in row]
        for row in rows
    ])


def build_dispatcher(
    *, bot: Bot, session_factory: _SessionFactory, llm: LLMClient,
    now_provider: NowProvider, on_user_created: Callable[[User], None],
    reschedule: Callable[[User], None],
) -> Dispatcher:
    dp = Dispatcher()

    async def downloader(file_id: str) -> bytes:
        f = await bot.get_file(file_id)
        bio = await bot.download_file(f.file_path)
        return bio.read()

    dp.message.register(
        lambda m: handle_start(m, session_factory=session_factory,
                               on_user_created=on_user_created),
        Command("start"),
    )
    dp.message.register(
        lambda m: handle_tz(m, session_factory=session_factory,
                            reschedule=reschedule),
        Command("tz"),
    )
    dp.message.register(
        lambda m: handle_digest_at(m, session_factory=session_factory,
                                   reschedule=reschedule),
        Command("digest_at"),
    )
    dp.message.register(
        lambda m: handle_list(m, session_factory=session_factory,
                              now_provider=now_provider,
                              on_user_created=on_user_created),
        Command("list"),
    )
    dp.message.register(
        lambda m: handle_add(m, session_factory=session_factory,
                             now_provider=now_provider,
                             on_user_created=on_user_created),
        Command("add"),
    )
    dp.message.register(
        lambda m: handle_ate(m, session_factory=session_factory,
                             now_provider=now_provider,
                             on_user_created=on_user_created),
        Command("ate"),
    )
    dp.message.register(
        lambda m: handle_toss(m, session_factory=session_factory,
                              now_provider=now_provider,
                              on_user_created=on_user_created),
        Command("toss"),
    )
    dp.message.register(
        lambda m: handle_delete(m, session_factory=session_factory,
                                now_provider=now_provider,
                                on_user_created=on_user_created),
        Command("delete"),
    )
    dp.message.register(
        lambda m: handle_snooze(m, session_factory=session_factory,
                                now_provider=now_provider,
                                on_user_created=on_user_created),
        Command("snooze"),
    )
    dp.message.register(
        lambda m: handle_correct(m, session_factory=session_factory,
                                 now_provider=now_provider,
                                 on_user_created=on_user_created),
        Command("correct"),
    )
    dp.message.register(
        lambda m: handle_stats(m, session_factory=session_factory,
                               now_provider=now_provider,
                               on_user_created=on_user_created),
        Command("stats"),
    )
    dp.message.register(
        lambda m: handle_help(m, session_factory=session_factory,
                              on_user_created=on_user_created),
        Command("help"),
    )
    dp.message.register(
        lambda m: handle_photo(m, session_factory=session_factory,
                               now_provider=now_provider, llm=llm,
                               photo_downloader=downloader,
                               on_user_created=on_user_created),
        F.photo,
    )
    dp.callback_query.register(
        lambda c: handle_callback(c, session_factory=session_factory,
                                  now_provider=now_provider),
    )
    return dp
```

- [ ] **Step 2: Smoke-check imports**

Run: `uv run python -c "from app.bot import build_dispatcher; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add app/bot.py
git commit -m "feat(bot): build_dispatcher() registers all message + callback handlers

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 8 — Scheduler

Goal: in-process APScheduler that fires per-user daily digests at the right wall-clock hour in the user's timezone, with one-retry-after-60s on Telegram send failure, and rebuilds jobs on process startup.

### Task 8.1 — `build_digest_payload()` (pure)

**Files:**
- Create: `app/scheduler.py`
- Create: `tests/test_scheduler_payload.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scheduler_payload.py
from datetime import date, datetime, timedelta, timezone
import pytest
from sqlmodel import SQLModel, Session, create_engine
from app.models import User, PantryItem
from app.scheduler import build_digest_payload


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=999, tz="America/Detroit",
                   digest_hour=8, created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def _pi(session, days_from_today, *, today, status="active", snoozed_until=None):
    pi = PantryItem(
        user_id=1, raw_name=f"item_{days_from_today}",
        normalized_name=f"item_{days_from_today}",
        category="other", qty=1.0, unit=None,
        purchased_on=today,
        shelf_life_days=days_from_today,
        shelf_life_source="llm", ingest_shelf_life_source="llm",
        expires_on=today + timedelta(days=days_from_today),
        status=status, snoozed_until=snoozed_until,
        created_via="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(pi); session.commit(); return pi


def test_payload_includes_only_active_within_7d_window(session):
    today = date(2026, 5, 26)
    _pi(session, -1, today=today)   # expired
    _pi(session, 0, today=today)    # today
    _pi(session, 7, today=today)    # boundary in
    _pi(session, 8, today=today)    # out
    _pi(session, 3, today=today, status="eaten")  # excluded
    payload = build_digest_payload(session, user_id=1, today=today)
    assert payload is not None
    assert len(payload.items) == 3


def test_payload_excludes_snoozed_active(session):
    today = date(2026, 5, 26)
    _pi(session, 2, today=today)
    _pi(session, 2, today=today,
        snoozed_until=today + timedelta(days=1))   # excluded
    payload = build_digest_payload(session, user_id=1, today=today)
    assert payload is not None
    assert len(payload.items) == 1


def test_payload_includes_items_with_passed_snooze(session):
    today = date(2026, 5, 26)
    _pi(session, 2, today=today,
        snoozed_until=today - timedelta(days=1))   # snooze ended
    payload = build_digest_payload(session, user_id=1, today=today)
    assert payload is not None
    assert len(payload.items) == 1


def test_empty_payload_when_nothing_due(session):
    today = date(2026, 5, 26)
    _pi(session, 14, today=today)   # 14 days out
    payload = build_digest_payload(session, user_id=1, today=today)
    assert payload is None
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_scheduler_payload.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `app/scheduler.py` (payload builder)**

```python
# app/scheduler.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Optional
from sqlmodel import Session
from app.models import User, PantryItem
from app.pantry_service import list_digest_due


@dataclass
class DigestPayload:
    user: User
    items: list[PantryItem]


def build_digest_payload(session: Session, *, user_id: int,
                         today: date) -> Optional[DigestPayload]:
    user = session.get(User, user_id)
    if user is None:
        return None
    rows = list_digest_due(session, user_id=user_id, today=today)
    if not rows:
        return None
    return DigestPayload(user=user, items=rows)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_scheduler_payload.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/scheduler.py tests/test_scheduler_payload.py
git commit -m "feat(scheduler): build_digest_payload() honoring snooze + 7d window

Per spec §7.2: today + (now <= today + 7); snoozed items
suppressed only while snoozed_until > today; expired items remain
in the digest until acted upon.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 8.2 — `send_digest()` with retry

**Files:**
- Modify: `app/scheduler.py` — append send + retry helpers
- Create: `tests/test_scheduler_send.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scheduler_send.py
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlmodel import SQLModel, Session, create_engine
from app.models import User, PantryItem
from app.scheduler import send_digest_once


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=999, tz="America/Detroit",
                   digest_hour=8, created_at=datetime.now(timezone.utc)))
        s.add(PantryItem(
            user_id=1, raw_name="Milk", normalized_name="milk",
            category="dairy", qty=1.0, unit="gal",
            purchased_on=date(2026, 5, 26),
            shelf_life_days=2,
            shelf_life_source="llm", ingest_shelf_life_source="llm",
            expires_on=date(2026, 5, 28),
            status="active", created_via="manual",
            created_at=datetime.now(timezone.utc),
        ))
        s.commit()
        yield s


def _factory(session):
    return lambda: session


@pytest.mark.asyncio
async def test_send_digest_sends_when_items_due(session):
    bot = MagicMock()
    bot.send_message = AsyncMock()
    sent = await send_digest_once(
        user_id=1, bot=bot,
        session_factory=_factory(session),
        today_provider=lambda tz: date(2026, 5, 26),
    )
    assert sent is True
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 999
    assert "Milk" in kwargs["text"]
    assert kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_send_digest_skips_silent_day(session):
    bot = MagicMock()
    bot.send_message = AsyncMock()
    # nothing due 30 days from now
    sent = await send_digest_once(
        user_id=1, bot=bot,
        session_factory=_factory(session),
        today_provider=lambda tz: date(2026, 7, 1),
    )
    assert sent is False
    bot.send_message.assert_not_awaited()
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_scheduler_send.py -v`
Expected: ImportError.

- [ ] **Step 3: Append to `app/scheduler.py`**

```python
# add to app/scheduler.py
import asyncio
import logging
from typing import Awaitable, Callable
from app.renderer import build_digest_keyboard, render_digest
from app.bot import to_aiogram_keyboard


log = logging.getLogger(__name__)


SessionFactory = Callable[[], "Session"]
TodayProvider = Callable[[str], date]


async def send_digest_once(*, user_id: int, bot,
                            session_factory: SessionFactory,
                            today_provider: TodayProvider) -> bool:
    with session_factory() as session:
        # First lookup just to get tz so we can compute today in user-local time.
        user = session.get(User, user_id)
        if user is None:
            log.warning("digest_skip_unknown_user", extra={"user_id": user_id})
            return False
        today = today_provider(user.tz)
        payload = build_digest_payload(session, user_id=user_id, today=today)
        if payload is None:
            log.info("digest_silent_day",
                     extra={"user_id": user_id, "today": str(today)})
            return False
        render = render_digest(payload.items, today=today)
        kb = build_digest_keyboard(render.rendered_item_ids,
                                   has_more=render.has_more)
        await bot.send_message(
            chat_id=payload.user.chat_id,
            text=render.text,
            reply_markup=to_aiogram_keyboard(kb),
        )
        log.info("digest_sent",
                 extra={"user_id": user_id, "items": render.rendered_count})
        return True


async def send_digest_with_retry(*, user_id: int, bot,
                                  session_factory: SessionFactory,
                                  today_provider: TodayProvider,
                                  retry_sleep_seconds: int = 60) -> None:
    try:
        await send_digest_once(user_id=user_id, bot=bot,
                               session_factory=session_factory,
                               today_provider=today_provider)
        return
    except Exception as e:
        log.warning("digest_send_failed",
                    extra={"user_id": user_id,
                           "error_class": type(e).__name__,
                           "attempt": 1, "will_retry": True})
    await asyncio.sleep(retry_sleep_seconds)
    try:
        await send_digest_once(user_id=user_id, bot=bot,
                               session_factory=session_factory,
                               today_provider=today_provider)
    except Exception as e:
        log.warning("digest_send_failed",
                    extra={"user_id": user_id,
                           "error_class": type(e).__name__,
                           "attempt": 2, "will_retry": False})
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_scheduler_send.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/scheduler.py tests/test_scheduler_send.py
git commit -m "feat(scheduler): send_digest with one 60s retry on transient failure

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 8.3 — Cron registration + replacement

**Files:**
- Modify: `app/scheduler.py` — append `schedule_user_digest` + `register_all_user_digests`
- Create: `tests/test_scheduler_registration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scheduler_registration.py
from datetime import datetime, timezone
import pytest
from sqlmodel import SQLModel, Session, create_engine
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.models import User
from app.scheduler import schedule_user_digest, register_all_user_digests


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1, tz="America/Detroit",
                   digest_hour=8, created_at=datetime.now(timezone.utc)))
        s.add(User(telegram_id=2, chat_id=2, tz="UTC",
                   digest_hour=20, created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def _noop(*a, **kw):
    pass


def test_schedule_creates_job(session):
    sched = AsyncIOScheduler()
    user = session.get(User, 1)
    schedule_user_digest(sched, user, send=_noop)
    jobs = sched.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == "digest:1"


def test_schedule_replaces_existing_for_same_user(session):
    sched = AsyncIOScheduler()
    user = session.get(User, 1)
    schedule_user_digest(sched, user, send=_noop)
    user.digest_hour = 9
    schedule_user_digest(sched, user, send=_noop)
    jobs = sched.get_jobs()
    assert len(jobs) == 1
    cron = jobs[0].trigger
    assert cron.fields[cron.FIELD_NAMES.index("hour")].expressions[0].first == 9


def test_register_all_user_digests(session):
    sched = AsyncIOScheduler()
    register_all_user_digests(sched, session_factory=lambda: session, send=_noop)
    ids = {j.id for j in sched.get_jobs()}
    assert ids == {"digest:1", "digest:2"}
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_scheduler_registration.py -v`
Expected: ImportError.

- [ ] **Step 3: Append to `app/scheduler.py`**

```python
# add to app/scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import select as _select


def schedule_user_digest(scheduler: AsyncIOScheduler, user: User, *,
                          send: Callable[..., Awaitable[None]]) -> None:
    scheduler.add_job(
        send, "cron",
        hour=user.digest_hour, minute=0, timezone=user.tz,
        args=[user.telegram_id],
        id=f"digest:{user.telegram_id}",
        replace_existing=True,
    )


def register_all_user_digests(scheduler: AsyncIOScheduler, *,
                               session_factory: SessionFactory,
                               send: Callable[..., Awaitable[None]]) -> None:
    with session_factory() as session:
        users = list(session.exec(_select(User)).all())
    for u in users:
        schedule_user_digest(scheduler, u, send=send)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_scheduler_registration.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/scheduler.py tests/test_scheduler_registration.py
git commit -m "feat(scheduler): per-user cron jobs with replace_existing semantics

id='digest:{user_id}' lets /tz and /digest_at simply re-call
schedule_user_digest to replace the schedule atomically. Process
restart rebuilds jobs from User rows via register_all_user_digests.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 9 — Entry point, migrations boot, deployment

Goal: `bin/run.py` boots cleanly: settings → DB → pre-migration backup → migrate → bot+scheduler. Container deploys to Railway.

### Task 9.1 — Pre-migration SQLite backup helper

**Files:**
- Create: `app/backup.py`
- Create: `tests/test_backup.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backup.py
from pathlib import Path
from app.backup import pre_migration_backup, BackupError


def test_backup_creates_file(tmp_path):
    db = tmp_path / "f.db"
    db.write_bytes(b"sqlite-bytes")
    out = pre_migration_backup(str(db), keep=5)
    assert out is not None and Path(out).exists()
    assert Path(out).read_bytes() == b"sqlite-bytes"


def test_no_backup_when_db_missing(tmp_path):
    db = tmp_path / "missing.db"
    assert pre_migration_backup(str(db), keep=5) is None


def test_keeps_only_n_backups(tmp_path):
    db = tmp_path / "f.db"
    db.write_bytes(b"v1")
    pre_migration_backup(str(db), keep=3)
    db.write_bytes(b"v2"); pre_migration_backup(str(db), keep=3)
    db.write_bytes(b"v3"); pre_migration_backup(str(db), keep=3)
    db.write_bytes(b"v4"); pre_migration_backup(str(db), keep=3)
    backups = sorted(tmp_path.glob("f.db.backup-*"))
    assert len(backups) == 3


def test_failure_to_write_raises(tmp_path, monkeypatch):
    db = tmp_path / "f.db"
    db.write_bytes(b"x")
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr("shutil.copy2", boom)
    import pytest
    with pytest.raises(BackupError):
        pre_migration_backup(str(db), keep=3)
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_backup.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `app/backup.py`**

```python
# app/backup.py
from datetime import datetime
from pathlib import Path
import shutil


class BackupError(Exception):
    pass


def pre_migration_backup(database_path: str, *, keep: int) -> str | None:
    src = Path(database_path)
    if not src.exists():
        return None
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    dst = src.with_name(f"{src.name}.backup-{ts}")
    try:
        shutil.copy2(src, dst)
    except OSError as e:
        raise BackupError(str(e))
    # Prune oldest
    pattern = f"{src.name}.backup-*"
    backups = sorted(src.parent.glob(pattern), key=lambda p: p.name)
    for old in backups[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass
    return str(dst)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_backup.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/backup.py tests/test_backup.py
git commit -m "feat(backup): pre-migration local SQLite backup with rolling keep=N

Per spec §9.2: copy the live DB to a timestamped sibling before
running Alembic upgrade; retain the most recent N backups; raise
BackupError loudly so startup fails before migration runs.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 9.2 — `bin/run.py` entry point

**Files:**
- Create: `bin/run.py`
- Create: `bin/__init__.py` (empty)

- [ ] **Step 1: Implement `bin/run.py`**

```python
# bin/run.py
"""Entry point. Boots in this exact order (spec §9.2):

  1. load settings
  2. open DB engine + session factory
  3. pre-migration backup if DB file already exists
  4. alembic upgrade head (fatal on failure)
  5. construct Bot + Anthropic SDK + LLMClient
  6. register per-user digest jobs from User rows
  7. start AsyncIOScheduler
  8. start dispatcher polling
"""
from __future__ import annotations
import asyncio
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import app.bot as bot_mod
from app.backup import BackupError, pre_migration_backup
from app.bot import build_dispatcher
from app.db import make_engine, make_session_factory
from app.llm import AnthropicLLMClient
from app.scheduler import (
    register_all_user_digests, schedule_user_digest, send_digest_with_retry,
)
from app.settings import Settings


def _configure_logging(env: str, level: str) -> None:
    fmt = ("%(asctime)s %(levelname)s %(name)s %(message)s"
           if env != "prod" else
           '{"ts":"%(asctime)s","lvl":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format=fmt, stream=sys.stdout)


async def _amain(settings: Settings) -> None:
    log = logging.getLogger("food-manager")
    log.info("startup_begin")

    # 2. DB.
    engine = make_engine(settings.database_path)
    session_factory = make_session_factory(engine)

    # 3. Pre-migration backup if DB already exists.
    if Path(settings.database_path).exists():
        try:
            backup_path = pre_migration_backup(settings.database_path, keep=5)
            log.info("pre_migration_backup_ok", extra={"path": backup_path})
        except BackupError as e:
            log.error("pre_migration_backup_failed", extra={"error": str(e)})
            raise SystemExit(2)

    # 4. Migrate.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={"DATABASE_PATH": settings.database_path,
             **{k: v for k, v in __import__("os").environ.items()}},
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.error("migration_failed", extra={"stderr": result.stderr})
        raise SystemExit(3)
    log.info("migration_ok")

    # 5. Bot + LLM.
    bot = Bot(token=settings.telegram_bot_token)
    sdk = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    llm = AnthropicLLMClient(sdk=sdk, model=settings.anthropic_model)

    bot_mod.ALLOWED_TELEGRAM_USER_ID = settings.allowed_telegram_user_id

    scheduler = AsyncIOScheduler()

    async def _send(user_id: int) -> None:
        await send_digest_with_retry(
            user_id=user_id, bot=bot,
            session_factory=session_factory,
            today_provider=lambda tz: datetime.now(ZoneInfo(tz)).date(),
        )

    # 6. Existing users' jobs.
    register_all_user_digests(scheduler, session_factory=session_factory, send=_send)

    # The bot needs to call schedule_user_digest itself when /start auto-creates
    # a user or /tz / /digest_at update an existing one.
    def reschedule(user) -> None:
        schedule_user_digest(scheduler, user, send=_send)

    dispatcher = build_dispatcher(
        bot=bot, session_factory=session_factory, llm=llm,
        now_provider=lambda tz: datetime.now(ZoneInfo(tz)),
        on_user_created=reschedule, reschedule=reschedule,
    )

    # 7. Scheduler.
    scheduler.start()
    log.info("scheduler_started")

    # 8. Polling.
    log.info("polling_start")
    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


def main() -> None:
    settings = Settings()
    _configure_logging(settings.env, settings.log_level)
    asyncio.run(_amain(settings))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-check imports**

Run: `uv run python -c "import bin.run; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add bin/__init__.py bin/run.py
git commit -m "feat(run): bin/run.py entry point per spec §9.2 startup sequence

Settings → DB → pre-migration backup → alembic upgrade →
bot+SDK+scheduler. ALLOWED_TELEGRAM_USER_ID injected from settings.
on_user_created and /tz/digest_at reschedules share the same closure
so cron rows always match User rows.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 9.3 — Dockerfile + railway.toml

**Files:**
- Create: `Dockerfile`
- Create: `railway.toml`

- [ ] **Step 1: Implement `Dockerfile`**

```dockerfile
# Dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app/ ./app/
COPY bin/ ./bin/
COPY migrations/ ./migrations/
COPY alembic.ini ./

ENV DATABASE_PATH=/data/food.db
RUN mkdir -p /data

CMD ["uv", "run", "python", "bin/run.py"]
```

- [ ] **Step 2: Implement `railway.toml`**

```toml
# railway.toml
[build]
builder = "DOCKERFILE"
dockerfilePath = "Dockerfile"

[deploy]
startCommand = "uv run python bin/run.py"
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 5

[[deploy.volumes]]
mountPath = "/data"
name = "food-data"
```

- [ ] **Step 3: Sanity check Dockerfile parses**

```bash
docker build -t food-manager:dev . --no-cache
```

(skip if Docker not available locally — Railway will build on push.)

- [ ] **Step 4: Commit**

```bash
git add Dockerfile railway.toml
git commit -m "chore(deploy): Dockerfile + railway.toml with /data volume

Single-stage Python 3.12 slim. App, migrations, alembic.ini bundled.
Persistent volume mount at /data so SQLite survives redeploys.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 9.4 — Expand README quickstart

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace `README.md` with the deploy-ready version**

```markdown
# food-manager

Single-user grocery pantry tracker + daily expiry digest as a Telegram bot.

See `docs/superpowers/specs/2026-05-26-food-manager-v1-design.md` for the
full design spec and `docs/superpowers/plans/2026-05-26-food-manager-v1.md`
for the implementation plan.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- A Telegram bot token from `@BotFather`
- An Anthropic API key
- Your own Telegram user id (`@userinfobot`)

## Local dev

1. `uv sync`
2. `cp .env.example .env` and fill in:
   - `TELEGRAM_BOT_TOKEN`
   - `ALLOWED_TELEGRAM_USER_ID` (your numeric Telegram user id)
   - `ANTHROPIC_API_KEY`
3. `DATABASE_PATH=./food.db uv run alembic upgrade head`
4. `uv run python bin/run.py`
5. Send the bot `/start` from your Telegram account.

## Tests

`uv run pytest`

## Deploy to Railway

1. `railway init` (or import the repo in the Railway dashboard).
2. Add a persistent volume named `food-data` mounted at `/data`.
3. Set environment variables (same names as `.env.example`).
4. Push: `git push railway main`.

The container runs `bin/run.py` which migrates on boot, starts the bot
(long-polling), and registers per-user digest cron jobs.

## Daily use

| Action | Command |
|---|---|
| Log a receipt | Send a photo |
| Add manually | `/add 2 lb chicken, dozen eggs` |
| See pantry | `/list`, `/list dairy`, `/list week`, `/list expired` |
| Got something wrong | `/correct <id> <days>` — teaches future estimates |
| Wrong import | `/delete <id>` — does NOT teach future estimates |
| Set digest time | `/digest_at 7` |
| Change timezone | `/tz America/New_York` |
| Stats | `/stats` |

Each morning at your configured hour, you'll get a digest if anything is
expiring in the next 7 days, with one-tap `[Ate / Tossed / Remind +2d]`
buttons.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(readme): quickstart, deploy, daily-use cheat sheet

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 10 — Golden-receipt eval scaffold

Goal: one CLI script that runs a few real receipts through the *real* LLM and diffs against expected JSON. Manual / weekly. Not in CI.

### Task 10.1 — `bin/eval_receipts.py`

**Files:**
- Create: `bin/eval_receipts.py`
- Create: `tests/fixtures/expected/.gitkeep`

- [ ] **Step 1: Implement `bin/eval_receipts.py`**

```python
# bin/eval_receipts.py
"""Run real receipt photos through the LIVE Anthropic API and diff
each result against a committed expected JSON file.

Usage: `uv run python bin/eval_receipts.py`

Reads photos from tests/fixtures/private_receipts/<name>.jpg (gitignored)
and expected results from tests/fixtures/expected/<name>.json (sanitized,
may be committed). Asserts key fields only (item names + shelf life days)
to keep noise low.

Spec §8: this is the only place we hit the real API. Manual / weekly.
"""
from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path

import anthropic
from app.llm import AnthropicLLMClient
from app.settings import Settings


FIXTURES = Path("tests/fixtures/private_receipts")
EXPECTED = Path("tests/fixtures/expected")


async def _evaluate_one(client: AnthropicLLMClient, photo: Path) -> tuple[bool, str]:
    expected_path = EXPECTED / (photo.stem + ".json")
    if not expected_path.exists():
        return False, f"no expected file at {expected_path}"
    expected = json.loads(expected_path.read_text())

    result = await client.extract_items_from_image(photo.read_bytes())
    actual_items = [
        {"name": i.name, "est_shelf_life_days": i.est_shelf_life_days}
        for i in result.parse.items if i.is_food
    ]
    expected_items = expected.get("items", [])
    diffs = []
    for e in expected_items:
        match = next((a for a in actual_items if a["name"].lower() == e["name"].lower()), None)
        if match is None:
            diffs.append(f"missing item: {e['name']!r}")
            continue
        if abs(match["est_shelf_life_days"] - e["est_shelf_life_days"]) > 1:
            diffs.append(
                f"{e['name']}: shelf life {match['est_shelf_life_days']} "
                f"vs expected {e['est_shelf_life_days']}"
            )
    if diffs:
        return False, "  " + "\n  ".join(diffs)
    return True, f"{len(actual_items)} items matched"


async def _amain() -> int:
    settings = Settings()
    sdk = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    client = AnthropicLLMClient(sdk=sdk, model=settings.anthropic_model)

    photos = sorted(FIXTURES.glob("*.jpg")) + sorted(FIXTURES.glob("*.png"))
    if not photos:
        print(f"no photos in {FIXTURES}/")
        return 0

    fails = 0
    for p in photos:
        ok, detail = await _evaluate_one(client, p)
        marker = "PASS" if ok else "FAIL"
        print(f"{marker} {p.name}\n{detail}\n")
        if not ok:
            fails += 1
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
```

- [ ] **Step 2: Create the fixture directories**

```bash
mkdir -p tests/fixtures/private_receipts tests/fixtures/expected
touch tests/fixtures/expected/.gitkeep
```

`tests/fixtures/private_receipts/` is gitignored on purpose. Do not force-add
real receipt photos or placeholder files from that directory.

- [ ] **Step 3: Verify the script's imports parse**

Run: `uv run python -c "import bin.eval_receipts; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add bin/eval_receipts.py tests/fixtures/expected/.gitkeep
git commit -m "chore(eval): bin/eval_receipts.py for manual/weekly LLM golden-eval

Compares parsed results against committed sanitized JSON; tolerates
+/-1 day on shelf-life estimates. Real receipt images stay out of
git via .gitignore on tests/fixtures/private_receipts/.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 11 — Final integration smoke + DoD check

### Task 11.1 — Full test suite green

- [ ] **Step 1: Run all tests**

Run: `uv run pytest -v`
Expected: all tests pass; total count should be ~70+. If any fail, fix before moving on.

- [ ] **Step 2: Manual end-to-end (requires real bot token + Anthropic key)**

1. Create a `@food_manager_dev_bot` with `@BotFather`; put the dev token in `.env`.
2. `DATABASE_PATH=./food.db uv run alembic upgrade head`
3. `uv run python bin/run.py`
4. Send `/start`. Expect setup confirmation.
5. Send `/help`. Expect command list.
6. Send a real receipt photo. Expect parsed items + costs within ~15s.
7. Send `/list`. Expect the items just added, sorted by expiry.
8. Send `/correct <id> 3`. Expect a confirmation. Then send the same receipt's photo again — expect `this receipt was already logged`.
9. Send `/digest_at <next hour>` and wait for the daily digest at that hour. Tap `✓ Ate` on one row; expect it to disappear/strikethrough.
10. Send `/stats`. Expect non-zero receipt count, cache-hit %, cost.

- [ ] **Step 3: Sanity-check Definition of Done (spec §13)**

Tick each item in the spec's §13. Anything not ticked = a follow-up issue, not a release blocker UNLESS the test suite is red.

- [ ] **Step 4: Tag v1.0.0**

```bash
git tag -a v1.0.0 -m "v1.0.0: single-user pantry bot ready for daily use"
```

---

## Risks logged during planning

These don't block any task — they're notes for the implementer.

1. **`strftime("%-d")` is POSIX only.** Renderer tests must use a format token your dev OS supports. The spec was written cross-platform-aware; Task 6.1 already notes this.
2. **Anthropic's vision API expects JPEG by default.** If Telegram sends PNG or HEIC, the bot may need to convert; `Pillow` would be the smallest dependency to add if this bites. Not in v1 deps — wait for the first failure.
3. **APScheduler + asyncio sharing the loop.** The dispatcher must be started AFTER the scheduler so the scheduler claims the loop first. Task 9.2 orders these correctly.
4. **Alembic invoked as a subprocess from `bin/run.py`.** Slightly heavier than calling Alembic's API in-process, but isolation is worth it — a failed migration shouldn't poison the bot process's import state.
5. **`/stats` cache-hit % depends on `ingest_shelf_life_source`.** If you ever backfill that column, do it carefully — overwriting `cache` rows with `llm` rows would lie to `/stats`.

---

## Self-review

**Spec coverage:** every section of the spec (§1–§9) is implemented by at least one task. §10 user-authored TODOs are scaffolded in Tasks 2.1, 2.3, 3.2, 6.1; the user fills in `/list` filter ordering preferences in Task 7.2 already. §11 future-plan sketches are explicit non-goals — no tasks (correct).

**Placeholder scan:** no TBD/TODO-in-the-plan (user-authored TODOs in the scaffolded code are intentional per §10). Every step that changes code shows the exact code; every test step shows the test code; every command step shows the exact command. No "similar to Task N" — code is repeated where needed (e.g., `_item()` helpers in tests are duplicated by design because subagents may run tasks out of order).

**Type consistency:** `ShelfLifeDecision` (Task 4.1) has fields `days / source / cache_was_hit` consistently referenced through Task 4.2. `MutationResult` (Task 5.2) has fields `applied / was_already` used identically in Tasks 5.2, 5.3, 7.4. `CallbackButton.callback_data` shape `act:{verb}:{item_id}` (Task 6.2) matches the `parse_callback()` parser (Task 7.3) and the verbs handled in `handle_callback` (Task 7.4): `ate`, `toss`, `snooze2`, `show_all`. `IngestSummary` field names used by `render_ingest_reply` (Task 6.1) match those produced by `ingest_photo` (Task 4.2).

**Spec requirements with no task — none found.** v1 is fully covered.
