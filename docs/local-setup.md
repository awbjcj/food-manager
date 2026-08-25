# Local setup

The bot, scheduler, operator bot, Mini App API, and built Mini App frontend all
run in one process. Run only one process for a Telegram bot token: Telegram
long-polling does not support two consumers using the same token.

Local mode is a private, single-user edition. Keep
`HOSTED_FEATURES_ENABLED=false` (the default). It disables public registration,
household invites/membership, quotas, plan sales, Telegram Stars checkout, and
subscription UI. This avoids running deployment-only commercial and
multi-tenant systems on a personal machine.

## 1. Create the Telegram bot

1. Open `@BotFather` in Telegram and run `/newbot`.
2. Copy the bot token into `TELEGRAM_BOT_TOKEN` in `.env`.
3. Get your numeric Telegram ID from `@userinfobot` and put it in
   `ALLOWED_TELEGRAM_USER_ID`.
4. Configure one image-capable AI provider: Anthropic, OpenAI, or Gemini.
   DeepSeek can handle text and search, but cannot be the only provider because
   receipt ingest needs image input.

Copy `.env.example` to `.env` before editing it:

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```bash
# macOS or Linux
cp .env.example .env
```

Never commit `.env`; it contains bot and provider credentials.

## 2. Start everything natively

Requirements: Python 3.12 or newer, `uv`, and Node.js/npm for the Mini App.

```powershell
# Windows PowerShell, from the repository root
uv sync
Push-Location web
npm ci
npm run build
Pop-Location
uv run python bin/run.py
```

```bash
# macOS or Linux, from the repository root
uv sync
(cd web && npm ci && npm run build)
uv run python bin/run.py
```

Startup backs up the existing SQLite database, applies Alembic migrations,
starts the HTTP server on `PORT` (8000 by default), registers digest jobs, and
starts Telegram polling. Confirm the HTTP side is ready at
<http://localhost:8000/healthz>. The response should be `{"ok": true}`.

After changing only Python code, restart the process. After changing the Mini
App, rebuild `web/dist` with `npm run build` and restart. Vite's standalone dev
server is useful for visual work (`cd web && npm run dev`), but it uses mock
data outside Telegram; the integrated build is served by the Python process.

## 3. Start everything with Docker Compose

Requirements: Docker Desktop on Windows/macOS, or Docker Engine with the
Compose plugin on Linux.

```text
docker compose up --build -d
docker compose ps
docker compose logs -f food-manager
```

The Compose service builds both the React Mini App and Python runtime, exposes
<http://localhost:8000>, and stores SQLite data in the named `food-data`
volume. Stop the service without deleting data:

```text
docker compose down
```

Use `docker compose down --volumes` only when you intentionally want to erase
the local database.

## 4. Build and run the image directly

```text
docker build -t food-manager:local .
docker volume create food-manager-data
docker run --name food-manager --env-file .env \
  -e DATABASE_PATH=/data/food.db -e PORT=8000 \
  -e HOSTED_FEATURES_ENABLED=false -e BILLING_ENABLED=false \
  -e OPEN_REGISTRATION=false \
  -p 8000:8000 -v food-manager-data:/data food-manager:local
```

In PowerShell, either enter the `docker run` command on one line or replace
each trailing `\` with PowerShell's backtick continuation character.

## 5. Enable the Telegram Mini App locally

The health endpoint and API work on localhost, but Telegram requires an HTTPS
URL for a Mini App. To test the real Telegram UI:

1. Start the app locally on port 8000.
2. Expose that port through an HTTPS development tunnel.
3. Set `WEB_APP_URL` in `.env` to the tunnel's public HTTPS root URL.
4. Restart food-manager. At startup it registers an **Open app** menu button
   with Telegram.

Treat a tunnel URL as public: keep registration closed unless needed, and do
not share it. Mini App API requests are authenticated with Telegram init data;
opening the URL directly in a normal browser cannot access a user's account.

## Optional systems

- Set `OPERATOR_BOT_TOKEN` to run the private operator bot in the same process.
  Access is limited to `OPERATOR_TELEGRAM_IDS` (or the bootstrap user by
  default).
- `BILLING_ENABLED` and `OPEN_REGISTRATION` are ignored while
  `HOSTED_FEATURES_ENABLED=false`. Do not enable hosted features on a public
  machine without following `docs/operations.md` and configuring operator
  controls.
- Set `SUB2API_BASE_URL` plus a provider-specific `SUB2API_*_TOKEN` to use an
  existing provider subscription through Sub2API. In development only,
  loopback HTTP is accepted; other gateway URLs must use HTTPS.

## Troubleshooting

- **Settings validation fails:** check that the default provider has either an
  API key or a Sub2API token, and that at least one configured provider can
  process images.
- **The Mini App shows no menu button:** `WEB_APP_URL` must be public HTTPS and
  reachable by Telegram; restart after changing it.
- **Port 8000 is busy:** change `PORT` for native startup. For Compose, also
  change the host side of `8000:8000` in `compose.yaml`.
- **Telegram reports a polling conflict:** stop the other local or deployed
  process using the same bot token.
- **Docker data disappears:** make sure `DATABASE_PATH` is `/data/food.db` and
  `/data` is backed by the named volume.
