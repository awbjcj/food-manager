# Food Manager v4.0 — Tenancy Overhaul (Households, Multi-User & Group Chat)

**Status:** Design approved, ready for implementation planning
**Date:** 2026-05-31
**Author:** awbjcj (with Claude)
**Builds on:** the single-user bot shipped through v3.5 (`2026-05-30-cook-v3.5-design.md`)
**Followed by:** v4.1 Chinese internationalization — a *separate* spec, deliberately deferred so this data-model migration lands and stabilizes first.

## 1. Summary

Today the bot is **single-tenant by construction**: `User.telegram_id` is the primary
key and *every* table foreign-keys to `user.telegram_id`. There is no entity above the
user that could own shared data, auth is a single `ALLOWED_TELEGRAM_USER_ID`, and only
private chats are accepted.

v4.0 introduces a **`Household`** that owns all shared data, lets multiple users join a
household by **invite code**, and allows the bot to be driven from an **authorized
group chat**. The daily digest becomes a shared-pantry digest delivered to **each
member's own DM at their own hour/timezone**.

This release is a **tenancy migration**, not a feature grab. Chinese i18n, member
removal, multi-household membership, and per-row actor attribution are explicitly
**out of scope** (§9).

## 2. Goals

- A `Household` entity owns the pantry and all history; users belong to **exactly one**
  household (1:1).
- New members join self-service via **single-use, expiring invite codes** — no redeploy.
- The bot works in an **authorized group chat** for commands and the digest; receipt
  **photos stay in DM** (privacy mode stays ON).
- The daily digest reflects the **shared** pantry and is pushed to **every member**, each
  in their own private chat at their own configured hour/timezone.
- Cooking uses **one shared household food profile**.
- The existing single user migrates losslessly into a "household of one."

## 3. Non-goals (explicit; deferred)

- **Chinese / i18n** — its own v4.1 spec.
- **Leaving / removing members, unbinding a group** — no lifecycle teardown in v4.0.
- **Multi-household per user** — membership is strictly 1:1.
- **Per-row actor attribution** (`added_by` / `eaten_by`) — ownership is the household
  only; we do not track which member performed an action.
- **A digest post to the group chat** — delivery is per-member DM only.
- **Group-membership-derived authorization** — being in the group is *not* enough; the
  sender must be a roster member of the bound household.

## 4. Tenancy model (the foundational decision)

```
Household (id, name, + shared food profile)
   | 1
   | owns
   | N
PantryItem · Receipt · ShelfLifeCache · PendingCorrection
CookSession · ShoppingList · SavedRecipe
   ^ ownership FK:  user_id  ->  household_id

User (telegram_id PK)  --household_id-->  Household   (belongs to exactly one)
GroupBinding (chat_id PK) --household_id--> Household  (a group chat acts on one household)
HouseholdInvite (code)    --household_id--> Household  (single-use, expiring)
```

- **Ownership** of shared data is the household. `user_id` as an *ownership* column is
  removed from shared tables and replaced by `household_id`.
- `User` stays keyed by `telegram_id` and keeps **per-person** settings; it gains
  `household_id`.
- A group chat is bound to **one** household; private chats resolve the user's household.

## 5. Data model — one Alembic migration (`0007`)

### 5.1 New table `Household`
| column | type | notes |
|---|---|---|
| `id` | Optional[int] PK | |
| `name` | str | default `"My Household"` |
| `diet` | str | moved from `User`, default `"none"` |
| `exclusions_json` | str | moved from `User`, default `"[]"` |
| `preferred_cuisines_json` | str | moved from `User`, default `"[]"` |
| `max_cook_minutes` | Optional[int] | moved from `User` |
| `household_size` | int | moved from `User`, default `1` |
| `profile_note` | str | moved from `User`, default `""` |
| `created_at` | datetime | |

### 5.2 New table `HouseholdInvite`
| column | type | notes |
|---|---|---|
| `id` | Optional[int] PK | |
| `code` | str | unique, indexed; short URL-safe token |
| `household_id` | int FK household.id, index | |
| `created_by_user_id` | int FK user.telegram_id | who minted it |
| `created_at` | datetime | |
| `expires_at` | datetime | default +24h |
| `redeemed_by_user_id` | Optional[int] FK user.telegram_id | set on redeem |
| `redeemed_at` | Optional[datetime] | set on redeem (single-use) |

