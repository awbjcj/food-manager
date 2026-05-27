# Food Manager v1.5 — Design Spec

**Date:** 2026-05-27
**Author:** awbjcj@gmail.com (with Claude, grilling)
**Status:** Approved for implementation planning
**Builds on:** `docs/superpowers/specs/2026-05-26-food-manager-v1-design.md` and
`docs/superpowers/plans/2026-05-26-food-manager-v1.md`

## 1. Purpose

v1.5 turns `/correct` and `/add` from narrow regex commands into
free-form natural-language commands backed by a cheap text-LLM, with
every proposed database write gated behind an inline `[Apply]/[Cancel]`
confirmation. Two user-facing surfaces grow; one new subsystem
(pending-correction storage + apply/cancel callbacks) lands underneath.

## 2. Scope

### In scope (v1.5)

- **`/correct <item_id> <free-text>`** — LLM-driven correction of any
  combination of `raw_name` / `normalized_name`, `category`,
  `expires_on`, and `shelf_life_days`. Shown to the user as a diff
  message; written only on `[Apply]`.
- **`/add <free-text>`** — LLM-driven manual entry. Supports one or
  more comma-or-otherwise-separated items in a single message; one
  pending row and one diff message per parsed item, each with its own
  `[Apply]/[Cancel]` buttons. Replaces v1's regex parser.
- **New `PendingCorrection` table** persisted alongside existing v1
  tables, holding the LLM-proposed diff/payload between Propose and
  Apply.
- **Apply / Cancel callback handlers** that resolve a pending row,
  mutate the DB inside a single transaction, and edit the original
  diff message in place.
- **Stale-pending semantics:** any mutation to an item kills pending
  corrections for that item in-transaction; a TTL sweep job kills any
  un-tapped diffs after 10 minutes.
- **Two-model split:** vision ingestion stays on `ANTHROPIC_MODEL`
  (default `claude-sonnet-4-6`); `/correct` and `/add` use a new
  `ANTHROPIC_TEXT_MODEL` (default `claude-haiku-4-5-20251001`).
- **`/stats` extension:** separate line items for receipts /
  corrections / adds, each with its own LLM cost roll-up.

### Explicit non-goals (v1.5)

These are deliberately out so v1.5 stays shippable.

- No `/undo` command. Apply/Cancel is the safety net; if a wrong
  Apply lands, re-`/correct` it.
- No mutation of `qty` or `unit` via `/correct` — display metadata,
  v2 territory.
- No multi-step LLM agent loops. One Haiku call per `/correct`, one
  per `/add` batch.
- No automatic re-classification of OTHER pantry items when one is
  renamed. A `/correct` on `#42` does not retroactively touch `#41`.
- No web UI. Confirmation lives in Telegram inline buttons.
- No backwards-compatibility for `/correct <id> <int_days>` and no
  preservation of v1's `/add` regex parser. The LLM path handles
  `/correct 42 7d` and `/add 2 lb chicken, dozen eggs` trivially.
- No `/cancel <pending_id>` command. The inline button is the only
  cancel surface; TTL handles walk-away.
- No row-level encryption of the pending payload. SQLite file
  permissions are the same as v1.
- No Gmail / email auto-ingest connector. That remains a separate
  v1.6/v2 feature because it needs OAuth, encrypted refresh-token
  storage, polling/source dedupe, and email filtering.
- No dedicated text-LLM usage/audit table. v1.5 `/stats` counts
  persisted pending proposal rows only; no-change and failed text-LLM
  attempts are log-only.
- Everything else listed in §2 of the v1 spec remains out of scope.

## 3. Locked decisions

| Decision | Choice | Notes |
|---|---|---|
| `/correct` syntax | LLM-first natural language | Haiku-class text model |
| Confirmation pattern | `[Apply]/[Cancel]` diff message, always | One extra tap; prevents silent LLM-driven corruption |
| Mutable fields via `/correct` | `name` (raw + normalized), `category`, `expires_on`, `shelf_life_days` | `qty`/`unit` deferred |
| Cache behavior on rename | LLM emits `cache_action: "move" \| "add_new" \| "leave"`, shown in diff | Most expressive; user sees and approves |
| `/add` syntax | LLM-first natural language (symmetric with `/correct`) | Replaces regex parser |
| Pending storage | New SQLite table `PendingCorrection`, Alembic `0002` | Survives bot restart |
| `/add` missing expiry | Service-layer fallback: cache → defaults → LLM estimate from the same `parse_add` call → conservative 3-day fallback | Reuses v1 logic; no second LLM call |
| Stale apply | Auto-expire pendings on any mutation to the item | In-transaction |
| `/add` batches | One pending row per parsed item; one diff message per row | Each independently applicable |
| `/stats` LLM cost | Separate columns/lines for receipts / correction proposals / add proposals | Text buckets are pending-row based |
| Pending TTL | 10 minutes; 5-minute sweep job marks `expired` | Bounds the pending table |
| Null diff | `"no changes detected"`, no row written | Not counted in `/stats`; info/warning log only |
| Model | Vision: `ANTHROPIC_MODEL` (default `claude-sonnet-4-6`); text: `ANTHROPIC_TEXT_MODEL` (default `claude-haiku-4-5-20251001`) | Two separate env vars; one client per |

