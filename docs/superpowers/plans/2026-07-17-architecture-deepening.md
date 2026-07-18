# Architecture Deepening Implementation Plan

> **Execution mode:** Inline, single-agent execution only. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Concentrate five shallow, leaking seams in `app/bot.py` into deep modules — a per-user client resolver, a callback dispatch table, a localized-view seam, a decomposed handler layer, and (optional) a shared JSON-repair tail — without changing any user-visible behaviour.

**Architecture:** Four ordered implementation tracks plus one audited exclusion. Track 1 replaces the five `_select_*` helpers and the eight-way client dep-threading with one `PerUserClients` resolver. Track 2 turns all callback parsers/routes + inline ack/edit idioms into a typed callback envelope and registry package behind the existing `callback_dispatch` seam. Track 3 adds `app/views.py`, composing translation/cached-name lookup + pure renderers across the 19 live render-prep sites. Track 4 splits `bot.py` into `app/handlers/*` after the cross-cutting glue lives in leaf modules. Track 5 was speculative and is excluded after live inspection confirmed that the provider repair tails have different observable contracts.

**Tech Stack:** Python 3.12, aiogram, SQLModel, pytest. No new dependencies. **No database migration** in any track — every change is code-shape only.

**Source:** Architecture review 2026-07-17 (`architecture-review-*.html`). Vocabulary: module, interface, deep/shallow, seam, adapter, leverage, locality.

## Global Constraints

- Run tests with `uv run pytest`. Use the smallest focused red/green test for each task, a focused regression set after each track, and the full suite only after all implementation tracks and again after final review/refactoring.
- **No behaviour change.** English output stays byte-identical (`tests/test_i18n.py` and renderer tests pin exact strings). Callback acks, edit-or-resend fallbacks, and provider-fallback logging (`llm_provider_fallback`) must be preserved verbatim.
- **No migration, no model changes.** If a task seems to need a schema change, stop — it is out of scope.
- Match existing patterns: keyword-only service args, `session: Session` first, explicit `today: date`, `# type: ignore[call-arg]` on `Settings()` in tests.
- Keep commits logically scoped to corrected plan tasks. Do not add a co-author trailer for an agent that did not author the implementation.

## Correctness Amendments (binding; supersede conflicting steps below)

The 2026-07-17 live-tree refresh found contract errors in the draft. The
following amendments are authoritative for implementation.

### Checkpoint evidence

- `ProviderSelector` has `for_provider`, `available_providers`,
  `default_provider`, and `_fallback`; missing providers raise
  `LLMProviderNotConfigured` when fallback is disabled.
- The six capability selectors in `app/llm.py` subclass `ProviderSelector`.
- Five `_select_*` helpers have 14 live call sites in `app/bot.py`.
- Translation prep has 19 live call sites (15 async translations, four cached
  callback refreshes), not 21.
- `parse_callback` covers the 24 `Verb` values in `app/commands.py`, but it does
  not parse `help:*` or `item:*`; those use separate parsing paths today.
- `_build_llm_clients` returns the eight-field `LLMBundle` described below and
  `build_dispatcher` threads all eight seams separately.
- `app/bot.py` is 3,627 lines at the checkpoint.

### Track 1 corrections

- Keep the public capability API from the original interface block:
  `clients.image(user)`, `text(user)`, `profile(user)`, `selection(user)`,
  `recipe(user)`, `nutrition(user)`, and `search(user)`. Store raw seams in
  private fields so methods and fields do not collide. `translation` remains a
  plain property to preserve current default-provider translation behaviour.
- Type capability inputs/outputs with their existing Protocols (and a small
  selector Protocol) rather than exposing `Any`. `for_tests` may accept bare
  fakes but must produce the same typed object.
- Preserve `_available_llm_providers` behaviour through `PerUserClients`; `/llm`
  must still list text-capable providers and flag text-only choices.
- Migrate directly from separate deps to `clients` after the additive wiring
  test; do not introduce a repository-wide intermediate `resolve(...)` idiom
  that is immediately removed.

### Track 2 corrections

- Implement `app/callbacks/` as a package from the start; `app/callbacks.py`
  and `app/callbacks/<group>.py` cannot coexist. Registry population must use
  explicit imports and reject duplicate route registration.
- Add an additive `parse_callback_request` contract that delegates to the
  existing command/item/help parsers and returns a discriminated, typed
  top-level envelope. Keep existing parsers public and byte-compatible.
- Registry keys are typed route keys from that envelope, not arbitrary strings.
  Tests must cover every currently registered callback prefix and prove every
  parsed route has exactly one handler.
- Telegram acknowledgement must happen before deferred translation, search,
  LLM, or long recipe work. `CallbackResult` therefore carries the immediate
  ack plus an optional deferred async effect/view. The apply step answers first,
  awaits the deferred work second, then edits-or-resends. A test must record
  ordering. Do not use a `dispatch → slow handler → apply/answer` sequence.
- Preserve every existing ack/alert string and the edit-or-resend fallback.
  Direct sends (for example recook results) are explicit deferred effects, not
  forced into an edit view.
- `CallbackContext` must expose named typed dependencies; do not use an
  all-`Any` service bag. Split narrower per-group contexts if one aggregate
  would make every handler depend on everything.

### Track 3 corrections

- `views.digest` defaults to `renderer.DIGEST_CAP`, not `None`, and its result
  preserves the complete `DigestRender` contract including `rendered_count`,
  `total_count`, IDs, items, and `has_more`, plus resolved names.
- Cover all live pairings: list, item card, digest, NL picker names, shopping,
  favorites, plan, receipt-ingest reply, cook result, recook, and the cached
  digest/item-card variants used after an early callback acknowledgement.
- Async view functions translate before rendering; synchronous cached variants
  only read `NameTranslation`. This preserves the two-phase-render convention
  and prevents callback paths from moving translation ahead of acknowledgement.
- Move `to_aiogram_keyboard` to a transport leaf module before scheduler/views
  adoption so `app/scheduler.py` no longer imports `app.bot` and extraction does
  not create a cycle.
- Add parity tests comparing each new view result to the current
  translate/cache + pure-renderer composition before migrating call sites.

