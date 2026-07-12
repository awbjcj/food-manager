"""Google Gemini clients (native ``google-genai`` SDK).

Unlike the Anthropic/OpenAI clients — which the codebase splits across modules by
capability — every Gemini client lives here, because the native SDK has its own
shape (``GenerateContentConfig`` objects, a structured-output mode that is
mutually exclusive with the Google Search grounding tool) that is best kept in
one place. Each client receives an already-constructed ``genai.Client`` so this
module imports cleanly without the dependency installed and is faked in tests;
``google.genai.types`` is imported lazily inside the one helper that needs it.

Capability coverage: Gemini is a full provider — image extraction, the text
tasks (correction/add/profile), the cook pipeline (selection/recipe/nutrition),
translation, and shelf-life web search via Google Search grounding.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from typing import Any, Optional, TypeVar

from pydantic import BaseModel

from app.cook.llm import (
    NUTRITION_SYSTEM_PROMPT,
    RECIPE_SYSTEM_PROMPT,
    SELECTION_SYSTEM_PROMPT,
)
from app.cook.models import NutritionScores, RecipeCandidates, SelectedItems
from app.llm import (
    CORRECTION_SYSTEM_PROMPT,
    OPENAI_ADD_SYSTEM_PROMPT,
    PROFILE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    CorrectionDiff,
    LLMResult,
    ParseResult,
    ProposedAddItem,
    ProposedAddItems,
    _detect_media_type,
    _PRICE_MICROS_PER_TOKEN_BY_MODEL,
)
from app.llm_transport import with_transport_retry
from app.profile_service import FoodProfile
from app.refine_service import SEARCH_SYSTEM_PROMPT
from app.shelf_life_search import ShelfLifeSearchClient, ShelfLifeSearchResult
from app.translation_llm import _TRANSLATE_SYSTEM_PROMPT, TranslationList, _user_msg

log = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)

# Gemini 2.5+/3.x are reasoning models: their "thinking" tokens are counted
# against ``max_output_tokens`` alongside the visible reply. A budget sized for
# the JSON output alone gets eaten by thinking, truncating the reply
# (``finish_reason=MAX_TOKENS``) so the JSON is cut mid-string and parsing fails
# — observed as "Gemini can't read the receipt". Every seam's ``max_output_tokens``
# below is therefore the intended *output* size; this headroom is added centrally
# to cover thinking. It is only a cap (unused tokens cost nothing), so it is
# deliberately generous.
_THINKING_HEADROOM_TOKENS = 8192


def _gemini_cost(response, model: str) -> Optional[int]:
    """Best-effort cost in micro-USD from Gemini ``usage_metadata`` token counts."""
    price = _PRICE_MICROS_PER_TOKEN_BY_MODEL.get(model)
    if price is None:
        return None
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None
    try:
        in_tokens = getattr(usage, "prompt_token_count", None) or 0
        out_tokens = getattr(usage, "candidates_token_count", None) or 0
        return round(in_tokens * price["input"] + out_tokens * price["output"])
    except Exception:
        return None


def _usage_dict(response) -> Optional[dict[str, Any]]:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None
    data = {
        key: getattr(usage, key)
        for key in ("prompt_token_count", "candidates_token_count", "total_token_count")
        if getattr(usage, key, None) is not None
    }
    return data or None


def _json_object(text: str) -> Any:
    """Parse the first ``{...}`` object out of a model reply.

    Google Search grounding disallows structured-output mode, so those replies
    are plain text asked to contain JSON; this trims any stray prose the way the
    Anthropic search client does.
    """
    snippet = text[text.index("{") : text.rindex("}") + 1]
    return json.loads(snippet)


class _GeminiCaller:
    """Shared Gemini ``generate_content`` wrapper with retry + cost.

    ``response_schema`` enables structured-output mode (reliable JSON) and is
    used for every non-search call. ``search`` enables the Google Search
    grounding tool; the two are mutually exclusive in the SDK, so search callers
    pass a JSON-instructing prompt and parse the text themselves.
    """

    def __init__(self, client, model: str, *, sleep=asyncio.sleep):
        self._client = client
        self._model = model
        self._sleep = sleep

    async def generate(
        self,
        *,
        system: str,
        contents,
        response_schema=None,
        search: bool = False,
        max_output_tokens: int = 2048,
    ):
        from google.genai import types

        config_kwargs: dict[str, Any] = {
            "system_instruction": system,
            "max_output_tokens": max_output_tokens + _THINKING_HEADROOM_TOKENS,
        }
        if search:
            config_kwargs["tools"] = [
                types.Tool(google_search=types.GoogleSearch())
            ]
        elif response_schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = response_schema

        config = types.GenerateContentConfig(**config_kwargs)
        return await with_transport_retry(
            lambda: self._client.aio.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            ),
            log_event="gemini_llm_failed",
            sleep=self._sleep,
        )

    async def structured(
        self,
        *,
        system: str,
        contents,
        model_cls: type[TModel],
        max_output_tokens: int = 2048,
    ) -> tuple[TModel, Optional[int]]:
        """Call with structured output and validate into ``model_cls``."""
        response = await self.generate(
            system=system,
            contents=contents,
            response_schema=model_cls,
            max_output_tokens=max_output_tokens,
        )
        cost = _gemini_cost(response, self._model)
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, model_cls):
            return parsed, cost
        if parsed is not None:
            return model_cls.model_validate(parsed), cost
        return model_cls.model_validate(json.loads(response.text)), cost


class GeminiLLMClient:
    """Receipt photo → parsed items (image capability)."""

    def __init__(self, client, model: str, sleep=asyncio.sleep):
        self._caller = _GeminiCaller(client, model, sleep=sleep)
        self._model = model

    async def extract_items_from_image(
        self,
        image_bytes: bytes,
        *,
        image_media_type: str | None = None,
    ) -> LLMResult:
        from google.genai import types

        media_type = image_media_type or _detect_media_type(image_bytes)
        contents = [
            types.Part.from_bytes(data=image_bytes, mime_type=media_type),
            "Parse this receipt.",
        ]
        response = await self._caller.generate(
            system=SYSTEM_PROMPT,
            contents=contents,
            response_schema=ParseResult,
        )
        try:
            parsed_raw = getattr(response, "parsed", None)
            if isinstance(parsed_raw, ParseResult):
                parsed = parsed_raw
            elif parsed_raw is not None:
                parsed = ParseResult.model_validate(parsed_raw)
            else:
                parsed = ParseResult.model_validate(json.loads(response.text))
        except Exception as exc:
            log.warning(
                "llm_json_validation_failed_final",
                extra={"error_class": type(exc).__name__, "error": str(exc)},
            )
            raise
        return LLMResult(
            parse=parsed,
            cost_micros_usd=_gemini_cost(response, self._model),
            provider_usage=_usage_dict(response),
        )


class GeminiTextLLMClient:
    """Correction + /add parsing (text capability)."""

    def __init__(self, client, model: str, sleep=asyncio.sleep):
        self._caller = _GeminiCaller(client, model, sleep=sleep)

    async def parse_correct(
        self,
        *,
        item_snapshot: dict[str, Any],
        cache_snapshot: Optional[dict[str, Any]],
        user_text: str,
        today: date,
    ) -> tuple[CorrectionDiff, Optional[int]]:
        user_msg = json.dumps(
            {
                "item_snapshot": item_snapshot,
                "cache_snapshot": cache_snapshot,
                "today": today.isoformat(),
                "user_text": user_text,
            }
        )
        return await self._caller.structured(
            system=CORRECTION_SYSTEM_PROMPT,
            contents=user_msg,
            model_cls=CorrectionDiff,
            max_output_tokens=1024,
        )

    async def parse_add(
        self,
        *,
        user_text: str,
        today: date,
        tz: str,
    ) -> tuple[list[ProposedAddItem], Optional[int]]:
        user_msg = json.dumps(
            {"today": today.isoformat(), "tz": tz, "user_text": user_text}
        )
        parsed, cost = await self._caller.structured(
            system=OPENAI_ADD_SYSTEM_PROMPT,
            contents=user_msg,
            model_cls=ProposedAddItems,
            max_output_tokens=1024,
        )
        return parsed.items, cost


class GeminiProfileLLMClient:
    def __init__(self, client, model: str, sleep=asyncio.sleep):
        self._caller = _GeminiCaller(client, model, sleep=sleep)

    async def parse_profile_update(
        self, *, current: FoodProfile, sentence: str
    ) -> tuple[FoodProfile, Optional[int]]:
        user_msg = json.dumps({"current": current.model_dump(), "sentence": sentence})
        return await self._caller.structured(
            system=PROFILE_SYSTEM_PROMPT,
            contents=user_msg,
            model_cls=FoodProfile,
            max_output_tokens=1024,
        )


class GeminiSelectionLLM:
    def __init__(self, client, model: str, sleep=asyncio.sleep):
        self._caller = _GeminiCaller(client, model, sleep=sleep)

    async def select_items(self, *, prompt: str) -> tuple[SelectedItems, Optional[int]]:
        return await self._caller.structured(
            system=SELECTION_SYSTEM_PROMPT,
            contents=prompt,
            model_cls=SelectedItems,
        )


class GeminiRecipeLLM:
    """Recipe finder — uses Google Search grounding, so it parses JSON text."""

    def __init__(self, client, model: str, sleep=asyncio.sleep):
        self._caller = _GeminiCaller(client, model, sleep=sleep)
        self._model = model

    async def fetch_recipes(
        self, *, prompt: str
    ) -> tuple[RecipeCandidates, Optional[int]]:
        response = await self._caller.generate(
            system=RECIPE_SYSTEM_PROMPT,
            contents=prompt,
            search=True,
        )
        cost = _gemini_cost(response, self._model)
        return RecipeCandidates.model_validate(_json_object(response.text)), cost


class GeminiNutritionLLM:
    def __init__(self, client, model: str, sleep=asyncio.sleep):
        self._caller = _GeminiCaller(client, model, sleep=sleep)

    async def score(self, *, prompt: str) -> tuple[NutritionScores, Optional[int]]:
        return await self._caller.structured(
            system=NUTRITION_SYSTEM_PROMPT,
            contents=prompt,
            model_cls=NutritionScores,
        )


class GeminiTranslationLLMClient:
    def __init__(self, client, model: str, sleep=asyncio.sleep):
        self._caller = _GeminiCaller(client, model, sleep=sleep)

    async def translate(
        self, *, texts: list[str], lang: str
    ) -> tuple[list[str], Optional[int]]:
        parsed, cost = await self._caller.structured(
            system=_TRANSLATE_SYSTEM_PROMPT,
            contents=_user_msg(texts, lang),
            model_cls=TranslationList,
            max_output_tokens=1024,
        )
        return [str(x) for x in parsed.items], cost


class GeminiSearchClient(ShelfLifeSearchClient):
    """Shelf-life lookup via Google Search grounding (search capability)."""

    def __init__(self, client, model: str, sleep=asyncio.sleep):
        self._caller = _GeminiCaller(client, model, sleep=sleep)
        self._model = model

    async def lookup_shelf_life(
        self, *, name: str, category: Optional[str]
    ) -> ShelfLifeSearchResult:
        prompt = f"Item: {name}" + (f" (category: {category})" if category else "")
        try:
            response = await self._caller.generate(
                system=SEARCH_SYSTEM_PROMPT,
                contents=prompt,
                search=True,
                max_output_tokens=512,
            )
        except Exception as exc:
            log.warning(
                "search_transport_failed", extra={"error_class": type(exc).__name__}
            )
            return ShelfLifeSearchResult(days=None, confidence=0.0, cost_micros_usd=None)

        cost = _gemini_cost(response, self._model)
        try:
            data = _json_object(response.text)
            return ShelfLifeSearchResult(
                days=int(data["days"]),
                confidence=float(data["confidence"]),
                cost_micros_usd=cost,
            )
        except Exception as exc:
            log.warning(
                "search_parse_failed", extra={"error_class": type(exc).__name__}
            )
            return ShelfLifeSearchResult(days=None, confidence=0.0, cost_micros_usd=cost)