## 4. Architecture

### 4.1 Module map (deltas only; v1 modules unchanged unless noted)

```
                    Telegram (user)
                          │  /correct, /add, [Apply], [Cancel]
                          ▼
                  ┌────────────────────┐
                  │  bot.py            │
                  │  + handle_correct  │ (rewired)
                  │  + handle_add      │ (rewired)
                  │  + handle_apply    │ (NEW callback)
                  │  + handle_cancel   │ (NEW callback)
                  └─────────┬──────────┘
                            │
            ┌───────────────┴───────────────┐
            ▼                               ▼
  ┌─────────────────────┐         ┌──────────────────────┐
  │ correction_service  │  NEW    │ pending_service      │  NEW
  │ - propose_correct() │         │ - create_pending()   │
  │ - propose_add()     │         │ - load_pending()     │
  │ - apply_correct()   │         │ - mark_applied()     │
  │ - apply_add()       │         │ - mark_cancelled()   │
  └─────┬───────────────┘         │ - expire_for_item()  │
        │                         │ - sweep_expired()    │
        ▼                         └──────────┬───────────┘
  ┌────────────────────────────┐             │
  │ llm.py                      │            │
  │ + TextLLMClient Protocol    │ NEW        │
  │ + AnthropicTextLLMClient    │ NEW        │
  │ + parse_correct(...)        │ NEW        │
  │ + parse_add(...)            │ NEW        │
  │ + CorrectionDiff,           │ NEW models │
  │   ProposedAddItem           │            │
  └────────────────────────────┘             │
                                             ▼
                                  ┌────────────────────────┐
                                  │ scheduler.py           │
                                  │ + sweep_expired_       │
                                  │   pendings (5-min job) │
                                  └────────────────────────┘
```

### 4.2 New and modified modules

| Module | Status | Responsibility |
|---|---|---|
| `app/models.py` | modified | Add `PendingCorrection` SQLModel; add `PendingActionType` / `PendingStatus` / `CacheAction` literal aliases |
| `app/llm.py` | modified | Add `TextLLMClient` Protocol with `parse_correct()` and `parse_add()`; add `AnthropicTextLLMClient`; add `CorrectionDiff` and `ProposedAddItem` Pydantic models; add Haiku pricing row |
| `app/correction_service.py` | NEW | Orchestrates the propose/apply lifecycle; wraps text-LLM calls; applies service-layer rules (back-compute, fallback chain) |
| `app/pending_service.py` | NEW | CRUD on `PendingCorrection`; mutation-based and TTL-based expiry helpers |
| `app/pantry_service.py` | modified | Every mutator (`mark_eaten` / `mark_tossed` / `mark_removed` / `snooze_item` / `correct_item`) calls `pending_service.expire_for_item` in the same transaction |
| `app/renderer.py` | modified | Add `render_correction_diff`, `render_add_diff`, `render_applied_correction`, `render_applied_add`, `render_terminal_state` (covers `cancelled` / `expired` / `stale` / `already_applied` / `already_cancelled`), `build_apply_cancel_keyboard` |
| `app/bot.py` | modified | Rewire `handle_correct` and `handle_add` to the propose pipeline; add `handle_apply` / `handle_cancel` callback handlers; register their callback prefixes (`apply:` / `cancel:`); update `parse_callback` to recognize them |
| `app/commands.py` | modified | Retire `parse_correct_args`; the `/correct` handler now passes the entire post-id substring to the service layer. `parse_callback` gains `apply` / `cancel` verbs |
| `app/scheduler.py` | modified | Register a process-wide `sweep_expired_pendings` cron job (every 5 min, UTC) |
| `app/settings.py` | modified | Add `anthropic_text_model` field with default `claude-haiku-4-5-20251001` |
| `app/ingest_service.py` | modified | `ingest_text` reduced to a primitive that takes an already-validated `ProposedAddRow` and writes the `PantryItem`. Old regex parsing helpers (`_HINT_RE`, `_QTY_PREFIX_RE`, `_parse_text_part`, `TextIngestSummary`) deleted |
| `migrations/versions/0002_pending_correction.py` | NEW | Adds the `PendingCorrection` table and its two indexes; no data migration needed |
| `.env.example` | modified | Add `ANTHROPIC_TEXT_MODEL=claude-haiku-4-5-20251001` |