### Track 4 corrections

- Hoist auth/request primitives and Telegram keyboard conversion to leaf
  modules before importing handler modules from `bot.py`; handlers must never
  import `app.bot`.
- Callback handlers live in `app/callbacks/<group>.py`; `app/handlers/*` owns
  message-command orchestration only. Keep compatibility re-exports from
  `app.bot` for existing callers/tests during this refactor.
- Each extracted module owns an explicit command specification consumed by the
  dispatcher. Add a roster parity test before extraction so no command,
  fallback text handler, photo handler, reply handler, error handler, or
  callback route is lost.
- The shrink target is evidence, not a reason to move unrelated code: every
  move must preserve import direction and pass the focused module tests.

### Track 5 disposition

- Do not implement the draft `call_with_repair(call_fn, parse_fn, ...)` helper.
  Live inspection disproved its safety gate: Anthropic text appends a typed
  user-content repair message; DeepSeek appends the rejected assistant output
  plus a new user message; cook clients have different retry/final logging;
  every loop accrues provider-specific cost per attempt. Abstracting only the
  `for` loop would hide these observable differences without meaningful depth.
  Record this audited exclusion as the completed outcome for Track 5.

---

### Task 0: Refresh checkpoint (mandatory, no feature code)

Reconcile this plan against the live tree before touching code. `bot.py` is under active development; line numbers below are anchors from 2026-07-17 and will drift.

- [ ] `app/providers.py`: confirm `ProviderSelector.for_provider(provider) -> T`, `available_providers`, `default_provider`, and the `fallback` flag exist as described. Confirm `LLMProviderNotConfigured` is raised when `fallback=False` and the provider is absent.
- [ ] `app/llm.py`: confirm the selector classes `LLMProviderSelector`, `TextLLMProviderSelector`, `ProfileLLMProviderSelector`, `SelectionLLMProviderSelector`, `RecipeLLMProviderSelector`, `NutritionLLMProviderSelector` each subclass `ProviderSelector` and delegate the default provider's method (≈ lines 269–339).
- [ ] `app/bot.py`: confirm the five helpers exist and their signatures: `_select_llm_client` (≈303), `_select_text_llm_client` (≈310), `_select_profile_llm` (≈317), `_select_cook` (≈2242), `_select_search` (≈2247). Grep every call site: `grep -n "_select_llm_client\|_select_text_llm_client\|_select_profile_llm\|_select_cook\|_select_search" app/bot.py`. Record the exact current line list — Track 1 Task 4 rewrites each.
- [ ] `app/bot.py`: grep `_translate_for_render\|_cached_names_for_render` and record all 21 call sites (Track 3).
- [ ] `app/bot.py`: confirm `parse_callback` returns `CallbackAction(verb, item_id, option_index, back_to, round_name)` and enumerate the full `Verb` set in `app/commands.py` (≈182–330). Track 2 Task 2 needs the complete verb list.
- [ ] `tests/fakes.py`: read it. Record the concrete fake client classes and their constructor shapes (`FakeLLMClient`, fake text/profile/search/cook clients). Track 1 Task 2's `PerUserClients.for_tests` and its tests bind to these.
- [ ] `bin/run.py`: confirm `_build_llm_clients` returns `LLMBundle(image, text, search, selection, recipe, nutrition, profile, translation)` (≈112–348) and `build_dispatcher` is called with the eight client kwargs (≈446–465).
- [ ] Commit: `git commit --allow-empty -m "docs(arch): refresh deepening plan against live tree"`

---

## TRACK 1 — `PerUserClients`: one resolver, not five helpers + eight deps

**Problem.** Provider selection is a shallow seam scattered across ~15 sites; five helpers each duplicate `getattr(client, "for_provider", None)` because the interface cannot tell a `ProviderSelector` from a test fake. `build_dispatcher` threads eight separate client kwargs, and every handler re-reads `user.llm_provider`.

**Solution.** One `PerUserClients` module built once at wiring time. It owns the `getattr` resolution in exactly one place and exposes typed, user-taking accessors. Handlers receive one `clients` dep instead of eight. Deletion test: delete `PerUserClients` and the resolve dance reappears at ~15 sites.

**Interface (the seam):**
```
PerUserClients.image(user)      -> LLMClient
PerUserClients.text(user)       -> TextLLMClient
PerUserClients.profile(user)    -> ProfileUpdateLLMClient
PerUserClients.selection(user)  -> SelectionLLMClient (duck-typed)
PerUserClients.recipe(user)     -> RecipeLLMClient (duck-typed)
PerUserClients.nutrition(user)  -> NutritionLLMClient (duck-typed)
PerUserClients.search(user)     -> ShelfLifeSearchClient | None
PerUserClients.translation      -> TranslationLLMClient | None   (property; not per-provider today)
```

---

### Task 1: `_resolve` — the single place the selector/fake question is answered

**Files:**
- Create: `app/client_set.py`
- Test: `tests/test_client_set.py` (new)

