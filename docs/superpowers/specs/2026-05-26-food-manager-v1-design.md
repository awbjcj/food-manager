# Food Manager v1 — Design Spec

**Date:** 2026-05-26
**Author:** awbjcj@gmail.com (with Claude, brainstorming + grilling)
**Status:** Approved for implementation planning

## 1. Purpose

A personal Telegram bot that ingests grocery receipt photos, estimates how
long each item will keep, stores the resulting pantry in SQLite, and sends a
single daily digest of items nearing expiry with inline buttons to mark them
eaten / tossed / snoozed. Corrections feed back into a shelf-life cache so
the bot's estimates improve over time.

## 2. Scope

### In scope (v1)

- Single user (the author).
- One input modality: receipt photo via Telegram.
- One reminder channel: Telegram, same chat as the bot.
- One vision-LLM call per receipt; structured-JSON output.
- Daily digest at user-configurable local hour, with `[Ate / Tossed / Remind +2d]`
  inline buttons that edit the digest message in place.
- Slash commands: `/start`, `/tz`, `/digest_at`, `/list [filter]`, `/add`,
  `/ate`, `/toss`, `/snooze`, `/correct`, `/delete`, `/stats`, `/help`.
- Persistent SQLite database on a mounted volume; long-running Python
  process deployed to Railway (or Fly.io / Render — choice deferred to
  implementation).
- Daily SQLite backup to off-host object storage (nice-to-have, not
  blocking).

### Explicit non-goals (v1)

These are real, valid v2+ features. They are out of v1 to keep the spec
shippable. Each will earn its own spec when its time comes.

- Multi-user / household-group sharing.
- Email / WhatsApp / WeChat / SMS notification channels.
- Voice input (Telegram voice messages → Whisper).
- Recipe recommender, recipe RAG corpus, goal-driven shopping list
  (see §11.1 for the v2 design sketch), multi-agent orchestration,
  nutrition data, user preference modelling beyond the shelf-life
  user-correction loop.
- Gmail auto-ingest connector for online grocery receipts (see §11.2 for
  the v1.5/v2 design sketch).
- Vercel deployment. A long-running scheduler is a poor fit for serverless;
  if Vercel ever becomes desirable, it requires splitting the bot webhook
  from the scheduler (Upstash QStash or similar). Out of scope here.
- Web UI / dashboard.
- Embedding-similarity matching for cache lookups.
- Receipt-image archive browser, item thumbnails in `/list`.

## 3. Locked decisions

| Decision | Choice | Notes |
|---|---|---|
| Language / runtime | Python 3.12 | `uv` for dependency management |
| Telegram framework | `aiogram` (async, long-polling) | No public webhook URL required |
| Database | SQLite + SQLModel | One file, no separate service |
| Migrations | Alembic | Start with `0001_initial`; persistent SQLite still needs migrations |
| Scheduler | APScheduler (`AsyncIOScheduler`) | In-process, shares the bot event loop |
| Vision LLM | Anthropic Claude (`claude-sonnet-4-6`) | Default; swap to OpenAI is a one-file change in `llm.py` |
| Hosting | Railway / Fly.io / Render | Long-running process, persistent volume |
| Settings / secrets | `pydantic-settings` from env / `.env` | Never logged |
| Logging | stdlib `logging` to stdout; JSON in prod | Railway tails it natively |

## 4. Architecture

### 4.1 Module map

```
┌──────────────────────────────────────────────────────────────────┐
│                       Telegram (user)                            │
└──────────────┬───────────────────────────────▲───────────────────┘
               │ photo, /commands              │ digest + buttons
               ▼                               │
        ┌─────────────────────────────────────────────┐
        │  bot.py (aiogram dispatcher, long-polling)  │
        │  handlers: /start, /list, photo, callback   │
        └─────┬───────────────────────────┬───────────┘
              │                           │
              ▼                           ▼
   ┌──────────────────────┐    ┌───────────────────────┐
   │  ingest_service.py   │    │  pantry_service.py    │
   │  photo → items + ttl │    │  list, mark eaten,    │
   │   (1 LLM vision call)│    │  snooze, delete       │
   └────┬────────────┬────┘    └──────────┬────────────┘
        │            │                    │
        ▼            ▼                    ▼
   ┌─────────┐  ┌──────────┐       ┌───────────────┐
   │ llm.py  │  │ cache    │       │  db.py        │
   │ (claude)│  │ (alias→  │◄──────┤  SQLModel +   │
   │         │  │  days)   │       │  SQLite file  │
   └─────────┘  └──────────┘       └───────┬───────┘
                                           ▲
   ┌─────────────────────────────────────┐ │
   │  scheduler.py  (APScheduler in-proc)│─┘
   │  - daily digest job (per-user cron) │
   │  - sends Telegram message via bot   │
   └─────────────────────────────────────┘
```

### 4.2 Module responsibilities

