# food-manager

[English](README.md) | [简体中文](README.zh-CN.md)

[![CI/CD](https://github.com/awbjcj/food-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/awbjcj/food-manager/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Telegram bot and Mini App that track your household's grocery pantry, turn
receipts into structured items, plan meals, and remind everyone before food
expires. It supports Anthropic, OpenAI, Gemini, and DeepSeek, shared households,
Telegram Stars subscriptions, and English, Chinese, French, and Spanish.

## Try the hosted bot

Use the live service at [@foodie_manager_bot](https://t.me/foodie_manager_bot).
It is the easiest way to try receipt scanning, shared household pantries, meal
planning, expiry reminders, and the Telegram Mini App without running a server.
Start on the free plan, then invite your household or upgrade with Telegram
Stars when you need more receipts, AI actions, or member seats.

The hosted Mini App is served from
[food-manager-production.up.railway.app](https://food-manager-production.up.railway.app),
but account access is authenticated by Telegram—open it through the bot's
**Open app** button. See [the hosted service guide](docs/hosted-service.md) for
share-ready promotional copy and onboarding instructions.

## How it works

1. **Receipt photo → pantry items**: Send a photo to the bot. The configured LLM parses the receipt, extracts food items with estimated shelf lives, and stores them in a local SQLite database.
2. **Daily digest**: Each morning at your configured hour, you receive a message listing everything expiring within 7 days, with one-tap buttons to mark items as eaten, tossed, snoozed, or moved to the fridge/freezer.
3. **Interactive pantry browsing**: `/pantry` opens the same digest, your full active list, or a single item card — with the same action buttons — outside the daily schedule.
4. **Storage-aware shelf life**: Moving an item to the fridge or freezer resets its shelf-life clock from the date it was stored (one-way: default → fridge → frozen), using a curated USDA table with web-search and cache fallback.
5. **Shelf life learning**: When you apply a `/correct` proposal, that correction can teach future imports of the same item.
6. **Manual add**: Use `/add` for items you didn't receive a receipt for. The bot proposes parsed items before inserting them.
7. **Recipes from your pantry**: `/cook` suggests a recipe built from what's about to expire, respecting your food profile (`/prefs`); save favorites and build a shopping list for what's missing.
8. **Meal planning and feedback**: `/plan 3` through `/plan 7` creates a multi-day dinner plan, `/calendar` exports it, and cooked actions plus `/history` keep pantry quantities and meal history current.
9. **Natural conversation**: Tell the bot “bought milk and eggs,” “remove milk from my shopping list,” “correct yogurt to expire Friday,” or “how long does salmon keep?” instead of memorizing every command.
10. **Shared households and groups**: `/invite` members to share pantry, shopping, quota, and plans; in hosted mode, `/bind` lets the same household operate safely from a group chat.
11. **Mini App and billing**: The Telegram Mini App provides account settings, quota visibility, Family-plan checkout, top-ups, and subscription management using Telegram Stars.
12. **Flexible provider funding**: Operators can keep providers on metered API keys or route individual providers through an existing Sub2API-backed subscription without restarting the bot.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Node.js and npm (only needed for a native Mini App build; Docker includes it)
- Or Docker Desktop / Docker Engine with Compose
- A Telegram bot token — create one via `@BotFather`
- An API key for at least one LLM provider capable of reading receipt photos: Anthropic, OpenAI, or Gemini (DeepSeek can't read photos and can't be the sole provider)
- Your Telegram user ID — get it from `@userinfobot`

## Quick start

```text
# 1. Copy .env.example to .env and fill in the Telegram fields plus one
#    image-capable provider key.

# 2. Build and start the complete stack.
docker compose up --build -d

# 3. Check readiness and follow startup logs.
docker compose ps
docker compose logs -f food-manager
```

Open <http://localhost:8000/healthz> and expect `{"ok": true}`, then send
`/start` to the bot from the Telegram account in
`ALLOWED_TELEGRAM_USER_ID`. The named Docker volume preserves the SQLite
database across restarts.

Local mode is intentionally single-user and billing-free. Compose forces
`HOSTED_FEATURES_ENABLED=false`, `OPEN_REGISTRATION=false`, and
`BILLING_ENABLED=false`, so it does not expose household invites, public
registration, quotas, plans, checkout, or subscription UI. Pantry tracking,
receipt ingest, reminders, recipes, meal planning, and personal settings remain
available.

For native Windows, macOS, and Linux instructions, direct `docker build` /
`docker run` commands, Mini App HTTPS setup, and troubleshooting, see
[`docs/local-setup.md`](docs/local-setup.md).

### Behavior notes

- `/correct <id> <free text>` and `/add <free text>` parse with the configured
  text model and reply with a diff
  message. Tap **Apply** to commit or **Cancel** to discard. Proposals
  expire after 10 minutes.
- `/llm [anthropic|openai|gemini|deepseek]` shows or changes the per-user LLM
  provider. DeepSeek still can't read receipt photos — photo calls fall back
  to a capable provider automatically — but does have native web search.
- Any mutation to a pantry item (mark eaten / tossed / removed / snoozed /
  corrected / moved to fridge or freezer) invalidates pending corrections for
  that item in the same transaction.
- `/stats` reports receipt accuracy, corrections, waste, estimated savings,
  cooked-meal follow-through, and LLM cost over the last 30 days.
- `/lang [en|zh|fr|es]` sets your language; every household member can pick
  their own — the underlying data stays in English and is translated per
  message.

## Tests

```bash
# Run all tests
uv run pytest

# Run the same static checks used by CI
uv run ruff check app tests bin migrations
uv run pyright app bin

# Audit runtime dependencies pinned in uv.lock
uv export --no-dev --no-emit-project --no-hashes --output-file requirements-audit.txt
uv run pip-audit --requirement requirements-audit.txt --strict --progress-spinner off
rm requirements-audit.txt

# Run a single test file
uv run pytest tests/test_core_services.py

# Run a single test by name
uv run pytest tests/test_core_services.py::test_shelf_life_defaults
```

## Deploy to Railway

1. Create or import the project in Railway, then connect the service source to
   `awbjcj/food-manager` on the `master` branch with automatic deploys enabled.
2. Add a persistent volume named `food-data` mounted at `/data`.
3. Set Railway service variables matching `.env.example`.
4. Push or merge to `master`. Railway builds and deploys the new revision
   automatically; GitHub Actions runs the independent CI quality gates.

The container runs `bin/run.py` which backs up the database, runs Alembic migrations, registers per-user digest cron jobs, then starts long-polling.

For a rollback, use the Railway service's **Deployments** view to restore a
previous successful deployment. This intentionally does not downgrade SQLite
migrations; restore a database backup separately if a migration itself must be
reverted.

## Bot commands

| Command                                      | Description                                                                       |
| -------------------------------------------- | --------------------------------------------------------------------------------- |
| Send a photo                                 | Parse a receipt and log all food items                                            |
| `/add 2 lb chicken, dozen eggs`              | Propose manual items without a receipt                                            |
| `/list`                                      | Show all active pantry items                                                      |
| `/list dairy`                                | Filter by category                                                                |
| `/list week`                                 | Show items expiring within 7 days                                                 |
| `/list expired`                              | Show already-expired items                                                        |
| `/pantry [digest\|<id>]`                     | Interactive pantry view — digest, full list, or one item card with action buttons |
| `/correct <id> <free text>`                  | Propose a natural-language correction                                             |
| `/delete <id>`                               | Remove a wrongly imported item (does not teach future imports)                    |
| `/digest_at 7`                               | Set your daily digest hour (0–23, in your timezone)                               |
| `/tz America/New_York`                       | Set your timezone                                                                 |
| `/lang [en\|zh\|fr\|es]`                     | Show or set your language                                                         |
| `/stats`                                     | Show pantry statistics                                                            |
| `/llm [anthropic\|openai\|gemini\|deepseek]` | Show or switch the LLM provider                                                   |
| `/prefs [sentence]`                          | Show or update your household's food profile                                      |
| `/cook`                                      | Get a recipe built from your pantry                                               |
| `/history`                                   | Show meals your household cooked                                                  |
| `/plan [3-7]`                                | Create a 3–7 day dinner plan (default 5 days)                                     |
| `/calendar`                                  | Export the active dinner plan as an `.ics` calendar file                          |
| `/shopping`                                  | View your to-buy list; tap an item once bought                                    |
| `/favorites`                                 | View saved recipes; tap to re-cook against your current pantry                    |
| `/invite [family]`                           | Invite one person (or `family` for a reusable link) to your household             |
| `/join <code>`                               | Join a household you were invited to                                              |
| `/bind`                                      | Bind the current hosted group chat to your household                              |
| `/household`                                 | List household members                                                            |
| `/leave`                                     | Leave your household                                                              |
| `/remove <id>`                               | (owner) Remove a member from your household                                       |
| `/quota`                                     | Show pooled household receipt and AI-action usage                                 |
| `/buy`                                       | Buy the Family plan or a quota top-up with Telegram Stars                         |
| `/billing`                                   | Show the current subscription status                                              |
| `/help`                                      | Show all commands                                                                 |

## Environment variables

| Variable                   | Required             | Default                     | Description                                                                                                                                                                                  |
| -------------------------- | -------------------- | --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TELEGRAM_BOT_TOKEN`       | Yes                  | —                           | Bot token from `@BotFather`                                                                                                                                                                  |
| `ALLOWED_TELEGRAM_USER_ID` | Yes                  | —                           | Bootstrap and default operator identity                                                                                                                                                      |
| `OPEN_REGISTRATION`        | No                   | `false`                     | Allow new Telegram users to provision free households; existing members and invites still work when false                                                                                    |
| `HOSTED_FEATURES_ENABLED`  | No                   | `false`                     | Enables deployment-only multi-tenant households, registration, quota/plan surfaces, and Stars billing; keep false for localhost                                                             |
| `BILLING_ENABLED`          | No                   | `false`                     | Enforce quota and expose Stars checkout; usage is still recorded when false                                                                                                                  |
| `WEB_APP_URL`              | No                   | —                           | Public HTTPS root URL for the Telegram Mini App; when set, startup installs the bot's **Open app** menu button                                                                              |
| `PORT`                     | No                   | `8000`                      | HTTP port for the Mini App, API, and `/healthz`                                                                                                                                              |
| `INGEST_PROVIDER`          | No                   | first configured            | Image-capable provider pinned for receipt ingest, independent of `/llm`; automatic preference is Gemini, then OpenAI, then Anthropic                                                        |
| `OPERATOR_TELEGRAM_IDS`    | No                   | `ALLOWED_TELEGRAM_USER_ID`  | Comma-separated operator Telegram IDs                                                                                                                                                        |
| `OPERATOR_BOT_TOKEN`       | No                   | —                           | Token for the optional, separate operator bot                                                                                                                                                |
| `LLM_PROVIDER`             | No                   | `anthropic`                 | Default provider: `anthropic`, `openai`, `gemini`, or `deepseek`. Each user can override with `/llm`. `deepseek` is text-only, so at least one image-capable provider's key must also be set |
| `ANTHROPIC_API_KEY`        | When using anthropic | —                           | Anthropic API key                                                                                                                                                                            |
| `ANTHROPIC_MODEL`          | No                   | `claude-sonnet-5`           | Claude model to use for receipt parsing                                                                                                                                                      |
| `ANTHROPIC_TEXT_MODEL`     | No                   | `claude-haiku-4-5-20251001` | Claude model to use for `/correct` and `/add` proposals                                                                                                                                      |
| `ANTHROPIC_SEARCH_MODEL`   | No                   | `claude-sonnet-5`           | Claude model used for shelf-life web search — **requires web search enabled on the Anthropic workspace**                                                                                     |
| `OPENAI_API_KEY`           | When using openai    | —                           | OpenAI API key                                                                                                                                                                               |
| `OPENAI_MODEL`             | No                   | `gpt-5.6-terra`             | OpenAI model to use for receipt parsing                                                                                                                                                      |
| `OPENAI_TEXT_MODEL`        | No                   | `gpt-5.6-luna`              | OpenAI model to use for `/correct` and `/add` proposals                                                                                                                                      |
| `GEMINI_API_KEY`           | When using gemini    | —                           | Google Gemini API key (native `google-genai` SDK)                                                                                                                                            |
| `GEMINI_MODEL`             | No                   | `gemini-3.1-pro-preview`    | Gemini model to use for receipt parsing                                                                                                                                                      |
| `GEMINI_TEXT_MODEL`        | No                   | `gemini-3.5-flash`          | Gemini model to use for `/correct` and `/add` proposals                                                                                                                                      |
| `DEEPSEEK_API_KEY`         | When using deepseek  | —                           | DeepSeek API key. DeepSeek still can't read receipt photos, but now has native web search                                                                                                    |
| `DEEPSEEK_MODEL`           | No                   | `deepseek-v4-flash`         | DeepSeek model to use for text and search tasks                                                                                                                                              |
| `DEEPSEEK_BASE_URL`        | No                   | `https://api.deepseek.com`  | DeepSeek API base URL (OpenAI-compatible)                                                                                                                                                    |
| `SUB2API_BASE_URL`         | No                   | —                           | Shared Sub2API gateway root; HTTPS required except for loopback HTTP in `ENV=dev`                                                                                                            |
| `SUB2API_ANTHROPIC_TOKEN`  | No                   | —                           | Anthropic subscription-routing token; makes subscription mode the default for Anthropic                                                                                                     |
| `SUB2API_OPENAI_TOKEN`     | No                   | —                           | OpenAI subscription-routing token                                                                                                                                                            |
| `SUB2API_GEMINI_TOKEN`     | No                   | —                           | Gemini subscription-routing token                                                                                                                                                            |
| `SUB2API_DEEPSEEK_TOKEN`   | No                   | —                           | DeepSeek subscription-routing token                                                                                                                                                          |
| `SPOONACULAR_API_KEY`      | No                   | —                           | Optional Spoonacular key; `/cook` and `/plan` otherwise use TheMealDB plus the configured LLM recipe source                                                                                   |
| `COOK_COST_CEILING_MICROS` | No                   | `100000`                    | Per-`/cook` LLM spend ceiling in micro-USD ($0.10); raise if recipes come back empty                                                                                                         |
| `PLAN_COST_CEILING_MICROS` | No                   | `150000`                    | Per-`/plan` LLM spend ceiling in micro-USD ($0.15); raise if week plans come back empty                                                                                                      |
| `DATABASE_PATH`            | No                   | `./food.db`                 | Path to the SQLite database file                                                                                                                                                             |
| `LOG_LEVEL`                | No                   | `INFO`                      | Logging level                                                                                                                                                                                |
| `ENV`                      | No                   | `dev`                       | Set to `prod` for JSON-structured logs                                                                                                                                                       |

Stars subscriptions renew every 30 days. Household owners can cancel renewal
from the Mini App; top-ups expire at the end of the current quota period.
For the complete purchase, verification, top-up, and cancellation flow, see
[`docs/telegram-subscriptions.md`](docs/telegram-subscriptions.md).

## Project docs

- [`docs/adr/`](docs/adr) — architecture decision records
- [`docs/operations.md`](docs/operations.md) — running and supervising the bot in production
- [`docs/local-setup.md`](docs/local-setup.md) — native and Docker setup for Windows, macOS, and Linux
- [`docs/hosted-service.md`](docs/hosted-service.md) — live bot onboarding and promotional copy
- [`docs/telegram-subscriptions.md`](docs/telegram-subscriptions.md) — user guide for Stars subscriptions and top-ups
- [`docs/superpowers/`](docs/superpowers) — the spec + plan for every shipped version, in order
- [`CONTEXT.md`](CONTEXT.md) — domain glossary (ubiquitous language) used across the codebase
- [`CLAUDE.md`](CLAUDE.md) / [`AGENTS.md`](AGENTS.md) — architecture and conventions reference for AI coding agents working in this repo

## Contributing

This is a solo-maintained personal project, built and documented heavily with
AI coding agents. Issues and pull requests are welcome — for anything beyond a
small fix, please open an issue first to discuss the change. Run
`uv run pytest`, `uv run ruff check app tests bin migrations`, and
`uv run pyright app bin` before submitting a PR; CI runs the same checks.

## License

[MIT](LICENSE)
