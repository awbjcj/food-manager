# `/cook` — Pantry-Aware Recipe Recommendation

**Status:** Design approved, ready for implementation planning
**Date:** 2026-05-30
**Author:** awbjcj (with Claude)

## 1. Summary

Add a `/cook` Telegram command that recommends a dish to make from what's
already in the pantry. A short interactive flow asks the user what they feel
like (meal-type, cuisine), then a deterministic pipeline of LLM stages selects
pantry items (prioritising soon-to-expire food without compromising on a
healthy, delicious result), fetches three candidate recipes via web search,
scores them, and presents the best pick with a display-only shopping list of
missing ingredients.

This is a **deterministic async pipeline**, not an agent framework. It runs on
the user's currently selected provider (`User.llm_provider`) and reuses the
repo's existing conventions: typed `Protocol` LLM clients with test fakes,
injected `Session`, caller-supplied `today`/`now`, micro-USD cost accounting,
and the inline-keyboard + pending-row interaction pattern.

## 2. Goals

- Recommend a dish that is **healthy, delicious, and uses soon-to-expire items**
  — expiry is a *priority signal*, never a hard rule.
- Honour a **persistent food profile** (diet, allergies/exclusions, preferred
  cuisines, max cook time, household size) that the user updates by typing a
  plain sentence. Exclusions are a hard safety filter.
- Keep the flow **bounded and cheap**: 3 LLM round-trips max, ≤3 button rounds,
  capped web-search uses, per-cook cost backstop.
- Stay **native to the codebase**: deterministic, unit-testable, single new
  table, one migration.

## 3. Non-goals (explicit; deferred to future specs)

- Recipe-database API integration (e.g. Spoonacular/Edamam).
- Local RAG over a corpus of exported recipes.
- Actionable shopping list / wiring missing items into `/add` or a saved
  shopping list. v1 shopping list is **display-only**.
- Recipe dedup ("don't repeat yesterday's dish") and a `/history` view.
- Multi-provider-per-request (running different stages on different SDKs in one
  request). The pipeline runs entirely on the user's selected provider.
- Free-text conversational FSM. Clarification is via inline-keyboard rounds.

## 4. User-facing surface

- **`/cook`** — trigger. Posts a **static** meal-type button row, then a cuisine
  button row **seeded from `profile.preferred_cuisines`** (falling back to a
  default set), both including a `[Surprise me]` option. Options are built in
  code — no extra LLM call to generate them. Then a `🍳 Thinking…` placeholder
  that is edited in place with the result.
- **`/prefs`** — view the persistent profile, and update it by typing a plain
  sentence (an LLM merges the sentence into the stored profile).
- **`/help`** and **`/stats`** updated to mention `/cook` and show cook cost.

### Result rendering (option B: summary + source link)

Top-pick card + a `[Show alternatives]` button revealing the other two.
Each candidate card shows: title, cuisine, time/effort, nutrition score,
ingredient list, a 2–3 line method gist, and a **link to the full recipe**.
Full step-by-step instructions are *not* reproduced in chat — the source URL
carries them. The shopping list (missing ingredients) is appended as
display-only text, computed lazily for whichever candidate is shown.

## 5. Flow

```
/cook
  → [round 1] meal-type buttons   (static list, incl. "Surprise me")
  → [round 2] cuisine buttons      (led by profile.preferred_cuisines; ≤2 rounds, hard max 3)
                                    state persisted in CookSession each tap
  → post "🍳 Thinking…" placeholder
  → [LLM 1: selection]  pick pantry items; expiry = priority not law; health+taste lead
  → [LLM 2: recipe+web] 3 candidates honouring profile; hard allergy filter in code
  → [LLM 3: nutrition]  score all 3 (health_score / effort / est_minutes / rationale)
  → [code: blend]       rank = 0.4·health + 0.4·expiry_use + 0.2·deliciousness  (weights tunable)
  → [code: shopping]    recipe_ingredients − pantry (normalised), lazy per shown pick
  → edit placeholder → top-pick card + [Show alternatives] + display-only shopping list
```

## 6. Pipeline stages

Each LLM stage is a typed function over `Session` returning a Pydantic result,
satisfied in tests by a fake (duck-typed Protocol), consistent with
`FakeLLMClient`. Provider is resolved once per request via the existing
selector.

1. **Selection (LLM).** Input: candidate set of active items with expiry dates
   and categories (from a `list_digest_due`-style query, widened as needed),
   plus meal-type + profile. Output: chosen item IDs. Balances expiry urgency
   against composing a genuinely good dish. The "fruit only in dessert / fruit
   acceptable depending on meal-type" judgement lives here, informed by the
   meal-type the user chose.

2. **Recipe + web search (LLM).** Input: selected items + meal-type + cuisine +
   profile. Output: **3 candidates**, each
   `{ title, cuisine, source_url, ingredients[ {name, qty?, unit?} ], method_gist, deliciousness }`.
   Reuses the existing web-search tool pattern (`AnthropicSearchClient`-style;
   OpenAI `web_search` for the OpenAI provider). On transport failure: retry
   (existing 3-attempt pattern) then degrade to a model-knowledge recipe
   annotated "(couldn't verify online)".

3. **Nutrition (LLM).** Input: the 3 candidates. Output per candidate:
   `{ health_score (0–100), effort (easy|medium|hard), est_minutes, rationale }`.