| Module | Owns | Depends on |
|---|---|---|
| `bot.py` | Telegram I/O, command + callback routing, user-facing error messages | services |
| `ingest_service.py` | Orchestrates photo → items → DB write; normalization + cache use | `llm`, `cache`, `db` |
| `pantry_service.py` | Non-ingest mutations: list, mark eaten/tossed, snooze, correct, stats | `db` |
| `scheduler.py` | Cron-style ticks, builds digest, sends via `Bot` instance | `pantry_service`, `renderer`, `Bot` |
| `renderer.py` | Pure formatting: digest text + inline keyboard from a list of items | none |
| `llm.py` | `extract_items_from_image(bytes) -> ParseResult` | anthropic SDK |
| `normalization.py` | `normalize(raw: str) -> str`; `ALIASES` dict | none |
| `shelf_life_defaults.py` | Conservative fallback days for manual `/add` cache misses | none |
| `cache.py` | Read/write `ShelfLifeCache` rows with user-correction priority | `db` |
| `db.py`, `models.py` | SQLModel models, session factory | sqlite |
| `bin/run.py` | Process entry point: builds Bot, registers handlers, starts scheduler + dispatcher | everything |

### 4.3 Abstraction layer (intentionally minimal)

Two Protocols only — `LLMClient` (`llm.py`) and `BotClient` (a thin facade
around `aiogram.Bot` used by `scheduler.py` and `pantry_service.py` for
send/edit). These exist *only* so tests can inject fakes; not for runtime
polymorphism. No other abstractions are introduced "for flexibility."

## 5. Data model

Three SQLModel tables. SQLite. `user_id` carried on owned tables even
though v1 is single-user — costs one column, prevents a migration when
groups arrive.

```python
Category = Literal[
    "dairy", "produce", "meat", "seafood", "bakery",
    "pantry", "frozen", "beverage", "other",
]

class User(SQLModel, table=True):
    telegram_id: int = Field(primary_key=True)   # also our auth
    chat_id: int                                 # private chat where digests get sent
    tz: str = "America/Detroit"                  # IANA name; for digest local time
    digest_hour: int = 8                         # 0–23
    created_at: datetime

class PantryItem(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.telegram_id", index=True)
    raw_name: str                                # what the LLM said: "Whole Milk 1 gal"
    normalized_name: str = Field(index=True)     # "whole milk"
    category: Category | None = Field(default=None, index=True)  # for /list <category>
    qty: float = 1.0
    unit: str | None = None                      # "gal", "lb", "oz", "g", "kg", "ml", "l", "ct", etc.
    purchased_on: date                           # from receipt or scan day
    shelf_life_days: int                         # chosen estimate used for expires_on
    shelf_life_source: Literal["cache","llm","manual_fallback","user_correction"]  # current value source
    ingest_shelf_life_source: Literal["cache","llm","manual_fallback","manual_user_hint"]  # original insert source
    expires_on: date = Field(index=True)         # purchased_on + days
    status: Literal["active","eaten","tossed","removed"] = "active"
    snoozed_until: date | None = None
    created_via: Literal["receipt","manual"]
    source_receipt_id: int | None = Field(default=None, foreign_key="receipt.id")
    created_at: datetime

class Receipt(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.telegram_id", index=True)
    photo_file_id: str            # Telegram file_id — re-fetch on demand, no blob in DB
    purchase_date: date
    purchase_date_source: Literal["receipt","scan_fallback"]
    scanned_at: datetime
    llm_cost_micros_usd: int | None    # total LLM cost for this ingestion

class ShelfLifeCache(SQLModel, table=True):
    user_id: int = Field(foreign_key="user.telegram_id", primary_key=True)
    normalized_name: str = Field(primary_key=True)
    days: int
    category: Category | None = None
    confidence: float                                # LLM-reported 0..1
    learned_at: datetime
    source: Literal["llm","user_correction"] = "llm"
```

### Design notes

- **No `Food` master table.** `ShelfLifeCache` IS the growing knowledge
  base. Promote to a typed taxonomy only if v2 demands it.
- **No merging of same-name items.** Separate purchases of the same food
  remain separate `PantryItem` rows with separate item IDs, purchase dates,
  and expiry dates.
- **Creation channel is explicit.** `created_via="receipt"` requires
  `source_receipt_id`; `created_via="manual"` requires `source_receipt_id
  is None`. v1 enforces this in service code/tests rather than SQLite check
  constraints.
- **Quantity is display metadata.** Quantity does not directly affect shelf
  life in v1. If package form changes shelf life, that distinction should
  appear in the item name before normalization/cache lookup.
- **`status` enum + `snoozed_until`** lets the daily digest query be one
  clean SELECT. Eaten/tossed rows are retained for consumption and waste
  stats; removed rows are retained only for audit/debug cleanup.
- **Snoozed items stay `active`.** `snoozed_until` temporarily suppresses
  reminders; it is not a separate item status.
- **`photo_file_id`, not bytes.** v1 does not archive receipt images. The
  Telegram file ID supports best-effort re-fetch while Telegram still makes
  the file available; durable image archival is out of scope.
- **Duplicate receipt guard.** Before extraction, ingestion checks whether
  the same user already has a `Receipt` with the same `photo_file_id`. If
  so, the bot replies that the receipt was already logged and does not
  insert duplicate pantry items. `Receipt` also has a DB-level unique
  constraint on `(user_id, photo_file_id)` to protect against races.
