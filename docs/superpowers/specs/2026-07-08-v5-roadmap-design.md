# Food Manager v5 Roadmap

**Date:** 2026-07-08
**Status:** Approved
**Type:** Master roadmap — sequences the next seven releases across four workstreams
(stability/performance, planned features, Agno-powered agentic features, usability).
Each release runs its own spec → plan → implement cycle; this document fixes scope
boundaries and ordering, not implementation detail.

## Motivating pains (as experienced today)

- **Stability/performance:** slow LLM responses with no progress feedback; silent
  failures (a missed digest is discovered days later); occasional crashes/hangs
  requiring manual restart. LLM cost is *not* a pain point.
- **Usability:** command friction (many slash commands with picky syntax), no
  progress feedback after sending a receipt photo, hard to get a quick pantry
  overview, and `/help` is a wall of text for new household members.
- **Feature backlog:** v4.8 (pantry action interface) and v4.9 (recipe engine)
  have committed specs + plans but are unbuilt; new ideas approved for the
  roadmap: natural-language input, meal planning, affinity learning, richer
  recipe media, waste/consumption analytics.

## Release sequence

| Release | Theme | Plan status |
|---|---|---|
| v5.0 | Stability & Feedback | needs plan (this doc is its spec seed) |
| v4.8 | Pantry action drill-down interface | plan committed — refresh, then execute |
| v4.9 | Recipe engine overhaul | plan committed — refresh, then execute |
| v5.1 | Agno natural-language input + onboarding/help | needs spec |
| v5.2 | Meal planning (Agno workflow over v4.9 engine) | needs spec |
| v5.3 | Affinity learning + recipe quality/media | needs spec (revive v3.6 design) |
| v5.4 | Waste/consumption analytics | needs spec |

**Housekeeping before v5.0:** commit the pending working-tree changes
(Gemini 3.5-flash model bump in `app/settings.py`, `app/i18n.py`, `CLAUDE.md`).

## v5.0 — Stability & Feedback

Smallest release; every later release benefits. Scope:

1. **Progress acknowledgments.** On photo ingest, `/add` with web refine, and
   `/cook`, immediately send an ack message ("📸 Reading your receipt…") plus the
   Telegram "typing" chat action, then edit the ack into the final result. Reuse
   the ack-first pattern from `app/callback_dispatch.py`. This addresses
   *perceived* LLM slowness directly.
2. **Owner error alerts.** An aiogram error middleware plus a digest-send failure
   hook DM the bootstrap owner (`ALLOWED_TELEGRAM_USER_ID`) a rate-limited alert
   with the error summary. Telegram is the alerting channel; no external
   monitoring stack.
3. **Digest watchdog + catch-up.** Persist a per-user `last_digest_sent` marker.
   On startup, if a user's digest hour has passed today and no digest was sent,
   send it late. A crash converts "digest lost" into "digest delayed."
4. **Crash resilience.** In-process: wrap polling in a restart-with-backoff loop
   in `bin/run.py`. Out-of-process: document a supervision setup (restart-on-exit)
   for the hosting environment. The single-process + SQLite + re-register-on-start
   architecture (ADR 0001) is unchanged.
5. **Timing telemetry.** Per-LLM-call timing logs so later latency optimization
   works from data. No optimization work in v5.0 itself.

## v4.8 and v4.9 — execute the committed plans

Ship in order (v4.8 → v4.9). Both plans predate the v4.7 multi-provider layer, so
each starts with a **plan-refresh checkpoint**: a pass over the committed plan to
update references to pre-v4.7 seams (single-provider assumptions, renamed
selectors) before execution begins. Scope changes beyond mechanical refresh go
back through a design conversation.

## Agno adoption boundary (applies to v5.1+)

[Agno](https://docs.agno.com) is adopted **only for new agentic seams**. The
existing `ProviderSelector` seams (image, text, profile, cook, translation,
search) stay hand-rolled — v4.7 shipped recently, is tested, and a migration has
no user-visible payoff. Rules:

- Agents are constructed once at bootstrap (never per message).
- Agent persistence uses Agno's `SqliteDb` on the same volume as `food.db`.
- Agent model selection honours `User.llm_provider` where that provider supports
  the needed capability, mirroring the existing selector-fallback semantics.
- Agents parse and decide; **all reads/writes go through existing service
  functions** (session injection, explicit `today`). An agent never touches the
  DB or Telegram directly. Tests fake the agent the same way `FakeLLMClient`
  fakes the LLM protocol.
- Migrating an existing seam to Agno requires its own future design decision;
  nothing in v5.1–v5.4 depends on such a migration.

## v5.1 — Natural-language input + onboarding

- A plain-text message handler backed by an Agno agent with a structured-output
  intent schema: "bought milk and two avocados" → ingest, "ate the yogurt" →
  mark eaten, "how long does salmon keep?" → shelf-life answer. Each intent
  dispatches to existing services. Unrecognized text gets a gentle help hint.
  Slash commands remain and keep working; natural language becomes the default
  path. (Kills the command-friction pain.)
- Tiered `/help` (short overview → per-topic detail) and a `/start` onboarding
  tour for new household members.

## v5.2 — Meal planning

An Agno Workflow over the v4.9 recipe engine: plan N days of meals from pantry
contents + food profile, aggregate a single shopping list. Depends on v4.9;
detailed scope brainstormed when its turn comes.

## v5.3 — Affinity learning + recipe quality/media

Revive the locked v3.6 affinity-learning design (learn household taste from
`/cook` liked/disliked feedback and bias selection), refreshed against the v4.9
engine, plus richer recipe output (images, source links) drawn from the v4.9
recipe sources. Detailed scope brainstormed when its turn comes.

## v5.4 — Waste/consumption analytics

Eaten-vs-tossed stats and trends. Current status changes overwrite history, so
this likely needs a lightweight event log; that is a design question for its
spec, not settled here.

## Process

- Order is fixed as listed; a release starts only when the previous one has
  merged.
- v5.0 proceeds directly to an implementation plan using this document's v5.0
  section as its spec. v5.1–v5.4 each get a design conversation + spec first.
- v4.8/v4.9 skip straight to plan-refresh → execute.
