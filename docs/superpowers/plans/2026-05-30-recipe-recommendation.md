# /cook Recipe Recommendation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/cook` Telegram command that recommends a dish from the user's pantry via a deterministic LLM pipeline (select items → fetch 3 recipes via web search → score → rank), honouring a sentence-driven persistent food profile, and showing a display-only shopping list.

**Architecture:** A deterministic async pipeline (no agent framework) running on the user's selected provider. Three `/cook` LLM stages (selection, recipe+web, nutrition) plus deterministic Python glue (hard allergy filter, expiry-utilization, blend/rank, shopping diff). State for the interactive button rounds lives in a new `CookSession` table mirroring the existing `PendingCorrection` lifecycle. A separate `parse_profile_update` LLM stage maintains a hybrid food profile stored as columns on `User`.

**Tech Stack:** Python, SQLModel/SQLAlchemy, Alembic, aiogram, pydantic, Anthropic + OpenAI SDKs, pytest.

**Spec:** `docs/superpowers/specs/2026-05-30-recipe-recommendation-design.md`

**Conventions to honour (verify by reading the referenced files before starting):**
- Service functions take `session: Session` first; callers pass `today`/`now` — never call `datetime.now()` inside pure logic. See `app/pantry_service.py`.
- LLM clients are `Protocol`s with dataclass fakes in `tests/fakes.py`. Provider chosen via `getattr(client, "for_provider", ...)` selectors (`app/bot.py:144-155`).
- State rows: create/load/set_message_id/terminal/sweep pattern in `app/pending_service.py`.
- Migrations follow `migrations/versions/0003_user_llm_provider.py` exactly (string `revision`, `down_revision`, `server_default` for non-null columns).
- Cost is integer micro-USD. `/stats` aggregates it (`app/pantry_service.py:201`).
- Tests use an in-memory engine fixture (`tests/test_core_services.py:37-44`): `create_engine("sqlite:///:memory:")`, `SQLModel.metadata.create_all`, seed a `User`.

**Run tests:** `uv run pytest` (single: `uv run pytest tests/test_cook_profile.py::test_name -v`).

---

## PHASE 1 — Persistent food profile (ships independently)

### Task 1: Add profile columns to the `User` model

**Files:**
- Modify: `app/models.py:26-32` (the `User` class)
- Test: `tests/test_cook_profile.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cook_profile.py
from datetime import datetime, timezone

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cook_profile.py::test_user_has_profile_columns_with_defaults -v`
Expected: FAIL with `AttributeError` / unexpected keyword (columns don't exist).

- [ ] **Step 3: Add the columns**

In `app/models.py`, add to the `User` class (after `llm_provider`):

```python
    diet: str = "none"
    exclusions_json: str = "[]"
    preferred_cuisines_json: str = "[]"
    max_cook_minutes: Optional[int] = None
    household_size: int = 1
    profile_note: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cook_profile.py::test_user_has_profile_columns_with_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_cook_profile.py
git commit -m "feat(model): add food-profile columns to User"
```

---

### Task 2: Alembic migration for the profile columns

**Files:**
- Create: `migrations/versions/0004_user_food_profile.py`
- Test: `tests/test_cook_profile.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_cook_profile.py
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa


def test_migration_0004_adds_profile_columns(tmp_path):
    db_path = tmp_path / "m.db"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={"DATABASE_PATH": str(db_path), **_env()},
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 0, result.stderr
    engine = sa.create_engine(f"sqlite:///{db_path}")
    cols = {c["name"] for c in sa.inspect(engine).get_columns("user")}
    assert {"diet", "exclusions_json", "preferred_cuisines_json",
            "max_cook_minutes", "household_size", "profile_note"} <= cols


def _env():
    import os
    return {k: v for k, v in os.environ.items()}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cook_profile.py::test_migration_0004_adds_profile_columns -v`
Expected: FAIL (head is `0003`; columns absent).

- [ ] **Step 3: Write the migration**

```python
# migrations/versions/0004_user_food_profile.py
"""user_food_profile

Revision ID: 0004_user_food_profile
Revises: 0003_user_llm_provider
Create Date: 2026-05-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_user_food_profile"
down_revision: Union[str, None] = "0003_user_llm_provider"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user", sa.Column("diet", sa.String(), nullable=False, server_default="none"))
    op.add_column("user", sa.Column("exclusions_json", sa.String(), nullable=False, server_default="[]"))
    op.add_column("user", sa.Column("preferred_cuisines_json", sa.String(), nullable=False, server_default="[]"))
    op.add_column("user", sa.Column("max_cook_minutes", sa.Integer(), nullable=True))
    op.add_column("user", sa.Column("household_size", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("user", sa.Column("profile_note", sa.String(), nullable=False, server_default=""))


def downgrade() -> None:
    for col in ("profile_note", "household_size", "max_cook_minutes",
                "preferred_cuisines_json", "exclusions_json", "diet"):
        op.drop_column("user", col)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cook_profile.py::test_migration_0004_adds_profile_columns -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0004_user_food_profile.py tests/test_cook_profile.py
git commit -m "feat(db): migration 0004 for User food-profile columns"
```

---

### Task 3: `FoodProfile` domain model + (de)serialization

A structured view over the `User` columns. Pure functions; no LLM, no I/O.

**Files:**
- Create: `app/profile_service.py`
- Test: `tests/test_cook_profile.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_cook_profile.py
from app.models import User
from app.profile_service import FoodProfile, profile_from_user, apply_profile_to_user


def test_profile_round_trips_through_user():
    user = User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc))
    profile = FoodProfile(
        diet="vegetarian",
        exclusions=["peanut", "cilantro"],
        preferred_cuisines=["chinese", "american"],
        max_cook_minutes=30,
        household_size=2,
        note="prefer one-pot meals",
    )
    apply_profile_to_user(user, profile)
    assert user.diet == "vegetarian"
    assert user.exclusions_json == '["peanut", "cilantro"]'
    assert profile_from_user(user) == profile


def test_profile_defaults_from_blank_user():
    user = User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc))
    assert profile_from_user(user) == FoodProfile()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cook_profile.py -k profile_ -v`
Expected: FAIL (`ModuleNotFoundError: app.profile_service`).

- [ ] **Step 3: Implement**

```python
# app/profile_service.py
from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, Field

from app.models import User


class FoodProfile(BaseModel):
    diet: str = "none"
    exclusions: list[str] = Field(default_factory=list)
    preferred_cuisines: list[str] = Field(default_factory=list)
    max_cook_minutes: Optional[int] = None
    household_size: int = 1
    note: str = ""


def profile_from_user(user: User) -> FoodProfile:
    return FoodProfile(
        diet=user.diet,
        exclusions=json.loads(user.exclusions_json or "[]"),
        preferred_cuisines=json.loads(user.preferred_cuisines_json or "[]"),
        max_cook_minutes=user.max_cook_minutes,
        household_size=user.household_size,
        note=user.profile_note,
    )


def apply_profile_to_user(user: User, profile: FoodProfile) -> None:
    user.diet = profile.diet
    user.exclusions_json = json.dumps(profile.exclusions)
    user.preferred_cuisines_json = json.dumps(profile.preferred_cuisines)
    user.max_cook_minutes = profile.max_cook_minutes
    user.household_size = profile.household_size
    user.profile_note = profile.note
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cook_profile.py -k profile_ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/profile_service.py tests/test_cook_profile.py
git commit -m "feat(profile): FoodProfile model and User (de)serialization"
```

---

### Task 4: Profile-update LLM protocol, clients, and fake

The sentence→profile merge stage. Protocol in `app/llm.py`; clients added there; fake in `tests/fakes.py`.

**Files:**
- Modify: `app/llm.py` (add `ProfileUpdateLLMClient` Protocol, `PROFILE_SYSTEM_PROMPT`, `AnthropicProfileLLMClient`, `OpenAIProfileLLMClient`)
- Modify: `tests/fakes.py` (add `FakeProfileLLMClient`)
- Test: `tests/test_cook_profile.py`

- [ ] **Step 1: Write the failing test (fake + protocol shape)**

```python
# add to tests/test_cook_profile.py
import asyncio

from app.profile_service import FoodProfile
from tests.fakes import FakeProfileLLMClient


def test_fake_profile_client_returns_merged_profile():
    merged = FoodProfile(diet="vegan", exclusions=["peanut"])
    fake = FakeProfileLLMClient(canned=(merged, 42))
    result, cost = asyncio.run(
        fake.parse_profile_update(current=FoodProfile(), sentence="I'm vegan, no peanuts")
    )
    assert result == merged
    assert cost == 42
    assert fake.calls[0]["sentence"] == "I'm vegan, no peanuts"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cook_profile.py::test_fake_profile_client_returns_merged_profile -v`
Expected: FAIL (`FakeProfileLLMClient` undefined).

- [ ] **Step 3: Add the Protocol + prompt + clients to `app/llm.py`**

Add near the other Protocols (after `TextLLMClient`, ~line 97). Import `FoodProfile` lazily inside methods to avoid a circular import (`profile_service` imports `models`, not `llm`, so a top import is actually safe — prefer a top-level `from app.profile_service import FoodProfile`):

```python
# app/llm.py  (add import at top)
from app.profile_service import FoodProfile

# add Protocol
class ProfileUpdateLLMClient(Protocol):
    async def parse_profile_update(
        self, *, current: FoodProfile, sentence: str
    ) -> tuple[FoodProfile, Optional[int]]: ...
```

```python
# app/llm.py  (add prompt + clients near the other text clients)
PROFILE_SYSTEM_PROMPT = """You maintain a user's food profile. You are given the
current profile as JSON and a new sentence. Return ONLY the updated profile as
JSON matching this schema (merge, do not drop existing values unless the user
clearly retracts them):
{
  "diet": "none|vegetarian|vegan|pescatarian|halal|kosher|other",
  "exclusions": [string],          // allergies and hard-avoid ingredients (lowercase singular)
  "preferred_cuisines": [string],  // e.g. ["chinese","american"]
  "max_cook_minutes": integer or null,
  "household_size": integer >= 1,
  "note": string                   // free-text preferences that don't fit a field
}
Add any newly stated allergy to "exclusions". No prose.
"""


class AnthropicProfileLLMClient(ProfileUpdateLLMClient):
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._delegate = AnthropicTextLLMClient(sdk, model, sleep)

    async def parse_profile_update(self, *, current, sentence):
        user_msg = json.dumps({"current": current.model_dump(), "sentence": sentence})

        def _parse(text: str) -> FoodProfile:
            return FoodProfile.model_validate(json.loads(text))

        return await self._delegate._call_with_schema(
            PROFILE_SYSTEM_PROMPT, user_msg, _parse
        )


class OpenAIProfileLLMClient(ProfileUpdateLLMClient):
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._delegate = OpenAITextLLMClient(sdk, model, sleep)

    async def parse_profile_update(self, *, current, sentence):
        user_msg = json.dumps({"current": current.model_dump(), "sentence": sentence})
        response = await self._delegate._create_response(
            PROFILE_SYSTEM_PROMPT, user_msg, FoodProfile
        )
        return FoodProfile.model_validate(_extract_openai_parsed(response)), None
```

> NOTE: `AnthropicTextLLMClient._call_with_schema` and `OpenAITextLLMClient._create_response` already exist (`app/llm.py:584`, `:666`). Reusing them keeps retry/cost behaviour identical. Define these new classes *after* those two classes in the file.

- [ ] **Step 4: Add the fake to `tests/fakes.py`**

```python
# tests/fakes.py
@dataclass
class FakeProfileLLMClient:
    canned: Optional[tuple["FoodProfile", Optional[int]]] = None
    raise_n_times: int = 0
    _raises: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def parse_profile_update(self, *, current, sentence):
        self.calls.append({"current": current, "sentence": sentence})
        if self._raises < self.raise_n_times:
            self._raises += 1
            raise RuntimeError("simulated profile-llm failure")
        assert self.canned is not None
        return self.canned
```

Add `from app.profile_service import FoodProfile` to the imports in `tests/fakes.py`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_cook_profile.py::test_fake_profile_client_returns_merged_profile -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/llm.py tests/fakes.py tests/test_cook_profile.py
git commit -m "feat(profile): profile-update LLM protocol, clients, and fake"
```

---

### Task 5: `update_profile_from_sentence` service

Loads the user's profile, calls the LLM, persists the merge. Keeps allergies structured.

**Files:**
- Modify: `app/profile_service.py`
- Test: `tests/test_cook_profile.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_cook_profile.py
from app.profile_service import update_profile_from_sentence


def test_update_profile_persists_merge_and_keeps_allergy_structured():
    with _session() as db:
        user = User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc))
        db.add(user)
        db.commit()
        merged = FoodProfile(diet="vegetarian", exclusions=["peanut"],
                             preferred_cuisines=["chinese"], note="spicy ok")
        fake = FakeProfileLLMClient(canned=(merged, 7))
        profile, cost = asyncio.run(update_profile_from_sentence(
            db, llm=fake, user=user, sentence="veggie, no peanuts, chinese, spicy ok",
        ))
        assert cost == 7
        db.refresh(user)
        assert profile_from_user(user) == merged
        assert "peanut" in profile.exclusions  # structured => hard filter can see it
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cook_profile.py::test_update_profile_persists_merge_and_keeps_allergy_structured -v`
Expected: FAIL (`update_profile_from_sentence` undefined).

- [ ] **Step 3: Implement**

```python
# app/profile_service.py  (append)
from datetime import date  # noqa: E402  (top of file is fine too)
from typing import Optional  # already imported

