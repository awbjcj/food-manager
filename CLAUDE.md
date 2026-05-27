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
| `app/llm.py` | `LLMClient` Protocol + `AnthropicLLMClient`; `ParsedItem`/`LLMResult` Pydantic models |
| `app/ingest_service.py` | Photo and `/add` text → `PantryItem` rows; `IngestSummary` dataclass |
| `app/pantry_service.py` | CRUD: list, snooze, mark eaten/tossed/removed, correct shelf life |
| `app/cache.py` | `ShelfLifeCache` read/write/user-correction |
| `app/commands.py` | Parse raw Telegram command argument strings into typed values |
| `app/renderer.py` | Format `PantryItem` lists into Telegram message text + inline keyboards |
| `app/bot.py` | aiogram handlers; `AuthDecision` / `authorize_and_get_user`; `build_dispatcher` |
| `app/scheduler.py` | APScheduler job registration; `schedule_user_digest`, `send_digest_once` |
| `app/backup.py` | SQLite `.backup-TIMESTAMP` rotation before migrations |
| `app/normalization.py` | Food name normalization (lowercasing, alias mapping) |
| `app/shelf_life_defaults.py` | Hardcoded shelf-life fallback table |
| `bin/run.py` | Entry point: loads settings, runs migrations, starts scheduler + polling |

### Key design conventions

- **Session injection**: All service functions receive `session: Session` as the first argument. No global state.
- **Explicit `today: date`**: Service functions never call `datetime.now()` internally — callers pass `today`. This makes tests deterministic without mocking.
- **LLMClient is a Protocol**: `FakeLLMClient` in `tests/fakes.py` satisfies the protocol via duck typing for unit tests.
- **`Settings()` instantiation**: Pydantic-settings loads from env vars via field aliases. Call sites in tests need `# type: ignore[call-arg]` because Pylance can't infer env-var loading.
- **`PantryItem.id` is `Optional[int]`**: Auto-populated by SQLite after commit. Assert `is not None` before passing to service functions in tests.
- **Scheduler `send` callback must be async**: `schedule_user_digest` and `register_all_user_digests` accept `Callable[..., Awaitable[None]]` — use `AsyncMock()` in tests, not sync lambdas.

### Database

SQLite via SQLModel/SQLAlchemy. Migrations managed by Alembic in `migrations/`. The `DATABASE_PATH` env var (default `./food.db`) is passed to both Alembic and the app engine.
