# Food Bot v2 "Polish + Accuracy" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship five polish + accuracy features to the single-user Telegram pantry bot: inline undo, category-grouped list rendering, a next-year date indicator, recognition that excludes non-trackable items, and websearch-backed shelf-life accuracy.

**Architecture:** Pure service functions take `session: Session` and explicit `today: date` (no global state, no internal `datetime.now()` for date logic). Display logic lives in `renderer.py` and is unit-tested without a DB. The LLM and a new web-search client are `Protocol`s with fakes in `tests/fakes.py`. Websearch refinement runs as a background asyncio task spawned from the photo handler, re-using the receipt reply's message handle to edit it in place. No new DB tables or columns are required — undo uses existing `status`/`source_receipt_id`, websearch writes to the existing `ShelfLifeCache`, and search cost accumulates into the existing `Receipt.llm_cost_micros_usd`.

**Tech Stack:** Python 3, SQLModel/SQLAlchemy + SQLite, Alembic, aiogram, APScheduler, Anthropic SDK (vision + native `web_search` tool), pytest + pytest-asyncio, uv.

---

## Conventions for every task

- Run a single test: `uv run pytest tests/path::test_name -v`
- Run the whole suite before a feature's final commit: `uv run pytest`
- Lint before commit: `uv run ruff check`
- All dates in tests use `today = date(2026, 5, 28)` unless a boundary case needs otherwise.
- "Untouched item" (used by undo and refine) is defined once, in Task 7, as:
  `status == "active" AND snoozed_until is None AND shelf_life_source != "user_correction"`.
  (Accepted limitation: a name-only correction does not flip `shelf_life_source`, so it is not treated as "touched". Documented in Task 7.)

---

## File Structure

**New files**
- `app/refine_service.py` — websearch shelf-life resolver protocol, result types, and the receipt-refine service function.
- `tests/test_v2_rendering.py` — rendering + year-indicator tests.
- `tests/test_v2_recognition.py` — track-worthy filtering tests.
- `tests/test_v2_undo.py` — undo service + callback tests.
- `tests/test_v2_websearch.py` — search resolver + refine service tests.

**Modified files**
- `app/renderer.py` — year-aware `_fmt_date`, urgency icon + qty/unit line helper, category-grouped `/list`, digest decorations, excluded-item report, undo keyboards, refined markers.
- `app/llm.py` — `ParsedItem.track_worthy`/`exclusion_reason`, system-prompt update.
- `app/ingest_service.py` — filter on `track_worthy`, collect excluded + uncached item ids in `IngestSummary`.
- `app/pantry_service.py` — `undo_receipt` / `undo_add` services + `is_untouched` helper.
- `app/commands.py` — parse `undo:receipt:<id>` / `undo:add:<id>` callbacks.
- `app/bot.py` — attach undo keyboards, handle undo callbacks, spawn the background refine task, inline `/add` search.
- `app/correction_service.py` — `/add` inline websearch on cache miss.

---

# FEATURE A — Rendering + Next-Year Indicator

## Task 1: Year-aware date formatting

**Files:**
- Modify: `app/renderer.py:11-12` (`_fmt_date`)
- Test: `tests/test_v2_rendering.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_v2_rendering.py
from datetime import date
from app.renderer import _fmt_date


def test_fmt_date_same_year_omits_year():
    assert _fmt_date(date(2026, 6, 2), today=date(2026, 5, 28)) == "Jun 2"


def test_fmt_date_different_year_shows_year():
    assert _fmt_date(date(2027, 6, 2), today=date(2026, 5, 28)) == "Jun 2 2027"


def test_fmt_date_dec_jan_boundary_shows_year_even_when_close():
    # 8 days out but next calendar year -> show year
    assert _fmt_date(date(2027, 1, 5), today=date(2026, 12, 28)) == "Jan 5 2027"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_v2_rendering.py -v`
Expected: FAIL — `_fmt_date() got an unexpected keyword argument 'today'`

- [ ] **Step 3: Update `_fmt_date` to take `today` and append the year cross-year**

```python
def _fmt_date(value: date, *, today: date) -> str:
    base = f"{value:%b} {value.day}"
    if value.year != today.year:
        return f"{base} {value.year}"
    return base
```

- [ ] **Step 4: Fix all existing call sites in `renderer.py`**

`_fmt_date` is called in `render_ingest_reply`, `render_list`, and (indirectly) the digest. Pass `today=` at each site. Specifically:
- `render_ingest_reply`: lines that format `expires_on`, `purchase_date`, and the assumed purchase date → `_fmt_date(x, today=today)`.
- `render_list`: the `expires {date}` branch → `_fmt_date(item.expires_on, today=today)`.
- `render_digest`: the header line uses `_fmt_date(today, today=today)`.

- [ ] **Step 5: Run the existing renderer tests to confirm no regressions**

Run: `uv run pytest tests/test_renderer_commands.py tests/test_v2_rendering.py -v`
Expected: PASS (existing assertions like `"exp May 31"` and `"May 30"` still hold because those dates are in 2026).

- [ ] **Step 6: Commit**

```bash
git add app/renderer.py tests/test_v2_rendering.py
git commit -m "feat(renderer): year-aware date formatting for cross-year expiries"
```

---

## Task 2: Shared per-line helper — urgency icon + qty/unit

**Files:**
- Modify: `app/renderer.py` (add helpers near top)
- Test: `tests/test_v2_rendering.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_v2_rendering.py (append)
from app.renderer import _urgency_icon, _qty_prefix, render_item_line
from tests.test_renderer_commands import _pantry_item  # reuse builder


def test_urgency_icon_thresholds():
    today = date(2026, 5, 28)
    assert _urgency_icon(date(2026, 5, 27), today=today) == "🔴"   # expired
    assert _urgency_icon(date(2026, 5, 28), today=today) == "🔴"   # today
    assert _urgency_icon(date(2026, 5, 30), today=today) == "🟡"   # within 3d
    assert _urgency_icon(date(2026, 6, 10), today=today) == "🟢"   # later


def test_qty_prefix_renders_qty_and_unit():
    assert _qty_prefix(2.0, "lb") == "2 lb "
    assert _qty_prefix(1.0, None) == ""          # qty 1 + no unit -> nothing
    assert _qty_prefix(3.0, None) == "3 "        # qty>1, no unit
    assert _qty_prefix(1.0, "gal") == "1 gal "


def test_render_item_line_combines_icon_qty_date():
    item = _pantry_item("Chicken", date(2026, 5, 29), 7)
    item.qty, item.unit = 2.0, "lb"
    line = render_item_line(item, today=date(2026, 5, 28))
    assert line == "🟡 #7 2 lb Chicken - May 29 (1d)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_v2_rendering.py -k "urgency or qty or item_line" -v`
Expected: FAIL — `ImportError: cannot import name '_urgency_icon'`

- [ ] **Step 3: Implement the helpers**

```python
URGENCY_SOON_DAYS = 3


def _urgency_icon(expires_on: date, *, today: date) -> str:
    delta = (expires_on - today).days
    if delta <= 0:
        return "🔴"
    if delta <= URGENCY_SOON_DAYS:
        return "🟡"
    return "🟢"


def _qty_prefix(qty: float, unit: str | None) -> str:
    qty_str = str(int(qty)) if float(qty).is_integer() else str(qty)
    if unit:
        return f"{qty_str} {unit} "
    if qty_str != "1":
        return f"{qty_str} "
    return ""


def render_item_line(item, *, today: date) -> str:
    icon = _urgency_icon(item.expires_on, today=today)
    qty = _qty_prefix(item.qty, item.unit)
    delta = (item.expires_on - today).days
    if delta < 0:
        tail = f"expired {-delta}d"
    elif delta == 0:
        tail = "today"
    else:
        tail = f"{_fmt_date(item.expires_on, today=today)} ({delta}d)"
    return f"{icon} #{item.id} {qty}{item.raw_name} - {tail}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_v2_rendering.py -k "urgency or qty or item_line" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/renderer.py tests/test_v2_rendering.py
git commit -m "feat(renderer): shared urgency-icon + qty/unit line helper"
```