from sqlmodel import Session

from app.llm import ProfileUpdateLLMClient


async def update_profile_from_sentence(
    session: Session,
    *,
    llm: ProfileUpdateLLMClient,
    user: User,
    sentence: str,
) -> tuple[FoodProfile, Optional[int]]:
    current = profile_from_user(user)
    merged, cost = await llm.parse_profile_update(current=current, sentence=sentence)
    apply_profile_to_user(user, merged)
    session.add(user)
    session.commit()
    session.refresh(user)
    return merged, cost
```

> NOTE: `app.llm` imports `FoodProfile` from `app.profile_service`. To avoid a cycle, import `ProfileUpdateLLMClient` lazily *inside* this function instead of at module top:
> ```python
>     from app.llm import ProfileUpdateLLMClient  # local import avoids cycle
> ```
> Use the local import; drop the top-level `from app.llm import ProfileUpdateLLMClient`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cook_profile.py::test_update_profile_persists_merge_and_keeps_allergy_structured -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/profile_service.py tests/test_cook_profile.py
git commit -m "feat(profile): update_profile_from_sentence service"
```

---

### Task 6: `/prefs` renderer, command handler, and dispatcher wiring

**Files:**
- Modify: `app/renderer.py` (add `render_profile`)
- Modify: `app/bot.py` (add `handle_prefs`, register in `build_dispatcher`, extend `HELP_TEXT`)
- Test: `tests/test_cook_profile.py`

- [ ] **Step 1: Write the failing renderer test**

```python
# add to tests/test_cook_profile.py
from app.renderer import render_profile


def test_render_profile_shows_fields():
    text = render_profile(FoodProfile(
        diet="vegetarian", exclusions=["peanut"], preferred_cuisines=["chinese"],
        max_cook_minutes=30, household_size=2, note="spicy ok",
    ))
    assert "vegetarian" in text
    assert "peanut" in text
    assert "chinese" in text
    assert "30" in text
    assert "spicy ok" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cook_profile.py::test_render_profile_shows_fields -v`
Expected: FAIL (`render_profile` undefined).

- [ ] **Step 3: Implement `render_profile`**

```python
# app/renderer.py  (append; import FoodProfile at top)
from app.profile_service import FoodProfile


def render_profile(profile: FoodProfile) -> str:
    exclusions = ", ".join(profile.exclusions) or "none"
    cuisines = ", ".join(profile.preferred_cuisines) or "any"
    cook = f"{profile.max_cook_minutes} min" if profile.max_cook_minutes else "no limit"
    note = profile.note or "(none)"
    return (
        "Your food profile:\n"
        f"  Diet: {profile.diet}\n"
        f"  Avoid: {exclusions}\n"
        f"  Cuisines: {cuisines}\n"
        f"  Max cook time: {cook}\n"
        f"  Household size: {profile.household_size}\n"
        f"  Notes: {note}\n"
        "Update by typing: /prefs <sentence>  (e.g. /prefs I'm vegan, no peanuts)"
    )
```

- [ ] **Step 4: Write the handler test (fake message)**

```python
# add to tests/test_cook_profile.py
from unittest.mock import AsyncMock

import app.bot as bot_mod
from app.bot import handle_prefs


class _Msg:
    def __init__(self, text, user_id=1, chat_id=1):
        self.text = text
        self.from_user = type("U", (), {"id": user_id})
        self.chat = type("C", (), {"id": chat_id, "type": "private"})
        self.answer = AsyncMock()


def test_handle_prefs_no_args_shows_profile(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        db.commit()
    msg = _Msg("/prefs")
    fake = FakeProfileLLMClient(canned=(FoodProfile(), None))
    asyncio.run(handle_prefs(
        msg, session_factory=lambda: Session(engine), profile_llm=fake,
    ))
    assert "food profile" in msg.answer.call_args[0][0].lower()
    assert fake.calls == []  # no sentence => no LLM call


def test_handle_prefs_with_sentence_updates(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        db.commit()
    msg = _Msg("/prefs I'm vegan")
    fake = FakeProfileLLMClient(canned=(FoodProfile(diet="vegan"), None))
    asyncio.run(handle_prefs(
        msg, session_factory=lambda: Session(engine), profile_llm=fake,
    ))
    assert fake.calls[0]["sentence"] == "I'm vegan"
    assert "vegan" in msg.answer.call_args[0][0]
```

- [ ] **Step 5: Run handler tests to verify they fail**

Run: `uv run pytest tests/test_cook_profile.py -k handle_prefs -v`
Expected: FAIL (`handle_prefs` undefined).

- [ ] **Step 6: Implement `handle_prefs` + provider selection + register it**

```python
# app/bot.py  (imports)
from app.llm import ProfileUpdateLLMClient
from app.profile_service import (
    profile_from_user, update_profile_from_sentence,
)
from app.renderer import render_profile  # add to the existing renderer import block


def _select_profile_llm(profile_llm: "ProfileUpdateLLMClient", provider: str):
    selector = getattr(profile_llm, "for_provider", None)
    return selector(provider) if callable(selector) else profile_llm


async def handle_prefs(
    msg,
    *,
    session_factory,
    profile_llm: ProfileUpdateLLMClient,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None:
            return
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await msg.answer(render_profile(profile_from_user(user)))
            return
        try:
            selected = _select_profile_llm(profile_llm, user.llm_provider)
            profile, _ = await update_profile_from_sentence(
                session, llm=selected, user=user, sentence=parts[1].strip(),
            )
        except LLMProviderNotConfigured:
            await msg.answer(f"LLM provider {user.llm_provider!r} is not configured. Use /llm.")
            return
        except Exception as exc:
            log.warning("prefs_update_failed",
                        extra={"user_id": user.telegram_id, "error_class": type(exc).__name__})
            await msg.answer("couldn't update your profile - try simpler wording")
            return
        await msg.answer("Updated.\n\n" + render_profile(profile))
```

Register in `build_dispatcher` (mirror `on_llm`, `app/bot.py:1168`). Add a `profile_llm` parameter to `build_dispatcher`'s signature, then:

```python
    async def on_prefs(message):
        await handle_prefs(
            message, session_factory=session_factory,
            profile_llm=profile_llm, on_user_created=on_user_created,
        )
    dispatcher.message.register(on_prefs, Command("prefs"))
```

