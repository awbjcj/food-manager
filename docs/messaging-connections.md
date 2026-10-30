# Shared messaging connections

The deployment owner configures Telegram, Slack, and WhatsApp connections at
`/admin/connections`. Household members use the installed connections; they do
not provide bot credentials. Installing a provider does not grant access to food
data. Existing invitations, household roles, bans, quotas, and language settings
still apply.

## Deploy and open setup

Run one application process against one persistent SQLite volume. Set:

```dotenv
PUBLIC_BASE_URL=https://your-food-manager.example
CONNECTION_ADMIN_TOKEN=<random-owner-token-at-least-32-characters>
CONNECTION_SECRET_KEY=<Fernet-key>
DATABASE_PATH=/data/food.db
```

Generate the two secrets separately and store them in your deployment's secret
settings:

```sh
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep the encryption key with your protected database backups. Replacing it
without re-encrypting connection credentials makes those credentials unreadable.
Rotating the owner token invalidates existing console sessions. Provider
credentials can be rotated from each connection card; rotation disables the
connection until it is validated and its bootstrap account sends another message.

Start with `uv run python bin/run.py`. The console can start without an LLM key
and without Telegram. Add an LLM provider in deployment settings and restart
before using food workflows. `ALLOWED_TELEGRAM_USER_ID=0` is appropriate when
Telegram environment credentials are absent. With an environment Telegram bot
in private mode, set its real owner user ID.

Open `https://your-food-manager.example/admin/connections`, sign in with the
owner token, and create a connection. The console uses a secure HttpOnly cookie,
CSRF protection, revision checks, write-only encrypted credentials, and bounded
login/test attempts. The operator console is currently in English; bot workflows
and onboarding support English, Chinese, French, and Spanish.

## Provider setup

### Telegram

Create a bot with BotFather and enter its bot token and your numeric Telegram
user ID. Validate credentials, send the bot a direct message, refresh status,
and enable it. New managed Telegram connections use long polling. Remove any
previous webhook before using this polling deployment; do not run another poller
with the same token.

Existing `TELEGRAM_BOT_TOKEN` deployments are imported as the environment-owned
`legacy-telegram` connection without copying the token into SQLite. Existing
Telegram commands, Mini App login, Stars billing, and historical digest jobs
remain supported. The owner console can disable this connection too; the setting
survives restarts. Environment token rotation must resolve to the same bot ID.
Use a new connection for a different bot.

### Slack

Create a workspace app and enter its client ID, client secret, signing secret,
and your Slack member ID. Open **Setup instructions** on the connection card;
it supplies a manifest, webhook URL, and OAuth redirect URL for this deployment.
Configure the app, install it with **Install in Slack**, and validate credentials.
Enable messages in the app's App Home so members can send it direct messages.
Send a DM from the bootstrap member, refresh, and enable the connection.

Use direct messages or `/food pantry`, `/food add ...`, etc. For a shared
conversation, invite the bot and send `/food bind` as the household owner.
Other members must independently join the same household. Channel history scopes
let the bot receive subscribed messages only where Slack grants it access; the
application still checks the household binding and sender on every event.

HTTP events, slash commands, and interactive actions use the same connection
webhook. The server verifies the raw body signature and timestamp before parsing,
persists an event before acknowledging it, and deduplicates repeated message
subscriptions and provider retries. OAuth state is single use, session bound,
revision bound, and expires after ten minutes. Enterprise-wide installation is
not supported.

### WhatsApp Cloud API

Use Meta's official Cloud API with an eligible WhatsApp business account and
business phone number. Provide the business account ID, phone number ID, a
supported Graph API version, system-user access token, app secret, and a webhook
verification token of your choosing. The bootstrap user is your personal
WhatsApp number in digits including country code. Provider eligibility,
business verification, permissions, and template approval happen in Meta's tools.

Subscribe the app to the business account's `messages` webhooks and configure
the URL shown by **Setup instructions**. The GET verification challenge and POST
signature validation are separate checks. Validate credentials, message the
business number from the bootstrap account, refresh, and enable the connection.

