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

Search uses POST /v1/tools/search via a bounded function-tool loop. Unlike the
Responses built-in web_search (Pro only), this works with Flash as well. Only
non-empty successful searches incur the $0.75/1,000-request fee. The temporary
server-side tool free period does not apply to these standalone API requests.
See https://api-docs.deepseek.com/guides/search/.

This integration has no image extraction client; photo calls still fall back
to another provider. Text and search capabilities are reflected in
``app.providers.PROVIDER_CAPABILITIES``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from functools import partial
from typing import Any

from app.cook.llm import NUTRITION_SYSTEM_PROMPT, SELECTION_SYSTEM_PROMPT
from app.cook.models import NutritionScores, SelectedItems
from app.llm import (
    CORRECTION_SYSTEM_PROMPT,
    OPENAI_ADD_SYSTEM_PROMPT,
    PROFILE_SYSTEM_PROMPT,
    CorrectionDiff,
    ProposedAddItem,
    ProposedAddItems,
    _add_cost,
    _cost_micros,
    _extract_openai_parsed,
    _web_search_call_count,
)
from app.llm_transport import is_retryable_transport_error, with_transport_retry
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
_DEEPSEEK_REASONING = {"effort": "low"}
_SEARCH_COST_MICROS = 750
_MAX_SEARCH_CALLS = 3
_DEEPSEEK_SEARCH_TOOL = {
    "type": "function",
    "name": "web_search",
    "description": "Search the web for current food safety and shelf-life evidence.",
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 128}},
        "required": ["query"],
        "additionalProperties": False,
    },
}


def _deepseek_cost_micros(response, model: str) -> int | None:
    """Token cost via the shared OpenAI-shaped helper.

    Unexpected server-side search remains unknown: its output items do not
    expose the number of successful, non-empty Search/Fetch API requests.
    """
    base = _cost_micros(response, model)
    if base is None:
        return None
    calls = _web_search_call_count(response)
    if calls:
        return None
    return base


def _retryable_search_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in {408, 429} or status >= 500
    return is_retryable_transport_error(exc)


class _DeepSeekResponsesClient:
    """Shared DeepSeek Responses-API call: structured output, retry, cost."""

    def __init__(
        self, sdk, model: str, *, web_search: bool = False, sleep=asyncio.sleep
    ):
        self._sdk = sdk
        self._model = model
        self._web_search = web_search
        self._sleep = sleep

    async def _search(self, call) -> tuple[dict, int]:
        try:
            if call.name != "web_search":
                raise ValueError("unsupported search tool")
            args = json.loads(call.arguments)
            query = args["query"]
            if not isinstance(query, str) or not query.strip() or len(query) > 128:
                raise ValueError("query must contain 1 to 128 characters")
            # SDK post retains the configured route/auth/HTTP client. Avoid
            # duplicating /v1 for gateways whose base URL already includes it.
            path = (
                "/tools/search"
                if self._sdk.base_url.path.rstrip("/").endswith("/v1")
                else "/v1/tools/search"
            )
            result = await with_transport_retry(
                lambda: self._sdk.post(
                    path,
                    cast_to=dict,
                    body={"query": query, "count": 5, "need_content": False},
                ),
                log_event="deepseek_search_failed",
                sleep=self._sleep,
                classify=_retryable_search_error,
            )
            if not isinstance(result, dict) or not isinstance(
                result.get("results"), list
            ):
                # A malformed successful response may still have been billed.
                return {"error": "Invalid search response"}, _SEARCH_COST_MICROS
            return result, _SEARCH_COST_MICROS if result["results"] else 0
        except Exception as exc:  # noqa: BLE001 - search must not block text tasks
            log.warning(
                "deepseek_search_unavailable", extra={"error_class": type(exc).__name__}
            )
            return {"error": "Search unavailable; do not claim web evidence."}, 0

    async def _create_response(
        self, system: str, user_text: str, text_format=None, *, require_search=False
    ):
        if require_search:
            # DeepSeek rejects required/named tool_choice in thinking mode.
            # Request search in the prompt and verify evidence before returning.
            system += (
                " Call the web_search function before returning a shelf-life answer."
            )
        history: list[Any] = [
            {"role": "system", "content": system},
            {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
        ]
        cost: int | None = 0
        searches = 0
        grounded = False
        for turn in range(_MAX_SEARCH_CALLS + 1):
            can_search = (
                self._web_search
                and searches < _MAX_SEARCH_CALLS
                and turn < _MAX_SEARCH_CALLS
            )
            kwargs: dict[str, Any] = {
                "model": self._model,
                "input": list(history),
                "tools": [_DEEPSEEK_SEARCH_TOOL] if can_search else [],
                "tool_choice": "auto" if can_search else "none",
                "reasoning": _DEEPSEEK_REASONING,
                "max_output_tokens": 1024 + _REASONING_HEADROOM_TOKENS,
            }
            create = self._sdk.responses.create
            if text_format is not None:
                kwargs["text_format"] = text_format
                create = self._sdk.responses.parse
            response = await with_transport_retry(
                partial(create, **kwargs),
                log_event="deepseek_llm_failed",
                sleep=self._sleep,
            )
            token_cost = _deepseek_cost_micros(response, self._model)
            cost = None if token_cost is None else _add_cost(cost, token_cost)
            calls = [item for item in response.output if item.type == "function_call"]
            if not calls:
                return (None if require_search and not grounded else response), cost
            if not can_search:
                raise ValueError("DeepSeek exceeded the search tool budget")
            # Stateless Responses requires the full output, including opaque
            # reasoning content, on every subsequent turn. Never log it.
            history.extend(response.output)
            for call in calls:
                if searches < _MAX_SEARCH_CALLS:
                    result, search_cost = await self._search(call)
                    grounded = grounded or bool(result.get("results"))
                    searches += 1
                    cost = _add_cost(cost, search_cost)
                else:
                    result = {
                        "error": "Search limit reached; answer with available evidence."
                    }
                history.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result),
                    }
                )
        raise ValueError("DeepSeek exceeded the response turn budget")

    async def call(self, system: str, user_text: str, model_cls):
        response, cost = await self._create_response(system, user_text, model_cls)
        parsed = model_cls.model_validate(_extract_openai_parsed(response))
        return parsed, cost


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
        return await self._client.call(
            CORRECTION_SYSTEM_PROMPT, user_msg, CorrectionDiff
        )

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
        return [str(x) for x in parsed.items], _deepseek_cost_micros(
            response, self._model
        )


class DeepSeekSearchClient(ShelfLifeSearchClient):
    """Shelf-life lookup backed by DeepSeek's standalone Search API."""

    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._client = _DeepSeekResponsesClient(
            sdk, model, web_search=True, sleep=sleep
        )

    async def lookup_shelf_life(
        self, *, name: str, category: str | None
    ) -> ShelfLifeSearchResult:
        prompt = f"Item: {name}" + (f" (category: {category})" if category else "")
        try:
            response, cost = await self._client._create_response(
                SEARCH_SYSTEM_PROMPT,
                prompt,
                require_search=True,
            )
        except Exception as exc:  # noqa: BLE001 - search must degrade to unknown, not crash
            log.warning(
                "search_transport_failed", extra={"error_class": type(exc).__name__}
            )
            return ShelfLifeSearchResult(
                days=None, confidence=0.0, cost_micros_usd=None
            )

        if response is None:
            return ShelfLifeSearchResult(
                days=None, confidence=0.0, cost_micros_usd=cost
            )
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
            return ShelfLifeSearchResult(
                days=None, confidence=0.0, cost_micros_usd=cost
            )