A code is redeemable iff `redeemed_at is None` and `expires_at > now`.

### 5.3 New table `GroupBinding`
| column | type | notes |
|---|---|---|
| `chat_id` | int PK | the Telegram group chat id |
| `household_id` | int FK household.id, index | |
| `bound_by_user_id` | int FK user.telegram_id | |
| `created_at` | datetime | |

One row per group chat → its household. (Re-binding is an upsert; unbind is out of scope.)

### 5.4 `User` — changed
- **Gains:** `household_id: int FK household.id, index`.
- **Keeps (per-person):** `telegram_id` (PK), `chat_id`, `tz`, `digest_hour`,
  `llm_provider`.
- **Drops (moved to `Household`):** `diet`, `exclusions_json`,
  `preferred_cuisines_json`, `max_cook_minutes`, `household_size`, `profile_note`.

### 5.5 Re-keyed shared tables
`user_id → household_id` (FK `household.id`) on: `PantryItem`, `Receipt`,
`PendingCorrection`, `CookSession`, `ShoppingList`, `SavedRecipe`.

`ShelfLifeCache`: composite PK changes from `(user_id, normalized_name)` to
`(household_id, normalized_name)`.

Every composite index currently leading with `user_id` is rebuilt to lead with
`household_id` (e.g. `ix_pantry_user_status_expires` →
`ix_pantry_household_status_expires`, and the category variant; the cook/pending
indexes likewise).

### 5.6 Migration steps (SQLite-aware)
1. Create `household`, `householdinvite`, `groupbinding`.
2. Add nullable `household_id` to `user`. For each existing user: create a `Household`
   (copying the six profile fields), set `user.household_id`.
3. Add nullable `household_id` to each shared table; backfill via the row's old
   `user_id → user.household_id`.
4. Recreate indexes with `household_id` leading; drop old `user_id`-led indexes.
5. Drop the old `user_id` columns from shared tables and the six moved columns from
   `user`; change `ShelfLifeCache` PK.

> **SQLite cannot `DROP COLUMN` or alter a PK in place** — steps 3–5 use Alembic
> `batch_alter_table` (table rebuild + copy). The existing `pre_migration_backup`
> (`bin/run.py`) already snapshots the DB before `alembic upgrade head`, so a failed
> rebuild is recoverable.

## 6. Authorization & membership

### 6.1 Bootstrap owner
Env `ALLOWED_TELEGRAM_USER_ID` is retained as the **single bootstrap owner**. On their
first `/start`, if they have no household, create a household-of-one and assign them.
(No multi-ID allowlist — everyone else joins by code.)

### 6.2 `authorize_and_get_user` rewrite
Returns an `AuthDecision` that now carries the resolved **household** as well as the
user, and accepts group chats. Resolution:

- **Private chat:**
  - Sender is the bootstrap owner with no household → provision household-of-one → allow.
  - Sender already a household member → allow (resolve their household).
  - Otherwise → reject with: *"ask a household member for an invite code, then
    `/join <code>`."* (`/join` and `/start` themselves are reachable so a code can be
    redeemed.)