### 4.3 Abstraction layer (still intentionally minimal)

The v1 spec's "Two Protocols only" rule is amended to three:
`LLMClient` (vision), `BotClient` (Telegram facade, unchanged), and the
new `TextLLMClient` (text-only correction/add parsing). All three exist
only so tests can inject fakes — no other abstractions are added.

## 5. Data model

One new table; existing tables untouched.

```python
Category = Literal[
    "dairy", "produce", "meat", "seafood", "bakery",
    "pantry", "frozen", "beverage", "other",
]
PendingActionType = Literal["correct", "add"]
PendingStatus = Literal["pending", "applied", "cancelled", "expired", "stale"]
CacheAction = Literal["move", "add_new", "leave"]


class PendingCorrection(SQLModel, table=True):
    __table_args__ = (
        Index("ix_pending_user_status_created",
              "user_id", "status", "created_at"),
        Index("ix_pending_item", "item_id"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.telegram_id", index=True)
    action_type: str                                   # PendingActionType
    item_id: Optional[int] = Field(
        default=None, foreign_key="pantryitem.id"
    )                                                  # NULL for /add
    proposed_json: str                                 # serialized payload (§5.1)
    original_snapshot_json: Optional[str] = None       # /correct only — diagnostic
    llm_cost_micros_usd: Optional[int] = None
    chat_id: int                                       # for edit_message_text
    message_id: Optional[int] = None                   # set after diff sent
    status: str = "pending"                            # PendingStatus
    created_at: datetime
    expires_at: datetime                               # created_at + 10 min
```

### 5.1 Serialized payload shapes

`/correct` (`action_type="correct"`):

```json
{
  "kind": "correct",
  "diff": {
    "name": {"old": "Milk", "new": "Heavy Cream"} | null,
    "category": {"old": "dairy", "new": "dairy"} | null,
    "expires_on": {"old": "2026-06-02", "new": "2026-06-05"} | null,
    "shelf_life_days": {"old": 7, "new": 10} | null
  },
  "cache_action": "move" | "add_new" | "leave",
  "rationale": "user clarified the item identity",
  "confidence": 0.92,
  "back_computed_days": true | false
}
```

`/add` (`action_type="add"`, one row per parsed item):

```json
{
  "kind": "add",
  "item": {
    "name": "Oat Milk",
    "category": "beverage",
    "qty": 0.5,
    "unit": "gal",
    "shelf_life_days": 10,
    "expires_on": "2026-06-06",
    "shelf_life_source": "user_correction" | "cache" | "manual_fallback" | "llm",
    "ingest_shelf_life_source": "manual_user_hint" | "cache" | "manual_fallback" | "llm",
    "explicit_user_expiry": true | false,
    "estimated_shelf_life_days": 10 | null,
    "confidence": 0.88
  }
}
```

Service code, NOT the LLM, fills `shelf_life_source` /
`ingest_shelf_life_source` based on which fallback tier produced the
final number.

### 5.2 Design notes

- **No FK from `PendingCorrection.item_id` to a unique constraint on
  `item_id`.** Multiple pendings can target the same item; the
  mutation-based expiry rule keeps them coherent.
- **`message_id` is nullable** because it is only known after the bot
  sends the diff message — populated in a follow-up update inside the
  same transaction.
- **`proposed_json` is JSON-as-text, not a JSON column.** SQLite has
  `JSON1` but the v1 stack keeps things plain; serialization is
  symmetric (Pydantic `.model_dump_json()` in/out).
- **`original_snapshot_json` is diagnostic only.** Apply does not
  refuse on snapshot mismatch — the mutation-based expiry rule
  already provides the integrity guarantee. The snapshot helps when
  reading the row in `sqlite3` for debugging.
- **No retention of applied/cancelled/expired rows beyond debug
  needs.** The sweep job moves `pending` → `expired` only; v1.5 does
  not delete terminal rows. A later `0003` migration can prune if
  the table grows uncomfortably.

## 6. LLM contract (text model)

### 6.1 `parse_correct`

