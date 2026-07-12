---
repo_url: https://github.com/awbjcj/food-manager
repo_name: food-manager
role: sole author
generated_at: 2026-07-11
---

# Project: food-manager

A production Telegram bot that ingests grocery-receipt photos, uses LLMs to
parse items and estimate shelf life, tracks a household pantry with expiry
dates, and sends a daily digest plus AI-driven recipe and meal-planning
features — engineered around a provider-agnostic LLM routing layer.

## Summary

The problem: household groceries expire and get wasted because nobody tracks
purchase-to-expiry timelines. The approach: a Telegram bot where a user photographs
a receipt, a vision-capable LLM extracts line items (`app/llm.py`,
`app/gemini_llm.py`), shelf-life is resolved through a cache → vendored USDA
FoodKeeper table → web-search → default-fallback chain
(`app/frozen_shelf_life.py`), and a scheduled per-user digest surfaces items due
within seven days (`app/scheduler.py`). On top of the pantry sits an AI recipe
engine (`/cook`), a multi-day meal planner (`/plan`), and natural-language input,
all built on **four interchangeable LLM providers** (Anthropic, OpenAI, Google
Gemini, DeepSeek) selected per user through one generic capability-aware selector
(`app/providers.py`). The repository is the work of a **sole author** — 311 of
312 commits (`git shortlog -sne`; the remaining commit is `copilot-swe-agent[bot]`)
— developed intensively over roughly seven weeks (`git log`: 2026-05-26 →
2026-07-10). It is an **actively shipped** project: spec-and-plan documents for
sixteen versioned iterations (v1 → v5.4) live under `docs/superpowers/`, and the
codebase is backed by 586 offline test functions.

## Tech stack (evidence-backed)

- **Python 3.12** — entire codebase; `requires-python = ">=3.12"` in `pyproject.toml`.
- **Anthropic Claude API** (`anthropic` SDK) — receipt/text parse + web-search clients in `app/llm.py`; dependency `anthropic>=0.39` in `pyproject.toml`.
- **OpenAI API** (`openai` SDK) — parallel provider clients in `app/llm.py`, `app/cook/llm.py`; dependency `openai>=2.11`.
- **Google Gemini** (`google-genai` SDK) — all Gemini clients with structured-output mode (`response_schema` / `response_mime_type`) and Google Search grounding in `app/gemini_llm.py`; dependency `google-genai>=1.0`.
- **DeepSeek** (OpenAI-compatible `chat.completions`) — text-only provider clients in `app/deepseek_llm.py`.
- **Agno** (agent framework) — intent classification (`app/nl_intent.py`), meal-plan week composition (`app/week_composer.py`), and taste affinity (`app/cook/affinity.py`); dependency `agno>=2.7.2`.
- **aiogram 3** — async Telegram bot handlers and dispatcher in `app/bot.py`; dependency `aiogram>=3.10,<4.0`.
- **SQLModel / SQLAlchemy** — 13 ORM table models in `app/models.py` (`Household`, `User`, `PantryItem`, `ShelfLifeCache`, `MealPlan`, …).
- **SQLite** — application datastore; `DATABASE_PATH` engine wiring in `app/db.py`, `.backup-TIMESTAMP` rotation in `app/backup.py`.
- **Alembic** — 16 schema migrations under `migrations/versions/`; dependency `alembic>=1.13`.
- **Pydantic v2 / pydantic-settings** — typed LLM result schemas and env-var settings in `app/settings.py`; dependencies `pydantic>=2.7`, `pydantic-settings>=2.4`.
- **APScheduler** — per-user cron digest jobs in `app/scheduler.py`; dependency `apscheduler>=3.10`.
- **httpx** — async transport underlying provider calls; dependency `httpx>=0.28.1`.
- **pytest / pytest-asyncio / pytest-mock / freezegun** — dev test stack in `pyproject.toml` `[dependency-groups]`; `asyncio_mode = "auto"`.
- **Ruff** — linting; dependency `ruff>=0.15.15`.
- **uv** — dependency and environment management; `[tool.uv] managed = true`.

## Architecture highlights

- **Architected a provider-agnostic LLM routing layer** that made adding a new
  model provider a one-line change: `app/providers.py` is the single source of
  truth for the `Provider` type and a `PROVIDER_CAPABILITIES` matrix (4 providers
  × {image, text, search}), consumed by one generic `ProviderSelector` — six
  capability seams subclass it (`grep -c "ProviderSelector)" app` → 6).
- **Engineered a capability-aware fallback policy** so a user's text-only
  provider choice is always honoured while image/search tasks silently reroute to
  a capable provider: `ProviderSelector.for_provider` falls back only when
  `fallback=True` (image/search) and raises `LLMProviderNotConfigured` otherwise
  (text), logging every substitution as `llm_provider_fallback` (`app/providers.py`).
