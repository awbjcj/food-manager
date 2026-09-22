# Mini App workflows

[English](mini-app.md) | [简体中文](mini-app.zh-CN.md)

Open the app from Telegram's **Open app** menu. The **Kitchen** tab provides
forms and interactive result cards for all commands registered on the user bot.
Home shortcuts open the corresponding Kitchen feature. No command typing or
return to the chat is required for these workflows.

| Area | Available workflows | Bot command equivalents |
| --- | --- | --- |
| Pantry | Browse, filter, scan JPEG/PNG receipts up to 10 MB, add food, approve/cancel corrections, undo, mark eaten/discarded, remove, snooze, refrigerate/freeze, statistics | `/pantry`, `/list`, `/add`, `/correct`, `/ate`, `/toss`, `/delete`, `/snooze`, `/stats`, photo uploads and item buttons |
| Meals | Recipe choices, alternatives, refinement, feedback, saving, shopping, cooked-meal confirmation and history | `/cook`, `/shopping`, `/favorites`, `/history` and recipe buttons |
| Planning | View the current plan, generate 3–7 days, swap meals, add missing ingredients, cancel, export an `.ics` calendar | `/plan`, `/calendar` and plan buttons |
| Preferences | Dietary preferences, exclusions, cuisines, cooking time, language, AI provider, time zone, daily digest hour | `/prefs`, `/lang`, `/llm`, `/tz`, `/digest_at` |
| Household | Members, single-use/reusable invitations, join, leave, owner-only member removal, connect a Telegram group | `/household`, `/invite`, `/join`, `/leave`, `/remove`, `/bind` |
| Account and help | Quota, plans/top-ups, billing details, renewal cancellation, onboarding, help, natural-language requests | `/quota`, `/buy`, `/billing`, `/start`, `/help` and free text |

Household and billing features retain the existing hosted-mode restrictions.
Owner checks, bans, quotas, provider availability, and confirmation rules remain
in the shared command handlers. Connecting a group requires its numeric Telegram
ID, a bot with access to that group, and verified group administrator status for
the current user. Operator-bot administration remains separate.

The interface supports English, Chinese, French, and Spanish. Dynamic result
content uses the same translation path as the bot. AI features require the same
configured providers as their chat equivalents. Telegram Stars checkout opens
Telegram's payment interface; calendar export downloads a file for the user to
import.

## Runtime and verification

`GET /api/workspace` returns the current user's cards and progress.
`POST /api/workspace/actions` submits a command, a server-issued button, a
correction reply, or natural-language input. `POST /api/workspace/photo` accepts
bounded image bytes. All endpoints validate signed Telegram init data; new users
can only start or redeem an invitation until they have joined a household.

The in-process workspace tracks slow jobs independently of an HTTP request,
rejects concurrent submissions, and deduplicates request IDs within the workspace.
Buttons reference server-owned cards and revisions. The browser cannot supply a
callback payload, another user's identity, or a correction reply marker. Changing
households clears old cards. Authentication and domain authorization are checked
again for subsequent requests and handler execution.

Food, plans, settings, and billing state remain in SQLite. Presentation cards and
jobs are process-local: cards expire after an hour without workspace requests,
and a restart clears them and stops unfinished work. Reopen the relevant feature
after a restart to inspect persisted state before repeating a mutation. Expired
Telegram authentication requires reopening the app from Telegram. This follows
the existing single-process deployment model; multiple web replicas require a
shared job/card store before use.

Verification commands:

```sh
uv run pytest tests/test_miniapp_workspace.py tests/test_webapp_api.py tests/test_webapp_auth.py
uv run ruff check app/miniapp_workspace.py app/webapp.py bin/run.py tests/test_miniapp_workspace.py
uv run pyright
cd web
npm run build
```

Integration tests exercise real SQLite mutations with fake external providers,
including approval and undo, recipe generation, household isolation, stale-card
rejection, calendar export, upload limits, and command-catalog coverage.
