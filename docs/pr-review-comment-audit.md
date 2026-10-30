# Historical PR review comment audit

Audited repository: `awbjcj/food-manager`. Code snapshot: `dev` at `ac1c175fd7e586543b1089c6c1e0186830d28141`. After fetching origin, local `HEAD` and `origin/dev` had zero divergence.

All 53 pull requests (open, closed, and merged) were inspected, including 26 conversation comments, 30 review submissions, and 40 inline threads. Four inline threads were already resolved. The 36 unresolved threads below span 16 PRs; no thread-comment pages were truncated. Top-level bot summaries and review receipts add no separately actionable unresolved items.

## Implementation

All **F01-F14** were selected and implemented locally on `fix/pr-review-comments`.
The inventory below preserves the original audit findings at the snapshot above;
its “Fix” entries describe the defects before these patches. GitHub threads and
production deployments have not been changed.

| Group | Implemented behavior | Regression coverage |
| --- | --- | --- |
| F01 | Standalone search uses direct DeepSeek credentials. Subscription-only clients omit search and allow selector fallback; optional text/profile search is isolated too. | `test_provider_modes.py`: fallback, separate SDK identity, and actual mocked search URL/auth header. |
| F02 | Parsed canonical English names remain authoritative over optional display translations. | `test_review_data_integrity.py`, `test_receipt_languages.py`. |
| F03 | Preference translation misses are admitted and every attempt/cost is metered; denied rendering uses cached/English names. | `test_review_translation.py`: action exhaustion, cost breaker, update paths, malformed/failing providers, free cache hits and English. |
| F04 | Batch entry resolves candidate names after admission, then page/selection callbacks use the warmed cache. | `test_review_translation.py`: direct zh/fr/es commands, old-card entry, acknowledgement order, denied entry, and page navigation. |
| F05 | Same-tier operator family grants honor the requested end date before or after expiry; live paid subscriptions remain protected. | `test_review_data_integrity.py`, `test_v60_operator.py`. |
| F06 | Rebased code passes locked dependency installation, lint, and tests before a lease-protected push. | `test_workflow_guards.py`: local bare Git repository, combined hotfix/feature tree, simulated validation success and failure. |
| F07 | The master merge guard requires the source repository as well as an allowed branch name. | `test_workflow_guards.py`: same-repository dev/hotfix, forks, other branches, and missing source identity. |
| F08 | Pantry and expanded digest pages are bounded by item count and UTF-16 length; actions retain origin, sort, and page. | `test_pantry_pagination.py`, updated scheduler and batch tests. |
| F09 | Storage cache keys use a reserved leading separator that normalization cannot produce. Ambiguous legacy space-prefixed entries are left intact and state durations are relearned; existing item expiry dates are unchanged. | Storage suites and `test_review_data_integrity.py`: natural-name collisions, namespace impersonation, fridge/frozen isolation, and user corrections. |
| F10 | Known storage-search charges accrue to receipt and summary, including unsuccessful lookups; known charges are retained when another estimate is unknown. | `test_review_data_integrity.py`. |
| F11 | Refinement replaces automatic estimates with search results while conditional upserts protect explicit user corrections. | `test_review_data_integrity.py`, existing refinement/storage tests. |
| F12 | New add proposals retain their purchase date; legacy payloads recover their origin from expiry minus duration. | `test_review_data_integrity.py`: application across midnight for new and legacy payloads. |
| F13 | Atomic receipt insertion translates only its own uniqueness conflict; cache and display-translation writes use conflict-safe upserts. Other integrity errors retain their actual failure path. | `test_review_data_integrity.py`: receipt race, cache race, and unrelated-error rollback. |
| F14 | Receipt schema failures receive one bounded corrective retry with validation context; successful results include both attempts' costs. | `test_claude_55_receipts.py`: tool and JSON responses, repair success/failure, bounded calls, and cost. |

Final validation:

