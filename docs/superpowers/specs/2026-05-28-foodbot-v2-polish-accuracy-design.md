# Food Bot v2 — "Polish + Accuracy" Design

**Date:** 2026-05-28
**Status:** Approved for planning
**Scope owner:** awbjcj

## Overview

v2 is a **polish + accuracy** release for the single-user Telegram pantry bot.
It improves correctness (undo, websearch-backed shelf life), recognition
quality (excluding non-trackable items), and readability (list rendering,
year indicator) — all **within the existing single-user model**.

The **family / shared-pantry** idea is explicitly **out of scope** for v2 and
deferred to its own v3 spec. Rationale: it invalidates the single-allowed-user
auth gate (`bot.py:74`, `bot.py:97`), turns every table's `user_id` partition
into a `pantry_id` partition, and rewrites the per-user scheduler. It is
plausibly larger than the other five features combined and must not be lumped
into this spec.

## Features

### 1. Undo last event (inline button, 10-minute TTL)

**Goal:** let the user reverse the most recent ingest event as a unit.

**Trigger / surface**
- An **Undo** button is attached to:
  - each **receipt ingest reply** → reverses that receipt's batch
    (items grouped by `source_receipt_id`);
  - each **`Added #N` confirmation** (post-`/add` apply) → reverses that single
    item (`item_id`).
- Available for **10 minutes**, enforced by comparing "now" against
  `receipt.scanned_at` / `item.created_at`. No new table: the target is encoded
  in callback data (`undo:receipt:<id>` / `undo:add:<item_id>`). After expiry
  the button reports the standard terminal state.

**Reversal semantics**
- **Partial-safe:** only items still `status="active"` **and** untouched
  (not eaten / tossed / corrected / snoozed) are reversed. Items the user
  already acted on are left intact and reported as skipped, e.g.
  `6 removed, skipped #3 (eaten), #7 (corrected)`.
- Reversed items are set to `status="removed"` (soft delete; preserved for
  `/stats` history).
- **Cache is never touched** — learned `ShelfLifeCache` entries survive an undo.

**Receipt deletion (re-import support)**
- **Full undo** (every item in the batch was eligible and removed): null
  `source_receipt_id` on the removed items, then **delete the `Receipt` row**.
  This clears the `uq_receipt_user_photo` dedup guard so the same photo can be
  re-scanned cleanly.
- **Partial undo** (some items kept): remove the eligible items but **keep the
  `Receipt` row** (its FK is still valid for the kept items). Re-import stays
  blocked — which is correct, since re-scanning would duplicate the kept items.

**Touched modules:** `pantry_service.py` (undo service fn), `bot.py` (callback
handler + button on ingest/add replies), `renderer.py` (undo button + skipped
report), `ingest_service.py` (expose receipt id already available).

### 2. Pantry list rendering (`/list`)

**Goal:** make `/list` scannable and stop dropping stored data.

- **Category-primary grouping:** category headers with counts; within each
  category, sort by `expires_on` ascending.
- **Per-line decorations (shared helpers):**
  - **Urgency icon:** 🔴 expired/today · 🟡 soon (within ~3 days) · 🟢 later.
  - **qty/unit** rendered (currently dropped by `render_list`, despite being
    stored at `models.py:63-64`), e.g. `🔴 #7 2 lb Chicken - expired 1d`.
- The **daily digest stays urgency-bucketed** (Expired / Today / Tomorrow /
  This week — the action view) but **adopts the same per-line decorations**
  (icon + qty/unit) so the two views read consistently. `/list` and the digest
  share line-level rendering, **not** top-level structure.

**Example**
```
Produce (2)
  🔴 #9 1 bag Spinach - expired 1d
  🟢 #4 Bananas - Jun 2 (5d)
Meat (1)
  🔴 #7 2 lb Chicken - exp 1d
```

**Touched modules:** `renderer.py` only (new category-order constant + shared
line helper).

### 3. Next-year indicator

**Goal:** disambiguate dates for long-shelf-life items (up to 730 days out).

- `_fmt_date` gains a `today` (or current-year) parameter and appends the year
  **iff `expires_on.year != today.year`** → `"Jun 2 2027"`.
- Calendar-year comparison (not a days-out threshold) so the **Dec→Jan boundary
  is correct**: an item expiring Jan 5 next year shows the year even though it
  is only a few days out.
- Ripples to all `_fmt_date` call sites in `renderer.py` (they must pass
  `today`).

**Touched modules:** `renderer.py`.

### 4. Recognition hardening (exclude non-trackable items)

**Goal:** stop logging items that are not worth expiry-tracking.

**Conceptual rule:** the filter is **"is this worth expiry-tracking?"**, not
"is this food?". Condiments *are* food but are shelf-stable and low-waste, so
they need a separate signal from the existing `is_food` flag (`llm.py:20`).

- **New structured fields** on the parsed item (vision schema, `llm.py`):
  - `track_worthy: bool`
  - `exclusion_reason: Optional[str]` (e.g. `"shelf_stable"`, `"non_food"`)