- **Consolidated six copy-pasted retry loops into one transport seam** with
  exponential backoff and a pluggable `classify` hook, letting a single loop serve
  two policies — retry-on-any (receipt/text/translation) vs. retry-only-transport
  (`is_retryable_transport_error`) for the OpenAI cook path (`app/llm_transport.py`).
- **Hardened receipt parsing against unreliable model JSON** with an "ask for
  JSON → validate against a Pydantic schema → repair once" loop reused across the
  Anthropic and DeepSeek text clients, and Gemini structured-output mode
  (`response_schema`) where the SDK supports it (`app/gemini_llm.py`, `app/llm.py`).
- **Enforced a single authorization gate** for a multi-user household model so
  every command and callback shares one membership check: `resolve_authorization()`
  in `app/bot.py` admits a Telegram id iff it has a `User` row or is the bootstrap
  owner, with all other entry gated behind single-use, TTL-bounded invites
  (`app/invite_service.py`).
- **Designed a forward-only storage-state axis** (`default → fridge → frozen`)
  orthogonal to food category, collapsing expiry to one shared formula
  (`shelf_life_origin(item) + shelf_life_days`) and resetting the origin on each
  transition (`app/storage_state.py`, `app/frozen_shelf_life.py`).
- **Built a canonical-English i18n design** across 4 languages (en/zh/fr/es)
  where the DB stores only English keys and language is a pure render-time
  concern: static chrome via a `MESSAGES` catalog (`app/i18n.py`) and dynamic
  names via a lazy LLM-translate-and-cache path that always falls back to English
  so the daily digest is never blocked (`app/translation_service.py`).
- **Instrumented every provider network call** with `*_timing` structured logs
  (duration_ms, attempts) and wrapped polling in an exponential-backoff restart
  supervisor (`run_with_restart`) with rate-limited owner alerts on failure
  (`app/resilience.py`, `app/alerts.py`, `app/llm_transport.py`).
- **Made the interactive pantry UI survive bot restarts** by deriving every
  button view from stateless callback payloads rather than server-side session
  state, routed through an ack-first callback seam that edits in place or resends
  on failure (`app/callback_dispatch.py`, `app/bot.py`).

## Quantified outcomes

- **586 test functions across 59 test files, running fully offline** — `grep -rh "def test_" tests | wc -l` → 586; `find tests -name "*.py" | wc -l` → 59; the LLM is faked via a `FakeLLMClient` protocol implementation (`tests/fakes.py`).
- **~13.3k LOC of application code across 49 modules** (~27.9k LOC total including tests) — `find app -name "*.py" | wc -l` → 49; `find app -name "*.py" | xargs wc -l` → 13,324; total Python `wc -l` → 27,925.
- **4 LLM providers unified behind one selector, spanning 6 capability seams** — `Provider` = `("anthropic","openai","gemini","deepseek")` and `grep -c "ProviderSelector)" app` → 6 (`app/providers.py`).
- **13 database tables under 16 Alembic migrations** — `grep -c "table=True" app/models.py` → 13; `find migrations/versions -name "*.py" | wc -l` → 16.
- **311 of 312 commits authored solo over ~7 weeks** — `git rev-list --count HEAD` → 312; `git shortlog -sne` shows one GitHub identity (`awbjcj`) across 311 commits plus one bot commit; `git log` range 2026-05-26 → 2026-07-10.
- **16 shipped iterations documented** as paired spec + plan files (v1 → v5.4) under `docs/superpowers/specs/` and `docs/superpowers/plans/`.
- **Runtime performance / latency / coverage-percentage:** None evidenced — no benchmark, profiling, or coverage artifact is checked into the repository; omitted rather than estimated.

## Skills demonstrated

- **Languages:** Python (3.12), SQL
- **AI / LLM Engineering:** multi-provider LLM orchestration (Anthropic Claude, OpenAI, Google Gemini, DeepSeek), vision/multimodal receipt parsing, structured output (JSON-schema / Pydantic-validated), retrieval-via-web-search grounding, agent frameworks (Agno) for intent classification & planning, prompt-repair/self-correction loops, provider capability routing & fallback, cost metering and budget ceilings
- **Frameworks:** aiogram (async Telegram bots), Agno, APScheduler, Pydantic v2, pydantic-settings
- **Databases:** SQLite, SQLModel, SQLAlchemy, Alembic (schema migrations)
- **Architecture:** protocol-based seams / dependency injection, capability-matrix provider abstraction, stateless callback-driven UI, forward-only state machines, single-authorization-gate multi-tenancy (household model), render-time internationalization (canonical-English storage), retry/backoff transport layer
- **Testing:** pytest, pytest-asyncio, pytest-mock, freezegun, fully offline test doubles (fake LLM/protocol fakes), deterministic time injection
- **Reliability & Operations:** exponential-backoff restart supervision, structured timing instrumentation, rate-limited operational alerting, database backup rotation, missed-job catch-up
- **Tooling:** uv, Ruff, Git