- `uv run pytest -q`: **1,244 passed, 3 existing SQLModel deprecation warnings** (77 additional test cases over the audit baseline).
- `uv run pyright`: **0 errors, 0 warnings**.
- `uv run ruff check app tests bin migrations` and `git diff --check`: clean.
- `uv build`: source distribution and wheel built successfully.
- `uv run alembic upgrade head` on a new temporary database: passed through `0023_pantry_outcome`.
- Focused regression run: 143 passed. The SDK-specific direct search mock was also rerun successfully after aligning its HTTP client with the installed SDK's typed HTTPX2 interface.

Logs, JUnit reports, build artifacts, and the migration smoke database are local
at `C:\Users\24216\AppData\Local\Temp\food-manager-pr-review-fixes`.
These are local tests and mocked provider/Telegram calls; hosted CI, live provider
acceptance, and deployment remain separate from this implementation. Delivery
uses a `fix:` commit on a `hotfix/*` branch targeting `master`, keeping unrelated
`dev` changes out of the release.

## Original audit result

- **14 recommended fix groups** cover **17 unresolved threads**; duplicate reports share a group.
- **13 threads are already addressed**, **1 is superseded**, **4 are low value/cosmetic or hypothetical**, and **1 is conditional concurrency hardening**.
- At the audit stage, application code, GitHub thread state, PR metadata, and production resources were not changed.

## Complete numbered inventory

Comment numbers C01-C36 are stable within this audit. Selected groups F01-F14 are implemented above; duplicates share one patch.

