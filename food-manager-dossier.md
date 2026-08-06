---
repo_url: https://github.com/awbjcj/food-manager
repo_name: food-manager
role: sole author
generated_at: 2026-08-05
---

# Project: food-manager

A production Telegram bot that ingests grocery-receipt photos, uses LLMs to
parse items and estimate shelf life, tracks a household pantry with expiry
dates, and layers on AI-driven recipes, meal planning, and a metered
subscription business — engineered around a provider-agnostic LLM routing
layer and a stateless, restart-safe interactive UI.

## Summary

The problem: household groceries expire and get wasted because nobody tracks
purchase-to-expiry timelines. The approach: a Telegram bot where a user
photographs a receipt, a vision-capable LLM extracts line items (`app/llm.py`,
`app/gemini_llm.py`), shelf-life is resolved through a cache → vendored USDA
FoodKeeper table → web-search → default-fallback chain
(`app/frozen_shelf_life.py`), and a scheduled per-user digest surfaces items
due within seven days (`app/scheduler.py`). On top of the pantry sits an AI
recipe engine (`/cook`, now backed by a real Spoonacular/TheMealDB source
chain — `app/cook/recipe_source.py`), a multi-day meal planner (`/plan`,
`app/plan_service.py`), natural-language input (`app/nl_intent.py`), and a
metered multi-household subscription business with Telegram Stars payments
and an operator bot (`app/billing/`, `app/operator/`) — all built on **four
interchangeable LLM providers** (Anthropic, OpenAI, Google Gemini, DeepSeek)
selected per user through one generic capability-aware selector base class,
now specialized into **9 capability seams** (`app/providers.py`). The
repository is the work of a **sole author** across three git identities
(`awbjcj`/`wujiajin0303@gmail.com`, `David Wu`/`awbjcj@users.noreply.github.com`,
`awbjcj`/`awbjcj@gmail.com`) — 353 of 359 commits, the rest split between
`dependabot[bot]` (5) and `copilot-swe-agent[bot]` (1) (`git shortlog -sne`).
It is an **actively shipped** project developed over roughly ten weeks and
counting: spec-and-plan documents for twenty-plus versioned iterations
(v1 → v6.0) live under `docs/superpowers/`, and the codebase is backed by 632
offline test functions.

Role: sole author (353 of 359 commits, `git shortlog -sne`)
Repository: https://github.com/awbjcj/food-manager
Timeline: 2026-05-26 – present

## Tech stack (evidence-backed)

- **Python 3.12** — entire codebase; `requires-python = ">=3.12"` in `pyproject.toml`.
- **Anthropic Claude API** (`anthropic` SDK) — receipt/text parse + web-search clients in `app/llm.py`; dependency `anthropic>=0.120.0,<1.0`.
- **OpenAI API** (`openai` SDK) — parallel provider clients in `app/llm.py`, `app/cook/llm.py`; dependency `openai>=2.48.0,<3.0`.
- **Google Gemini** (`google-genai` SDK) — all Gemini clients with structured-output mode (`response_schema` / `response_mime_type`) and Google Search grounding in `app/gemini_llm.py`; dependency `google-genai>=2.14.0,<3.0`.
- **DeepSeek** (OpenAI-compatible `chat.completions`) — text-only provider clients in `app/deepseek_llm.py`.
- **Agno** (agent framework) — intent classification (`app/nl_intent.py`), meal-plan week composition (`app/week_composer.py`), and taste affinity (`app/cook/affinity.py`); dependency `agno>=2.8.5`.
- **Spoonacular API** — real-source recipe candidates in `app/cook/recipe_source.py`, wired into the live `/cook` pipeline via `app/cook/service.py`.
- **TheMealDB API** — secondary real recipe source in `app/cook/recipe_source.py`.
- **aiogram 3** — async Telegram bot handlers and dispatcher in `app/bot.py`; dependency `aiogram>=3.30.0,<4.0`.
- **SQLModel / SQLAlchemy** — 16 ORM table models in `app/models.py` (`Household`, `User`, `PantryItem`, `Subscription`, `LedgerEntry`, `MealPlan`, …).
- **SQLite** — application datastore; `DATABASE_PATH` engine wiring in `app/db.py`, `.backup-TIMESTAMP` rotation in `app/backup.py`.
- **Alembic** — 17 schema migrations under `migrations/versions/`; dependency `alembic>=1.18.5,<2.0`.
- **Pydantic v2 / pydantic-settings** — typed LLM result schemas and env-var settings in `app/settings.py`; dependencies `pydantic>=2.7,<3.0`, `pydantic-settings>=2.14.2,<3.0`.
- **APScheduler** — per-user cron digest jobs in `app/scheduler.py`; dependency `apscheduler>=3.11.3,<4.0`.
- **httpx** — async transport underlying provider calls; dependency `httpx>=0.28.1`.
- **Telegram Stars** — in-chat subscription/top-up payment rail in `app/handlers/billing.py` (pre-checkout validation + successful-payment application).
- **pytest / pytest-asyncio / pytest-mock / freezegun** — dev test stack in `pyproject.toml` `[dependency-groups]`; `asyncio_mode = "auto"`.
- **Ruff** — linting; dependency `ruff>=0.16.0`.
- **Pyright** — static type checking; dependency `pyright>=1.1.411`.
- **pip-audit** — locked-dependency vulnerability scanning, run in CI (`.github/workflows/ci.yml`).
- **GitHub Actions** — CI/CD: lint, type-check, migration smoke test, full test suite, Docker build, dependency audit (`.github/workflows/ci.yml`).
- **Docker** — production container build (`Dockerfile`), deployed via Railway.
- **uv** — dependency and environment management; `[tool.uv] managed = true`.

## Architecture highlights