**Interfaces:**
- Produces: `resolve(client: T | None, provider: str) -> T | None` — if `client` exposes a callable `for_provider`, return `client.for_provider(provider)`; else return `client` unchanged (covers bare fakes and `None`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_client_set.py`:

```python
from app.client_set import resolve


class _Selector:
    def __init__(self, mapping):
        self._mapping = mapping

    def for_provider(self, provider):
        return self._mapping[provider]


class _BareFake:
    pass


def test_resolve_routes_through_for_provider_when_present():
    a, b = _BareFake(), _BareFake()
    sel = _Selector({"anthropic": a, "deepseek": b})
    assert resolve(sel, "deepseek") is b


def test_resolve_passes_bare_client_through_unchanged():
    fake = _BareFake()
    assert resolve(fake, "anthropic") is fake


def test_resolve_passes_none_through():
    assert resolve(None, "anthropic") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_client_set.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.client_set'`

- [ ] **Step 3: Write minimal implementation**

Create `app/client_set.py`:

```python
"""Per-user client resolution — the one place a provider name becomes a client.

Historically each call site did ``getattr(client, "for_provider", None)`` to
tell a live ``ProviderSelector`` (which routes per provider) from a bare test
fake (which does not). That question is answered here exactly once; every
handler receives a :class:`PerUserClients` and asks for a capability by name.
"""
from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


def resolve(client: T | None, provider: str) -> T | None:
    """Route ``client`` to ``provider`` if it is a selector, else pass through.

    A ``ProviderSelector`` exposes ``for_provider``; bare fakes and ``None`` do
    not and are returned unchanged.
    """
    selector = getattr(client, "for_provider", None)
    if callable(selector):
        return selector(provider)
    return client
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_client_set.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/client_set.py tests/test_client_set.py
git commit -m "feat(arch): add resolve() — single per-user client resolution point"
```

---

### Task 2: `PerUserClients` — typed accessors over the eight seams

**Files:**
- Modify: `app/client_set.py`
- Test: `tests/test_client_set.py`

**Interfaces:**
- Consumes: `resolve` (Task 1).
- Produces: `PerUserClients` dataclass with fields
  `image, text, profile, selection, recipe, nutrition, search, translation`
  (each a selector, bare client, or `None`) and methods
  `image(user) / text(user) / profile(user) / selection(user) / recipe(user) / nutrition(user) / search(user)`
  returning `resolve(field, user.llm_provider)`, plus a `translation` **property** returning the field unchanged (translation is not per-provider today).
- Produces: classmethod `for_tests(*, image=None, text=None, profile=None, selection=None, recipe=None, nutrition=None, search=None, translation=None) -> PerUserClients` for constructing from bare fakes.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_client_set.py`:

```python
from types import SimpleNamespace

from app.client_set import PerUserClients


def _user(provider="anthropic"):
    return SimpleNamespace(llm_provider=provider)


def test_accessors_resolve_by_user_provider():
    sel = _Selector({"anthropic": "A", "deepseek": "D"})
    clients = PerUserClients.for_tests(text=sel)
    assert clients.text(_user("deepseek")) == "D"
    assert clients.text(_user("anthropic")) == "A"


def test_bare_fake_accessor_ignores_provider():
    fake = _BareFake()
    clients = PerUserClients.for_tests(image=fake)
    assert clients.image(_user("gemini")) is fake


def test_optional_search_none_returns_none():
    clients = PerUserClients.for_tests()
    assert clients.search(_user()) is None


def test_translation_is_plain_property():
    clients = PerUserClients.for_tests(translation="T")
    assert clients.translation == "T"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_client_set.py -v`
Expected: FAIL with `ImportError: cannot import name 'PerUserClients'`

- [ ] **Step 3: Write minimal implementation**

Append to `app/client_set.py`:

```python
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PerUserClients:
    """The capability clients for a request, resolved per the user's provider.

    Built once at wiring time from the live selectors (production) or from bare
    fakes (tests, via :meth:`for_tests`). Handlers take one of these instead of
    eight separate client dependencies.
    """

    image: Any = None
    text: Any = None
    profile: Any = None
    selection: Any = None
    recipe: Any = None
    nutrition: Any = None
    search: Any = None
    _translation: Any = None

    @classmethod
    def for_tests(cls, *, image=None, text=None, profile=None, selection=None,
                  recipe=None, nutrition=None, search=None, translation=None):
        return cls(image=image, text=text, profile=profile, selection=selection,
                   recipe=recipe, nutrition=nutrition, search=search,
                   _translation=translation)

    def _for(self, field, user):
        return resolve(field, user.llm_provider)

    def image_for(self, user):        return self._for(self.image, user)
    def text_for(self, user):         return self._for(self.text, user)
    def profile_for(self, user):      return self._for(self.profile, user)
    def selection_for(self, user):    return self._for(self.selection, user)
    def recipe_for(self, user):       return self._for(self.recipe, user)
    def nutrition_for(self, user):    return self._for(self.nutrition, user)
    def search_for(self, user):       return self._for(self.search, user)

    @property
    def translation(self):
        return self._translation
```

> Naming note: methods are `*_for(user)` (not `text(user)`) so the class can keep plain data fields of the same capability name. Update the interface block references accordingly — accessors are `text_for`, `image_for`, etc.

- [ ] **Step 4: Update the test to the `_for` accessor names**

Change `clients.text(...)` → `clients.text_for(...)`, `clients.image(...)` → `clients.image_for(...)`, `clients.search(...)` → `clients.search_for(...)` in the Task 2 tests.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_client_set.py -v`
Expected: PASS (7 passed)

- [ ] **Step 6: Commit**

```bash
git add app/client_set.py tests/test_client_set.py
git commit -m "feat(arch): PerUserClients resolver with per-capability accessors"
```

---

### Task 3: Build `PerUserClients` in `bin/run.py` and thread it through `build_dispatcher`

**Files:**
- Modify: `bin/run.py:335-348` (return a `PerUserClients` alongside/instead of the raw bundle) and `bin/run.py:446-465` (pass `clients=`).
- Modify: `app/bot.py:3458-3509` (`build_dispatcher` signature + `deps` dict).
- Test: `tests/test_bot_dispatcher.py` (or the existing dispatcher-construction test — find it via `grep -rln build_dispatcher tests/`).

**Interfaces:**
- Consumes: `LLMBundle` (unchanged), `PerUserClients` (Task 2).
- Produces: `build_dispatcher(..., clients: PerUserClients, ...)` accepts a `clients` kwarg. For this task, add it **additively** — keep the eight existing kwargs so nothing breaks yet; Task 4/5 remove them.

- [ ] **Step 1: Write the failing test**

In the dispatcher construction test, assert `build_dispatcher` accepts `clients=`:

```python
def test_build_dispatcher_accepts_client_set(fake_session_factory):
    from app.client_set import PerUserClients
    dispatcher = build_dispatcher(
        bot=FakeBot(),
        session_factory=fake_session_factory,
        llm=FakeLLMClient(),
        text_llm=FakeTextLLMClient(),
        profile_llm=FakeProfileLLMClient(),
        now_provider=lambda tz: datetime(2026, 7, 17, tzinfo=ZoneInfo(tz)),
        on_user_created=lambda u: None,
        reschedule=lambda u: None,
        clients=PerUserClients.for_tests(),
    )
    assert dispatcher is not None
```

(Reuse whatever fakes/fixtures the existing dispatcher test already imports; do not invent new ones.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bot_dispatcher.py -v` (adjust path)
Expected: FAIL with `TypeError: build_dispatcher() got an unexpected keyword argument 'clients'`

- [ ] **Step 3: Add the kwarg**

In `app/bot.py` `build_dispatcher`, add `clients: "PerUserClients | None" = None,` to the signature and `"clients": clients,` to the `deps` dict. Add `from app.client_set import PerUserClients` to imports.

- [ ] **Step 4: Build it in `bin/run.py`**

After `bundle = _build_llm_clients(settings)` (≈387), add:

```python
    clients = PerUserClients(
        image=bundle.image,
        text=bundle.text,
        profile=bundle.profile,
        selection=bundle.selection,
        recipe=bundle.recipe,
        nutrition=bundle.nutrition,
        search=bundle.search,
        _translation=bundle.translation,
    )
```

Add `from app.client_set import PerUserClients` to `bin/run.py` imports and pass `clients=clients,` in the `build_dispatcher(...)` call.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_bot_dispatcher.py -v && uv run pytest -q`
Expected: PASS; full suite green.

- [ ] **Step 6: Commit**

```bash
git add app/bot.py bin/run.py tests/
git commit -m "feat(arch): thread PerUserClients through build_dispatcher (additive)"
```

---

### Task 4: Migrate the five `_select_*` call sites to `clients.*_for(user)`

**Files:**
- Modify: `app/bot.py` — the ~15 call sites recorded in Task 0; delete `_select_llm_client`, `_select_text_llm_client`, `_select_profile_llm`, `_select_cook`, `_select_search`.
- Test: existing handler tests (no new tests; behaviour is unchanged and already covered).

**Interfaces:**
- Consumes: `clients: PerUserClients` — but individual handlers currently receive `text_llm`, `search`, `profile_llm`, `llm`, `recipe_llm`, `nutrition_llm`, `selection_llm` as separate deps. This task keeps those deps and instead **routes them through module-level `resolve`** so the diff is minimal and reviewable; Task 5 swaps the deps for `clients`.

- [ ] **Step 1: Import `resolve`**

Add `from app.client_set import resolve` to `app/bot.py`.

- [ ] **Step 2: Replace each helper body with `resolve`, then inline**

The transform is mechanical and identical at every site. Example (`handle_add`, ≈925):

```python
# BEFORE
selected_text_llm = _select_text_llm_client(text_llm, user.llm_provider)
selected_search = _select_search(search, user.llm_provider)
# AFTER
selected_text_llm = resolve(text_llm, user.llm_provider)
selected_search = resolve(search, user.llm_provider)
```

Apply the same `resolve(<client>, user.llm_provider)` substitution at every recorded site:
`_select_llm_client(...)` → `resolve(...)`, `_select_text_llm_client(...)` → `resolve(...)`, `_select_profile_llm(...)` → `resolve(...)`, `_select_cook(...)` → `resolve(...)`, `_select_search(...)` → `resolve(...)`. Note `_plan_source` (≈1660) and `run_cook_and_render` (≈2700) each call `_select_cook` two–three times — convert all.

- [ ] **Step 3: Delete the five now-unused helpers**

Remove `_select_llm_client`, `_select_text_llm_client`, `_select_profile_llm` (≈303–321), `_select_cook`, `_select_search` (≈2242–2259). Keep `_select_search`'s docstring intent as a one-line comment on `resolve` if useful.

- [ ] **Step 4: Verify no references remain**

Run: `grep -n "_select_llm_client\|_select_text_llm_client\|_select_profile_llm\|_select_cook\|_select_search" app/`
Expected: no output.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all green (behaviour identical).

- [ ] **Step 6: Commit**

```bash
git add app/bot.py
git commit -m "refactor(arch): collapse five _select_* helpers into resolve()"
```

---

### Task 5: Replace per-handler client deps with the single `clients` dep

**Files:**
- Modify: `app/bot.py` — handler signatures + `_MESSAGE_COMMANDS` roster (≈3415–3455) + `build_dispatcher` `deps` and the two explicit `register` blocks (photo, correct-reply, nl) + `on_callback` sub-dispatch (≈3563–3613).
- Modify: `bin/run.py` — drop the now-unused `llm=/text_llm=/profile_llm=/search=/selection_llm=/recipe_llm=/nutrition_llm=` kwargs from `build_dispatcher(...)`.
- Test: handler tests — update construction to pass `clients=PerUserClients.for_tests(...)` instead of individual clients.

**Interfaces:**
- Produces: every handler takes `clients: PerUserClients` in place of `llm/text_llm/profile_llm/search/selection_llm/recipe_llm/nutrition_llm`. `translation_llm`, `session_factory`, `now_provider`, `bot`, `spawn`, `on_user_created`, `reschedule`, `unschedule`, `intent_agent`, `composer`, `recipe_sources` stay as-is.

> This is the largest single task in Track 1 and touches every handler. Right-sizing note: it is one task because the handlers are not independently shippable mid-migration (the roster wires them together). Do it in one focused pass, leaning on the full suite as the gate.

- [ ] **Step 1: Convert one handler + its test as the pattern**

`handle_add` (≈994): replace deps `text_llm`, `search` with `clients`; body uses `resolve(clients.text, user.llm_provider)` → simplify to `clients.text_for(user)` and `clients.search_for(user)`. Update its roster row and its test to pass `clients=PerUserClients.for_tests(text=fake_text, search=fake_search)`.

Run: `uv run pytest tests/ -k add -v` → PASS.

- [ ] **Step 2: Convert the remaining client-taking handlers**

Apply the identical pattern to: `handle_correct`, `handle_correct_reply`, `handle_cook`, `handle_plan`, `handle_prefs`, `handle_llm`, `handle_photo`, `handle_nl_message`, `run_cook_and_render`, `handle_cook_callback`, `handle_plan_callback`, `_plan_source`, `handle_callback` (its `search` use ≈3173), and the digest-render helpers if any read clients. Each: swap the dep, call `clients.<cap>_for(user)`, update the roster row + `register(...)` block + test construction.

- [ ] **Step 3: Simplify `deps` and `_MESSAGE_COMMANDS`**

In `build_dispatcher`, delete `llm/text_llm/profile_llm/search/selection_llm/recipe_llm/nutrition_llm` from the signature and `deps`; add `"clients": clients`. Update every roster tuple's dep-name list to use `"clients"` where it previously named a client seam.

- [ ] **Step 4: Simplify `bin/run.py`**

Remove the seven client kwargs from the `build_dispatcher(...)` call; keep `clients=clients`, `translation_llm=`, `recipe_sources=`, `intent_agent=`, `composer=`, `alerter=`.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add app/bot.py bin/run.py tests/
git commit -m "refactor(arch): one PerUserClients dep replaces eight client kwargs"
```

**Track 1 done:** the `getattr` dance lives in one function; handlers name capabilities, not mechanisms; `build_dispatcher` lost seven kwargs.

---

## TRACK 2 — Callback dispatch table behind the `callback_dispatch` seam

**Problem.** A tap crosses a string-prefix ladder in `on_callback` and then a 300–375-line verb if/elif inside `handle_callback` / `handle_cook_callback` / `handle_item_callback` / `_handle_pending_callback`. The `callback_dispatch.edit_or_resend` seam exists but is bypassed by two other inline idioms (`cb.message.edit_text` + try/except at ≈3028; `_safe_edit_cb` at ≈2262), so "a tap never dead-ends" holds unevenly.

**Solution.** `parse_callback` already yields `CallbackAction(verb, …)`. Add a `verb → async handler(ctx) -> CallbackResult` registry; each handler returns a `CallbackResult` (an ack string and/or a view). One apply-step runs `callback_dispatch.answer` then `edit_or_resend`, uniformly. `on_callback` becomes: parse → look up → build ctx → apply.

**Interface (the seam):**
```
@dataclass CallbackResult:
    ack: str | None = None
    alert: bool = False
    view: View | None = None            # (text, keyboard)
@dataclass CallbackContext: cb, session, user, today, clients, translation_llm, now, spawn, bot, recipe_sources
register(verb) decorator -> populates _REGISTRY: dict[Verb, Handler]
async dispatch(action, ctx) -> CallbackResult
```

---

### Task 1: `CallbackResult`, `View`, and the apply-step

**Files:**
- Modify: `app/callback_dispatch.py`
- Test: `tests/test_callback_dispatch.py` (extend; find via `grep -rln callback_dispatch tests/`)

**Interfaces:**
- Produces: `@dataclass(frozen=True) View(text: str, keyboard=None)`; `@dataclass(frozen=True) CallbackResult(ack: str | None = None, alert: bool = False, view: View | None = None)`.
- Produces: `async def apply(cb, result: CallbackResult) -> None` — calls `answer(cb, result.ack or "", show_alert=result.alert)` then, if `result.view`, `edit_or_resend(cb, result.view.text, result.view.keyboard)`.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from app.callback_dispatch import View, CallbackResult, apply


class _Msg:
    def __init__(self): self.edited = None
    async def edit_text(self, text, reply_markup=None): self.edited = (text, reply_markup)


class _Cb:
    def __init__(self): self.message = _Msg(); self.answered = None
    async def answer(self, text="", show_alert=False): self.answered = (text, show_alert)


@pytest.mark.asyncio
async def test_apply_acks_then_edits():
    cb = _Cb()
    await apply(cb, CallbackResult(ack="saved", view=View("new text", None)))
    assert cb.answered == ("saved", False)
    assert cb.message.edited == ("new text", None)


@pytest.mark.asyncio
async def test_apply_ack_only_does_not_edit():
    cb = _Cb()
    await apply(cb, CallbackResult(ack="noted"))
    assert cb.answered == ("noted", False)
    assert cb.message.edited is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_callback_dispatch.py -k apply -v`
Expected: FAIL (`ImportError: cannot import name 'View'`)

- [ ] **Step 3: Implement**

Append to `app/callback_dispatch.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class View:
    text: str
    keyboard: object | None = None


@dataclass(frozen=True)
class CallbackResult:
    ack: str | None = None
    alert: bool = False
    view: View | None = None


async def apply(cb, result: "CallbackResult") -> None:
    """Acknowledge, then (if a view is present) edit-or-resend — the one path."""
    await answer(cb, result.ack or "", show_alert=result.alert)
    if result.view is not None:
        await edit_or_resend(cb, result.view.text, result.view.keyboard)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_callback_dispatch.py -k apply -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/callback_dispatch.py tests/test_callback_dispatch.py
git commit -m "feat(arch): CallbackResult/View + single apply() step"
```

---

### Task 2: The verb registry + `dispatch`

**Files:**
- Create: `app/callbacks.py`
- Test: `tests/test_callbacks.py` (new)

**Interfaces:**
- Consumes: `CallbackAction`/`Verb` (`app/commands.py`), `CallbackResult` (Task 1).
- Produces: `@dataclass CallbackContext(...)` (fields per the seam block above; keep it permissive — plain attributes).
- Produces: `register(*verbs: str)` decorator populating a module `_REGISTRY: dict[str, Handler]`.
- Produces: `async def dispatch(action: CallbackAction, ctx: CallbackContext) -> CallbackResult` — looks up `action.verb`; unknown verb returns `CallbackResult(ack="unrecognized action")`.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from app.callbacks import register, dispatch, CallbackContext, _REGISTRY
from app.callback_dispatch import CallbackResult
from app.commands import CallbackAction


def test_register_populates_registry():
    @register("unit_test_verb")
    async def _h(action, ctx):
        return CallbackResult(ack="ok")
    assert "unit_test_verb" in _REGISTRY


@pytest.mark.asyncio
async def test_dispatch_unknown_verb_is_soft_ack():
    action = CallbackAction(verb="does_not_exist", item_id=None)
    result = await dispatch(action, CallbackContext())
    assert result.ack == "unrecognized action"
    assert result.view is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_callbacks.py -v`
Expected: FAIL (`ModuleNotFoundError: app.callbacks`)

- [ ] **Step 3: Implement**

Create `app/callbacks.py`:

```python
"""Callback verb registry: verb -> handler -> CallbackResult.

Replaces the prefix ladder + per-handler if/elif. Each handler is small, takes
a CallbackContext, and returns a CallbackResult; dispatch() looks up the verb
and the caller applies the result through callback_dispatch.apply().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.callback_dispatch import CallbackResult
from app.commands import CallbackAction

Handler = Callable[[CallbackAction, "CallbackContext"], Awaitable[CallbackResult]]
_REGISTRY: dict[str, Handler] = {}


def register(*verbs: str) -> Callable[[Handler], Handler]:
    def _decorate(handler: Handler) -> Handler:
        for verb in verbs:
            _REGISTRY[verb] = handler
        return handler
    return _decorate


@dataclass
class CallbackContext:
    cb: Any = None
    session: Any = None
    user: Any = None
    today: Any = None
    clients: Any = None
    translation_llm: Any = None
    now: Any = None
    spawn: Any = None
    bot: Any = None
    recipe_sources: tuple = field(default_factory=tuple)


async def dispatch(action: CallbackAction, ctx: CallbackContext) -> CallbackResult:
    handler = _REGISTRY.get(action.verb)
    if handler is None:
        return CallbackResult(ack="unrecognized action")
    return await handler(action, ctx)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_callbacks.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/callbacks.py tests/test_callbacks.py
git commit -m "feat(arch): callback verb registry + dispatch"
```

---

### Tasks 3–7: Migrate verb groups into the registry

Each task moves one cohesive verb group out of its giant handler into `register`-decorated functions in a new `app/callbacks/<group>.py` (or sections of `app/callbacks.py`), returning `CallbackResult`. After each, `on_callback` routes that group through `dispatch` + `apply`, and the old branch is deleted. Each group is independently testable — a reviewer can approve `cook` while rejecting `pending`.

Order and scope (verbs from Task 0's enumeration):

- [ ] **Task 3 — feedback/save/shop group:** `cook_like`, `cook_dislike`, `cook_save`, `cook_shop`, `shop_done`, `fav_cook`. Source: `handle_callback` (≈2945–3200). These are mostly ack-only or single-edit; the cleanest first migration.
- [ ] **Task 4 — item card group:** `ate`, `toss`, `snooze2`, `freeze`, `fridge`, `show_all`, and the item-card navigation in `handle_item_callback` (≈2767–2900). Return views built from the renderer (Track 3 will supply localized views — until then keep the existing translate-then-render inline in the handler).
- [ ] **Task 5 — pending group:** `apply`, `cancel`, `undo_*` in `_handle_pending_callback` (≈3201–3337).
- [ ] **Task 6 — cook interaction group:** `cook_pick`, `cook_alt`, `cook_more`, `cook_more_opts`, `cook_adjust` in `handle_cook_callback` (≈2287–2661). Largest; may split into 6a/6b.
- [ ] **Task 7 — plan group:** `plan_swap`, `plan_shop`, `plan_cancel` in `handle_plan_callback` (≈1810–1901).

**Per-task recipe (identical each time):**
1. Write a test for one verb's handler returning the right `CallbackResult` (ack text and/or view), constructing `CallbackContext` with fakes — no aiogram mock needed, you assert the `CallbackResult`, which is the new test surface.
2. Move the branch body into a `@register("<verb>")` async fn returning `CallbackResult`.
3. In `on_callback`, replace the branch/prefix with: `result = await dispatch(action, ctx); await apply(cb, result)`.
4. Delete the old branch and, once a whole giant handler is empty, delete it.
5. Run `uv run pytest -q`; commit `refactor(arch): migrate <group> callbacks to registry`.

- [ ] **Task 8 — retire `_safe_edit_cb` and the inline `edit_text` idiom:** grep `_safe_edit_cb\|cb.message.edit_text` in `app/bot.py`; every remaining use must route through `apply`/`edit_or_resend`. Delete `_safe_edit_cb` (≈2262) and `_safe_edit_bot` if unused. Run suite; commit `refactor(arch): single edit-or-resend idiom for all callbacks`.

**Track 2 done:** one dispatch table, one ack/edit rule, `on_callback` is a few lines.

---

## TRACK 3 — `app/views.py`: localized-view seam over the 21 render-prep sites

**Problem.** "Resolve names in the user's language, then render" is one operation split across two statements at 21 sites; the field→renderer pairing (`i.raw_name` vs `i.name`) is copy-pasted and easy to get subtly wrong.

**Solution.** An async `app/views.py` whose functions take domain objects + `user` and return finished text (and, where relevant, the rendered-items/has-more tuple the keyboard builder needs). Each composes `translate_texts`/`cached_name_translations` + the **unchanged pure renderer**.

> ⚠️ **Convention callout.** CLAUDE.md: "renderers stay pure/synchronous… never call the async translator from inside a renderer." This track **honours** that — `app/renderer.py` is untouched; the async composition lives in the new `views` module *above* the renderer. Confirm with the maintainer that the convention meant "not inside `renderer.py`", not "no composition seam at all", before starting. If they want it recorded, add an ADR: `docs/adr/0002-views-compose-translation-above-pure-renderers.md`.

---

### Task 1: `views.digest` — the first localized view

**Files:**
- Create: `app/views.py`
- Test: `tests/test_views.py` (new)

**Interfaces:**
- Consumes: `translate_texts` (`app/translation_service.py`), `render_digest`/`build_digest_keyboard` (`app/renderer.py`), `_translate_for_render` logic (inline the `lang=="en" or llm is None` short-circuit).
- Produces: `async def digest(session, items, *, user, today, translation_llm, cap=None) -> RenderedDigestView` where `RenderedDigestView` carries `text`, `rendered_items`, `has_more`, `names` (so the caller can build the keyboard) — mirror the existing `render_digest` return plus the resolved `names`.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from types import SimpleNamespace
from app.views import digest


@pytest.mark.asyncio
async def test_digest_view_english_needs_no_translation(session, make_item):
    items = [make_item(raw_name="Milk", days=2)]
    user = SimpleNamespace(lang="en", household_id=1, llm_provider="anthropic")
    view = await digest(session, items, user=user, today=TODAY, translation_llm=None)
    assert "Milk" in view.text
    assert view.names == {}
```

(Reuse the session fixture + item factory the renderer/pantry tests already use; find via `grep -rln "def make_item\|render_digest" tests/`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_views.py -v`
Expected: FAIL (`ModuleNotFoundError: app.views`)

- [ ] **Step 3: Implement**

Create `app/views.py`:

```python
"""Localized views: compose per-user name translation with the pure renderers.

Renderers (app/renderer.py) stay pure and synchronous. This module does the
async name resolution first, then calls the renderer — the two-phase render
convention, factored out of the 21 handler call sites that duplicated it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlmodel import Session

from app.renderer import render_digest
from app.translation_service import translate_texts


async def _names_for(session, *, lang, texts, translation_llm) -> dict[str, str]:
    if lang == "en" or translation_llm is None:
        return {}
    return await translate_texts(session, [t for t in texts if t], lang=lang, llm=translation_llm)


@dataclass(frozen=True)
class RenderedDigestView:
    text: str
    rendered_items: list
    has_more: bool
    names: dict[str, str]


async def digest(session: Session, items, *, user, today: date,
                 translation_llm, cap=None) -> RenderedDigestView:
    names = await _names_for(
        session, lang=user.lang, texts=[i.raw_name for i in items],
        translation_llm=translation_llm,
    )
    rendered = render_digest(items, today=today, lang=user.lang, names=names, cap=cap)
    return RenderedDigestView(
        text=rendered.text,
        rendered_items=rendered.rendered_items,
        has_more=rendered.has_more,
        names=names,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_views.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/views.py tests/test_views.py
git commit -m "feat(arch): views.digest composes translation + pure renderer"
```

---

### Task 2: Add the remaining view functions

**Files:** Modify `app/views.py`; Test `tests/test_views.py`.

Add, each with the same shape (resolve names → call the matching pure renderer), driven by the renderers currently paired with `_translate_for_render` in `bot.py`:

- [ ] `views.active_list(session, items, *, user, today, translation_llm) -> str` (wraps `render_list`).
- [ ] `views.item_card(session, item, *, user, today, translation_llm) -> str` (wraps `render_item_card`).
- [ ] `views.shopping(session, items, *, user, translation_llm) -> str` (wraps `render_shopping_list`).
- [ ] `views.cook_result(session, cards, *, user, translation_llm) -> str` (wraps the cook result renderer; texts = title/cuisine/method + ingredients + shopping, per `_cook_card_texts`).
- [ ] `views.plan(session, plan, entries, *, user, translation_llm) -> str` (wraps `render_plan` / `_render_plan_message`).

Write one test per function asserting the English path renders without translation and (where a fake translator is available) that non-English routes texts through it. Commit `feat(arch): view functions for list/item/shopping/cook/plan`.

---

### Task 3: Migrate the 21 call sites to `views.*`

**Files:** Modify `app/bot.py` (21 sites from Task 0), `app/scheduler.py:65-71` (digest send path).

- [ ] Replace each `names = await _translate_for_render(...)` + `render_X(..., names=names)` pair with a single `view = await views.X(...)`. Where the site also builds a keyboard from `rendered_items`/`names`, take those from the returned view (that is why `digest`/`cook_result` return the extra fields).
- [ ] `scheduler.send_digest_once` (≈65) currently inlines the same translate-then-`render_digest`; route it through `views.digest` too — this is the locality payoff (the digest and the `/pantry digest` command now share one view).
- [ ] Delete `_translate_for_render` and `_cached_names_for_render` from `bot.py` once no site references them (some callback sites use the sync `_cached_names_for_render`; add a `views.*_cached` sync variant or keep a single shared `_names_for`/`cached_name_translations` call — do **not** leave two idioms).
- [ ] Run `uv run pytest -q`; English output must stay byte-identical.
- [ ] Commit `refactor(arch): route all render-prep through views seam`.

**Track 3 done:** field→renderer coupling lives in one module; the digest command and scheduled digest render identically by construction.

---

## TRACK 4 — Split `bot.py` into `app/handlers/*`

**Problem.** One 3,627-line module holds transport, per-feature orchestration, callback routing, and (until Tracks 1–3) provider wiring and render prep. Friction is navigational.

**Solution.** With the glue now in seams (`client_set`, `callbacks`, `views`), extract cohesive handler groups. `bot.py` keeps `build_dispatcher`, `_MESSAGE_COMMANDS`, auth (`resolve_authorization`, `authorize_and_get_user`, `_request`, `_guard`), `to_aiogram_keyboard`, and shared context types.

> Do this **after** Tracks 1–3. Splitting first would scatter the same leaks across more files. This track is decomposition, not deepening — its payoff is locality of navigation and smaller files you can hold in context.

**File structure (each a focused module with one responsibility):**
- `app/handlers/household.py` — start, invite, join, household, leave, remove, tz, lang, digest_at, notify-join.
- `app/handlers/pantry.py` — list, pantry, add, ate, toss, delete, snooze, correct(+reply), stats, and their item-card callbacks.
- `app/handlers/cook.py` — cook, cook callbacks, run_cook_and_render, cuisine helpers.
- `app/handlers/plan.py` — plan, plan callbacks, plan rendering.
- `app/handlers/shopping.py` — shopping, favorites.
- `app/handlers/meta.py` — llm, prefs, help(+callback), nl-message, photo.

---

### Tasks (one per module, repeatable recipe)

For each module above, in order (household first — least coupled):

- [ ] **Step 1:** Create `app/handlers/<group>.py`. Move the group's handler functions verbatim (they already take explicit deps + `clients`/`views`/`dispatch`). Move only helpers used solely by this group; leave shared helpers in `bot.py` and import them.
- [ ] **Step 2:** In `bot.py`, import the moved handlers: `from app.handlers.<group> import handle_x, handle_y`. The `_MESSAGE_COMMANDS` roster and `on_callback` keep referencing the same names — now imported, not local.
- [ ] **Step 3:** Fix circular imports. `to_aiogram_keyboard`, `_request`, `CallbackContext`-building live in `bot.py`; if a handler module needs them and `bot.py` imports the module, hoist the shared piece into a leaf module (`app/handler_support.py`) that both import. Prefer hoisting over `import app.bot` inside handlers.
- [ ] **Step 4:** Run `uv run pytest -q`. Update test imports only if a test imported a handler from `app.bot` directly (`grep -rn "from app.bot import handle_"`); re-export from `bot.py` (`from app.handlers.<group> import handle_x  # noqa: F401`) to avoid churn, or update the test import.
- [ ] **Step 5:** Commit `refactor(arch): extract <group> handlers from bot.py`.

- [ ] **Final task — shrink check:** after all groups move, `wc -l app/bot.py` should be well under ~1,000 lines (wiring + auth + roster + keyboard). Confirm `build_dispatcher` still registers every command and callback group. Full suite green. Commit `refactor(arch): bot.py is now dispatcher + auth + roster`.

---

## TRACK 5 — (Speculative) shared JSON validate/repair tail

**Problem.** `AnthropicTextLLMClient._call_with_schema` (≈590–639), `DeepSeek._chat_json` (≈74), and `cook/llm.py`'s shared Anthropic call (≈65) each re-implement "call → extract JSON text → `model_validate` → on failure append a repair message and retry once → accrue cost." The transport genuinely differs per SDK; the tail does not.

**Solution.** Extract `app/llm_json.py::call_with_repair(call_fn, parse_fn, *, on_repair, retries=1)` and have the Anthropic + DeepSeek text paths use it. Leave OpenAI (SDK `responses.parse` structured output — no repair loop) and Gemini (structured output / grounding split) alone.

> Marked speculative in the review: CLAUDE.md deliberately centralizes each provider's clients "because their SDK shapes differ enough." Only proceed if, on inspection, the Anthropic and DeepSeek tails are line-for-line the same modulo the `call_fn`. If they have diverged (different cost extraction, different repair-message shape), **skip this track** and record why.

---

### Task 1: `call_with_repair`

**Files:** Create `app/llm_json.py`; Test `tests/test_llm_json.py` (new).

**Interfaces:**
- Produces: `async def call_with_repair(call_fn, parse_fn, *, on_repair, retries=1)` — `call_fn()` returns raw text; `parse_fn(text)` returns the validated model or raises; on the first `retries` failures, call `on_repair(exc)` to mutate the next prompt and retry; re-raise on final failure.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from app.llm_json import call_with_repair


@pytest.mark.asyncio
async def test_repairs_once_then_succeeds():
    calls = {"n": 0}
    async def call_fn():
        calls["n"] += 1
        return "bad" if calls["n"] == 1 else "good"
    def parse_fn(text):
        if text != "good":
            raise ValueError("nope")
        return "parsed"
    repairs = []
    result = await call_with_repair(call_fn, parse_fn, on_repair=repairs.append)
    assert result == "parsed"
    assert calls["n"] == 2 and len(repairs) == 1


@pytest.mark.asyncio
async def test_reraises_after_retries_exhausted():
    async def call_fn(): return "bad"
    def parse_fn(text): raise ValueError("always")
    with pytest.raises(ValueError):
        await call_with_repair(call_fn, parse_fn, on_repair=lambda e: None)
```

- [ ] **Step 2:** Run: `uv run pytest tests/test_llm_json.py -v` → FAIL (no module).
- [ ] **Step 3:** Implement `call_with_repair` with a `for attempt in range(retries + 1)` loop: `text = await call_fn(); try: return parse_fn(text) except Exception as exc: if attempt == retries: raise; on_repair(exc)`.
- [ ] **Step 4:** Run tests → PASS.
- [ ] **Step 5:** Commit `feat(arch): call_with_repair shared JSON validate/repair loop`.

### Task 2: Adopt in Anthropic + DeepSeek text paths

- [ ] Rewrite `AnthropicTextLLMClient._call_with_schema` to delegate to `call_with_repair`, moving the "append repair text to `user_content`" logic into the `on_repair` callback. Keep cost accrual identical.
- [ ] Rewrite `DeepSeek._chat_json` the same way.
- [ ] Run the provider tests (`uv run pytest tests/test_multi_provider.py -v` and any `test_llm*.py`). All green.
- [ ] Commit `refactor(arch): Anthropic+DeepSeek text clients share call_with_repair`.

---

## Self-Review

**Spec coverage:** Each of the five review candidates maps to a track — 01→Track 1, 02→Track 2, 03→Track 3, 04→Track 4, 05→Track 5. ✓

**Ordering justified:** Track 1 removes the client glue that Track 4 would otherwise split awkwardly; Tracks 2–3 remove the callback/render glue for the same reason; Track 4 is last; Track 5 is independent and optional. ✓

**Placeholder scan:** No "TBD/handle edge cases/similar to Task N." Repetitive migrations (Track 1 Task 4/5, Track 2 Tasks 3–7, Track 3 Task 3, Track 4) show the exact transform once and enumerate every target site from Task 0 — the pattern is genuinely identical at each site, so the plan gives the transform + the site list rather than 21 copies. ✓

**Type consistency:** `resolve`/`PerUserClients.*_for`/`CallbackResult`/`View`/`CallbackContext`/`dispatch`/`RenderedDigestView`/`call_with_repair` are named identically wherever referenced across tracks. Accessor names settled on `*_for(user)` (Track 1 Task 2 Step 4 reconciles the interface block). ✓

**Risk register:**
- Every track is a behaviour-preserving refactor; the ~600-test suite is the gate after each task. If any English string or callback ack changes, the task is wrong — revert and re-slice.
- Track 3 touches a documented convention — the callout requires maintainer confirmation (and offers an ADR) before starting.
- Track 5 is gated on inspection; skip-and-record is an acceptable outcome.

---

**Plan complete and saved to `docs/superpowers/plans/2026-07-17-architecture-deepening.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?** (And do you want to run all five tracks, or start with Track 1 — the top recommendation — and reassess?)