| Comment | PR / original review | Assessment | Group | Current finding / required action |
| --- | --- | --- | --- | --- |
| C01 | [#45](https://github.com/awbjcj/food-manager/pull/45#discussion_r4308102030) | Fix | F01 | **DeepSeek subscription search targets the gateway.** bin/run.py:365 still registers the gateway SDK as the search client; app/deepseek_llm.py:126 posts the standalone search route on that SDK. A mocked gateway receives https://gateway.example/v1/tools/search and its 404 becomes an unknown result without selector fallback. |
| C02 | [#45](https://github.com/awbjcj/food-manager/pull/45#discussion_r4308102033) | Fix | F02 | **Canonical receipt names can be overwritten.** app/ingest_service.py:109 replaces parsed name with the en translation without validating agreement. Carrots plus en=Carottes persists raw_name=Carottes and normalized_name=carottes. Existing localization tests intentionally canonicalize French name via en, so a fix must reconcile that fixture with the documented English-name contract. |
| C03 | [#39](https://github.com/awbjcj/food-manager/pull/39#discussion_r4180596696) | Fix | F03 | **Preference translations bypass admission/accounting.** app/handlers/meta.py:564, 578, and 618 pass a live translator on display, denied update, and successful update. Mocked denied admission still allows translation; display calls admission zero times. Translation cost is discarded in app/translation_service.py:98. |
| C04 | [#38](https://github.com/awbjcj/food-manager/pull/38#discussion_r4180229357) | Fix | F04 | **Batch entry leaves uncached names in English.** app/views.py:51 reads only cached translations; both app/handlers/pantry.py:139 and app/callbacks/batch.py call it. A Chinese /pantry batch with an uncached Milk name never invokes the configured translator. This is lower priority than correctness/accounting, and the patch must preserve cache-only navigation and quota degradation. |
| C05 | [#31](https://github.com/awbjcj/food-manager/pull/31#discussion_r3998160879) | Fix | F05 | **Same-tier family grants cannot be renewed.** app/operator/tiers.py:73 returns before applying duration or rolling an elapsed period. A 30-day family grant followed by a 90-day family grant reports changed=False and retains the original end date. |
| C06 | [#29](https://github.com/awbjcj/food-manager/pull/29#discussion_r3964736690) | Already addressed | - | **Subscription arrow wraps on narrow screens.** web/src/App.tsx:167 now has two direct grid children: row-copy and the arrow. web/src/styles.css:168 defines two matching columns; the old <=420px override no longer exists. This is source verification, not a new visual acceptance test. |
| C07 | [#27](https://github.com/awbjcj/food-manager/pull/27#discussion_r3900795425) | Low value | - | **Historical PR title does not match its diff.** This concerns metadata on merged PR #27. Renaming a historical PR has no runtime or present CI benefit; no code patch is warranted. |
| C08 | [#27](https://github.com/awbjcj/food-manager/pull/27#discussion_r3900799964) | Fix | F06 | **Automatically rebased dev work is not tested together.** .github/workflows/sync-dev-with-master.yml rebases and immediately pushes with the persisted GITHUB_TOKEN. It runs no lint/tests, while ci-dev.yml has no callable workflow entry. GitHub documents that token-generated pushes do not start push workflows. |
| C09 | [#26](https://github.com/awbjcj/food-manager/pull/26#discussion_r3900732858) | Already addressed | - | **Credential-mode coverage was missing from the old helper.** Although tests/test_agno_models.py still hard-codes api, later tests/test_agno_runtime.py:117 uses real api/subscription modes and :172 asserts actual mock-transport request hosts for all providers and both Agno seams. tests/test_provider_modes.py:63 separately verifies subscription token/endpoint resolution. The requested behavioral coverage now exists; changing only the old helper would add little. |
| C10 | [#26](https://github.com/awbjcj/food-manager/pull/26#discussion_r3900732894) | Low value | - | **Signature test should tolerate future **kwargs.** The installed SDK signatures accept the current emitted parameters, and the suite passes. The inspected stable create method has no VAR_KEYWORD parameter. This is hypothetical future-proofing, not a present regression; handle if an SDK changes the contract. |
| C11 | [#26](https://github.com/awbjcj/food-manager/pull/26#discussion_r3900732926) | Low value | - | **Add a guard around an Agno private helper in a test.** The helper exists and the adapter contract tests pass. A guard would improve a future test failure message, without adding current runtime coverage or fixing behavior. |
| C12 | [#24](https://github.com/awbjcj/food-manager/pull/24#discussion_r3900346255) | Fix; duplicate | F07 | **Fork branch can impersonate the canonical source branch.** .github/workflows/guard-main-merge.yml:18 validates HEAD_REF only. A fork named dev or hotfix/... passes the source-name test. Same root cause as C14/C15; one repository-identity check fixes all three comments. This does not itself grant a fork permission to merge. |
| C13 | [#23](https://github.com/awbjcj/food-manager/pull/23#discussion_r3900299052) | Low value | - | **Workflow says main while protecting master.** Cosmetic workflow naming. The trigger and shell message correctly target master. Renaming the workflow is optional housekeeping, not a functional patch. |
| C14 | [#23](https://github.com/awbjcj/food-manager/pull/23#discussion_r3900299090) | Fix; duplicate | F07 | **Require the same repository for allowed source branches.** Duplicate of C12/C15; add a head-repository identity condition before the branch allowlist. |
| C15 | [#23](https://github.com/awbjcj/food-manager/pull/23#discussion_r3900300608) | Fix; duplicate | F07 | **Check source repository before accepting branch name.** Duplicate of C12/C14; the guard has no repository-identity comparison today. |
| C16 | [#7](https://github.com/awbjcj/food-manager/pull/7#discussion_r3410622088) | Already addressed | - | **Pantry actions lose full-list context.** app/renderer.py:393 appends origin/sort to action callbacks, and app/callbacks/actions.py routes back_to=all through _refresh_pantry_message. Existing test_full_pantry_item_action_refreshes_full_pantry covers the behavior. |
| C17 | [#7](https://github.com/awbjcj/food-manager/pull/7#discussion_r3410818397) | Fix; duplicate | F08 | **Full pantry can exceed Telegram message limits.** app/handlers/pantry.py:188 and app/callbacks/items.py:255 still use cap=None. The reproduction renders 110 items into 6,654 characters and 115 buttons. /pantry batch is paginated, but the normal full-list route remains unbounded. Same fix as C18. |
| C18 | [#6](https://github.com/awbjcj/food-manager/pull/6#discussion_r3388501038) | Fix; duplicate | F08 | **Expanded digest remains unbounded.** app/callbacks/actions.py:249 renders show:all with cap=None and builds a button for every result. Address alongside normal pantry pagination. The old comment's rough button-count thresholds are not needed to establish the reproduced text-length failure. |
| C19 | [#5](https://github.com/awbjcj/food-manager/pull/5#discussion_r3385060407) | Fix | F09 | **Storage cache keys collide with natural food names.** app/frozen_shelf_life.py:149 returns frozen pizza for both the natural default-storage name and the frozen-state key. A seeded 3-day default entry bypasses the vendored 60-day freezer table. The analogous fridge prefix deserves the same treatment. |
| C20 | [#5](https://github.com/awbjcj/food-manager/pull/5#discussion_r3385060410) | Fix | F10 | **Frozen search cost is dropped.** StorageDecision at app/frozen_shelf_life.py:157 has no cost field. app/ingest_service.py:130 and :160 retain extraction cost only. A 100-micro image call plus a 900-micro frozen lookup records 100 in both summary and Receipt. |
| C21 | [#4](https://github.com/awbjcj/food-manager/pull/4#discussion_r3329196598) | Already addressed | - | **Recipes can select expired items.** app/cook/service.py:159 and :256 filter expires_on >= today before selection, usable-item counting, and shopping comparison. test_run_cook_excludes_expired_items covers the main pipeline. |
| C22 | [#3](https://github.com/awbjcj/food-manager/pull/3#discussion_r3322287810) | Already addressed | - | **Fresh explicit-expiry adds cannot be undone.** app/pantry_service.py:526 has a separate _is_undoable_add rule that permits the initial user_correction source. test_undo_add_explicit_expiry_is_undoable covers this case. |
| C23 | [#3](https://github.com/awbjcj/food-manager/pull/3#discussion_r3322291740) | Already addressed | - | **Excluded-only receipt omits exclusion explanation.** app/renderer.py:119 now renders skipped names and the /add override within the zero-insert branch. test_render_ingest_reply_excluded_only_receipt covers it. |
| C24 | [#3](https://github.com/awbjcj/food-manager/pull/3#discussion_r3322474090) | Fix | F11 | **Refinement leaves a stale shelf-life cache.** app/refine_service.py:98 calls insert-only put_cached; app/cache.py returns existing rows unchanged. The reproduction updates Milk from 7 days to 3 days while its cache remains 7. |
| C25 | [#2](https://github.com/awbjcj/food-manager/pull/2#discussion_r3309473269) | Fix | F12 | **Applying an add across midnight changes its origin.** AddPayload has no proposal purchase date. app/correction_service.py:443 assigns callback today while preserving the old expiry. A proposed 3-day duration becomes only 2 days between stored purchase date and expiry. |
| C26 | [#2](https://github.com/awbjcj/food-manager/pull/2#discussion_r3309478017) | Already addressed | - | **Message-send failures leave unseen pending proposals.** app/handlers/pantry.py:283 and :599 catch send failures, mark the proposal cancelled, and commit. This solves the orphan-pending problem through compensation, although it does not implement the comment's suggested flush-only transaction strategy. |
| C27 | [#2](https://github.com/awbjcj/food-manager/pull/2#discussion_r3309478072) | Already addressed | - | **Text-LLM quantities allow zero/negative values.** ProposedAddItem.qty in app/llm.py:101 has ge=0.01; AddPayload.qty in app/correction_service.py:37 has gt=0. The persisted add payload is validated before application. |
| C28 | [#2](https://github.com/awbjcj/food-manager/pull/2#discussion_r3311286011) | Conditional hardening | H01 | **Stale pending sessions can apply twice.** Two preloaded sessions can each insert and mark the same proposal applied: the reproduction creates two rows. However, app/callbacks/pending.py has no await between loading/checking the proposal and committing the add; rapid callbacks in the documented single-process event loop cannot interleave there. A guarded claim is useful if concurrent workers/callers are supported. |
| C29 | [#1](https://github.com/awbjcj/food-manager/pull/1#discussion_r3308136492) | Already addressed | - | **Valid empty receipt parses should return no-food.** AnthropicLLMClient now returns an LLMResult for items=[] rather than raising. The existing PNG-media test supplies an empty items list and succeeds. The exact old corrective-retry sequence has been replaced, but its reported final-empty error is gone. |
| C30 | [#1](https://github.com/awbjcj/food-manager/pull/1#discussion_r3308136495) | Already addressed | - | **Map receipt uniqueness races to DuplicateReceipt.** app/ingest_service.py:234 catches IntegrityError and translates receipt races. The original missing mapping is addressed; C33 identifies that its present scope is too broad. |
| C31 | [#1](https://github.com/awbjcj/food-manager/pull/1#discussion_r3308206209) | Superseded | - | **Legacy regex rejects a bare dozen quantity.** The digit-prefix regex/manual ingest implementation discussed in PR #1 no longer exists. Admitted /add uses propose_add and the text-LLM parser; quota-denied adds deliberately use a simpler fallback. Restoring the removed regex is not a useful fix. |
| C32 | [#1](https://github.com/awbjcj/food-manager/pull/1#discussion_r3308206211) | Already addressed | - | **Production uv run may sync development dependencies.** railway.toml and Dockerfile both now launch uv run --no-sync python bin/run.py, matching the already-built environment. |
| C33 | [#1](https://github.com/awbjcj/food-manager/pull/1#discussion_r3308229103) | Fix | F13 | **Unrelated integrity failures are mislabeled as duplicates.** app/ingest_service.py:234 catches every IntegrityError from the entire transaction. An injected cache check/insert race causing a real cache uniqueness failure becomes DuplicateReceipt even though there is no receipt for that photo. This reproduction models a race; it is not a live concurrency/load test. |
| C34 | [#1](https://github.com/awbjcj/food-manager/pull/1#discussion_r3308289151) | Already addressed | - | **Receipt date confidence accepts values outside 0..1.** ParseResult.purchase_date_confidence in app/llm.py:61 now has ge=0.0 and le=1.0. test_anthropic_client_validates_purchase_date_confidence rejects 85. |
| C35 | [#1](https://github.com/awbjcj/food-manager/pull/1#discussion_r3308289152) | Already addressed | - | **Image MIME type is always JPEG.** app/llm.py:583 detects the media type or accepts an explicit type. test_anthropic_client_preserves_png_media_type verifies PNG bytes are declared image/png. |
| C36 | [#1](https://github.com/awbjcj/food-manager/pull/1#discussion_r3308527186) | Fix | F14 | **Receipt schema failures have no corrective retry.** app/llm.py:590 makes one call; its validation exception is logged as final and re-raised. A mocked invalid first payload followed by a valid second payload still fails after exactly one call. The design documents a bounded corrective retry. |

## Recommended patches

| Group | Priority | Comments | Patch | Validation required |
| --- | --- | --- | --- | --- |
| F01 | P1 | C01 | **Separate standalone DeepSeek search from subscription routing.** Use explicitly configured first-party search credentials, or omit DeepSeek search in subscription mode so a capable configured provider can be selected. Keep subscription text routing intact. | Test both credential modes, selection fallback, and that the standalone endpoint never receives a gateway token. A mocked 404 is local evidence; deployed gateway behavior needs separate acceptance. |
| F02 | P2 | C02 | **Protect canonical English identity during receipt ingest.** Enforce a single canonical name and validate agreement with en display entries before persistence. Preserve multilingual display caching. Reconcile the existing fixture that deliberately supplies a French name and a different English translation. | Schema-valid mismatched name/en must not change normalization or shelf-life keys. Cover matching entries, missing translations, and localized receipts. Coordinate schema repair with F14. |
| F03 | P1 | C03 | **Meter every preference-render translation.** Admit uncached translation work, account the returned cost, and use cached/English names when denied. Cover read-only display as well as denied and successful profile updates; currently all three can translate outside accounting. | Denied and cost-breaker cases make zero provider calls; English/cache hits remain free; admitted misses are recorded once with the actual cost. |
| F04 | P2, lower priority | C04 | **Resolve batch-entry translation misses.** Resolve names asynchronously when opening a batch from the command or an older pantry card, with admission and fallback consistent with F03. Keep navigation responsive and preserve the deliberate cache-only callback policy where appropriate. | Open a batch directly in zh/fr/es with uncached manual items; exercise language changes and quota denial. Renderer remains synchronous and callback acknowledgement occurs before async work. |
| F05 | P2 | C05 | **Refresh an operator family grant in the same tier.** Roll elapsed state and honor the requested entitlement period for operator-comped family access. Retain no-op behavior where the effective tier/period is truly equivalent and preserve paid-subscription conflict protection. | Renew/change duration before and after expiry; verify next metered operation retains the requested family access and the ledger records an actual change once. |
| F06 | P2 | C08 | **Validate the combined rebased dev tree before publishing it.** Run the locked dependency install, lint, and tests on the rebased checkout before its force-with-lease push, or use an equivalent callable CI path that validates that precise tree. | A failing combined tree cannot be pushed by sync. Unchanged branches skip redundant validation. Confirm behavior in GitHub Actions separately from local workflow/source checks. |
| F07 | P2 | C12, C14, C15 | **Bind source-branch allowlisting to this repository.** Compare the PR head repository identity with the base/current repository before allowing dev or hotfix/*. | Same-repository dev/hotfix pass; fork dev/hotfix and other branch names fail. This tightens the stated source policy; it does not replace merge permissions or branch protection. |
| F08 | P2 | C17, C18 | **Bound normal pantry and expanded digest messages.** Introduce pagination or a bounded text-aware view for both command and callback routes. Preserve list origin and sort order. The existing batch pagination can inform the design but does not protect the normal list. | Exercise >110 items, long translated names, next/back navigation, show-all, and post-action refresh. Every rendered message stays within the platform text limit; validate the actual keyboard shape too. |
| F09 | P1 | C19 | **Give storage states an unambiguous cache namespace.** Separate state keys from normalized natural food names in the shared storage_cache_key seam. Handle ambiguous legacy entries intentionally rather than blindly copying a possibly fresh duration into a freezer entry. | Default frozen pizza and frozen-state pizza remain independent; include fridge collisions, user corrections, table fallback, and legacy-cache handling. |
| F10 | P2 | C20 | **Accrue paid storage-search attempts into receipt cost.** Carry search cost through StorageDecision, including known cost from unsuccessful lookups, then accrue it into both the receipt and ingest summary used by downstream accounting. | 100 image micros plus 900 search micros becomes 1,000 in summary/receipt; also cover cache/table hits, multiple frozen items, unknown costs, and fallback results. |
| F11 | P2 | C24 | **Replace stale automatic cache estimates after refinement.** Update an existing automatically learned estimate after successful search while protecting explicit user corrections and concurrent edits. | Refined item and future ingest/add cache lookups agree; user corrections made while search runs remain authoritative. |
| F12 | P2 | C25 | **Keep proposal purchase date and expiry consistent across midnight.** Persist the proposal date in its payload and use it at application, or consistently recompute the complete date/duration set. Define backward compatibility for pending JSON created before the patch. | Apply immediately across local midnight within the 10-minute TTL; verify duration/origin/expiry agree for estimated and explicit dates, including old pending payloads. |
| F13 | P2 | C33 | **Translate only genuine receipt duplicate constraints.** Narrow duplicate classification to receipt uniqueness. Handle cache upserts without rolling back valid receipts where feasible, and propagate unrelated failures through their real error path. | A same-photo receipt race remains DuplicateReceipt. Distinct-photo cache/translation conflicts and other integrity failures are never mislabeled; confirm transaction rollback/atomicity. |
| F14 | P2 | C36 | **Retry a malformed receipt parse once with correction context.** Issue one bounded repair request for malformed tool payload/JSON/schema output, respecting current tool-choice policy and accruing the costs of attempts. | Invalid then valid succeeds after two requests; two invalid responses fail after two; transport retries remain separately bounded; valid empty parses retain no-food behavior. |

A practical batching order is F01/F03/F09 first; F02/F10/F11/F12/F13/F14 for receipt and shelf-life correctness; F05/F08/F04 for renewal and pantry flows; then F06/F07 for CI.

### Additional selections

- **H01 / C28 — atomic pending claims.** If concurrent workers/callers are part of the intended deployment, guard the pending-to-applied transition with a database status claim in the same transaction as the pantry insert. Preserve rollback on failure and test stale apply/apply and apply/cancel sessions. The documented deployment currently runs one poller, and this handler does not yield before commit, so this is not a reproduced rapid-callback bug in that deployment.

## Verification evidence

The full local suite passed: **1,167 passed, 3 warnings**, with `uv run pytest -q --junitxml=<temporary audit directory>/pytest-baseline.xml`. This is baseline verification before product patches; it does not prove that the gaps identified above are covered by existing tests.

An isolated Python reproducer used newly created in-memory SQLite databases, dummy credentials, test doubles, and `httpx.MockTransport`. It sent no real provider requests and accessed no live bot/production data.

| Reproduction | Observed result |
| --- | --- |
| Canonical-name mismatch | `name=Carrots`, `en=Carottes` persisted as `Carottes` / `carottes`. |
| Frozen/default key collision | Existing `frozen pizza=3` won over the vendored 60-day freezer table. |
| Frozen lookup cost | Image 100 + search 900 micros persisted only 100 in receipt and summary. |
| Refinement cache | Item changed to 3 days; cache retained 7 days. |
| Add across midnight | Stored shelf life 3 days; expiry minus purchase date 2 days. |
| Family grant renewal | Second 90-day grant reported unchanged and retained the 30-day end. |
| Large pantry | 110 items produced 6,654 characters and 115 buttons. |
| Receipt schema repair | Invalid first response raised ValidationError after one call despite a valid second response being available. |
| Preference display | One translation call, zero admission calls, zero accounting calls. |
| Preference update denied | Admission denied; one translation call still occurred and no accounting call did. |
| Chinese batch entry | Uncached Milk stayed English; configured translator was not called. |
| Injected cache check/insert race | Real cache uniqueness failure became DuplicateReceipt with no receipt for the distinct photo. |
| Two stale pending sessions | Both applied the same proposal, producing two pantry rows; this is conditional service hardening. |
| Mocked DeepSeek gateway | Standalone search was sent to gateway.example/v1/tools/search; mocked 404 was swallowed into unknown search. |
| SDK signature check | Installed stable Anthropic create has no arbitrary **kwargs parameter; the hypothetical signature comment is not a current failure. |

Raw fetched comments, the reproducer, its JSON output, and test logs/JUnit output are stored locally at `C:\Users\24216\AppData\Local\Temp\food-manager-pr-review-audit`. They are investigation artifacts, not committed application tests.

## Platform documentation checked

Context7 was used for GitHub Actions and DeepSeek API documentation; the exact DeepSeek Search guide was opened directly when Context7 did not return that endpoint.

- [GitHub GITHUB_TOKEN workflow triggering](https://docs.github.com/en/actions/concepts/security/github_token): pushes authenticated with that token do not start another push-triggered workflow. Explicit workflow dispatch/reusable execution is a separate choice.
- [DeepSeek Search and Fetch guide](https://api-docs.deepseek.com/guides/search/): standalone search is POST https://api.deepseek.com/v1/tools/search and uses a DeepSeek API key. The code reproducer verifies routing only; it does not establish deployed gateway support.
- [Sub2API endpoint registry](https://github.com/Wei-Shaw/sub2api/blob/main/backend/internal/handler/endpoint.go): the inspected canonical endpoint list does not expose /v1/tools/search. This supports treating the current gateway route as unsupported unless a separately deployed extension is confirmed.
- [OpenAI Python HTTPX2 customization](https://github.com/openai/openai-python/blob/main/httpx2.md): the direct search mock uses an HTTPX2 async client compatible with the installed SDK and closes both SDK clients explicitly.

Source review and local tests are separate from live Telegram UI, provider credentials, deployed gateway behavior, and hosted CI acceptance.