- **Receipt LLM cost includes retries.** `llm_cost_micros_usd` is the total
  API cost consumed by the receipt ingestion, including malformed-JSON
  correction retries and retry-after-error calls that reached the API.
- **`source: "user_correction"`** outranks `"llm"` in `cache.get(user_id,
  normalized_name)`. This is the entire learning loop — five lines of code,
  no ML. Cache rows are scoped per user from v1 so future household or
  multi-user support does not inherit another user's kitchen assumptions.
- **`category` denormalized onto `PantryItem`** (not joined via cache) so
  `/list dairy` is a single indexed query.

### Constraints and indexes

- `Receipt` has a unique constraint on `(user_id, photo_file_id)`.
- `PantryItem` is indexed for digest/list queries by `(user_id, status,
  expires_on)`.
- `PantryItem` is indexed for category lists by `(user_id, status, category,
  expires_on)`.
- `PantryItem.source_receipt_id` is indexed for receipt-item lookups and
  debugging.
- `ShelfLifeCache` uses `(user_id, normalized_name)` as its primary key.

## 6. Ingestion pipeline

End-to-end on photo receipt:

```
[user sends photo] ──► bot handler ──► ingest_service.ingest_photo(user, file_id)
                                                │
                                                ▼
                          ┌─────────────────────────────────────┐
                          │  1. download bytes from Telegram     │
                          │  2. llm.extract_items_from_image()   │  ◄── 1 API call
                          │  3. for each item:                   │
                          │       normalized = normalize(raw)    │
                          │       days = compute_days(parsed)    │
                          │  4. insert PantryItem rows           │
                          │  5. return summary for bot reply     │
                          └─────────────────────────────────────┘
```

### 6.1 The single LLM call

```python
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
  - est_shelf_life_days: expected shelf life from purchase date under the
      item's normal storage assumption; use frozen shelf life for frozen items.
      Must be an integer from 1 to 730.
      Use conservative estimates. Examples:
        whole milk = 7, fresh chicken = 2, bananas = 5,
        canned beans = 365, fresh bread = 4, eggs = 28
  - confidence: 0.0–1.0 how sure you are about the parsed food item fields
"""
```

The `est_shelf_life_days` figures and any kitchen-specific calibrations
are owned by the user — they are a TODO marker in `llm.py` for the user
to tune (see §10).

Pydantic models for the response:

```python
class ParsedItem(BaseModel):
    is_food: bool
    name: str
    qty: float = 1.0
    unit: str | None = None
    category: Category | None = None
    est_shelf_life_days: int = Field(ge=1, le=730)
    confidence: float

class ParseResult(BaseModel):
    purchase_date: date | None = None
    purchase_date_confidence: float = 0.0
    items: list[ParsedItem]
```

### 6.2 Normalization + cache

```python
def compute_days(user: User, parsed: ParsedItem) -> int:
    norm = normalize(parsed.name)
    cached = cache.get(user.telegram_id, norm)
    if cached and cached.source == "user_correction":
        return cached.days                    # user overrides always win
    if cached:
        return cached.days                    # prior LLM estimate
    if parsed.confidence >= 0.6:
        cache.put(user.telegram_id, norm, parsed.est_shelf_life_days,
                  source="llm",
                  category=parsed.category,
                  confidence=parsed.confidence)
    return parsed.est_shelf_life_days
```

`normalize()` is the cache-hit-rate-determining function. v1 baseline:
lowercase → strip non-alphanumerics → collapse whitespace → drop trailing
size suffixes (`1 gal`, `12 oz`, `dozen`) → apply small hand-curated
`ALIASES` dict. The exact rules are a TODO marker for the user (see §10);
the function signature and tests are scaffolded by the implementation.

Cache update policy: the first LLM estimate for a `(user_id,
normalized_name)` is kept until the user provides a shelf-life correction.
Later LLM estimates do not overwrite an existing cache row.
`learned_at` means when the current cache value was learned; `/correct`
updates it to the correction time.

### 6.3 Error handling

| Failure | Response |
|---|---|
| Anthropic 4xx/5xx or timeout | Retry 2× with exponential backoff; on final fail, bot replies "couldn't read that one, try a clearer photo or `/add <items>` manually" |
| LLM returns malformed JSON or fails schema validation | One retry with corrective system message summarizing validation errors; on second fail, same user-facing message |
| LLM returns zero items | One corrective retry; on second empty result, bot replies no food found |
| LLM returns `is_food == false` items | Drop from inserts; do not count as unclear skipped food |
| LLM returns food items with `confidence < 0.3` | Drop from inserts; bot reply notes "skipped N unclear items" and includes recognizable skipped names, capped to a few lines |
| LLM returns food items with `0.3 <= confidence < 0.6` | Insert the pantry item, but do not create a new LLM shelf-life cache row from it |
| Receipt date missing or `purchase_date_confidence < 0.7` | Use scan date as the assumed purchase date and make that assumption visible in the bot reply |

`Receipt` rows are created only after extraction yields at least one inserted
pantry item. Receipt, item, and cache writes for a successful ingestion are
committed in one transaction. If no food items are inserted, no `Receipt`
row is created; the bot replies with no food found / skipped-item context.