**Deterministic glue (Python, unit-tested):**
- Hard **allergy/exclusion filter** drops any candidate containing an excluded
  ingredient (matched via `normalization.py`). Never bypassed by the LLM.
- **Expiry-utilization** factor: how many of the selected soon-to-expire items
  each candidate actually uses, computed from pantry expiry data.
- **Blend & rank**: `0.4·health + 0.4·expiry_use + 0.2·deliciousness`
  (weights tunable constants).
- **Shopping diff**: `recipe_ingredients − pantry`, matched through the existing
  normalized-name/alias map, with a thin name-matching pass. Display-only.

## 7. Data model

### New table `CookSession`

| column | type | notes |
|---|---|---|
| `id` | Optional[int] PK | |
| `user_id` | int FK user.telegram_id, index | |
| `status` | str | collecting / ready / done / cancelled / expired |
| `meal_type` | Optional[str] | filled after round 1 |
| `cuisine` | Optional[str] | filled after round 2 |
| `selected_item_ids` | str (JSON) | |
| `candidates_json` | Optional[str] | the 3 scored candidates |
| `chosen_index` | Optional[int] | which candidate is shown |
| `chat_id` | int | |
| `message_id` | Optional[int] | the editable result/placeholder message |
| `llm_cost_micros_usd` | Optional[int] | accrued across stages |
| `created_at` | datetime | |
| `expires_at` | datetime | 10-min TTL |

One Alembic migration. Finished rows are retained (feed `/stats`; no hard
delete). A new `/cook` while one is `collecting` **supersedes** it
(old → `cancelled`).

### Persistent profile (sentence-driven, hybrid storage)

The user updates the profile by typing a **plain sentence**; an LLM merges it
into the stored profile. The profile is **hybrid** so that natural-language
flexibility and code-enforceable safety coexist:

- **Structured slice** (code relies on these):
  - `diet` (e.g. none/vegetarian/vegan/…)
  - `exclusions[]` — allergies / hard-avoid ingredients
  - `preferred_cuisines[]` — e.g. ["chinese", "american"]
  - `max_cook_minutes`
  - `household_size`
- **`profile_note`** — free text for everything that doesn't fit a field
  ("prefer one-pot meals", "we like it spicy", "no deep-frying").

**Update stage (LLM, text).** A `parse_profile_update`-style call takes
`(current_profile, new_sentence)` and returns the merged profile (structured
fields + `profile_note`). It is its own text-LLM stage, *not* part of the
`/cook` pipeline — triggered by `/prefs <sentence>` and opportunistically when
the cook conversation surfaces a new constraint (bot offers to save it).

**Usage.** The recipe stage (LLM 2) gets the **whole profile injected**
(structured fields + `profile_note`) so the model honours every nuance.
`preferred_cuisines[]` additionally seeds the Q4 cuisine button round.

**Safety.** `exclusions[]` is enforced as a **hard code filter** on candidate
ingredients (via `normalization.py`), never left to prompt compliance — this is
why exclusions remain a structured field rather than living only in free text.

Stored on `User` (new columns) or a small dedicated table — to be settled in
the implementation plan. One Alembic migration.

## 8. Failure policy

| Case | Handling |
|---|---|
| Pantry too thin | Guard before any LLM call: require ≥3 track-worthy active items, else friendly reply. |
| Allergy/exclusion wipes out all candidates | Regenerate once (re-prompt naming the violated ingredients). Still failing → refuse clearly. **Never serve a violating dish.** |
| Web search transport failure | Retry (3-attempt), then degrade to unverified model recipe annotated as such. |
| User abandons mid-flow | `CookSession.expires_at` TTL (10 min). Expired-round tap → "session expired, start a new /cook". |
| Concurrent `/cook` | Supersede: old session → `cancelled`, start fresh. |
| Cost runaway | Bounded by construction (≤3 stages, ≤3 rounds, capped web-search uses) **plus** a configurable per-cook micro-USD backstop that halts further stages. |

## 9. Cost & stats

Each stage's micro-USD cost accrues onto the `CookSession` row (consistent with
`Receipt.llm_cost_micros_usd` and the text-LLM cost accounting). `/stats` gains
a cook-cost line alongside the existing receipt and text-LLM cost lines.

## 10. Testing

- Fakes for all three pipeline LLM stages, the search client, and the
  `parse_profile_update` stage (duck-typed Protocols), mirroring
  `tests/fakes.py`.
- Profile-update merge tested deterministically: a sentence merges into the
  structured slice + `profile_note` without dropping prior fields; a new
  allergy lands in `exclusions[]` (so the hard filter sees it).
- Deterministic `today`/`now` injected; no `datetime.now()` inside services.
- Pure-Python unit tests for: the hard allergy filter, expiry-utilization,
  blend/ranking weights, shopping-list diff + name matching.
- State-machine tests for the round/TTL/supersede transitions via injected
  `now`, mirroring the pending-correction tests.
- Min-items guard, regenerate-once-then-refuse, and web-fallback paths covered.

## 11. Provider note

The pipeline is provider-agnostic via the existing `LLMClient`/`TextLLMClient`
protocols and selector. It runs wholly on `User.llm_provider`; both Anthropic
and OpenAI are supported, selected per-account via `/llm`. No request fans out
across both SDKs simultaneously.