```python
class CorrectionDiff(BaseModel):
    name: Optional[str] = None                    # new raw_name; None = unchanged
    category: Optional[Category] = None
    expires_on: Optional[date] = None
    shelf_life_days: Optional[int] = Field(default=None, ge=1, le=730)
    cache_action: CacheAction = "leave"
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class TextLLMClient(Protocol):
    async def parse_correct(
        self, *, item_snapshot: dict, cache_snapshot: dict | None,
        user_text: str, today: date,
    ) -> tuple[CorrectionDiff, int | None]: ...
    async def parse_add(
        self, *, user_text: str, today: date, tz: str,
    ) -> tuple[list[ProposedAddItem], int | None]: ...
```

The second tuple element is the LLM call cost in micros USD (or `None`
for unknown models). Both methods raise on transport or schema failure
after the bounded retry policy (same shape as v1's
`AnthropicLLMClient`: 2 transport retries with backoff, 1 corrective
retry on schema failure).

**System prompt sketch (tunable; lives in `app/llm.py` next to the
v1 vision prompt):**

```
You parse a user-supplied correction for a single pantry item.
Return ONLY valid JSON matching the schema. No prose.

You receive:
  - item_snapshot: {id, raw_name, normalized_name, category,
                    qty, unit, purchased_on, shelf_life_days,
                    expires_on, status}
  - cache_snapshot: null OR {normalized_name, days, category,
                              source, confidence, learned_at}
  - today: YYYY-MM-DD in the user's local timezone
  - user_text: free-form correction message

Rules:
  - Set ONLY the fields the user actually wants to change.
  - Never set both shelf_life_days and expires_on; prefer the
    one the user stated more explicitly.
  - cache_action="move" when the user clarifies a misidentified
    item (e.g. "this is actually heavy cream"). Use "add_new"
    when both names are legitimate but distinct (e.g. "this
    receipt line was generic milk, but this carton is whole
    milk"). Use "leave" when only date/category/days changes.
  - rationale: one short clause explaining the change.
  - confidence: 0.0–1.0 of your parse, not of the food domain.
```

The user-facing `TODO(user)` marker (per v1 §10) sits in this prompt
so the user can tune over time.

### 6.2 `parse_add`

```python
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

**System prompt sketch:**

```
You parse a user-supplied "add to pantry" message into one or
more discrete items. Return ONLY valid JSON: a list of items
matching the schema.

For each item:
  - name: clean, expanded ("Oat Milk", not "OM 1/2 gal").
  - category: one of dairy|produce|meat|seafood|bakery|pantry|
              frozen|beverage|other. Null if unsure.
  - qty / unit: as the user stated; default qty=1.0; unit may
                be null.
  - explicit_user_expiry: true if the user explicitly stated
                          a shelf life ("keeps 10 days",
                          "expires June 5"), else false.
  - shelf_life_days: integer 1..730 ONLY if explicit_user_expiry
                     is true. Leave null otherwise; the service
                     layer will fall back to the user's cache,
                     defaults, and estimated_shelf_life_days.
  - expires_on: YYYY-MM-DD if the user stated an absolute date.
                Leave null otherwise.
  - estimated_shelf_life_days: conservative food-domain estimate
                for this item under normal storage, even when the
                user did not state expiry. Null only if genuinely
                unknown. The service layer may use this after cache
                and defaults miss.
  - confidence: 0.0–1.0 of your parse.

Comma, semicolon, "and", and newline are valid item
separators. Do NOT invent items the user didn't mention.
```

### 6.3 Service-layer post-processing

`/correct`:
- If `name is None and category is None and expires_on is None and shelf_life_days is None` → null diff, no pending row, bot replies `"no changes detected"`.
- If both `expires_on` and `shelf_life_days` are set, prefer `shelf_life_days` and log a warning (the prompt forbids both; this is the safety net).
- If only `expires_on` is set, back-compute
  `shelf_life_days = (expires_on - item.purchased_on).days`. If the
  result is outside `[1, 730]`, reject the diff with
  `"expires_on out of range for purchase date"`.
- If only `shelf_life_days` is set, compute
  `expires_on = item.purchased_on + timedelta(days=shelf_life_days)`.
- The resulting `proposed_json.diff.*` map records `{"old": ..., "new": ...}` for each changed field, computed against the snapshot at propose time.

`/add`:
- If `explicit_user_expiry is True`, treat the user as authoritative:
  `shelf_life_source="user_correction"`,
  `ingest_shelf_life_source="manual_user_hint"`, and on Apply write a
  `user_correction` cache row keyed by `normalize(name)`.
- Else run the fallback chain: `cache → defaults → LLM estimate from
  estimated_shelf_life_days`. This uses the same `parse_add` call;
  no second text-LLM call is made.
  Source labels become `cache` / `manual_fallback` / `llm` (a new
  `IngestShelfLifeSource` literal `"llm"` may be added if needed; v1
  already permits it on the receipt path).
- If cache/defaults miss and `estimated_shelf_life_days` is null, the
  service layer assigns a conservative `shelf_life_days=3` and
  `ingest_shelf_life_source="manual_fallback"`, surfacing that
  assumption in the diff. v1.5 never inserts a `PantryItem` with
  `shelf_life_days=0` or `expires_on=null`.
- Cost from one LLM call is split evenly across the N pending rows
  (`per_row = total_cost // N`, remainder added to the first row).