No circuit breakers, no dead-letter queue. Single user.

### 6.4 Bot reply after a successful ingest

```
✅ Logged 8 items from this receipt:
   • #42 Whole Milk 1 gal — exp May 31 (7d)
   • #43 Bananas ×6 — exp May 27 (3d)
   • ... (6 more)
Cost: $0.018
```

Whenever a pantry item name is displayed to the user, include its item ID
so command-based corrections and deletions have a visible reference.
User-facing item names use `raw_name`; `normalized_name` is internal to
lookup, grouping, and cache behavior.
The ingest reply includes purchase-date context only when useful: show
`Purchase date: <date>` when the receipt date differs from scan date, or
`Purchase date assumed: <date>` when scan-date fallback was used.
If any inserted food items had `0.3 <= confidence < 0.6`, the reply includes
a capped "Low confidence" note with item IDs so the user can `/delete` or
`/correct` them.

## 7. Scheduler & digest UX

### 7.1 Scheduler

In-process APScheduler `AsyncIOScheduler`, sharing the bot's event loop.
One cron job per registered user, identified by `id=f"digest:{user_id}"`
so it can be replaced when the user changes `digest_hour` or `tz`.

```python
def schedule_user_digest(user: User, bot: Bot):
    scheduler.add_job(
        send_digest, "cron",
        hour=user.digest_hour, minute=0, timezone=user.tz,
        args=[user.telegram_id, bot],
        id=f"digest:{user.telegram_id}",
        replace_existing=True,
    )
```