- **Group chat:**
  - Look up `GroupBinding` by `chat.id`. No binding → reject (*"run /bind from a
    household member to use me here"*).
  - Binding found → sender **must be a member of that bound household**; non-members are
    rejected. Resolve to the bound household (the group binding is authoritative here,
    independent of the sender's private-chat context).

### 6.3 New commands
| command | where | effect |
|---|---|---|
| `/invite` | DM | member mints a single-use code (default 24h); replies with the code + redemption hint |
| `/join <code>` | DM | non-member redeems a valid code → sets their `household_id`; idempotent friendly errors for bad/expired/used codes and already-a-member |
| `/bind` | group | a member binds the current group chat to their household (upsert) |

## 7. Group chat routing

- `_guard` is extended to handle `chat.type in {group, supergroup}` via §6.2.
- **Photos in a group** are rejected with *"send receipts to me in a private chat."*
  Bot **privacy mode stays ON** (BotFather default) — the bot only sees its slash
  commands, @mentions, and replies in groups, never ordinary chatter or photos.
- All mutating/listing commands (`/list`, `/add`, `/cook`, `/shopping`, `/favorites`,
  `/ate`, `/toss`, `/snooze`, `/correct`, `/delete`, `/stats`) work in a bound group,
  acting on that group's household.
- Per-user settings commands (`/tz`, `/digest_at`, `/llm`) still apply to the *sender*
  even when issued in a group (they configure that person's DM digest).

## 8. Daily digest

- **Scheduling is unchanged**: one per-user cron job keyed by `telegram_id`, at the
  user's `digest_hour` in the user's `tz` (existing `schedule_user_digest`,
  `register_all_user_digests`).
- **Only the data source changes**: the job resolves `user → household → due items`, so
  `build_digest_payload` / `list_digest_due` query by `household_id`. Two members of one
  household each receive the same shared pantry's digest in their own DM at their own
  hour. Digest action buttons (ate/toss/snooze) operate on the shared household items.
- No group-chat digest post in v4.0.

## 9. Components / files touched

| file | change |
|---|---|
| `app/models.py` | new `Household`, `HouseholdInvite`, `GroupBinding`; re-keyed FKs/indexes; trimmed `User` |
| `app/household_service.py` *(new)* | create household-of-one; mint/redeem invite (validity rules); bind group; resolve user→household |
| `app/bot.py` | `authorize_and_get_user` + `_guard` rewrite (household + group); `/invite`, `/join`, `/bind` handlers; group routing; photo-in-group rejection; dispatcher registration |
| `app/pantry_service.py`, `ingest_service.py`, `cook_service.py`, `cook_session_service.py`, `shopping_service.py`, `favorites_service.py`, `cache.py`, `pending_service.py`, `correction_service.py`, `cook_feedback.py` | ownership param `user_id → household_id` on all shared-data paths |
| `app/profile_service.py` | read/write the food profile on `Household` rather than `User` |
| `app/scheduler.py` | digest job resolves household before querying due items |
| `app/settings.py` | `ALLOWED_TELEGRAM_USER_ID` semantics documented as *bootstrap owner* (no schema change) |
| `migrations/` | migration `0007` (§5.6) |

Service signatures changing `user_id → household_id` is the **bulk of the work** and the
**bulk of the test churn**.

## 10. Edge handling

| case | handling |
|---|---|
| Non-member DMs the bot | reject with invite-code instructions; `/join`/`/start` still reachable |
| `/join` with bad / expired / already-used code | distinct friendly messages; no state change |
| `/join` when already in a household | *"you're already in a household"* (no switch in v4.0) |
| `/invite` by a non-member | rejected by `_guard` like any other command |
| Command in an **unbound** group | *"run /bind from a household member first"* |
| Command in a bound group by a **non-member** | rejected (roster-only) |
| Photo posted in a group | *"send receipts to me in a private chat"* |
| Bootstrap owner first contact | auto-provision household-of-one |
| Two members, one shared pantry | each gets a DM digest at their own hour; buttons act on shared items |

## 11. Testing

Service/pure-function tests with injected `now`/`today`, mirroring existing
`tests/test_*.py` patterns:

- **Household isolation:** household A cannot read/mutate B's pantry, cache, cook
  sessions, shopping list, or favorites (every re-keyed service).
- **Invite/join:** mint code; redeem (single-use → second redeem fails); expiry;
  unknown code; already-a-member; redeemer's `household_id` set correctly.
- **Group binding & auth:** bind sets the row; member command in bound group resolves
  the household; non-member command rejected; command in unbound group rejected; photo
  in group rejected.
- **Auth resolution:** bootstrap owner auto-provisioned; non-member DM rejected with
  hint; member resolved to correct household.
- **Digest:** two members of one household are each scheduled and each DMed the shared
  household's due items; digest button actions mutate shared items.
- **Migration:** seed old-shape rows (user + per-user pantry/cache/cook), run migration,
  assert a household-of-one per user, profile fields copied to `Household`, and every
  shared row backfilled to the right `household_id`.
- **Existing-test migration:** update the `user_id`-keyed fakes/fixtures across the
  suite to `household_id` (large but mechanical).

## 12. Provider / cost note

No new LLM stages and no change to existing prompts in v4.0. All additions are
deterministic Python over the database. (LLM **output language** is a v4.1 concern.)
