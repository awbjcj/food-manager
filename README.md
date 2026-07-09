# food-manager

A Telegram bot that tracks your household's grocery pantry and sends a daily expiry digest. Send it a photo of a receipt and it extracts the items, estimates shelf lives using your choice of Anthropic, OpenAI, Gemini, or DeepSeek, and reminds you before things go bad. Multiple people can share one household's pantry, shopping list, and food preferences, and the bot speaks English, Chinese, French, or Spanish.

## How it works

1. **Receipt photo → pantry items**: Send a photo to the bot. The configured LLM parses the receipt, extracts food items with estimated shelf lives, and stores them in a local SQLite database.
2. **Daily digest**: Each morning at your configured hour, you receive a message listing everything expiring within 7 days, with one-tap buttons to mark items as eaten, tossed, snoozed, or moved to the fridge/freezer.
3. **Interactive pantry browsing**: `/pantry` opens the same digest, your full active list, or a single item card — with the same action buttons — outside the daily schedule.
4. **Storage-aware shelf life**: Moving an item to the fridge or freezer resets its shelf-life clock from the date it was stored (one-way: default → fridge → frozen), using a curated USDA table with web-search and cache fallback.
5. **Shelf life learning**: When you apply a `/correct` proposal, that correction can teach future imports of the same item.
6. **Manual add**: Use `/add` for items you didn't receive a receipt for. The bot proposes parsed items before inserting them.
7. **Recipes from your pantry**: `/cook` suggests a recipe built from what's about to expire, respecting your food profile (`/prefs`); save favorites and build a shopping list for what's missing.
8. **Shared households**: `/invite` a household member to share your pantry, shopping list, and preferences; anyone in the household sees the same data, rendered in their own language.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- A Telegram bot token — create one via `@BotFather`
- An API key for at least one LLM provider capable of reading receipt photos: Anthropic, OpenAI, or Gemini (DeepSeek is text-only and can't be the sole provider)
- Your Telegram user ID — get it from `@userinfobot`

## Local dev

```bash
# 1. Install dependencies
uv sync

# 2. Configure environment
cp .env.example .env
# Fill in TELEGRAM_BOT_TOKEN, ALLOWED_TELEGRAM_USER_ID, and an API key for
# LLM_PROVIDER (default anthropic). See "Environment variables" below for the
# full per-provider key/model list.

# 3. Run database migrations
DATABASE_PATH=./food.db uv run alembic upgrade head

# 4. Start the bot
uv run python bin/run.py

# 5. Send /start from your Telegram account to create your household and user record
```

### Behavior notes

- `/correct <id> <free text>` and `/add <free text>` parse with the configured
  text model and reply with a diff
  message. Tap **Apply** to commit or **Cancel** to discard. Proposals
  expire after 10 minutes.
- `/llm [anthropic|openai|gemini|deepseek]` shows or changes the per-user LLM
  provider. DeepSeek is text-only (no photo reading, no web search) — those
  calls fall back to a capable provider automatically.
- Any mutation to a pantry item (mark eaten / tossed / removed / snoozed /
  corrected / moved to fridge or freezer) invalidates pending corrections for
  that item in the same transaction.
- `/stats` reports text-LLM cost broken down by action type.
- `/lang [en|zh|fr|es]` sets your language; every household member can pick
  their own — the underlying data stays in English and is translated per
  message.

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
| `/pantry [digest\|<id>]` | Interactive pantry view — digest, full list, or one item card with action buttons |
| `/correct <id> <free text>` | Propose a natural-language correction |
| `/delete <id>` | Remove a wrongly imported item (does not teach future imports) |
| `/digest_at 7` | Set your daily digest hour (0–23, in your timezone) |
| `/tz America/New_York` | Set your timezone |
| `/lang [en\|zh\|fr\|es]` | Show or set your language |
| `/stats` | Show pantry statistics |
| `/llm [anthropic\|openai\|gemini\|deepseek]` | Show or switch the LLM provider |
| `/prefs [sentence]` | Show or update your household's food profile |
| `/cook` | Get a recipe built from your pantry |
| `/shopping` | View your to-buy list; tap an item once bought |
| `/favorites` | View saved recipes; tap to re-cook against your current pantry |
| `/invite [family]` | Invite one person (or `family` for a reusable link) to your household |
| `/join <code>` | Join a household you were invited to |
| `/household` | List household members |
| `/leave` | Leave your household |
| `/remove <id>` | (owner) Remove a member from your household |
| `/help` | Show all commands |

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | — | Bot token from `@BotFather` |
| `ALLOWED_TELEGRAM_USER_ID` | Yes | — | Numeric Telegram user ID allowed to bootstrap the first household |
| `LLM_PROVIDER` | No | `anthropic` | Default provider: `anthropic`, `openai`, `gemini`, or `deepseek`. Each user can override with `/llm`. `deepseek` is text-only, so at least one image-capable provider's key must also be set |
| `ANTHROPIC_API_KEY` | When using anthropic | — | Anthropic API key |
| `ANTHROPIC_MODEL` | No | `claude-sonnet-4-6` | Claude model to use for receipt parsing |
| `ANTHROPIC_TEXT_MODEL` | No | `claude-haiku-4-5-20251001` | Claude model to use for `/correct` and `/add` proposals |
| `ANTHROPIC_SEARCH_MODEL` | No | `claude-sonnet-4-6` | Claude model used for shelf-life web search — **requires web search enabled on the Anthropic workspace** |
| `OPENAI_API_KEY` | When using openai | — | OpenAI API key |
| `OPENAI_MODEL` | No | `gpt-5.4` | OpenAI model to use for receipt parsing |
| `OPENAI_TEXT_MODEL` | No | `gpt-5.4-mini` | OpenAI model to use for `/correct` and `/add` proposals |
| `GEMINI_API_KEY` | When using gemini | — | Google Gemini API key (native `google-genai` SDK) |
| `GEMINI_MODEL` | No | `gemini-3.5-flash` | Gemini model to use for receipt parsing |
| `GEMINI_TEXT_MODEL` | No | `gemini-3.5-flash` | Gemini model to use for `/correct` and `/add` proposals |
| `DEEPSEEK_API_KEY` | When using deepseek | — | DeepSeek API key. DeepSeek is text-only: no receipt photo reading, no web search |
| `DEEPSEEK_MODEL` | No | `deepseek-chat` | DeepSeek model to use for text tasks |
| `DEEPSEEK_BASE_URL` | No | `https://api.deepseek.com` | DeepSeek API base URL (OpenAI-compatible) |
| `SPOONACULAR_API_KEY` | No | — | Optional Spoonacular key for the in-progress real-source `/cook` recipe chain (not yet wired into the live pipeline) |
| `COOK_COST_CEILING_MICROS` | No | `100000` | Per-`/cook` LLM spend ceiling in micro-USD ($0.10); raise if recipes come back empty |
| `DATABASE_PATH` | No | `./food.db` | Path to the SQLite database file |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `ENV` | No | `dev` | Set to `prod` for JSON-structured logs |
