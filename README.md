# food-manager

A single-user Telegram bot that tracks your grocery pantry and sends a daily expiry digest. Send it a photo of a receipt and it extracts the items, estimates shelf lives using Claude, and reminds you before things go bad.

## How it works

1. **Receipt photo → pantry items**: Send a photo to the bot. Claude parses the receipt, extracts food items with estimated shelf lives, and stores them in a local SQLite database.
2. **Daily digest**: Each morning at your configured hour, you receive a message listing everything expiring within 7 days, with one-tap buttons to mark items as eaten, tossed, or snooze for 2 days.
3. **Shelf life learning**: When you apply a `/correct` proposal, that correction can teach future imports of the same item.
4. **Manual add**: Use `/add` for items you didn't receive a receipt for. The bot proposes parsed items before inserting them.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- A Telegram bot token — create one via `@BotFather`
- An Anthropic API key
- Your Telegram user ID — get it from `@userinfobot`

## Local dev

```bash
# 1. Install dependencies
uv sync

# 2. Configure environment
cp .env.example .env
# Fill in TELEGRAM_BOT_TOKEN, ALLOWED_TELEGRAM_USER_ID, ANTHROPIC_API_KEY
# Optional: set ANTHROPIC_TEXT_MODEL for /correct and /add proposals

# 3. Run database migrations
DATABASE_PATH=./food.db uv run alembic upgrade head

# 4. Start the bot
uv run python bin/run.py

# 5. Send /start from your Telegram account to create your user record
```

### v1.5 behavior

- `/correct <id> <free text>` and `/add <free text>` parse with Claude
  Haiku (configurable via `ANTHROPIC_TEXT_MODEL`) and reply with a diff
  message. Tap **Apply** to commit or **Cancel** to discard. Proposals
  expire after 10 minutes.
- Any mutation to a pantry item (mark eaten / tossed / removed / snoozed /
  corrected) invalidates pending corrections for that item in the same
  transaction.
- `/stats` reports text-LLM cost broken down by action type.

## Tests

```bash
# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_core_services.py

# Run a single test by name
uv run pytest tests/test_core_services.py::test_shelf_life_defaults
```

## Deploy to Railway

1. `railway init` (or import the repo in the Railway dashboard).
2. Add a persistent volume named `food-data` mounted at `/data`.
3. Set environment variables matching `.env.example`.
4. Push: `git push railway main`.

The container runs `bin/run.py` which backs up the database, runs Alembic migrations, registers per-user digest cron jobs, then starts long-polling.

## Bot commands

| Command | Description |
|---|---|
| Send a photo | Parse a receipt and log all food items |
| `/add 2 lb chicken, dozen eggs` | Propose manual items without a receipt |
| `/list` | Show all active pantry items |
| `/list dairy` | Filter by category |
| `/list week` | Show items expiring within 7 days |
| `/list expired` | Show already-expired items |
| `/correct <id> <free text>` | Propose a natural-language correction |
| `/delete <id>` | Remove a wrongly imported item (does not teach future imports) |
| `/digest_at 7` | Set your daily digest hour (0–23, in your timezone) |
| `/tz America/New_York` | Set your timezone |
| `/stats` | Show pantry statistics |
| `/help` | Show all commands |

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | — | Bot token from `@BotFather` |
| `ALLOWED_TELEGRAM_USER_ID` | Yes | — | Your numeric Telegram user ID |
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key |
| `DATABASE_PATH` | No | `./food.db` | Path to the SQLite database file |
| `ANTHROPIC_MODEL` | No | `claude-sonnet-4-6` | Claude model to use for receipt parsing |
| `ANTHROPIC_TEXT_MODEL` | No | `claude-haiku-4-5-20251001` | Claude model to use for `/correct` and `/add` proposals |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `ENV` | No | `dev` | Set to `prod` for JSON-structured logs |