Add to `HELP_TEXT` (after the `/llm` line): `"  /prefs [sentence] - show or update your food profile\n"`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_cook_profile.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add app/bot.py app/renderer.py tests/test_cook_profile.py
git commit -m "feat(profile): /prefs command to view and update the food profile"
```

> **Phase 1 is now shippable**: `/prefs` works end-to-end. `run.py` wiring for `profile_llm` is done in Task 17 alongside the cook clients; until then it is constructed in tests only. If shipping Phase 1 alone, do the `profile_llm` slice of Task 17 now.

---

## PHASE 2 — CookSession table + state machine

### Task 7: `CookSession` model + migration 0005

**Files:**
- Modify: `app/models.py`
- Create: `migrations/versions/0005_cook_session.py`
- Test: `tests/test_cook_session.py` (create)

- [ ] **Step 1: Write the failing model test**

```python
# tests/test_cook_session.py
from datetime import datetime, timedelta, timezone

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cook_session.py::test_cook_session_row_persists -v`
Expected: FAIL (`ImportError: CookSession`).

- [ ] **Step 3: Add the model + Literal aliases to `app/models.py`**

```python
# app/models.py  (near the other Literal aliases at top)
CookStatus = Literal["collecting", "ready", "done", "cancelled", "expired"]

# app/models.py  (new table, after PendingCorrection)
class CookSession(SQLModel, table=True):
    __table_args__ = (
        Index("ix_cook_user_status_created", "user_id", "status", "created_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.telegram_id", index=True)
    status: str = "collecting"
    meal_type: Optional[str] = None
    cuisine: Optional[str] = None
    selected_item_ids: str = "[]"
    candidates_json: Optional[str] = None
    chosen_index: Optional[int] = None
    chat_id: int
    message_id: Optional[int] = None
    llm_cost_micros_usd: Optional[int] = None
    created_at: datetime
    expires_at: datetime
```

- [ ] **Step 4: Run model test to verify it passes**

Run: `uv run pytest tests/test_cook_session.py::test_cook_session_row_persists -v`
Expected: PASS

- [ ] **Step 5: Write the migration + its test**

```python
# migrations/versions/0005_cook_session.py
"""cook_session

Revision ID: 0005_cook_session
Revises: 0004_user_food_profile
Create Date: 2026-05-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_cook_session"
down_revision: Union[str, None] = "0004_user_food_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cooksession",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.telegram_id"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("meal_type", sa.String(), nullable=True),
        sa.Column("cuisine", sa.String(), nullable=True),
        sa.Column("selected_item_ids", sa.String(), nullable=False),
        sa.Column("candidates_json", sa.String(), nullable=True),
        sa.Column("chosen_index", sa.Integer(), nullable=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("llm_cost_micros_usd", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_cook_user_status_created", "cooksession",
                    ["user_id", "status", "created_at"])
    op.create_index("ix_cooksession_user_id", "cooksession", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_cooksession_user_id", table_name="cooksession")
    op.drop_index("ix_cook_user_status_created", table_name="cooksession")
    op.drop_table("cooksession")
```

```python
# add to tests/test_cook_session.py
import subprocess, sys, os
from pathlib import Path
import sqlalchemy as sa


def test_migration_0005_creates_cooksession(tmp_path):
    db_path = tmp_path / "m.db"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={"DATABASE_PATH": str(db_path), **dict(os.environ)},
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 0, result.stderr
    engine = sa.create_engine(f"sqlite:///{db_path}")
    assert "cooksession" in sa.inspect(engine).get_table_names()
```

- [ ] **Step 6: Run migration test to verify it passes**

Run: `uv run pytest tests/test_cook_session.py::test_migration_0005_creates_cooksession -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/models.py migrations/versions/0005_cook_session.py tests/test_cook_session.py
git commit -m "feat(db): CookSession table + migration 0005"
```

---

### Task 8: `cook_session_service` — lifecycle helpers

Mirror `pending_service`: create (superseding any in-flight), load, advance rounds, set message id, terminal transitions, accrue cost, sweep expired.

**Files:**
- Create: `app/cook_session_service.py`
- Test: `tests/test_cook_session.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_cook_session.py
from app.cook_session_service import (
    COOK_TTL_MINUTES, create_cook_session, load_cook_session,
    supersede_active, accrue_cost, sweep_expired_cooks,
)


def test_create_supersedes_previous_active():
    with _session() as db:
        now = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
        first = create_cook_session(db, user_id=1, chat_id=1, now=now)
        second = create_cook_session(db, user_id=1, chat_id=1, now=now)
        db.refresh(first)
        assert first.status == "cancelled"
        assert second.status == "collecting"
        assert load_cook_session(db, user_id=1, cook_id=second.id).id == second.id


def test_accrue_cost_sums():
    with _session() as db:
        now = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
        row = create_cook_session(db, user_id=1, chat_id=1, now=now)
        accrue_cost(db, cook=row, add_micros=100)
        accrue_cost(db, cook=row, add_micros=50)
        assert row.llm_cost_micros_usd == 150


def test_sweep_expires_old_collecting():
    with _session() as db:
        old = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
        create_cook_session(db, user_id=1, chat_id=1, now=old)
        swept = sweep_expired_cooks(db, now=old + timedelta(minutes=COOK_TTL_MINUTES + 1))
        assert swept == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cook_session.py -k "supersede or accrue or sweep" -v`
Expected: FAIL (`ModuleNotFoundError: app.cook_session_service`).

- [ ] **Step 3: Implement**

```python
# app/cook_session_service.py
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.models import CookSession
from app.pending_service import utc_naive

COOK_TTL_MINUTES = 10


def supersede_active(session: Session, *, user_id: int) -> int:
    rows = list(session.exec(
        select(CookSession).where(
            CookSession.user_id == user_id,
            CookSession.status.in_(("collecting", "ready")),  # type: ignore[attr-defined]
        )
    ).all())
    for row in rows:
        row.status = "cancelled"
        session.add(row)
    if rows:
        session.flush()
    return len(rows)


def create_cook_session(
    session: Session, *, user_id: int, chat_id: int, now: datetime
) -> CookSession:
    supersede_active(session, user_id=user_id)
    created = utc_naive(now)
    row = CookSession(
        user_id=user_id, status="collecting", chat_id=chat_id,
        selected_item_ids="[]", created_at=created,
        expires_at=created + timedelta(minutes=COOK_TTL_MINUTES),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def load_cook_session(
    session: Session, *, user_id: int, cook_id: int
) -> Optional[CookSession]:
    row = session.get(CookSession, cook_id)
    if row is None or row.user_id != user_id:
        return None
    return row


def set_message_id(session: Session, *, cook: CookSession, message_id: int) -> None:
    cook.message_id = message_id
    session.add(cook)
    session.commit()


def accrue_cost(session: Session, *, cook: CookSession, add_micros: Optional[int]) -> None:
    if not add_micros:
        return
    cook.llm_cost_micros_usd = (cook.llm_cost_micros_usd or 0) + add_micros
    session.add(cook)
    session.commit()


def mark_status(session: Session, *, cook: CookSession, status: str) -> None:
    cook.status = status
    session.add(cook)
    session.commit()


def sweep_expired_cooks(session: Session, *, now: datetime) -> int:
    now = utc_naive(now)
    rows = list(session.exec(
        select(CookSession).where(
            CookSession.status.in_(("collecting", "ready")),  # type: ignore[attr-defined]
            CookSession.expires_at < now,
        )
    ).all())
    for row in rows:
        row.status = "expired"
        session.add(row)
    if rows:
        session.commit()
    return len(rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cook_session.py -k "supersede or accrue or sweep" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/cook_session_service.py tests/test_cook_session.py
git commit -m "feat(cook): CookSession lifecycle service"
```

---

## PHASE 3 — Pipeline schemas, LLM stages, and pure logic

### Task 9: Pipeline Pydantic schemas

**Files:**
- Create: `app/cook_models.py`
- Test: `tests/test_cook_pipeline.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cook_pipeline.py
from app.cook_models import (
    RecipeCandidate, RecipeIngredient, NutritionScore, ScoredCandidate,
)


def test_recipe_candidate_validates():
    c = RecipeCandidate(
        title="Tomato Pasta", cuisine="italian", source_url="https://x/y",
        ingredients=[RecipeIngredient(name="tomato", qty=2, unit="ct"),
                     RecipeIngredient(name="pasta")],
        method_gist="Boil pasta, make sauce.", deliciousness=0.8,
    )
    assert c.ingredients[1].qty is None
    assert 0.0 <= c.deliciousness <= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cook_pipeline.py::test_recipe_candidate_validates -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# app/cook_models.py
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Effort = Literal["easy", "medium", "hard"]


class SelectedItems(BaseModel):
    item_ids: list[int]
    rationale: str = ""


class RecipeIngredient(BaseModel):
    name: str
    qty: Optional[float] = None
    unit: Optional[str] = None


class RecipeCandidate(BaseModel):
    title: str
    cuisine: str
    source_url: Optional[str] = None
    ingredients: list[RecipeIngredient]
    method_gist: str
    deliciousness: float = Field(ge=0.0, le=1.0, default=0.5)


class RecipeCandidates(BaseModel):
    candidates: list[RecipeCandidate]


class NutritionScore(BaseModel):
    health_score: int = Field(ge=0, le=100)
    effort: Effort
    est_minutes: int = Field(ge=1, le=600)
    rationale: str


class NutritionScores(BaseModel):
    scores: list[NutritionScore]


class ScoredCandidate(BaseModel):
    recipe: RecipeCandidate
    nutrition: NutritionScore
    expiry_use: float = Field(ge=0.0, le=1.0)
    final_score: float
    shopping_list: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cook_pipeline.py::test_recipe_candidate_validates -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/cook_models.py tests/test_cook_pipeline.py
git commit -m "feat(cook): pipeline Pydantic schemas"
```

---

### Task 10: Pure logic — allergy filter, expiry-utilization, blend, shopping diff

All deterministic; the highest-value tests in the feature.

**Files:**
- Create: `app/cook_logic.py`
- Test: `tests/test_cook_pipeline.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_cook_pipeline.py
from app.cook_logic import (
    BLEND_WEIGHTS, violates_exclusions, expiry_utilization,
    blended_score, shopping_list,
)


def test_violates_exclusions_matches_normalized_substring():
    assert violates_exclusions(["peanut butter", "jam"], exclusions=["peanut"])
    assert not violates_exclusions(["almond butter"], exclusions=["peanut"])


def test_expiry_utilization_fraction_of_urgent_items_used():
    # urgent item names: tomato (1d), spinach (2d); recipe uses tomato only
    used = expiry_utilization(
        recipe_names=["tomato", "pasta"],
        urgent_names=["tomato", "spinach"],
    )
    assert used == 0.5


def test_blended_score_weights():
    score = blended_score(health_0_1=1.0, expiry_use=0.0, deliciousness=0.0)
    assert abs(score - BLEND_WEIGHTS["health"]) < 1e-9


def test_shopping_list_excludes_pantry_items():
    missing = shopping_list(
        recipe_names=["tomato", "pasta", "basil"],
        pantry_normalized=["tomato", "basil"],
    )
    assert missing == ["pasta"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cook_pipeline.py -k "exclusions or expiry or blended or shopping" -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# app/cook_logic.py
from __future__ import annotations

from app.normalization import normalize

BLEND_WEIGHTS = {"health": 0.4, "expiry": 0.4, "deliciousness": 0.2}


def violates_exclusions(ingredient_names, *, exclusions) -> bool:
    norm_excl = [normalize(e) for e in exclusions if e.strip()]
    for raw in ingredient_names:
        n = normalize(raw)
        for ex in norm_excl:
            if ex and ex in n:
                return True
    return False


def expiry_utilization(*, recipe_names, urgent_names) -> float:
    if not urgent_names:
        return 0.0
    recipe_norm = {normalize(n) for n in recipe_names}
    used = sum(1 for u in urgent_names if normalize(u) in recipe_norm)
    return used / len(urgent_names)


def blended_score(*, health_0_1: float, expiry_use: float, deliciousness: float) -> float:
    return (
        BLEND_WEIGHTS["health"] * health_0_1
        + BLEND_WEIGHTS["expiry"] * expiry_use
        + BLEND_WEIGHTS["deliciousness"] * deliciousness
    )


def shopping_list(*, recipe_names, pantry_normalized) -> list[str]:
    have = {normalize(n) for n in pantry_normalized}
    missing: list[str] = []
    seen: set[str] = set()
    for raw in recipe_names:
        n = normalize(raw)
        if n in have or n in seen:
            continue
        seen.add(n)
        missing.append(raw)
    return missing
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cook_pipeline.py -k "exclusions or expiry or blended or shopping" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/cook_logic.py tests/test_cook_pipeline.py
git commit -m "feat(cook): pure logic for filter, expiry-use, blend, shopping diff"
```

---

### Task 11: The three pipeline LLM clients + fakes

Each is a Protocol with Anthropic + OpenAI implementations and a fake. They follow the structured-output patterns already in `app/llm.py` (`_create_message`/`_extract_tool_input` for Anthropic, `responses.parse`/`_extract_openai_parsed` for OpenAI). Web search uses the existing tool configs.

**Files:**
- Create: `app/cook_llm.py`
- Modify: `tests/fakes.py`
- Test: `tests/test_cook_pipeline.py`

- [ ] **Step 1: Write the failing fake test**

```python
# add to tests/test_cook_pipeline.py
import asyncio

from app.cook_models import (
    SelectedItems, RecipeCandidate, RecipeIngredient, RecipeCandidates,
    NutritionScore, NutritionScores,
)
from tests.fakes import FakeSelectionLLM, FakeRecipeLLM, FakeNutritionLLM


def test_fakes_return_canned():
    sel = FakeSelectionLLM(canned=(SelectedItems(item_ids=[1, 2]), 5))
    rec = FakeRecipeLLM(canned=(RecipeCandidates(candidates=[]), 9))
    nut = FakeNutritionLLM(canned=(NutritionScores(scores=[]), 3))
    assert asyncio.run(sel.select_items(prompt="x"))[0].item_ids == [1, 2]
    assert asyncio.run(rec.fetch_recipes(prompt="x"))[1] == 9
    assert asyncio.run(nut.score(prompt="x"))[1] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cook_pipeline.py::test_fakes_return_canned -v`
Expected: FAIL (fakes undefined).

- [ ] **Step 3: Implement protocols + clients**

```python
# app/cook_llm.py
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Protocol

from app.cook_models import (
    NutritionScores, RecipeCandidates, SelectedItems,
)
from app.llm import _extract_openai_parsed, _OPENAI_REASONING, _OPENAI_WEB_SEARCH_TOOL

log = logging.getLogger(__name__)

SELECTION_SYSTEM_PROMPT = """You choose which pantry items to cook with. You get a
JSON list of candidate items (id, name, category, days_to_expiry) plus the user's
meal_type and food profile. Choose a coherent set for a single healthy, delicious
dish. Prefer items expiring soon, but NEVER include an item just because it expires
soon if it would make a bad dish. Respect the meal_type (e.g. fruit is fine for a
dessert/snack, usually not a savoury main). Return ONLY JSON: {"item_ids":[int],
"rationale": string}."""

RECIPE_SYSTEM_PROMPT = """You are a recipe finder. Given chosen ingredients, a
cuisine, a meal_type, and the user's food profile (including hard "avoid"
ingredients), return THREE distinct recipes. Use web search to find real recipes
and include a source_url. NEVER use any avoided ingredient. Return ONLY JSON
matching: {"candidates":[{"title","cuisine","source_url","ingredients":[{"name",
"qty","unit"}],"method_gist","deliciousness":0..1}]} (exactly 3 candidates)."""

NUTRITION_SYSTEM_PROMPT = """You are a nutrition expert. For each recipe candidate,
score it. Return ONLY JSON matching: {"scores":[{"health_score":0..100,
"effort":"easy|medium|hard","est_minutes":int,"rationale":string}]} with one entry
per candidate, in the same order."""


class SelectionLLMClient(Protocol):
    async def select_items(self, *, prompt: str) -> tuple[SelectedItems, Optional[int]]: ...


class RecipeLLMClient(Protocol):
    async def fetch_recipes(self, *, prompt: str) -> tuple[RecipeCandidates, Optional[int]]: ...


class NutritionLLMClient(Protocol):
    async def score(self, *, prompt: str) -> tuple[NutritionScores, Optional[int]]: ...
```

Add Anthropic + OpenAI implementations. They reuse the existing helpers; mirror `AnthropicTextLLMClient._create_message` (tool-free, parse JSON text) and `OpenAITextLLMClient._create_response` (`responses.parse`). For the **recipe** client, enable web search: Anthropic adds `tools=[{"type":"web_search_20250305","name":"web_search","max_uses":3}]`; OpenAI already passes `_OPENAI_WEB_SEARCH_TOOL`. Concretely:

```python
# app/cook_llm.py  (continued)
import json

from app.cook_models import NutritionScores, RecipeCandidates, SelectedItems
from app.llm import _cost_micros, _extract_json_text


class _AnthropicJSONClient:
    """Shared Anthropic structured-text call with retry + cost, schema-validated."""
    def __init__(self, sdk, model: str, *, web_search: bool = False, sleep=asyncio.sleep):
        self._sdk = sdk
        self._model = model
        self._web_search = web_search
        self._sleep = sleep

    async def _call(self, system: str, user_text: str, model_cls):
        tools = ([{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
                 if self._web_search else [])
        last_exc = None
        for attempt in range(3):
            try:
                msg = await self._sdk.messages.create(
                    model=self._model, max_tokens=2048, system=system,
                    tools=tools,
                    messages=[{"role": "user", "content": user_text}],
                )
                parsed = model_cls.model_validate(json.loads(_extract_json_text(msg)))
                return parsed, _cost_micros(msg, self._model)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt == 2:
                    raise
                await self._sleep(2 ** attempt)
        raise last_exc  # unreachable


class AnthropicSelectionLLM(SelectionLLMClient):
    def __init__(self, sdk, model, sleep=asyncio.sleep):
        self._c = _AnthropicJSONClient(sdk, model, web_search=False, sleep=sleep)
    async def select_items(self, *, prompt):
        return await self._c._call(SELECTION_SYSTEM_PROMPT, prompt, SelectedItems)


class AnthropicRecipeLLM(RecipeLLMClient):
    def __init__(self, sdk, model, sleep=asyncio.sleep):
        self._c = _AnthropicJSONClient(sdk, model, web_search=True, sleep=sleep)
    async def fetch_recipes(self, *, prompt):
        return await self._c._call(RECIPE_SYSTEM_PROMPT, prompt, RecipeCandidates)


class AnthropicNutritionLLM(NutritionLLMClient):
    def __init__(self, sdk, model, sleep=asyncio.sleep):
        self._c = _AnthropicJSONClient(sdk, model, web_search=False, sleep=sleep)
    async def score(self, *, prompt):
        return await self._c._call(NUTRITION_SYSTEM_PROMPT, prompt, NutritionScores)


class _OpenAIJSONClient:
    def __init__(self, sdk, model, *, web_search=False, sleep=asyncio.sleep):
        self._sdk = sdk
        self._model = model
        self._web_search = web_search
        self._sleep = sleep

    async def _call(self, system, user_text, model_cls):
        tools = [_OPENAI_WEB_SEARCH_TOOL] if self._web_search else []
        for attempt in range(3):
            try:
                resp = await self._sdk.responses.parse(
                    model=self._model,
                    input=[{"role": "system", "content": system},
                           {"role": "user", "content": [{"type": "input_text", "text": user_text}]}],
                    tools=tools, reasoning=_OPENAI_REASONING,
                    text_format=model_cls, max_output_tokens=2048,
                )
                return model_cls.model_validate(_extract_openai_parsed(resp)), None
            except Exception:  # noqa: BLE001
                if attempt == 2:
                    raise
                await self._sleep(2 ** attempt)
        raise RuntimeError("unreachable")


class OpenAISelectionLLM(SelectionLLMClient):
    def __init__(self, sdk, model, sleep=asyncio.sleep):
        self._c = _OpenAIJSONClient(sdk, model, web_search=False, sleep=sleep)
    async def select_items(self, *, prompt):
        return await self._c._call(SELECTION_SYSTEM_PROMPT, prompt, SelectedItems)


class OpenAIRecipeLLM(RecipeLLMClient):
    def __init__(self, sdk, model, sleep=asyncio.sleep):
        self._c = _OpenAIJSONClient(sdk, model, web_search=True, sleep=sleep)
    async def fetch_recipes(self, *, prompt):
        return await self._c._call(RECIPE_SYSTEM_PROMPT, prompt, RecipeCandidates)


class OpenAINutritionLLM(NutritionLLMClient):
    def __init__(self, sdk, model, sleep=asyncio.sleep):
        self._c = _OpenAIJSONClient(sdk, model, web_search=False, sleep=sleep)
    async def score(self, *, prompt):
        return await self._c._call(NUTRITION_SYSTEM_PROMPT, prompt, NutritionScores)
```

- [ ] **Step 4: Add fakes to `tests/fakes.py`**

```python
# tests/fakes.py
from app.cook_models import NutritionScores, RecipeCandidates, SelectedItems  # add imports


@dataclass
class FakeSelectionLLM:
    canned: Optional[tuple[SelectedItems, Optional[int]]] = None
    raise_n_times: int = 0
    _raises: int = 0
    calls: list[str] = field(default_factory=list)
    async def select_items(self, *, prompt):
        self.calls.append(prompt)
        if self._raises < self.raise_n_times:
            self._raises += 1
            raise RuntimeError("simulated selection failure")
        assert self.canned is not None
        return self.canned


@dataclass
class FakeRecipeLLM:
    canned: Optional[tuple[RecipeCandidates, Optional[int]]] = None
    canned_sequence: Optional[list[tuple[RecipeCandidates, Optional[int]]]] = None
    raise_n_times: int = 0
    _raises: int = 0
    calls: list[str] = field(default_factory=list)
    async def fetch_recipes(self, *, prompt):
        self.calls.append(prompt)
        if self._raises < self.raise_n_times:
            self._raises += 1
            raise RuntimeError("simulated recipe failure")
        if self.canned_sequence:
            return self.canned_sequence.pop(0)
        assert self.canned is not None
        return self.canned


@dataclass
class FakeNutritionLLM:
    canned: Optional[tuple[NutritionScores, Optional[int]]] = None
    raise_n_times: int = 0
    _raises: int = 0
    calls: list[str] = field(default_factory=list)
    async def score(self, *, prompt):
        self.calls.append(prompt)
        if self._raises < self.raise_n_times:
            self._raises += 1
            raise RuntimeError("simulated nutrition failure")
        assert self.canned is not None
        return self.canned
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_cook_pipeline.py::test_fakes_return_canned -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/cook_llm.py tests/fakes.py tests/test_cook_pipeline.py
git commit -m "feat(cook): selection/recipe/nutrition LLM clients + fakes"
```

---

## PHASE 4 — Orchestration

### Task 12: `cook_service.run_cook` — the pipeline

Ties stages together with the failure policy: min-items guard, hard allergy filter, regenerate-once on wipeout, blend/rank, lazy shopping list for the top pick. Accrues cost on the `CookSession`. Pure-ish: takes already-selected `CookSession` (meal_type/cuisine filled) and the three LLM clients + search-derived urgent set.

**Files:**
- Create: `app/cook_service.py`
- Test: `tests/test_cook_pipeline.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_cook_pipeline.py
from datetime import date, datetime, timedelta, timezone

from sqlmodel import SQLModel, Session, create_engine

from app.models import CookSession, PantryItem, User
from app.cook_models import RecipeCandidate, RecipeIngredient, RecipeCandidates, NutritionScore, NutritionScores, SelectedItems
from app.cook_service import run_cook, MIN_USABLE_ITEMS, NotEnoughItems
from app.profile_service import FoodProfile
from tests.fakes import FakeSelectionLLM, FakeRecipeLLM, FakeNutritionLLM


def _db_with_items(n, expiry_days):
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    db = Session(engine)
    db.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
    today = date(2026, 5, 30)
    for i in range(n):
        db.add(PantryItem(
            user_id=1, raw_name=f"item{i}", normalized_name=f"item{i}",
            category="produce", qty=1.0, purchased_on=today,
            shelf_life_days=expiry_days, shelf_life_source="llm",
            ingest_shelf_life_source="llm",
            expires_on=today + timedelta(days=expiry_days),
            status="active", created_via="receipt", created_at=datetime.now(timezone.utc),
        ))
    db.commit()
    return db, today


def _cook_row(db):
    now = datetime(2026, 5, 30, 12, 0).replace(tzinfo=None)
    row = CookSession(user_id=1, status="ready", chat_id=1, meal_type="dinner",
                      cuisine="italian", selected_item_ids="[]",
                      created_at=now, expires_at=now + timedelta(minutes=10))
    db.add(row); db.commit(); db.refresh(row)
    return row


def test_run_cook_guards_thin_pantry():
    import asyncio, pytest
    db, today = _db_with_items(MIN_USABLE_ITEMS - 1, 2)
    cook = _cook_row(db)
    with pytest.raises(NotEnoughItems):
        asyncio.run(run_cook(
            db, cook=cook, profile=FoodProfile(),
            selection_llm=FakeSelectionLLM(canned=(SelectedItems(item_ids=[]), 0)),
            recipe_llm=FakeRecipeLLM(canned=(RecipeCandidates(candidates=[]), 0)),
            nutrition_llm=FakeNutritionLLM(canned=(NutritionScores(scores=[]), 0)),
            today=today,
        ))


def test_run_cook_ranks_and_filters_allergens():
    import asyncio
    db, today = _db_with_items(4, 2)
    cook = _cook_row(db)
    ids = [r.id for r in db.exec(__import__("sqlmodel").select(PantryItem)).all()]
    candidates = RecipeCandidates(candidates=[
        RecipeCandidate(title="Peanut Dish", cuisine="thai", source_url="u",
                        ingredients=[RecipeIngredient(name="peanut")],
                        method_gist="x", deliciousness=0.9),
        RecipeCandidate(title="Safe Dish", cuisine="italian", source_url="u",
                        ingredients=[RecipeIngredient(name="item0"), RecipeIngredient(name="pasta")],
                        method_gist="y", deliciousness=0.5),
    ])
    scores = NutritionScores(scores=[
        NutritionScore(health_score=90, effort="easy", est_minutes=20, rationale="a"),
        NutritionScore(health_score=80, effort="easy", est_minutes=25, rationale="b"),
    ])
    result = asyncio.run(run_cook(
        db, cook=cook, profile=FoodProfile(exclusions=["peanut"]),
        selection_llm=FakeSelectionLLM(canned=(SelectedItems(item_ids=ids), 5)),
        recipe_llm=FakeRecipeLLM(canned=(candidates, 9)),
        nutrition_llm=FakeNutritionLLM(canned=(scores, 3)),
        today=today,
    ))
    assert [c.recipe.title for c in result] == ["Safe Dish"]  # peanut dish filtered out
    assert "pasta" in result[0].shopping_list
    db.refresh(cook)
    assert cook.llm_cost_micros_usd == 17  # 5 + 9 + 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cook_pipeline.py -k run_cook -v`
Expected: FAIL (`app.cook_service` missing).

- [ ] **Step 3: Implement**

```python
# app/cook_service.py
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.cook_logic import (
    blended_score, expiry_utilization, shopping_list, violates_exclusions,
)
from app.cook_llm import NutritionLLMClient, RecipeLLMClient, SelectionLLMClient
from app.cook_models import RecipeCandidate, ScoredCandidate
from app.cook_session_service import accrue_cost
from app.models import CookSession, PantryItem
from app.pantry_service import list_active, ListFilter
from app.profile_service import FoodProfile

log = logging.getLogger(__name__)

MIN_USABLE_ITEMS = 3
URGENT_DAYS = 5


class NotEnoughItems(Exception):
    pass


def _ingredient_names(recipe: RecipeCandidate) -> list[str]:
    return [i.name for i in recipe.ingredients]


async def run_cook(
    session: Session,
    *,
    cook: CookSession,
    profile: FoodProfile,
    selection_llm: SelectionLLMClient,
    recipe_llm: RecipeLLMClient,
    nutrition_llm: NutritionLLMClient,
    today: date,
) -> list[ScoredCandidate]:
    active = list_active(session, user_id=cook.user_id, f=ListFilter.default(), today=today)
    if len(active) < MIN_USABLE_ITEMS:
        raise NotEnoughItems()

    candidate_items = [
        {"id": i.id, "name": i.raw_name, "category": i.category,
         "days_to_expiry": (i.expires_on - today).days}
        for i in active
    ]
    sel_prompt = json.dumps({
        "items": candidate_items, "meal_type": cook.meal_type,
        "profile": profile.model_dump(),
    })
    selected, c1 = await selection_llm.select_items(prompt=sel_prompt)
    accrue_cost(session, cook=cook, add_micros=c1)

    chosen = [i for i in active if i.id in set(selected.item_ids)] or active
    cook.selected_item_ids = json.dumps([i.id for i in chosen])
    session.add(cook); session.commit()

    urgent_names = [i.raw_name for i in chosen if (i.expires_on - today).days <= URGENT_DAYS]
    recipe_prompt = json.dumps({
        "ingredients": [i.raw_name for i in chosen],
        "cuisine": cook.cuisine, "meal_type": cook.meal_type,
        "profile": profile.model_dump(),
    })

    candidates, c2 = await recipe_llm.fetch_recipes(prompt=recipe_prompt)
    accrue_cost(session, cook=cook, add_micros=c2)

    safe = [c for c in candidates.candidates
            if not violates_exclusions(_ingredient_names(c), exclusions=profile.exclusions)]
    if not safe:
        retry_prompt = json.dumps({
            "ingredients": [i.raw_name for i in chosen], "cuisine": cook.cuisine,
            "meal_type": cook.meal_type, "profile": profile.model_dump(),
            "must_avoid": profile.exclusions,
        })
        candidates, c2b = await recipe_llm.fetch_recipes(prompt=retry_prompt)
        accrue_cost(session, cook=cook, add_micros=c2b)
        safe = [c for c in candidates.candidates
                if not violates_exclusions(_ingredient_names(c), exclusions=profile.exclusions)]
    if not safe:
        return []

    nut_prompt = json.dumps({
        "candidates": [c.model_dump() for c in safe],
    })
    scores, c3 = await nutrition_llm.score(prompt=nut_prompt)
    accrue_cost(session, cook=cook, add_micros=c3)

    pantry_norm = [i.normalized_name for i in active]
    scored: list[ScoredCandidate] = []
    for idx, recipe in enumerate(safe):
        nutrition = scores.scores[idx] if idx < len(scores.scores) else None
        if nutrition is None:
            continue
        eu = expiry_utilization(recipe_names=_ingredient_names(recipe), urgent_names=urgent_names)
        final = blended_score(
            health_0_1=nutrition.health_score / 100.0,
            expiry_use=eu, deliciousness=recipe.deliciousness,
        )
        scored.append(ScoredCandidate(
            recipe=recipe, nutrition=nutrition, expiry_use=eu, final_score=final,
            shopping_list=[],
        ))

    scored.sort(key=lambda s: s.final_score, reverse=True)
    if scored:  # lazy shopping list for the top pick only
        top = scored[0]
        top.shopping_list = shopping_list(
            recipe_names=_ingredient_names(top.recipe), pantry_normalized=pantry_norm,
        )
    cook.candidates_json = json.dumps([s.model_dump() for s in scored])
    cook.chosen_index = 0
    session.add(cook); session.commit()
    return scored
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cook_pipeline.py -k run_cook -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/cook_service.py tests/test_cook_pipeline.py
git commit -m "feat(cook): run_cook pipeline orchestration with failure policy"
```

---

## PHASE 5 — Telegram surface

### Task 13: Renderer for cook cards + keyboards

**Files:**
- Modify: `app/renderer.py`
- Test: `tests/test_cook_render.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cook_render.py
from app.cook_models import (
    RecipeCandidate, RecipeIngredient, NutritionScore, ScoredCandidate,
)
from app.renderer import render_cook_result


def _scored(title, n_alts=0):
    rec = RecipeCandidate(title=title, cuisine="italian", source_url="https://x",
                          ingredients=[RecipeIngredient(name="pasta")],
                          method_gist="boil", deliciousness=0.7)
    nut = NutritionScore(health_score=80, effort="easy", est_minutes=20, rationale="ok")
    return ScoredCandidate(recipe=rec, nutrition=nut, expiry_use=0.5,
                           final_score=0.7, shopping_list=["pasta"])


def test_render_cook_result_shows_top_pick_and_shopping():
    text = render_cook_result([_scored("Top"), _scored("Alt")], show_alternatives=False)
    assert "Top" in text
    assert "80" in text          # health score
    assert "20 min" in text
    assert "pasta" in text       # shopping list
    assert "Alt" not in text     # hidden until expanded


def test_render_cook_result_expanded_shows_alternatives():
    text = render_cook_result([_scored("Top"), _scored("Alt")], show_alternatives=True)
    assert "Alt" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cook_render.py -v`
Expected: FAIL (`render_cook_result` undefined).

- [ ] **Step 3: Implement renderer + keyboard**

```python
# app/renderer.py  (append; ScoredCandidate import at top)
from app.cook_models import ScoredCandidate


def _render_card(card: ScoredCandidate, *, rank: int) -> str:
    r = card.recipe
    n = card.nutrition
    header = f"{'⭐ ' if rank == 0 else ''}{r.title} ({r.cuisine})"
    lines = [
        header,
        f"  Health {n.health_score}/100 · {n.effort} · {n.est_minutes} min",
        f"  {r.method_gist}",
    ]
    if r.source_url:
        lines.append(f"  Recipe: {r.source_url}")
    if rank == 0 and card.shopping_list:
        lines.append("  Need to buy: " + ", ".join(card.shopping_list))
    elif rank == 0:
        lines.append("  Need to buy: nothing - you have it all!")
    return "\n".join(lines)


def render_cook_result(cards: list[ScoredCandidate], *, show_alternatives: bool) -> str:
    if not cards:
        return "Couldn't find a recipe that fits your pantry and restrictions."
    blocks = [_render_card(cards[0], rank=0)]
    if show_alternatives:
        for idx, card in enumerate(cards[1:], start=1):
            blocks.append(_render_card(card, rank=idx))
    return "\n\n".join(blocks)


def build_cook_alternatives_keyboard(cook_id: int) -> list[list[CallbackButton]]:
    return [[CallbackButton(text="Show alternatives", callback_data=f"cookalt:{cook_id}")]]


def build_cook_round_keyboard(cook_id: int, options: list[str]) -> list[list[CallbackButton]]:
    return [[CallbackButton(text=o, callback_data=f"cookpick:{cook_id}:{i}")]
            for i, o in enumerate(options)]
```

> NOTE: `CallbackButton` already exists in `app/renderer.py` (used by `build_apply_cancel_keyboard`). Confirm its fields are `text` / `callback_data` before using.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cook_render.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/renderer.py tests/test_cook_render.py
git commit -m "feat(cook): renderer for recipe cards and keyboards"
```

---

### Task 14: Extend callback parsing for cook rounds + alternatives

**Files:**
- Modify: `app/commands.py` (`Verb`, `CallbackAction`, `parse_callback`)
- Test: `tests/test_cook_render.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_cook_render.py
from app.commands import parse_callback


def test_parse_cook_callbacks():
    pick = parse_callback("cookpick:7:2")
    assert pick.verb == "cook_pick" and pick.item_id == 7 and pick.option_index == 2
    alt = parse_callback("cookalt:7")
    assert alt.verb == "cook_alt" and alt.item_id == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cook_render.py::test_parse_cook_callbacks -v`
Expected: FAIL (`AttributeError: option_index` / unknown callback).

- [ ] **Step 3: Implement**

In `app/commands.py`: extend `Verb`, add `option_index` to `CallbackAction` (default `None`), and branches in `parse_callback`:

```python
Verb = Literal[
    "ate", "toss", "snooze2", "show_all", "apply", "cancel",
    "undo_receipt", "undo_add", "cook_pick", "cook_alt",
]


@dataclass(frozen=True)
class CallbackAction:
    verb: Verb
    item_id: Optional[int]
    option_index: Optional[int] = None
```

```python
# inside parse_callback, before the final `parts = data.split(":")` fallback:
    if data.startswith("cookalt:"):
        _, _, raw_id = data.partition(":")
        try:
            cook_id = int(raw_id)
        except ValueError as exc:
            raise CommandError(f"bad cook id {raw_id!r}") from exc
        return CallbackAction(verb="cook_alt", item_id=cook_id)
    if data.startswith("cookpick:"):
        _, raw_id, raw_idx = data.split(":")
        try:
            return CallbackAction(verb="cook_pick", item_id=int(raw_id),
                                  option_index=int(raw_idx))
        except ValueError as exc:
            raise CommandError(f"bad cookpick data {data!r}") from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cook_render.py::test_parse_cook_callbacks -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/commands.py tests/test_cook_render.py
git commit -m "feat(cook): parse cook round + alternatives callbacks"
```

---

### Task 15: `/cook` handler — round flow + async pipeline + edit

This is the integration glue. It mirrors `handle_photo`'s `spawn`/`edit_message_text` pattern (`app/bot.py:773-798`) and `handle_correct`'s round flow. Round options come from the profile + a small LLM-free default for round 1; cuisine round leads with `profile.preferred_cuisines`.

**Files:**
- Modify: `app/bot.py` (add `handle_cook`, extend `handle_callback` for `cook_pick`/`cook_alt`, register `/cook`)
- Test: `tests/test_cook_bot.py` (create)

- [ ] **Step 1: Write the failing test (first round posts meal-type buttons)**

```python
# tests/test_cook_bot.py
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from sqlmodel import SQLModel, Session, create_engine

import app.bot as bot_mod
from app.bot import handle_cook
from app.models import CookSession, User


def _engine_with_user():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        db.commit()
    return engine


class _Msg:
    def __init__(self, text="/cook", user_id=1, chat_id=1):
        self.text = text
        self.from_user = type("U", (), {"id": user_id})
        self.chat = type("C", (), {"id": chat_id, "type": "private"})
        self.answer = AsyncMock(return_value=type("S", (), {"message_id": 99}))


def test_cook_first_round_creates_session_and_asks_meal_type(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    msg = _Msg()
    asyncio.run(handle_cook(
        msg, session_factory=lambda: Session(engine),
        now_provider=lambda tz: datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
    ))
    msg.answer.assert_awaited()
    # a collecting CookSession exists
    with Session(engine) as db:
        rows = db.exec(__import__("sqlmodel").select(CookSession)).all()
        assert len(rows) == 1 and rows[0].status == "collecting"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cook_bot.py::test_cook_first_round_creates_session_and_asks_meal_type -v`
Expected: FAIL (`handle_cook` undefined).

- [ ] **Step 3: Implement `handle_cook` (round 1 only first)**

```python
# app/bot.py
from app.cook_session_service import create_cook_session, set_message_id as set_cook_message_id
from app.renderer import build_cook_round_keyboard

MEAL_TYPES = ["Dinner", "Lunch", "Breakfast", "Dessert", "Snack"]


async def handle_cook(
    msg, *, session_factory, now_provider,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    with session_factory() as session:
        user = await _guard(msg, session, on_user_created=on_user_created)
        if user is None:
            return
        now = now_provider(user.tz)
        cook = create_cook_session(
            session, user_id=user.telegram_id, chat_id=msg.chat.id,
            now=now.astimezone(timezone.utc),
        )
        assert cook.id is not None
        keyboard = to_aiogram_keyboard(build_cook_round_keyboard(cook.id, MEAL_TYPES))
        sent = await msg.answer("What are you cooking?", reply_markup=keyboard)
        set_cook_message_id(session, cook=cook, message_id=sent.message_id)
```

Register in `build_dispatcher` (mirror `on_list`):

```python
    async def on_cook(message):
        await handle_cook(message, session_factory=session_factory,
                          now_provider=now_provider, on_user_created=on_user_created)
    dispatcher.message.register(on_cook, Command("cook"))
```

- [ ] **Step 4: Run round-1 test to verify it passes**

Run: `uv run pytest tests/test_cook_bot.py::test_cook_first_round_creates_session_and_asks_meal_type -v`
Expected: PASS. Commit here:

```bash
git add app/bot.py tests/test_cook_bot.py
git commit -m "feat(cook): /cook round 1 (meal-type selection)"
```

- [ ] **Step 5: Write the failing test for `cook_pick` advancing rounds + running the pipeline**

```python
# add to tests/test_cook_bot.py
from app.bot import handle_cook_callback
from app.cook_models import (
    RecipeCandidate, RecipeIngredient, RecipeCandidates, NutritionScore, NutritionScores, SelectedItems,
)
from app.models import PantryItem
from datetime import date, timedelta
from tests.fakes import FakeSelectionLLM, FakeRecipeLLM, FakeNutritionLLM


class _Cb:
    def __init__(self, data, user_id=1, chat_id=1, message_id=99):
        self.data = data
        self.from_user = type("U", (), {"id": user_id})
        self.message = type("M", (), {
            "chat": type("C", (), {"id": chat_id}),
            "edit_text": AsyncMock(), "answer": AsyncMock(),
        })()
        self.answer = AsyncMock()


def test_cook_pick_cuisine_then_runs_pipeline(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    today = date(2026, 5, 30)
    with Session(engine) as db:
        for i in range(4):
            db.add(PantryItem(
                user_id=1, raw_name=f"item{i}", normalized_name=f"item{i}",
                category="produce", qty=1.0, purchased_on=today, shelf_life_days=2,
                shelf_life_source="llm", ingest_shelf_life_source="llm",
                expires_on=today + timedelta(days=2), status="active",
                created_via="receipt", created_at=datetime.now(timezone.utc)))
        db.commit()
    # round 1 to create the session + record meal_type
    msg = _Msg()
    asyncio.run(handle_cook(msg, session_factory=lambda: Session(engine),
                            now_provider=lambda tz: datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)))
    with Session(engine) as db:
        cook_id = db.exec(__import__("sqlmodel").select(CookSession)).all()[0].id

    selection = FakeSelectionLLM(canned=(SelectedItems(item_ids=[]), 5))
    recipe = FakeRecipeLLM(canned=(RecipeCandidates(candidates=[
        RecipeCandidate(title="Safe", cuisine="italian", source_url="u",
                        ingredients=[RecipeIngredient(name="item0"), RecipeIngredient(name="pasta")],
                        method_gist="x", deliciousness=0.6)]), 9))
    nutrition = FakeNutritionLLM(canned=(NutritionScores(scores=[
        NutritionScore(health_score=80, effort="easy", est_minutes=20, rationale="ok")]), 3))

    # pick meal-type (option 0), then cuisine (option 0)
    cb_meal = _Cb(f"cookpick:{cook_id}:0")
    asyncio.run(handle_cook_callback(
        cb_meal, session_factory=lambda: Session(engine),
        now_provider=lambda tz: datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
        selection_llm=selection, recipe_llm=recipe, nutrition_llm=nutrition,
        spawn=lambda coro: asyncio.get_event_loop().run_until_complete(coro), bot=None,
    ))
    cb_cuisine = _Cb(f"cookpick:{cook_id}:0")
    asyncio.run(handle_cook_callback(
        cb_cuisine, session_factory=lambda: Session(engine),
        now_provider=lambda tz: datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
        selection_llm=selection, recipe_llm=recipe, nutrition_llm=nutrition,
        spawn=lambda coro: asyncio.get_event_loop().run_until_complete(coro), bot=None,
    ))
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook.status == "done"
        assert "Safe" in (cook.candidates_json or "")
```

> NOTE: `spawn` is injected (like `handle_photo`'s `spawn=asyncio.create_task`) so the test can run the pipeline synchronously. Round-tracking: a session with `meal_type is None` is on round 1; with `meal_type` set but `cuisine is None` is on round 2; once `cuisine` is set, run the pipeline.

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_cook_bot.py::test_cook_pick_cuisine_then_runs_pipeline -v`
Expected: FAIL (`handle_cook_callback` undefined).

- [ ] **Step 7: Implement `handle_cook_callback` + cuisine options + pipeline kick-off**

```python
# app/bot.py
import json as _json

from app.cook_models import ScoredCandidate
from app.cook_service import NotEnoughItems, run_cook
from app.cook_session_service import load_cook_session, mark_status
from app.profile_service import profile_from_user
from app.renderer import (
    build_cook_alternatives_keyboard, render_cook_result,
)

DEFAULT_CUISINES = ["Italian", "Mexican", "Chinese", "American", "Surprise me"]


def _cuisine_options(user: User) -> list[str]:
    prefs = _json.loads(user.preferred_cuisines_json or "[]")
    options = [c.title() for c in prefs] if prefs else list(DEFAULT_CUISINES)
    if "Surprise me" not in options:
        options.append("Surprise me")
    return options[:5]


async def handle_cook_callback(
    cb, *, session_factory, now_provider,
    selection_llm, recipe_llm, nutrition_llm, spawn, bot,
):
    action = parse_callback(cb.data)
    with session_factory() as session:
        user = session.get(User, cb.from_user.id)
        if user is None:
            await cb.answer("not configured")
            return
        cook = load_cook_session(session, user_id=user.telegram_id, cook_id=action.item_id)
        if cook is None or cook.status not in ("collecting", "ready", "done"):
            await cb.answer("this cook session expired - start a new /cook")
            return

        if action.verb == "cook_alt":
            cards = [ScoredCandidate.model_validate(c)
                     for c in _json.loads(cook.candidates_json or "[]")]
            try:
                await cb.message.edit_text(render_cook_result(cards, show_alternatives=True))
            except Exception as exc:
                log.warning("cook_alt_edit_failed", extra={"error_class": type(exc).__name__})
            await cb.answer("showing alternatives")
            return

        # cook_pick: advance the rounds
        options_meal = MEAL_TYPES
        if cook.meal_type is None:
            cook.meal_type = options_meal[action.option_index]
            session.add(cook); session.commit()
            keyboard = to_aiogram_keyboard(
                build_cook_round_keyboard(cook.id, _cuisine_options(user)))
            try:
                await cb.message.edit_text("Which cuisine?", reply_markup=keyboard)
            except Exception as exc:
                log.warning("cook_round_edit_failed", extra={"error_class": type(exc).__name__})
            await cb.answer()
            return

        if cook.cuisine is None:
            cook.cuisine = _cuisine_options(user)[action.option_index]
            cook.status = "ready"
            session.add(cook); session.commit()
            try:
                await cb.message.edit_text("🍳 Thinking…")
            except Exception as exc:
                log.warning("cook_thinking_edit_failed", extra={"error_class": type(exc).__name__})
            await cb.answer()

        cook_id = cook.id
        profile = profile_from_user(user)
        chat_id = cb.message.chat.id

    async def _run():
        with session_factory() as s2:
            cook2 = load_cook_session(s2, user_id=cb.from_user.id, cook_id=cook_id)
            if cook2 is None or cook2.status != "ready":
                return
            today = now_provider(user.tz).date()
            try:
                cards = await run_cook(
                    s2, cook=cook2, profile=profile,
                    selection_llm=selection_llm, recipe_llm=recipe_llm,
                    nutrition_llm=nutrition_llm, today=today,
                )
            except NotEnoughItems:
                mark_status(s2, cook=cook2, status="cancelled")
                if bot is not None:
                    await bot.edit_message_text(
                        chat_id=chat_id, message_id=cook2.message_id,
                        text="Not enough usable items - send a receipt or /add a few things.")
                return
            except Exception as exc:
                log.warning("cook_pipeline_failed", extra={"error_class": type(exc).__name__})
                mark_status(s2, cook=cook2, status="cancelled")
                return
            mark_status(s2, cook=cook2, status="done")
            text = render_cook_result(cards, show_alternatives=False)
            keyboard = (to_aiogram_keyboard(build_cook_alternatives_keyboard(cook2.id))
                        if len(cards) > 1 else None)
            if bot is not None:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id, message_id=cook2.message_id,
                        text=text, reply_markup=keyboard)
                except Exception as exc:
                    log.warning("cook_result_edit_failed", extra={"error_class": type(exc).__name__})

    spawn(_run())
```

Route cook callbacks: in `handle_callback` (`app/bot.py:801`), the existing `parse_callback` now yields `cook_pick`/`cook_alt`. Add an early dispatch so they reach `handle_cook_callback`. Because `handle_callback`'s signature lacks the cook clients, register a **separate** callback handler in `build_dispatcher` filtered to cook data, OR thread the cook clients through. Simplest, lowest-risk: register a dedicated callback handler before the generic one:

```python
    async def on_cook_callback(callback):
        if not (callback.data or "").startswith(("cookpick:", "cookalt:")):
            return await handle_callback(callback, session_factory=session_factory,
                                         now_provider=now_provider)
        await handle_cook_callback(
            callback, session_factory=session_factory, now_provider=now_provider,
            selection_llm=selection_llm, recipe_llm=recipe_llm, nutrition_llm=nutrition_llm,
            spawn=asyncio.create_task, bot=bot,
        )
    dispatcher.callback_query.register(on_cook_callback)
```

Replace the existing single `dispatcher.callback_query.register(on_callback)` with `on_cook_callback` (which delegates non-cook callbacks to `handle_callback`). Add `selection_llm`, `recipe_llm`, `nutrition_llm` params to `build_dispatcher`.

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run pytest tests/test_cook_bot.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add app/bot.py tests/test_cook_bot.py
git commit -m "feat(cook): /cook round flow, pipeline kick-off, and result edit"
```

---

## PHASE 6 — Wiring, stats, sweep, help

### Task 16: Construct cook + profile clients in `run.py` and pass through

**Files:**
- Modify: `app/llm.py` add provider selectors `ProfileLLMProviderSelector`, `SelectionLLMProviderSelector`, `RecipeLLMProviderSelector`, `NutritionLLMProviderSelector` (thin, like `TextLLMProviderSelector`)
- Modify: `bin/run.py` (`_build_llm_clients` returns the new clients; pass to `build_dispatcher`)
- Test: `tests/test_cook_bot.py` (a wiring smoke test)

- [ ] **Step 1: Write the failing wiring test**

```python
# add to tests/test_cook_bot.py
from app.settings import Settings


def test_build_llm_clients_returns_cook_clients(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "1")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    from bin.run import _build_llm_clients
    bundle = _build_llm_clients(Settings())  # type: ignore[call-arg]
    # selection/recipe/nutrition/profile selectors present for anthropic
    assert bundle.selection.default_provider == "anthropic"
    assert bundle.recipe.default_provider == "anthropic"
    assert bundle.nutrition.default_provider == "anthropic"
    assert bundle.profile.default_provider == "anthropic"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cook_bot.py::test_build_llm_clients_returns_cook_clients -v`
Expected: FAIL (`_build_llm_clients` returns a 3-tuple; no `.selection`).

- [ ] **Step 3: Add selectors to `app/llm.py`**

Add four selectors modelled exactly on `TextLLMProviderSelector` (same `__init__`, `available_providers`, `default_provider`, `for_provider`), delegating the one method each Protocol defines. Example for selection (repeat the shape for recipe/nutrition/profile):

```python
# app/llm.py
class SelectionLLMProviderSelector:
    def __init__(self, clients: dict, default_provider: str):
        if default_provider not in clients:
            raise LLMProviderNotConfigured(default_provider)
        self._clients = clients
        self._default_provider = default_provider

    @property
    def available_providers(self): return tuple(sorted(self._clients))
    @property
    def default_provider(self): return self._default_provider
    def for_provider(self, provider: str):
        try: return self._clients[provider]
        except KeyError as exc: raise LLMProviderNotConfigured(provider) from exc

    async def select_items(self, *, prompt):
        return await self.for_provider(self._default_provider).select_items(prompt=prompt)
```

Recipe selector delegates `fetch_recipes`, nutrition delegates `score`, profile delegates `parse_profile_update`. (The cook callback selects the per-user client itself via `getattr(..., "for_provider")`, like `_select_text_llm_client` — apply that in `handle_cook_callback` Step before calling `run_cook`: select each client for `user.llm_provider`.)

> NOTE: update `handle_cook_callback` to select per-user clients: `selection_llm = _select(selection_llm, user.llm_provider)` etc., using a helper `_select_cook(client, provider)` identical in shape to `_select_text_llm_client`. Without this, the default-provider client is used regardless of `/llm`.

- [ ] **Step 4: Update `_build_llm_clients` + `build_dispatcher` call in `bin/run.py`**

Return a small dataclass instead of a tuple:

```python
# bin/run.py
from dataclasses import dataclass

from app.cook_llm import (
    AnthropicSelectionLLM, AnthropicRecipeLLM, AnthropicNutritionLLM,
    OpenAISelectionLLM, OpenAIRecipeLLM, OpenAINutritionLLM,
)
from app.llm import (
    AnthropicProfileLLMClient, OpenAIProfileLLMClient,
    SelectionLLMProviderSelector, RecipeLLMProviderSelector,
    NutritionLLMProviderSelector, ProfileLLMProviderSelector,
)


@dataclass
class LLMBundle:
    image: object
    text: object
    search: object
    selection: object
    recipe: object
    nutrition: object
    profile: object
```

In `_build_llm_clients`, additionally populate `selection_clients`, `recipe_clients`, `nutrition_clients`, `profile_clients` per provider (anthropic uses `settings.anthropic_model`; recipe uses `settings.anthropic_search_model` so it has web search; openai uses `settings.openai_model`). Return `LLMBundle(...)`. Update `_amain` to unpack `bundle = _build_llm_clients(settings)` and pass `selection_llm=bundle.selection, recipe_llm=bundle.recipe, nutrition_llm=bundle.nutrition, profile_llm=bundle.profile` into `build_dispatcher` alongside the existing `llm=bundle.image, text_llm=bundle.text, search=bundle.search`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_cook_bot.py::test_build_llm_clients_returns_cook_clients -v`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS (fix any signature mismatches in existing `build_dispatcher` callers/tests — search `build_dispatcher(` and add the new kwargs).

- [ ] **Step 7: Commit**

```bash
git add app/llm.py bin/run.py tests/test_cook_bot.py
git commit -m "feat(cook): construct and wire cook + profile LLM clients"
```

---

### Task 17: `/stats` cook-cost line + `/help` + cook sweep job

**Files:**
- Modify: `app/pantry_service.py` (`Stats`, `compute_stats`), `app/renderer.py` (`render_stats`)
- Modify: `app/scheduler.py` (register cook sweep), `bin/run.py` (call it)
- Modify: `app/bot.py` `HELP_TEXT` (add `/cook`)
- Test: `tests/test_cook_session.py`, `tests/test_cook_render.py`

- [ ] **Step 1: Write the failing stats test**

```python
# add to tests/test_cook_session.py
from datetime import datetime, timezone
from app.models import CookSession
from app.pantry_service import compute_stats


def test_stats_counts_cook_cost():
    with _session() as db:
        now = datetime(2026, 5, 30, 12, 0)
        db.add(CookSession(user_id=1, status="done", chat_id=1, selected_item_ids="[]",
                           llm_cost_micros_usd=500, created_at=now,
                           expires_at=now))
        db.commit()
        stats = compute_stats(db, user_id=1, now=datetime(2026, 5, 30, 13, 0, tzinfo=timezone.utc))
        assert stats.cook_cost_micros_usd == 500
        assert stats.cook_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cook_session.py::test_stats_counts_cook_cost -v`
Expected: FAIL (`Stats` has no `cook_cost_micros_usd`).

- [ ] **Step 3: Implement**

Add `cook_cost_micros_usd: int = 0` and `cook_count: int = 0` to the `Stats` dataclass (`app/pantry_service.py:181`). In `compute_stats`, after the pending rows block, query cook sessions in-window and aggregate:

```python
# app/pantry_service.py  (inside compute_stats, before the return)
    from app.models import CookSession  # local import; avoids top-level churn
    cook_rows = list(session.exec(
        select(CookSession).where(
            CookSession.user_id == user_id,
            CookSession.created_at >= since.replace(tzinfo=None),
        )
    ).all())
    cook_cost = sum(r.llm_cost_micros_usd or 0 for r in cook_rows)
```

Add `cook_cost_micros_usd=cook_cost, cook_count=len(cook_rows)` to the `Stats(...)` constructor. Add a line to `render_stats` in `app/renderer.py`: `f"Cook sessions: {stats.cook_count} (${stats.cook_cost_micros_usd/1_000_000:.3f})"`. Add a renderer assertion test in `tests/test_cook_render.py`.

- [ ] **Step 4: Register the cook sweep job**

```python
# app/scheduler.py
from app.cook_session_service import sweep_expired_cooks
from datetime import datetime, timezone


def _cook_sweep_job(session_factory) -> None:
    try:
        with session_factory() as session:
            swept = sweep_expired_cooks(session, now=datetime.now(timezone.utc))
            if swept:
                log.info("cook_swept", extra={"count": swept})
    except Exception as exc:
        log.warning("cook_sweep_failed", extra={"error_class": type(exc).__name__})


def register_sweep_expired_cooks(scheduler, *, session_factory) -> None:
    scheduler.add_job(_cook_sweep_job, "cron", minute="*/5", timezone="UTC",
                      args=[session_factory], id="sweep_expired_cooks", replace_existing=True)
```

Call `register_sweep_expired_cooks(scheduler, session_factory=session_factory)` in `bin/run.py:_amain` next to `register_sweep_expired_pendings`. Add `"  /cook - get a recipe from your pantry\n"` to `HELP_TEXT`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_cook_session.py::test_stats_counts_cook_cost tests/test_cook_render.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/pantry_service.py app/renderer.py app/scheduler.py bin/run.py app/bot.py tests/
git commit -m "feat(cook): cook-cost stats, /cook help, expired-cook sweep job"
```

---

## Self-review notes (resolved)

- **Spec coverage:** profile (Tasks 1-6) ↔ §7; CookSession state (7-8) ↔ §7; pipeline schemas/stages (9-12) ↔ §6; rendering option-B (13) ↔ §4; rounds + supersede + TTL (8, 15) ↔ §5/§8; failure policy (12, 15) ↔ §8; cost+stats (17) ↔ §9; provider-agnostic selection (16) ↔ §11; web search via existing tool configs (11) ↔ §6.
- **Deferred (non-goals, §3):** no recipe-DB API, no RAG, shopping list display-only, no dedup/history, single-provider-per-request, no free-text FSM. None are implemented — correct.
- **Type consistency:** `SelectedItems.item_ids`, `RecipeCandidate.ingredients[].name`, `ScoredCandidate.{recipe,nutrition,expiry_use,final_score,shopping_list}`, `CallbackAction.option_index`, `LLMBundle.{selection,recipe,nutrition,profile}` are used consistently across tasks. Method names: `select_items` / `fetch_recipes` / `score` / `parse_profile_update` stable from Task 11/4 through Task 16.
- **Open implementation choice deliberately left to the worker:** exact wording of system prompts (tune against real output) and blend weights (constants in `cook_logic.BLEND_WEIGHTS`).
