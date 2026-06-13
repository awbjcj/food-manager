from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional, Protocol

from app.cook.models import NutritionScores, RecipeCandidates, SelectedItems
from app.llm import (
    _OPENAI_REASONING,
    _OPENAI_WEB_SEARCH_TOOL,
    _cost_micros,
    _extract_json_text,
    _extract_openai_parsed,
)
from app.llm_transport import is_retryable_transport_error, with_transport_retry

log = logging.getLogger(__name__)

SELECTION_SYSTEM_PROMPT = """You choose which pantry items to cook with. You get a
JSON list of candidate items (id, name, category, days_to_expiry) plus the user's
meal_type and food profile. Choose a coherent set for a single healthy, delicious
dish. Prefer items expiring soon, but NEVER include an item just because it expires
soon if it would make a bad dish. Respect the meal_type (e.g. fruit is fine for a
dessert/snack, usually not a savoury main). Return ONLY JSON: {"item_ids":[int],
"rationale": string}."""

RECIPE_SYSTEM_PROMPT = """You are a recipe finder. Given chosen ingredients, a
cuisine, a meal_type, and the user's food profile (including hard "avoid"
ingredients), return THREE distinct recipes. Use web search to find real recipes
and include a source_url. NEVER use any avoided ingredient. Return ONLY JSON
matching: {"candidates":[{"title","cuisine","source_url","ingredients":[{"name",
"qty","unit"}],"method_gist","deliciousness":0..1}]} (exactly 3 candidates)."""

NUTRITION_SYSTEM_PROMPT = """You are a nutrition expert. For each recipe candidate,
score it. Return ONLY JSON matching: {"scores":[{"health_score":0..100,
"effort":"easy|medium|hard","est_minutes":int,"rationale":string}]} with one entry
per candidate, in the same order."""

SCHEMA_REPAIR_INSTRUCTION = (
    "Your last response did not match the schema. Return ONLY valid JSON matching "
    "the schema."
)


class SelectionLLMClient(Protocol):
    async def select_items(
        self, *, prompt: str
    ) -> tuple[SelectedItems, Optional[int]]: ...


class RecipeLLMClient(Protocol):
    async def fetch_recipes(
        self, *, prompt: str
    ) -> tuple[RecipeCandidates, Optional[int]]: ...


class NutritionLLMClient(Protocol):
    async def score(
        self, *, prompt: str
    ) -> tuple[NutritionScores, Optional[int]]: ...


