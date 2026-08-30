# Deferred Backlog

**Last audited:** 2026-08-29, against feature branch at `cf059e3`.

Every spec in `docs/superpowers/specs/` carries a non-goals section listing what
it deliberately did not build. Those deferrals were scattered across seventeen
documents, and the plan files' `- [ ]` checkboxes were never ticked (0 checked
across all 21 plans), so completion could not be read from the docs at all — each
item below was verified against source.

This file is the standing answer to "what did we say we'd do later?" Update it
when a release opens or closes a deferral.

---

## Open

### 1. Email / Gmail receipt connector

**Source:** `2026-05-26-food-manager-v1-design.md` §11.2 (with §11.2.6 scoping the
connector's own non-goals).
**Status:** not built. The v1 spec notes the text-extraction pipeline was shaped
to serve future modalities, so the ingest side is reusable; the connector,
polling, and credential storage are not.

### 2. Postgres migration

**Source:** `2026-07-28-v6.0-multi-tenant-commercialization-design.md` §3, §12.
**Status:** deliberately deferred, with a stated trigger. §12 records that no raw
SQL exists anywhere, so this remains a connection-string change; the one thing
that forces it is needing two processes on separate machines, which is precisely
why the operator bot is co-located in the same process.

### 3. Anti-abuse beyond `/ban`

**Source:** v6.0 §3, §13.
**Status:** accepted risk, not an oversight. §13 reasons that a free household
costs roughly $0.06/month to serve, so farming throwaway accounts costs more
effort than it returns, and phone verification is disproportionate at this stage.
Revisit only if observed abuse contradicts that arithmetic.

### 4. Additional paid tiers, proration, annual billing

**Source:** v6.0 §3.
**Status:** deferred by design. `PlanTier` and the `plans.py` catalog are shaped
so a second tier is data rather than code. Stars subscriptions are a fixed 30-day
period; partial periods are unmodelled.

### 5. Open-ended user-typed languages

**Source:** `2026-05-31-v4.1-multilanguage-design.md` §146.
**Status:** `LANGS` is fixed at `("en", "zh", "fr", "es")` (`app/i18n.py:8`). The
render-time translation architecture leaves room for more; the static `MESSAGES`
catalog is what bounds it.

### 6. Cross-session recipe cache

**Source:** `2026-06-10-v4.9-recipe-engine-design.md` §324.
**Status:** deferred on the reasoning that source quota is ample. Each `/cook`
and `/plan` search hits the source chain fresh.

### 7. Migrating existing seams to Agno

**Source:** `2026-07-08-v5-roadmap-design.md`, Agno adoption boundary.
**Status:** deliberately out of scope and requires its own design decision. Agno
is used only for new agentic seams (`nl_intent.py`, `week_composer.py`); the
hand-rolled `ProviderSelector` seams stay as they are because a migration has no
user-visible payoff.

---

## Closed since being deferred

Recorded because the specs still read as though these are open, and two of them
are still described as unbuilt in `CLAUDE.md`.

| Deferral                              | Closed by                                     | Evidence                                                                                     |
| ------------------------------------- | --------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Real recipe-source chain (v4.9)       | wired into `/cook` and `/plan`                | `bin/run.py:394` builds `SpoonacularSource`; `handlers/cook.py:174`, `handlers/plan.py:60`   |
| Affinity learning (v3.6 → v5.3)       | v5.3                                          | `cook/affinity.py` → `cook/service.py:195`, `plan_service.py:217`                            |
| Waste/consumption analytics (v5.4)    | v5.4, in `pantry_service.py` not `stats_service.py` | `pantry_service.py:275-384`, `renderer.py:652`                                          |
| `qty`/`unit` via `/correct` (v1.5)    | shipped                                       | `correction_service.py:37-38`                                                                |
| Actionable shopping list (v3 non-goal) | v3.5                                          | `app/shopping_service.py`, `/shopping`                                                       |
| `bot.py` decomposition                | architecture deepening, 2026-07-17            | `app/handlers/`, `app/callbacks/`, `views.py`, `client_set.py`, `telegram_ui.py`             |
| Recipe dedup (deferred since v3.5)    | v5.5                                          | `cook/novelty.py` as a fifth `blended_score` term                                            |
| Meal-plan digest line + cooked marking (v5.2 §5) | v5.5                               | `CookedMeal`, `digest.tonight`                                                               |
| NL `plan` intent (v5.2 §5)            | v5.5                                          | `NLIntent.kind` gains `cook`, `plan`, `cooked`                                                |
| Group-chat households (v4.0)          | 2026-08-29                                    | `GroupBinding`, `/bind`, group-aware command/callback authorization, private-only receipts   |
| Ad-hoc cook marking + history          | 2026-08-29                                    | `cookmade:*`, `CookedMeal.source="cook"`, `/history`                                        |
| Receipt auto-clear of shopping items   | 2026-08-29                                    | `ingest_photo` atomically calls `check_off_purchased_names`                                   |
| NL correction + shopping operations    | 2026-08-29                                    | `NLIntent.kind` supports `correct` and add/remove/show shopping operations                    |
| Active-plan calendar export            | 2026-08-29                                    | `/calendar` emits a standards-compatible `.ics` file                                          |

---

## Standing rejections

Not deferrals — decisions that should stay decided unless the premise changes.

- **Per-member quota and per-row actor attribution** (v4.0 §3, v6.0 §3).
  Ownership is the household; the bot does not track which member did what.
- **A web dashboard** (v6.0 §3). Everything is in Telegram.
- **Non-Stars payment rails** (v6.0 §3). The `PaymentProvider` Protocol makes a
  second rail additive if the economics change.
- **The shared JSON-repair helper** (architecture deepening, Track 5). Live
  inspection disproved its safety gate: each provider's repair tail has a
  different observable contract, so abstracting the loop would hide real
  differences without adding depth.
