# `/pantry` Interactive Command — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/pantry [digest|<id>]` command that renders the same interactive inline-keyboard UI as the daily digest so the user can manage items (ate/toss/snooze/freeze/correct/remove) without knowing item IDs.

**Architecture:** View-origin context is encoded stateless in callback data (`item:open:<id>:all` / `item:list:all`). `ItemAction` gains a `back_to` field parsed by `parse_item_callback`. Two keyboard builder functions receive an optional `back_to` param (default `"digest"`, no existing callers break). A new `handle_pantry` handler and `_refresh_pantry_message` are added to `bot.py`; `handle_item_callback` is updated to route `item:list:all` to the new refresh function.

**Tech Stack:** Python, aiogram, SQLModel, pytest

---

## File Map

| File | Action | What changes |
|------|--------|--------------|
| `app/commands.py` | Modify | `ItemAction.back_to`; extend `parse_item_callback`; new `parse_pantry_arg` |
| `app/renderer.py` | Modify | `back_to` param on `build_digest_keyboard` + `build_item_card_keyboard` |
| `app/i18n.py` | Modify | `pantry.all_clear` key (4 languages) |
| `app/bot.py` | Modify | `handle_pantry`; `_refresh_pantry_message`; update `handle_item_callback`; register `/pantry` |
| `tests/test_renderer_commands.py` | Modify | Tests for new parser + keyboard callbacks |
| `tests/test_pantry_bot.py` | Create | Tests for `handle_pantry` and callback routing |

---

### Task 1: Extend `ItemAction` + `parse_item_callback` + add `parse_pantry_arg`

**Files:**
- Modify: `app/commands.py`
- Modify: `tests/test_renderer_commands.py`

- [ ] **Step 1: Write the failing tests**

Add to the bottom of `tests/test_renderer_commands.py`:

```python
from app.commands import parse_pantry_arg

# --- parse_item_callback back_to ---

def test_item_list_defaults_to_digest():
    action = parse_item_callback("item:list")
    assert action.kind == "list"
    assert action.back_to == "digest"


def test_item_list_all():
    action = parse_item_callback("item:list:all")
    assert action.kind == "list"
    assert action.back_to == "all"


def test_item_list_bad_suffix_raises():
    with pytest.raises(CommandError):
        parse_item_callback("item:list:unknown")


def test_item_open_defaults_to_digest():
    action = parse_item_callback("item:open:5")
    assert action.kind == "open"
    assert action.item_id == 5
    assert action.back_to == "digest"


def test_item_open_all():
    action = parse_item_callback("item:open:5:all")
    assert action.kind == "open"
    assert action.item_id == 5
    assert action.back_to == "all"


def test_item_open_bad_suffix_raises():
    with pytest.raises(CommandError):
        parse_item_callback("item:open:5:unknown")


# --- parse_pantry_arg ---

def test_parse_pantry_arg_no_args():
    assert parse_pantry_arg([]) == "all"


def test_parse_pantry_arg_digest():
    assert parse_pantry_arg(["digest"]) == "digest"


def test_parse_pantry_arg_numeric_id():
    assert parse_pantry_arg(["5"]) == 5


def test_parse_pantry_arg_hash_id():
    assert parse_pantry_arg(["#42"]) == 42


def test_parse_pantry_arg_invalid_raises():
    with pytest.raises(CommandError):
        parse_pantry_arg(["unknown"])
```

- [ ] **Step 2: Run to confirm tests fail**

```
uv run pytest tests/test_renderer_commands.py -k "back_to or pantry_arg" -v
```

