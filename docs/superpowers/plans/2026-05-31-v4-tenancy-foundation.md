# v4.0 Tenancy Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a `Household` entity that owns all shared pantry data, re-key every shared table from `user_id` to `household_id`, and migrate the existing single user into a "household of one" — leaving the bot behaving exactly as today but tenant-ready.

**Architecture:** A new `Household` table holds the food-profile fields moved off `User`; `User` gains `household_id`. Every shared table (`PantryItem`, `Receipt`, `ShelfLifeCache`, `PendingCorrection`, `CookSession`, `ShoppingList`, `SavedRecipe`) swaps its `user_id` ownership column for `household_id`. All service functions take `household_id` instead of `user_id` on ownership paths. The bot provisions a household-of-one for the bootstrap owner and passes `user.household_id` to services. Multi-user join (invite codes) and group chat are **separate later plans**.

**Tech Stack:** Python 3, SQLModel/SQLAlchemy, Alembic (SQLite), aiogram, pytest, `uv`.

---

## Plan decomposition (read first)

The v4.0 spec (`docs/superpowers/specs/2026-05-31-v4-tenancy-overhaul-design.md`) is delivered as **three sequential plans**, each leaving the bot working and the suite green:

1. **This plan — Tenancy Foundation.** `Household` + re-key + migration `0007`. End state: single bootstrap user operates on a household-of-one; behaviour identical to today.
2. **Plan 2 — Multi-user join.** `HouseholdInvite`, `/invite`, `/join`, auth rewrite to resolve a member's household, migration `0008`.
3. **Plan 3 — Group chat.** `GroupBinding`, `/bind`, group routing, photo-in-group rejection, migration `0009`.

> **Deviation from spec §5:** the spec proposed a single migration `0007`. Splitting it across the three plans (one migration each) keeps every plan independently shippable and testable. The end-state schema is identical.

## File structure

| File | Responsibility | This plan |
|---|---|---|
| `app/models.py` | ORM tables | add `Household`, `User.household_id`; re-key 7 shared tables + indexes |
| `app/household_service.py` *(new)* | household lifecycle | `provision_solo_household(session, user)` |
| `migrations/versions/0007_household.py` *(new)* | schema migration | create `household`, backfill, re-key, swap indexes |
| `app/cache.py` | shelf-life cache CRUD | `user_id` → `household_id` |
| `app/pantry_service.py` | pantry CRUD/stats | `user_id` → `household_id` |
| `app/ingest_service.py` | photo/text → items | `user_id` → `household_id` |
| `app/pending_service.py` | pending proposals | `user_id` → `household_id` |
| `app/correction_service.py` | correct/add proposals | `user_id` → `household_id` |
| `app/cook_session_service.py` | cook session lifecycle | `user_id` → `household_id` |
| `app/shopping_service.py` | to-buy list | `user_id` → `household_id` |
| `app/favorites_service.py` | saved recipes | `user_id` → `household_id` |
| `app/profile_service.py` | food profile | read/write on `Household` |
| `app/scheduler.py` | digest jobs | resolve `user → household` before querying |
| `app/bot.py` | aiogram handlers | provision household; pass `household_id` to services |
| `tests/**` | suite | update `user_id` fixtures → `household_id`; add isolation + migration tests |

**Convention reminders (from `CLAUDE.md`):** service functions take `session` first and an explicit `today`/`now` (never call `datetime.now()` inside); `PantryItem.id` etc. are `Optional[int]` (assert non-None in tests); run tests with `uv run pytest`.

> **Implementation note on the re-key:** these tasks rename the *ownership* parameter `user_id: int` → `household_id: int` and the column `X.user_id` → `X.household_id` throughout each service. Internal variable names and exception text that mention "user" may stay; only the ownership identity changes. Each service task is its own red/green/commit cycle so the suite is bisectable.

---

## Task 1: `Household` model + `User.household_id`; move profile fields off `User`

**Files:**
- Modify: `app/models.py:28-40` (the `User` class) and add `Household` above it.
- Test: `tests/test_household_models.py` *(new)*

- [ ] **Step 1: Write the failing test**

```python
# tests/test_household_models.py
from datetime import datetime, timezone

from sqlmodel import Session, SQLModel, create_engine

from app.models import Household, User


def _engine():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return engine


def test_household_owns_user_and_holds_profile():
    engine = _engine()
    with Session(engine) as db:
        hh = Household(name="Smiths", diet="vegetarian", created_at=datetime.now(timezone.utc))
        db.add(hh)
        db.commit()
        db.refresh(hh)
        assert hh.id is not None
        db.add(User(telegram_id=1, chat_id=1, household_id=hh.id,
                    created_at=datetime.now(timezone.utc)))
        db.commit()
        loaded = db.get(User, 1)
        assert loaded is not None and loaded.household_id == hh.id


def test_user_no_longer_has_profile_fields():
    # profile fields moved to Household
    assert not hasattr(User(telegram_id=1, chat_id=1, household_id=1,
                            created_at=datetime.now(timezone.utc)), "diet")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_household_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'Household'`.

- [ ] **Step 3: Add `Household` and edit `User` in `app/models.py`**

Insert `Household` immediately before `class User` (after the `Literal` aliases, ~line 27):

```python
class Household(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = "My Household"
    diet: str = "none"
    exclusions_json: str = "[]"
    preferred_cuisines_json: str = "[]"
    max_cook_minutes: Optional[int] = None
    household_size: int = 1
    profile_note: str = ""
    created_at: datetime
```

Replace the `User` class body (lines 28-40) with — note the six profile fields are **removed** and `household_id` is **added**:

```python
class User(SQLModel, table=True):
    telegram_id: int = Field(primary_key=True)
    chat_id: int
    household_id: int = Field(foreign_key="household.id", index=True)
    tz: str = "America/Detroit"
    digest_hour: int = 8
    llm_provider: str = "anthropic"
    created_at: datetime
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_household_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_household_models.py
git commit -m "feat(models): add Household; move food profile off User"
```

---

## Task 2: Re-key the 7 shared tables to `household_id`

**Files:**
- Modify: `app/models.py` — `Receipt`, `PantryItem`, `ShelfLifeCache`, `PendingCorrection`, `CookSession`, `ShoppingList`, `SavedRecipe`.
- Test: `tests/test_household_models.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_household_models.py`:

```python
from app.models import (
    CookSession, PantryItem, PendingCorrection, Receipt, SavedRecipe,
    ShelfLifeCache, ShoppingList,
)


def test_shared_tables_are_household_keyed():
    for model in (Receipt, PantryItem, PendingCorrection, CookSession,
                  ShoppingList, SavedRecipe):
        cols = set(model.model_fields)
        assert "household_id" in cols, model.__name__
        assert "user_id" not in cols, model.__name__
    # ShelfLifeCache: composite PK now (household_id, normalized_name)
    assert "household_id" in set(ShelfLifeCache.model_fields)
    assert "user_id" not in set(ShelfLifeCache.model_fields)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_household_models.py::test_shared_tables_are_household_keyed -v`
Expected: FAIL — `user_id` still present.

- [ ] **Step 3: Edit each table in `app/models.py`**

`Receipt` — replace `__table_args__` and the `user_id` line:

```python
class Receipt(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("household_id", "photo_file_id", name="uq_receipt_household_photo"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
    photo_file_id: str
    purchase_date: date
    purchase_date_source: str
    scanned_at: datetime
    llm_cost_micros_usd: Optional[int] = None
```

`PantryItem` — replace `__table_args__` and the `user_id` line:

```python
class PantryItem(SQLModel, table=True):
    __table_args__ = (
        Index("ix_pantry_household_status_expires", "household_id", "status", "expires_on"),
        Index(
            "ix_pantry_household_status_category_expires",
            "household_id", "status", "category", "expires_on",
        ),
        Index("ix_pantry_source_receipt", "source_receipt_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
    raw_name: str
    # ...rest of PantryItem unchanged...
```

`ShelfLifeCache` — PK moves to `household_id`:

```python
class ShelfLifeCache(SQLModel, table=True):
    household_id: int = Field(foreign_key="household.id", primary_key=True)
    normalized_name: str = Field(primary_key=True)
    days: int
    category: Optional[str] = None
    confidence: float
    learned_at: datetime
    source: str = "llm"
```

`PendingCorrection` — index + column:

```python
class PendingCorrection(SQLModel, table=True):
    __table_args__ = (
        Index("ix_pending_household_status_created", "household_id", "status", "created_at"),
        Index("ix_pending_item", "item_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
    action_type: str
    # ...rest unchanged...
```

`CookSession` — index + column:

```python
class CookSession(SQLModel, table=True):
    __table_args__ = (
        Index("ix_cook_household_status_created", "household_id", "status", "created_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
    status: str = "collecting"
    # ...rest unchanged...
```

`ShoppingList` and `SavedRecipe` — swap the `user_id` line for:

```python
    household_id: int = Field(foreign_key="household.id", index=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_household_models.py -v`
