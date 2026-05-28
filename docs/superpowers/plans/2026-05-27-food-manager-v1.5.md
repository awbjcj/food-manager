# Food Manager v1.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `/correct` and `/add` into LLM-driven natural-language commands gated by an `[Apply]/[Cancel]` confirmation backed by a new `PendingCorrection` SQLite table — without disturbing v1's photo-ingest or digest paths.

**Architecture:** One new SQLModel table (`PendingCorrection`) persisted via Alembic `0002`. One new `TextLLMClient` Protocol with `parse_correct()` and `parse_add()` returning Pydantic-validated diffs and proposals; vision client untouched. Two new service modules (`correction_service`, `pending_service`) own the propose/apply lifecycle. `bot.py` rewires `/correct` and `/add` to the propose pipeline and adds `[Apply]/[Cancel]` callback handlers. `pantry_service` mutators gain a single in-transaction call to `pending_service.expire_for_item` so any item mutation kills siblings. APScheduler gains a 5-minute UTC sweep job for the 10-minute TTL.

**Tech Stack:** Python 3.12 / uv · aiogram 3.x · SQLModel + SQLAlchemy 2.x · Alembic · APScheduler 3.x · Anthropic Python SDK (Haiku 4.5 for text + Sonnet 4.6 for vision) · pydantic-settings · pytest + pytest-asyncio + freezegun.

**Reference spec:** `docs/superpowers/specs/2026-05-27-food-manager-v1.5-design.md` — every locked decision, table column, command behaviour, and threshold below comes from there.

**Conventions used throughout this plan:**
- File paths are repo-relative from `D:/Fun/food-manager/`.
- Tests use `pytest`; run with `uv run pytest <path>`.
- Commits use Conventional Commits (`feat:`, `test:`, `chore:`, `refactor:`) with the trailing `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` line.
- TDD discipline is mandatory: a failing test exists and is observed to fail before any implementation step.
- After each task completes, run the full test suite (`uv run pytest`) to verify no regressions before committing.
- "Karpathy mode": no abstractions beyond what the spec mandates. No speculative configurability. Touch only what the task requires; do not refactor adjacent v1 code.

---

## Phase 1 — Data model and migration

Goal: `PendingCorrection` exists in code and in a clean Alembic upgrade. v1 tables untouched.

### Task 1.1 — Add `PendingCorrection` SQLModel + literal aliases

**Files:**
- Modify: `app/models.py`
- Create: `tests/test_models_pending.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_pending.py
from datetime import date, datetime, timezone, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import (
    CacheAction,
    PantryItem,
    PendingActionType,
    PendingCorrection,
    PendingStatus,
    Receipt,
    User,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def test_insert_pending_correct_with_item_id(session):
    item = PantryItem(
        user_id=1, raw_name="Milk", normalized_name="milk",
        category="dairy", qty=1.0, unit="gal",
        purchased_on=date(2026, 5, 26),
        shelf_life_days=7, shelf_life_source="llm",
        ingest_shelf_life_source="llm",
        expires_on=date(2026, 6, 2),
        status="active", created_via="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(item)
    session.commit()

    now = datetime.now(timezone.utc)
    pending = PendingCorrection(
        user_id=1,
        action_type="correct",
        item_id=item.id,
        proposed_json='{"kind":"correct","diff":{}}',
        original_snapshot_json='{"id":1}',
        llm_cost_micros_usd=42,
        chat_id=1,
        message_id=None,
        status="pending",
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    session.add(pending)
    session.commit()

    rows = session.exec(select(PendingCorrection)).all()
    assert len(rows) == 1
    assert rows[0].action_type == "correct"
    assert rows[0].status == "pending"
    assert rows[0].item_id == item.id


def test_insert_pending_add_with_null_item_id(session):
    now = datetime.now(timezone.utc)
    pending = PendingCorrection(
        user_id=1,
        action_type="add",
        item_id=None,
        proposed_json='{"kind":"add","item":{"name":"Oat Milk"}}',
        chat_id=1,
        status="pending",
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    session.add(pending)
    session.commit()
    assert pending.id is not None


def test_literal_aliases_exist():
    assert PendingActionType is not None
    assert PendingStatus is not None
    assert CacheAction is not None
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_models_pending.py -v`
Expected: ImportError on `PendingCorrection` / aliases.

- [ ] **Step 3: Modify `app/models.py` — append the new aliases and table after `ShelfLifeCache`**

```python
# Add to the literal-aliases block near the top
PendingActionType = Literal["correct", "add"]
PendingStatus = Literal["pending", "applied", "cancelled", "expired", "stale"]
CacheAction = Literal["move", "add_new", "leave"]
```

And, at the bottom of the file:

```python
class PendingCorrection(SQLModel, table=True):
    __table_args__ = (
        Index(
            "ix_pending_user_status_created",
            "user_id", "status", "created_at",
        ),
        Index("ix_pending_item", "item_id"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.telegram_id", index=True)
    action_type: str
    item_id: Optional[int] = Field(default=None, foreign_key="pantryitem.id")
    proposed_json: str
    original_snapshot_json: Optional[str] = None
    llm_cost_micros_usd: Optional[int] = None
    chat_id: int
    message_id: Optional[int] = None
    status: str = "pending"
    created_at: datetime
    expires_at: datetime
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_models_pending.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run full suite to confirm no regression**

Run: `uv run pytest`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add app/models.py tests/test_models_pending.py
git commit -m "feat(models): add PendingCorrection table for /correct + /add diffs

Per v1.5 spec §5: nullable item_id (NULL for /add); JSON-as-text
payloads; user_id/status/created_at compound index for sweep
queries; item_id index for mutation-based expiry lookups.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 1.2 — Alembic `0002_pending_correction` migration

**Files:**
- Create: `migrations/versions/0002_pending_correction.py`
- Create: `tests/test_migration_0002.py`

- [ ] **Step 1: Write the failing migration test**

```python
# tests/test_migration_0002.py
import sqlite3
import subprocess


def test_alembic_upgrade_creates_pending_correction(tmp_path, monkeypatch):
    db = tmp_path / "m2.db"
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
    assert "pendingcorrection" in tables

    indexes = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='pendingcorrection'"
    ).fetchall()}
    assert "ix_pending_user_status_created" in indexes
    assert "ix_pending_item" in indexes
    con.close()
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_migration_0002.py -v`
Expected: assertion failure on missing table.

- [ ] **Step 3: Generate the migration file**

```bash
uv run alembic revision --autogenerate -m "pending_correction"
```

Rename the generated file to `migrations/versions/0002_pending_correction.py`. Edit the top so `revision = "0002_pending_correction"` and `down_revision = "0001_initial"`. Verify the autogenerated `upgrade()` body creates the `pendingcorrection` table and the two named indexes; hand-edit if names differ.

- [ ] **Step 4: Run test, expect PASS**

Run: `uv run pytest tests/test_migration_0002.py -v`
Expected: 1 passed.

- [ ] **Step 5: Run full suite**

Run: `uv run pytest`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add migrations/versions/0002_pending_correction.py tests/test_migration_0002.py
git commit -m "feat(db): alembic 0002 — pendingcorrection table + indexes

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 1.3 — Add `ANTHROPIC_TEXT_MODEL` setting

**Files:**
- Modify: `app/settings.py`
- Modify: `.env.example`
- Modify: `tests/test_settings.py`

- [ ] **Step 1: Extend the existing settings test**

Append to `tests/test_settings.py`:

```python
def test_anthropic_text_model_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_TEXT_MODEL", raising=False)
    from app.settings import Settings
    s = Settings()
    assert s.anthropic_text_model == "claude-haiku-4-5-20251001"


