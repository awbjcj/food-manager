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
- Daily digest at user-configurable local hour, with `[Ate / Tossed / +2d]`
  inline buttons that edit the digest message in place.
- Slash commands: `/start`, `/tz`, `/digest_at`, `/list [filter]`, `/add`,
  `/correct`, `/stats`, `/help`.
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
class User(SQLModel, table=True):
    telegram_id: int = Field(primary_key=True)   # also our auth
    chat_id: int                                 # where digests get sent
    tz: str = "Asia/Singapore"                   # IANA name; for digest local time
    digest_hour: int = 8                         # 0–23
    created_at: datetime

class PantryItem(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.telegram_id", index=True)
    raw_name: str                                # what the LLM said: "Whole Milk 1 gal"
    normalized_name: str = Field(index=True)     # "whole milk"
    category: str | None = Field(default=None, index=True)  # for /list <category>
    qty: float = 1.0
    unit: str | None = None                      # "gal", "lb", "ct", None
    purchased_on: date                           # from receipt or scan day
    expires_on: date = Field(index=True)         # purchased_on + days
    status: Literal["active","eaten","tossed","snoozed"] = "active"
    snoozed_until: date | None = None
    source_receipt_id: int | None = Field(default=None, foreign_key="receipt.id")
    created_at: datetime

class Receipt(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.telegram_id", index=True)
    photo_file_id: str            # Telegram file_id — re-fetch on demand, no blob in DB
    scanned_at: datetime
    llm_cost_cents: int | None    # for /stats

class ShelfLifeCache(SQLModel, table=True):
    normalized_name: str = Field(primary_key=True)
    days: int
    category: str | None = None
    confidence: float                                # LLM-reported 0..1
    learned_at: datetime
    source: Literal["llm","user_correction"] = "llm"
```

### Design notes

- **No `Food` master table.** `ShelfLifeCache` IS the growing knowledge
  base. Promote to a typed taxonomy only if v2 demands it.
- **`status` enum + `snoozed_until`** lets the daily digest query be one
  clean SELECT. Eaten/tossed rows are retained for stats (waste rate).
- **`photo_file_id`, not bytes.** Telegram retains the photo; re-fetch by
  ID if ever needed.
- **`source: "user_correction"`** outranks `"llm"` in `cache.get()`. This
  is the entire learning loop — five lines of code, no ML.
- **`category` denormalized onto `PantryItem`** (not joined via cache) so
  `/list dairy` is a single indexed query.

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

For each line item:
  - name: clean human-readable name ("Whole Milk 1 gal"), expand abbreviations
  - qty:  numeric quantity (1.0 if ambiguous)
  - unit: "gal"|"lb"|"oz"|"ct"|"bunch"|"each"|null
  - category: "dairy"|"produce"|"meat"|"seafood"|"bakery"|"pantry"|"frozen"|"other"
  - est_shelf_life_days: refrigerated shelf life from purchase date.
      Use conservative estimates. Examples:
        whole milk = 7, fresh chicken = 2, bananas = 5,
        canned beans = 365, fresh bread = 4, eggs = 28
  - confidence: 0.0–1.0 how sure you are this is a food item
"""
```

The `est_shelf_life_days` figures and any kitchen-specific calibrations
are owned by the user — they are a TODO marker in `llm.py` for the user
to tune (see §10).

Pydantic models for the response:

```python
class ParsedItem(BaseModel):
    name: str
    qty: float = 1.0
    unit: str | None = None
    category: str | None = None
    est_shelf_life_days: int
    confidence: float

class ParseResult(BaseModel):
    items: list[ParsedItem]
```

### 6.2 Normalization + cache

```python
def compute_days(parsed: ParsedItem) -> int:
    norm = normalize(parsed.name)
    cached = cache.get(norm)
    if cached and cached.source == "user_correction":
        return cached.days                    # user overrides always win
    if cached:
        return cached.days                    # prior LLM estimate
    cache.put(norm, parsed.est_shelf_life_days,
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

### 6.3 Error handling

| Failure | Response |
|---|---|
| Anthropic 4xx/5xx or timeout | Retry 2× with exponential backoff; on final fail, bot replies "couldn't read that one, try a clearer photo or `/add <items>` manually" |
| LLM returns malformed JSON | One retry with corrective system message; on second fail, same user-facing message |
| LLM returns `confidence < 0.3` items | Drop silently from inserts; bot reply notes "skipped N unclear items" |

No circuit breakers, no dead-letter queue. Single user.

### 6.4 Bot reply after a successful ingest

```
✅ Logged 8 items from this receipt:
   • Whole Milk 1 gal — exp May 31 (7d)
   • Bananas ×6 — exp May 27 (3d)
   • ... (6 more)
Cost: $0.018
```

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

```sql
SELECT * FROM pantryitem
WHERE user_id = ?
  AND status = 'active'
  AND (snoozed_until IS NULL OR snoozed_until <= DATE('now'))
  AND expires_on <= DATE('now', '+7 days')
ORDER BY expires_on ASC;
```

Bucketed by the renderer into `expired`, `today`, `tomorrow`, `this_week`.
Empty result ⇒ no message is sent (silent days).

### 7.3 Digest message

```
🍅 Pantry digest — Tue May 27

❗ Expired (1)
   • Spinach (yesterday)

🔥 Today (2)
   • Whole Milk 1 gal
   • Bananas (×6)

📅 Tomorrow (1)
   • Sliced Bread

📆 This week (3)
   • Greek Yogurt — Fri
   • Chicken Thighs — Sat
   • Strawberries — Sun
```

One row of inline buttons per item, callback data `act:{verb}:{item_id}`:

```
[✓ Ate]  [🗑 Tossed]  [⏰ +2d]
```

If item count exceeds 20, truncate and append `[show all]` button that
sends a paged follow-up.

On button tap:
1. Service applies the mutation (status / snoozed_until).
2. Renderer rebuilds the message body.
3. Handler calls `bot.edit_message_text()` — same message, updated. No
   chat-spam from confirmations.

The exact wording, emoji, and grouping headers in the digest are a TODO
marker for the user (see §10).

### 7.4 Commands

| Command | Behaviour |
|---|---|
| `/start` | Creates User row, captures `chat_id`, prompts for `/tz` |
| `/tz Asia/Singapore` | Sets timezone (IANA); reschedules digest job |
| `/digest_at 8` | Sets digest hour 0–23; reschedules |
| `/list` | All active items, sorted by `expires_on` |
| `/list <category>` | Filter by category (`dairy`, `produce`, …) |
| `/list week` | Items expiring in next 7 days |
| `/list expired` | Items past expiry, still active |
| `/add 2 lb chicken, dozen eggs` | Manual text-ingest, regex parser; falls back to telling user to send a photo if it can't parse |
| `/correct <item_id> <days>` | Adjusts item's `expires_on` AND writes `ShelfLifeCache` row with `source="user_correction"` |
| `/stats` | Last-30-day: receipt count, item count, cache-hit %, total LLM spend cents |
| `/help` | Lists the above |

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
│   ├── renderer.py
│   ├── cache.py
│   ├── db.py
│   └── models.py
├── tests/
├── bin/
│   ├── run.py              # entry point: starts bot + scheduler in one loop
│   └── eval_receipts.py
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

The implementation will scaffold four files with TODO blocks for the user
to fill in. These are decisions where the user's domain knowledge / taste
matters more than the implementation's:

1. **`app/normalization.py:normalize()` + `ALIASES`** — the exact rules
   for converting cleaned LLM names into cache lookup keys. Determines
   cache hit rate.
2. **`app/llm.py:SYSTEM_PROMPT`** — the shelf-life examples block, tuned
   to the user's actual kitchen (banana ripeness preference, freezer
   habits, etc.).
3. **`app/renderer.py:render_digest()`** — the digest template wording,
   emoji, and grouping headers. Read every morning by the user.
4. **`app/bot.py` `/list` filter dispatcher** — the exact filter tokens
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