class _AnthropicJSONClient:
    """Shared Anthropic structured-text call with retry + cost, schema-validated."""

    def __init__(self, sdk, model: str, *, web_search: bool = False, sleep=asyncio.sleep):
        self._sdk = sdk
        self._model = model
        self._web_search = web_search
        self._sleep = sleep

    async def _create_message(self, system: str, user_content, tools):
        return await with_transport_retry(
            lambda: self._sdk.messages.create(
                model=self._model,
                max_tokens=2048,
                system=system,
                tools=tools,
                messages=[{"role": "user", "content": user_content}],
            ),
            log_event="cook_llm_failed",
            sleep=self._sleep,
        )

    async def _call(self, system: str, user_text: str, model_cls):
        tools = (
            [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
            if self._web_search
            else []
        )
        user_content = [{"type": "text", "text": user_text}]
        total_cost = 0
        unknown_cost = False
        for attempt in range(2):
            msg = await self._create_message(system, user_content, tools)
            cost = _cost_micros(msg, self._model)
            if cost is None:
                unknown_cost = True
            else:
                total_cost += cost
            try:
                parsed = model_cls.model_validate(json.loads(_extract_json_text(msg)))
                return parsed, None if unknown_cost else total_cost
            except Exception as exc:
                if attempt == 1:
                    log.warning(
                        "cook_llm_schema_failed_final",
                        extra={"error_class": type(exc).__name__},
                    )
                    raise
                log.warning(
                    "cook_llm_schema_failed_retrying",
                    extra={"error_class": type(exc).__name__},
                )
                user_content = [
                    *user_content,
                    {"type": "text", "text": SCHEMA_REPAIR_INSTRUCTION},
                ]
        raise RuntimeError("unreachable")


class AnthropicSelectionLLM(SelectionLLMClient):
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._client = _AnthropicJSONClient(sdk, model, sleep=sleep)

    async def select_items(self, *, prompt: str) -> tuple[SelectedItems, Optional[int]]:
        return await self._client._call(SELECTION_SYSTEM_PROMPT, prompt, SelectedItems)


class AnthropicRecipeLLM(RecipeLLMClient):
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._client = _AnthropicJSONClient(
            sdk,
            model,
            web_search=True,
            sleep=sleep,
        )

    async def fetch_recipes(
        self, *, prompt: str
    ) -> tuple[RecipeCandidates, Optional[int]]:
        return await self._client._call(RECIPE_SYSTEM_PROMPT, prompt, RecipeCandidates)


class AnthropicNutritionLLM(NutritionLLMClient):
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._client = _AnthropicJSONClient(sdk, model, sleep=sleep)

    async def score(self, *, prompt: str) -> tuple[NutritionScores, Optional[int]]:
        return await self._client._call(NUTRITION_SYSTEM_PROMPT, prompt, NutritionScores)


class _OpenAIJSONClient:
    def __init__(self, sdk, model, *, web_search=False, sleep=asyncio.sleep):
        self._sdk = sdk
        self._model = model
        self._web_search = web_search
        self._sleep = sleep

    async def _create_response(self, system, user_content, tools, model_cls):
        kwargs = {
            "model": self._model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "tools": tools,
            "reasoning": _OPENAI_REASONING,
            "text_format": model_cls,
            "max_output_tokens": 2048,
        }
        if self._web_search:
            kwargs["max_tool_calls"] = 3

        return await with_transport_retry(
            lambda: self._sdk.responses.parse(**kwargs),
            log_event="cook_llm_failed",
            sleep=self._sleep,
            classify=is_retryable_transport_error,
        )

    async def _call(self, system, user_text, model_cls):
        tools = [_OPENAI_WEB_SEARCH_TOOL] if self._web_search else []
        user_content = [{"type": "input_text", "text": user_text}]
        total_cost = 0
        unknown_cost = False
        for attempt in range(2):
            resp = await self._create_response(system, user_content, tools, model_cls)
            cost = _cost_micros(resp, self._model)
            if cost is None:
                unknown_cost = True
            else:
                total_cost += cost
            try:
                parsed = model_cls.model_validate(_extract_openai_parsed(resp))
                return parsed, None if unknown_cost else total_cost
            except Exception as exc:
                if attempt == 1:
                    log.warning(
                        "cook_llm_schema_failed_final",
                        extra={"error_class": type(exc).__name__},
                    )
                    raise
                log.warning(
                    "cook_llm_schema_failed_retrying",
                    extra={"error_class": type(exc).__name__},
                )
                user_content = [
                    *user_content,
                    {"type": "input_text", "text": SCHEMA_REPAIR_INSTRUCTION},
                ]
        raise RuntimeError("unreachable")


class OpenAISelectionLLM(SelectionLLMClient):
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._client = _OpenAIJSONClient(sdk, model, sleep=sleep)

    async def select_items(self, *, prompt: str) -> tuple[SelectedItems, Optional[int]]:
        return await self._client._call(SELECTION_SYSTEM_PROMPT, prompt, SelectedItems)


class OpenAIRecipeLLM(RecipeLLMClient):
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._client = _OpenAIJSONClient(sdk, model, web_search=True, sleep=sleep)

    async def fetch_recipes(
        self, *, prompt: str
    ) -> tuple[RecipeCandidates, Optional[int]]:
        return await self._client._call(RECIPE_SYSTEM_PROMPT, prompt, RecipeCandidates)


class OpenAINutritionLLM(NutritionLLMClient):
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._client = _OpenAIJSONClient(sdk, model, sleep=sleep)

    async def score(self, *, prompt: str) -> tuple[NutritionScores, Optional[int]]:
        return await self._client._call(NUTRITION_SYSTEM_PROMPT, prompt, NutritionScores)
