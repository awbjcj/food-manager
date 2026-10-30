# Messaging provider rollout

**Status:** Implemented on dev, 2026-10-30; local provider fixtures and Telegram regression tests pass. Live provider acceptance remains a deployment step. See [setup and recovery](../../messaging-connections.md).

**Implementation decisions:** The stable internal user key is an independent integer, retaining historical numeric actor references; new providers never receive fabricated Telegram IDs. Legacy Telegram handlers and Stars remain behind compatibility adapters while managed connections use the normalized command runtime. Domain services retain their existing commits with membership revalidation. Interrupted paid operations stop as unknown instead of being automatically replayed; durable per-step LLM resumption is deferred. New Telegram connections use polling. Connection credentials are configured in the owner console; existing Telegram environment credentials remain supported. The setup console is English; shared bot onboarding supports all four existing locales. These choices and rollback boundaries are documented in the setup guide.
**Design:** [Messaging providers and deployment-owned connections](../specs/2026-10-29-messaging-provider-abstraction-design.md).
**Owner decision:** Connections belong to the deployment owner and are shared by
households. Do not build customer-managed installations in this rollout.

## 1. Extract the application boundary with Telegram compatibility

Add `app/messaging/contracts.py` and a fake adapter for behavior tests. Define
typed ingress, addresses, media, actions, content, delivery results and error
codes from the design. Keep messaging-provider names separate from LLM providers.

Extract one complete vertical path first: pantry list → item card → mark eaten.
Move authorization and orchestration into `app/application/commands/`; adapt
Telegram input and output at the existing handler entry points. Preserve public
imports while callers migrate. Keep `Session` and explicit dates injected.

Then migrate receipt ingest, add/correct and pending confirmation, cooking,
shopping/favorites, plans, household membership, preferences and natural-language
input. Audit callbacks and progress helpers for Telegram-shaped duck typing,
not just `aiogram` imports. Adapt existing `CallbackButton` values while the
renderers transition; preserve English text and old callback decoders.

**Likely files:** `app/handler_support.py`, `app/handlers/`, `app/callbacks/`,
`app/bot.py`, `app/callback_dispatch.py`, `app/progress.py`, `app/renderer.py`,
`app/views.py`, `app/telegram_ui.py`, `app/miniapp_workspace.py`.

**Exit evidence:** Fake transport executes the vertical path with no Telegram
objects. Existing Telegram command/callback/render tests pass. No live Slack or
WhatsApp support is claimed at this stage.

## 2. Introduce neutral identity and addresses

Use additive migrations before changing the `User` primary key. Read the
repository's `create-migration` skill when implementing actual migrations.

1. Back up a database fixture using the existing backup mechanism. Create a
   stable legacy Telegram connection identity locally without network calls in
   the migration; verify its external bot identity at runtime before activation.
2. Add and backfill opaque `User.id` values plus external identity and direct
   destination rows. Keep `telegram_id` as the legacy primary key during the
   expansion step. Add new actor/address columns beside existing ones.
3. Inventory both foreign keys and plain integer provenance: `GroupBinding`,
   `PantryBatch`, `PantryOutcome`, `HouseholdInvite`, pending corrections,
   cook sessions, meal plans, Mini App payloads/workspace calls, scheduler IDs,
   operator queries, and receipt deduplication. Map existing actors explicitly.
   Historical deleted actors retain qualified legacy provenance; do not invent
   active memberships to satisfy a backfill.
4. Move lookups to `ExternalIdentity` → internal `User.id`. During transition,
   Telegram entry points use an explicit compatibility resolver instead of
   assuming `session.get(User, telegram_id)` still works. New writes populate
   both old and new fields only where Telegram actually supplies old values.
5. In a coordinated release, rebuild the SQLite user table with internal ID as
   primary key; make legacy Telegram address fields nullable, migrate foreign
   keys and enforce identity uniqueness. Gate non-Telegram registration until
   all required constraints and readers have moved.
6. Change leave/removal to revoke the user's membership and destinations across
   all linked identities while retaining identity/ban state. Central auth must
   distinguish intentionally unassigned users from missing-household recovery.
   Migrate invitations, shared-conversation bindings and local bootstrap policy.
7. Add verified account-linking and destination selection. Reject automatic
   merges of two established households. Preserve Stars charge/payer fields and
   resolve operator Telegram inputs through the compatibility boundary.

**Exit evidence:** Migration tests run against realistic fixtures with multiple
households, groups, deleted actors, invitations, active pending work, batches,
paid subscriptions and duplicate receipts. Validate foreign keys, row counts,
permissions, rollback boundaries and byte-identical historical ledger contents.
Slack-style string IDs can exist without a fabricated Telegram ID.

**Rollback boundary:** Before non-Telegram writes, rollback can restore a backed-up
database and compatible application version. After new-provider writes, the old
Telegram-only binary is not a safe downgrade. Use a forward repair or a coordinated
restore that explicitly accounts for later data; never promise a lossless downgrade.

