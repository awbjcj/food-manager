# Operating the bot

One long-running process (ADR 0001). In-process, `app/resilience.py` restarts a
crashed polling loop with backoff, and startup catch-up (`catch_up_missed_digests`)
re-sends a digest missed during downtime. The layers below cover hard process
death (OOM, unhandled exit, host reboot) — run exactly one instance; two pollers
on one token conflict.

## GitHub Actions and Railway

`.github/workflows/ci.yml` runs on pull requests and pushes to `master`, plus a
weekly schedule. It enforces Ruff, Pyright, an empty-database Alembic upgrade,
the full test suite, Python and Docker builds, and a locked-dependency audit.
It is CI-only: Railway is connected directly to `master` and handles every
deployment.

Protect `master` and require the `Quality gates`, `Build artifacts`, and
`Dependency audit` checks before merge. That prevents Railway's automatic
deployment from receiving a merged revision that has not passed CI. Do not put
Railway, provider, or Telegram production credentials in GitHub Actions; they
belong in Railway service variables.

For rollback, use Railway's **Deployments** view to restore a previous
successful deployment. This is a code rollback: Alembic is not downgraded
automatically. `bin/run.py` creates a SQLite backup before migrations; restore
that backup deliberately when a schema rollback is required.

## systemd (Linux)

`/etc/systemd/system/food-manager.service`:

    [Unit]
    Description=Food Manager Telegram bot
    After=network-online.target
    Wants=network-online.target

    [Service]
    WorkingDirectory=/opt/food-manager
    EnvironmentFile=/opt/food-manager/.env
    ExecStart=/usr/local/bin/uv run python bin/run.py
    Restart=always
    RestartSec=5

    [Install]
    WantedBy=multi-user.target

Then: `sudo systemctl enable --now food-manager`.

## Docker Compose

    services:
      food-manager:
        build: .
        env_file: .env
        restart: unless-stopped
        volumes:
          - ./data:/data          # DATABASE_PATH=/data/food.db

## Windows (dev box)

Simplest supervisor is a PowerShell loop:

    while ($true) { uv run python bin/run.py; Start-Sleep -Seconds 5 }

For unattended operation register it as a Scheduled Task ("At startup",
"Restart on failure") pointing at that loop in a `.ps1` file.

## What to expect when things break

- Unhandled handler error → owner gets a `⚠️ handler_error: ...` DM
  (rate-limited to one per 5 min per event).
- Digest send fails twice → owner gets `⚠️ digest_failed: ...`.
- Polling crash → `polling_crashed` log + `⚠️ polling_crashed: ...` DM, then
  auto-restart with 1s→300s backoff.
- Process died overnight → on restart, any digest whose hour already passed
  and wasn't recorded in `User.last_digest_date` is sent late.
