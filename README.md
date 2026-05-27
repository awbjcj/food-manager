# food-manager

Single-user grocery pantry tracker + daily expiry digest as a Telegram bot.

See `docs/superpowers/specs/2026-05-26-food-manager-v1-design.md` for the
full design spec and `docs/superpowers/plans/2026-05-26-food-manager-v1.md`
for the implementation plan.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- A Telegram bot token from `@BotFather`
- An Anthropic API key
- Your own Telegram user id (`@userinfobot`)

## Local dev

1. `uv sync`
2. `cp .env.example .env` and fill in:
   - `TELEGRAM_BOT_TOKEN`
   - `ALLOWED_TELEGRAM_USER_ID` (your numeric Telegram user id)
   - `ANTHROPIC_API_KEY`
3. `DATABASE_PATH=./food.db uv run alembic upgrade head`
4. `uv run python bin/run.py`
5. Send the bot `/start` from your Telegram account.

## Tests

`uv run pytest`

## Deploy to Railway

1. `railway init` (or import the repo in the Railway dashboard).
2. Add a persistent volume named `food-data` mounted at `/data`.
3. Set environment variables (same names as `.env.example`).
4. Push: `git push railway main`.

The container runs `bin/run.py` which migrates on boot, starts the bot
(long-polling), and registers per-user digest cron jobs.

## Daily use

| Action | Command |
|---|---|
| Log a receipt | Send a photo |
| Add manually | `/add 2 lb chicken, dozen eggs` |
| See pantry | `/list`, `/list dairy`, `/list week`, `/list expired` |
| Got something wrong | `/correct <id> <days>` - teaches future estimates |
| Wrong import | `/delete <id>` - does NOT teach future estimates |
| Set digest time | `/digest_at 7` |
| Change timezone | `/tz America/New_York` |
| Stats | `/stats` |

Each morning at your configured hour, you'll get a digest if anything is
expiring in the next 7 days, with one-tap `[Ate / Tossed / Remind +2d]`
buttons.
