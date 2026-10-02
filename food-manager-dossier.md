---
repo_url: https://github.com/awbjcj/food-manager
repo_name: food-manager
role: sole author
generated_at: 2026-10-02
source_revision: 548eb0db3389e9918d1e5c9d2216587e261e3d78
---

# Project: Food Manager — Shared Pantry and Meal-Planning Assistant

## Summary

A Telegram bot and Mini App for households tracking groceries, expiry dates, recipes, and meal plans. Receipt photos and natural-language inputs become persisted pantry records through capability-aware model routing. Shared household membership, metered actions, payments, and operator controls surround the domain workflows. Recent implementation gives the Mini App a Kitchen workspace that invokes existing bot handlers and groups pantry items by source receipt. The project is actively implemented by one human author with automation-account commits.

Role: sole author (431 of 447 repository commits across the owner’s Git identities, `git shortlog -sne 548eb0db3389e9918d1e5c9d2216587e261e3d78`; preserved upstream/contributor and automation history is excluded from the owner count)
Repository: https://github.com/awbjcj/food-manager
Timeline: 2026-05 – present (`git log --reverse --format=%as`)

## Tech stack (evidence-backed)

- Python — services and handlers under `app/`.
- aiogram — dispatcher and domain handlers in `app/bot.py` and `app/handlers/`.
- aiohttp — Mini App routes in `app/webapp.py`.
- SQLModel — household, pantry, usage, and subscription models in `app/models.py`.
- SQLAlchemy — database engine and sessions in `app/db.py`.
- SQLite — persistent datastore in `app/db.py`.
- Alembic — schema migrations under `migrations/versions/`.
- Anthropic API — receipt/text clients in `app/llm.py`.
- OpenAI API — parallel model clients in `app/llm.py` and `app/cook/llm.py`.
- Google Gemini — model clients in `app/gemini_llm.py`.
- DeepSeek — model clients in `app/deepseek_llm.py`.
- Agno — model construction in `app/agno_models.py`.
- APScheduler — per-user digest scheduling in `app/scheduler.py`.
- Pydantic — typed model output and runtime configuration in `app/llm.py` and `app/settings.py`.
- Spoonacular API — configured recipe-source implementation in `app/cook/recipe_source.py`.
- React — Mini App interface in `web/src/`.
- TypeScript — Mini App API and workspace contracts in `web/src/`.
- pytest — service and Mini App regression tests under `tests/`.
- Docker — runtime image in `Dockerfile`.

## Architecture highlights

- Engineered capability-aware provider routing so receipt parsing selects an image-capable client while text tasks retain user preference (`app/providers.py`, `app/llm.py`).
- Reused domain authorization, quotas, and confirmation transactions in the Mini App Kitchen by dispatching commands and server-owned card actions to existing handlers (`app/miniapp_workspace.py`, `tests/test_miniapp_workspace.py`; `28b4a9e`).
- Made pantry receipt groups recognizable with source-receipt sorting, badges, and contextual action callbacks (`app/pantry_service.py`, `app/renderer.py`, `app/views.py`; `dd85ef6`).
- Enforced metered admission and atomic payment-entitlement updates through `app/billing/meter.py` and `app/handlers/billing.py`.
- Allowed live per-provider credential-mode overrides with credential-availability checks (`app/provider_mode_service.py`, `tests/test_provider_modes.py`).
- Localized pantry and Mini App interactions through shared server and browser catalogs (`app/i18n.py`, `web/src/i18n.ts`, `tests/test_i18n_rendering.py`; `10ab208`).

## Quantified outcomes

- Counted 819 declared Python test functions in 82 tracked files with test definitions; this is a static count, not a test-pass or coverage result (`git grep -E '^\s*(async\s+)?def\s+test_' 548eb0db3389e9918d1e5c9d2216587e261e3d78 -- .`, restricted to `test_*.py` / `*_test.py` paths).
- Counted 203 tracked Python files, including tests where present (`git ls-tree -r --name-only 548eb0db3389e9918d1e5c9d2216587e261e3d78 -- .`, filtered to `.py`).
- Recorded 447 repository commits at source revision `548eb0db` (`git rev-list --count 548eb0db3389e9918d1e5c9d2216587e261e3d78`).
- None evidenced for production latency, uptime, business impact, or benchmarked model quality; no such outcome is claimed.

## Skills demonstrated

Languages: Python, TypeScript
Frameworks: aiogram, aiohttp, SQLModel, SQLAlchemy, Pydantic, Agno, APScheduler, React
Databases: SQLite
AI and APIs: Anthropic API, OpenAI API, Google Gemini, DeepSeek, Spoonacular API
Testing: pytest
Tooling: Alembic, Docker