On process startup, `register_user_jobs()` re-creates the jobs from the
`User` table. APScheduler state is NOT persisted; missed digests are
intentionally not replayed (yesterday's "expiring today" is stale).

### 7.2 Digest query

The scheduler computes `today = datetime.now(ZoneInfo(user.tz)).date()` in
Python and passes that explicit date into the query. SQLite `DATE('now')`
is not used for digest bucketing because it depends on server time rather
than the user's configured timezone.

The reminder window is through the next 7 calendar days, inclusive of both
today and the date exactly 7 days from today.
Expired active items remain in every daily digest until they are marked
eaten/tossed or snoozed.

```sql
SELECT * FROM pantryitem
WHERE user_id = ?
  AND status = 'active'
  AND (snoozed_until IS NULL OR snoozed_until <= :today)
  AND expires_on <= :today_plus_7
ORDER BY expires_on ASC;
```

Bucketed by the renderer into:
- `expired`: `expires_on < today`
- `today`: `expires_on == today`
- `tomorrow`: `expires_on == today + 1`
- `this_week`: `today + 2 <= expires_on <= today + 7`

Empty result ⇒ no message is sent (silent days).

### 7.3 Digest message

```
🍅 Pantry digest — Tue May 27

❗ Expired (1)
   • #41 Spinach (yesterday)

🔥 Today (2)
   • #42 Whole Milk 1 gal
   • #43 Bananas (×6)

📅 Tomorrow (1)
   • #44 Sliced Bread

📆 This week (3)
   • #45 Greek Yogurt — Fri
   • #46 Chicken Thighs — Sat
   • #47 Strawberries — Sun
```

One row of inline buttons per item, callback data `act:{verb}:{item_id}`:

```
[✓ Ate]  [🗑 Tossed]  [⏰ Remind +2d]
```

If more than 20 matching pantry items would be actionable, render at most
20 item rows with inline buttons, note how many additional items were
omitted, and append a `[show all]` button. The follow-up can be paged text
rather than another 20-button digest.

On button tap:
1. Handler resolves the Telegram user and rejects the action unless the
   pantry item belongs to that user.
2. Service applies the mutation (status / snoozed_until).
3. Renderer rebuilds the message body.
4. Handler calls `bot.edit_message_text()` — same message, updated. No
   chat-spam from confirmations.

`Remind +2d` only sets `snoozed_until = today + 2`; it does not change
`expires_on` or learned shelf life. If the item is still expired when the
snooze ends, it returns to the expired bucket.
Callback actions are idempotent: repeated eaten/tossed actions against
already non-active items are harmless no-ops with an updated/already-updated
callback answer. Snooze against non-active items is rejected or treated as a
no-op.
Any terminal status change to `eaten`, `tossed`, or `removed` clears
`snoozed_until`.

The exact wording, emoji, and grouping headers in the digest are a TODO
marker for the user (see §10).

### 7.4 Commands

| Command | Behaviour |
|---|---|
| `/start` | For the authorized Telegram user only, creates User row, captures `chat_id`, shows the default timezone and how to change it |
| `/tz America/Detroit` | Sets timezone (IANA); reschedules digest job |
| `/digest_at 8` | Sets digest hour 0–23; reschedules |
| `/list` | All active items with item IDs, sorted by `expires_on` |
| `/list <category>` | Filter by category (`dairy`, `produce`, `meat`, `seafood`, `bakery`, `pantry`, `frozen`, `beverage`, `other`) |
| `/list week` | Items expiring in next 7 days |
| `/list expired` | Items past expiry, still active |
| `/add 2 lb chicken, dozen eggs` | Manual text-ingest, regex parser; accepts optional trailing shelf-life hints like `milk 7d`; uses normalization/cache for shelf life, but does not create LLM cache rows; on cache miss without an explicit hint, uses conservative built-in fallback if available or asks the user to `/correct` after adding |
| `/ate <item_id>` | Marks an active item eaten using the same mutation as the digest button |
| `/toss <item_id>` | Marks an active item tossed using the same mutation as the digest button |
| `/snooze <item_id> [days]` | Snoozes reminders for an active item; default is 2 days, allowed range 1–30 |
| `/correct <item_id> <shelf_life_days>` | Sets item's `shelf_life_days`, `expires_on` from its purchase date, and `shelf_life_source="user_correction"`; also writes a user-scoped `ShelfLifeCache` row with `source="user_correction"` |
| `/delete <item_id>` | Marks an incorrect or duplicate item as `removed` without counting it as eaten or tossed |
| `/stats` | Last-30-day: receipt count, tracked item count excluding `removed`, removed item count, cache-hit %, successful-receipt LLM spend, waste rate as `tossed / (eaten + tossed)` |
| `/help` | Lists the above, including that `/correct <id> <shelf_life_days>` teaches future estimates and `/delete <id>` is for wrong or duplicate imports |

All message and callback handlers enforce `ALLOWED_TELEGRAM_USER_ID`; other
Telegram users receive a generic not-authorized response and cannot create
users or mutate pantry items.
v1 supports private chat only. `telegram_id` remains identity/auth, while
`chat_id` records where replies and digests are sent; group chats are
rejected even if the authorized user is present.
Any authorized private-chat interaction auto-creates the `User` row if it
does not already exist, using `America/Detroit` and `digest_hour=8`.
Auto-creation immediately schedules the user's digest job through the same
schedule/replace path used by `/tz` and `/digest_at`. `/start` is a friendly
setup/status command rather than a required gate.
The default timezone is `America/Detroit`. `/tz` accepts valid IANA
timezone names only; ambiguous aliases such as `EST` are rejected with
examples. `/digest_at` accepts whole hours only, 0-23. `digest_hour` is a
wall-clock hour in the current timezone, so changing `/tz` keeps the same
hour number and reschedules the next digest in the new timezone.

`/correct` is allowed for `active`, `eaten`, and `tossed` pantry items, but
rejected for `removed` items so import-cleanup rows cannot teach the cache.
Commands that take an item ID accept either `42` or `#42`.
User-supplied shelf-life days from `/correct` and manual `/add` hints must
be in the same `1..730` range as LLM estimates.
`/ate`, `/toss`, and `/snooze` only transition active items; already
terminal items return an already-updated/not-active response rather than
changing status.
For manual `/add`, an explicit trailing shelf-life hint such as `7d` sets
`shelf_life_source="user_correction"`,
`ingest_shelf_life_source="manual_user_hint"`, and writes the user-scoped
cache. Manual adds use `purchased_on = today` in the user's configured
timezone; v1 does not parse backdated manual-add dates. Multiple
comma-separated manual items are parsed independently, so explicit `7d`
hints apply only to the item they trail, and parse failures are reported
without blocking successfully parsed items. Other separators are not
accepted; failed parses tell the user to separate items with commas.
Trailing shelf-life hints are not included in `raw_name`.

`/list` with no filter and `/list <category>` include all active items,
including expired active items, sorted oldest expiry first. Date-relative
commands, including `/list week` and `/list expired`, compute
`today` in the user's configured timezone. `/list week` uses the same
inclusive window as the digest: `today <= expires_on <= today + 7`. Snooze
only suppresses scheduled digest reminders; explicit `/list` commands still
show snoozed active items when they match the requested filter.

`/stats` LLM spend includes only successful receipt ingestions with stored
`Receipt` rows. Failed attempts that consumed LLM cost are logged but not
included in v1 stats. Cache-hit percentage is computed among pantry items
created from receipt ingestion in the last 30 days, excluding non-food,
skipped, manual-add, and `removed` items; a hit means shelf life came from
an existing cache row instead of the current LLM estimate, tracked via
`PantryItem.ingest_shelf_life_source`.

### 7.5 Scheduler error handling

| Failure | Response |
|---|---|
| `bot.send_message` raises | Log + retry once after 60s; if still failing, drop this tick |
| Process restart mid-day | Jobs recreated from `User` table on startup; missed digests NOT replayed |
| Clock drift / DST | APScheduler handles IANA tz natively |

## 8. Testing strategy

| Layer | What's tested | Tooling | Count target |
|---|---|---|---|
| Unit | `normalize()`, `compute_days()`, `render_digest()`, query builders | `pytest` + parametrize | 30–40 |
| Integration | services against `:memory:` SQLite, `FakeLLMClient` injected | `pytest`, factory fixtures | 10–15 |
| Bot smoke | One end-to-end per handler with aiogram test helpers + `FakeLLMClient` | `pytest-asyncio` | 5–6 |
| Manual | Real photos against a `@food_manager_dev_bot` private chat | the user | ongoing |
| Golden-receipt eval | `bin/eval_receipts.py` runs ~5 fixture photos through the *real* LLM, diffs against expected JSON | weekly | n/a |

No mocking of Anthropic in unit tests — Protocol-based fakes only. Golden
eval is the only place that hits the real API, and it's manual / weekly.

## 9. Deployment

### 9.1 Layout

```
food-manager/
├── app/
│   ├── bot.py
│   ├── ingest_service.py
│   ├── pantry_service.py
│   ├── scheduler.py
│   ├── llm.py
│   ├── normalization.py
│   ├── shelf_life_defaults.py
│   ├── renderer.py
│   ├── cache.py
│   ├── db.py
│   └── models.py
├── tests/
├── bin/
│   ├── run.py              # entry point: starts bot + scheduler in one loop
│   └── eval_receipts.py
├── migrations/             # Alembic migrations, starting with 0001_initial
├── pyproject.toml          # uv-managed
├── Dockerfile              # python:3.12-slim
├── railway.toml            # service + volume config
├── .env.example
└── README.md
```

### 9.2 Runtime

Single Docker container, single Python process: `python bin/run.py`. The
process owns both the aiogram dispatcher (long-polling) and the
APScheduler instance.
On startup, `bin/run.py` runs Alembic `upgrade head` before starting the bot
or scheduler; migration failure is fatal. If the SQLite DB already exists,
startup first creates a local timestamped SQLite `.backup` copy before
running migrations, retaining the latest 5 local migration backups. If the
pre-migration backup fails for an existing DB, startup fails before running
the migration.

Persistent volume mounted at `/data`; SQLite file at `/data/food.db`.

### 9.3 Backups (nice-to-have, not blocking v1)

Additional APScheduler job at 03:00 local:
```
sqlite3 /data/food.db ".backup /tmp/snap.db"
rclone copy /tmp/snap.db b2:food-mgr-backups/food-$(date +%F).db
```
Retain 14 daily snapshots. Backblaze B2 free tier covers it.

### 9.4 Secrets

```
TELEGRAM_BOT_TOKEN=...
ALLOWED_TELEGRAM_USER_ID=...
ANTHROPIC_API_KEY=...
DATABASE_PATH=/data/food.db   # or ./food.db locally
LOG_LEVEL=INFO
ENV=prod                      # dev|prod
```

Loaded via `pydantic-settings.BaseSettings`. Never logged. `.env`
gitignored; `.env.example` committed with placeholders.

### 9.5 Local dev

`uv run bin/run.py` with a local `.env`. Same code path as prod, SQLite
file in `./food.db` (gitignored). A separate `@food_manager_dev_bot`
Telegram bot is used so dev traffic doesn't collide with prod.

## 10. User-authored TODO markers

The implementation will scaffold five files with TODO blocks for the user
to fill in. These are decisions where the user's domain knowledge / taste
matters more than the implementation's:

1. **`app/normalization.py:normalize()` + `ALIASES`** — the exact rules
   for converting cleaned LLM names into cache lookup keys. Determines
   cache hit rate. v1 should generally remove marketing/quality adjectives
   that do not materially change shelf life (`organic`, `fresh`, `large`)
   and preserve form/state words that do (`frozen`, `cut`, `sliced`,
   `cooked`, `raw`), with tests for the user's preferred edge cases.
2. **`app/llm.py:SYSTEM_PROMPT`** — the shelf-life examples block, tuned
   to the user's actual kitchen (banana ripeness preference, freezer
   habits, etc.).