---

## Task 3: Category-grouped `/list`

**Files:**
- Modify: `app/renderer.py` (`render_list`)
- Test: `tests/test_v2_rendering.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_v2_rendering.py (append)
from app.renderer import render_list


def _cat_item(name, expires_on, item_id, category, qty=1.0, unit=None):
    item = _pantry_item(name, expires_on, item_id)
    item.category = category
    item.qty, item.unit = qty, unit
    return item


def test_render_list_groups_by_category_then_expiry():
    today = date(2026, 5, 28)
    items = [
        _cat_item("Bananas", date(2026, 6, 2), 4, "produce"),
        _cat_item("Spinach", date(2026, 5, 27), 9, "produce"),
        _cat_item("Chicken", date(2026, 5, 27), 7, "meat", qty=2.0, unit="lb"),
    ]
    text = render_list(items, today=today)
    # category headers with counts, ordered by ALLOWED order (produce before meat)
    assert "Produce (2)" in text
    assert "Meat (1)" in text
    assert text.index("Produce (2)") < text.index("Meat (1)")
    # within produce, expired Spinach (#9) sorts before Bananas (#4)
    assert text.index("#9 Spinach") < text.index("#4 Bananas")
    # qty/unit + icon present
    assert "🔴 #7 2 lb Chicken" in text


def test_render_list_empty_unchanged():
    assert "no items" in render_list([], today=date(2026, 5, 28)).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_v2_rendering.py -k "groups_by_category or list_empty" -v`
Expected: FAIL — current `render_list` is flat (`"Produce (2)"` not present).

- [ ] **Step 3: Rewrite `render_list` to group by category**

```python
CATEGORY_ORDER = (
    "produce", "dairy", "meat", "seafood", "bakery",
    "frozen", "beverage", "pantry", "other",
)


def render_list(items: list, *, today: date) -> str:
    if not items:
        return "no items match this filter"
    by_cat: dict[str, list] = {}
    for item in items:
        key = item.category or "other"
        by_cat.setdefault(key, []).append(item)
    ordered = sorted(
        by_cat.keys(),
        key=lambda c: CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else len(CATEGORY_ORDER),
    )
    lines: list[str] = []
    for cat in ordered:
        group = sorted(by_cat[cat], key=lambda i: i.expires_on)
        lines.append(f"{cat.capitalize()} ({len(group)})")
        lines.extend(f"  {render_item_line(i, today=today)}" for i in group)
    return "\n".join(lines)
```

- [ ] **Step 4: Update the old flat-list assertion in `tests/test_renderer_commands.py`**

The existing `test_render_list_and_stats` asserts `"#1 Milk" in text and "May 30" in text`. With grouping + icon, the line becomes `"🟢 #1 Milk - May 30 (...)"`. Update that assertion to:

```python
    text = render_list([_pantry_item("Milk", date(2026, 5, 30), 1)], today=date(2026, 5, 26))
    assert "#1 Milk" in text and "May 30" in text  # still true (substring)
    assert "Other (1)" in text                      # now grouped
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_v2_rendering.py tests/test_renderer_commands.py -v`
Expected: PASS

- [ ] **Step 6: Apply per-line decorations to the digest (consistency)**

In `render_digest`, replace the body of the local `line_for(item)` with the shared helper so the digest shows icons + qty/unit:

```python
        def line_for(item) -> str:
            return "  " + render_item_line(item, today=today)
```

Update `tests/test_renderer_commands.py::test_render_digest_buckets_and_keyboard`: the line assertions like `"#41 Spinach"` remain true as substrings, but add `assert "🔴 #41 Spinach" in rendered.text`. Keep the bucket-header assertions unchanged.

- [ ] **Step 7: Run the renderer suite**

Run: `uv run pytest tests/test_renderer_commands.py tests/test_v2_rendering.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add app/renderer.py tests/test_v2_rendering.py tests/test_renderer_commands.py
git commit -m "feat(renderer): category-grouped /list and digest line decorations"
```

---

# FEATURE B — Recognition Hardening

## Task 4: Add `track_worthy` / `exclusion_reason` to the parse schema

**Files:**
- Modify: `app/llm.py:19-27` (`ParsedItem`) and `SYSTEM_PROMPT`
- Test: `tests/test_v2_recognition.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_v2_recognition.py
from app.llm import ParsedItem


def test_parsed_item_defaults_track_worthy_true():
    item = ParsedItem(
        is_food=True, name="Whole Milk", est_shelf_life_days=7, confidence=0.9
    )
    assert item.track_worthy is True
    assert item.exclusion_reason is None


def test_parsed_item_can_be_excluded():
    item = ParsedItem(
        is_food=True, name="Ketchup", est_shelf_life_days=365, confidence=0.9,
        track_worthy=False, exclusion_reason="shelf_stable",
    )
    assert item.track_worthy is False
    assert item.exclusion_reason == "shelf_stable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_v2_recognition.py -v`
Expected: FAIL — `ParsedItem` has no field `track_worthy`.

- [ ] **Step 3: Add the fields (defaulted, backward-compatible)**

In `ParsedItem`:

```python
    track_worthy: bool = True
    exclusion_reason: Optional[str] = None
```

- [ ] **Step 4: Update `SYSTEM_PROMPT` to classify non-trackable items**

Append to the per-line-item bullet list in `SYSTEM_PROMPT` (after `confidence`):

```
  - track_worthy: false for items not worth expiry-tracking even if edible:
        medicines/supplements/vitamins, condiments & sauces (ketchup, soy
        sauce, dressing, jam), spices & seasonings (salt, pepper, dried herbs),
        and household/toiletries. true for genuinely perishable food AND
        legitimately stocked staples (canned beans, rice, pasta).
  - exclusion_reason: when track_worthy is false, one of "non_food",
        "shelf_stable", "household". null when track_worthy is true.
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_v2_recognition.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/llm.py tests/test_v2_recognition.py
git commit -m "feat(llm): add track_worthy/exclusion_reason to parse schema + prompt"
```

---

## Task 5: Filter non-trackable items in ingest + report them

**Files:**
- Modify: `app/ingest_service.py` (`IngestSummary`, `ingest_photo` loop)
- Modify: `app/renderer.py` (`render_ingest_reply`)
- Test: `tests/test_v2_recognition.py`, `tests/test_core_services.py`

- [ ] **Step 1: Write the failing service test**

```python
# tests/test_v2_recognition.py (append)
import asyncio
from datetime import date, datetime, timezone
import pytest
from sqlmodel import SQLModel, Session, create_engine, select
from app.ingest_service import ingest_photo
from app.llm import LLMResult, ParseResult, ParsedItem
from app.models import PantryItem, User
from tests.fakes import FakeLLMClient


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        db.commit()
        yield db


@pytest.mark.asyncio
async def test_ingest_excludes_non_trackable_and_reports(session):
    llm = FakeLLMClient(canned=LLMResult(parse=ParseResult(
        purchase_date=date(2026, 5, 28), purchase_date_confidence=0.9,
        items=[
            ParsedItem(is_food=True, name="Whole Milk", est_shelf_life_days=7, confidence=0.9),
            ParsedItem(is_food=True, name="Ketchup", est_shelf_life_days=365, confidence=0.9,
                       track_worthy=False, exclusion_reason="shelf_stable"),
            ParsedItem(is_food=False, name="Advil", est_shelf_life_days=365, confidence=0.9,
                       track_worthy=False, exclusion_reason="non_food"),
        ],
    ), cost_micros_usd=1000))
    summary = await ingest_photo(
        session, llm, user_id=1, photo_file_id="fid", image_bytes=b"jpg",
        today=date(2026, 5, 28),
    )
    assert summary.inserted_food_count == 1
    assert summary.skipped_excluded_count == 2
    assert set(summary.skipped_excluded_names) == {"Ketchup", "Advil"}
    names = {i.normalized_name for i in session.exec(select(PantryItem)).all()}
    assert names == {"whole milk"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_v2_recognition.py::test_ingest_excludes_non_trackable_and_reports -v`
