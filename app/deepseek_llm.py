"""DeepSeek clients (OpenAI-Responses-API-compatible endpoint).

DeepSeek's Responses API (https://api-docs.deepseek.com/guides/responses_api)
mirrors OpenAI's Responses API wire shape closely enough that this module
reuses app.llm's OpenAI-shaped helpers (``_cost_micros``,
``_extract_openai_parsed``, ``_web_search_call_count``) directly instead of
re-deriving them. DeepSeek is reached through the OpenAI SDK pointed at
``DEEPSEEK_BASE_URL`` (unchanged); only the endpoint moved from
``.chat.completions`` to ``.responses``. Structured output (``text_format=``)
means the model is schema-guaranteed to return valid JSON, so — like the
OpenAI clients this mirrors — there is no "ask for JSON, validate, repair
once" loop here anymore.

DeepSeek's ``web_search``/``web_search_2025_08_26`` tool is native and
server-side (queries execute on DeepSeek's infrastructure, not the caller's),
but its per-call price is undisclosed — unlike OpenAI's published $10/1,000
calls. A response with search invoked therefore reports its cost as unknown
(``None``) rather than silently under-pricing it; see
``_deepseek_cost_micros``.

DeepSeek is still image-incapable (no image extraction client here); it now
has a search capability via the native web_search tool, reflected in
``app.providers.PROVIDER_CAPABILITIES``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from typing import Any

from app.cook.llm import NUTRITION_SYSTEM_PROMPT, SELECTION_SYSTEM_PROMPT
from app.cook.models import NutritionScores, SelectedItems
from app.llm import (
    _OPENAI_REASONING,
    _OPENAI_WEB_SEARCH_TOOL,
    CORRECTION_SYSTEM_PROMPT,
    OPENAI_ADD_SYSTEM_PROMPT,
    PROFILE_SYSTEM_PROMPT,
    CorrectionDiff,
    ProposedAddItem,
    ProposedAddItems,
    _cost_micros,
    _extract_openai_parsed,
    _web_search_call_count,
)
from app.llm_transport import with_transport_retry
from app.profile_service import FoodProfile
from app.refine_service import SEARCH_SYSTEM_PROMPT
from app.shelf_life_search import ShelfLifeSearchClient, ShelfLifeSearchResult
from app.translation_llm import _TRANSLATE_SYSTEM_PROMPT, TranslationList, _user_msg

log = logging.getLogger(__name__)

# DeepSeek's reasoning models (called here with reasoning={"effort": "low"})
# count their "thinking" tokens against max_output_tokens alongside the
# visible reply, the same way Gemini 2.5+/3.x do (see gemini_llm.py's
# _THINKING_HEADROOM_TOKENS). A budget sized for the JSON reply alone gets
# eaten by thinking, truncating output_text mid-JSON so it fails to parse —
# observed as /cook succeeding at the HTTP layer (200 OK, timing logged) but
# still failing the pipeline immediately after. This headroom is added on
# top of each call's intended *output* size; it is only a cap (unused tokens
# cost nothing), so it is deliberately generous.
_REASONING_HEADROOM_TOKENS = 8192


def _deepseek_cost_micros(response, model: str) -> int | None:
    """Token cost via the shared OpenAI-shaped helper.

    Unknown (``None``), not silently under-priced, whenever the web_search
    tool was invoked — DeepSeek does not publish a per-search rate for it.
    """
    base = _cost_micros(response, model)
    if base is None:
        return None
    calls = _web_search_call_count(response)
    if calls:
        log.info("deepseek_web_search_cost_unknown", extra={"search_calls": calls})
        return None
    return base


class _DeepSeekResponsesClient:
    """Shared DeepSeek Responses-API call: structured output, retry, cost."""

    def __init__(
        self, sdk, model: str, *, web_search: bool = False, sleep=asyncio.sleep
    ):
        self._sdk = sdk
        self._model = model
        self._web_search = web_search
        self._sleep = sleep

    async def _create_response(self, system: str, user_text: str, text_format):
        return await with_transport_retry(
            lambda: self._sdk.responses.parse(
                model=self._model,
                input=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": user_text}],
                    },
                ],
                tools=[_OPENAI_WEB_SEARCH_TOOL] if self._web_search else [],
                reasoning=_OPENAI_REASONING,
                text_format=text_format,
                max_output_tokens=1024 + _REASONING_HEADROOM_TOKENS,
            ),
            log_event="deepseek_llm_failed",
            sleep=self._sleep,
        )

    async def call(self, system: str, user_text: str, model_cls):
        response = await self._create_response(system, user_text, model_cls)
        parsed = model_cls.model_validate(_extract_openai_parsed(response))
        return parsed, _deepseek_cost_micros(response, self._model)


class DeepSeekTextLLMClient:
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._client = _DeepSeekResponsesClient(
            sdk, model, web_search=True, sleep=sleep
        )

    async def parse_correct(
        self,
        *,
        item_snapshot: dict[str, Any],
        cache_snapshot: dict[str, Any] | None,
        user_text: str,
        today: date,
    ) -> tuple[CorrectionDiff, int | None]:
        user_msg = json.dumps(
            {
                "item_snapshot": item_snapshot,
                "cache_snapshot": cache_snapshot,
                "today": today.isoformat(),
                "user_text": user_text,
            }
        )
        return await self._client.call(CORRECTION_SYSTEM_PROMPT, user_msg, CorrectionDiff)

    async def parse_add(
        self,
        *,
        user_text: str,
        today: date,
        tz: str,
    ) -> tuple[list[ProposedAddItem], int | None]:
        user_msg = json.dumps(
            {"today": today.isoformat(), "tz": tz, "user_text": user_text}
        )
        parsed, cost = await self._client.call(
            OPENAI_ADD_SYSTEM_PROMPT, user_msg, ProposedAddItems
        )
        return parsed.items, cost


class DeepSeekProfileLLMClient:
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._client = _DeepSeekResponsesClient(
            sdk, model, web_search=True, sleep=sleep
        )

    async def parse_profile_update(
        self, *, current: FoodProfile, sentence: str
    ) -> tuple[FoodProfile, int | None]:
        user_msg = json.dumps({"current": current.model_dump(), "sentence": sentence})
        return await self._client.call(PROFILE_SYSTEM_PROMPT, user_msg, FoodProfile)


class DeepSeekSelectionLLM:
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._client = _DeepSeekResponsesClient(sdk, model, sleep=sleep)

    async def select_items(self, *, prompt: str) -> tuple[SelectedItems, int | None]:
        return await self._client.call(SELECTION_SYSTEM_PROMPT, prompt, SelectedItems)


class DeepSeekNutritionLLM:
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._client = _DeepSeekResponsesClient(sdk, model, sleep=sleep)

    async def score(self, *, prompt: str) -> tuple[NutritionScores, int | None]:
        return await self._client.call(NUTRITION_SYSTEM_PROMPT, prompt, NutritionScores)


class DeepSeekTranslationLLMClient:
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._sdk = sdk
        self._model = model
        self._sleep = sleep

    async def translate(
        self, *, texts: list[str], lang: str
    ) -> tuple[list[str], int | None]:
        response = await with_transport_retry(
            lambda: self._sdk.responses.parse(
                model=self._model,
                input=[
                    {"role": "system", "content": _TRANSLATE_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": _user_msg(texts, lang)}
                        ],
                    },
                ],
                text_format=TranslationList,
                max_output_tokens=1024 + _REASONING_HEADROOM_TOKENS,
            ),
            log_event="deepseek_llm_failed",
            sleep=self._sleep,
        )
        parsed = TranslationList.model_validate(_extract_openai_parsed(response))
        return [str(x) for x in parsed.items], _deepseek_cost_micros(response, self._model)


class DeepSeekSearchClient(ShelfLifeSearchClient):
    """Shelf-life lookup via DeepSeek's native ``web_search`` tool."""

    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._sdk = sdk
        self._model = model
        self._sleep = sleep

    async def lookup_shelf_life(
        self, *, name: str, category: str | None
    ) -> ShelfLifeSearchResult:
        prompt = f"Item: {name}" + (f" (category: {category})" if category else "")
        try:
            response = await with_transport_retry(
                lambda: self._sdk.responses.create(
                    model=self._model,
                    input=[
                        {"role": "system", "content": SEARCH_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": prompt}],
                        },
                    ],
                    tools=[_OPENAI_WEB_SEARCH_TOOL],
                ),
                log_event="deepseek_llm_failed",
                sleep=self._sleep,
            )
        except Exception as exc:  # noqa: BLE001 - search must degrade to unknown, not crash
            log.warning(
                "search_transport_failed", extra={"error_class": type(exc).__name__}
            )
            return ShelfLifeSearchResult(days=None, confidence=0.0, cost_micros_usd=None)

        cost = _deepseek_cost_micros(response, self._model)
        try:
            text = response.output_text
            data = json.loads(text[text.index("{") : text.rindex("}") + 1])
            return ShelfLifeSearchResult(
                days=int(data["days"]),
                confidence=float(data["confidence"]),
                cost_micros_usd=cost,
            )
        except Exception as exc:  # noqa: BLE001 - search must degrade to unknown, not crash
            log.warning(
                "search_parse_failed", extra={"error_class": type(exc).__name__}
            )
            return ShelfLifeSearchResult(days=None, confidence=0.0, cost_micros_usd=cost)