3. **`app/shelf_life_defaults.py`** — conservative fallback shelf-life
   values used by manual `/add` when no cache entry or explicit hint exists.
   Lookup order is exact normalized-name default, then conservative category
   default, then a user-facing prompt to add a `7d` hint or use `/correct`.
   Defaults may return both days and optional category; v1 does not include
   a separate category parser for manual text.
4. **`app/renderer.py:render_digest()`** — the digest template wording,
   emoji, and grouping headers. Read every morning by the user.
5. **`app/bot.py` `/list` filter dispatcher** — the exact filter tokens
   (`week`, `expired`, plus categories) and behaviour ordering.

Each TODO block ships with a clear signature, surrounding context,
unit-test scaffolding, and 2–3 representative test inputs already written
so the user knows when they're done.

## 11. v1.5 triggers

Specific, measurable conditions under which additional work is justified
post-launch. Listed so we know *not* to build them now.

| Trigger | Add |
|---|---|
| Cache hit rate stays < 70 % after 50 real receipts (`/stats`) | Smarter normalization: LLM-assisted merge on miss, or embedding similarity (whichever measurement suggests) |
| User finds themselves typing `/add` more than scanning photos | Text-ingest via LLM (reuse the extraction pipeline minus the image) |
| User wants household sharing | Multi-user spec: groups, per-group digests, conflict resolution for overlapping pantry items |
| User wants recipes from expiring items + goal-driven shopping list | Build the **Recipe RAG + shopping-list** subsystem described in §11.1. Likely a separate service consuming the pantry DB read-only. |
| User wants reminders outside Telegram | Notification-channel abstraction; pick one of email / WhatsApp / SignalCli to start |
| User receives many grocery receipts by email (Instacart, Amazon Fresh, Walmart+, etc.) | Build the **Gmail auto-ingest connector** described in §11.2. Reuses the existing extraction pipeline; mostly capture + auth glue. |