Expected: FAIL — `IngestSummary` has no `skipped_excluded_count`.

- [ ] **Step 3: Add fields to `IngestSummary`**

```python
    skipped_excluded_count: int = 0
    skipped_excluded_names: list[str] = field(default_factory=list)
    uncached_item_ids: list[int] = field(default_factory=list)
```

(`uncached_item_ids` is used later by the websearch refine; populate it now while we touch this loop.)

- [ ] **Step 4: Update the ingest filter loop**

In `ingest_photo`, in the `for parsed_item in parsed_receipt.items:` loop, add the track-worthy check **before** the `is_food` check, and record excluded names:

```python
        if not parsed_item.track_worthy:
            summary.skipped_excluded_count += 1
            summary.skipped_excluded_names.append(parsed_item.name)
            continue
        if not parsed_item.is_food:
            summary.skipped_non_food_count += 1
            continue
```

Then, after each item is inserted and flushed (where `summary.inserted_food_count += 1`), record uncached items for refine:

```python
            if not decision.cache_was_hit:
                summary.uncached_item_ids.append(pantry_item.id)
```

- [ ] **Step 5: Update `render_ingest_reply` to report excluded items**

Before the final `_fmt_cost` append in `render_ingest_reply`, add:

```python
    if summary.skipped_excluded_count:
        names = ", ".join(summary.skipped_excluded_names[:5])
        more = "" if len(summary.skipped_excluded_names) <= 5 else ", ..."
        lines.append(f"Skipped (not tracked): {names}{more}")
        lines.append("Want one tracked? /add <name>")
```

- [ ] **Step 6: Write the renderer test**

```python
# tests/test_v2_recognition.py (append)
from app.ingest_service import IngestSummary
from app.renderer import render_ingest_reply


def test_render_ingest_reply_lists_excluded():
    summary = IngestSummary(
        receipt_id=1, inserted_food_count=1,
        inserted_item_ids=[1], inserted_item_names=["Whole Milk"],
        inserted_item_expires_on=[date(2026, 6, 4)], inserted_item_shelf_life_days=[7],
        purchase_date=date(2026, 5, 28), purchase_date_assumed=False,
        cost_micros_usd=1000,
        skipped_excluded_count=2, skipped_excluded_names=["Ketchup", "Advil"],
    )
    text = render_ingest_reply(summary, today=date(2026, 5, 28))
    assert "Skipped (not tracked): Ketchup, Advil" in text
    assert "/add" in text
```

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_v2_recognition.py -v`
Expected: PASS

- [ ] **Step 8: Run the full suite (the ingest loop changed)**

Run: `uv run pytest`
Expected: PASS — existing ingest tests still pass because `track_worthy` defaults to `True`.

- [ ] **Step 9: Commit**

```bash
git add app/ingest_service.py app/renderer.py tests/test_v2_recognition.py
git commit -m "feat(ingest): exclude non-trackable items and report them in reply"
```

---

# FEATURE C — Undo (inline button, 10-min TTL)

## Task 6: Undo callback parsing

**Files:**
- Modify: `app/commands.py` (`Verb`, `parse_callback`)
- Test: `tests/test_v2_undo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_v2_undo.py
import pytest
from app.commands import CallbackAction, CommandError, parse_callback


def test_parse_undo_receipt_and_add():
    assert parse_callback("undo:receipt:12") == CallbackAction(verb="undo_receipt", item_id=12)
    assert parse_callback("undo:add:7") == CallbackAction(verb="undo_add", item_id=7)