## 3. Add durable event admission and delivery

Implement inbox uniqueness, leases, bounded retention and crash recovery in
SQLite. Add an outbox and operation identifiers. Refactor service commits where
needed so command completion, mutations and outbound intent commit together.
Persist paid LLM stage outcomes separately from transport retries; retain current
quota accounting semantics and report uncertain attempts accurately.

Move digest delivery out of `app/scheduler.py` into the messaging delivery service.
Key scheduling by internal user and the logical run by user/local date; choose
one verified opted-in destination. Preserve silent days and restart catch-up.
Recheck bans, membership generations and connection status before every send.

**Exit evidence:** Meaningful fault-injection tests for duplicate ingress,
concurrent callback actions, restart after admission, crash after mutation,
crash after provider acceptance, rate limits, revoked credentials, opt-out,
membership removal, cron/catch-up overlap and a changed digest destination.
No assertion promises exactly-once external delivery.

## 4. Ship deployment connection setup

Separate core settings from optional provider settings and runtime construction.
Preserve the Telegram environment names and import them once. Add setup-only
startup, conditional provider validation and independent adapter supervision.
Reuse the existing aiohttp listener and existing frontend build where suitable;
register API/webhook routes before the SPA fallback.

Implement deployment-admin sessions, CSRF protection, versioned connection
updates, secret storage, checks and explicit test sends. Build the setup page
around tasks: choose provider, install, verify, receive test, identify owner,
enable, rotate or disable. Surface credential source and actual inbound/outbound
status. Keep `/healthz` compatible and add readiness plus private diagnostics.

**Exit evidence:** Fresh deployment can open setup with no Telegram credentials.
Household-owner and Mini App credentials cannot access admin routes. Secrets are
not returned in reads, validation messages or logs. Rotation preserves a verified
account's identity and isolates restart to its adapter. Environment import and
wizard records have deterministic ownership after restart.

**Delivery docs:** Update `.env.example`, README and local/hosted operations
guides, including the Chinese counterparts, only when the associated behavior
ships. Include setup URLs, bootstrap policy, key backup/rotation and the
single-process deployment constraint. Update host templates only for implemented
configuration. Proposed variables must not appear as current support prematurely.

## 5. Prove the design with Slack

Implement a ready-to-import manifest, admin-owned OAuth installation and the HTTP
event/action receiver. Verify signatures, timestamps, installation identity and
response shapes. Persist ingress before fast acknowledgement. Normalize Slack
commands, direct messages, private image media and action responses into the
shared contracts; render replies through Block Kit with plain-text fallback.

Start with one workspace per connection and direct conversations. Once the shared
authorization tests pass, add explicit household-bound channels and thread-aware
replies. Workspace membership alone must never authorize pantry access. Ignore
own-bot events and ensure one message delivered through multiple subscribed event
types does not trigger duplicate application work.

**Exit evidence:** The same application behavior tests run for Telegram and Slack.
In a real test workspace, verify installation, bootstrap, photo ingest, pantry
actions, reminders, token revocation, reinstall and duplicate-event delivery.
Record which evidence is mocked and which was accepted by Slack.

## 6. Add WhatsApp Cloud API

Recheck current official Meta documentation for the selected supported Graph API
version, account onboarding, consent, media and template rules. Resolve the
documentation access limitation recorded in the design before promising exact
setup steps or eligibility. Use the official Cloud API integration.

Implement GET verification, signed POST ingress, per-message batch handling,
media download and status callbacks. Direct conversations only in the first
release. Normalize text commands and interactive replies; use lists or explicit
text choices when actions exceed supported limits. All choices still map to
persisted scoped actions and current authorization checks.

Add customer-service-window tracking, notification opt-in/out, locale-specific
template mappings and suppression reasons. The setup page must distinguish
credentials validated, inbound verified, and proactive reminders ready.

**Exit evidence:** Live test sender receives a receipt and completes a pantry
action; tested retries do not duplicate pantry rows. Verify both an in-window
reply and an approved-template reminder outside the window, plus missing-template,
wrong-locale and opted-out cases. Provider approval and delivery are external
acceptance steps, not conclusions drawn from unit tests.

## Release gates

Run focused tests during each change, then the repository-required suite and
type/lint checks before integration. Use contract tests for shared application
behavior, provider fixtures for payload handling, and live smoke tests for each
enabled adapter. Do not write tests that only mirror dataclass field definitions.

Each release must report database migration status, Telegram regression results,
new-provider capability coverage, local versus live test evidence, and remaining
provider restrictions. Enable adapters individually; a successful Slack release
does not imply WhatsApp onboarding or billing parity.

This plan does not add non-Telegram payments, general web login for other
providers, self-service household bot credentials, cross-household account merges,
multi-replica deployment, or a Slack Marketplace launch. Those are independent
product decisions and should not delay the shared messaging boundary.