Expected: PASS. (The rest of the suite is now red — services still say `user_id`. That's expected; Tasks 4-13 fix it.)

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_household_models.py
git commit -m "feat(models): re-key shared tables user_id -> household_id"
```

---

## Task 3: Alembic migration `0007` (create household, backfill, re-key, swap indexes)

**Files:**
- Create: `migrations/versions/0007_household.py`
- Modify: `tests/test_migrations.py` (the old index-name assertions must become the new names)
- Test: `tests/test_migration_0007_backfill.py` *(new)*

- [ ] **Step 1: Write the failing backfill test**

```python
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
    # Build the pre-0007 schema and seed one user + one pantry row.
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
    # one household created, profile copied from the user
    hh = cur.execute("SELECT id, diet, exclusions_json, household_size, profile_note "
                     "FROM household").fetchall()
    assert len(hh) == 1
    hid, diet, excl, size, note = hh[0]
    assert (diet, excl, size, note) == ("vegan", '["nuts"]', 2, "note")
    # user linked to it, profile columns dropped
    assert cur.execute("SELECT household_id FROM user WHERE telegram_id=7").fetchone()[0] == hid
    user_cols = {r[1] for r in cur.execute("PRAGMA table_info('user')").fetchall()}
    assert "diet" not in user_cols and "user_id" not in user_cols
    # pantry + cache rows backfilled to the household, user_id column gone
    assert cur.execute("SELECT household_id FROM pantryitem WHERE raw_name='Milk'").fetchone()[0] == hid
    assert cur.execute("SELECT household_id FROM shelflifecache WHERE normalized_name='milk'").fetchone()[0] == hid
    pcols = {r[1] for r in cur.execute("PRAGMA table_info('pantryitem')").fetchall()}
    assert "user_id" not in pcols and "household_id" in pcols
    con.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_migration_0007_backfill.py -v`
Expected: FAIL — `alembic upgrade head` errors (no `0007` revision / target `household` table missing).

- [ ] **Step 3: Write the migration**

```python
# migrations/versions/0007_household.py
"""v4.0: Household tenancy — create household, backfill, re-key shared tables.

Revision ID: 0007_household
Revises: 0006_cook_v35
Create Date: 2026-05-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_household"
down_revision: Union[str, None] = "0006_cook_v35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# tables re-keyed with a simple user_id -> household_id swap (no PK change)
_SIMPLE = ["receipt", "pantryitem", "pendingcorrection", "cooksession",
           "shoppinglist", "savedrecipe"]


def upgrade() -> None:
    bind = op.get_bind()

    # 1. household table (profile fields move here from user)
    op.create_table(
        "household",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, server_default="My Household"),
        sa.Column("diet", sa.String(), nullable=False, server_default="none"),
        sa.Column("exclusions_json", sa.String(), nullable=False, server_default="[]"),
        sa.Column("preferred_cuisines_json", sa.String(), nullable=False, server_default="[]"),
        sa.Column("max_cook_minutes", sa.Integer(), nullable=True),
        sa.Column("household_size", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("profile_note", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    # 2. user.household_id (nullable for backfill), then one household per user
    op.add_column("user", sa.Column("household_id", sa.Integer(), nullable=True))
    users = bind.execute(sa.text(
        "SELECT telegram_id, diet, exclusions_json, preferred_cuisines_json, "
        "max_cook_minutes, household_size, profile_note, created_at FROM user"
    )).fetchall()
    for u in users:
        res = bind.execute(sa.text(
            "INSERT INTO household (name, diet, exclusions_json, preferred_cuisines_json, "
            "max_cook_minutes, household_size, profile_note, created_at) VALUES "
            "('My Household', :diet, :excl, :cuis, :maxc, :size, :note, :created)"
        ), {"diet": u.diet, "excl": u.exclusions_json, "cuis": u.preferred_cuisines_json,
            "maxc": u.max_cook_minutes, "size": u.household_size,
            "note": u.profile_note, "created": u.created_at})
        hid = res.lastrowid
        bind.execute(sa.text("UPDATE user SET household_id = :hid WHERE telegram_id = :tid"),
                     {"hid": hid, "tid": u.telegram_id})

    # 3. add household_id to each shared table and backfill via user_id -> user.household_id
    for tbl in _SIMPLE:
        op.add_column(tbl, sa.Column("household_id", sa.Integer(), nullable=True))
        bind.execute(sa.text(
            f"UPDATE {tbl} SET household_id = "
            f"(SELECT household_id FROM user WHERE user.telegram_id = {tbl}.user_id)"
        ))
    # shelflifecache is rebuilt wholesale below (PK change), so just backfill into a temp col
    op.add_column("shelflifecache", sa.Column("household_id", sa.Integer(), nullable=True))
    bind.execute(sa.text(
        "UPDATE shelflifecache SET household_id = "
        "(SELECT household_id FROM user WHERE user.telegram_id = shelflifecache.user_id)"
    ))

    # 4. user: drop the six moved profile columns, make household_id NOT NULL + FK
    with op.batch_alter_table("user") as b:
        b.alter_column("household_id", existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key("fk_user_household", "household", ["household_id"], ["id"])
        b.create_index("ix_user_household_id", ["household_id"])
        for col in ("diet", "exclusions_json", "preferred_cuisines_json",
                    "max_cook_minutes", "household_size", "profile_note"):
            b.drop_column(col)

    # 5. receipt: swap unique constraint + drop user_id; household_id NOT NULL + FK
    with op.batch_alter_table("receipt") as b:
        b.alter_column("household_id", existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key("fk_receipt_household", "household", ["household_id"], ["id"])
        b.create_index("ix_receipt_household_id", ["household_id"])
        b.create_unique_constraint("uq_receipt_household_photo", ["household_id", "photo_file_id"])
        b.drop_column("user_id")

    # 6. pantryitem: swap composite indexes + drop user_id
    op.drop_index("ix_pantry_user_status_expires", table_name="pantryitem")
    op.drop_index("ix_pantry_user_status_category_expires", table_name="pantryitem")
    with op.batch_alter_table("pantryitem") as b:
        b.alter_column("household_id", existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key("fk_pantry_household", "household", ["household_id"], ["id"])
        b.create_index("ix_pantry_household_id", ["household_id"])
        b.create_index("ix_pantry_household_status_expires",
                       ["household_id", "status", "expires_on"])
        b.create_index("ix_pantry_household_status_category_expires",
                       ["household_id", "status", "category", "expires_on"])
        b.drop_column("user_id")

    # 7. pendingcorrection: swap composite index + drop user_id
    op.drop_index("ix_pending_user_status_created", table_name="pendingcorrection")
    with op.batch_alter_table("pendingcorrection") as b:
        b.alter_column("household_id", existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key("fk_pending_household", "household", ["household_id"], ["id"])
        b.create_index("ix_pending_household_id", ["household_id"])
        b.create_index("ix_pending_household_status_created",
                       ["household_id", "status", "created_at"])
        b.drop_column("user_id")

    # 8. cooksession: swap composite index + drop user_id
    op.drop_index("ix_cook_user_status_created", table_name="cooksession")
    with op.batch_alter_table("cooksession") as b:
        b.alter_column("household_id", existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key("fk_cook_household", "household", ["household_id"], ["id"])
        b.create_index("ix_cook_household_id", ["household_id"])
        b.create_index("ix_cook_household_status_created",
                       ["household_id", "status", "created_at"])
        b.drop_column("user_id")

    # 9. shoppinglist + savedrecipe: drop user_id index, drop user_id
    op.drop_index("ix_shoppinglist_user_id", table_name="shoppinglist")
    with op.batch_alter_table("shoppinglist") as b:
        b.alter_column("household_id", existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key("fk_shopping_household", "household", ["household_id"], ["id"])
        b.create_index("ix_shoppinglist_household_id", ["household_id"])
        b.drop_column("user_id")
    op.drop_index("ix_savedrecipe_user_id", table_name="savedrecipe")
    with op.batch_alter_table("savedrecipe") as b:
        b.alter_column("household_id", existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key("fk_saved_household", "household", ["household_id"], ["id"])
        b.create_index("ix_savedrecipe_household_id", ["household_id"])
        b.drop_column("user_id")

    # 10. shelflifecache: PK change (user_id, name) -> (household_id, name) via rebuild
    op.create_table(
        "shelflifecache_new",
        sa.Column("household_id", sa.Integer(), sa.ForeignKey("household.id"), primary_key=True),
        sa.Column("normalized_name", sa.String(), primary_key=True),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("learned_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(), nullable=False, server_default="llm"),
    )
    bind.execute(sa.text(
        "INSERT INTO shelflifecache_new "
        "(household_id, normalized_name, days, category, confidence, learned_at, source) "
        "SELECT household_id, normalized_name, days, category, confidence, learned_at, source "
        "FROM shelflifecache WHERE household_id IS NOT NULL"
    ))
    op.drop_table("shelflifecache")
    op.rename_table("shelflifecache_new", "shelflifecache")


def downgrade() -> None:
    raise NotImplementedError("0007 household migration is forward-only")
```

- [ ] **Step 4: Run both migration tests to verify they pass**

Run: `uv run pytest tests/test_migration_0007_backfill.py -v`
Expected: PASS. If a `batch_alter_table` step errors, read the alembic stderr in the assertion message and fix the offending step, then re-run.

- [ ] **Step 5: Update `tests/test_migrations.py` for the new index/column names**

Replace the `pantryitem`/`pendingcorrection` index assertions and the unique-constraint assertion (lines 47, 55-67) with the household equivalents:

```python
    assert ("household_id", "photo_file_id") in unique_columns
    ...
    assert "ix_pantry_household_status_expires" in pantry_indexes
    assert "ix_pantry_household_status_category_expires" in pantry_indexes
    assert "ix_pantry_source_receipt" in pantry_indexes
    ...
    assert "ix_pending_household_status_created" in pending_indexes
    assert "ix_pending_item" in pending_indexes
```

Also add `"household"` to the `issubset(tables)` set.

- [ ] **Step 6: Run the migration suite**

Run: `uv run pytest tests/test_migrations.py tests/test_migration_0007_backfill.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add migrations/versions/0007_household.py tests/test_migrations.py tests/test_migration_0007_backfill.py
git commit -m "feat(migrate): 0007 household tenancy — create, backfill, re-key"
```

---

## Task 4: Re-key `app/cache.py`

**Files:**
- Modify: `app/cache.py` (all functions)
- Test: `tests/test_core_services.py` (cache tests) — update `user_id=` kwargs to `household_id=`

- [ ] **Step 1: Edit `app/cache.py`** — rename the ownership parameter and PK tuple. Every `user_id` becomes `household_id`:

```python
def get_cached(session, household_id: int, normalized_name: str):
    return session.get(ShelfLifeCache, (household_id, normalized_name))


def put_cached(session, household_id, normalized_name, *, days, category,
               confidence, source="llm", commit=True):
    existing = get_cached(session, household_id, normalized_name)
    if existing is not None:
        return existing
    row = ShelfLifeCache(household_id=household_id, normalized_name=normalized_name,
                         days=days, category=category, confidence=confidence,
                         learned_at=datetime.now(timezone.utc), source=source)
    # ...unchanged tail...


def write_user_correction(session, household_id, normalized_name, *, days,
                          category=None, commit=True):
    existing = get_cached(session, household_id, normalized_name)
    # ...same body, but the ShelfLifeCache(...) constructor uses household_id=household_id...
```

- [ ] **Step 2: Update the cache tests** in `tests/test_core_services.py` — change every `get_cached(db, user_id, ...)` / `put_cached(..., user_id, ...)` / `write_user_correction(..., user_id, ...)` call to pass a household id, and any `ShelfLifeCache(user_id=...)` construction to `household_id=...`. (Grep `tests/test_core_services.py` for `cache` and `ShelfLifeCache`.)

- [ ] **Step 3: Run the cache tests**

Run: `uv run pytest tests/test_core_services.py -k cache -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add app/cache.py tests/test_core_services.py
git commit -m "refactor(cache): key shelf-life cache by household_id"
```

---

## Task 5: Re-key `app/pantry_service.py`

**Files:**
- Modify: `app/pantry_service.py` (every function with a `user_id` ownership param)
- Test: `tests/test_core_services.py`, `tests/test_v2_undo.py`, `tests/test_cook_v35_stats.py`

- [ ] **Step 1: Edit `app/pantry_service.py`.** Rename `user_id: int` → `household_id: int` on: `list_active`, `active_pantry_names`, `list_digest_due`, `_load_owned`, `mark_eaten`, `mark_tossed`, `mark_removed`, `snooze_item`, `correct_item`, `compute_stats`, `undo_receipt`, `undo_add`. Change `PantryItem.user_id ==` / `Receipt.user_id ==` / `PendingCorrection.user_id ==` filters to `.household_id ==`, and `pantry_item.user_id != user_id` → `pantry_item.household_id != household_id`. Update the internal calls that forward the id:

```python
def _set_terminal(session, pantry_item, status):
    if pantry_item.status != "active":
        return MutationResult(applied=False, was_already=True)
    pantry_item.status = status
    pantry_item.snoozed_until = None
    assert pantry_item.id is not None
    expire_for_item(session, household_id=pantry_item.household_id, item_id=pantry_item.id)
    # ...
```

`correct_item` forwards to the cache with household:

```python
    write_user_correction(session, household_id, pantry_item.normalized_name,
                          days=days, category=pantry_item.category, commit=False)
```

And in `compute_stats`, every `.where(X.user_id == user_id)` becomes `.where(X.household_id == household_id)` (Receipt, PantryItem, PendingCorrection, CookSession).

> `NotOwnerOrMissing`'s docstring may keep the word "user"; only the comparison changes.

- [ ] **Step 2: Update the tests.** In `tests/test_core_services.py`, `tests/test_v2_undo.py`, `tests/test_cook_v35_stats.py`: change `user_id=<n>` kwargs on these pantry calls to `household_id=<n>`, and any `PantryItem(user_id=...)`/`Receipt(user_id=...)` constructions to `household_id=...`. Seed a `Household` row where a FK is now required.

- [ ] **Step 3: Run**

Run: `uv run pytest tests/test_core_services.py tests/test_v2_undo.py tests/test_cook_v35_stats.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add app/pantry_service.py tests/test_core_services.py tests/test_v2_undo.py tests/test_cook_v35_stats.py
git commit -m "refactor(pantry): key pantry service by household_id"
```

---

## Task 6: Re-key `app/pending_service.py` + `app/correction_service.py`

**Files:**
- Modify: `app/pending_service.py`, `app/correction_service.py`
- Test: `tests/test_core_services.py`, `tests/test_v2_websearch.py`

- [ ] **Step 1: Edit `app/pending_service.py`.** Rename `user_id` → `household_id` on `create_pending`, `load_pending`, `expire_for_item`. Constructor `PendingCorrection(household_id=household_id, ...)`; filters `PendingCorrection.household_id == household_id`; ownership check `pending.household_id != household_id`.

- [ ] **Step 2: Edit `app/correction_service.py`.** Rename `user_id` → `household_id` on `propose_correct`, `apply_correct`, `propose_add`, `apply_add`. Forward to cache helpers with `household_id`; the `session.get(ShelfLifeCache, (household_id, old_normalized))` tuple uses `household_id`; `PantryItem(household_id=household_id, ...)` in `apply_add`.

- [ ] **Step 3: Update tests** in `tests/test_core_services.py` and `tests/test_v2_websearch.py`: `user_id=` → `household_id=` on these calls; seed a `Household`.

- [ ] **Step 4: Run**

Run: `uv run pytest tests/test_core_services.py tests/test_v2_websearch.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/pending_service.py app/correction_service.py tests/test_core_services.py tests/test_v2_websearch.py
git commit -m "refactor(pending,correction): key by household_id"
```

---

## Task 7: Re-key `app/ingest_service.py`

**Files:**
- Modify: `app/ingest_service.py` (`compute_shelf_life`, `ingest_photo`)
- Test: `tests/test_core_services.py`, `tests/test_v2_recognition.py`

- [ ] **Step 1: Edit `app/ingest_service.py`.** `compute_shelf_life(session, *, household_id, parsed)` — forward `get_cached(session, household_id, ...)` and `put_cached(session, household_id, ...)`. `ingest_photo(session, llm, *, household_id, photo_file_id, image_bytes, today)` — the duplicate-check filter `Receipt.household_id == household_id`; `Receipt(household_id=household_id, ...)`; `compute_shelf_life(session, household_id=household_id, parsed=...)`; `PantryItem(household_id=household_id, ...)`. Update the `DuplicateReceipt` docstring text only if desired.

- [ ] **Step 2: Update tests** in `tests/test_core_services.py` and `tests/test_v2_recognition.py`: `ingest_photo(..., user_id=...)` → `household_id=...`; seed a `Household`.

- [ ] **Step 3: Run**

Run: `uv run pytest tests/test_core_services.py tests/test_v2_recognition.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add app/ingest_service.py tests/test_core_services.py tests/test_v2_recognition.py
git commit -m "refactor(ingest): key receipt ingest by household_id"
```

---

## Task 8: Re-key `app/cook_session_service.py`

**Files:**
- Modify: `app/cook_session_service.py` (`supersede_active`, `create_cook_session`, `load_cook_session`)
- Test: `tests/test_cook_session.py`, `tests/test_cook_v35_bot_callbacks.py`

- [ ] **Step 1: Edit `app/cook_session_service.py`.** Rename `user_id` → `household_id` on `supersede_active`, `create_cook_session`, `load_cook_session`. `CookSession.household_id == household_id` filters; `CookSession(household_id=household_id, ...)`; ownership check `row.household_id != household_id`.

- [ ] **Step 2: Update tests.** `tests/test_cook_session.py` and the `CookSession(user_id=1, ...)` constructions in `tests/test_cook_v35_bot_callbacks.py` → `household_id=1`; `create_cook_session`/`load_cook_session` `user_id=` → `household_id=`. Seed a `Household`.

- [ ] **Step 3: Run**

Run: `uv run pytest tests/test_cook_session.py -v`
Expected: PASS. (Full cook-bot tests still need Task 12's bot edits; that's fine.)

- [ ] **Step 4: Commit**

```bash
git add app/cook_session_service.py tests/test_cook_session.py tests/test_cook_v35_bot_callbacks.py
git commit -m "refactor(cook-session): key by household_id"
```

---

## Task 9: Re-key `app/shopping_service.py` + `app/favorites_service.py`

**Files:**
- Modify: `app/shopping_service.py`, `app/favorites_service.py`
- Test: `tests/test_shopping_service.py`, `tests/test_favorites_service.py`

- [ ] **Step 1: Edit `app/shopping_service.py`.** Rename `user_id` → `household_id` on `_pending_normalized`, `add_missing`, `list_pending`, `check_off`. `ShoppingList.household_id == household_id`; `ShoppingList(household_id=household_id, ...)`; `row.household_id != household_id`.

- [ ] **Step 2: Edit `app/favorites_service.py`.** Rename `user_id` → `household_id` on `save_candidate`, `list_saved`, `load_saved`, `recook_shopping_list`. `SavedRecipe.household_id == household_id`; `SavedRecipe(household_id=household_id, ...)`; `row.household_id != household_id`; `active_pantry_names(session, household_id=household_id, today=today)`.

- [ ] **Step 3: Update tests** in `tests/test_shopping_service.py`, `tests/test_favorites_service.py`: `user_id=` → `household_id=`; seed a `Household`.

- [ ] **Step 4: Run**

Run: `uv run pytest tests/test_shopping_service.py tests/test_favorites_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/shopping_service.py app/favorites_service.py tests/test_shopping_service.py tests/test_favorites_service.py
git commit -m "refactor(shopping,favorites): key by household_id"
```

---

## Task 10: Move the food profile onto `Household` (`app/profile_service.py`)

**Files:**
- Modify: `app/profile_service.py`
- Test: `tests/test_cook_profile.py`

- [ ] **Step 1: Write/adjust the failing test.** In `tests/test_cook_profile.py`, the profile now reads from a `Household`. Change `profile_from_user(user)` call sites to `profile_from_household(household)` and construct a `Household(diet=..., exclusions_json=..., ...)` instead of setting those on `User`. Add:

```python
def test_profile_round_trips_through_household():
    from app.models import Household
    from app.profile_service import (FoodProfile, apply_profile_to_household,
                                      profile_from_household)
    hh = Household(name="x", created_at=__import__("datetime").datetime.now())
    apply_profile_to_household(hh, FoodProfile(diet="keto", exclusions=["soy"],
                                               household_size=3))
    p = profile_from_household(hh)
    assert p.diet == "keto" and p.exclusions == ["soy"] and p.household_size == 3
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cook_profile.py::test_profile_round_trips_through_household -v`
Expected: FAIL — `profile_from_household` undefined.

- [ ] **Step 3: Edit `app/profile_service.py`.** Replace the `User`-bound functions with `Household`-bound ones and update `update_profile_from_sentence` to take a `household`:

```python
from app.models import Household

def profile_from_household(household: Household) -> FoodProfile:
    return FoodProfile(
        diet=household.diet,
        exclusions=json.loads(household.exclusions_json or "[]"),
        preferred_cuisines=json.loads(household.preferred_cuisines_json or "[]"),
        max_cook_minutes=household.max_cook_minutes,
        household_size=household.household_size,
        note=household.profile_note,
    )

def apply_profile_to_household(household: Household, profile: FoodProfile) -> None:
    household.diet = profile.diet
    household.exclusions_json = json.dumps(profile.exclusions)
    household.preferred_cuisines_json = json.dumps(profile.preferred_cuisines)
    household.max_cook_minutes = profile.max_cook_minutes
    household.household_size = profile.household_size
    household.profile_note = profile.note

async def update_profile_from_sentence(session, *, llm, household: Household,
                                       sentence: str):
    current = profile_from_household(household)
    merged, cost = await llm.parse_profile_update(current=current, sentence=sentence)
    apply_profile_to_household(household, merged)
    session.add(household)
    session.commit()
    session.refresh(household)
    return merged, cost
```

- [ ] **Step 4: Run**

Run: `uv run pytest tests/test_cook_profile.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/profile_service.py tests/test_cook_profile.py
git commit -m "refactor(profile): store food profile on Household"
```

---

## Task 11: `provision_solo_household` helper + scheduler digest resolution

**Files:**
- Create: `app/household_service.py`
- Modify: `app/scheduler.py` (`build_digest_payload`, `send_digest_once`)
- Test: `tests/test_household_service.py` *(new)*, `tests/test_v1_5_rendering_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_household_service.py
from datetime import datetime, timezone
from sqlmodel import Session, SQLModel, create_engine
from app.models import Household, User
from app.household_service import provision_solo_household


def test_provision_creates_one_household_and_links_user():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        u = User(telegram_id=5, chat_id=5, household_id=0,
                 created_at=datetime.now(timezone.utc))
        hh = provision_solo_household(db, u)
        assert hh.id is not None and u.household_id == hh.id
        assert db.exec(__import__("sqlmodel").select(Household)).all()  # persisted
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_household_service.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Create `app/household_service.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session

from app.models import Household, User


def provision_solo_household(session: Session, user: User) -> Household:
    """Create a household-of-one and link the user to it. Caller commits context."""
    household = Household(name="My Household", created_at=datetime.now(timezone.utc))
    session.add(household)
    session.commit()
    session.refresh(household)
    user.household_id = household.id
    session.add(user)
    session.commit()
    session.refresh(user)
    return household
```

- [ ] **Step 4: Edit `app/scheduler.py`** so the digest resolves the user's household. In `build_digest_payload`, after loading `user`, query by household:

```python
def build_digest_payload(session, *, user_id, today):
    user = session.get(User, user_id)
    if user is None:
        return None
    rows = list_digest_due(session, household_id=user.household_id, today=today)
    if not rows:
        return None
    return DigestPayload(user=user, items=rows)
```

(`send_digest_once` already calls `build_digest_payload`; no other change.)

- [ ] **Step 5: Update `tests/test_v1_5_rendering_scheduler.py`** — any `list_digest_due(..., user_id=...)` or seeded `PantryItem(user_id=...)`/`User(...)` now needs `household_id` and a `Household`; assert the digest still renders the household's due items.

- [ ] **Step 6: Run**

Run: `uv run pytest tests/test_household_service.py tests/test_v1_5_rendering_scheduler.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/household_service.py app/scheduler.py tests/test_household_service.py tests/test_v1_5_rendering_scheduler.py
git commit -m "feat(household): provision solo household; digest resolves household"
```

---

## Task 12: Wire `app/bot.py` — provision on auth, pass `household_id` to services

**Files:**
- Modify: `app/bot.py` (`AuthDecision`, `authorize_and_get_user`, `_guard`, every handler that forwards an id, the callback handlers)
- Test: `tests/test_cook_v35_bot_commands.py`, `tests/test_cook_v35_bot_callbacks.py`, `tests/test_cook_bot.py`, `tests/test_v1_5_bot.py`, `tests/test_renderer_commands.py`

- [ ] **Step 1: Edit `authorize_and_get_user` to provision a household.** Add a `household` field to `AuthDecision` and ensure the bootstrap owner gets one. Replace the creation/return tail:

```python
@dataclass
class AuthDecision:
    allowed: bool
    user: Optional[User]
    created: bool
    reason: str
    household: Optional[Household] = None


def authorize_and_get_user(session, *, allowed_user_id, telegram_user_id,
                           chat_id, chat_type) -> AuthDecision:
    if telegram_user_id != allowed_user_id:
        return AuthDecision(False, None, False, "not authorized")
    if chat_type != "private":
        return AuthDecision(False, None, False, "this bot only works in private chat")

    existing = session.get(User, telegram_user_id)
    if existing is not None:
        household = session.get(Household, existing.household_id)
        if household is None:  # defensive: legacy row without a household
            household = provision_solo_household(session, existing)
        return AuthDecision(True, existing, False, "ok", household=household)

    user = User(telegram_id=telegram_user_id, chat_id=chat_id, household_id=0,
                tz=DEFAULT_TZ, digest_hour=DEFAULT_DIGEST_HOUR,
                llm_provider=DEFAULT_LLM_PROVIDER,
                created_at=datetime.now(timezone.utc))
    session.add(user)
    session.commit()
    session.refresh(user)
    household = provision_solo_household(session, user)
    return AuthDecision(True, user, True, "created", household=household)
```

Add imports at the top of `app/bot.py`:

```python
from app.models import CookSession, Household, PantryItem, User
from app.household_service import provision_solo_household
```

- [ ] **Step 2: Make `_guard` return the household too.** Change its return type to `tuple[User, Household] | None` and the handlers accordingly — OR keep returning `User` and have handlers read `user.household_id`. **Chosen approach (smaller diff): keep `_guard` returning `User`; handlers pass `user.household_id`.** No change to `_guard` body beyond `authorize_and_get_user` already provisioning, so `user.household_id` is always valid.

- [ ] **Step 3: Replace every `user.telegram_id` passed as an *ownership* id with `user.household_id`** in the handlers. Concretely, in `app/bot.py` change these call sites:
  - `handle_list`: `list_active(session, household_id=user.household_id, f=..., today=...)`
  - `handle_add`: `propose_add(session, llm=..., household_id=user.household_id, ...)`
  - `_terminal_cmd`: `fn(session, household_id=user.household_id, item_id=item_id, today=...)`
  - `handle_snooze`: `snooze_item(session, household_id=user.household_id, ...)`
  - `handle_correct`: item lookup `item.household_id != user.household_id`; `propose_correct(session, llm=..., household_id=user.household_id, item=item, ...)`; `create_pending(session, household_id=user.household_id, ...)`
  - `handle_stats`: `compute_stats(session, household_id=user.household_id, now=...)`
  - `handle_shopping`: `list_pending(session, household_id=user.household_id)`
  - `handle_favorites`: `list_saved(session, household_id=user.household_id)`
  - `handle_cook`: `create_cook_session(session, household_id=user.household_id, chat_id=..., now=...)`
  - `handle_prefs`: load `household = session.get(Household, user.household_id)`, call `render_profile(profile_from_household(household))` and `update_profile_from_sentence(session, llm=..., household=household, sentence=...)`
  - `handle_photo`: `ingest_photo(session, selected_llm, household_id=user.household_id, ...)`; the `refine_user_id` capture becomes `refine_household_id = user.household_id` and is passed to `run_receipt_refine` (see Task 13 note).
  - `handle_callback` / `handle_cook_callback` / `run_cook_and_render`: replace `user.telegram_id` ownership args with `user.household_id` in `load_cook_session`, `set_feedback` (unchanged — operates on the loaded cook), `save_candidate`, `active_pantry_names`, `add_missing`, `check_off`, `load_saved`, `recook_shopping_list`, `list_digest_due`, `mark_eaten/tossed`, `snooze_item`, `undo_receipt`, `undo_add`. For `run_cook_and_render`, thread a `household_id` param alongside `user_id` (it loads `user` then uses `user.household_id`).

Update `profile_service` import:

```python
from app.profile_service import profile_from_household, update_profile_from_sentence
```

- [ ] **Step 4: Update the bot tests.** In `tests/test_cook_v35_bot_commands.py`, `tests/test_cook_v35_bot_callbacks.py`, `tests/test_cook_bot.py`, `tests/test_v1_5_bot.py`, `tests/test_renderer_commands.py`: every `_engine_with_user()` helper must also create a `Household` and set `User.household_id`; seeded `PantryItem`/`CookSession`/`ShoppingList`/`SavedRecipe` use `household_id`; `load_cook_session`/`create_cook_session` etc. use `household_id`. Update the shared helper:

```python
def _engine_with_user():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        hh = Household(name="h", created_at=datetime.now(timezone.utc))
        db.add(hh); db.commit(); db.refresh(hh)
        db.add(User(telegram_id=1, chat_id=1, household_id=hh.id,
                    created_at=datetime.now(timezone.utc)))
        db.commit()
    return engine
```

- [ ] **Step 5: Run the bot tests**

Run: `uv run pytest tests/test_cook_v35_bot_commands.py tests/test_cook_v35_bot_callbacks.py tests/test_cook_bot.py tests/test_v1_5_bot.py tests/test_renderer_commands.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/bot.py tests/
git commit -m "feat(bot): provision household on auth; pass household_id to services"
```

---

## Task 13: Re-key `app/refine_service.py` + add the household-isolation test

**Files:**
- Modify: `app/refine_service.py` (`run_receipt_refine` — the `user_id` ownership arg)
- Test: `tests/test_v2_websearch.py` (refine), `tests/test_household_isolation.py` *(new)*

- [ ] **Step 1: Edit `app/refine_service.py`.** Where `run_receipt_refine(..., user_id=..., receipt_id=..., ...)` filters/updates `PantryItem`/`Receipt` by owner, rename the parameter to `household_id` and change the filters to `.household_id == household_id`. (Grep `app/refine_service.py` for the 5 `user_id` hits and swap each.)

- [ ] **Step 2: Write the isolation test**

```python
# tests/test_household_isolation.py
from datetime import date, datetime, timezone
from sqlmodel import Session, SQLModel, create_engine
from app.models import Household, PantryItem
from app.pantry_service import list_active, ListFilter


def _engine_two_households():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        for name in ("A", "B"):
            db.add(Household(name=name, created_at=datetime.now(timezone.utc)))
        db.commit()
    return engine


def test_list_active_is_isolated_per_household():
    engine = _engine_two_households()
    today = date(2026, 5, 30)
    with Session(engine) as db:
        db.add(PantryItem(household_id=1, raw_name="A-milk", normalized_name="milk",
                          category="dairy", qty=1.0, purchased_on=today,
                          shelf_life_days=5, shelf_life_source="llm",
                          ingest_shelf_life_source="llm", expires_on=date(2026, 6, 5),
                          status="active", created_via="receipt",
                          created_at=datetime.now(timezone.utc)))
        db.add(PantryItem(household_id=2, raw_name="B-eggs", normalized_name="eggs",
                          category="dairy", qty=1.0, purchased_on=today,
                          shelf_life_days=5, shelf_life_source="llm",
                          ingest_shelf_life_source="llm", expires_on=date(2026, 6, 5),
                          status="active", created_via="receipt",
                          created_at=datetime.now(timezone.utc)))
        db.commit()
        a = list_active(db, household_id=1, f=ListFilter.default(), today=today)
        b = list_active(db, household_id=2, f=ListFilter.default(), today=today)
        assert [i.raw_name for i in a] == ["A-milk"]
        assert [i.raw_name for i in b] == ["B-eggs"]
```

- [ ] **Step 3: Run**

Run: `uv run pytest tests/test_household_isolation.py tests/test_v2_websearch.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add app/refine_service.py tests/test_household_isolation.py tests/test_v2_websearch.py
git commit -m "refactor(refine): key by household_id; add isolation test"
```

---

## Task 14: Full-suite green + lint sweep

**Files:** none (verification task)

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest -q`
Expected: all PASS. If a stray `user_id=` ownership call remains, the failure names the file/line — fix it (swap to `household_id`) and re-run.

- [ ] **Step 2: Grep for leftover ownership references**

Run: `uv run python -c "import app.bot, app.scheduler, app.ingest_service"` then
Run (search): look for any remaining `\.user_id` on the re-keyed models in `app/` (via the Grep tool, pattern `user_id`, path `app/`). Every remaining hit must be either (a) inside `User`-related code that legitimately means the person, or (b) a bug to fix. Document the survivors in the commit message.

- [ ] **Step 3: Lint**

Run: `ruff check app tests`
Expected: clean (fix unused imports left by the re-key, e.g. a now-unused `profile_from_user`).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(v4): foundation suite green + lint cleanup"
```

---

## Self-review (completed)

- **Spec coverage:** §4 tenancy model → Tasks 1-2; §5.1 Household + §5.4 User trim → Task 1; §5.5 re-key + indexes → Task 2; §5.6 migration → Task 3; §8 digest household resolution → Task 11; profile-on-household (§4) → Task 10; service re-key (§9) → Tasks 4-9, 13; bot provisioning (§6.1 bootstrap owner) → Task 12. **Deferred to Plans 2-3 (not this plan):** `HouseholdInvite`, `GroupBinding`, `/invite`, `/join`, `/bind`, group routing, multi-member auth resolution, photo-in-group rejection (spec §5.2-5.3, §6.2-6.3, §7) — called out in "Plan decomposition" above.
- **Placeholder scan:** none — every code step shows the code; the mechanical re-key tasks enumerate exact functions and the exact column/kwarg swap.
- **Type consistency:** the ownership parameter is named `household_id: int` everywhere; cache helpers keep positional `(session, household_id, normalized_name)`; `provision_solo_household(session, user) -> Household`; `profile_from_household` / `apply_profile_to_household` / `update_profile_from_sentence(..., household=...)` are consistent across Tasks 10 and 12.

> **Known risk flagged for the implementer:** Task 3's migration is the highest-risk step (SQLite `batch_alter_table` rebuilds + the `shelflifecache` PK swap). The pre-migration backup in `bin/run.py` protects production; the backfill test in Task 3 proves correctness before any other task depends on it. Run Task 3 before the service tasks.