- **Architected a provider-agnostic LLM routing layer** that made adding a new
  model provider a one-line change: `app/providers.py` is the single source of
  truth for the `Provider` type and a `PROVIDER_CAPABILITIES` matrix (4
  providers × {image, text, search}), consumed by one generic
  `ProviderSelector[T]` base class now specialized into 9 capability-specific
  selectors across `app/llm.py`, `app/nl_intent.py`, `app/week_composer.py`,
  `app/shelf_life_search.py`, and `app/translation_llm.py`
  (`grep -rn "class .*(ProviderSelector" app` → 9).
- **Decoupled the Telegram dispatcher from every feature handler** by splitting
  `bot.py` into `app/handlers/` (per-feature command handlers) and
  `app/callbacks/` (a typed `CallbackRegistry` routing stateless button
  payloads), then enforced the boundary with an AST-based architecture test
  that fails the build if any handler or callback module imports the
  dispatcher module (`tests/test_handler_architecture.py::test_handler_modules_do_not_import_dispatcher_module`).
- **Built a single ack-first, edit-or-resend callback seam** (`app/callback_dispatch.py`)
  so no individual button handler has to re-derive Telegram's callback-token
  expiry or in-place-edit failure handling: acknowledge before any slow work,
  then edit the message in place, falling back to a fresh send on any failure
  other than "not modified".
- **Engineered a metered, fail-closed billing and operator layer** for
  multi-tenant commercialization: `app/billing/meter.py` gates every
  LLM-backed action through `admit()` (receipts/actions/cost-breaker limits,
  degrade-not-block for non-ingest ops) before work runs and `commit()`
  records usage after, while `app/operator/auth.py` seeds `OPERATOR_IDS` as an
  empty frozenset by default so operator commands (`/whois`, `/grant`,
  `/refund`, `/ban`, `/reconcile`) deny everyone until explicitly configured
  in `bin/run.py`.
- **Made Telegram Stars payments atomic** by validating payer, SKU, amount,
  and household membership together in `_validate_checkout`
  (`app/handlers/billing.py`) before writing the ledger row and entitlement
  update in the same database transaction.
- **Wired a real multi-source recipe engine into `/cook`**, replacing an
  LLM-only candidate generator with a `RecipeSource` chain over Spoonacular
  and TheMealDB (`app/cook/recipe_source.py`, consumed by `app/cook/service.py`),
  ranked by a blended score of health, expiry-utilization, deliciousness, and
  taste affinity (`app/cook/logic.py`, `app/cook/affinity.py`).
- **Made the interactive pantry and meal-plan UI survive bot restarts** by
  deriving every button view from stateless callback payloads rather than
  server-side session state, with `app/views.py` composing pure, synchronous,
  per-language renderers above the async translation/data layer.
- **Instrumented every provider network call** with `*_timing` structured logs
  (duration_ms, attempts) and wrapped polling in an exponential-backoff
  restart supervisor (`run_with_restart`) with rate-limited owner alerts on
  failure (`app/resilience.py`, `app/alerts.py`, `app/llm_transport.py`).

## Quantified outcomes

- **632 test functions across 71 files, running fully offline** — `grep -rh "def test_" tests | wc -l` → 632; `find tests -name "*.py" | wc -l` → 71; the LLM is faked via a `FakeLLMClient` protocol implementation (`tests/fakes.py`).
- **~16.3k LOC of application code across 82 modules** (~32.7k LOC total including tests) — `find app -name "*.py" | wc -l` → 82; `find app -name "*.py" | xargs wc -l` → 16,349; total Python `wc -l` (excluding `.venv`) → 32,704.
- **4 LLM providers unified behind one selector, now spanning 9 capability seams** — `Provider` = `("anthropic","openai","gemini","deepseek")` and `grep -rn "class .*(ProviderSelector" app` → 9 (`app/providers.py`).
- **16 database tables under 17 Alembic migrations** — `grep -c "table=True" app/models.py` → 16; `find migrations/versions -name "*.py" | wc -l` → 17.
- **353 of 359 commits authored solo over ~10 weeks** — `git rev-list --count HEAD` → 359; `git shortlog -sne` shows one person across three git identities (353 commits) plus `dependabot[bot]` (5) and `copilot-swe-agent[bot]` (1); `git log` range 2026-05-26 → present.
- **20+ shipped iterations documented** as paired spec + plan files (v1 → v6.0, phases 1–4) under `docs/superpowers/specs/` and `docs/superpowers/plans/`.
- **Runtime performance / latency / coverage-percentage:** None evidenced — no benchmark, profiling, or coverage artifact is checked into the repository; omitted rather than estimated.

## Skills demonstrated

Languages: Python, SQL
Frameworks: aiogram, Agno, APScheduler, Pydantic (aliases: pydantic-settings)
Databases: SQLite, SQLModel, SQLAlchemy, Alembic
AI & LLM Engineering: Multi-provider LLM orchestration, Vision/multimodal parsing, Structured output, Retrieval-augmented generation (aliases: RAG), Agent frameworks, Prompt engineering, Provider capability routing, Cost metering
Payments & Billing: Telegram Stars, Quota metering, Subscription entitlements
Architecture: Dependency injection (aliases: protocol-based seams), Capability-matrix provider abstraction, Stateless UI design, Multi-tenancy (aliases: household model), Internationalization (aliases: i18n), Retry and backoff design
Testing: Pytest, Architecture testing (aliases: AST-based tests), Test doubles (aliases: fakes, protocol fakes), Deterministic time testing (aliases: freezegun)
Reliability & Operations: Docker, GitHub Actions, Structured logging, Exponential backoff, Dependency auditing (aliases: pip-audit)
Tooling: uv, Ruff, Pyright, Git