## 7. End-to-end flows

### 7.1 `/correct`

```
user: /correct 42 actually heavy cream, dairy, expires June 5
  │
  ▼
bot.handle_correct:
  ├ auth guard (existing)
  ├ parse first token after /correct as item_id (existing helper)
  ├ load PantryItem 42 (owned by user); reject NotOwnerOrMissing
  ├ load ShelfLifeCache row for normalize(item.raw_name)
  ├ correction_service.propose_correct(session, user, item,
  │                                    cache_row, free_text)
  │     ├ snapshot = serialize(item)
  │     ├ diff, cost = TextLLMClient.parse_correct(...)
  │     ├ if null diff: return None
  │     ├ apply post-processing rules (back-compute, range checks)
  │     ├ payload = {kind:"correct", diff, cache_action, rationale,
  │     │            confidence, back_computed_days}
  │     └ return Proposal(payload, cost, snapshot)
  ├ if None: reply "no changes detected"
  └ else:
      ├ pending = pending_service.create_pending(
      │      session, user_id, action="correct", item_id=42,
      │      proposed_json=payload.json, snapshot_json=snapshot.json,
      │      cost=cost, chat_id=msg.chat.id)
      ├ text, keyboard = renderer.render_correction_diff(
      │      pending.id, payload, item_snapshot)
      ├ sent = await bot.send_message(chat_id, text, reply_markup=keyboard)
      └ pending.message_id = sent.message_id; session.commit()
```

Rendered diff message:

```
Proposed correction for #42 Milk:
  • name: Milk → Heavy Cream
  • category: dairy (unchanged)
  • expires_on: 2026-06-02 → 2026-06-05
  • shelf_life_days: 7 → 10  (back-computed from expires_on)
  • cache: move learning from "milk" → "heavy cream"

Reason: user clarified the item identity
Expires in 10 min.
[ ✓ Apply ]  [ ✗ Cancel ]
```

Callback data: `apply:{pending_id}` and `cancel:{pending_id}`.

### 7.2 `/add`

```
user: /add a half gallon of oat milk that keeps about 10 days, a fresh basil
  │
  ▼
bot.handle_add:
  ├ auth guard (existing)
  ├ correction_service.propose_add(session, user, free_text, today)
  │     ├ items, cost = TextLLMClient.parse_add(...)
  │     ├ for each ProposedAddItem:
  │     │     resolve shelf life via fallback chain
  │     │     → ProposedAddRow with sources filled in
  │     ├ split cost across rows
  │     └ return list[Proposal]
  └ for each Proposal:
      ├ create pending row + diff message (same shape as /correct)
      └ (N separate messages, each with its own buttons)
```

Rendered per-item diff message:

```
Proposed add — Oat Milk:
  • category: beverage
  • qty / unit: 0.5 gal
  • expires_on: 2026-06-06 (user said "about 10 days")
  • shelf_life_days: 10  (source: user_correction)

Confidence: 0.88
Expires in 10 min.
[ ✓ Apply ]  [ ✗ Cancel ]
```

### 7.3 Apply / Cancel

