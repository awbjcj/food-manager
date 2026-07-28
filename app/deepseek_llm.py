"""DeepSeek clients (OpenAI-compatible ``chat.completions``).

DeepSeek's public API is **text-only** — no image input and no API-level
web-search tool — so this module implements only the text capabilities
(correction/add, profile, cook selection & nutrition, translation). Image
extraction, web search, and the search-backed recipe step are not implemented
here; for a DeepSeek user they fall back to a capable provider via the selectors
in ``app.providers``.

DeepSeek is reached through the OpenAI SDK pointed at ``DEEPSEEK_BASE_URL``, but
it speaks the **Chat Completions** API, not the Responses API the existing
``OpenAI*`` clients use — so it cannot reuse them. Instead every client shares
``_DeepSeekJSONClient``, which mirrors the Anthropic text client's proven
"ask for JSON, validate, repair once" loop. ``response_format=json_object``
constrains the model to a JSON object; the schema lives in each system prompt.
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
    _PRICE_MICROS_PER_TOKEN_BY_MODEL,
    CORRECTION_SYSTEM_PROMPT,
    OPENAI_ADD_SYSTEM_PROMPT,
    PROFILE_SYSTEM_PROMPT,
    CorrectionDiff,
    ProposedAddItem,
    ProposedAddItems,
)
from app.llm_transport import with_transport_retry
from app.profile_service import FoodProfile
from app.translation_llm import _TRANSLATE_SYSTEM_PROMPT, TranslationList, _user_msg

log = logging.getLogger(__name__)

SCHEMA_REPAIR_INSTRUCTION = (
    "Your last response did not match the schema. Return ONLY valid JSON "
    "matching the schema."
)

# json_object mode requires an object root, so translation returns {"items":[...]}
# rather than the bare array the Anthropic translation client uses.
_DEEPSEEK_TRANSLATE_SYSTEM_PROMPT = (
    _TRANSLATE_SYSTEM_PROMPT
    + '\nReturn ONLY a JSON object {"items": [<translated strings>]} with the '
    "same length and order as the input. No prose."
)


def _chat_cost(response, model: str) -> int | None:
    """Best-effort cost in micro-USD from Chat Completions ``usage`` tokens."""
    price = _PRICE_MICROS_PER_TOKEN_BY_MODEL.get(model)
    if price is None:
        return None
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    try:
        in_tokens = getattr(usage, "prompt_tokens", None) or 0
        out_tokens = getattr(usage, "completion_tokens", None) or 0
        return round(in_tokens * price["input"] + out_tokens * price["output"])
    except Exception:  # noqa: BLE001 - cost estimate is best-effort
        return None


class _DeepSeekJSONClient:
    """Shared Chat Completions call: JSON object out, validate, repair once."""

    def __init__(self, sdk, model: str, *, sleep=asyncio.sleep):
        self._sdk = sdk
        self._model = model
        self._sleep = sleep

    async def _create(self, system: str, messages: list[dict[str, str]]):
        return await with_transport_retry(
            lambda: self._sdk.chat.completions.create(
                model=self._model,
                max_tokens=1024,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, *messages],
            ),
            log_event="deepseek_llm_failed",
            sleep=self._sleep,
        )

    async def call(self, system: str, user_text: str, parse_fn):
        messages: list[dict[str, str]] = [{"role": "user", "content": user_text}]
        total_cost = 0
        unknown_cost = False
        for attempt in (0, 1):
            response = await self._create(system, messages)
            cost = _chat_cost(response, self._model)
            if cost is None:
                unknown_cost = True
            else:
                total_cost += cost
            text = response.choices[0].message.content or ""
            try:
                return parse_fn(text), None if unknown_cost else total_cost
            except Exception as exc:
                if attempt == 1:
                    log.warning(
                        "deepseek_schema_failed_final",
                        extra={"error_class": type(exc).__name__},
                    )
                    raise
                messages = [
                    *messages,
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": SCHEMA_REPAIR_INSTRUCTION},
                ]
        raise RuntimeError("unreachable")


class DeepSeekTextLLMClient:
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._client = _DeepSeekJSONClient(sdk, model, sleep=sleep)

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

        def _parse(text: str) -> CorrectionDiff:
            return CorrectionDiff.model_validate(json.loads(text))

        return await self._client.call(CORRECTION_SYSTEM_PROMPT, user_msg, _parse)

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

        def _parse(text: str) -> list[ProposedAddItem]:
            return ProposedAddItems.model_validate(json.loads(text)).items

        return await self._client.call(OPENAI_ADD_SYSTEM_PROMPT, user_msg, _parse)


class DeepSeekProfileLLMClient:
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._client = _DeepSeekJSONClient(sdk, model, sleep=sleep)

    async def parse_profile_update(
        self, *, current: FoodProfile, sentence: str
    ) -> tuple[FoodProfile, int | None]:
        user_msg = json.dumps({"current": current.model_dump(), "sentence": sentence})

        def _parse(text: str) -> FoodProfile:
            return FoodProfile.model_validate(json.loads(text))

        return await self._client.call(PROFILE_SYSTEM_PROMPT, user_msg, _parse)


class DeepSeekSelectionLLM:
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._client = _DeepSeekJSONClient(sdk, model, sleep=sleep)

    async def select_items(self, *, prompt: str) -> tuple[SelectedItems, int | None]:
        def _parse(text: str) -> SelectedItems:
            return SelectedItems.model_validate(json.loads(text))

        return await self._client.call(SELECTION_SYSTEM_PROMPT, prompt, _parse)


class DeepSeekNutritionLLM:
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._client = _DeepSeekJSONClient(sdk, model, sleep=sleep)

    async def score(self, *, prompt: str) -> tuple[NutritionScores, int | None]:
        def _parse(text: str) -> NutritionScores:
            return NutritionScores.model_validate(json.loads(text))

        return await self._client.call(NUTRITION_SYSTEM_PROMPT, prompt, _parse)


class DeepSeekTranslationLLMClient:
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._client = _DeepSeekJSONClient(sdk, model, sleep=sleep)

    async def translate(
        self, *, texts: list[str], lang: str
    ) -> tuple[list[str], int | None]:
        def _parse(text: str) -> list[str]:
            return [str(x) for x in TranslationList.model_validate(json.loads(text)).items]

        return await self._client.call(
            _DEEPSEEK_TRANSLATE_SYSTEM_PROMPT, _user_msg(texts, lang), _parse
        )