def test_parse_undo_bad_id():
    with pytest.raises(CommandError):
        parse_callback("undo:receipt:x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_v2_undo.py -v`
Expected: FAIL — `unrecognized callback data 'undo:receipt:12'`

- [ ] **Step 3: Extend `Verb` and `parse_callback`**

Add `"undo_receipt"` and `"undo_add"` to the `Verb` Literal. In `parse_callback`, before the final `act:` parsing, add:

```python
    if data.startswith("undo:"):
        _, _, rest = data.partition(":")
        kind, _, raw_id = rest.partition(":")
        if kind not in ("receipt", "add"):
            raise CommandError(f"unknown undo kind {kind!r}")
        try:
            target_id = int(raw_id)
        except ValueError as exc:
            raise CommandError(f"bad undo id {raw_id!r}") from exc
        return CallbackAction(verb=cast(Verb, f"undo_{kind}"), item_id=target_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_v2_undo.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/commands.py tests/test_v2_undo.py
git commit -m "feat(commands): parse undo:receipt / undo:add callbacks"
```

---

## Task 7: Undo service functions

**Files:**
- Modify: `app/pantry_service.py` (add `is_untouched`, `UndoResult`, `undo_receipt`, `undo_add`, `UNDO_TTL_MINUTES`)
- Test: `tests/test_v2_undo.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_v2_undo.py (append)
from datetime import date, datetime, timedelta, timezone
from sqlmodel import SQLModel, Session, create_engine
from app.models import PantryItem, Receipt, User


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        db.commit()
        yield db


def _receipt(session, *, scanned_at):
    r = Receipt(user_id=1, photo_file_id=f"p{scanned_at.timestamp()}",
                purchase_date=date(2026, 5, 28), purchase_date_source="receipt",
                scanned_at=scanned_at)
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


def _ritem(session, receipt_id, name, *, status="active", snoozed_until=None,
           source="llm", created_at=None):
    item = PantryItem(
        user_id=1, raw_name=name, normalized_name=name.lower(), category="produce",
        qty=1.0, unit=None, purchased_on=date(2026, 5, 28), shelf_life_days=5,
        shelf_life_source=source, ingest_shelf_life_source="llm",
        expires_on=date(2026, 6, 2), status=status, snoozed_until=snoozed_until,
        created_via="receipt", source_receipt_id=receipt_id,
        created_at=created_at or datetime.now(timezone.utc),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def test_undo_receipt_full_removes_all_and_deletes_receipt(session):
    now = datetime.now(timezone.utc)
    r = _receipt(session, scanned_at=now)
    a = _ritem(session, r.id, "A")
    b = _ritem(session, r.id, "B")
    from app.pantry_service import undo_receipt
    result = undo_receipt(session, user_id=1, receipt_id=r.id, now=now)
    assert result.expired is False
    assert set(result.removed_ids) == {a.id, b.id}
    assert result.skipped == []
    assert result.receipt_deleted is True
    session.refresh(a); session.refresh(b)
    assert a.status == "removed" and b.status == "removed"
    assert a.source_receipt_id is None  # FK nulled before delete
    assert session.get(Receipt, r.id) is None


def test_undo_receipt_partial_keeps_receipt(session):
    now = datetime.now(timezone.utc)
    r = _receipt(session, scanned_at=now)
    a = _ritem(session, r.id, "A")
    eaten = _ritem(session, r.id, "B", status="eaten")
    from app.pantry_service import undo_receipt
    result = undo_receipt(session, user_id=1, receipt_id=r.id, now=now)
    assert result.removed_ids == [a.id]
    assert result.skipped == [(eaten.id, "eaten")]
    assert result.receipt_deleted is False
    assert session.get(Receipt, r.id) is not None
    session.refresh(a)
    assert a.status == "removed" and a.source_receipt_id == r.id  # FK kept


def test_undo_receipt_expired_after_ttl(session):
    now = datetime.now(timezone.utc)
    r = _receipt(session, scanned_at=now - timedelta(minutes=11))
    _ritem(session, r.id, "A")
    from app.pantry_service import undo_receipt
    result = undo_receipt(session, user_id=1, receipt_id=r.id, now=now)
    assert result.expired is True
    assert result.removed_ids == []


def test_undo_add_single_item(session):
    now = datetime.now(timezone.utc)
    item = _ritem(session, None, "Solo", created_at=now)
    from app.pantry_service import undo_add
    result = undo_add(session, user_id=1, item_id=item.id, now=now)
    assert result.removed_ids == [item.id]
    assert result.receipt_deleted is False
    session.refresh(item)
    assert item.status == "removed"


def test_undo_add_skips_corrected(session):
    now = datetime.now(timezone.utc)
    item = _ritem(session, None, "Solo", source="user_correction", created_at=now)
    from app.pantry_service import undo_add
    result = undo_add(session, user_id=1, item_id=item.id, now=now)
    assert result.removed_ids == []
    assert result.skipped == [(item.id, "corrected")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_v2_undo.py -k undo_receipt or -k undo_add -v`
Expected: FAIL — `cannot import name 'undo_receipt'`

- [ ] **Step 3: Implement the undo services**

Add to `app/pantry_service.py` (import `Receipt` is already imported):

```python
UNDO_TTL_MINUTES = 10


def is_untouched(item: PantryItem) -> bool:
    return (
        item.status == "active"
        and item.snoozed_until is None
        and item.shelf_life_source != "user_correction"
    )


def _skip_reason(item: PantryItem) -> str:
    if item.status != "active":
        return item.status              # eaten / tossed / removed
    if item.snoozed_until is not None:
        return "snoozed"
    return "corrected"                   # shelf_life_source == user_correction


@dataclass(frozen=True)
class UndoResult:
    removed_ids: list[int]
    skipped: list[tuple[int, str]]
    receipt_deleted: bool
    expired: bool


def _expired(reference: datetime, now: datetime) -> bool:
    ref = reference if reference.tzinfo else reference.replace(tzinfo=timezone.utc)
    return (now - ref) > timedelta(minutes=UNDO_TTL_MINUTES)


def undo_receipt(
    session: Session, *, user_id: int, receipt_id: int, now: datetime
) -> UndoResult:
    receipt = session.get(Receipt, receipt_id)
    if receipt is None or receipt.user_id != user_id:
        return UndoResult([], [], False, expired=False)
    if _expired(receipt.scanned_at, now):
        return UndoResult([], [], False, expired=True)

    items = list(session.exec(
        select(PantryItem).where(
            PantryItem.user_id == user_id,
            PantryItem.source_receipt_id == receipt_id,
        )
    ).all())
    removed_ids: list[int] = []
    skipped: list[tuple[int, str]] = []
    for item in items:
        assert item.id is not None
        if is_untouched(item):
            item.status = "removed"
            item.snoozed_until = None
            removed_ids.append(item.id)
        else:
            skipped.append((item.id, _skip_reason(item)))
        session.add(item)

    full = not skipped
    if full:
        for item in items:
            item.source_receipt_id = None
            session.add(item)
        session.flush()
        session.delete(receipt)
    session.commit()
    return UndoResult(removed_ids, skipped, receipt_deleted=full, expired=False)


def undo_add(
    session: Session, *, user_id: int, item_id: int, now: datetime
) -> UndoResult:
    item = session.get(PantryItem, item_id)
    if item is None or item.user_id != user_id:
        return UndoResult([], [], False, expired=False)
    if _expired(item.created_at, now):
        return UndoResult([], [], False, expired=True)
    assert item.id is not None
    if not is_untouched(item):
        return UndoResult([], [(item.id, _skip_reason(item))], False, expired=False)
    item.status = "removed"
    item.snoozed_until = None
    session.add(item)
    session.commit()
    return UndoResult([item.id], [], receipt_deleted=False, expired=False)
```

(`datetime`, `timedelta`, `timezone` are already imported in `pantry_service.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_v2_undo.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/pantry_service.py tests/test_v2_undo.py
git commit -m "feat(pantry): undo_receipt/undo_add with TTL and untouched-only semantics"
```

---

## Task 8: Renderer — undo keyboards and result text

**Files:**
- Modify: `app/renderer.py` (`build_undo_keyboard`, `build_undo_add_keyboard`, `render_undo_result`)
- Test: `tests/test_v2_undo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_v2_undo.py (append)
from app.pantry_service import UndoResult
from app.renderer import (
    build_undo_keyboard, build_undo_add_keyboard, render_undo_result,
)


def test_undo_keyboards():
    assert build_undo_keyboard(receipt_id=12)[0][0].callback_data == "undo:receipt:12"
    assert build_undo_add_keyboard(item_id=7)[0][0].callback_data == "undo:add:7"


def test_render_undo_result_full_and_partial():
    full = render_undo_result(UndoResult([1, 2], [], receipt_deleted=True, expired=False))
    assert "Undone" in full and "2" in full
    partial = render_undo_result(
        UndoResult([1], [(3, "eaten")], receipt_deleted=False, expired=False)
    )
    assert "skipped #3 (eaten)" in partial
    expired = render_undo_result(UndoResult([], [], False, expired=True))
    assert "expired" in expired.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_v2_undo.py -k "keyboards or undo_result" -v`
Expected: FAIL — import errors.

- [ ] **Step 3: Implement the renderer pieces**

```python
def build_undo_keyboard(*, receipt_id: int) -> list[list[CallbackButton]]:
    return [[CallbackButton(text="Undo", callback_data=f"undo:receipt:{receipt_id}")]]


def build_undo_add_keyboard(*, item_id: int) -> list[list[CallbackButton]]:
    return [[CallbackButton(text="Undo", callback_data=f"undo:add:{item_id}")]]


def render_undo_result(result) -> str:
    if result.expired:
        return "Undo window expired (10 min) - use /delete <id> instead."
    if not result.removed_ids and not result.skipped:
        return "Nothing to undo."
    parts = [f"Undone: removed {len(result.removed_ids)} item(s)."]
    if result.skipped:
        skipped = ", ".join(f"#{i} ({why})" for i, why in result.skipped)
        parts.append(f"Skipped {skipped}.")
    return " ".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_v2_undo.py -k "keyboards or undo_result" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/renderer.py tests/test_v2_undo.py
git commit -m "feat(renderer): undo keyboards and result text"
```

---

## Task 9: Wire undo into the bot

**Files:**
- Modify: `app/bot.py` (`handle_photo` attaches undo keyboard; `_handle_pending_callback`/`handle_callback` handle undo verbs; the add-apply path attaches its undo keyboard)
- Test: `tests/test_v2_undo.py` (handler-level, using the existing in-memory style)

- [ ] **Step 1: Write a handler test using a stub message**

```python
# tests/test_v2_undo.py (append)
from unittest.mock import AsyncMock
from datetime import datetime, timezone
import app.bot as bot
from app.bot import handle_callback


class _StubCbMessage:
    def __init__(self):
        self.edit_text = AsyncMock()
        self.answer = AsyncMock()


class _StubCallback:
    def __init__(self, data, user_id=1):
        self.data = data
        self.from_user = type("U", (), {"id": user_id})()
        self.message = _StubCbMessage()
        self.answer = AsyncMock()


@pytest.mark.asyncio
async def test_handle_callback_undo_receipt(session, monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_TELEGRAM_USER_ID", 1)
    now = datetime.now(timezone.utc)
    r = _receipt(session, scanned_at=now)
    _ritem(session, r.id, "A")
    cb = _StubCallback(f"undo:receipt:{r.id}")

    def factory():
        return session

    # session fixture yields a live session; wrap so `with factory() as s` works
    class _Ctx:
        def __enter__(self_): return session
        def __exit__(self_, *a): return False
    monkeypatch.setattr(bot, "_noop_user_created", bot._noop_user_created)

    await handle_callback(cb, session_factory=lambda: _Ctx(),
                          now_provider=lambda tz: now)
    cb.message.edit_text.assert_awaited()
    assert "Undone" in cb.message.edit_text.await_args.args[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_v2_undo.py::test_handle_callback_undo_receipt -v`
Expected: FAIL — `handle_callback` does not branch on `undo_receipt`.

- [ ] **Step 3: Handle undo verbs in `handle_callback`**

In `handle_callback`, after the `apply`/`cancel` block and before the generic `item_id` action block, add:

```python
        if action.verb in ("undo_receipt", "undo_add"):
            target_id = action.item_id
            assert target_id is not None
            now = datetime.now(timezone.utc)
            if action.verb == "undo_receipt":
                result = undo_receipt(session, user_id=user.telegram_id,
                                      receipt_id=target_id, now=now)
            else:
                result = undo_add(session, user_id=user.telegram_id,
                                  item_id=target_id, now=now)
            try:
                await cb.message.edit_text(render_undo_result(result))
            except Exception as exc:
                log.warning("undo_edit_failed", extra={"error_class": type(exc).__name__})
            await cb.answer("undone" if result.removed_ids else "nothing undone")
            return
```

Add imports at the top of `bot.py`: `undo_receipt`, `undo_add` from `app.pantry_service`; `render_undo_result`, `build_undo_keyboard`, `build_undo_add_keyboard` from `app.renderer`.

- [ ] **Step 4: Attach the undo keyboard to the receipt reply**

In `handle_photo`, change the final reply to include the keyboard and capture the sent message (needed later by refine):

```python
        keyboard = (
            to_aiogram_keyboard(build_undo_keyboard(receipt_id=summary.receipt_id))
            if summary.receipt_id is not None and summary.inserted_food_count
            else None
        )
        sent = await msg.answer(render_ingest_reply(summary, today=today), reply_markup=keyboard)
```

- [ ] **Step 5: Attach the undo keyboard to the applied-add confirmation**

In `_handle_pending_callback`, in the `add` branch, after `mark_applied`/`commit`, change the edit to include the undo button:

```python
    try:
        await cb.message.edit_text(
            render_applied_add(item_id=new_id, payload=payload),
            reply_markup=to_aiogram_keyboard(build_undo_add_keyboard(item_id=new_id)),
        )
```

- [ ] **Step 6: Run the undo handler test + full suite**

Run: `uv run pytest tests/test_v2_undo.py -v && uv run pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/bot.py tests/test_v2_undo.py
git commit -m "feat(bot): wire undo buttons on receipt and add replies"
```

---

# FEATURE D — Websearch Shelf-Life Accuracy

## Task 10: Search client protocol, result type, and fake

**Files:**
- Create: `app/refine_service.py`
- Modify: `tests/fakes.py` (add `FakeSearchClient`)
- Test: `tests/test_v2_websearch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_v2_websearch.py
import asyncio
from app.refine_service import ShelfLifeSearchResult, resolve_search_days, SEARCH_MIN_CONFIDENCE


def test_resolve_accepts_confident_in_range():
    r = ShelfLifeSearchResult(days=14, confidence=0.9, cost_micros_usd=500)
    assert resolve_search_days(r) == 14


def test_resolve_rejects_low_confidence():
    r = ShelfLifeSearchResult(days=14, confidence=SEARCH_MIN_CONFIDENCE - 0.01, cost_micros_usd=500)
    assert resolve_search_days(r) is None


def test_resolve_rejects_out_of_range_or_missing():
    assert resolve_search_days(ShelfLifeSearchResult(days=None, confidence=0.9, cost_micros_usd=0)) is None
    assert resolve_search_days(ShelfLifeSearchResult(days=0, confidence=0.9, cost_micros_usd=0)) is None
    assert resolve_search_days(ShelfLifeSearchResult(days=999, confidence=0.9, cost_micros_usd=0)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_v2_websearch.py -v`
Expected: FAIL — module `app.refine_service` does not exist.

- [ ] **Step 3: Create `app/refine_service.py` with the protocol, result, and resolver**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


SEARCH_MIN_CONFIDENCE = 0.7
SHELF_LIFE_DAYS_MIN = 1
SHELF_LIFE_DAYS_MAX = 730


@dataclass(frozen=True)
class ShelfLifeSearchResult:
    days: Optional[int]
    confidence: float
    cost_micros_usd: Optional[int]


class ShelfLifeSearchClient(Protocol):
    async def lookup_shelf_life(
        self, *, name: str, category: Optional[str]
    ) -> ShelfLifeSearchResult: ...


def resolve_search_days(result: ShelfLifeSearchResult) -> Optional[int]:
    if result.confidence < SEARCH_MIN_CONFIDENCE:
        return None
    if result.days is None:
        return None
    if not (SHELF_LIFE_DAYS_MIN <= result.days <= SHELF_LIFE_DAYS_MAX):
        return None
    return result.days
```

- [ ] **Step 4: Add `FakeSearchClient` to `tests/fakes.py`**

```python
# tests/fakes.py (append)
from app.refine_service import ShelfLifeSearchClient, ShelfLifeSearchResult


@dataclass
class FakeSearchClient(ShelfLifeSearchClient):
    by_name: dict[str, ShelfLifeSearchResult] = field(default_factory=dict)
    default: Optional[ShelfLifeSearchResult] = None
    calls: list[str] = field(default_factory=list)

    async def lookup_shelf_life(self, *, name, category):
        self.calls.append(name)
        if name in self.by_name:
            return self.by_name[name]
        assert self.default is not None, f"no canned result for {name!r}"
        return self.default
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_v2_websearch.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/refine_service.py tests/fakes.py tests/test_v2_websearch.py
git commit -m "feat(refine): search client protocol, result type, resolver, fake"
```

---

## Task 11: Receipt refine service (untouched-only, write-back, cost accrual)

**Files:**
- Modify: `app/refine_service.py` (add `RefineResult`, `refine_receipt_items`)
- Test: `tests/test_v2_websearch.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_v2_websearch.py (append)
import pytest
from datetime import date, datetime, timezone, timedelta
from sqlmodel import SQLModel, Session, create_engine
from app.cache import get_cached
from app.models import PantryItem, Receipt, User
from app.refine_service import refine_receipt_items, ShelfLifeSearchResult
from tests.fakes import FakeSearchClient


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        db.commit()
        yield db


def _item(session, name, *, days=5, status="active", source="llm", rid=None):
    item = PantryItem(
        user_id=1, raw_name=name, normalized_name=name.lower(), category="dairy",
        qty=1.0, unit=None, purchased_on=date(2026, 5, 28), shelf_life_days=days,
        shelf_life_source=source, ingest_shelf_life_source="llm",
        expires_on=date(2026, 5, 28) + timedelta(days=days), status=status,
        created_via="receipt", source_receipt_id=rid,
        created_at=datetime.now(timezone.utc),
    )
    session.add(item); session.commit(); session.refresh(item)
    return item


@pytest.mark.asyncio
async def test_refine_updates_untouched_item_and_writes_cache(session):
    item = _item(session, "Kefir", days=7)
    search = FakeSearchClient(by_name={
        "Kefir": ShelfLifeSearchResult(days=14, confidence=0.9, cost_micros_usd=400),
    })
    result = await refine_receipt_items(
        session, search, user_id=1, item_ids=[item.id], today=date(2026, 5, 28),
    )
    assert result.updated_ids == [item.id]
    assert result.total_cost_micros == 400
    session.refresh(item)
    assert item.shelf_life_days == 14
    assert item.expires_on == date(2026, 5, 28) + timedelta(days=14)
    cached = get_cached(session, 1, "kefir")
    assert cached is not None and cached.days == 14


@pytest.mark.asyncio
async def test_refine_skips_touched_items(session):
    corrected = _item(session, "Tofu", source="user_correction")
    eaten = _item(session, "Milk", status="eaten")
    search = FakeSearchClient(default=ShelfLifeSearchResult(days=20, confidence=0.95, cost_micros_usd=100))
    result = await refine_receipt_items(
        session, search, user_id=1, item_ids=[corrected.id, eaten.id], today=date(2026, 5, 28),
    )
    assert result.updated_ids == []
    # search should not even be called for ineligible items
    assert search.calls == []


@pytest.mark.asyncio
async def test_refine_skips_low_confidence_keeps_estimate(session):
    item = _item(session, "Brie", days=7)
    search = FakeSearchClient(default=ShelfLifeSearchResult(days=30, confidence=0.4, cost_micros_usd=200))
    result = await refine_receipt_items(
        session, search, user_id=1, item_ids=[item.id], today=date(2026, 5, 28),
    )
    assert result.updated_ids == []
    session.refresh(item)
    assert item.shelf_life_days == 7  # unchanged
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_v2_websearch.py -k refine -v`
Expected: FAIL — `cannot import name 'refine_receipt_items'`

- [ ] **Step 3: Implement `RefineResult` and `refine_receipt_items`**

Append to `app/refine_service.py`:

```python
from datetime import date, timedelta

from sqlmodel import Session

from app.cache import put_cached
from app.models import PantryItem
from app.pantry_service import is_untouched


@dataclass(frozen=True)
class RefineResult:
    updated_ids: list[int]
    total_cost_micros: Optional[int]


async def refine_receipt_items(
    session: Session,
    search: ShelfLifeSearchClient,
    *,
    user_id: int,
    item_ids: list[int],
    today: date,
) -> RefineResult:
    updated: list[int] = []
    total_cost = 0
    saw_cost = False
    for item_id in item_ids:
        item = session.get(PantryItem, item_id)
        if item is None or item.user_id != user_id or not is_untouched(item):
            continue
        result = await search.lookup_shelf_life(name=item.raw_name, category=item.category)
        if result.cost_micros_usd is not None:
            total_cost += result.cost_micros_usd
            saw_cost = True
        days = resolve_search_days(result)
        if days is None:
            continue
        # re-check untouched: nothing else ran, but keep the guard explicit
        item.shelf_life_days = days
        item.shelf_life_source = "websearch"
        item.expires_on = item.purchased_on + timedelta(days=days)
        session.add(item)
        put_cached(
            session, user_id, item.normalized_name,
            days=days, category=item.category, confidence=result.confidence,
            source="llm", commit=False,
        )
        assert item.id is not None
        updated.append(item.id)
    session.commit()
    return RefineResult(updated, total_cost if saw_cost else None)
```

**Note on `shelf_life_source="websearch"`:** the `ShelfLifeSource` Literal in `models.py` does not include `"websearch"`, but the column is a plain `str`, so this persists fine. Add `"websearch"` to the `ShelfLifeSource` Literal in `app/models.py` for documentation/type accuracy (no migration needed — it's a `str` column).

**Note on `put_cached`:** it is a no-op if a row already exists (see `cache.py:26-28`). Since refine only runs on cache-miss items, this writes the searched value. `source="llm"` keeps it overridable by a future user correction.

- [ ] **Step 4: Add `"websearch"` to the `ShelfLifeSource` Literal**

In `app/models.py`, change:
```python
ShelfLifeSource = Literal["cache", "llm", "manual_fallback", "user_correction", "websearch"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_v2_websearch.py -k refine -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/refine_service.py app/models.py tests/test_v2_websearch.py
git commit -m "feat(refine): receipt refine service with untouched guard + cache write-back"
```

---

## Task 12: Background refine wiring in the bot (edit-in-place + cost accrual)

**Files:**
- Modify: `app/bot.py` (`handle_photo` spawns refine; `build_dispatcher` injects `search` + a `spawn` hook)
- Test: `tests/test_v2_websearch.py`

- [ ] **Step 1: Write the failing test (run refine synchronously via injected spawn)**

```python
# tests/test_v2_websearch.py (append)
from unittest.mock import AsyncMock
import app.bot as bot
from app.bot import handle_photo
from app.llm import LLMResult, ParseResult, ParsedItem
from tests.fakes import FakeLLMClient, FakeSearchClient


class _Ctx:
    def __init__(self, session): self._s = session
    def __enter__(self): return self._s
    def __exit__(self, *a): return False


class _StubMsg:
    def __init__(self, chat_id=1):
        self.chat = type("C", (), {"id": chat_id, "type": "private"})()
        self.from_user = type("U", (), {"id": 1})()
        self.photo = [type("P", (), {"file_id": "fid"})()]
        self.answer = AsyncMock(return_value=type("Sent", (), {"message_id": 99})())


@pytest.mark.asyncio
async def test_handle_photo_spawns_refine_and_edits(session, monkeypatch):
    monkeypatch.setattr(bot, "ALLOWED_TELEGRAM_USER_ID", 1)
    llm = FakeLLMClient(canned=LLMResult(parse=ParseResult(
        purchase_date=date(2026, 5, 28), purchase_date_confidence=0.9,
        items=[ParsedItem(is_food=True, name="Kefir", est_shelf_life_days=7, confidence=0.9)],
    ), cost_micros_usd=1000))
    search = FakeSearchClient(default=ShelfLifeSearchResult(days=14, confidence=0.95, cost_micros_usd=300))
    edits: list = []
    bot_obj = type("B", (), {})()
    bot_obj.edit_message_text = AsyncMock(side_effect=lambda **kw: edits.append(kw))

    spawned = []
    def spawn(coro):
        spawned.append(coro)  # capture instead of create_task

    msg = _StubMsg()
    await handle_photo(
        msg, session_factory=lambda: _Ctx(session),
        now_provider=lambda tz: datetime(2026, 5, 28, tzinfo=timezone.utc),
        llm=llm, photo_downloader=AsyncMock(return_value=b"jpg"),
        search=search, spawn=spawn, bot=bot_obj,
    )
    assert msg.answer.await_count == 1          # fast reply sent
    assert len(spawned) == 1                      # refine scheduled
    await spawned[0]                              # run the background coro
    assert bot_obj.edit_message_text.await_count == 1
    assert "Kefir" in edits[0]["text"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_v2_websearch.py::test_handle_photo_spawns_refine_and_edits -v`
Expected: FAIL — `handle_photo` has no `search`/`spawn`/`bot` parameters.

- [ ] **Step 3: Add refine spawning to `handle_photo`**

Extend `handle_photo`'s signature with `search`, `spawn`, and `bot`, and after sending the fast reply, schedule the refine. The refine coroutine opens its **own** session, runs `refine_receipt_items`, accrues cost into the receipt, and edits the message:

```python
async def handle_photo(
    msg, *, session_factory, now_provider, llm, photo_downloader,
    search=None, spawn=None, bot=None,
    on_user_created=_noop_user_created,
) -> None:
    ...  # unchanged up through computing `summary`
        keyboard = (
            to_aiogram_keyboard(build_undo_keyboard(receipt_id=summary.receipt_id))
            if summary.receipt_id is not None and summary.inserted_food_count
            else None
        )
        sent = await msg.answer(render_ingest_reply(summary, today=today), reply_markup=keyboard)

    if (
        search is not None and spawn is not None and bot is not None
        and summary.receipt_id is not None and summary.uncached_item_ids
    ):
        chat_id = msg.chat.id
        message_id = sent.message_id
        receipt_id = summary.receipt_id

        async def _run_refine():
            with session_factory() as refine_session:
                result = await refine_receipt_items(
                    refine_session, search, user_id=summary.uncached_item_ids and msg.from_user.id,
                    item_ids=summary.uncached_item_ids, today=today,
                )
                if not result.updated_ids:
                    return
                _accrue_receipt_cost(refine_session, receipt_id, result.total_cost_micros)
                refined = frozenset(result.updated_ids)
                _refresh_summary_from_db(refine_session, summary)
                text = render_ingest_reply(summary, today=today, refined_ids=refined)
            try:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id, text=text,
                    reply_markup=to_aiogram_keyboard(build_undo_keyboard(receipt_id=receipt_id)),
                )
            except Exception as exc:
                log.warning("refine_edit_failed", extra={"error_class": type(exc).__name__})

        spawn(_run_refine())
```

Add two small helpers in `bot.py`:

```python
def _accrue_receipt_cost(session, receipt_id, add_micros):
    if not add_micros:
        return
    receipt = session.get(Receipt, receipt_id)
    if receipt is None:
        return
    receipt.llm_cost_micros_usd = (receipt.llm_cost_micros_usd or 0) + add_micros
    session.add(receipt)
    session.commit()


def _refresh_summary_from_db(session, summary):
    for idx, item_id in enumerate(summary.inserted_item_ids):
        item = session.get(PantryItem, item_id)
        if item is None:
            continue
        summary.inserted_item_expires_on[idx] = item.expires_on
        summary.inserted_item_shelf_life_days[idx] = item.shelf_life_days
```

Add imports: `from app.refine_service import refine_receipt_items`, `from app.models import Receipt` (and `PantryItem` already imported).

- [ ] **Step 4: Add `refined_ids` to `render_ingest_reply`**

In `render_ingest_reply`, change the per-item loop to mark refined lines:

```python
def render_ingest_reply(summary, *, today, refined_ids=frozenset()):
    ...
    for item_id, name, expires_on, shelf_life_days in zip(...):
        mark = " ✓refined" if item_id in refined_ids else ""
        lines.append(
            f"  - #{item_id} {name} - exp {_fmt_date(expires_on, today=today)} ({shelf_life_days}d){mark}"
        )
```

- [ ] **Step 5: Inject `search`/`spawn`/`bot` in `build_dispatcher`**

`build_dispatcher` gains a `search` parameter. The `on_photo` closure passes `search=search`, `spawn=asyncio.create_task`, `bot=bot`. Add `import asyncio` at the top of `bot.py`.

```python
    async def on_photo(message):
        await handle_photo(
            message, session_factory=session_factory, now_provider=now_provider,
            llm=llm, photo_downloader=downloader,
            search=search, spawn=asyncio.create_task, bot=bot,
            on_user_created=on_user_created,
        )
```

- [ ] **Step 6: Run the test + full suite**

Run: `uv run pytest tests/test_v2_websearch.py -v && uv run pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/bot.py app/renderer.py tests/test_v2_websearch.py
git commit -m "feat(bot): background websearch refine with edit-in-place + cost accrual"
```

---

## Task 13: Inline `/add` websearch on cache miss

**Files:**
- Modify: `app/correction_service.py` (`propose_add` accepts an optional `search` and uses it on cache miss before falling back to defaults/estimate)
- Modify: `app/bot.py` (`handle_add` passes `search`)
- Test: `tests/test_v2_websearch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_v2_websearch.py (append)
from app.correction_service import propose_add
from tests.fakes import FakeTextLLMClient
from app.llm import ProposedAddItem


@pytest.mark.asyncio
async def test_propose_add_uses_search_on_cache_miss(session):
    text_llm = FakeTextLLMClient(canned_add=([
        ProposedAddItem(name="Kefir", explicit_user_expiry=False,
                        estimated_shelf_life_days=7, confidence=0.9)
    ], 500))
    search = FakeSearchClient(default=ShelfLifeSearchResult(days=14, confidence=0.95, cost_micros_usd=200))
    proposals, _ = await propose_add(
        session, llm=text_llm, user_id=1, user_text="kefir",
        today=date(2026, 5, 28), tz="America/Detroit", search=search,
    )
    assert proposals[0].payload.shelf_life_days == 14
    assert proposals[0].payload.shelf_life_source == "websearch"


@pytest.mark.asyncio
async def test_propose_add_skips_search_when_user_gave_expiry(session):
    text_llm = FakeTextLLMClient(canned_add=([
        ProposedAddItem(name="Kefir", explicit_user_expiry=True,
                        shelf_life_days=3, confidence=0.9)
    ], 500))
    search = FakeSearchClient(default=ShelfLifeSearchResult(days=14, confidence=0.95, cost_micros_usd=200))
    proposals, _ = await propose_add(
        session, llm=text_llm, user_id=1, user_text="kefir keeps 3 days",
        today=date(2026, 5, 28), tz="America/Detroit", search=search,
    )
    assert proposals[0].payload.shelf_life_days == 3
    assert search.calls == []  # explicit expiry -> no search
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_v2_websearch.py -k propose_add -v`
Expected: FAIL — `propose_add` has no `search` parameter.

- [ ] **Step 3: Thread `search` through `propose_add`**

Change the signature to add `search: Optional[ShelfLifeSearchClient] = None`. In the `else` (non-explicit) branch, the precedence becomes **cache → websearch → defaults → estimate**. Replace the current non-explicit block:

```python
        else:
            cached = get_cached(session, user_id, normalized)
            if cached is not None:
                days = cached.days
                shelf_life_source = "cache"
                ingest_source = "cache"
                category = category or cached.category
            else:
                searched = None
                if search is not None:
                    searched = resolve_search_days(
                        await search.lookup_shelf_life(name=parsed.name, category=category)
                    )
                if searched is not None:
                    days = searched
                    shelf_life_source = "websearch"
                    ingest_source = "llm"
                    put_cached(session, user_id, normalized, days=days,
                               category=category, confidence=0.9, source="llm", commit=False)
                else:
                    default = lookup_default(normalized)
                    if default is not None:
                        days = default.days
                        shelf_life_source = "manual_fallback"
                        ingest_source = "manual_fallback"
                        category = category or default.category
                    elif parsed.estimated_shelf_life_days is not None:
                        days = parsed.estimated_shelf_life_days
                        shelf_life_source = "llm"
                        ingest_source = "llm"
                    else:
                        days = CONSERVATIVE_FALLBACK_DAYS
                        shelf_life_source = "manual_fallback"
                        ingest_source = "manual_fallback"
            expires_on = today + timedelta(days=days)
```

Add imports to `correction_service.py`:
```python
from app.cache import get_cached, put_cached, write_user_correction
from app.refine_service import ShelfLifeSearchClient, resolve_search_days
```
And widen the `AddPayload.shelf_life_source` Literal to include `"websearch"`:
```python
    shelf_life_source: Literal["user_correction", "cache", "manual_fallback", "llm", "websearch"]
```

- [ ] **Step 4: Pass `search` from `handle_add`**

`handle_add` gains a `search=None` parameter and forwards it to `propose_add`. In `build_dispatcher`, `on_add` passes `search=search`.

- [ ] **Step 5: Run tests + full suite**

Run: `uv run pytest tests/test_v2_websearch.py -k propose_add -v && uv run pytest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/correction_service.py app/bot.py tests/test_v2_websearch.py
git commit -m "feat(add): inline websearch on cache miss with cache write-back"
```

---

## Task 14: Anthropic web_search client + production wiring

**Files:**
- Modify: `app/refine_service.py` (add `AnthropicSearchClient`)
- Modify: `app/settings.py` (`anthropic_search_model`), `bin/run.py` (construct + inject `search`)
- Test: `tests/test_v2_websearch.py` (client parses a stubbed tool response)

- [ ] **Step 0: Confirm the web_search tool shape from current docs**

Before implementing, fetch current Anthropic docs for the server-side `web_search` tool (request block shape, result content, and usage/cost fields). Use the context7 MCP (`resolve-library-id` → `query-docs` for the Anthropic SDK) or the official docs. Adjust the parsing in Step 3 to match what the docs show; the test in Step 1 stubs the SDK so it stays deterministic regardless.

- [ ] **Step 1: Write the failing test (SDK fully stubbed)**

```python
# tests/test_v2_websearch.py (append)
from unittest.mock import AsyncMock, MagicMock
from app.refine_service import AnthropicSearchClient


@pytest.mark.asyncio
async def test_anthropic_search_client_parses_days_and_cost():
    # The client asks the model to return a small JSON object after searching.
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text='{"days": 14, "confidence": 0.9}')]
    msg.usage = MagicMock(input_tokens=100, output_tokens=10)
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(return_value=msg)
    client = AnthropicSearchClient(sdk=sdk, model="claude-sonnet-4-6")
    result = await client.lookup_shelf_life(name="Kefir", category="dairy")
    assert result.days == 14
    assert result.confidence == 0.9
    assert result.cost_micros_usd == 450  # 100*3 + 10*15 (sonnet pricing)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_v2_websearch.py::test_anthropic_search_client_parses_days_and_cost -v`
Expected: FAIL — `cannot import name 'AnthropicSearchClient'`

- [ ] **Step 3: Implement `AnthropicSearchClient`**

Append to `app/refine_service.py`. Reuse the cost helper pattern from `llm.py` (duplicated minimally to avoid a circular import; keep the same per-model micro pricing table or import `_PRICE_MICROS_PER_TOKEN_BY_MODEL` from `app.llm`).

```python
import json
import logging
from typing import Any

log = logging.getLogger(__name__)

SEARCH_SYSTEM_PROMPT = (
    "You research how long a grocery item stays good under normal home storage. "
    "Use web search to verify. Then reply with ONLY a JSON object: "
    '{"days": <int 1..730>, "confidence": <float 0..1>}. '
    "Use conservative estimates. No prose."
)


class AnthropicSearchClient(ShelfLifeSearchClient):
    def __init__(self, sdk, model: str, *, max_uses: int = 2):
        self._sdk = sdk
        self._model = model
        self._max_uses = max_uses

    async def lookup_shelf_life(self, *, name, category):
        from app.llm import _PRICE_MICROS_PER_TOKEN_BY_MODEL  # local import avoids cycle
        prompt = f"Item: {name}" + (f" (category: {category})" if category else "")
        try:
            msg = await self._sdk.messages.create(
                model=self._model,
                max_tokens=512,
                system=SEARCH_SYSTEM_PROMPT,
                tools=[{"type": "web_search_20250305", "name": "web_search",
                        "max_uses": self._max_uses}],
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            log.warning("search_transport_failed", extra={"error_class": type(exc).__name__})
            return ShelfLifeSearchResult(days=None, confidence=0.0, cost_micros_usd=None)

        price = _PRICE_MICROS_PER_TOKEN_BY_MODEL.get(self._model)
        cost = None
        usage = getattr(msg, "usage", None)
        if price is not None and usage is not None:
            cost = usage.input_tokens * price["input"] + usage.output_tokens * price["output"]

        text = "".join(
            b.text for b in msg.content if getattr(b, "type", None) == "text"
        ).strip()
        try:
            data: dict[str, Any] = json.loads(text[text.index("{"): text.rindex("}") + 1])
            return ShelfLifeSearchResult(
                days=int(data["days"]), confidence=float(data["confidence"]),
                cost_micros_usd=cost,
            )
        except Exception as exc:
            log.warning("search_parse_failed", extra={"error_class": type(exc).__name__})
            return ShelfLifeSearchResult(days=None, confidence=0.0, cost_micros_usd=cost)
```

(The `web_search_20250305` tool type and pricing are confirmed in Step 0; adjust if docs differ.)

- [ ] **Step 4: Add the search model setting**

In `app/settings.py`:
```python
    anthropic_search_model: str = Field(default="claude-sonnet-4-6", alias="ANTHROPIC_SEARCH_MODEL")
```

- [ ] **Step 5: Construct + inject the client in `bin/run.py`**

In `bin/run.py`, build an `AnthropicSearchClient(sdk, settings.anthropic_search_model)` and pass it into `build_dispatcher(..., search=search)`. (Mirror how the existing `AnthropicLLMClient` / `AnthropicTextLLMClient` are constructed.)

- [ ] **Step 6: Run test + full suite**

Run: `uv run pytest tests/test_v2_websearch.py -v && uv run pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/refine_service.py app/settings.py bin/run.py tests/test_v2_websearch.py
git commit -m "feat(refine): Anthropic web_search client + production wiring"
```

---

## Task 15: `/stats` reflects websearch cost + final full-suite gate

**Files:**
- Test: `tests/test_v2_websearch.py` (verify accrued cost flows into stats)

- [ ] **Step 1: Write the integration assertion**

Because search cost is accrued into `Receipt.llm_cost_micros_usd` (Task 12), `compute_stats` already counts it with no code change. Add a regression test that proves it:

```python
# tests/test_v2_websearch.py (append)
from app.pantry_service import compute_stats
from app.models import Receipt
from app.bot import _accrue_receipt_cost


def test_accrued_search_cost_shows_in_stats(session):
    r = Receipt(user_id=1, photo_file_id="r1", purchase_date=date(2026, 5, 28),
                purchase_date_source="receipt", scanned_at=datetime.now(timezone.utc),
                llm_cost_micros_usd=1000)
    session.add(r); session.commit()
    _accrue_receipt_cost(session, r.id, 300)  # simulate refine cost
    stats = compute_stats(session, user_id=1, now=datetime.now(timezone.utc))
    assert stats.total_cost_micros_usd == 1300
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_v2_websearch.py::test_accrued_search_cost_shows_in_stats -v`
Expected: PASS

- [ ] **Step 3: Run the entire suite + lint**

Run: `uv run pytest && uv run ruff check`
Expected: PASS, no lint errors.

- [ ] **Step 4: Commit**

```bash
git add tests/test_v2_websearch.py
git commit -m "test(stats): websearch cost accrues into receipt and stats"
```

---

## Self-Review

**Spec coverage**
- Undo (inline button, 10-min TTL, untouched-only, full-vs-partial receipt deletion, cache kept): Tasks 6–9. ✔
- List rendering (category-primary, qty/unit, urgency icons; digest decorations): Tasks 1–3. ✔
- Next-year indicator (calendar-year rule): Task 1. ✔
- Recognition hardening (`track_worthy`, report + `/add` override): Tasks 4–5. ✔
- Websearch accuracy (cache-miss trigger, write-back, background refine, edit-in-place, untouched guard, inline `/add`, native tool, cost in stats): Tasks 10–15. ✔
- Family/shared-pantry: correctly **absent** (deferred to v3). ✔

**Type consistency**
- `is_untouched` defined once (Task 7), reused by `refine_receipt_items` (Task 11). ✔
- `UndoResult` fields (`removed_ids`, `skipped`, `receipt_deleted`, `expired`) used identically across Tasks 7–9. ✔
- `ShelfLifeSearchResult` / `resolve_search_days` defined in Task 10, used in Tasks 11 and 13. ✔
- `"websearch"` added to both `models.ShelfLifeSource` and `AddPayload.shelf_life_source` literals (Tasks 11, 13). ✔
- `render_ingest_reply` gains `refined_ids` with a default, preserving existing call sites and tests (Tasks 5, 12). ✔

**Accepted limitations (documented in-plan)**
- A name-only `/correct` does not flip `shelf_life_source`, so undo/refine treat such an item as "untouched" (Task 7 note).
- Websearch cost is folded into `Receipt.llm_cost_micros_usd` rather than tracked in a separate column (no migration); `/stats` therefore reports combined LLM + search spend (Tasks 12, 15).

**Known follow-up for the implementer**
- Task 14 Step 0 requires confirming the exact Anthropic `web_search` tool block + cost fields from current docs before finalizing the client; the test stubs the SDK so it stays deterministic.