```
callback "apply:123" (or "cancel:123"):
  ├ auth check (telegram user == ALLOWED_TELEGRAM_USER_ID)
  ├ session.get(PendingCorrection, 123)
  ├ verify pending.user_id == cb.from_user.id; else "not found"
  ├ if pending.status != "pending" OR pending.expires_at <= now():
  │     ├ cb.answer(f"already {pending.status}" or "expired")
  │     ├ edit message to neutered terminal state
  │     └ return
  ├ if action_type == "correct":
  │     ├ payload = json.loads(pending.proposed_json)
  │     ├ correction_service.apply_correct(session, pending, payload)
  │     │     ├ pantry_service.expire_for_item(session, user_id, item_id)
  │     │     │   (kills siblings BEFORE we mutate)
  │     │     ├ item = load item; mutate fields per diff
  │     │     ├ resolve cache_action:
  │     │     │     "move":    delete OLD cache row, write NEW
  │     │     │                 user_correction row keyed on
  │     │     │                 normalize(new_name)
  │     │     │     "add_new": leave OLD; write NEW user_correction row
  │     │     │     "leave":   leave cache untouched
  │     │     ├ if shelf_life_days changed (regardless of name):
  │     │     │     ensure cache row at the current normalized_name
  │     │     │     is a user_correction with the new days
  │     │     ├ if only category changed:
  │     │     │     ensure cache row at the current normalized_name
  │     │     │     is a user_correction preserving current days
  │     │     │     and using the corrected category
  │     │     └ if shelf_life_days changed:
  │     │           item.shelf_life_source = "user_correction"
  │     ├ pending.status = "applied"; session.commit()
  │     └ edit_message_text(render_applied(payload, item))
  │
  ├ if action_type == "add":
  │     ├ payload = json.loads(pending.proposed_json)
  │     ├ correction_service.apply_add(session, pending, payload) → new id
  │     │     ├ insert PantryItem (created_via="manual",
  │     │     │   source_receipt_id=None) with payload's resolved fields
  │     │     └ if shelf_life_source == "user_correction":
  │     │         write user_correction cache row
  │     ├ pending.status = "applied"; session.commit()
  │     └ edit_message_text(render_applied_add(new_id, payload))
  │
  └ if cancel: pending.status="cancelled"; session.commit();
                edit_message_text("✗ Cancelled.")
```

Idempotency: tapping `[Apply]` twice rapidly is harmless — the second
tap loads the row, sees `status != "pending"`, and edits the message
to the already-applied state without mutating anything else.

### 7.4 Stale-pending expiry

Three sources, all set `status` to a terminal value so Apply refuses:

1. **TTL (10 min)** — APScheduler cron job `sweep_expired_pendings`
   runs every 5 min UTC, sets `status="expired"` for all rows where
   `status="pending" AND expires_at < now()`. Does NOT edit Telegram
   messages — those become dead buttons that, when tapped, hit the
   "already expired" branch in the callback handler.
2. **Mutation-based** — every mutator in `pantry_service`
   (`mark_eaten`, `mark_tossed`, `mark_removed`, `snooze_item`,
   `correct_item`, and `apply_correct` itself) calls
   `pending_service.expire_for_item(session, user_id, item_id)`
   BEFORE its own commit, setting siblings to `status="stale"`. Same
   transaction → atomic.
3. **Explicit `[Cancel]` button** — sets `status="cancelled"`.

`expire_for_item` MUST NOT cascade to other users' rows; the
predicate is `user_id=? AND item_id=? AND status='pending'`.

### 7.5 Auth and chat-type rules

Unchanged from v1: only `ALLOWED_TELEGRAM_USER_ID` may invoke any of
`/correct`, `/add`, Apply, or Cancel; private chat only; unauthorized
attempts are logged and silently rejected per v1 §7.4.

## 8. Commands (v1.5 deltas)

| Command | v1 behavior | v1.5 behavior |
|---|---|---|
| `/correct <id> <free-text>` | `parse_correct_args` requires `<int days>` only; sets `shelf_life_days` + writes a `user_correction` cache row | Free text after `<id>` is sent to `TextLLMClient.parse_correct`. Bot replies with a diff message and `[Apply]/[Cancel]` buttons. Apply mutates per §7.3 |
| `/add <free-text>` | Regex parser handles `qty unit name [Nd]` segments separated by commas | Free text sent to `TextLLMClient.parse_add`. Bot replies with one diff message per parsed item, each with `[Apply]/[Cancel]` buttons |
| `/help` | Lists v1 commands | Updated to describe new `/correct` and `/add` semantics and `[Apply]/[Cancel]` behavior, including the 10-minute TTL |

All other commands behave identically. The `/add` reply format from
v1 (`"Added N items: ..."`) is replaced by the per-item diff messages,
each turning into `"✓ Added #<id> ..."` only on Apply.

## 9. Settings and secrets

`.env.example` gains one line; existing entries unchanged:

```
ANTHROPIC_TEXT_MODEL=claude-haiku-4-5-20251001
```

`app/settings.py`:

```python
anthropic_text_model: str = Field(
    default="claude-haiku-4-5-20251001",
    alias="ANTHROPIC_TEXT_MODEL",
)
```

`_PRICE_MICROS_PER_TOKEN_BY_MODEL` in `app/llm.py` gains:

```python
"claude-haiku-4-5-20251001": {"input": 1, "output": 5},
```

These are micros-USD per token; verify the values against current
Anthropic pricing at implementation time. Unknown-model cost still
reports `None` per v1 §6.3.

## 10. `/stats` extension

```python
@dataclass(frozen=True)
class TextLLMCost:
    correction_proposal_count: int
    correction_cost_micros: int
    correction_unknown_cost_count: int
    add_proposal_count: int
    add_cost_micros: int
    add_unknown_cost_count: int


# Stats grows one field:
class Stats:
    # ... existing fields unchanged ...
    text_llm: TextLLMCost
```

Query: `SELECT action_type, llm_cost_micros_usd FROM pendingcorrection
WHERE user_id = ? AND created_at >= ?` (30 days), grouped in Python
into the dataclass above. Text stats are proposal-row based, not
attempt based. Cost is counted whether the pending was applied,
cancelled, expired, or stale — the LLM call already happened. A
null-diff `/correct`, transport failure, or parse/schema failure creates
no pending row and is not counted in `/stats`; those attempts are
log-only in v1.5. Attempt-level accounting would require a separate
usage table and is explicitly out of scope.

Rendered output:

```
Last 30 days:
  Receipts:    12  ($0.18 total / $0.015 avg)
  Corrections:  8  ($0.0032 total, 1 unknown)
  Adds:         4  ($0.0019 total)
  ...
```

`compute_stats` no longer marks tracked-item / cache-hit % as
"receipt-only" — but its semantics are unchanged because manual
adds in v1.5 still write rows with `created_via="manual"` and are
already excluded from the cache-hit % calculation.

## 11. Scheduler additions

In addition to v1's per-user `digest:{user_id}` cron job, the
scheduler registers one process-wide job:

```python
scheduler.add_job(
    pending_service.sweep_expired,
    "cron", minute="*/5", timezone="UTC",
    args=[session_factory],
    id="sweep_expired_pendings",
    replace_existing=True,
)
```

`sweep_expired(session_factory)` opens a fresh session, marks expired
rows, commits, closes. No retry on failure (the next 5-min tick will
catch any that slipped). Failures log `pending_sweep_failed` with the
error class only.

## 12. Migrations

Single new Alembic revision: `0002_pending_correction`.

- Creates `pendingcorrection` table with the columns in §5.
- Creates `ix_pending_user_status_created` and `ix_pending_item`.
- `down_revision = "0001_initial"`.

The v1 startup-time SQLite backup rotation (v1 spec §9.2) still
runs before migration — `0002` is small but the safety net is the
same as for any production migration.

## 13. Testing strategy