Replies within the 24-hour customer service window can use ordinary text and
interactive lists. The window uses the provider's authenticated message time;
replayed old messages cannot extend it. Daily reminders require `reminders on`.
Outside the window, configure an approved reminder template per language with
one `{{1}}` body parameter for the number of items due, an optional static footer,
and no header or buttons. Credential validation checks template approval and
shape. Missing templates suppress delivery instead of attempting a free-form
message. Provider delivery receipts update accepted messages to delivered, read,
or failed when available.

Meta's current onboarding requirements must be checked in its console. Local
tests use provider fixtures; no real Slack workspace or WhatsApp business number
was connected as part of this implementation.

## Use one account on multiple providers

1. On your existing connection, send `link` in a direct conversation.
2. On the new connection, send `link CODE` **before** creating another household.
3. Return to the original connection and send `confirm_link CODE`.

Codes expire after ten minutes. An identity already attached to a different
account cannot be merged automatically. Settings, quota, household membership,
and bans belong to the internal user. Leaving or being removed revokes all linked
identities' household access, invalidates old buttons, and suppresses queued
replies. An invitation can admit an inactive identity again.

Send `reminders on` in the direct conversation that should receive your digest;
this replaces the previous destination. `reminders off` disables that destination.
Timezone and reminder hour remain per user. The durable scheduler records one
digest per user's local date. WhatsApp limitations still apply to that delivery.

Mini App authentication and Stars payments remain Telegram-specific. Link the
same account to Telegram before purchasing there; balances remain household-wide.
Managed Telegram connections currently use the shared command surface; use the
environment Telegram connection for Stars and Mini App integration.

## Delivery and recovery

Inbox admission, application execution, and delivery are separate stages.
Messages are sent from a persisted outbox. Definite rate limits and temporary
failures receive bounded retries. A timeout, partial multi-message send, or
restart during an external operation may leave its outcome unknown. Such work
is never automatically replayed: doing so could duplicate a paid LLM call or a
message. Inspect the affected operation and provider before manually repeating
the user command. Acceptance by a provider is distinct from delivery to a device.

The application currently processes one inbound command at a time and one send
at a time. Domain services still own their existing transaction commits;
membership is checked before commits and before delivery. This is conservative
recovery, not an exactly-once promise across external services. Run one replica;
distributed workers and durable per-LLM-step resumption are not implemented.

## API and migration notes

The owner API is under `/admin/api`: session creation/deletion; paginated
connection list/create; revision-checked patch, credential check, OAuth start,
and test send; setup instructions; and delivery status. Mutation requests require
`Origin`, the session cookie, and `X-CSRF-Token`. Connection mutations/checks use
`If-Match: <revision>`. Test sends require `Idempotency-Key` and can only target
the verified bootstrap conversation. Structured errors never return credentials.
Credentials omitted from PATCH are retained; null fields are rejected. A
connection's verified provider account is immutable.

Migrations `385cb579c777` and `a5b28e451343` preserve legacy Telegram user IDs as
internal integer IDs, introduce independently generated IDs for new users, and
backfill identities, conversations, and notification destinations. New Slack and
WhatsApp users have no fabricated Telegram ID. Historical billing payloads and
actor references remain unchanged. The application creates a pre-migration
backup. Roll back database and application together after new messaging writes;
the downgrade guard refuses to discard those identities and queues.

Implementation references: `app/application/context.py`, `app/messaging/`,
`app/connection_admin.py`, and `tests/test_messaging*.py`.

Provider references: [Slack request verification](https://docs.slack.dev/authentication/verifying-requests-from-slack/),
[Slack OAuth](https://docs.slack.dev/authentication/installing-with-oauth/),
[Meta Cloud API samples](https://github.com/fbsamples/whatsapp-api-examples),
[Pillow image validation](https://pillow.readthedocs.io/en/stable/reference/Image.html).
