# Agno 3.1.3 compatibility review

Reviewed on October 15, 2026. [PyPI](https://pypi.org/project/agno/3.1.3/)
lists 3.1.3 as the latest stable release, published October 12. The runtime
requirement is now `agno>=3.1.3,<4.0`; `uv.lock` upgrades only Agno from 3.1.2
to 3.1.3. Provider SDK versions stay locked as before.

## Capabilities used by this project

Agno powers `build_intent_agent` and `build_week_composer`. Both reuse a native
`Agent` per configured provider, request typed Pydantic output, and leave
database operations, authorization, quota admission, and Telegram interaction
to application services. Receipt recognition and recipe generation use their
existing provider clients, rather than Agno agents.

The current [structured-output API](https://docs.agno.com/input-output/structured-output/agent)
supports the existing native schemas for Anthropic, OpenAI, and Gemini, and
JSON mode with schema-guided parsing for DeepSeek. The upgrade preserves those
capabilities and the API/Sub2API endpoint selection for each provider.

Each classification and composition now supplies a fresh session ID. Agno
[otherwise reuses the session ID on an agent instance](https://docs.agno.com/sessions/overview).
These are independent requests from potentially different households, so
explicit session IDs keep sequential and concurrent runs separately identified
while retaining provider-client reuse. No conversation history or agent database
is enabled.

## New release features assessed

The [3.1.3 release notes](https://github.com/agno-agi/agno/releases/tag/v3.1.3)
primarily extend the external `ClaudeAgent` and `CodexAgent` integrations:
durable transcripts, background execution and cancellation, retries, metrics,
compaction, checkpoint replay, structured output, and attachments. These are
different from this project's native `Agent` with a Claude/OpenAI model adapter.
They do not automatically enhance the bot's current agents. Adopting them would
require a separate SDK/CLI runtime and a deliberate change to the provider and
execution architecture; they are not enabled by this upgrade.

The new Anakin web toolkit is not needed by the two structured-output agents.
Shelf-life search already has a dedicated provider-routed service. AgentOS
routes, durable queues, filesystem storage, and MCP serving are also unused.
No Agno database migration is required for this project.

## Regression coverage

`tests/test_agno_runtime.py` runs the real Agno agents and installed provider
SDKs against local HTTP fixtures, covering:

- Both agents and all four providers, with direct and Sub2API endpoints.
- Serialized native schema requests and DeepSeek JSON mode; typed responses.
- Invalid JSON, schema-invalid objects, and authentication errors, which must
  fail without returning an actionable intent or plan.
- Sequential and concurrent calls on a reused agent: distinct sessions and
  exactly one current user message per request.

The Anthropic tests use `httpx2.MockTransport`; the other adapters accept
`httpx.MockTransport`. Mocking only HTTP keeps SDK serialization and Agno
parsing in the test path. Existing service and handler tests cover downstream
fallbacks, pantry operations, recipes, plans, billing, households, and rendering.

These checks establish local compatibility, not live provider availability or
model response quality. Live Telegram/provider acceptance testing and deployment
are separate from this dependency upgrade.

## Verification results

- Python suite: 1,022 passed (980 existing tests plus 42 runtime contract tests).
  The 42 runtime tests also passed separately with the repository's current
  default model IDs; both concurrency cases passed after removing an assertion
  that depended on request arrival order.
- Ruff and repository-wide Pyright (including tests): passed; Pyright reported
  zero errors and warnings. CI now checks the same repository-wide scope.
- Mini App: 3 unit tests and 17 browser tests passed; production build passed.
- Alembic: all migrations applied successfully to a new temporary SQLite DB.
- Python wheel/source distribution and production Docker image: built successfully.
- Linux container with networking disabled: bootstrap imports and all eight
  provider/agent combinations constructed successfully with Agno 3.1.3.
- Lockfile consistency, installed dependency compatibility, and Python dependency
  audit: passed; no known Python dependency vulnerabilities reported.

The Docker build's npm audit exposed one existing high-severity advisory for
`source-map-js` 1.2.1 ([GHSA-68fv-2mgg-jv7q](https://github.com/advisories/GHSA-68fv-2mgg-jv7q)).
The frontend lockfile was unchanged by this Agno upgrade; the same version was
present in the baseline. That separate frontend dependency update remains open.