| Layer | What's tested | Tooling | Count target |
|---|---|---|---|
| Unit | `CorrectionDiff` Pydantic validation; back-compute math (`expires_on` ↔ `shelf_life_days`); range checks; null-diff detection; cost-split rounding; renderer output for correction / add / applied / cancelled / stale; cache-action resolver | `pytest` + parametrize | 25–35 |
| Integration | `propose_correct` against `:memory:` with `FakeTextLLMClient`; `apply_correct` updates pantry + cache atomically for each `cache_action`; `apply_add` writes a `PantryItem` and optional cache row; mutation-based expiry kills siblings (snooze #42 then Apply pending → stale); TTL sweep marks expired rows | `pytest` + factory fixtures | 15–20 |
| Bot smoke | `handle_correct` and `handle_add` send the expected messages; Apply / Cancel callbacks edit the original message in place; rejected (`stale` / `expired` / `cancelled` / `applied`) tapped buttons reply sensibly without mutating | `pytest-asyncio`, aiogram test helpers | 6–8 |
| Migration | `0002_pending_correction` creates table + indexes on a temp DB | `pytest` + Alembic | 1 |
| Manual | Real `@food_manager_dev_bot` end-to-end: /correct rename + cache move; /add multi-item + selective Apply; walk-away → 10-min expiry | the user | ongoing |
| Golden | v1's `bin/eval_receipts.py` is untouched. A new `bin/eval_text_llm.py` (optional, not blocking) runs ~5 fixture (item_snapshot, user_text) pairs through the real Haiku and diffs against expected `CorrectionDiff` / `ProposedAddItem`. | manual / weekly | n/a |

`FakeTextLLMClient` mirrors `FakeLLMClient`: dataclass with
`canned_correct`, `canned_add`, `canned_sequence`, `calls`,
`raise_n_times`. Lives next to the existing fake in `tests/fakes.py`.

No mocking of Anthropic in unit/integration tests — Protocol-based
fakes only.

## 14. Deployment

Same Docker container, same `python bin/run.py` entry point. Startup
order changes only in that `bin/run.py` now constructs TWO clients
(`AnthropicLLMClient` for vision, `AnthropicTextLLMClient` for
text) and passes both into `build_dispatcher`. APScheduler
registration adds the sweep job alongside per-user digest jobs.

The persistent volume and SQLite path are unchanged.

## 15. User-authored TODO markers (additions to v1 §10)

6. **`app/llm.py:CORRECTION_SYSTEM_PROMPT`** — tune the rules the LLM
   uses to choose `cache_action`, especially the
   `move`-vs-`add_new` boundary, against the user's actual /correct
   patterns. Default text in §6.1 is the starting point.
7. **`app/llm.py:ADD_SYSTEM_PROMPT`** — tune the separator handling
   and the "do not invent items" guidance against the user's actual
   `/add` patterns. Default text in §6.2.
8. **`app/renderer.py:render_correction_diff` and
   `render_add_diff`** — the wording, emoji, and ordering of fields
   in the diff messages. Read every time the user wants to correct
   something.

## 16. Risks and open questions

| Risk | Mitigation |
|---|---|
| Haiku misparses a correction and the diff looks plausible enough to Apply | Confirmation flow forces a deliberate tap; `/correct` again is always safe; `cache_action="leave"` is the default so a bad parse never silently moves learning |
| Pending table grows unbounded if user never cancels and never restarts | TTL sweep keeps `pending` rows bounded; terminal rows (`applied` / `cancelled` / `expired` / `stale`) accumulate but are tiny; v2 prune migration if it becomes a problem |
| Haiku pricing changes | `_PRICE_MICROS_PER_TOKEN_BY_MODEL` is editable; unknown-model cost reports `None`; `/stats` shows `(N unknown)` per bucket |
| Two LLM calls per ingest-then-correct flow doubles the per-event cost | Haiku is ~30× cheaper than Sonnet; in absolute terms a /correct costs cents-per-month even at typical use |
| Mutation-based expiry misses an edge case (e.g. direct SQL or future feature mutating items) | Helper is centralized in `pending_service.expire_for_item`; any new mutator MUST call it; covered by a "every pantry_service mutator calls expire_for_item" test |
| Telegram message edit fails after Apply (rate limit, deleted message) | Log `pending_message_edit_failed`; the DB write already committed, so the user's pantry state is correct even if the message stays as the diff |

## 17. v2 triggers

| Trigger | Add |
|---|---|
| Diff messages frequently re-corrected because the LLM picked the wrong `cache_action` | Add a third button to the diff: `[Apply, but keep cache]` |
| User asks to `/correct` qty/unit | Promote v1.5 §11 non-goal to a spec line; small change, just allow it in `CorrectionDiff` |
| Pending table grows past a few thousand rows | `0003_prune_terminal_pendings` migration: drop rows where `status != 'pending'` and `created_at < now()-90d` |
| User receives many grocery receipts by email | Gmail auto-ingest connector as a separate v1.6/v2 spec: OAuth, encrypted token storage, polling/source dedupe, sender/label filters |
| Multi-user / household lands (v1 §11.1 trigger) | `PendingCorrection.user_id` is already in the primary key path; no schema change needed |

## 18. Definition of done for v1.5

- New `PendingCorrection` table + Alembic `0002` migration applies
  cleanly on top of an existing v1 SQLite database with no data
  loss.
- `/correct <id> <free-text>` produces a pending row + diff message +
  working Apply/Cancel buttons; applied corrections update the
  pantry item and the cache per the LLM's `cache_action`.
- `/add <free-text>` (single or multi-item) produces N pending rows +
  N diff messages with working buttons. Selective Apply works.
- Mutation-based expiry verified end-to-end: `/snooze 42` after a
  pending `/correct 42` makes the pending tap reply `stale`.
- TTL sweep verified end-to-end (freezegun advances 11 minutes; sweep
  marks the row `expired`).
- `/stats` shows three separate cost buckets (receipts / corrections
  / adds) with both totals and unknown-cost counts.
- All unit, integration, and bot-smoke tests pass.
  `uv run pytest` clean.
- `/help` text is updated to reflect new `/correct` and `/add`
  semantics including the confirmation flow and TTL.
- README is updated with the new `ANTHROPIC_TEXT_MODEL` env var.

---

**End of design spec.** Next step: implementation plan via the
`superpowers:writing-plans` skill, derived from this document.
