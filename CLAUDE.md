# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_core_services.py

# Run a single test by name
uv run pytest tests/test_core_services.py::test_shelf_life_defaults

# Apply database migrations
DATABASE_PATH=./food.db uv run alembic upgrade head

# Start the bot
uv run python bin/run.py
```

## Architecture

Single-user Telegram bot: user sends grocery receipt photos → Claude parses them → pantry items are tracked with expiry dates → daily digest message is sent each morning.

### Data flow

1. **Photo ingest**: `bot.py` receives a photo message → downloads bytes → calls `ingest_service.ingest_photo()` → calls `LLMClient.extract_items_from_image()` → creates `PantryItem` rows
2. **Shelf life resolution** (inside ingest): `compute_shelf_life()` checks `ShelfLifeCache` first, falls back to LLM's estimate. High-confidence results are written back to cache.
3. **Daily digest**: APScheduler cron job (per-user, stored in memory) calls `send_digest_once()` → `list_digest_due()` → renders message with inline keyboard buttons

### Layer responsibilities

| Module | Responsibility |
|---|---|
| `app/models.py` | SQLModel ORM: `User`, `Receipt`, `PantryItem`, `ShelfLifeCache` |
| `app/settings.py` | `pydantic-settings` env-var loading (aliases like `TELEGRAM_BOT_TOKEN`) |
| `app/db.py` | Engine + session factory creation |
| `app/providers.py` | Canonical `Provider` type, `PROVIDER_CAPABILITIES`/`supports()`, `LLMProviderNotConfigured`, and the generic `ProviderSelector` base (with fallback) reused by every seam |
| `app/llm.py` | `LLMClient` Protocol + Anthropic/OpenAI clients; capability selectors (subclass `ProviderSelector`); `ParsedItem`/`LLMResult` models |
| `app/gemini_llm.py` | All Google Gemini clients (native `google-genai`): image, text, profile, cook, translation, search |
| `app/deepseek_llm.py` | DeepSeek text-only clients (OpenAI-compatible `chat.completions`): text, profile, selection, nutrition, translation |
| `app/translation_llm.py` | `TranslationLLMClient` Protocol + Anthropic/OpenAI clients + selector (translates dynamic names) |
| `app/translation_service.py` | `translate_texts()`: lazy LLM translate + `NameTranslation` cache, English fallback |
| `app/i18n.py` | Static `MESSAGES` catalog, `t(key, lang, **kw)`, locale date/weekday helpers |
| `app/ingest_service.py` | Photo and `/add` text → `PantryItem` rows; `IngestSummary` dataclass |
| `app/pantry_service.py` | CRUD: list, snooze, mark eaten/tossed/removed, correct shelf life |
| `app/household_service.py` | Provision/restore a user's household-of-one |
| `app/invite_service.py` | Single-use household invites + membership (join/leave/remove) |
| `app/cache.py` | `ShelfLifeCache` read/write/user-correction |
| `app/commands.py` | Parse raw Telegram command argument strings into typed values |
| `app/renderer.py` | Format `PantryItem` lists into Telegram message text + inline keyboards |
| `app/bot.py` | aiogram handlers; `AuthDecision` / `authorize_and_get_user`; `build_dispatcher` |
| `app/scheduler.py` | APScheduler job registration; `schedule_user_digest`, `send_digest_once` |
| `app/backup.py` | SQLite `.backup-TIMESTAMP` rotation before migrations |
| `app/normalization.py` | Food name normalization (lowercasing, alias mapping) |
| `app/shelf_life_defaults.py` | Hardcoded shelf-life fallback table |
| `app/pending_service.py` | `PendingCorrection` lifecycle shared by `/correct` and `/add`: create, apply, cancel, 10-min TTL expiry |
| `app/correction_service.py` | Parses a `/correct` free-text diff against an item and applies it (cache update, back-computed days, name translation) |
| `app/refine_service.py` | Post-ingest web-search refine pass over a fresh receipt's low-confidence items |
| `app/shelf_life_search.py` | `ShelfLifeSearchClient` Protocol + `ShelfLifeSearchResult` + `resolve_search_days` — the shared web-search contract consumed by correction, ingest, refine, and frozen/fridge resolution |
| `app/storage_state.py` | Storage State axis (`default → fridge → frozen`, forward-only) + `shelf_life_origin`/`compute_expiry` shared formula |
| `app/frozen_shelf_life.py` | `resolve_storage_days` (generic, parameterised by Storage State; `resolve_frozen_days` is a frozen-only wrapper): cache → vendored USDA FoodKeeper table → web search → default fallback |
| `app/profile_service.py` | `FoodProfile` household food-preference model (diet, exclusions, cuisines, cook-time cap) read from/written to `Household` |
| `app/shopping_service.py` | Shopping list CRUD: add missing `/cook` ingredients, list, mark bought |
| `app/llm_transport.py` | `with_transport_retry`: shared retry/backoff policy for every provider network call |
| `app/callback_dispatch.py` | Callback-query seam: ack-first, then edit-in-place or resend-as-new-message so a button tap never dead-ends |
| `app/progress.py` | Progress-ack seam for slow commands: `start_progress` / `finish_progress` (edit ack into result) / `clear_progress` |
| `app/alerts.py` | `OwnerAlerter`: rate-limited operational alert DMs to the bootstrap owner |
| `app/resilience.py` | `run_with_restart`: exponential-backoff restart loop around polling |
| `app/nl_intent.py` | Agno intent seam (v5.1): `NLIntent` schema, per-provider `AgnoIntentAgent`s built at bootstrap, `IntentAgentSelector` (no fallback), `match_items` |
| `app/week_composer.py` | Agno meal-plan seam (v5.2): `DaySpec`/`WeekPlanSpec`, per-provider `AgnoWeekComposer`s built at bootstrap, `WeekComposerSelector` (no fallback), pure `heuristic_compose` fallback |
| `app/plan_service.py` | Meal-plan orchestrator (v5.2): `build_plan` (sequential pantry allocation + composer/heuristic), `swap_day`, `aggregate_shopping`, `cancel_active_plans` |
| `app/cook/*` | Recipe engine: `models.py` (Purpose/Effort/`RecipeCriteria`/`ScoredCandidate`), `recipe_source.py` (Spoonacular/TheMealDB real-source building blocks, v4.9, not yet wired in), `llm.py` (selection/recipe/nutrition clients), `logic.py` (scoring, shopping-list diff), `service.py` (live LLM-only pipeline), `session_service.py` (`CookSession` cost/state), `favorites_service.py` (`SavedRecipe`), `feedback.py` (liked/disliked signal) |
| `bin/run.py` | Entry point: loads settings, runs migrations, starts scheduler + polling |

### Key design conventions

- **Session injection**: All service functions receive `session: Session` as the first argument. No global state.
- **Explicit `today: date`**: Service functions never call `datetime.now()` internally — callers pass `today`. This makes tests deterministic without mocking.
- **LLMClient is a Protocol**: `FakeLLMClient` in `tests/fakes.py` satisfies the protocol via duck typing for unit tests.
- **`Settings()` instantiation**: Pydantic-settings loads from env vars via field aliases. Call sites in tests need `# type: ignore[call-arg]` because Pylance can't infer env-var loading.
- **`PantryItem.id` is `Optional[int]`**: Auto-populated by SQLite after commit. Assert `is not None` before passing to service functions in tests.
- **Scheduler `send` callback must be async**: `schedule_user_digest` and `register_all_user_digests` accept `Callable[..., Awaitable[None]]` — use `AsyncMock()` in tests, not sync lambdas.

### Internationalization (v4.1)

Per-user language (`User.lang`, one of `en|zh|fr|es`; `/lang` to set). **The DB always stores canonical English** — language is a render-time concern, so `normalized_name`, `ShelfLifeCache`, and dedup keep operating on stable English keys and a language switch is just a re-render.

- **Two-phase render**: renderers stay pure/synchronous and take `lang="en"` + a pre-resolved `names: Mapping[str,str]` map. Handlers/scheduler do the async work first — read `user.lang`, call `_translate_for_render` / `translate_texts` to resolve dynamic names — then call the sync renderer. Never call the async translator from inside a renderer.
- **Static chrome** (headers, buttons, `/help`) → `app/i18n.py` `MESSAGES` catalog via `t(key, lang, **kw)`, English fallback for any missing variant.
- **Dynamic names** (item names, recipe title/cuisine/method) → `translate_texts()` lazily LLM-translates and caches in the global `NameTranslation` table; any failure falls back to English so the daily digest is never blocked.
- **English must stay byte-identical**: every renderer defaults `lang="en"`, and existing tests assert exact English strings. When adding a catalog key, its `en` value must equal the literal it replaces; the integrity test in `tests/test_i18n.py` enforces placeholder parity across languages.

### Multi-user households (v4.2)

A household can have multiple members who share everything household-scoped (pantry, shopping list, `ShelfLifeCache`, food profile) automatically — sharing is a consequence of every domain row being keyed by `household_id`, so no per-feature work is needed. Per-user settings (`lang`, `tz`, `digest_hour`, `llm_provider`, digest job) stay on `User`.

- **Single authorization gate**: `resolve_authorization()` in `bot.py` is the one membership check, used by `authorize_and_get_user` (commands) and `_authorized_callback_user` (callback queries). A Telegram id is allowed iff it has a `User` row (member of some household) or equals the bootstrap `ALLOWED_TELEGRAM_USER_ID` on first contact. Everyone else is rejected; the only other way in is redeeming an invite.
- **Roles** (`User.role`): `owner` (household creator; backfilled for pre-v4.2 users) and `member`. Any member may `/invite`; only the owner may `/remove` members; the owner cannot `/leave`.
- **Invites** (`HouseholdInvite`, `invite_service.py`): 24h TTL, `secrets.token_urlsafe` token. `max_uses` controls redemptions — `1` (default, `/invite`) is single-use; `None` (`/invite family`) is reusable until expiry for onboarding several people at once; `uses` counts redemptions. `/invite` yields both a `t.me/<bot>?start=<token>` deep-link and a raw code for `/join <code>`; both route through `_try_redeem_invite`. Leaving/removal deletes **all** the user's invites (a multi-use link stays live after its first redemption, so filtering on `redeemed_by` would miss it). `max_uses` has no DB `server_default` on purpose — one would coerce app-inserted `NULL` (unlimited) back to `1`; existing rows are backfilled via `UPDATE` in migration `0011`.
- **Join notifications**: on a successful redeem, `_notify_household_join` best-effort DMs every existing member (in their own language) that someone joined; failures are swallowed so a blocked chat never breaks the join.
- A removed/left user's `User` row is deleted (deauthorized) and their digest job is cancelled via the `unschedule` callback wired in `bin/run.py`.

### Storage states: default → fridge → frozen (v4.6)

`PantryItem.storage` (`default | fridge | frozen`) is a storage axis orthogonal
to `category`: `category` is what the food is, and `storage` is how it is kept
for shelf-life purposes. Transitions are one-way forward only
(`default → fridge → frozen`; `frozen` is terminal) — see `app/storage_state.py`
for the transition graph and the one shared formula: expiry is
`shelf_life_origin(item) + shelf_life_days`, where `shelf_life_origin` is
`stored_on` once the item has entered a non-default state, else `purchased_on`.
`stored_on` is set when an item enters `fridge` or `frozen` (purchase date for
LLM-flagged frozen buys, today for a `🧊 Fridge` / `❄️ Freeze` tap) and becomes
the new Shelf-Life Origin — moving `default → fridge` and later `fridge →
frozen` each reset it. Fridge/frozen durations both come from
`app/frozen_shelf_life.py`, one resolver parameterised by Storage State: cache
-> vendored USDA FoodKeeper table -> `ShelfLifeSearchClient` queried as
`"frozen <food>"`/`"fridge <food>"` -> a cached default (90d frozen, 7d fridge).
Frozen items are excluded from the post-ingest fresh web-search refine path and,
with their long expiries, fall out of the 7-day digest window automatically.

### Interactive pantry management (v4.8)

`/pantry [digest|<id>]` renders a stateful view from stateless callback data —
`digest` (items due within 7 days), no-arg (full active list), or a numeric
`<id>` (single item card with action buttons: Correct, Remove, Freeze, Fridge,
back-to-list). Every button tap re-derives its target view from the callback
payload rather than server-side session state, so a card stays actionable even
across bot restarts. `app/callback_dispatch.py` is the shared seam every button
handler goes through: acknowledge the callback first (Telegram callback tokens
expire quickly), then edit the message in place, falling back to sending a
fresh message if the edit fails for any reason other than "not modified" (which
is treated as success, since it means the view didn't need to change).

### Recipe engine: `/cook`, `/shopping`, `/favorites` (v3.5, v4.9 in progress)

The live `/cook` pipeline (`app/cook/service.py::run_cook`, called from
`run_cook_and_render` in `bot.py`) is LLM-only, in three metered steps against
the household's active pantry and `FoodProfile` (`app/profile_service.py`):
`selection_llm` picks which pantry items to use, `recipe_llm` turns those into
candidate recipes (regenerating once if every candidate violates a profile
exclusion), then `nutrition_llm` scores each one. Each step's cost accrues onto
the `CookSession` row (`app/cook/session_service.py`) and the pipeline bails
early once `COOK_COST_CEILING_MICROS` is exceeded (raise it if recipes come
back empty). Final ranking is `blended_score` (`app/cook/logic.py`): nutrition
health score + expiry utilization (how much of the pantry's soon-to-expire
stock the recipe uses) + source `deliciousness`; `shopping_list` is the
ingredient gap versus the pantry. Result cards offer "Show alternatives" (the
next-ranked candidate from the same run) plus ★ Save
(`app/cook/favorites_service.py` → `SavedRecipe`, re-cookable against the
current pantry via `/favorites`) and ➕ Shopping list (`app/shopping_service.py`
→ `/shopping`, tap an item once bought). 👍/👎 feedback (`app/cook/feedback.py`)
records a `(cuisine, ingredients, verdict)` signal per session for future
affinity-weighted scoring — not yet consumed by `blended_score`.

**v4.9 (in progress, not yet wired in):** `app/cook/recipe_source.py` has the
building blocks for a real-source alternative to the LLM-only pipeline —
`RecipeCriteria`/`Purpose` (use-it-up, quick, healthy, comfort, surprise),
a Spoonacular `RecipeSource` (`SPOONACULAR_API_KEY`), and TheMealDB fetch
helpers — but `run_cook` does not call into it yet; see `tests/test_recipe_source.py`
for the parts already covered in isolation.

### Multi-provider LLM routing (v4.7)

Four providers are selectable per user (`User.llm_provider`, set via `/llm`):
`anthropic`, `openai`, `gemini`, `deepseek`. The column is a plain `str` (no DB
migration needed); `app/providers.py` is the single source of truth for the
`Provider` type and the capability matrix.

- **Capabilities differ per provider.** `anthropic`/`openai`/`gemini` are full
  providers (image + web search + text); **DeepSeek's API is text-only** (no
  image input, no API-level web-search tool). `PROVIDER_CAPABILITIES` /
  `supports(provider, capability)` encode this.
- **One generic selector.** Every seam (image, text, profile, cook
  selection/recipe/nutrition, translation, search) is a thin subclass of
  `ProviderSelector` mapping `provider -> client`. `for_provider(name)` returns
  the client; with `fallback=True` a missing provider routes to a capable one
  (the seed default if capable, else the first available, logged as
  `llm_provider_fallback`). Fallback is **on** for image/search/recipe (a
  provider may legitimately lack them) and **off** for text seams (the user's
  choice is always honoured).
- **Selectability floor is text.** `_available_llm_providers` (bot.py) lists
  providers that can serve the *text* tasks, so DeepSeek is selectable even
  though it can't read photos; `/llm` status flags text-only providers.
- **Seed defaults must be capable.** `bin/run.py::_capable_default` ensures an
  image/search/recipe selector is seeded with a provider that actually has the
  capability (a DeepSeek global default falls back to gemini/anthropic). Settings
  validation refuses a text-only default unless an image-capable key is set.
- **Web search is now per-user.** It used to be hardwired to Anthropic; it is a
  `SearchProviderSelector` resolved via `_select_search(search, user.llm_provider)`
  at the ingest/`/add`/freeze call sites. Only anthropic + gemini have a
  `ShelfLifeSearchClient`; others fall back.
- **Provider client cohesion.** Anthropic/OpenAI clients stay split by capability
  (`llm.py`, `cook/llm.py`, `translation_llm.py`), but each new provider's
  clients live in one module (`gemini_llm.py`, `deepseek_llm.py`) because their
  SDK shapes differ enough to be worth centralizing. DeepSeek reuses the
  Anthropic text client's "ask for JSON, validate, repair once" loop over
  `chat.completions`; Gemini uses structured output, except the search/recipe
  paths which use Google Search grounding and parse JSON from text (grounding and
  structured-output mode are mutually exclusive in the SDK).

### Stability & feedback (v5.0)

Slow commands (photo ingest, `/add`) ack immediately via `app/progress.py` and
edit the ack into the final reply; `/cook` already had its own "Thinking..."
state. Failures are loud: an aiogram errors observer and the digest retry's
`on_final_failure` hook DM the owner through `OwnerAlerter` (rate-limited, best
effort). `User.last_digest_date` records completed digest runs (silent days
included) so `catch_up_missed_digests` at startup sends a missed digest late
instead of never. Polling is wrapped in `run_with_restart` (backoff, reset
after stable runs); `docs/operations.md` documents outer supervision. All
provider calls log `*_timing` (duration_ms, attempts) via `with_transport_retry`.

### Natural-language input & onboarding (v5.1)

Plain text (non-command, non-reply) routes to `handle_nl_message`: an Agno
agent (`app/nl_intent.py`, classify-only, stateless, per-provider like the
text seams) maps the message to a typed `NLIntent`, and the handler dispatches
to existing services — add → the `/add` pending flow via `_run_add_flow`,
unambiguous marks apply directly, ambiguous marks show an `item:open` picker,
shelf-life questions answer from cache → defaults → web search, pantry queries
reuse the digest render. Agent failure degrades to a help hint; with no
provider configured the catch-all is not registered. `/help` is tiered
(overview + `help:<topic>` drill-down) and `/start` sends a welcome tour.

### Meal planning (v5.2)

`/plan [3-7]` (default 5) builds a `MealPlan` + one `MealPlanEntry` per day via
`app/plan_service.py::build_plan`: gather active pantry → `WeekComposer`
(`app/week_composer.py`, Agno, degrades to pure `heuristic_compose` on any
failure) proposes per-day cuisine/purpose/feature-items → sequential
allocation searches the v4.9 `RecipeSource` chain one day at a time against a
pantry pool that shrinks as earlier days consume ingredients, so expiring
items get cooked first and the aggregated shopping list is correct by
construction. A new `/plan` cancels any existing active plan for the
household (superseded note in the reply). Callbacks are stateless
(`plan:swap:<id>:<day>`, `plan:shop:<id>`, `plan:cancel:<id>`): swap re-searches
one day (paginated, deduped against sibling recipes) and re-renders the whole
plan in place; shop aggregates+dedupes every day's ingredient gap through
`add_missing` (idempotent on repeat taps); cancel is terminal. Cost is capped
by `PLAN_COST_CEILING_MICROS` (mirrors `COOK_COST_CEILING_MICROS`), enforced
before every search via a shared remaining-budget calculation.

### Database

SQLite via SQLModel/SQLAlchemy. Migrations managed by Alembic in `migrations/`. The `DATABASE_PATH` env var (default `./food.db`) is passed to both Alembic and the app engine.