Expected: `ImportError` or `AttributeError` (fields don't exist yet).

- [ ] **Step 3: Implement in `app/commands.py`**

Replace the `ItemAction` dataclass and `parse_item_callback` function, and add `parse_pantry_arg`. The `ItemKind` literal and `Verb` literal are unchanged.

```python
# Replace the existing ItemAction dataclass:
@dataclass(frozen=True)
class ItemAction:
    kind: ItemKind
    item_id: Optional[int] = None
    nudge_code: Optional[str] = None
    back_to: str = "digest"   # "digest" | "all"
```

```python
# Replace the existing parse_item_callback function:
def parse_item_callback(data: str) -> ItemAction:
    parts = data.split(":")
    if len(parts) < 2 or parts[0] != "item":
        raise CommandError(f"not an item callback {data!r}")
    kind = parts[1]
    if kind == "list":
        if len(parts) == 2:
            return ItemAction(kind="list", back_to="digest")
        if len(parts) == 3 and parts[2] == "all":
            return ItemAction(kind="list", back_to="all")
        raise CommandError(f"bad item callback {data!r}")
    if kind == "nudge":
        if len(parts) != 4 or parts[3] not in NUDGE_CODES:
            raise CommandError(f"bad item nudge {data!r}")
        try:
            return ItemAction(kind="nudge", item_id=int(parts[2]), nudge_code=parts[3])
        except ValueError as exc:
            raise CommandError(f"bad item id {parts[2]!r}") from exc
    if kind in ("open", "corr", "ctext", "rm", "rmok"):
        if len(parts) == 3:
            try:
                return ItemAction(kind=cast(ItemKind, kind), item_id=int(parts[2]), back_to="digest")
            except ValueError as exc:
                raise CommandError(f"bad item id {parts[2]!r}") from exc
        if kind == "open" and len(parts) == 4 and parts[3] == "all":
            try:
                return ItemAction(kind="open", item_id=int(parts[2]), back_to="all")
            except ValueError as exc:
                raise CommandError(f"bad item id {parts[2]!r}") from exc
        raise CommandError(f"bad item callback {data!r}")
    raise CommandError(f"unknown item kind {kind!r}")
```

```python
# Add after parse_item_callback (before _CORRECT_REPLY_MARKER):
def parse_pantry_arg(args: Sequence[str]) -> "Literal['all', 'digest'] | int":
    if not args:
        return "all"
    if len(args) > 1:
        raise CommandError("usage: /pantry [digest|<item_id>]")
    token = args[0].strip()
    if token == "digest":
        return "digest"
    try:
        return parse_item_id_arg(token)
    except CommandError:
        raise CommandError("usage: /pantry [digest|<item_id>]")
```

Also add `Literal` to the return type annotation import. The `Literal` is already imported at the top via `from typing import Literal, Optional, Sequence, cast` — no change needed.

- [ ] **Step 4: Run tests to confirm they pass**

```
uv run pytest tests/test_renderer_commands.py -k "back_to or pantry_arg" -v
```

Expected: all new tests PASS.

- [ ] **Step 5: Run full suite to check no regressions**

```
uv run pytest tests/test_renderer_commands.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/commands.py tests/test_renderer_commands.py
git commit -m "feat(pantry): extend ItemAction.back_to and add parse_pantry_arg"
```

---

### Task 2: Add `back_to` param to keyboard builders in `renderer.py`

**Files:**
- Modify: `app/renderer.py`
- Modify: `tests/test_renderer_commands.py`

- [ ] **Step 1: Write the failing tests**

Add to the bottom of `tests/test_renderer_commands.py`:

```python
from datetime import timedelta
from types import SimpleNamespace

def _stub_item(id: int, expires_in_days: int = 3):
    today = date(2026, 6, 14)
    return SimpleNamespace(
        id=id,
        raw_name="Milk",
        qty=1.0,
        unit=None,
        shelf_life_days=7,
        expires_on=today + timedelta(days=expires_in_days),
        storage="default",
    )


_TODAY = date(2026, 6, 14)


def test_build_digest_keyboard_default_back_to_digest():
    item = _stub_item(5)
    rows = build_digest_keyboard([item], has_more=False, today=_TODAY)
    assert rows[0][0].callback_data == "item:open:5"


def test_build_digest_keyboard_back_to_all():
    item = _stub_item(5)
    rows = build_digest_keyboard([item], has_more=False, today=_TODAY, back_to="all")
    assert rows[0][0].callback_data == "item:open:5:all"


def test_build_item_card_keyboard_default_back_button():
    item = _stub_item(5)
    rows = build_item_card_keyboard(item)
    back_btn = rows[-1][0]
    assert back_btn.callback_data == "item:list"


def test_build_item_card_keyboard_back_to_all():
    item = _stub_item(5)
    rows = build_item_card_keyboard(item, back_to="all")
    back_btn = rows[-1][0]
    assert back_btn.callback_data == "item:list:all"
```

- [ ] **Step 2: Run to confirm they fail**

```
uv run pytest tests/test_renderer_commands.py -k "back_to_all or back_button" -v
```

Expected: `TypeError` (unexpected keyword argument `back_to`).

- [ ] **Step 3: Update `build_digest_keyboard` in `app/renderer.py`**

Replace the existing `build_digest_keyboard` function:

```python
def build_digest_keyboard(
    items: list, *, has_more: bool, today: date, lang: str = "en", names=None, back_to: str = "digest"
) -> list[list[CallbackButton]]:
    def _open_data(item_id: int) -> str:
        if back_to == "all":
            return f"item:open:{item_id}:all"
        return f"item:open:{item_id}"

    buttons = [
        CallbackButton(
            text=f"{_urgency_icon(item.expires_on, today=today)} #{item.id} {_name(names, item.raw_name)}",
            callback_data=_open_data(item.id),
        )
        for item in items
    ]
    rows: list[list[CallbackButton]] = [
        buttons[i:i + 2] for i in range(0, len(buttons), 2)
    ]
    if has_more:
        rows.append([CallbackButton(text=t("btn.show_all", lang), callback_data="show:all")])
    return rows
```

- [ ] **Step 4: Update `build_item_card_keyboard` in `app/renderer.py`**

Replace the existing `build_item_card_keyboard` function:

```python
def build_item_card_keyboard(item, *, lang: str = "en", back_to: str = "digest") -> list[list[CallbackButton]]:
    item_id = item.id
    back_data = "item:list:all" if back_to == "all" else "item:list"
    rows: list[list[CallbackButton]] = [
        [
            CallbackButton(text=t("btn.ate", lang), callback_data=f"act:ate:{item_id}"),
            CallbackButton(text=t("btn.tossed", lang), callback_data=f"act:toss:{item_id}"),
        ]
    ]
    second = [
        CallbackButton(text=t("btn.snooze2", lang), callback_data=f"act:snooze2:{item_id}")
    ]
    _STORAGE_BUTTONS = {
        "fridge": ("btn.fridge", f"act:fridge:{item_id}"),
        "frozen": ("btn.freeze", f"act:freeze:{item_id}"),
    }
    for target in next_storage_options(getattr(item, "storage", "default")):
        key, data = _STORAGE_BUTTONS[target]
        second.append(CallbackButton(text=t(key, lang), callback_data=data))
    rows.append(second)
    rows.append([
        CallbackButton(text=t("btn.correct", lang), callback_data=f"item:corr:{item_id}"),
        CallbackButton(text=t("btn.remove", lang), callback_data=f"item:rm:{item_id}"),
    ])
    rows.append([CallbackButton(text=t("btn.back_to_list", lang), callback_data=back_data)])
    return rows
```

- [ ] **Step 5: Run the new tests**

```
uv run pytest tests/test_renderer_commands.py -k "back_to_all or back_button" -v
```

Expected: all PASS.

- [ ] **Step 6: Run full renderer test suite**

```
uv run pytest tests/test_renderer_commands.py tests/test_frozen_storage.py tests/test_fridge_storage.py -v
```

Expected: all PASS (existing callers omit `back_to` and get the same default behaviour).

- [ ] **Step 7: Commit**

```bash
git add app/renderer.py tests/test_renderer_commands.py
git commit -m "feat(pantry): add back_to param to build_digest_keyboard and build_item_card_keyboard"
```

---

### Task 3: Add `pantry.all_clear` i18n key

**Files:**
- Modify: `app/i18n.py`

- [ ] **Step 1: Add the key**

Find the `"digest.pantry_clear"` entry in `app/i18n.py` (line ~681) and insert the new key **directly after** it:

```python
    "pantry.all_clear": {
        "en": "Your pantry is clear.",
        "zh": "您的食品储藏室已清空。",
        "fr": "Votre garde-manger est vide.",
        "es": "Tu despensa está vacía.",
    },
```

- [ ] **Step 2: Run i18n integrity test**

```
uv run pytest tests/test_i18n.py -v
```

Expected: all PASS (the integrity test checks placeholder parity across languages).

- [ ] **Step 3: Commit**

```bash
git add app/i18n.py
git commit -m "feat(pantry): add pantry.all_clear i18n key"
```

---

### Task 4: Update `handle_item_callback` + add `_refresh_pantry_message`

**Files:**
- Modify: `app/bot.py`
- Create: `tests/test_pantry_bot.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pantry_bot.py`:

```python
"""Tests for /pantry command and item:list:all callback routing."""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine

import app.bot as bot_mod
from app.models import Household, PantryItem, User


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    def make():
        return Session(engine)

    with make() as db:
        hh = Household(created_at=datetime.now(timezone.utc))
        db.add(hh)
        db.commit()
        db.refresh(hh)
        assert hh.id is not None
        db.add(User(
            telegram_id=1, chat_id=99, household_id=hh.id,
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
    return make


def _active_item(session_factory, *, expires_in_days: int = 3) -> int:
    today = date(2026, 6, 14)
    with session_factory() as db:
        item = PantryItem(
            household_id=1,
            raw_name="Milk",
            normalized_name="milk",
            category="dairy",
            qty=1.0,
            unit=None,
            purchased_on=today,
            shelf_life_days=7,
            shelf_life_source="llm",
            ingest_shelf_life_source="llm",
            expires_on=today + timedelta(days=expires_in_days),
            status="active",
            created_via="manual",
            created_at=datetime.now(timezone.utc),
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        assert item.id is not None
        return item.id


def _cb(data: str):
    cb = MagicMock()
    cb.from_user = MagicMock(id=1)
    cb.data = data
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    cb.message.chat = MagicMock(id=99)
    cb.message.message_id = 1
    return cb


def _now_provider(tz: str):
    from datetime import datetime, timezone
    return datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


# --- item:list:all routes to _refresh_pantry_message (uses list_active) ---

@pytest.mark.asyncio
async def test_item_list_all_callback_calls_refresh_pantry(session_factory):
    item_id = _active_item(session_factory)
    cb = _cb("item:list:all")

    with patch.object(bot_mod, "_refresh_pantry_message", new_callable=AsyncMock) as mock_refresh:
        await bot_mod.handle_item_callback(
            cb,
            session_factory=session_factory,
            now_provider=_now_provider,
        )

    mock_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_item_list_digest_callback_calls_refresh_digest(session_factory):
    item_id = _active_item(session_factory)
    cb = _cb("item:list")

    with patch.object(bot_mod, "_refresh_digest_message", new_callable=AsyncMock) as mock_refresh:
        await bot_mod.handle_item_callback(
            cb,
            session_factory=session_factory,
            now_provider=_now_provider,
        )

    mock_refresh.assert_awaited_once()


# --- item:open:<id>:all preserves back_to in the rendered card ---

@pytest.mark.asyncio
async def test_item_open_all_renders_back_to_all(session_factory):
    item_id = _active_item(session_factory)
    cb = _cb(f"item:open:{item_id}:all")

    await bot_mod.handle_item_callback(
        cb,
        session_factory=session_factory,
        now_provider=_now_provider,
    )

    # edit_text or answer was called — check keyboard contains item:list:all
    call_args = cb.message.edit_text.call_args or cb.message.answer.call_args
    assert call_args is not None
    keyboard = call_args.kwargs.get("reply_markup") or (call_args.args[1] if len(call_args.args) > 1 else None)
    assert keyboard is not None
    # Back button is the last row, first button
    last_row = keyboard.inline_keyboard[-1]
    assert last_row[0].callback_data == "item:list:all"
```

- [ ] **Step 2: Run to confirm tests fail**

```
uv run pytest tests/test_pantry_bot.py -v
```

Expected: `AttributeError: module 'app.bot' has no attribute '_refresh_pantry_message'` or similar.

- [ ] **Step 3: Add `_refresh_pantry_message` to `app/bot.py`**

Add this function directly after the existing `_refresh_digest_message` function (around line 2432):

```python
async def _refresh_pantry_message(
    cb, session, household_id: int, today: date, *, lang: str = "en", translation_llm=None
) -> None:
    remaining = list_active(session, household_id=household_id, today=today)
    if remaining:
        names = await _translate_for_render(
            session,
            lang=lang,
            texts=[i.raw_name for i in remaining],
            translation_llm=translation_llm,
        )
        rendered = render_digest(remaining, today=today, lang=lang, names=names, cap=None)
        keyboard = to_aiogram_keyboard(
            build_digest_keyboard(
                rendered.rendered_items,
                has_more=False,
                today=today,
                lang=lang,
                names=names,
                back_to="all",
            )
        )
        await edit_or_resend(cb, rendered.text, keyboard)
        return
    await edit_or_resend(cb, t("pantry.all_clear", lang))
```

- [ ] **Step 4: Update `handle_item_callback` in `app/bot.py`**

Find the `if action.kind == "list":` block (around line 1895) and replace it:

```python
        if action.kind == "list":
            await dispatch_answer(cb)
            if action.back_to == "all":
                await _refresh_pantry_message(
                    cb, session, user.household_id, today,
                    lang=user.lang, translation_llm=translation_llm,
                )
            else:
                await refresh()
            return
```

Also update the `action.kind == "open"` branch (around line 1962) to pass `back_to`:

```python
        if action.kind == "open":
            await edit_or_resend(
                cb,
                render_item_card(item, today=today, lang=user.lang, names=names),
                to_aiogram_keyboard(build_item_card_keyboard(item, lang=user.lang, back_to=action.back_to)),
            )
```

- [ ] **Step 5: Run the new tests**

```
uv run pytest tests/test_pantry_bot.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 6: Run broader bot test suite**

```
uv run pytest tests/test_v1_5_bot.py tests/test_frozen_storage.py tests/test_fridge_storage.py -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add app/bot.py tests/test_pantry_bot.py
git commit -m "feat(pantry): add _refresh_pantry_message and route item:list:all"
```

---

### Task 5: Add `handle_pantry` handler + register `/pantry`

**Files:**
- Modify: `app/bot.py`
- Modify: `tests/test_pantry_bot.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pantry_bot.py`:

```python
def _msg(text: str):
    msg = MagicMock()
    msg.from_user = MagicMock(id=1)
    msg.chat = MagicMock(id=99, type="private")
    msg.text = text
    msg.answer = AsyncMock()
    return msg


# --- /pantry (all mode) ---

@pytest.mark.asyncio
async def test_pantry_all_mode_sends_interactive_message(session_factory):
    _active_item(session_factory)
    msg = _msg("/pantry")

    await bot_mod.handle_pantry(
        msg,
        session_factory=session_factory,
        now_provider=_now_provider,
    )

    msg.answer.assert_awaited_once()
    call_kwargs = msg.answer.call_args.kwargs
    # Must have a keyboard
    assert call_kwargs.get("reply_markup") is not None
    # Keyboard must contain item:open:<id>:all buttons
    keyboard = call_kwargs["reply_markup"]
    all_data = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
    assert any(":all" in d for d in all_data)


# --- /pantry digest ---

@pytest.mark.asyncio
async def test_pantry_digest_mode_sends_interactive_message(session_factory):
    # Item expiring in 2 days — within digest window
    _active_item(session_factory, expires_in_days=2)
    msg = _msg("/pantry digest")

    await bot_mod.handle_pantry(
        msg,
        session_factory=session_factory,
        now_provider=_now_provider,
    )

    msg.answer.assert_awaited_once()
    call_kwargs = msg.answer.call_args.kwargs
    assert call_kwargs.get("reply_markup") is not None
    # Digest mode buttons do NOT have :all suffix
    keyboard = call_kwargs["reply_markup"]
    all_data = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
    assert any(d.startswith("item:open:") for d in all_data)
    assert not any(d.endswith(":all") for d in all_data)


# --- /pantry <id> ---

@pytest.mark.asyncio
async def test_pantry_item_id_mode_sends_item_card(session_factory):
    item_id = _active_item(session_factory)
    msg = _msg(f"/pantry {item_id}")

    await bot_mod.handle_pantry(
        msg,
        session_factory=session_factory,
        now_provider=_now_provider,
    )

    msg.answer.assert_awaited_once()
    call_kwargs = msg.answer.call_args.kwargs
    keyboard = call_kwargs.get("reply_markup")
    assert keyboard is not None
    # Back button should be item:list:all
    last_row = keyboard.inline_keyboard[-1]
    assert last_row[0].callback_data == "item:list:all"


# --- /pantry unknown ---

@pytest.mark.asyncio
async def test_pantry_invalid_arg_replies_with_error(session_factory):
    msg = _msg("/pantry nonsense")

    await bot_mod.handle_pantry(
        msg,
        session_factory=session_factory,
        now_provider=_now_provider,
    )

    msg.answer.assert_awaited_once()
    text = msg.answer.call_args.args[0]
    assert "usage" in text.lower() or "pantry" in text.lower()


# --- /pantry <missing id> ---

@pytest.mark.asyncio
async def test_pantry_missing_item_id_replies_no_item(session_factory):
    msg = _msg("/pantry 9999")

    await bot_mod.handle_pantry(
        msg,
        session_factory=session_factory,
        now_provider=_now_provider,
    )

    msg.answer.assert_awaited_once()
    text = msg.answer.call_args.args[0]
    assert "9999" in text


# --- /pantry empty pantry ---

@pytest.mark.asyncio
async def test_pantry_all_empty_sends_all_clear(session_factory):
    msg = _msg("/pantry")

    await bot_mod.handle_pantry(
        msg,
        session_factory=session_factory,
        now_provider=_now_provider,
    )

    msg.answer.assert_awaited_once()
    text = msg.answer.call_args.args[0]
    assert "clear" in text.lower() or "empty" in text.lower() or "pantry" in text.lower()
```

- [ ] **Step 2: Run to confirm they fail**

```
uv run pytest tests/test_pantry_bot.py -k "pantry" -v
```

Expected: `AttributeError: module 'app.bot' has no attribute 'handle_pantry'`.

- [ ] **Step 3: Add the import for `parse_pantry_arg` in `app/bot.py`**

Find the `from app.commands import (` block (around line 18) and add `parse_pantry_arg` to it:

```python
from app.commands import (
    CommandError,
    parse_callback,
    parse_correct_reply_marker,
    parse_digest_at,
    parse_invite_mode,
    parse_invite_token,
    parse_item_callback,
    parse_item_id_arg,
    parse_lang,
    parse_llm_provider,
    parse_list_filter,
    parse_member_id,
    parse_pantry_arg,        # ← add this line
    parse_snooze_args,
    parse_tz,
)
```

- [ ] **Step 4: Add `handle_pantry` to `app/bot.py`**

Add this function after `handle_list` (around line 779):

```python
async def handle_pantry(
    msg,
    *,
    session_factory: _SessionFactory,
    now_provider: NowProvider,
    on_user_created: Callable[[User], None] = _noop_user_created,
    translation_llm=None,
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
        now_provider=now_provider,
    ) as ctx:
        if ctx is None:
            return
        session, user, today = ctx.session, ctx.user, _require_today(ctx.today)
        parts = (msg.text or "").split(maxsplit=1)
        args = parts[1].split() if len(parts) > 1 else []
        try:
            mode = parse_pantry_arg(args)
        except CommandError as exc:
            await msg.answer(str(exc))
            return

        if isinstance(mode, int):
            item = session.get(PantryItem, mode)
            if item is None or item.household_id != user.household_id:
                await msg.answer(f"no item #{mode}")
                return
            if item.status != "active":
                await msg.answer(f"#{mode} is {item.status}")
                return
            names = await _translate_for_render(
                session, lang=user.lang, texts=[item.raw_name],
                translation_llm=translation_llm,
            )
            await msg.answer(
                render_item_card(item, today=today, lang=user.lang, names=names),
                reply_markup=to_aiogram_keyboard(
                    build_item_card_keyboard(item, lang=user.lang, back_to="all")
                ),
            )
            return

        if mode == "digest":
            items = list_digest_due(session, household_id=user.household_id, today=today)
            back_to = "digest"
        else:
            items = list_active(session, household_id=user.household_id, today=today)
            back_to = "all"

        names = await _translate_for_render(
            session, lang=user.lang,
            texts=[i.raw_name for i in items],
            translation_llm=translation_llm,
        )
        cap = None if back_to == "all" else 10
        rendered = render_digest(items, today=today, lang=user.lang, names=names, cap=cap)
        if not rendered.text:
            empty_key = "pantry.all_clear" if back_to == "all" else "digest.pantry_clear"
            await msg.answer(t(empty_key, user.lang))
            return
        keyboard = to_aiogram_keyboard(
            build_digest_keyboard(
                rendered.rendered_items,
                has_more=rendered.has_more,
                today=today,
                lang=user.lang,
                names=names,
                back_to=back_to,
            )
        )
        await msg.answer(rendered.text, reply_markup=keyboard)
```

- [ ] **Step 5: Register `/pantry` in `_MESSAGE_COMMANDS`**

Find `_MESSAGE_COMMANDS` (around line 2455) and add one line after the `"list"` entry:

```python
    ("list", handle_list, ("session_factory", "now_provider", "on_user_created", "translation_llm")),
    ("pantry", handle_pantry, ("session_factory", "now_provider", "on_user_created", "translation_llm")),  # ← add
    ("add", handle_add, ("session_factory", "now_provider", "text_llm", "on_user_created", "search")),
```

- [ ] **Step 6: Run the new tests**

```
uv run pytest tests/test_pantry_bot.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 7: Run the full test suite**

```
uv run pytest -v
```

Expected: all PASS. If ruff is configured, also run:

```
uv run ruff check app/commands.py app/renderer.py app/bot.py app/i18n.py
```

Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add app/bot.py tests/test_pantry_bot.py
git commit -m "feat(pantry): add /pantry interactive command with digest/all/id modes"
```