### 11.1 Recipe RAG + goal-driven shopping list (v2 sketch)

Triggered when the user wants the pantry to drive *action* (what to cook,
what to buy), not just send reminders. Three coupled features that share
data and only make sense together — splitting them into separate v1.5
deltas would produce three half-features.

#### 11.1.1 Recipe corpus + RAG index

- **Corpus:** a recipe collection — either a curated personal cookbook
  imported as JSON/Markdown, or a scraped + cleaned public dataset
  (e.g. Recipe1M+, Food.com export). Each recipe is structured as
  `{title, ingredients: [{name, qty, unit, optional}], steps,
  tags: [cuisine, diet, time], approx_nutrition}`.
- **Vector store:** embeddings of `title + ingredient names + tags`
  indexed in a local store (sqlite-vec for the "stay-on-the-SQLite-file"
  philosophy, or a hosted store like Turso/Pinecone if scale demands).
- **Embedding model:** re-use whichever model the cache-hit decision
  lands on (§11 first row) so we run one embedder, not two.
- **Retrieval query:** "given my current pantry (weighted toward
  soonest-to-expire) + my preference profile, return top-K recipes I
  can mostly make." Re-ranking pass scores by `(pantry_coverage_ratio,
  expiring_item_use_count, preference_match)`.

#### 11.1.2 User preferences (new table)

```
UserPreference(user_id, liked_cuisines, disliked_ingredients,
               dietary_restrictions, target_metric, max_cook_time_minutes,
               updated_at)
```

- `target_metric` is the user-defined axis: `"healthier" | "faster" |
  "tastier" | "cheaper"` or a free-text descriptor the LLM interprets.
- Built up via `/like`, `/dislike`, `/prefer <text>` commands, plus
  implicit signal: every recipe selected as a goal nudges the profile.

#### 11.1.3 Recipe-as-goal + shopping list flow

```
/cook
   └─► bot shows ranked recipes:
          "Have 7/9 — missing eggs, parsley → Pasta Carbonara"
          "Have 5/6 — missing thyme         → Roast Chicken"
          [pick a recipe]
              │
              ▼
       diff(pantry, recipe.requirements) → missing items
              │
              ▼
       bot replies with toggleable buttons:
          [ ] eggs ×2
          [ ] parsley ×1
          [✓ Add selected to list]   [Cancel]
              │
              ▼
       chosen items → ShoppingListItem rows
              │
              ▼
       /shop renders the current shopping list,
            grouped by category, oldest-added first
```

**On next receipt scan:** `ingest_service` auto-fulfills shopping list
rows when a matching `normalized_name` shows up. This closes a satisfying
loop — buy what you said you'd buy and the list cleans itself.

#### 11.1.4 New tables when this lands

| Table | Purpose |
|---|---|
| `Recipe` | Recipe metadata (the corpus); may be paired with an external vector store for embeddings |
| `UserPreference` | Per-user dietary/cuisine profile + chosen target metric |
| `ShoppingListItem` | Pending items the user wants to buy: `(user_id, name, normalized_name, qty, unit, source_recipe_id, status: pending|fulfilled|removed, added_at, fulfilled_at)` |

#### 11.1.5 What's still out of scope even in this v2

- **Multi-agent orchestration of recipe ranking.** One LLM call with
  structured output, fed (pantry, preferences, retrieved candidates),
  can rank across the target metric just fine. Multi-agent revisited
  only if a single-call ranker provably can't represent the user's
  metric — same "measure before optimizing" stance as the cache.
- **Auto-grocery-order integration** (Instacart, etc.). The list lives
  in the bot; the buying happens elsewhere.
- **Pantry deductions per recipe cook.** Marking a recipe as "made" and
  auto-decrementing pantry rows is tempting but it's a new
  consumption-tracking subsystem. Defer until the basic shopping-list
  loop has been used for a few weeks.

### 11.2 Gmail auto-ingest connector (v1.5/v2 sketch)

Online grocery orders (Instacart, Amazon Fresh, Walmart+, Whole Foods,
HelloFresh, …) arrive as emails — no photo needed, no manual scan.
A Gmail connector pulls those receipts automatically, runs them through
the **existing LLM extraction pipeline** with text-or-HTML input instead
of image input, and writes `PantryItem` rows without the user ever
opening Telegram. This closes the last manual capture step for anyone
who shops online.

This is positioned as v1.5 (not v2) specifically because most of the
work is auth + capture glue — the extraction pipeline already exists.

#### 11.2.1 Connection model

Two real options. Pick **poll** first; upgrade only if latency matters.

| Option | How it works | Trade-offs |
|---|---|---|
| **Poll (recommended)** | Cron job every 15–30 min in the existing APScheduler. List new Gmail messages since the last seen `historyId`, fetch, ingest. | No public webhook needed. No GCP Pub/Sub. Same long-running process. ~15-min latency, fine for groceries. |
| **Push** | Gmail `watch` + Google Cloud Pub/Sub → webhook endpoint on the bot service → fetch + ingest. | Near-real-time. Requires public HTTPS endpoint (adds a tiny FastAPI route to `bin/run.py`) and one-time GCP setup. Worth it only if low latency is a felt need. |

#### 11.2.2 Filtering — which emails count as receipts?

Three layers, cheapest first:

1. **Sender whitelist (per-user):** maintained via `/email_add_sender
   receipts@instacart.com`. Free, deterministic, hits 90 % of real cases.
2. **Label-based:** user applies a `food-manager-ingest` Gmail label;
   connector processes only labelled threads. Zero ambiguity, requires
   user discipline.
3. **LLM classifier (fallback, opt-in):** for unrecognized senders, a
   single cheap classification call asks "is this a grocery receipt?"
   Small per-email cost; gated behind a user-toggleable flag so the
   classifier doesn't quietly accrue spend.

#### 11.2.3 Auth + scopes

- OAuth2 via Google. `gmail.readonly` scope only — never modify mail.
- **Refresh token stored encrypted at rest** in DB (column-level
  encryption keyed off an env-var-supplied secret). Plain refresh
  tokens in SQLite are a no.
- `/connect_gmail` triggers the OAuth flow; bot replies with a one-time
  link to a hosted consent page (a small FastAPI route bolted onto
  `bin/run.py` for the OAuth callback).
- `/disconnect_gmail` purges the integration row and revokes the token
  via Google's revoke endpoint.

#### 11.2.4 Ingest pipeline reuse

```
gmail_connector tick (poll, ~15 min)
   │
   ▼