def test_anthropic_text_model_override(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_TEXT_MODEL", "claude-foo")
    from app.settings import Settings
    s = Settings()
    assert s.anthropic_text_model == "claude-foo"
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_settings.py -v -k anthropic_text_model`
Expected: AttributeError on `anthropic_text_model`.

- [ ] **Step 3: Add the field to `app/settings.py`**

Append to the `Settings` class:

```python
    anthropic_text_model: str = Field(
        default="claude-haiku-4-5-20251001",
        alias="ANTHROPIC_TEXT_MODEL",
    )
```

- [ ] **Step 4: Update `.env.example`**

Add one line at the end:

```
ANTHROPIC_TEXT_MODEL=claude-haiku-4-5-20251001
```

- [ ] **Step 5: Run tests, expect PASS**

Run: `uv run pytest tests/test_settings.py -v`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add app/settings.py .env.example tests/test_settings.py
git commit -m "feat(settings): add ANTHROPIC_TEXT_MODEL with Haiku default

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 2 — Text LLM client

Goal: a `TextLLMClient` Protocol with `parse_correct()` and `parse_add()`, a real `AnthropicTextLLMClient`, and a fake for tests — all matching v1's `LLMClient` pattern (bounded retry, JSON validation, cost in micros USD).

### Task 2.1 — Pydantic models: `CorrectionDiff` and `ProposedAddItem`

**Files:**
- Modify: `app/llm.py` (append models below existing ones)
- Create: `tests/test_text_llm_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_text_llm_models.py
from datetime import date
import pytest

from app.llm import CorrectionDiff, ProposedAddItem


def test_correction_diff_all_none_is_null_diff():
    d = CorrectionDiff(cache_action="leave", rationale="", confidence=0.9)
    assert d.name is None and d.category is None
    assert d.expires_on is None and d.shelf_life_days is None


def test_correction_diff_shelf_life_range_validated():
    with pytest.raises(Exception):
        CorrectionDiff(shelf_life_days=0, cache_action="leave",
                       rationale="x", confidence=0.5)
    with pytest.raises(Exception):
        CorrectionDiff(shelf_life_days=731, cache_action="leave",
                       rationale="x", confidence=0.5)


def test_correction_diff_confidence_range_validated():
    with pytest.raises(Exception):
        CorrectionDiff(cache_action="leave", rationale="x", confidence=1.5)


def test_proposed_add_item_min_fields():
    item = ProposedAddItem(
        name="Oat Milk", explicit_user_expiry=False, confidence=0.8
    )
    assert item.qty == 1.0
    assert item.category is None
    assert item.shelf_life_days is None
    assert item.expires_on is None
    assert item.estimated_shelf_life_days is None


def test_proposed_add_item_with_explicit_expiry():
    item = ProposedAddItem(
        name="Oat Milk", category="beverage",
        explicit_user_expiry=True, shelf_life_days=10,
        expires_on=date(2026, 6, 6),
        estimated_shelf_life_days=10, confidence=0.88,
    )
    assert item.shelf_life_days == 10
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_text_llm_models.py -v`
Expected: ImportError on `CorrectionDiff` / `ProposedAddItem`.

- [ ] **Step 3: Append to `app/llm.py` (just below the existing `LLMResult` class)**

```python
CacheAction = Literal["move", "add_new", "leave"]


class CorrectionDiff(BaseModel):
    name: Optional[str] = None
    category: Optional[Category] = None
    expires_on: Optional[date] = None
    shelf_life_days: Optional[int] = Field(default=None, ge=1, le=730)
    cache_action: CacheAction = "leave"
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class ProposedAddItem(BaseModel):
    name: str
    category: Optional[Category] = None
    qty: float = 1.0
    unit: Optional[str] = None
    explicit_user_expiry: bool
    shelf_life_days: Optional[int] = Field(default=None, ge=1, le=730)
    expires_on: Optional[date] = None
    estimated_shelf_life_days: Optional[int] = Field(default=None, ge=1, le=730)
    confidence: float = Field(ge=0.0, le=1.0)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_text_llm_models.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/llm.py tests/test_text_llm_models.py
git commit -m "feat(llm): add CorrectionDiff + ProposedAddItem schemas

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2.2 — `TextLLMClient` Protocol + `FakeTextLLMClient`

**Files:**
- Modify: `app/llm.py`
- Modify: `tests/fakes.py`
- Create: `tests/test_text_llm_protocol.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_text_llm_protocol.py
import asyncio
from datetime import date

from app.llm import CorrectionDiff, ProposedAddItem, TextLLMClient
from tests.fakes import FakeTextLLMClient


def test_fake_text_llm_returns_canned_correction():
    canned = CorrectionDiff(
        name="Heavy Cream", category="dairy", expires_on=date(2026, 6, 5),
        shelf_life_days=10, cache_action="move",
        rationale="user clarified item identity", confidence=0.92,
    )
    fake: TextLLMClient = FakeTextLLMClient(canned_correct=(canned, 350))
    diff, cost = asyncio.run(
        fake.parse_correct(
            item_snapshot={"id": 42}, cache_snapshot=None,
            user_text="actually heavy cream", today=date(2026, 5, 27),
        )
    )
    assert diff.name == "Heavy Cream"
    assert cost == 350


def test_fake_text_llm_returns_canned_add():
    items = [ProposedAddItem(
        name="Oat Milk", category="beverage",
        explicit_user_expiry=True, shelf_life_days=10,
        expires_on=date(2026, 6, 6),
        estimated_shelf_life_days=10, confidence=0.88,
    )]
    fake: TextLLMClient = FakeTextLLMClient(canned_add=(items, 280))
    result, cost = asyncio.run(
        fake.parse_add(user_text="oat milk 10d", today=date(2026, 5, 27),
                       tz="America/Detroit")
    )
    assert len(result) == 1 and result[0].name == "Oat Milk"
    assert cost == 280
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_text_llm_protocol.py -v`
Expected: ImportError on `TextLLMClient` / `FakeTextLLMClient`.

- [ ] **Step 3: Append Protocol to `app/llm.py`**

```python
class TextLLMClient(Protocol):
    async def parse_correct(
        self,
        *,
        item_snapshot: dict[str, Any],
        cache_snapshot: Optional[dict[str, Any]],
        user_text: str,
        today: date,
    ) -> tuple[CorrectionDiff, Optional[int]]: ...

    async def parse_add(
        self,
        *,
        user_text: str,
        today: date,
        tz: str,
    ) -> tuple[list[ProposedAddItem], Optional[int]]: ...
```

- [ ] **Step 4: Append `FakeTextLLMClient` to `tests/fakes.py`**

```python
from dataclasses import field
from typing import Any, Optional
from app.llm import CorrectionDiff, ProposedAddItem


@dataclass
class FakeTextLLMClient:
    canned_correct: Optional[tuple[CorrectionDiff, Optional[int]]] = None
    canned_add: Optional[tuple[list[ProposedAddItem], Optional[int]]] = None
    canned_correct_sequence: Optional[list[tuple[CorrectionDiff, Optional[int]]]] = None
    canned_add_sequence: Optional[list[tuple[list[ProposedAddItem], Optional[int]]]] = None
    raise_n_times: int = 0
    _raises: int = 0
    correct_calls: list[dict[str, Any]] = field(default_factory=list)
    add_calls: list[dict[str, Any]] = field(default_factory=list)

    async def parse_correct(self, *, item_snapshot, cache_snapshot, user_text, today):
        self.correct_calls.append(
            {"item_snapshot": item_snapshot, "cache_snapshot": cache_snapshot,
             "user_text": user_text, "today": today}
        )
        if self._raises < self.raise_n_times:
            self._raises += 1
            raise RuntimeError("simulated text-llm failure")
        if self.canned_correct_sequence:
            return self.canned_correct_sequence.pop(0)
        assert self.canned_correct is not None
        return self.canned_correct

    async def parse_add(self, *, user_text, today, tz):
        self.add_calls.append({"user_text": user_text, "today": today, "tz": tz})
        if self._raises < self.raise_n_times:
            self._raises += 1
            raise RuntimeError("simulated text-llm failure")
        if self.canned_add_sequence:
            return self.canned_add_sequence.pop(0)
        assert self.canned_add is not None
        return self.canned_add
```

- [ ] **Step 5: Run tests, expect PASS**

Run: `uv run pytest tests/test_text_llm_protocol.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add app/llm.py tests/fakes.py tests/test_text_llm_protocol.py
git commit -m "feat(llm): TextLLMClient Protocol + FakeTextLLMClient

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2.3 — `AnthropicTextLLMClient` (real implementation)

**Files:**
- Modify: `app/llm.py`
- Create: `tests/test_anthropic_text_llm.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_anthropic_text_llm.py
import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.llm import AnthropicTextLLMClient


class _Stub:
    def __init__(self, text: str, in_tok: int = 200, out_tok: int = 60):
        self.content = [MagicMock(type="text", text=text)]
        self.usage = MagicMock(input_tokens=in_tok, output_tokens=out_tok)


@pytest.mark.asyncio
async def test_parse_correct_happy_path():
    raw = json.dumps({
        "name": "Heavy Cream", "category": "dairy",
        "expires_on": "2026-06-05", "shelf_life_days": 10,
        "cache_action": "move",
        "rationale": "user clarified identity", "confidence": 0.92,
    })
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(return_value=_Stub(raw))
    client = AnthropicTextLLMClient(sdk=sdk, model="claude-haiku-4-5-20251001")

    diff, cost = await client.parse_correct(
        item_snapshot={"id": 42, "raw_name": "Milk"},
        cache_snapshot=None,
        user_text="actually heavy cream",
        today=date(2026, 5, 27),
    )
    assert diff.name == "Heavy Cream"
    assert diff.cache_action == "move"
    assert cost is not None and cost > 0


@pytest.mark.asyncio
async def test_parse_correct_retries_malformed_json():
    bad = "not json"
    good = json.dumps({
        "cache_action": "leave", "rationale": "ok", "confidence": 0.5,
    })
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(side_effect=[_Stub(bad), _Stub(good)])
    client = AnthropicTextLLMClient(sdk=sdk, model="claude-haiku-4-5-20251001")
    diff, _ = await client.parse_correct(
        item_snapshot={}, cache_snapshot=None,
        user_text="x", today=date(2026, 5, 27),
    )
    assert diff.name is None
    assert sdk.messages.create.call_count == 2


@pytest.mark.asyncio
async def test_parse_correct_gives_up_after_one_correction():
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(side_effect=[_Stub("garbage"), _Stub("still garbage")])
    client = AnthropicTextLLMClient(sdk=sdk, model="claude-haiku-4-5-20251001")
    with pytest.raises(Exception):
        await client.parse_correct(
            item_snapshot={}, cache_snapshot=None,
            user_text="x", today=date(2026, 5, 27),
        )
    assert sdk.messages.create.call_count == 2


@pytest.mark.asyncio
async def test_parse_correct_retries_transport_twice():
    good = json.dumps({"cache_action": "leave", "rationale": "ok", "confidence": 0.5})
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(side_effect=[
        RuntimeError("temp"), RuntimeError("temp"), _Stub(good),
    ])
    sleep = AsyncMock()
    client = AnthropicTextLLMClient(
        sdk=sdk, model="claude-haiku-4-5-20251001", sleep=sleep,
    )
    await client.parse_correct(
        item_snapshot={}, cache_snapshot=None,
        user_text="x", today=date(2026, 5, 27),
    )
    assert sdk.messages.create.call_count == 3
    assert sleep.await_count == 2


@pytest.mark.asyncio
async def test_parse_add_happy_path_list():
    raw = json.dumps([
        {"name": "Oat Milk", "category": "beverage", "qty": 0.5, "unit": "gal",
         "explicit_user_expiry": True, "shelf_life_days": 10,
         "expires_on": "2026-06-06", "estimated_shelf_life_days": 10,
         "confidence": 0.88},
        {"name": "Fresh Basil", "category": "produce", "qty": 1.0, "unit": None,
         "explicit_user_expiry": False, "shelf_life_days": None,
         "expires_on": None, "estimated_shelf_life_days": 7, "confidence": 0.7},
    ])
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(return_value=_Stub(raw))
    client = AnthropicTextLLMClient(sdk=sdk, model="claude-haiku-4-5-20251001")
    items, cost = await client.parse_add(
        user_text="oat milk 10d, basil", today=date(2026, 5, 27),
        tz="America/Detroit",
    )
    assert len(items) == 2
    assert items[0].explicit_user_expiry is True
    assert items[1].estimated_shelf_life_days == 7
    assert cost is not None and cost > 0


@pytest.mark.asyncio
async def test_unknown_model_cost_is_none():
    raw = json.dumps({"cache_action": "leave", "rationale": "ok", "confidence": 0.5})
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(return_value=_Stub(raw))
    client = AnthropicTextLLMClient(sdk=sdk, model="future-haiku")
    _, cost = await client.parse_correct(
        item_snapshot={}, cache_snapshot=None,
        user_text="x", today=date(2026, 5, 27),
    )
    assert cost is None
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_anthropic_text_llm.py -v`
Expected: ImportError on `AnthropicTextLLMClient`.

- [ ] **Step 3: Add `AnthropicTextLLMClient` to `app/llm.py`**

Append after the existing `AnthropicLLMClient`:

```python
import json

CORRECTION_SYSTEM_PROMPT = """You parse a user-supplied correction for a single pantry item.
Return ONLY valid JSON matching the CorrectionDiff schema. No prose.

You receive (in the user message):
  - item_snapshot: {id, raw_name, normalized_name, category, qty, unit,
                    purchased_on, shelf_life_days, expires_on, status}
  - cache_snapshot: null OR {normalized_name, days, category,
                              source, confidence, learned_at}
  - today: YYYY-MM-DD in the user's local timezone
  - user_text: free-form correction message

Rules:
  - Set ONLY the fields the user actually wants to change. Leave the
    others null.
  - Never set both shelf_life_days and expires_on; prefer the one
    the user stated more explicitly.
  - cache_action="move" when the user clarifies a misidentified item
    (e.g. "this is actually heavy cream"). "add_new" when both names
    are legitimate but distinct. "leave" when only date/category/days
    changes, or when uncertain.
  - rationale: one short clause explaining the change.
  - confidence: 0.0–1.0 of your parse, not of the food domain.

TODO(user): tune the move-vs-add_new boundary and category mapping to
the user's typical correction patterns.
"""

ADD_SYSTEM_PROMPT = """You parse a user-supplied "add to pantry" message into one or
more discrete items. Return ONLY valid JSON: a list of items matching
the ProposedAddItem schema.

For each item:
  - name: clean, expanded ("Oat Milk", not "OM 1/2 gal").
  - category: one of dairy|produce|meat|seafood|bakery|pantry|frozen|
              beverage|other. Null if unsure.
  - qty / unit: as the user stated; default qty=1.0; unit may be null.
  - explicit_user_expiry: true if the user explicitly stated a shelf
                          life ("keeps 10 days", "expires June 5"),
                          else false.
  - shelf_life_days: integer 1..730 ONLY if explicit_user_expiry is
                     true. Null otherwise.
  - expires_on: YYYY-MM-DD if the user stated an absolute date.
  - estimated_shelf_life_days: conservative food-domain estimate
                under normal storage, even when the user did not
                state expiry. Null only if genuinely unknown.
  - confidence: 0.0–1.0 of your parse.

Comma, semicolon, "and", and newline are valid item separators. Do
NOT invent items the user didn't mention.

TODO(user): tune separator handling and the "do not invent" guidance
against the user's typical /add patterns.
"""


_PRICE_MICROS_PER_TOKEN_BY_MODEL["claude-haiku-4-5-20251001"] = {
    "input": 1, "output": 5,
}


def _extract_json_text(message) -> str:
    chunks: list[str] = []
    for block in message.content:
        if getattr(block, "type", None) == "text":
            chunks.append(block.text)
    if not chunks:
        raise ValueError("no text block in text-LLM response")
    text = "\n".join(chunks).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


class AnthropicTextLLMClient:
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._sdk = sdk
        self._model = model
        self._sleep = sleep

    async def _create_message(self, system: str, user_content):
        for attempt in range(3):
            try:
                return await self._sdk.messages.create(
                    model=self._model, max_tokens=1024,
                    system=system,
                    messages=[{"role": "user", "content": user_content}],
                )
            except Exception as e:
                if attempt == 2:
                    log.warning(
                        "text_llm_transport_failed_final",
                        extra={"error_class": type(e).__name__},
                    )
                    raise
                log.warning(
                    "text_llm_transport_failed_retrying",
                    extra={"error_class": type(e).__name__},
                )
                await self._sleep(2 ** attempt)

    async def _call_with_schema(self, system: str, user_text: str, parse_fn):
        user_content = [{"type": "text", "text": user_text}]
        total_cost = 0
        unknown_cost = False
        for attempt in (0, 1):
            msg = await self._create_message(system, user_content)
            c = _cost_micros(msg, self._model)
            if c is not None:
                total_cost += c
            else:
                unknown_cost = True
            text = _extract_json_text(msg)
            try:
                return parse_fn(text), (None if unknown_cost else total_cost)
            except Exception as e:
                if attempt == 1:
                    log.warning(
                        "text_llm_schema_failed_final",
                        extra={"error_class": type(e).__name__},
                    )
                    raise
                user_content = [
                    *user_content,
                    {"type": "text",
                     "text": (f"Your last response did not match the schema "
                              f"(error: {type(e).__name__}). "
                              f"Return ONLY valid JSON matching the schema.")},
                ]
        raise RuntimeError("unreachable")

    async def parse_correct(self, *, item_snapshot, cache_snapshot,
                            user_text, today):
        user_msg = json.dumps({
            "item_snapshot": item_snapshot,
            "cache_snapshot": cache_snapshot,
            "today": today.isoformat(),
            "user_text": user_text,
        })

        def _parse(text: str) -> CorrectionDiff:
            return CorrectionDiff.model_validate(json.loads(text))

        return await self._call_with_schema(
            CORRECTION_SYSTEM_PROMPT, user_msg, _parse,
        )

    async def parse_add(self, *, user_text, today, tz):
        user_msg = json.dumps({
            "today": today.isoformat(),
            "tz": tz,
            "user_text": user_text,
        })

        def _parse(text: str) -> list[ProposedAddItem]:
            data = json.loads(text)
            if not isinstance(data, list):
                raise ValueError("parse_add expected a JSON array")
            return [ProposedAddItem.model_validate(d) for d in data]

        return await self._call_with_schema(
            ADD_SYSTEM_PROMPT, user_msg, _parse,
        )
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_anthropic_text_llm.py -v`
Expected: 6 passed.

- [ ] **Step 5: Run full suite**

Run: `uv run pytest`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add app/llm.py tests/test_anthropic_text_llm.py
git commit -m "feat(llm): AnthropicTextLLMClient with bounded retry + schema validation

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 3 — Pending-correction service

Goal: a small focused module that owns CRUD on `PendingCorrection` plus the two expiry helpers (`expire_for_item`, `sweep_expired`). One responsibility per function; called by `correction_service`, `pantry_service`, and the scheduler.

### Task 3.1 — `create_pending` + `load_pending`

**Files:**
- Create: `app/pending_service.py`
- Create: `tests/test_pending_service_crud.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pending_service_crud.py
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import PendingCorrection, User
from app.pending_service import (
    PENDING_TTL_MINUTES,
    create_pending,
    load_pending,
    set_message_id,
)


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def test_create_pending_writes_expected_columns(session):
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    pending = create_pending(
        session,
        user_id=1,
        action_type="correct",
        item_id=42,
        proposed_json='{"kind":"correct"}',
        snapshot_json='{"id":42}',
        cost_micros_usd=350,
        chat_id=1,
        now=now,
    )
    assert pending.id is not None
    assert pending.status == "pending"
    assert pending.expires_at == now + timedelta(minutes=PENDING_TTL_MINUTES)
    assert pending.message_id is None


def test_load_pending_returns_row_when_owner_matches(session):
    now = datetime.now(timezone.utc)
    p = create_pending(
        session, user_id=1, action_type="add", item_id=None,
        proposed_json="{}", snapshot_json=None,
        cost_micros_usd=None, chat_id=1, now=now,
    )
    loaded = load_pending(session, user_id=1, pending_id=p.id)
    assert loaded is not None and loaded.id == p.id


def test_load_pending_returns_none_for_wrong_user(session):
    session.add(User(telegram_id=2, chat_id=2, created_at=datetime.now(timezone.utc)))
    session.commit()
    p = create_pending(
        session, user_id=1, action_type="add", item_id=None,
        proposed_json="{}", snapshot_json=None,
        cost_micros_usd=None, chat_id=1, now=datetime.now(timezone.utc),
    )
    assert load_pending(session, user_id=2, pending_id=p.id) is None


def test_set_message_id_persists(session):
    p = create_pending(
        session, user_id=1, action_type="correct", item_id=1,
        proposed_json="{}", snapshot_json=None,
        cost_micros_usd=None, chat_id=1, now=datetime.now(timezone.utc),
    )
    set_message_id(session, pending=p, message_id=987)
    session.refresh(p)
    assert p.message_id == 987
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_pending_service_crud.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `app/pending_service.py`**

```python
# app/pending_service.py
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session

from app.models import PendingCorrection

PENDING_TTL_MINUTES = 10


def create_pending(
    session: Session,
    *,
    user_id: int,
    action_type: str,
    item_id: Optional[int],
    proposed_json: str,
    snapshot_json: Optional[str],
    cost_micros_usd: Optional[int],
    chat_id: int,
    now: datetime,
) -> PendingCorrection:
    pending = PendingCorrection(
        user_id=user_id,
        action_type=action_type,
        item_id=item_id,
        proposed_json=proposed_json,
        original_snapshot_json=snapshot_json,
        llm_cost_micros_usd=cost_micros_usd,
        chat_id=chat_id,
        message_id=None,
        status="pending",
        created_at=now,
        expires_at=now + timedelta(minutes=PENDING_TTL_MINUTES),
    )
    session.add(pending)
    session.commit()
    session.refresh(pending)
    return pending


def load_pending(
    session: Session, *, user_id: int, pending_id: int
) -> Optional[PendingCorrection]:
    pending = session.get(PendingCorrection, pending_id)
    if pending is None or pending.user_id != user_id:
        return None
    return pending


def set_message_id(
    session: Session, *, pending: PendingCorrection, message_id: int
) -> None:
    pending.message_id = message_id
    session.add(pending)
    session.commit()
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_pending_service_crud.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/pending_service.py tests/test_pending_service_crud.py
git commit -m "feat(pending): create_pending + load_pending + set_message_id

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3.2 — `mark_applied` / `mark_cancelled` terminal transitions

**Files:**
- Modify: `app/pending_service.py`
- Create: `tests/test_pending_service_terminal.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pending_service_terminal.py
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import PendingCorrection, User
from app.pending_service import (
    PendingNotPending,
    create_pending,
    mark_applied,
    mark_cancelled,
)


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def _make(session):
    return create_pending(
        session, user_id=1, action_type="correct", item_id=1,
        proposed_json="{}", snapshot_json=None,
        cost_micros_usd=None, chat_id=1, now=datetime.now(timezone.utc),
    )


def test_mark_applied_transitions_status(session):
    p = _make(session)
    mark_applied(session, pending=p)
    session.refresh(p)
    assert p.status == "applied"


def test_mark_cancelled_transitions_status(session):
    p = _make(session)
    mark_cancelled(session, pending=p)
    session.refresh(p)
    assert p.status == "cancelled"


def test_mark_applied_raises_if_not_pending(session):
    p = _make(session)
    mark_applied(session, pending=p)
    with pytest.raises(PendingNotPending):
        mark_applied(session, pending=p)


def test_mark_cancelled_raises_if_not_pending(session):
    p = _make(session)
    mark_cancelled(session, pending=p)
    with pytest.raises(PendingNotPending):
        mark_cancelled(session, pending=p)
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_pending_service_terminal.py -v`
Expected: ImportError.

- [ ] **Step 3: Append to `app/pending_service.py`**

```python
class PendingNotPending(Exception):
    """Raised when a terminal transition is attempted on a non-pending row."""


def _set_terminal(
    session: Session, *, pending: PendingCorrection, status: str
) -> None:
    if pending.status != "pending":
        raise PendingNotPending(pending.status)
    pending.status = status
    session.add(pending)
    session.flush()


def mark_applied(session: Session, *, pending: PendingCorrection) -> None:
    _set_terminal(session, pending=pending, status="applied")


def mark_cancelled(session: Session, *, pending: PendingCorrection) -> None:
    _set_terminal(session, pending=pending, status="cancelled")
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_pending_service_terminal.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/pending_service.py tests/test_pending_service_terminal.py
git commit -m "feat(pending): terminal transitions mark_applied + mark_cancelled

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3.3 — `expire_for_item` (mutation-based)

**Files:**
- Modify: `app/pending_service.py`
- Create: `tests/test_pending_service_expire_for_item.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pending_service_expire_for_item.py
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import PendingCorrection, User
from app.pending_service import create_pending, expire_for_item


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        s.add(User(telegram_id=2, chat_id=2, created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def _make(session, user_id, item_id):
    return create_pending(
        session, user_id=user_id, action_type="correct", item_id=item_id,
        proposed_json="{}", snapshot_json=None,
        cost_micros_usd=None, chat_id=user_id, now=datetime.now(timezone.utc),
    )


def test_expire_for_item_marks_only_matching_user_and_item(session):
    a = _make(session, 1, 42)
    b = _make(session, 1, 42)
    c = _make(session, 1, 43)            # other item, same user
    d = _make(session, 2, 42)            # same item id but other user

    expire_for_item(session, user_id=1, item_id=42)

    for p in (a, b, c, d):
        session.refresh(p)
    assert a.status == "stale"
    assert b.status == "stale"
    assert c.status == "pending"
    assert d.status == "pending"


def test_expire_for_item_skips_already_terminal_rows(session):
    p = _make(session, 1, 42)
    p.status = "applied"
    session.add(p); session.commit()

    expire_for_item(session, user_id=1, item_id=42)
    session.refresh(p)
    assert p.status == "applied"


def test_expire_for_item_excludes_named_pending(session):
    a = _make(session, 1, 42)
    b = _make(session, 1, 42)
    expire_for_item(session, user_id=1, item_id=42, exclude_pending_id=a.id)
    session.commit()
    session.refresh(a); session.refresh(b)
    assert a.status == "pending"
    assert b.status == "stale"
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_pending_service_expire_for_item.py -v`
Expected: ImportError.

- [ ] **Step 3: Append to `app/pending_service.py`**

```python
from sqlmodel import select


def expire_for_item(
    session: Session, *, user_id: int, item_id: int,
    exclude_pending_id: Optional[int] = None,
) -> int:
    """Mark every still-pending row for this (user, item) as 'stale'.

    Returns the number of rows transitioned. Caller owns the surrounding
    transaction and commit so item mutation + pending expiry are atomic.
    """
    query = select(PendingCorrection).where(
        PendingCorrection.user_id == user_id,
        PendingCorrection.item_id == item_id,
        PendingCorrection.status == "pending",
    )
    if exclude_pending_id is not None:
        query = query.where(PendingCorrection.id != exclude_pending_id)
    rows = session.exec(query).all()
    for row in rows:
        row.status = "stale"
        session.add(row)
    if rows:
        session.flush()
    return len(rows)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_pending_service_expire_for_item.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/pending_service.py tests/test_pending_service_expire_for_item.py
git commit -m "feat(pending): expire_for_item marks sibling pendings stale

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3.4 — `sweep_expired` (TTL-based)

**Files:**
- Modify: `app/pending_service.py`
- Create: `tests/test_pending_service_sweep.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pending_service_sweep.py
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import PendingCorrection, User
from app.pending_service import create_pending, sweep_expired


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def test_sweep_marks_expired_rows(session):
    base = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    old = create_pending(
        session, user_id=1, action_type="correct", item_id=1,
        proposed_json="{}", snapshot_json=None,
        cost_micros_usd=None, chat_id=1, now=base - timedelta(minutes=20),
    )
    fresh = create_pending(
        session, user_id=1, action_type="correct", item_id=2,
        proposed_json="{}", snapshot_json=None,
        cost_micros_usd=None, chat_id=1, now=base,
    )

    swept = sweep_expired(session, now=base)
    assert swept == 1
    session.refresh(old); session.refresh(fresh)
    assert old.status == "expired"
    assert fresh.status == "pending"


def test_sweep_skips_terminal_rows(session):
    base = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    p = create_pending(
        session, user_id=1, action_type="correct", item_id=1,
        proposed_json="{}", snapshot_json=None,
        cost_micros_usd=None, chat_id=1, now=base - timedelta(minutes=20),
    )
    p.status = "cancelled"
    session.add(p); session.commit()

    swept = sweep_expired(session, now=base)
    assert swept == 0
    session.refresh(p)
    assert p.status == "cancelled"
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_pending_service_sweep.py -v`
Expected: ImportError.

- [ ] **Step 3: Append to `app/pending_service.py`**

```python
def sweep_expired(session: Session, *, now: datetime) -> int:
    """Mark all pending rows whose expires_at < now as 'expired'.

    Returns the number of rows transitioned.
    """
    rows = session.exec(
        select(PendingCorrection).where(
            PendingCorrection.status == "pending",
            PendingCorrection.expires_at < now,
        )
    ).all()
    for row in rows:
        row.status = "expired"
        session.add(row)
    if rows:
        session.commit()
    return len(rows)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_pending_service_sweep.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/pending_service.py tests/test_pending_service_sweep.py
git commit -m "feat(pending): sweep_expired for TTL-based cleanup

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 4 — Correction service: propose + apply

Goal: `app/correction_service.py` orchestrates the LLM call, the service-layer post-processing rules (back-compute, range checks, fallback chain), and the eventual mutations on Apply.

### Task 4.1 — `Proposal` dataclass + JSON payload helpers

**Files:**
- Create: `app/correction_service.py`
- Create: `tests/test_correction_payload.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_correction_payload.py
import json
from datetime import date

from app.correction_service import (
    CorrectPayload,
    AddPayload,
    correct_payload_to_json,
    add_payload_to_json,
    correct_payload_from_json,
    add_payload_from_json,
)


def test_correct_payload_round_trip():
    p = CorrectPayload(
        diff={
            "name": {"old": "Milk", "new": "Heavy Cream"},
            "category": None,
            "expires_on": {"old": "2026-06-02", "new": "2026-06-05"},
            "shelf_life_days": {"old": 7, "new": 10},
        },
        cache_action="move",
        rationale="user clarified identity",
        confidence=0.92,
        back_computed_days=True,
    )
    blob = correct_payload_to_json(p)
    decoded = correct_payload_from_json(blob)
    assert decoded.diff["name"]["new"] == "Heavy Cream"
    assert decoded.cache_action == "move"
    assert decoded.back_computed_days is True
    assert decoded.confidence == 0.92


def test_add_payload_round_trip():
    p = AddPayload(
        name="Oat Milk", category="beverage", qty=0.5, unit="gal",
        shelf_life_days=10, expires_on=date(2026, 6, 6),
        shelf_life_source="user_correction",
        ingest_shelf_life_source="manual_user_hint",
        explicit_user_expiry=True,
        estimated_shelf_life_days=10,
        confidence=0.88,
    )
    blob = add_payload_to_json(p)
    raw = json.loads(blob)
    assert raw["kind"] == "add"
    assert raw["item"]["name"] == "Oat Milk"
    decoded = add_payload_from_json(blob)
    assert decoded.expires_on == date(2026, 6, 6)
    assert decoded.shelf_life_source == "user_correction"
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_correction_payload.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `app/correction_service.py`** (Pydantic-backed payload classes)

```python
# app/correction_service.py
from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class CorrectPayload(BaseModel):
    kind: Literal["correct"] = "correct"
    diff: dict[str, Optional[dict[str, Any]]]   # field -> {"old":..., "new":...} or None
    cache_action: Literal["move", "add_new", "leave"]
    rationale: str
    confidence: float
    back_computed_days: bool = False


class AddPayload(BaseModel):
    kind: Literal["add"] = "add"
    name: str
    category: Optional[str] = None
    qty: float = 1.0
    unit: Optional[str] = None
    shelf_life_days: int = Field(ge=1, le=730)
    expires_on: date
    shelf_life_source: Literal[
        "user_correction", "cache", "manual_fallback", "llm",
    ]
    ingest_shelf_life_source: Literal[
        "manual_user_hint", "cache", "manual_fallback", "llm",
    ]
    explicit_user_expiry: bool
    estimated_shelf_life_days: Optional[int] = None
    confidence: float


def correct_payload_to_json(p: CorrectPayload) -> str:
    return p.model_dump_json()


def add_payload_to_json(p: AddPayload) -> str:
    return json.dumps({
        "kind": "add",
        "item": p.model_dump(mode="json", exclude={"kind"}),
    })


def correct_payload_from_json(blob: str) -> CorrectPayload:
    return CorrectPayload.model_validate_json(blob)


def add_payload_from_json(blob: str) -> AddPayload:
    data = json.loads(blob)
    if data.get("kind") == "add" and "item" in data:
        return AddPayload.model_validate(data["item"])
    return AddPayload.model_validate(data)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_correction_payload.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/correction_service.py tests/test_correction_payload.py
git commit -m "feat(correction): pydantic payload classes for /correct and /add

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4.2 — `propose_correct`: LLM call + post-processing

**Files:**
- Modify: `app/correction_service.py`
- Create: `tests/test_propose_correct.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_propose_correct.py
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.cache import put_cached
from app.correction_service import (
    NullDiff,
    ProposeCorrectError,
    item_snapshot_to_json,
    propose_correct,
)
from app.llm import CorrectionDiff
from app.models import PantryItem, User
from tests.fakes import FakeTextLLMClient


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def _item(session) -> PantryItem:
    item = PantryItem(
        user_id=1, raw_name="Milk", normalized_name="milk",
        category="dairy", qty=1.0, unit="gal",
        purchased_on=date(2026, 5, 26),
        shelf_life_days=7, shelf_life_source="llm",
        ingest_shelf_life_source="llm",
        expires_on=date(2026, 6, 2),
        status="active", created_via="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(item); session.commit(); session.refresh(item)
    return item


async def test_null_diff_raises(session):
    item = _item(session)
    fake = FakeTextLLMClient(canned_correct=(CorrectionDiff(
        cache_action="leave", rationale="no change", confidence=0.5,
    ), 100))
    with pytest.raises(NullDiff):
        await propose_correct(
            session, llm=fake, user_id=1, item=item,
            user_text="looks fine", today=date(2026, 5, 27),
        )


async def test_only_expires_on_back_computes_days(session):
    item = _item(session)
    fake = FakeTextLLMClient(canned_correct=(CorrectionDiff(
        expires_on=date(2026, 6, 5),
        cache_action="leave", rationale="date update", confidence=0.8,
    ), 100))
    payload, cost = await propose_correct(
        session, llm=fake, user_id=1, item=item,
        user_text="expires June 5", today=date(2026, 5, 27),
    )
    assert payload.back_computed_days is True
    # purchased_on=May 26, expires_on=June 5 → 10 days
    assert payload.diff["shelf_life_days"]["new"] == 10
    assert payload.diff["expires_on"]["new"] == "2026-06-05"
    assert cost == 100


async def test_only_shelf_life_days_forward_computes_expires(session):
    item = _item(session)
    fake = FakeTextLLMClient(canned_correct=(CorrectionDiff(
        shelf_life_days=10,
        cache_action="leave", rationale="days update", confidence=0.8,
    ), 100))
    payload, _ = await propose_correct(
        session, llm=fake, user_id=1, item=item,
        user_text="10 days", today=date(2026, 5, 27),
    )
    assert payload.diff["expires_on"]["new"] == "2026-06-05"
    assert payload.back_computed_days is False


async def test_both_set_prefers_shelf_life_days(session):
    item = _item(session)
    fake = FakeTextLLMClient(canned_correct=(CorrectionDiff(
        expires_on=date(2099, 1, 1), shelf_life_days=10,
        cache_action="leave", rationale="x", confidence=0.8,
    ), 100))
    payload, _ = await propose_correct(
        session, llm=fake, user_id=1, item=item,
        user_text="10 days", today=date(2026, 5, 27),
    )
    assert payload.diff["expires_on"]["new"] == "2026-06-05"
    assert payload.diff["shelf_life_days"]["new"] == 10


async def test_expires_out_of_range_rejected(session):
    item = _item(session)
    fake = FakeTextLLMClient(canned_correct=(CorrectionDiff(
        expires_on=date(2026, 5, 25),  # before purchased_on
        cache_action="leave", rationale="x", confidence=0.8,
    ), 100))
    with pytest.raises(ProposeCorrectError):
        await propose_correct(
            session, llm=fake, user_id=1, item=item,
            user_text="x", today=date(2026, 5, 27),
        )


async def test_name_and_category_changes_diff(session):
    item = _item(session)
    put_cached(session, 1, "milk", days=7, category="dairy",
               confidence=0.9, source="llm")
    fake = FakeTextLLMClient(canned_correct=(CorrectionDiff(
        name="Heavy Cream", category="dairy",
        cache_action="move", rationale="user clarified", confidence=0.9,
    ), 200))
    payload, _ = await propose_correct(
        session, llm=fake, user_id=1, item=item,
        user_text="actually heavy cream", today=date(2026, 5, 27),
    )
    assert payload.diff["name"]["old"] == "Milk"
    assert payload.diff["name"]["new"] == "Heavy Cream"
    assert payload.cache_action == "move"


def test_item_snapshot_to_json_contains_original_item(session):
    item = _item(session)
    raw = item_snapshot_to_json(item)
    assert '"raw_name": "Milk"' in raw
    assert '"shelf_life_days": 7' in raw
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_propose_correct.py -v`
Expected: ImportError on `propose_correct`.

- [ ] **Step 3: Append to `app/correction_service.py`**

```python
from datetime import timedelta
from typing import Optional

from sqlmodel import Session

from app.cache import get_cached
from app.llm import CorrectionDiff, TextLLMClient
from app.models import PantryItem


class NullDiff(Exception):
    """Raised when the LLM-parsed diff has no field changes."""


class ProposeCorrectError(Exception):
    """Raised when the LLM-parsed diff cannot be validated (range, etc)."""


def _snapshot(item: PantryItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "raw_name": item.raw_name,
        "normalized_name": item.normalized_name,
        "category": item.category,
        "qty": item.qty,
        "unit": item.unit,
        "purchased_on": item.purchased_on.isoformat(),
        "shelf_life_days": item.shelf_life_days,
        "expires_on": item.expires_on.isoformat(),
        "status": item.status,
    }


def item_snapshot_to_json(item: PantryItem) -> str:
    return json.dumps(_snapshot(item), sort_keys=True)


def _cache_snapshot(row) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return {
        "normalized_name": row.normalized_name,
        "days": row.days,
        "category": row.category,
        "source": row.source,
        "confidence": row.confidence,
        "learned_at": row.learned_at.isoformat(),
    }


async def propose_correct(
    session: Session,
    *,
    llm: TextLLMClient,
    user_id: int,
    item: PantryItem,
    user_text: str,
    today: date,
) -> tuple[CorrectPayload, Optional[int]]:
    cache_row = get_cached(session, user_id, item.normalized_name)
    diff, cost = await llm.parse_correct(
        item_snapshot=_snapshot(item),
        cache_snapshot=_cache_snapshot(cache_row),
        user_text=user_text,
        today=today,
    )

    # Null-diff detection
    if (diff.name is None and diff.category is None
            and diff.expires_on is None and diff.shelf_life_days is None):
        raise NullDiff()

    new_expires = diff.expires_on
    new_days = diff.shelf_life_days
    back_computed = False

    if new_expires is not None and new_days is not None:
        # Safety net: prefer days, drop expires_on
        new_expires = item.purchased_on + timedelta(days=new_days)
    elif new_expires is not None:
        delta = (new_expires - item.purchased_on).days
        if delta < 1 or delta > 730:
            raise ProposeCorrectError(
                "expires_on out of range for purchase date"
            )
        new_days = delta
        back_computed = True
    elif new_days is not None:
        new_expires = item.purchased_on + timedelta(days=new_days)

    payload_diff: dict[str, Optional[dict[str, Any]]] = {
        "name": (
            {"old": item.raw_name, "new": diff.name}
            if diff.name is not None and diff.name != item.raw_name
            else None
        ),
        "category": (
            {"old": item.category, "new": diff.category}
            if diff.category is not None and diff.category != item.category
            else None
        ),
        "expires_on": (
            {"old": item.expires_on.isoformat(),
             "new": new_expires.isoformat()}
            if new_expires is not None and new_expires != item.expires_on
            else None
        ),
        "shelf_life_days": (
            {"old": item.shelf_life_days, "new": new_days}
            if new_days is not None and new_days != item.shelf_life_days
            else None
        ),
    }

    if all(v is None for v in payload_diff.values()):
        raise NullDiff()

    payload = CorrectPayload(
        diff=payload_diff,
        cache_action=diff.cache_action,
        rationale=diff.rationale,
        confidence=diff.confidence,
        back_computed_days=back_computed,
    )
    return payload, cost
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_propose_correct.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add app/correction_service.py tests/test_propose_correct.py
git commit -m "feat(correction): propose_correct with back-compute and range checks

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4.3 — `apply_correct`: field updates + cache_action

**Files:**
- Modify: `app/cache.py`
- Modify: `app/correction_service.py`
- Create: `tests/test_apply_correct.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_apply_correct.py
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.cache import get_cached, put_cached, write_user_correction
from app.correction_service import (
    CorrectPayload,
    apply_correct,
)
from app.models import PantryItem, User


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def _item(session, name="Milk", norm="milk") -> PantryItem:
    item = PantryItem(
        user_id=1, raw_name=name, normalized_name=norm,
        category="dairy", qty=1.0, unit="gal",
        purchased_on=date(2026, 5, 26),
        shelf_life_days=7, shelf_life_source="llm",
        ingest_shelf_life_source="llm",
        expires_on=date(2026, 6, 2),
        status="active", created_via="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(item); session.commit(); session.refresh(item)
    return item


def test_apply_cache_action_move_deletes_old_and_writes_new(session):
    item = _item(session)
    put_cached(session, 1, "milk", days=7, category="dairy",
               confidence=0.9, source="llm")
    payload = CorrectPayload(
        diff={
            "name": {"old": "Milk", "new": "Heavy Cream"},
            "category": None,
            "expires_on": {"old": "2026-06-02", "new": "2026-06-05"},
            "shelf_life_days": {"old": 7, "new": 10},
        },
        cache_action="move", rationale="x", confidence=0.9,
    )
    apply_correct(session, user_id=1, item=item, payload=payload)
    session.refresh(item)
    assert item.raw_name == "Heavy Cream"
    assert item.normalized_name == "heavy cream"
    assert item.shelf_life_days == 10
    assert item.expires_on == date(2026, 6, 5)
    assert item.shelf_life_source == "user_correction"
    assert get_cached(session, 1, "milk") is None
    new_row = get_cached(session, 1, "heavy cream")
    assert new_row is not None and new_row.days == 10
    assert new_row.source == "user_correction"


def test_apply_cache_action_add_new_keeps_old(session):
    item = _item(session)
    put_cached(session, 1, "milk", days=7, category="dairy",
               confidence=0.9, source="llm")
    payload = CorrectPayload(
        diff={
            "name": {"old": "Milk", "new": "Whole Milk"},
            "category": None,
            "expires_on": None,
            "shelf_life_days": None,
        },
        cache_action="add_new", rationale="x", confidence=0.9,
    )
    apply_correct(session, user_id=1, item=item, payload=payload)
    assert get_cached(session, 1, "milk") is not None
    assert get_cached(session, 1, "whole milk") is not None


def test_apply_category_only_leave_updates_current_cache_row(session):
    item = _item(session)
    put_cached(session, 1, "milk", days=7, category="dairy",
               confidence=0.9, source="llm")
    payload = CorrectPayload(
        diff={
            "name": None,
            "category": {"old": "dairy", "new": "beverage"},
            "expires_on": None,
            "shelf_life_days": None,
        },
        cache_action="leave", rationale="x", confidence=0.9,
    )
    apply_correct(session, user_id=1, item=item, payload=payload)
    session.refresh(item)
    assert item.category == "beverage"
    row = get_cached(session, 1, "milk")
    assert row is not None
    assert row.source == "user_correction"
    assert row.days == 7
    assert row.category == "beverage"
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_apply_correct.py -v`
Expected: ImportError on `apply_correct`.

- [ ] **Step 3: First make `write_user_correction` transaction-safe**

Modify `app/cache.py` so callers can opt out of an internal commit:

```python
def write_user_correction(
    session: Session,
    user_id: int,
    normalized_name: str,
    *,
    days: int,
    category: Optional[str] = None,
    commit: bool = True,
) -> ShelfLifeCache:
    # ... existing create/update logic unchanged ...
    if commit:
        session.commit()
        session.refresh(row)
    else:
        session.flush()
    return row
```

Keep the default `commit=True` so existing v1 callers continue to work
until they are explicitly moved into a broader transaction.

- [ ] **Step 4: Append to `app/correction_service.py`**

```python
from app.cache import write_user_correction
from app.models import ShelfLifeCache
from app.normalization import normalize


def apply_correct(
    session: Session,
    *,
    user_id: int,
    item: PantryItem,
    payload: CorrectPayload,
) -> None:
    """Mutate `item` per payload.diff, then apply `payload.cache_action`.

    Caller (the apply callback) is responsible for the surrounding
    `pending_service.expire_for_item(...)` call AND for marking the
    pending row as 'applied'. This function only handles the data
    mutation.
    """
    old_normalized = item.normalized_name

    name_change = payload.diff.get("name")
    category_change = payload.diff.get("category")
    expires_change = payload.diff.get("expires_on")
    days_change = payload.diff.get("shelf_life_days")

    if name_change is not None:
        item.raw_name = name_change["new"]
        item.normalized_name = normalize(name_change["new"])
    if category_change is not None:
        item.category = category_change["new"]
    if days_change is not None:
        item.shelf_life_days = days_change["new"]
        item.shelf_life_source = "user_correction"
    if expires_change is not None:
        item.expires_on = date.fromisoformat(expires_change["new"])
    session.add(item)

    new_normalized = item.normalized_name
    new_days = item.shelf_life_days
    new_category = item.category

    if payload.cache_action == "move":
        old_row = session.get(ShelfLifeCache, (user_id, old_normalized))
        if old_row is not None and old_normalized != new_normalized:
            session.delete(old_row)
        write_user_correction(
            session, user_id, new_normalized,
            days=new_days, category=new_category, commit=False,
        )
    elif payload.cache_action == "add_new":
        write_user_correction(
            session, user_id, new_normalized,
            days=new_days, category=new_category, commit=False,
        )
    elif payload.cache_action == "leave":
        if days_change is not None or (
            category_change is not None and name_change is None
        ):
            # Even on "leave", a shelf-life correction is authoritative
            # for the current normalized name. Category-only corrections
            # also teach the current cache row; name+category with
            # cache_action="leave" leaves cache identity untouched.
            write_user_correction(
                session, user_id, new_normalized,
                days=new_days, category=new_category, commit=False,
            )
    session.flush()
```

- [ ] **Step 5: Run tests, expect PASS**

Run: `uv run pytest tests/test_apply_correct.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add app/cache.py app/correction_service.py tests/test_apply_correct.py
git commit -m "feat(correction): apply_correct with move/add_new/leave cache actions

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4.4 — `propose_add`: LLM call + fallback chain

**Files:**
- Modify: `app/correction_service.py`
- Create: `tests/test_propose_add.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_propose_add.py
from datetime import date, datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.cache import put_cached
from app.correction_service import propose_add
from app.llm import ProposedAddItem
from app.models import User
from tests.fakes import FakeTextLLMClient


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


async def test_explicit_user_expiry_marks_user_correction(session):
    items = [ProposedAddItem(
        name="Oat Milk", category="beverage",
        explicit_user_expiry=True, shelf_life_days=10,
        estimated_shelf_life_days=10, confidence=0.88,
    )]
    fake = FakeTextLLMClient(canned_add=(items, 200))
    rows, total_cost = await propose_add(
        session, llm=fake, user_id=1, user_text="oat milk 10d",
        today=date(2026, 5, 27), tz="America/Detroit",
    )
    assert len(rows) == 1
    p = rows[0]
    assert p.payload.shelf_life_source == "user_correction"
    assert p.payload.ingest_shelf_life_source == "manual_user_hint"
    assert p.payload.shelf_life_days == 10
    assert p.payload.expires_on == date(2026, 6, 6)
    assert p.cost_share == 200


async def test_implicit_expiry_uses_cache_when_present(session):
    put_cached(session, 1, "oat milk", days=14, category="beverage",
               confidence=0.9, source="user_correction")
    items = [ProposedAddItem(
        name="Oat Milk", category="beverage",
        explicit_user_expiry=False,
        estimated_shelf_life_days=8, confidence=0.7,
    )]
    fake = FakeTextLLMClient(canned_add=(items, 100))
    rows, _ = await propose_add(
        session, llm=fake, user_id=1, user_text="oat milk",
        today=date(2026, 5, 27), tz="America/Detroit",
    )
    p = rows[0].payload
    assert p.shelf_life_source == "cache"
    assert p.ingest_shelf_life_source == "cache"
    assert p.shelf_life_days == 14


async def test_implicit_expiry_uses_defaults_when_cache_misses(session):
    items = [ProposedAddItem(
        name="Whole Milk", explicit_user_expiry=False,
        estimated_shelf_life_days=10, confidence=0.7,
    )]
    fake = FakeTextLLMClient(canned_add=(items, 100))
    rows, _ = await propose_add(
        session, llm=fake, user_id=1, user_text="whole milk",
        today=date(2026, 5, 27), tz="America/Detroit",
    )
    p = rows[0].payload
    # `whole milk` is in shelf_life_defaults._EXACT as 7 days, dairy
    assert p.shelf_life_source == "manual_fallback"
    assert p.ingest_shelf_life_source == "manual_fallback"
    assert p.shelf_life_days == 7


async def test_implicit_expiry_falls_back_to_llm_estimate(session):
    items = [ProposedAddItem(
        name="Star Fruit", explicit_user_expiry=False,
        estimated_shelf_life_days=6, confidence=0.7,
    )]
    fake = FakeTextLLMClient(canned_add=(items, 100))
    rows, _ = await propose_add(
        session, llm=fake, user_id=1, user_text="star fruit",
        today=date(2026, 5, 27), tz="America/Detroit",
    )
    p = rows[0].payload
    assert p.shelf_life_source == "llm"
    assert p.ingest_shelf_life_source == "llm"
    assert p.shelf_life_days == 6


async def test_all_miss_uses_conservative_three_day_fallback(session):
    items = [ProposedAddItem(
        name="Mystery Item", explicit_user_expiry=False,
        estimated_shelf_life_days=None, confidence=0.5,
    )]
    fake = FakeTextLLMClient(canned_add=(items, 100))
    rows, _ = await propose_add(
        session, llm=fake, user_id=1, user_text="mystery item",
        today=date(2026, 5, 27), tz="America/Detroit",
    )
    p = rows[0].payload
    assert p.shelf_life_days == 3
    assert p.ingest_shelf_life_source == "manual_fallback"


async def test_cost_split_across_multiple_items(session):
    items = [
        ProposedAddItem(name="A", explicit_user_expiry=False,
                        estimated_shelf_life_days=7, confidence=0.8),
        ProposedAddItem(name="B", explicit_user_expiry=False,
                        estimated_shelf_life_days=7, confidence=0.8),
        ProposedAddItem(name="C", explicit_user_expiry=False,
                        estimated_shelf_life_days=7, confidence=0.8),
    ]
    fake = FakeTextLLMClient(canned_add=(items, 301))
    rows, total = await propose_add(
        session, llm=fake, user_id=1, user_text="a, b, c",
        today=date(2026, 5, 27), tz="America/Detroit",
    )
    # 301 // 3 = 100 each; remainder 1 added to first row
    assert [r.cost_share for r in rows] == [101, 100, 100]
    assert total == 301
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_propose_add.py -v`
Expected: ImportError on `propose_add`.

- [ ] **Step 3: Append to `app/correction_service.py`**

```python
from dataclasses import dataclass
from datetime import timedelta

from app.shelf_life_defaults import lookup_default


CONSERVATIVE_FALLBACK_DAYS = 3


@dataclass
class AddProposal:
    payload: AddPayload
    cost_share: Optional[int]


async def propose_add(
    session: Session,
    *,
    llm: TextLLMClient,
    user_id: int,
    user_text: str,
    today: date,
    tz: str,
) -> tuple[list[AddProposal], Optional[int]]:
    items, total_cost = await llm.parse_add(
        user_text=user_text, today=today, tz=tz,
    )
    if not items:
        return [], total_cost

    # Split cost: floor share + remainder to the first row.
    if total_cost is None:
        shares: list[Optional[int]] = [None] * len(items)
    else:
        base = total_cost // len(items)
        remainder = total_cost - base * len(items)
        shares = [base + remainder if i == 0 else base for i in range(len(items))]

    proposals: list[AddProposal] = []
    for parsed, cost_share in zip(items, shares):
        normalized = normalize(parsed.name)

        if parsed.explicit_user_expiry:
            # User stated expiry: trust them; treat as user_correction.
            if parsed.shelf_life_days is not None:
                days = parsed.shelf_life_days
            elif parsed.expires_on is not None:
                delta = (parsed.expires_on - today).days
                days = max(1, min(730, delta))
            else:
                days = CONSERVATIVE_FALLBACK_DAYS
            expires_on = today + timedelta(days=days)
            shelf_life_source = "user_correction"
            ingest_source = "manual_user_hint"
            category = parsed.category
        else:
            cached = get_cached(session, user_id, normalized)
            if cached is not None:
                days = cached.days
                shelf_life_source = "cache"
                ingest_source = "cache"
                category = parsed.category or cached.category
            else:
                default = lookup_default(normalized)
                if default is not None:
                    days = default.days
                    shelf_life_source = "manual_fallback"
                    ingest_source = "manual_fallback"
                    category = parsed.category or default.category
                elif parsed.estimated_shelf_life_days is not None:
                    days = parsed.estimated_shelf_life_days
                    shelf_life_source = "llm"
                    ingest_source = "llm"
                    category = parsed.category
                else:
                    days = CONSERVATIVE_FALLBACK_DAYS
                    shelf_life_source = "manual_fallback"
                    ingest_source = "manual_fallback"
                    category = parsed.category
            expires_on = today + timedelta(days=days)

        proposals.append(AddProposal(
            payload=AddPayload(
                name=parsed.name,
                category=category,
                qty=parsed.qty,
                unit=parsed.unit,
                shelf_life_days=days,
                expires_on=expires_on,
                shelf_life_source=shelf_life_source,
                ingest_shelf_life_source=ingest_source,
                explicit_user_expiry=parsed.explicit_user_expiry,
                estimated_shelf_life_days=parsed.estimated_shelf_life_days,
                confidence=parsed.confidence,
            ),
            cost_share=cost_share,
        ))
    return proposals, total_cost
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_propose_add.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add app/correction_service.py tests/test_propose_add.py
git commit -m "feat(correction): propose_add with cache→defaults→llm fallback chain

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4.5 — `apply_add`: insert PantryItem + optional cache write

**Files:**
- Modify: `app/correction_service.py`
- Create: `tests/test_apply_add.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_apply_add.py
from datetime import date, datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.cache import get_cached
from app.correction_service import AddPayload, apply_add
from app.models import PantryItem, User


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def test_apply_add_user_correction_writes_cache(session):
    payload = AddPayload(
        name="Oat Milk", category="beverage", qty=0.5, unit="gal",
        shelf_life_days=10, expires_on=date(2026, 6, 6),
        shelf_life_source="user_correction",
        ingest_shelf_life_source="manual_user_hint",
        explicit_user_expiry=True,
        estimated_shelf_life_days=10, confidence=0.88,
    )
    item_id = apply_add(session, user_id=1, payload=payload,
                        today=date(2026, 5, 27))
    item = session.get(PantryItem, item_id)
    assert item is not None
    assert item.normalized_name == "oat milk"
    assert item.shelf_life_source == "user_correction"
    assert item.ingest_shelf_life_source == "manual_user_hint"
    assert item.purchased_on == date(2026, 5, 27)
    cache = get_cached(session, 1, "oat milk")
    assert cache is not None and cache.source == "user_correction"


def test_apply_add_non_user_correction_does_not_write_cache(session):
    payload = AddPayload(
        name="Star Fruit", category="produce", qty=1.0, unit=None,
        shelf_life_days=6, expires_on=date(2026, 6, 2),
        shelf_life_source="llm",
        ingest_shelf_life_source="llm",
        explicit_user_expiry=False,
        estimated_shelf_life_days=6, confidence=0.7,
    )
    item_id = apply_add(session, user_id=1, payload=payload,
                        today=date(2026, 5, 27))
    item = session.get(PantryItem, item_id)
    assert item.shelf_life_source == "llm"
    assert get_cached(session, 1, "star fruit") is None
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_apply_add.py -v`
Expected: ImportError.

- [ ] **Step 3: Append to `app/correction_service.py`**

```python
from datetime import datetime as _dt
from datetime import timezone as _tz


def apply_add(
    session: Session,
    *,
    user_id: int,
    payload: AddPayload,
    today: date,
) -> int:
    normalized = normalize(payload.name)
    item = PantryItem(
        user_id=user_id,
        raw_name=payload.name,
        normalized_name=normalized,
        category=payload.category,
        qty=payload.qty,
        unit=payload.unit,
        purchased_on=today,
        shelf_life_days=payload.shelf_life_days,
        shelf_life_source=payload.shelf_life_source,
        ingest_shelf_life_source=payload.ingest_shelf_life_source,
        expires_on=payload.expires_on,
        status="active",
        created_via="manual",
        source_receipt_id=None,
        created_at=_dt.now(_tz.utc),
    )
    session.add(item)
    session.flush()
    assert item.id is not None

    if payload.shelf_life_source == "user_correction":
        write_user_correction(
            session, user_id, normalized,
            days=payload.shelf_life_days,
            category=payload.category,
            commit=False,
        )
    session.flush()
    return item.id
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_apply_add.py -v`
Expected: 2 passed.

- [ ] **Step 5: Run full suite**

Run: `uv run pytest`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add app/correction_service.py tests/test_apply_add.py
git commit -m "feat(correction): apply_add inserts PantryItem + optional cache write

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 5 — Wire mutation-based expiry into `pantry_service`

Goal: every existing mutator in `app/pantry_service.py` calls `pending_service.expire_for_item(session, user_id, item_id)` BEFORE its own commit, so any state change to an item invalidates pending corrections for that item.

### Task 5.1 — Mutators call `expire_for_item`

**Files:**
- Modify: `app/pantry_service.py`
- Create: `tests/test_pantry_service_pending_expiry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pantry_service_pending_expiry.py
from datetime import date, datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import PantryItem, PendingCorrection, User
from app.pantry_service import (
    correct_item,
    mark_eaten,
    mark_removed,
    mark_tossed,
    snooze_item,
)
from app.pending_service import create_pending


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def _item_with_pending(session) -> tuple[PantryItem, PendingCorrection]:
    item = PantryItem(
        user_id=1, raw_name="Milk", normalized_name="milk",
        category="dairy", qty=1.0, unit="gal",
        purchased_on=date(2026, 5, 26),
        shelf_life_days=7, shelf_life_source="llm",
        ingest_shelf_life_source="llm",
        expires_on=date(2026, 6, 2),
        status="active", created_via="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(item); session.commit(); session.refresh(item)
    pending = create_pending(
        session, user_id=1, action_type="correct", item_id=item.id,
        proposed_json="{}", snapshot_json=None,
        cost_micros_usd=None, chat_id=1, now=datetime.now(timezone.utc),
    )
    return item, pending


def test_mark_eaten_kills_pending(session):
    item, pending = _item_with_pending(session)
    mark_eaten(session, user_id=1, item_id=item.id, today=date(2026, 5, 27))
    session.refresh(pending)
    assert pending.status == "stale"


def test_mark_tossed_kills_pending(session):
    item, pending = _item_with_pending(session)
    mark_tossed(session, user_id=1, item_id=item.id, today=date(2026, 5, 27))
    session.refresh(pending)
    assert pending.status == "stale"


def test_mark_removed_kills_pending(session):
    item, pending = _item_with_pending(session)
    mark_removed(session, user_id=1, item_id=item.id, today=date(2026, 5, 27))
    session.refresh(pending)
    assert pending.status == "stale"


def test_snooze_item_kills_pending(session):
    item, pending = _item_with_pending(session)
    snooze_item(session, user_id=1, item_id=item.id,
                today=date(2026, 5, 27), days=2)
    session.refresh(pending)
    assert pending.status == "stale"


def test_correct_item_kills_pending(session):
    item, pending = _item_with_pending(session)
    correct_item(session, user_id=1, item_id=item.id,
                 days=10, today=date(2026, 5, 27))
    session.refresh(pending)
    assert pending.status == "stale"
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_pantry_service_pending_expiry.py -v`
Expected: status stays `pending` because no expiry call exists yet.

- [ ] **Step 3: Modify `app/pantry_service.py`** — add the import and a single call site in each mutator. Concretely:

Add import at the top of the file:

```python
from app.pending_service import expire_for_item
```

In `_set_terminal`, BEFORE `session.add(pantry_item)`:

```python
    expire_for_item(session, user_id=pantry_item.user_id, item_id=pantry_item.id)
```

In `mark_removed`, BEFORE `session.add(pantry_item)`:

```python
    expire_for_item(session, user_id=pantry_item.user_id, item_id=pantry_item.id)
```

In `snooze_item`, BEFORE `session.add(pantry_item)`:

```python
    expire_for_item(session, user_id=pantry_item.user_id, item_id=pantry_item.id)
```

In `correct_item`, BEFORE the mutator's own `session.add(pantry_item)`:

```python
    expire_for_item(session, user_id=pantry_item.user_id, item_id=pantry_item.id)
```

(Keep all existing logic intact; this is a single new line per mutator.)

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_pantry_service_pending_expiry.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run full suite**

Run: `uv run pytest`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add app/pantry_service.py tests/test_pantry_service_pending_expiry.py
git commit -m "feat(pantry): every mutator calls expire_for_item before commit

Per v1.5 spec §7.4: mutation-based pending expiry runs in the same
transaction so a snooze/eat/toss/remove/correct on item N marks all
pending corrections for N as 'stale' atomically.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 6 — Renderer extensions

Goal: pure formatting functions for the diff messages and their terminal states. No I/O, no DB, no LLM. All testable from strings.

### Task 6.1 — `render_correction_diff` + apply/cancel keyboard

**Files:**
- Modify: `app/renderer.py`
- Create: `tests/test_render_correction_diff.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render_correction_diff.py
from app.correction_service import CorrectPayload
from app.renderer import (
    CallbackButton,
    build_apply_cancel_keyboard,
    render_correction_diff,
)


def test_diff_renders_all_changed_fields():
    payload = CorrectPayload(
        diff={
            "name": {"old": "Milk", "new": "Heavy Cream"},
            "category": None,
            "expires_on": {"old": "2026-06-02", "new": "2026-06-05"},
            "shelf_life_days": {"old": 7, "new": 10},
        },
        cache_action="move",
        rationale="user clarified the item identity",
        confidence=0.92, back_computed_days=True,
    )
    text = render_correction_diff(
        pending_id=123, payload=payload, item_id=42, item_raw_name="Milk",
    )
    assert "#42" in text and "Milk" in text
    assert "name: Milk → Heavy Cream" in text
    assert "expires_on: 2026-06-02 → 2026-06-05" in text
    assert "shelf_life_days: 7 → 10" in text
    assert "back-computed" in text
    assert "move" in text
    assert "user clarified the item identity" in text


def test_diff_skips_unchanged_fields():
    payload = CorrectPayload(
        diff={
            "name": None,
            "category": {"old": "dairy", "new": "beverage"},
            "expires_on": None,
            "shelf_life_days": None,
        },
        cache_action="leave",
        rationale="category fix", confidence=0.9,
    )
    text = render_correction_diff(
        pending_id=1, payload=payload, item_id=42, item_raw_name="Milk",
    )
    assert "category: dairy → beverage" in text
    assert "name:" not in text
    assert "shelf_life_days:" not in text


def test_keyboard_has_apply_and_cancel_buttons():
    rows = build_apply_cancel_keyboard(pending_id=123)
    flat = [b for row in rows for b in row]
    assert len(flat) == 2
    callbacks = {b.callback_data for b in flat}
    assert "apply:123" in callbacks
    assert "cancel:123" in callbacks
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_render_correction_diff.py -v`
Expected: ImportError.

- [ ] **Step 3: Append to `app/renderer.py`**

```python
from app.correction_service import CorrectPayload, AddPayload


# TODO(user): tune correction/add diff wording, emoji, and field order
# against the messages you actually want to read in Telegram.
def render_correction_diff(
    *,
    pending_id: int,
    payload: CorrectPayload,
    item_id: int,
    item_raw_name: str,
) -> str:
    lines = [f"Proposed correction for #{item_id} {item_raw_name}:"]
    for field in ("name", "category", "expires_on", "shelf_life_days"):
        change = payload.diff.get(field)
        if change is None:
            continue
        suffix = ""
        if field == "shelf_life_days" and payload.back_computed_days:
            suffix = "  (back-computed from expires_on)"
        lines.append(f"  • {field}: {change['old']} → {change['new']}{suffix}")
    lines.append(f"  • cache: {payload.cache_action}")
    lines.append("")
    lines.append(f"Reason: {payload.rationale}")
    lines.append("Expires in 10 min.")
    return "\n".join(lines)


def build_apply_cancel_keyboard(*, pending_id: int) -> list[list[CallbackButton]]:
    return [[
        CallbackButton(text="✓ Apply", callback_data=f"apply:{pending_id}"),
        CallbackButton(text="✗ Cancel", callback_data=f"cancel:{pending_id}"),
    ]]
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_render_correction_diff.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/renderer.py tests/test_render_correction_diff.py
git commit -m "feat(renderer): render_correction_diff + apply/cancel keyboard

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6.2 — `render_add_diff`

**Files:**
- Modify: `app/renderer.py`
- Create: `tests/test_render_add_diff.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render_add_diff.py
from datetime import date

from app.correction_service import AddPayload
from app.renderer import render_add_diff


def test_add_diff_user_correction_source():
    p = AddPayload(
        name="Oat Milk", category="beverage", qty=0.5, unit="gal",
        shelf_life_days=10, expires_on=date(2026, 6, 6),
        shelf_life_source="user_correction",
        ingest_shelf_life_source="manual_user_hint",
        explicit_user_expiry=True,
        estimated_shelf_life_days=10, confidence=0.88,
    )
    text = render_add_diff(pending_id=1, payload=p)
    assert "Oat Milk" in text
    assert "beverage" in text
    assert "0.5 gal" in text
    assert "2026-06-06" in text
    assert "user_correction" in text
    assert "0.88" in text


def test_add_diff_handles_null_category_and_unit():
    p = AddPayload(
        name="Mystery", category=None, qty=1.0, unit=None,
        shelf_life_days=3, expires_on=date(2026, 5, 30),
        shelf_life_source="manual_fallback",
        ingest_shelf_life_source="manual_fallback",
        explicit_user_expiry=False,
        estimated_shelf_life_days=None, confidence=0.5,
    )
    text = render_add_diff(pending_id=1, payload=p)
    assert "category: —" in text or "category: (unknown)" in text
    assert "1.0" in text
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_render_add_diff.py -v`
Expected: ImportError.

- [ ] **Step 3: Append to `app/renderer.py`**

```python
def render_add_diff(*, pending_id: int, payload: AddPayload) -> str:
    category = payload.category if payload.category is not None else "(unknown)"
    unit = f" {payload.unit}" if payload.unit else ""
    lines = [
        f"Proposed add — {payload.name}:",
        f"  • category: {category}",
        f"  • qty / unit: {payload.qty}{unit}",
        f"  • expires_on: {payload.expires_on.isoformat()}",
        f"  • shelf_life_days: {payload.shelf_life_days}  "
        f"(source: {payload.shelf_life_source})",
        "",
        f"Confidence: {payload.confidence:.2f}",
        "Expires in 10 min.",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_render_add_diff.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/renderer.py tests/test_render_add_diff.py
git commit -m "feat(renderer): render_add_diff for per-item /add proposals

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6.3 — Applied + terminal-state renderers

**Files:**
- Modify: `app/renderer.py`
- Create: `tests/test_render_pending_terminal.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render_pending_terminal.py
from datetime import date

from app.correction_service import AddPayload, CorrectPayload
from app.renderer import (
    render_applied_add,
    render_applied_correction,
    render_terminal_state,
)


def test_render_applied_correction_shows_diff_summary():
    payload = CorrectPayload(
        diff={
            "name": {"old": "Milk", "new": "Heavy Cream"},
            "category": None,
            "expires_on": None,
            "shelf_life_days": None,
        },
        cache_action="move",
        rationale="x", confidence=0.9,
    )
    text = render_applied_correction(item_id=42, payload=payload)
    assert text.startswith("✓ Applied")
    assert "#42" in text
    assert "Heavy Cream" in text


def test_render_applied_add_shows_new_id():
    payload = AddPayload(
        name="Oat Milk", category="beverage", qty=1.0, unit=None,
        shelf_life_days=10, expires_on=date(2026, 6, 6),
        shelf_life_source="user_correction",
        ingest_shelf_life_source="manual_user_hint",
        explicit_user_expiry=True,
        estimated_shelf_life_days=10, confidence=0.88,
    )
    text = render_applied_add(item_id=99, payload=payload)
    assert "#99" in text and "Oat Milk" in text


def test_render_terminal_state_each_status():
    assert "Cancelled" in render_terminal_state("cancelled")
    assert "expired" in render_terminal_state("expired")
    assert "stale" in render_terminal_state("stale")
    assert "already applied" in render_terminal_state("applied")
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_render_pending_terminal.py -v`
Expected: ImportError.

- [ ] **Step 3: Append to `app/renderer.py`**

```python
def render_applied_correction(*, item_id: int, payload: CorrectPayload) -> str:
    changes = []
    for field in ("name", "category", "expires_on", "shelf_life_days"):
        change = payload.diff.get(field)
        if change is not None:
            changes.append(f"{field}={change['new']}")
    suffix = ", ".join(changes) if changes else "no changes"
    return f"✓ Applied to #{item_id}: {suffix}"


def render_applied_add(*, item_id: int, payload: AddPayload) -> str:
    return (
        f"✓ Added #{item_id} {payload.name} "
        f"(expires {payload.expires_on.isoformat()})"
    )


_TERMINAL_LABELS = {
    "cancelled": "✗ Cancelled.",
    "expired": "This proposal has expired — re-run the command.",
    "stale": "This proposal is stale (the item changed) — re-run the command.",
    "applied": "This proposal was already applied.",
}


def render_terminal_state(status: str) -> str:
    return _TERMINAL_LABELS.get(status, f"This proposal is no longer pending ({status}).")
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_render_pending_terminal.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run full suite**

Run: `uv run pytest`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add app/renderer.py tests/test_render_pending_terminal.py
git commit -m "feat(renderer): applied + terminal-state renderers for pending diffs

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 7 — Bot wiring

Goal: rewire `/correct` and `/add`; add `[Apply]` / `[Cancel]` callback handlers; update `parse_callback`; refresh `/help` text. Existing handlers (digest callbacks, photo ingest, etc.) untouched.

### Task 7.1 — Extend `parse_callback` for apply/cancel verbs

**Files:**
- Modify: `app/commands.py`
- Create: `tests/test_parse_callback_apply_cancel.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_parse_callback_apply_cancel.py
import pytest

from app.commands import CommandError, parse_callback


def test_parse_apply_callback():
    a = parse_callback("apply:123")
    assert a.verb == "apply"
    assert a.item_id == 123


def test_parse_cancel_callback():
    a = parse_callback("cancel:7")
    assert a.verb == "cancel"
    assert a.item_id == 7


def test_parse_existing_act_callback_unchanged():
    a = parse_callback("act:ate:42")
    assert a.verb == "ate"
    assert a.item_id == 42


def test_parse_bad_apply_id():
    with pytest.raises(CommandError):
        parse_callback("apply:notanint")
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_parse_callback_apply_cancel.py -v`
Expected: failure — `apply`/`cancel` not recognized.

- [ ] **Step 3: Modify `app/commands.py`**

Update the `Verb` literal and `parse_callback`:

```python
Verb = Literal["ate", "toss", "snooze2", "show_all", "apply", "cancel"]


def parse_callback(data: str) -> CallbackAction:
    if data == "show:all":
        return CallbackAction(verb="show_all", item_id=None)
    if data.startswith("apply:") or data.startswith("cancel:"):
        verb, _, raw_id = data.partition(":")
        try:
            pid = int(raw_id)
        except ValueError as exc:
            raise CommandError(f"bad pending id {raw_id!r}") from exc
        return CallbackAction(verb=cast(Verb, verb), item_id=pid)
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "act":
        raise CommandError(f"unrecognized callback data {data!r}")
    verb = parts[1]
    if verb not in ("ate", "toss", "snooze2"):
        raise CommandError(f"unknown verb {verb!r}")
    try:
        item_id = int(parts[2])
    except ValueError as exc:
        raise CommandError(f"bad item id {parts[2]!r}") from exc
    return CallbackAction(verb=cast(Verb, verb), item_id=item_id)
```

The `CallbackAction.item_id` field is reused as the pending id for `apply`/`cancel` — name is now slightly off (it carries either an item id or a pending id depending on verb), but the surface is small and we keep the dataclass single-purpose.

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_parse_callback_apply_cancel.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/commands.py tests/test_parse_callback_apply_cancel.py
git commit -m "feat(commands): parse_callback recognizes apply/cancel prefixes

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7.2 — `handle_correct` rewired to propose pipeline

**Files:**
- Modify: `app/bot.py`
- Modify: `app/commands.py` (delete `parse_correct_args`)
- Create: `tests/test_handle_correct_v1_5.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_handle_correct_v1_5.py
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

import app.bot as bot_mod
from app.llm import CorrectionDiff
from app.models import PantryItem, PendingCorrection, User
from tests.fakes import FakeTextLLMClient


@pytest.fixture
def session_factory():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    def make():
        return Session(eng)
    with make() as s:
        s.add(User(telegram_id=1, chat_id=99, created_at=datetime.now(timezone.utc)))
        s.commit()
    return make


def _make_item(session_factory) -> int:
    with session_factory() as s:
        item = PantryItem(
            user_id=1, raw_name="Milk", normalized_name="milk",
            category="dairy", qty=1.0, unit="gal",
            purchased_on=date(2026, 5, 26),
            shelf_life_days=7, shelf_life_source="llm",
            ingest_shelf_life_source="llm",
            expires_on=date(2026, 6, 2),
            status="active", created_via="manual",
            created_at=datetime.now(timezone.utc),
        )
        s.add(item); s.commit(); s.refresh(item)
        return item.id


async def test_handle_correct_creates_pending_and_sends_diff(
    session_factory, monkeypatch
):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    item_id = _make_item(session_factory)
    fake_llm = FakeTextLLMClient(canned_correct=(CorrectionDiff(
        name="Heavy Cream",
        cache_action="move", rationale="x", confidence=0.9,
    ), 150))

    sent_message = MagicMock(message_id=4242)
    msg = MagicMock()
    msg.from_user.id = 1
    msg.chat.id = 99
    msg.chat.type = "private"
    msg.text = f"/correct {item_id} actually heavy cream"
    msg.answer = AsyncMock(return_value=sent_message)

    await bot_mod.handle_correct(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 5, 27, tzinfo=timezone.utc),
        text_llm=fake_llm,
    )

    msg.answer.assert_called_once()
    text, kwargs = msg.answer.call_args.args[0], msg.answer.call_args.kwargs
    assert "Proposed correction" in text
    assert "Heavy Cream" in text
    assert "reply_markup" in kwargs

    with session_factory() as s:
        rows = list(s.exec(select(PendingCorrection)).all())
        assert len(rows) == 1
        assert rows[0].action_type == "correct"
        assert rows[0].item_id == item_id
        assert rows[0].message_id == 4242
        assert rows[0].llm_cost_micros_usd == 150
        assert rows[0].original_snapshot_json is not None
        assert '"raw_name": "Milk"' in rows[0].original_snapshot_json


async def test_handle_correct_null_diff_replies_no_changes(
    session_factory, monkeypatch
):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    item_id = _make_item(session_factory)
    fake_llm = FakeTextLLMClient(canned_correct=(CorrectionDiff(
        cache_action="leave", rationale="no change", confidence=0.5,
    ), 100))
    msg = MagicMock()
    msg.from_user.id = 1
    msg.chat.id = 99
    msg.chat.type = "private"
    msg.text = f"/correct {item_id} looks fine"
    msg.answer = AsyncMock()

    await bot_mod.handle_correct(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 5, 27, tzinfo=timezone.utc),
        text_llm=fake_llm,
    )
    msg.answer.assert_called_with("no changes detected")
    with session_factory() as s:
        rows = list(s.exec(select(PendingCorrection)).all())
        assert rows == []


async def test_handle_correct_unknown_item_replies(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    fake_llm = FakeTextLLMClient(canned_correct=(CorrectionDiff(
        cache_action="leave", rationale="x", confidence=0.5,
    ), 100))
    msg = MagicMock()
    msg.from_user.id = 1
    msg.chat.id = 99
    msg.chat.type = "private"
    msg.text = "/correct 999 anything"
    msg.answer = AsyncMock()

    await bot_mod.handle_correct(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 5, 27, tzinfo=timezone.utc),
        text_llm=fake_llm,
    )
    msg.answer.assert_called_with("no item #999")
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_handle_correct_v1_5.py -v`
Expected: `handle_correct` does not accept `text_llm`.

- [ ] **Step 3: Replace `handle_correct` in `app/bot.py`**

```python
from app.commands import parse_item_id_arg  # already imported
from app.correction_service import (
    NullDiff,
    ProposeCorrectError,
    correct_payload_to_json,
    item_snapshot_to_json,
    propose_correct,
)
from app.llm import TextLLMClient
from app.pending_service import create_pending, set_message_id
from app.renderer import (
    build_apply_cancel_keyboard,
    render_correction_diff,
)


async def handle_correct(
    msg,
    *,
    session_factory,
    now_provider,
    text_llm: TextLLMClient,
    on_user_created: Callable[[User], None] = _noop_user_created,
) -> None:
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None:
            return
        parts = (msg.text or "").split(maxsplit=2)
        if len(parts) < 3 or not parts[2].strip():
            await msg.answer("usage: /correct <item_id> <free text>")
            return
        try:
            item_id = parse_item_id_arg(parts[1].strip())
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        item = session.get(PantryItem, item_id)
        if item is None or item.user_id != user.telegram_id:
            await msg.answer(f"no item #{item_id}")
            return
        if item.status == "removed":
            await msg.answer(f"#{item_id} is removed; cannot correct")
            return
        today = now_provider(user.tz).date()
        try:
            payload, cost = await propose_correct(
                session,
                llm=text_llm,
                user_id=user.telegram_id,
                item=item,
                user_text=parts[2].strip(),
                today=today,
            )
        except NullDiff:
            await msg.answer("no changes detected")
            return
        except ProposeCorrectError as exc:
            await msg.answer(str(exc))
            return
        except Exception as exc:
            log.warning(
                "correction_propose_failed",
                extra={
                    "user_id": user.telegram_id,
                    "item_id": item_id,
                    "error_class": type(exc).__name__,
                },
            )
            await msg.answer("couldn't parse that correction — try simpler wording")
            return

        snapshot_json = item_snapshot_to_json(item)
        pending = create_pending(
            session,
            user_id=user.telegram_id,
            action_type="correct",
            item_id=item_id,
            proposed_json=correct_payload_to_json(payload),
            snapshot_json=snapshot_json,
            cost_micros_usd=cost,
            chat_id=msg.chat.id,
            now=datetime.now(timezone.utc),
        )
        text = render_correction_diff(
            pending_id=pending.id, payload=payload,
            item_id=item_id, item_raw_name=item.raw_name,
        )
        keyboard = to_aiogram_keyboard(
            build_apply_cancel_keyboard(pending_id=pending.id)
        )
        sent = await msg.answer(text, reply_markup=keyboard)
        set_message_id(session, pending=pending, message_id=sent.message_id)
```

Also delete `from app.commands import ... parse_correct_args` and remove `parse_correct_args` from `app/commands.py` along with its `Verb` literal entries if still referenced.

- [ ] **Step 4: Add `text_llm` to `handle_correct` callers in `build_dispatcher`**

In `build_dispatcher`, change the signature of `on_correct`:

```python
    async def on_correct(message):
        await handle_correct(
            message,
            session_factory=session_factory,
            now_provider=now_provider,
            text_llm=text_llm,
            on_user_created=on_user_created,
        )
```

And add `text_llm: TextLLMClient` as a new keyword argument to `build_dispatcher` itself; default to NotImplemented so callers must pass it.

- [ ] **Step 5: Run tests, expect PASS**

Run: `uv run pytest tests/test_handle_correct_v1_5.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add app/bot.py app/commands.py tests/test_handle_correct_v1_5.py
git commit -m "feat(bot): handle_correct uses TextLLMClient + writes pending row

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7.3 — `handle_add` rewired to propose pipeline

**Files:**
- Modify: `app/bot.py`
- Create: `tests/test_handle_add_v1_5.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_handle_add_v1_5.py
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

import app.bot as bot_mod
from app.llm import ProposedAddItem
from app.models import PendingCorrection, User
from tests.fakes import FakeTextLLMClient


@pytest.fixture
def session_factory():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    def make():
        return Session(eng)
    with make() as s:
        s.add(User(telegram_id=1, chat_id=99, created_at=datetime.now(timezone.utc)))
        s.commit()
    return make


async def test_handle_add_multi_item_sends_per_item_messages(
    session_factory, monkeypatch
):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    items = [
        ProposedAddItem(name="Oat Milk", category="beverage",
                        explicit_user_expiry=True, shelf_life_days=10,
                        estimated_shelf_life_days=10, confidence=0.88),
        ProposedAddItem(name="Basil", category="produce",
                        explicit_user_expiry=False,
                        estimated_shelf_life_days=7, confidence=0.7),
    ]
    fake_llm = FakeTextLLMClient(canned_add=(items, 200))

    sent_a = MagicMock(message_id=1001)
    sent_b = MagicMock(message_id=1002)
    msg = MagicMock()
    msg.from_user.id = 1
    msg.chat.id = 99
    msg.chat.type = "private"
    msg.text = "/add oat milk 10d, basil"
    msg.answer = AsyncMock(side_effect=[sent_a, sent_b])

    await bot_mod.handle_add(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 5, 27, tzinfo=timezone.utc),
        text_llm=fake_llm,
    )

    assert msg.answer.await_count == 2
    with session_factory() as s:
        rows = list(s.exec(select(PendingCorrection)).all())
        assert len(rows) == 2
        assert {r.action_type for r in rows} == {"add"}
        assert {r.message_id for r in rows} == {1001, 1002}
        assert sum(r.llm_cost_micros_usd for r in rows) == 200


async def test_handle_add_empty_llm_result_replies(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    fake_llm = FakeTextLLMClient(canned_add=([], 50))
    msg = MagicMock()
    msg.from_user.id = 1
    msg.chat.id = 99
    msg.chat.type = "private"
    msg.text = "/add"
    msg.answer = AsyncMock()

    await bot_mod.handle_add(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 5, 27, tzinfo=timezone.utc),
        text_llm=fake_llm,
    )
    msg.answer.assert_called()
    assert "usage" in msg.answer.call_args.args[0].lower()
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_handle_add_v1_5.py -v`
Expected: `handle_add` does not accept `text_llm`.

- [ ] **Step 3: Replace `handle_add` in `app/bot.py`**

```python
from app.correction_service import (
    AddProposal,
    add_payload_to_json,
    propose_add,
)
from app.renderer import render_add_diff


async def handle_add(
    msg,
    *,
    session_factory,
    now_provider,
    text_llm: TextLLMClient,
    on_user_created: Callable[[User], None] = _noop_user_created,
) -> None:
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None:
            return
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await msg.answer("usage: /add <free text — name, category, expiry>")
            return
        today = now_provider(user.tz).date()
        try:
            proposals, _ = await propose_add(
                session,
                llm=text_llm,
                user_id=user.telegram_id,
                user_text=parts[1].strip(),
                today=today,
                tz=user.tz,
            )
        except Exception as exc:
            log.warning(
                "add_propose_failed",
                extra={
                    "user_id": user.telegram_id,
                    "error_class": type(exc).__name__,
                },
            )
            await msg.answer("couldn't parse that add — try simpler wording")
            return
        if not proposals:
            await msg.answer("usage: /add <free text — name, category, expiry>")
            return
        for proposal in proposals:
            pending = create_pending(
                session,
                user_id=user.telegram_id,
                action_type="add",
                item_id=None,
                proposed_json=add_payload_to_json(proposal.payload),
                snapshot_json=None,
                cost_micros_usd=proposal.cost_share,
                chat_id=msg.chat.id,
                now=datetime.now(timezone.utc),
            )
            text = render_add_diff(pending_id=pending.id, payload=proposal.payload)
            keyboard = to_aiogram_keyboard(
                build_apply_cancel_keyboard(pending_id=pending.id)
            )
            sent = await msg.answer(text, reply_markup=keyboard)
            set_message_id(session, pending=pending, message_id=sent.message_id)
```

Update `build_dispatcher.on_add` to pass `text_llm=text_llm`.

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_handle_add_v1_5.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/bot.py tests/test_handle_add_v1_5.py
git commit -m "feat(bot): handle_add uses TextLLMClient + writes N pending rows

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7.4 — Apply / Cancel callback handlers

**Files:**
- Modify: `app/bot.py`
- Create: `tests/test_handle_apply_cancel.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_handle_apply_cancel.py
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

import app.bot as bot_mod
from app.correction_service import CorrectPayload, correct_payload_to_json
from app.models import PantryItem, PendingCorrection, User
from app.pending_service import create_pending


@pytest.fixture
def session_factory():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    def make():
        return Session(eng)
    with make() as s:
        s.add(User(telegram_id=1, chat_id=99, created_at=datetime.now(timezone.utc)))
        s.commit()
    return make


def _make_pending_correct(session_factory) -> tuple[int, int]:
    with session_factory() as s:
        item = PantryItem(
            user_id=1, raw_name="Milk", normalized_name="milk",
            category="dairy", qty=1.0, unit="gal",
            purchased_on=date(2026, 5, 26),
            shelf_life_days=7, shelf_life_source="llm",
            ingest_shelf_life_source="llm",
            expires_on=date(2026, 6, 2),
            status="active", created_via="manual",
            created_at=datetime.now(timezone.utc),
        )
        s.add(item); s.commit(); s.refresh(item)
        payload = CorrectPayload(
            diff={
                "name": {"old": "Milk", "new": "Heavy Cream"},
                "category": None,
                "expires_on": None,
                "shelf_life_days": None,
            },
            cache_action="move", rationale="x", confidence=0.9,
        )
        p = create_pending(
            s, user_id=1, action_type="correct", item_id=item.id,
            proposed_json=correct_payload_to_json(payload),
            snapshot_json=None, cost_micros_usd=100,
            chat_id=99, now=datetime.now(timezone.utc),
        )
        return p.id, item.id


async def test_apply_correct_mutates_and_marks_applied(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    pending_id, item_id = _make_pending_correct(session_factory)
    cb = MagicMock()
    cb.from_user.id = 1
    cb.data = f"apply:{pending_id}"
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()

    await bot_mod.handle_callback(
        cb,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 5, 27, tzinfo=timezone.utc),
    )

    with session_factory() as s:
        p = s.get(PendingCorrection, pending_id)
        assert p.status == "applied"
        item = s.get(PantryItem, item_id)
        assert item.raw_name == "Heavy Cream"
    cb.message.edit_text.assert_called_once()
    cb.answer.assert_called()


async def test_cancel_marks_pending_cancelled(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    pending_id, item_id = _make_pending_correct(session_factory)
    cb = MagicMock()
    cb.from_user.id = 1
    cb.data = f"cancel:{pending_id}"
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()

    await bot_mod.handle_callback(
        cb,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 5, 27, tzinfo=timezone.utc),
    )
    with session_factory() as s:
        p = s.get(PendingCorrection, pending_id)
        assert p.status == "cancelled"
        item = s.get(PantryItem, item_id)
        assert item.raw_name == "Milk"  # untouched


async def test_apply_on_stale_pending_refuses(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    pending_id, item_id = _make_pending_correct(session_factory)
    with session_factory() as s:
        p = s.get(PendingCorrection, pending_id)
        p.status = "stale"
        s.add(p); s.commit()
    cb = MagicMock()
    cb.from_user.id = 1
    cb.data = f"apply:{pending_id}"
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()

    await bot_mod.handle_callback(
        cb,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 5, 27, tzinfo=timezone.utc),
    )
    with session_factory() as s:
        item = s.get(PantryItem, item_id)
        assert item.raw_name == "Milk"  # NOT mutated
        assert s.get(PendingCorrection, pending_id).status == "stale"


async def test_apply_on_expired_pending_marks_expired_and_refuses(
    session_factory, monkeypatch
):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    pending_id, item_id = _make_pending_correct(session_factory)
    with session_factory() as s:
        p = s.get(PendingCorrection, pending_id)
        p.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        s.add(p); s.commit()
    cb = MagicMock()
    cb.from_user.id = 1
    cb.data = f"apply:{pending_id}"
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()

    await bot_mod.handle_callback(
        cb,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 5, 27, tzinfo=timezone.utc),
    )
    with session_factory() as s:
        assert s.get(PendingCorrection, pending_id).status == "expired"
        assert s.get(PantryItem, item_id).raw_name == "Milk"
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_handle_apply_cancel.py -v`
Expected: `handle_callback` doesn't recognize `apply:` / `cancel:`.

- [ ] **Step 3: Extend `handle_callback` in `app/bot.py`**

Inside `handle_callback`, add an early branch BEFORE the existing `act:`-based block (which lives after the auth check + `parse_callback`):

```python
        if action.verb in ("apply", "cancel"):
            pending_id = action.item_id
            assert pending_id is not None
            await _handle_pending_callback(
                cb, session=session, today=today,
                pending_id=pending_id, verb=action.verb,
            )
            return
```

Then implement `_handle_pending_callback` (module-level helper):

```python
from app.correction_service import (
    add_payload_from_json,
    apply_add,
    apply_correct,
    correct_payload_from_json,
)
from app.pending_service import (
    expire_for_item,
    load_pending,
    mark_applied,
    mark_cancelled,
)
from app.renderer import (
    render_applied_add,
    render_applied_correction,
    render_terminal_state,
)


async def _handle_pending_callback(
    cb, *, session, today, pending_id: int, verb: str
) -> None:
    pending = load_pending(session, user_id=cb.from_user.id, pending_id=pending_id)
    if pending is None:
        await cb.answer("not found")
        return

    now = datetime.now(timezone.utc)
    if pending.status != "pending" or pending.expires_at <= now:
        terminal = pending.status if pending.status != "pending" else "expired"
        if terminal == "expired" and pending.status == "pending":
            pending.status = "expired"
            session.add(pending)
            session.commit()
        try:
            await cb.message.edit_text(render_terminal_state(terminal))
        except Exception as exc:
            log.warning("pending_message_edit_failed",
                        extra={"error_class": type(exc).__name__})
        await cb.answer(f"already {terminal}")
        return

    if verb == "cancel":
        mark_cancelled(session, pending=pending)
        session.commit()
        try:
            await cb.message.edit_text(render_terminal_state("cancelled"))
        except Exception as exc:
            log.warning("pending_message_edit_failed",
                        extra={"error_class": type(exc).__name__})
        await cb.answer("cancelled")
        return

    # verb == "apply"
    if pending.action_type == "correct":
        payload = correct_payload_from_json(pending.proposed_json)
        assert pending.item_id is not None
        item = session.get(PantryItem, pending.item_id)
        if item is None:
            mark_cancelled(session, pending=pending)
            session.commit()
            try:
                await cb.message.edit_text("Item gone — proposal cancelled.")
            except Exception as exc:
                log.warning("pending_message_edit_failed",
                            extra={"error_class": type(exc).__name__})
            await cb.answer("item gone")
            return
        expire_for_item(
            session, user_id=cb.from_user.id, item_id=item.id,
            exclude_pending_id=pending.id,
        )
        apply_correct(session, user_id=cb.from_user.id, item=item, payload=payload)
        mark_applied(session, pending=pending)
        session.commit()
        try:
            await cb.message.edit_text(
                render_applied_correction(item_id=item.id, payload=payload)
            )
        except Exception as exc:
            log.warning("pending_message_edit_failed",
                        extra={"error_class": type(exc).__name__})
        log.info(
            "item_action_applied",
            extra={"user_id": cb.from_user.id, "item_id": item.id,
                   "action": "correct"},
        )
        await cb.answer("applied")
        return

    # action_type == "add"
    payload = add_payload_from_json(pending.proposed_json)
    new_id = apply_add(session, user_id=cb.from_user.id, payload=payload, today=today)
    mark_applied(session, pending=pending)
    session.commit()
    try:
        await cb.message.edit_text(
            render_applied_add(item_id=new_id, payload=payload)
        )
    except Exception as exc:
        log.warning("pending_message_edit_failed",
                    extra={"error_class": type(exc).__name__})
    log.info(
        "item_action_applied",
        extra={"user_id": cb.from_user.id, "item_id": new_id, "action": "add"},
    )
    await cb.answer("added")
```

`expire_for_item(..., exclude_pending_id=pending.id)` is required here:
Apply must stale sibling proposals for the same item without invalidating
the proposal currently being applied. The helper was defined with this
parameter in Task 3.3 and covered by its own regression test.

- [ ] **Step 4: Run tests, expect PASS**

Run:
```
uv run pytest tests/test_pending_service_expire_for_item.py tests/test_handle_apply_cancel.py -v
```
Expected: all green.

- [ ] **Step 5: Run full suite**

Run: `uv run pytest`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add app/bot.py tests/test_handle_apply_cancel.py
git commit -m "feat(bot): handle_apply/handle_cancel callbacks via _handle_pending_callback

Apply commits item/cache mutation and pending status in one transaction;
expire_for_item(exclude_pending_id=...) kills siblings without
invalidating the row being applied.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 7.5 — Update `/help` text and `build_dispatcher` signature

**Files:**
- Modify: `app/bot.py`
- Create: `tests/test_help_text_v1_5.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_help_text_v1_5.py
from app.bot import HELP_TEXT


def test_help_describes_new_correct_semantics():
    assert "Apply" in HELP_TEXT or "apply" in HELP_TEXT
    assert "/correct" in HELP_TEXT
    assert "natural language" in HELP_TEXT.lower() or "free text" in HELP_TEXT.lower()


def test_help_describes_new_add_semantics():
    assert "/add" in HELP_TEXT
    assert "10 min" in HELP_TEXT or "expires" in HELP_TEXT.lower()
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_help_text_v1_5.py -v`
Expected: missing strings.

- [ ] **Step 3: Replace `HELP_TEXT` in `app/bot.py`**

```python
HELP_TEXT = (
    "Commands:\n"
    "  /start - setup status\n"
    "  /tz <IANA> - set timezone\n"
    "  /digest_at <0..23> - set digest hour\n"
    "  /list [category|week|expired] - show pantry\n"
    "  /add <free text> - propose new items in natural language.\n"
    "      Replies with a diff per item; tap Apply or Cancel.\n"
    "      Proposals expire after 10 min.\n"
    "  /ate <id> - mark eaten\n"
    "  /toss <id> - mark tossed\n"
    "  /snooze <id> [days=2] - suppress reminders 1..30d\n"
    "  /correct <id> <free text> - propose a correction in natural\n"
    "      language (name, category, expires, days). Replies with a\n"
    "      diff; tap Apply or Cancel. Proposal expires after 10 min.\n"
    "  /delete <id> - remove a wrong/duplicate import\n"
    "  /stats - last 30 days\n"
    "  /help - this message\n"
    "Send a receipt photo to log it."
)
```

- [ ] **Step 4: Update `build_dispatcher` signature to require `text_llm`**

```python
def build_dispatcher(
    *,
    bot: Bot,
    session_factory: _SessionFactory,
    llm: LLMClient,
    text_llm: TextLLMClient,
    now_provider: NowProvider,
    on_user_created: Callable[[User], None],
    reschedule: Callable[[User], None],
) -> Dispatcher:
    ...
```

And ensure `on_correct` / `on_add` both pass `text_llm=text_llm`.

- [ ] **Step 5: Run tests, expect PASS**

Run: `uv run pytest tests/test_help_text_v1_5.py -v`
Expected: 2 passed.

- [ ] **Step 6: Run full suite**

Run: `uv run pytest`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add app/bot.py tests/test_help_text_v1_5.py
git commit -m "feat(bot): update /help for v1.5 semantics + thread text_llm

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 8 — Scheduler: TTL sweep job

Goal: APScheduler runs `pending_service.sweep_expired` every 5 minutes UTC.

### Task 8.1 — Register `sweep_expired_pendings` cron

**Files:**
- Modify: `app/scheduler.py`
- Create: `tests/test_scheduler_sweep_registration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scheduler_sweep_registration.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.scheduler import register_sweep_expired_pendings


def test_register_sweep_expired_pendings_adds_job():
    scheduler = AsyncIOScheduler()
    register_sweep_expired_pendings(scheduler, session_factory=lambda: None)
    job = scheduler.get_job("sweep_expired_pendings")
    assert job is not None
    trigger = str(job.trigger)
    assert "minute='*/5'" in trigger
    assert "UTC" in trigger
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_scheduler_sweep_registration.py -v`
Expected: ImportError on `register_sweep_expired_pendings`.

- [ ] **Step 3: Append to `app/scheduler.py`**

```python
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.pending_service import sweep_expired

_log = logging.getLogger(__name__)


def _sweep_job(session_factory) -> None:
    try:
        with session_factory() as session:
            swept = sweep_expired(session, now=datetime.now(timezone.utc))
            if swept:
                _log.info("pending_swept", extra={"count": swept})
    except Exception as exc:
        _log.warning(
            "pending_sweep_failed",
            extra={"error_class": type(exc).__name__},
        )


def register_sweep_expired_pendings(
    scheduler: AsyncIOScheduler, *, session_factory
) -> None:
    scheduler.add_job(
        _sweep_job,
        "cron",
        minute="*/5",
        timezone="UTC",
        args=[session_factory],
        id="sweep_expired_pendings",
        replace_existing=True,
    )
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_scheduler_sweep_registration.py -v`
Expected: 1 passed.

- [ ] **Step 5: Add an integration test that verifies the job actually marks rows**

```python
# tests/test_scheduler_sweep_integration.py
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import PendingCorrection, User
from app.pending_service import create_pending
from app.scheduler import _sweep_job


@pytest.fixture
def session_factory():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    def make():
        return Session(eng)
    with make() as s:
        s.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        s.commit()
    return make


def test_sweep_job_marks_old_rows_expired(session_factory):
    with session_factory() as s:
        create_pending(
            s, user_id=1, action_type="correct", item_id=1,
            proposed_json="{}", snapshot_json=None,
            cost_micros_usd=None, chat_id=1,
            now=datetime.now(timezone.utc) - timedelta(minutes=20),
        )
    _sweep_job(session_factory)
    with session_factory() as s:
        row = s.query(PendingCorrection).first()
        assert row.status == "expired"
```

- [ ] **Step 6: Run tests, expect PASS**

Run: `uv run pytest tests/test_scheduler_sweep_integration.py -v`
Expected: 1 passed.

- [ ] **Step 7: Run full suite**

Run: `uv run pytest`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add app/scheduler.py tests/test_scheduler_sweep_registration.py tests/test_scheduler_sweep_integration.py
git commit -m "feat(scheduler): register sweep_expired_pendings every 5 min UTC

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 9 — `/stats` extension: text-LLM cost buckets

Goal: `compute_stats` returns a `TextLLMCost` field; `render_stats` shows three buckets (receipts / corrections / adds).

### Task 9.1 — Extend `compute_stats` to aggregate `PendingCorrection` costs

**Files:**
- Modify: `app/pantry_service.py`
- Create: `tests/test_compute_stats_text_llm.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compute_stats_text_llm.py
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import PendingCorrection, User
from app.pantry_service import compute_stats
from app.pending_service import create_pending


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        s.commit()
        yield s


def test_text_llm_breakdown_groups_by_action_type(session):
    now = datetime.now(timezone.utc)
    # 2 corrections (one with unknown cost), 1 add
    create_pending(session, user_id=1, action_type="correct", item_id=1,
                   proposed_json="{}", snapshot_json=None,
                   cost_micros_usd=300, chat_id=1, now=now)
    create_pending(session, user_id=1, action_type="correct", item_id=2,
                   proposed_json="{}", snapshot_json=None,
                   cost_micros_usd=None, chat_id=1, now=now)
    create_pending(session, user_id=1, action_type="add", item_id=None,
                   proposed_json="{}", snapshot_json=None,
                   cost_micros_usd=200, chat_id=1, now=now)
    # And one outside the 30-day window
    create_pending(session, user_id=1, action_type="correct", item_id=3,
                   proposed_json="{}", snapshot_json=None,
                   cost_micros_usd=999, chat_id=1,
                   now=now - timedelta(days=31))

    stats = compute_stats(session, user_id=1, now=now)
    assert stats.text_llm.correction_proposal_count == 2
    assert stats.text_llm.correction_cost_micros == 300
    assert stats.text_llm.correction_unknown_cost_count == 1
    assert stats.text_llm.add_proposal_count == 1
    assert stats.text_llm.add_cost_micros == 200
    assert stats.text_llm.add_unknown_cost_count == 0


def test_text_llm_breakdown_empty_when_no_pendings(session):
    now = datetime.now(timezone.utc)
    stats = compute_stats(session, user_id=1, now=now)
    assert stats.text_llm.correction_proposal_count == 0
    assert stats.text_llm.add_proposal_count == 0
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_compute_stats_text_llm.py -v`
Expected: AttributeError on `stats.text_llm`.

- [ ] **Step 3: Modify `app/pantry_service.py`**

Add the dataclass near the existing `Stats`:

```python
@dataclass(frozen=True)
class TextLLMCost:
    correction_proposal_count: int
    correction_cost_micros: int
    correction_unknown_cost_count: int
    add_proposal_count: int
    add_cost_micros: int
    add_unknown_cost_count: int
```

Extend `Stats` with one field (preserve existing fields):

```python
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
    text_llm: TextLLMCost
```

Inside `compute_stats`, after computing `waste_rate`, before the return:

```python
    pending_rows = list(
        session.exec(
            select(PendingCorrection).where(
                PendingCorrection.user_id == user_id,
                PendingCorrection.created_at >= since,
            )
        ).all()
    )
    correction_rows = [r for r in pending_rows if r.action_type == "correct"]
    add_rows = [r for r in pending_rows if r.action_type == "add"]
    text_llm = TextLLMCost(
        correction_proposal_count=len(correction_rows),
        correction_cost_micros=sum(
            r.llm_cost_micros_usd or 0 for r in correction_rows
        ),
        correction_unknown_cost_count=sum(
            1 for r in correction_rows if r.llm_cost_micros_usd is None
        ),
        add_proposal_count=len(add_rows),
        add_cost_micros=sum(r.llm_cost_micros_usd or 0 for r in add_rows),
        add_unknown_cost_count=sum(
            1 for r in add_rows if r.llm_cost_micros_usd is None
        ),
    )
```

Pass `text_llm=text_llm` into the `Stats(...)` constructor.

Add the missing import at the top of `app/pantry_service.py`:

```python
from app.models import PendingCorrection
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_compute_stats_text_llm.py -v`
Expected: 2 passed.

- [ ] **Step 5: Run full suite**

Run: `uv run pytest`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add app/pantry_service.py tests/test_compute_stats_text_llm.py
git commit -m "feat(stats): aggregate PendingCorrection cost by action_type

Per v1.5 spec §10: three buckets — receipts / corrections / adds —
each with total cost + unknown-cost count. Text buckets are
proposal-row based per spec.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 9.2 — Extend `render_stats` with the text-LLM lines

**Files:**
- Modify: `app/renderer.py`
- Create: `tests/test_render_stats_text_llm.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_render_stats_text_llm.py
from app.pantry_service import Stats, TextLLMCost
from app.renderer import render_stats


def _stats(**kw):
    base = dict(
        receipt_count=2, tracked_item_count=10, removed_item_count=0,
        cache_hit_percent=50.0,
        total_cost_micros_usd=180_000, avg_cost_micros_usd=90_000,
        unknown_cost_receipt_count=0,
        waste_rate_percent=0.0,
        text_llm=TextLLMCost(
            correction_proposal_count=3, correction_cost_micros=320,
            correction_unknown_cost_count=1,
            add_proposal_count=2, add_cost_micros=190,
            add_unknown_cost_count=0,
        ),
    )
    base.update(kw)
    return Stats(**base)


def test_render_stats_shows_text_buckets():
    text = render_stats(_stats())
    assert "Corrections" in text
    assert "Adds" in text
    assert "3" in text and "2" in text
    assert "1 unknown" in text


def test_render_stats_zero_text_buckets_renders_clean():
    text = render_stats(_stats(text_llm=TextLLMCost(
        correction_proposal_count=0, correction_cost_micros=0,
        correction_unknown_cost_count=0,
        add_proposal_count=0, add_cost_micros=0,
        add_unknown_cost_count=0,
    )))
    assert "Corrections" in text
    assert "0" in text
```

- [ ] **Step 2: Run test, expect FAIL**

Run: `uv run pytest tests/test_render_stats_text_llm.py -v`
Expected: rendering does not include `Corrections` / `Adds` lines.

- [ ] **Step 3: Modify `render_stats` in `app/renderer.py`** to append the two text-LLM lines under the existing receipt block. The exact wording is left flexible but must include the strings the test asserts on:

```python
    # Append after the existing receipt-cost block, BEFORE the return:
    tl = stats.text_llm
    corr_unknown = f", {tl.correction_unknown_cost_count} unknown" if tl.correction_unknown_cost_count else ""
    add_unknown = f", {tl.add_unknown_cost_count} unknown" if tl.add_unknown_cost_count else ""
    lines.append(
        f"  Corrections: {tl.correction_proposal_count}  "
        f"(${tl.correction_cost_micros/1_000_000:.4f} total{corr_unknown})"
    )
    lines.append(
        f"  Adds:        {tl.add_proposal_count}  "
        f"(${tl.add_cost_micros/1_000_000:.4f} total{add_unknown})"
    )
```

- [ ] **Step 4: Run tests, expect PASS**

Run: `uv run pytest tests/test_render_stats_text_llm.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/renderer.py tests/test_render_stats_text_llm.py
git commit -m "feat(renderer): /stats shows Corrections + Adds cost buckets

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 10 — `bin/run.py` wiring

Goal: construct `AnthropicTextLLMClient`, pass it into `build_dispatcher`, and register the sweep job.

### Task 10.1 — Wire `text_llm` and sweep job in `bin/run.py`

**Files:**
- Modify: `bin/run.py`

There is no automated test for the entry point itself; verification is by manual smoke (Phase 12) plus the existing build_dispatcher signature being checked at import time.

- [ ] **Step 1: Modify `bin/run.py`**

At construction time:

```python
from anthropic import AsyncAnthropic

from app.llm import AnthropicLLMClient, AnthropicTextLLMClient
from app.scheduler import register_sweep_expired_pendings
```

After creating the vision client, also create the text client:

```python
    sdk = AsyncAnthropic(api_key=settings.anthropic_api_key)
    llm = AnthropicLLMClient(sdk=sdk, model=settings.anthropic_model)
    text_llm = AnthropicTextLLMClient(
        sdk=sdk, model=settings.anthropic_text_model
    )
```

In the `build_dispatcher(...)` call, add `text_llm=text_llm`.

After `register_all_user_digests(...)` (or wherever per-user jobs are registered), add:

```python
    register_sweep_expired_pendings(scheduler, session_factory=session_factory)
```

- [ ] **Step 2: Run full suite (to confirm import + signature wiring is intact)**

Run: `uv run pytest`
Expected: all green.

- [ ] **Step 3: Smoke-import the entry point**

```bash
uv run python -c "import bin.run"
```

Expected: exits 0 with no error. If `bin/run.py` performs side effects on import that need env vars set, prefer:

```bash
TELEGRAM_BOT_TOKEN=x ALLOWED_TELEGRAM_USER_ID=1 ANTHROPIC_API_KEY=k \
    uv run python -c "import importlib; importlib.import_module('bin.run')"
```

- [ ] **Step 4: Commit**

```bash
git add bin/run.py
git commit -m "feat(run): construct AnthropicTextLLMClient + register sweep job

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 11 — Cleanup: retire v1 regex parser and `parse_correct_args`

Goal: remove dead code in `app/ingest_service.py` and `app/commands.py` superseded by the LLM path. No new behavior.

### Task 11.1 — Delete v1 `/add` regex parser and `TextIngestSummary`

**Files:**
- Modify: `app/ingest_service.py`
- Modify: `tests/` — delete any tests that exercised the old regex parser.

- [ ] **Step 1: Identify and list tests that reference the old helpers**

Run:

```powershell
rg -n "ingest_text|TextIngestSummary|_parse_text_part|_HINT_RE|_QTY_PREFIX_RE|_DOZEN_PREFIX_RE" app tests
```

Make a list. Each test that exercises the v1 `/add` regex (qty/unit prefix, `Nd` hint regex, comma split with regex) should be removed — the LLM path is now the only manual-ingest path.

- [ ] **Step 2: In `app/ingest_service.py`, delete:**

- the regexes `_HINT_RE`, `_QTY_PREFIX_RE`, `_DOZEN_PREFIX_RE`
- the function `_parse_text_part`
- the dataclass `TextIngestSummary`
- the function `ingest_text`

These are now replaced by `correction_service.propose_add` and `apply_add`. Confirm nothing imports them.

```powershell
rg -n "ingest_text|TextIngestSummary|_parse_text_part|_HINT_RE|_QTY_PREFIX_RE|_DOZEN_PREFIX_RE" app tests
```

Expected after deletion: empty output (no matches).

- [ ] **Step 3: Delete the old `parse_correct_args` from `app/commands.py`** (if not already removed in Task 7.2). Confirm nothing imports it.

- [ ] **Step 4: Remove now-orphaned test files**

If `tests/test_ingest_text.py` (or similar) exists and exercises the deleted helpers exclusively, `git rm` it. If a test file mixes old and surviving cases, edit it to drop only the obsolete cases.

- [ ] **Step 5: Run full suite**

Run: `uv run pytest`
Expected: all green. No failures, no collection errors.

- [ ] **Step 6: Commit**

```bash
git add -A app/ingest_service.py app/commands.py tests/
git commit -m "refactor: remove v1 /add regex parser + parse_correct_args

Replaced by the LLM-driven correction_service.propose_add /
apply_add path landed in Phase 4. Tests covering only the
regex are removed; cases that still apply moved into the v1.5
test suite.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Phase 12 — Documentation + manual verification

### Task 12.1 — Update `README.md`

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add `ANTHROPIC_TEXT_MODEL` to the Quickstart `.env` block**

In the README's environment block (or its closest equivalent), list `ANTHROPIC_TEXT_MODEL` alongside `ANTHROPIC_MODEL`.

- [ ] **Step 2: Add a short "v1.5 behavior" paragraph under the Quickstart**

```markdown
### v1.5 behavior

- `/correct <id> <free text>` and `/add <free text>` parse with Claude
  Haiku (configurable via `ANTHROPIC_TEXT_MODEL`) and reply with a
  diff message. Tap **Apply** to commit or **Cancel** to discard.
  Proposals expire after 10 minutes.
- Any mutation to a pantry item (mark eaten / tossed / removed /
  snoozed / corrected) invalidates pending corrections for that item
  in the same transaction.
- `/stats` reports text-LLM cost broken down by action type.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): document v1.5 /correct + /add LLM flow

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 12.2 — Manual smoke against `@food_manager_dev_bot`

**Files:** none (manual checklist).

- [ ] **Step 1: Local DB migration**

Run: `DATABASE_PATH=./food.db uv run alembic upgrade head`
Expected: applies `0002_pending_correction` cleanly.

- [ ] **Step 2: Start the dev bot**

Run: `uv run python bin/run.py`
Expected: scheduler boots, sweep job logs registration, dispatcher polls.

- [ ] **Step 3: `/correct` smoke**

In Telegram:
1. `/correct <existing-id> actually heavy cream and expires June 5`.
2. Verify diff message arrives within ~3 s.
3. Tap **Apply**. Verify the message edits to `✓ Applied to #...`. Verify the pantry item's name and expiry updated.
4. Re-run `/correct` on the same item, then tap **Cancel**. Verify the message edits to `✗ Cancelled.` and no DB mutation occurred.

- [ ] **Step 4: `/add` smoke**

1. `/add a half gallon of oat milk that keeps about 10 days, fresh basil`.
2. Two diff messages arrive. Apply one, cancel the other.
3. Verify `/list` shows the applied item, not the cancelled one.

- [ ] **Step 5: Mutation-based expiry smoke**

1. `/correct <id> ...` — leave the diff un-tapped.
2. `/snooze <id>` from another message.
3. Tap **Apply** on the original diff. Verify the response is `This proposal is stale ...` and no mutation occurred.

- [ ] **Step 6: TTL smoke**

1. `/correct <id> ...` — leave the diff un-tapped.
2. Wait 11+ minutes (or run `_sweep_job(session_factory)` from a Python shell against the local DB).
3. Tap **Apply**. Verify `expired` response and no mutation.

- [ ] **Step 7: `/stats` smoke**

Run `/stats`. Verify three buckets — receipts / corrections / adds — appear with sensible counts and costs.

- [ ] **Step 8: Commit any incidental fixes**

If any manual smoke surfaced a bug, fix it with a small focused commit (`fix: ...`). Do NOT batch unrelated fixes into one commit.

---

## Self-Review

Re-read the spec, then walk the plan once.

**1. Spec coverage:**

| Spec section | Implemented by |
|---|---|
| §1 Purpose / §2 Scope | Phases 1–10 collectively |
| §3 Locked decisions | Each row maps to: Phase 2 (LLM), Phase 4 (confirmation pattern via apply/cancel handlers), §6.1 schema (Task 2.1), Phase 4 (cache_action), Phase 4 (`/add` LLM-first), Task 1.2 (Alembic 0002), Phase 4 (fallback chain), Phase 5 (stale apply), Phase 7 (per-item /add batches), Phase 9 (stats split), Phase 8 (TTL sweep), Phase 4 (null diff), Task 1.3 (text model setting) |
| §4 Architecture / module map | Phase 2 (`TextLLMClient`), Phase 3 (`pending_service`), Phase 4 (`correction_service`), Phase 5 (mutator wiring), Phase 6 (renderer), Phase 7 (bot), Phase 8 (scheduler), Phase 9 (stats), Phase 10 (run.py), Phase 11 (cleanup) |
| §5 Data model | Task 1.1 (model) + Task 1.2 (migration) + Task 4.1 (payload Pydantic) |
| §6 LLM contract | Task 2.1 (Pydantic models) + Task 2.3 (real client + system prompts) |
| §6.3 Service-layer post-processing | Tasks 4.2 (correct), 4.4 (add) |
| §7 End-to-end flows | Tasks 7.2 (`/correct`), 7.3 (`/add`), 7.4 (apply/cancel) |
| §7.4 Stale-pending expiry — three sources | TTL (Phase 8), mutation (Phase 5 + Task 7.4 exclude_pending_id), cancel button (Task 7.4) |
| §7.5 Auth/chat-type | Existing v1 `_guard` reused in Tasks 7.2 / 7.3 |
| §8 Commands deltas | Tasks 7.2, 7.3, 7.5 (HELP_TEXT) |
| §9 Settings/secrets | Task 1.3 + Task 10.1 |
| §10 `/stats` extension | Phase 9 |
| §11 Scheduler additions | Phase 8 |
| §12 Migrations | Task 1.2 |
| §13 Testing strategy | TDD throughout — counts roughly match the spec targets |
| §14 Deployment | Task 10.1 |
| §15 User-authored TODO markers | Tasks 2.3 (prompts), 6.1 + 6.2 (renderers); markers are present in code comments per spec |
| §18 Definition of done | Task 12.2 manual checklist + Phases 1–11 |

No gaps found.

**2. Placeholder scan:** Skim every task for "TBD", "TODO" (non-spec), "handle edge cases", "similar to Task N". None present. The three `TODO(user)` strings are deliberate per spec §15 and live in code comments, not the plan.

**3. Type / signature consistency:**

- `pending_service.create_pending(session, *, user_id, action_type, item_id, proposed_json, snapshot_json, cost_micros_usd, chat_id, now) -> PendingCorrection` — same call sites in Tasks 7.2, 7.3, 8 integration tests, 9 stats tests, 5 expiry tests.
- `pending_service.expire_for_item(session, *, user_id, item_id, exclude_pending_id=None) -> int` — defined in Task 3.3 with an exclude test, called from Task 7.4 with `exclude_pending_id=pending.id`. Call sites in Phase 5 use the default (None), which preserves the original behavior.
- `propose_correct(...)` returns `tuple[CorrectPayload, Optional[int]]`; consumer in Task 7.2 unpacks `(payload, cost)`.
- `propose_add(...)` returns `tuple[list[AddProposal], Optional[int]]`; consumer in Task 7.3 iterates over `proposals` and uses `proposal.cost_share` and `proposal.payload`.
- `apply_correct(session, *, user_id, item, payload)` and `apply_add(session, *, user_id, payload, today)` — Task 7.4 passes these arguments correctly and commits once after marking the pending row applied.
- `Stats.text_llm: TextLLMCost` — declared in Task 9.1, consumed by render_stats in Task 9.2.

No inconsistencies found.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-27-food-manager-v1.5.md`. Two execution options:**

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. Uses `superpowers:subagent-driven-development`.
2. **Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

**Which approach?**