- **Excluded classes:** medicines / supplements / vitamins; condiments &
  sauces; spices & seasonings; household / toiletries.
- **System prompt** updated to classify these as `track_worthy=false` with a
  reason, while still tracking genuinely perishable items and legitimately
  tracked shelf-stable staples per existing guidance (the prompt's
  `canned beans = 365` example etc. stays — the line is "low-waste condiment/
  spice" vs "real pantry stock").
- **Transparency + override:** the receipt reply lists excluded items, e.g.
  `Skipped (shelf-stable): ketchup, salt, pepper`. To track one anyway, the
  user manually `/add`s it (existing path bypasses recognition).

**Touched modules:** `llm.py` (schema + prompt), `ingest_service.py`
(filter on `track_worthy`, collect excluded list), `renderer.py` (report
excluded), `IngestSummary` (new excluded fields).

### 5. Websearch shelf-life accuracy

**Goal:** improve shelf-life estimates using web search, paying the cost at
most once per food type.

**Precedence:** `cache → websearch → LLM estimate` (plus `defaults` for `/add`,
slotting websearch ahead of the LLM estimate).

**Trigger:** **cache miss only.** A successful, confident search result is
**written back to `ShelfLifeCache`**, so each food type is searched once, ever;
cost scales with pantry *variety*, not receipt count.

**Receipts (background refine):**
1. Insert items immediately with the LLM estimate and reply instantly.
2. Spawn a **background task** (own DB session) that, for each uncached item,
   runs a web search.
3. **Refine guard:** update an item only if it is still `active` **and** still
   on the original LLM estimate (`shelf_life_source` unchanged by the user).
   User corrections / actions are never overwritten.
4. On update: adjust `shelf_life_days` + `expires_on`, set source to reflect
   websearch, write `ShelfLifeCache`, and **edit the original reply in place**
   (mark refined lines, e.g. `✓refined`). No new follow-up message.

**`/add` (inline search):** on a cache miss, search the **single** item inline
before showing the proposal diff (the user is already waiting on the proposal),
rather than in the background.

**Mechanism:** Anthropic native `web_search` server tool attached to the
messages call. Exact wiring (tool block shape, result parsing, cost fields)
confirmed from current Anthropic docs at planning time.

**Failure / low confidence:** silently keep the LLM estimate; no item update,
no message edit. (Optionally still cache the LLM estimate as today's behavior
does.)

**Cost:** web search cost is tracked and surfaced in `/stats`, consistent with
the existing per-call cost accounting (`Receipt.llm_cost_micros_usd`,
`PendingCorrection.llm_cost_micros_usd`, `compute_stats`).

**Touched modules:** new search-resolver module + Protocol; `ingest_service.py`
(background refine path), `correction_service.py` (`/add` inline search),
`cache.py` (write-back), `bot.py` (background task wiring + message edit),
`pantry_service.py`/`renderer.py` (`/stats` cost surfacing).

## Cross-cutting design conventions (preserved)

- **Session injection** and **explicit `today: date`** conventions hold; the
  background refine task creates its own short-lived session and is given the
  stored `chat_id` + `message_id` to edit the reply.
- **Protocol + fakes:** the web search client is introduced as a `Protocol`
  with a `FakeSearchClient` for tests, mirroring `FakeLLMClient`.

## Testing strategy

- **Undo:** full vs partial undo (items kept when eaten/corrected); receipt
  deleted only on full undo; FK nulled on removed items; 10-min TTL expiry;
  cache untouched.
- **Rendering:** category grouping + ordering; qty/unit shown; urgency icon
  thresholds; digest adopts per-line decorations.
- **Year indicator:** same-year (no year), next-year, Dec→Jan boundary case.
- **Recognition:** `track_worthy=false` classes filtered and reported;
  perishables still tracked; `/add` override path.
- **Websearch:** cache-miss triggers search; write-back; refine guard skips
  user-touched/inactive items; `/add` inline search; failure keeps estimate;
  cost surfaced in stats. Background refine tested by awaiting the injected
  task directly (deterministic, no real async timing).

## Out of scope (v3)

- Family / shared-pantry: multi-user membership, invites, shared pantry
  partitioning, per-action attribution, concurrent edits, scheduler rewrite.
  Gets its own spec → plan → implementation cycle.

## Open items confirmed during design

- Undo target = last **event** (receipt batch or single `/add`), inline button,
  10-min TTL, partial-safe, cache kept.
- Receipt deletion = **full undo only**.
- `/list` = **category-primary**, digest stays urgency-bucketed; shared
  per-line decorations.
- Year rule = **different calendar year**.
- Exclusions = medicines/supplements, condiments/sauces, spices/seasonings,
  household/toiletries; reported + `/add` override.
- Websearch = cache-miss trigger, write-back, background refine for receipts,
  inline for `/add`, untouched-only refine guard, edit-in-place, cost tracked.