list_new_messages(user, since=last_history_id)
   │
   ▼  for each candidate:
classify(sender, subject, label) → is_receipt?
   │
   ▼ yes
extract_body(message) → text/plain or sanitized HTML
   │
   ▼
ingest_service.ingest_text(user, body, source="email",
                           source_id=msg_id, dedupe_key=msg_id)
   │
   ▼
same LLM extraction → ParseResult → cache → PantryItem rows
   │
   ▼
update last_history_id; record msg_id in seen-set
```

**Key win:** `ingest_service.ingest_text()` is the same function used by
the `/add` text-ingest path and (later) by voice transcription. One
extraction pipeline, many input modalities. **Adding a new modality
means wiring its capture, not its parsing.** The case for this
modularity in v1 pays off here.

**De-duplication:** `dedupe_key=msg_id` ensures a re-processed email
doesn't double-insert items. Same goes for the (`source`, `source_id`)
composite — useful if a confirmation email and a separate receipt email
both describe the same order; user can manually merge or we LLM-merge
on detection.

#### 11.2.5 New table

```
UserEmailIntegration(
    user_id            FK → User,
    provider           Literal["gmail"],
    encrypted_refresh_token  bytes,
    last_history_id    str | None,
    last_polled_at     datetime | None,
    enabled            bool = True,
    sender_whitelist   JSON   # list[str]
    label_filter       str | None,
    llm_classifier_enabled bool = False,
    connected_at       datetime,
)
```

Plus a small addition to `Receipt`: optional `source: Literal["photo",
"email", "text"] = "photo"` and `source_id: str | None` (the Gmail
msg_id when relevant) so `/stats` can break down ingest paths.

#### 11.2.6 Out of scope for the Gmail connector itself

- **Other email providers** (Outlook, Yahoo, ProtonMail). Same shape,
  different auth — separate spec per provider when there's demand.
- **Two-way Gmail** (sending email digests, auto-labelling processed
  messages). Read-only stays read-only.
- **Calendar integration** ("you're cooking on Saturday — buy by
  Friday"). Different product surface; defer.
- **Receipt PDF attachments**. Some retailers attach a PDF rather than
  inline a body. PDF → text is doable but adds a parsing dependency;
  defer until measured demand.

## 12. Risks and open questions

| Risk | Mitigation |
|---|---|
| Vision LLM cost runs higher than expected | `/stats` exposes per-day spend; alert-by-eyeball; cap by adding a daily budget check before LLM calls if needed |
| Anthropic model deprecation breaks JSON shape | Weekly golden-receipt eval catches regressions before user sees them |
| Railway free tier no longer enough | Fly.io or Render are interchangeable; Dockerfile + `bin/run.py` are platform-agnostic |
| SQLite corruption on volume failure | Daily B2 backups (§9.3); restore = `rclone copy` + restart |
| Telegram rate limits on large receipts (many buttons) | 20-item truncation rule in §7.3 keeps us well under |

## 13. Definition of done for v1

- Bot deployed to Railway, responds to `/start`.
- Sending a real receipt photo produces correct `PantryItem` rows and a
  bot reply within 15 s.
- Daily digest fires at the configured local hour, shows correct buckets,
  buttons update the message in place.
- `/correct` writes a `user_correction` row that overrides future LLM
  estimates for that item.
- All listed commands work and are documented in `/help`.
- Test suite passes; golden-receipt eval has been run at least once.
- README explains: setup, deploy, daily-use commands, backup/restore.

---

**End of design spec.** Next step: implementation plan via the
`superpowers:writing-plans` skill, derived from this document.
