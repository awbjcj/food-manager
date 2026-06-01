from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import date
from typing import Any, Literal, Optional, Protocol

from pydantic import BaseModel, Field

from app.profile_service import FoodProfile

Category = Literal[
    "dairy",
    "produce",
    "meat",
    "seafood",
    "bakery",
    "pantry",
    "frozen",
    "beverage",
    "other",
]


class ParsedItem(BaseModel):
    is_food: bool
    name: str
    qty: float = Field(default=1.0, gt=0)
    unit: Optional[str] = None
    category: Optional[Category] = None
    est_shelf_life_days: int = Field(ge=1, le=730)
    confidence: float = Field(ge=0.0, le=1.0)
    track_worthy: bool = True
    exclusion_reason: Optional[str] = None


class ParseResult(BaseModel):
    purchase_date: Optional[date] = None
    purchase_date_confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    items: list[ParsedItem]


class LLMResult(BaseModel):
    parse: ParseResult
    cost_micros_usd: Optional[int] = None
    provider_usage: Optional[dict[str, Any]] = None


CacheAction = Literal["move", "add_new", "leave"]


class CorrectionDiff(BaseModel):
    name: Optional[str] = None
    category: Optional[Category] = None
    expires_on: Optional[date] = None
    shelf_life_days: Optional[int] = Field(default=None, ge=1, le=730)
    cache_action: CacheAction = "leave"
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class ProposedAddItem(BaseModel):
    name: str
    category: Optional[Category] = None
    qty: float = Field(default=1.0, gt=0)
    unit: Optional[str] = None
    explicit_user_expiry: bool
    shelf_life_days: Optional[int] = Field(default=None, ge=1, le=730)
    expires_on: Optional[date] = None
    estimated_shelf_life_days: Optional[int] = Field(default=None, ge=1, le=730)
    confidence: float = Field(ge=0.0, le=1.0)


class ProposedAddItems(BaseModel):
    items: list[ProposedAddItem]


class LLMClient(Protocol):
    async def extract_items_from_image(
        self,
        image_bytes: bytes,
        *,
        image_media_type: str | None = None,
    ) -> LLMResult: ...


class TextLLMClient(Protocol):
    async def parse_correct(
        self,
        *,
        item_snapshot: dict[str, Any],
        cache_snapshot: Optional[dict[str, Any]],
        user_text: str,
        today: date,
    ) -> tuple[CorrectionDiff, Optional[int]]: ...

    async def parse_add(
        self,
        *,
        user_text: str,
        today: date,
        tz: str,
    ) -> tuple[list[ProposedAddItem], Optional[int]]: ...


class ProfileUpdateLLMClient(Protocol):
    async def parse_profile_update(
        self, *, current: FoodProfile, sentence: str
    ) -> tuple[FoodProfile, Optional[int]]: ...


log = logging.getLogger(__name__)
LLMProviderName = Literal["anthropic", "openai"]


SYSTEM_PROMPT = """You parse grocery receipt photos.
Return ONLY valid JSON matching the schema. No prose.

Receipt-level fields:
  - purchase_date: YYYY-MM-DD date shown on the receipt, or null if unreadable
  - purchase_date_confidence: 0.0-1.0 how sure you are about purchase_date

Return all recognizable purchased line items, excluding store metadata,
subtotals, totals, taxes, discounts, coupons, and payment lines. For each
returned line item:
  - is_food: true if this is a pantry-relevant food OR drink item (beverages
    like juice, milk drinks, soda, water all count), false for purchased
    non-food items such as paper towels or bags
  - name: clean human-readable name ("Whole Milk 1 gal"), expand abbreviations
  - qty: display-oriented purchased quantity (1.0 if ambiguous)
  - unit: "gal"|"lb"|"oz"|"g"|"kg"|"ml"|"l"|"ct"|"bunch"|"each"|null
  - category: "dairy"|"produce"|"meat"|"seafood"|"bakery"|"pantry"|"frozen"|"beverage"|"other"
  - est_shelf_life_days: integer 1..730. Conservative estimates. Examples:
        whole milk = 7, fresh chicken = 2, bananas = 5,
        canned beans = 365, fresh bread = 4, eggs = 28
  - confidence: 0.0-1.0
  - track_worthy: false for items not worth expiry-tracking even if edible:
    medicines/supplements/vitamins, condiments & sauces (ketchup, soy sauce,
    dressing, jam), spices & seasonings (salt, pepper, dried herbs),
    shelf-stable beverages (soda, bottled water, sealed juice, coffee beans,
    tea bags), and household/toiletries. true for genuinely perishable food,
    perishable drinks (fresh juice, milk-based drinks, opened cartons), AND
    legitimately stocked staples (canned beans, rice, pasta).
  - exclusion_reason: when track_worthy is false, one of "non_food",
    "shelf_stable", "household". null when track_worthy is true.

TODO(user): tune the example shelf-life values above to your kitchen.
"""

_PARSE_RECEIPT_TOOL = {
    "name": "parse_receipt",
    "description": "Submit the parsed receipt data.",
    "input_schema": ParseResult.model_json_schema(),
}

_PRICE_MICROS_PER_TOKEN_BY_MODEL = {
    "claude-sonnet-4-6": {"input": 3, "output": 15},
    "claude-haiku-4-5-20251001": {"input": 1, "output": 5},
    "gpt-5.4": {"input": 2.5, "output": 15},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.5},
}

_OPENAI_WEB_SEARCH_TOOL = {
    "type": "web_search",
    "search_context_size": "low",
}
_OPENAI_REASONING = {"effort": "low"}


def _extract_tool_input(message) -> dict:
    for block in message.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input  # type: ignore[return-value]
    raise ValueError(
        f"no tool_use block in response; stop_reason={getattr(message, 'stop_reason', '?')}"
    )


def _cost_micros(message, model: str) -> int | None:
    price = _PRICE_MICROS_PER_TOKEN_BY_MODEL.get(model)
    if price is None:
        return None
    usage = getattr(message, "usage", None)
    if usage is None:
        return None
    try:
        # round the total to whole micro-USD; per-token rates may be fractional
        # (e.g. OpenAI), while the integer Anthropic rates are unaffected.
        return round(
            usage.input_tokens * price["input"] + usage.output_tokens * price["output"]
        )
    except Exception:
        return None


def _usage_dict(message) -> dict[str, Any] | None:
    usage = getattr(message, "usage", None)
    if usage is None:
        return None
    model_dump = getattr(usage, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict):
            return dumped
    data = {
        key: getattr(usage, key)
        for key in ("input_tokens", "output_tokens", "total_tokens")
        if getattr(usage, key, None) is not None
    }
    return data or None


def _extract_openai_parsed(response):
    parsed = getattr(response, "output_parsed", None)
    if parsed is not None:
        return parsed

    for output in getattr(response, "output", []) or []:
        if getattr(output, "type", None) != "message":
            continue
        for item in getattr(output, "content", []) or []:
            refusal = getattr(item, "refusal", None)
            if getattr(item, "type", None) == "refusal" or refusal:
                raise ValueError(f"OpenAI refused response: {refusal or ''}".strip())
            parsed = getattr(item, "parsed", None)
            if parsed is not None:
                return parsed
    raise ValueError("no parsed content in OpenAI response")


class LLMProviderNotConfigured(ValueError):
    pass


class LLMProviderSelector(LLMClient):
    def __init__(
        self,
        clients: dict[str, LLMClient],
        default_provider: LLMProviderName,
    ):
        if default_provider not in clients:
            raise LLMProviderNotConfigured(default_provider)
        self._clients = clients
        self._default_provider: LLMProviderName = default_provider

    @property
    def available_providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._clients))

    @property
    def default_provider(self) -> LLMProviderName:
        return self._default_provider

    def for_provider(self, provider: str) -> LLMClient:
        try:
            return self._clients[provider]
        except KeyError as exc:
            raise LLMProviderNotConfigured(provider) from exc

    async def extract_items_from_image(
        self,
        image_bytes: bytes,
        *,
        image_media_type: str | None = None,
    ) -> LLMResult:
        return await self.for_provider(self._default_provider).extract_items_from_image(
            image_bytes,
            image_media_type=image_media_type,
        )


class TextLLMProviderSelector(TextLLMClient):
    def __init__(
        self,
        clients: dict[str, TextLLMClient],
        default_provider: LLMProviderName,
    ):
        if default_provider not in clients:
            raise LLMProviderNotConfigured(default_provider)
        self._clients = clients
        self._default_provider: LLMProviderName = default_provider

    @property
    def available_providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._clients))

    @property
    def default_provider(self) -> LLMProviderName:
        return self._default_provider

    def for_provider(self, provider: str) -> TextLLMClient:
        try:
            return self._clients[provider]
        except KeyError as exc:
            raise LLMProviderNotConfigured(provider) from exc

    async def parse_correct(
        self,
        *,
        item_snapshot: dict[str, Any],
        cache_snapshot: Optional[dict[str, Any]],
        user_text: str,
        today: date,
    ) -> tuple[CorrectionDiff, Optional[int]]:
        return await self.for_provider(self._default_provider).parse_correct(
            item_snapshot=item_snapshot,
            cache_snapshot=cache_snapshot,
            user_text=user_text,
            today=today,
        )

    async def parse_add(
        self,
        *,
        user_text: str,
        today: date,
        tz: str,
    ) -> tuple[list[ProposedAddItem], Optional[int]]:
        return await self.for_provider(self._default_provider).parse_add(
            user_text=user_text,
            today=today,
            tz=tz,
        )


class ProfileLLMProviderSelector(ProfileUpdateLLMClient):
    def __init__(
        self,
        clients: dict[str, ProfileUpdateLLMClient],
        default_provider: LLMProviderName,
    ):
        if default_provider not in clients:
            raise LLMProviderNotConfigured(default_provider)
        self._clients = clients
        self._default_provider: LLMProviderName = default_provider

    @property
    def available_providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._clients))

    @property
    def default_provider(self) -> LLMProviderName:
        return self._default_provider

    def for_provider(self, provider: str) -> ProfileUpdateLLMClient:
        try:
            return self._clients[provider]
        except KeyError as exc:
            raise LLMProviderNotConfigured(provider) from exc

    async def parse_profile_update(
        self, *, current: FoodProfile, sentence: str
    ) -> tuple[FoodProfile, Optional[int]]:
        return await self.for_provider(self._default_provider).parse_profile_update(
            current=current,
            sentence=sentence,
        )


# The cook-pipeline selectors are duck-typed rather than subclassing the cook
# Protocols (which live in app.cook_llm and import from this module), so that
# app.llm has no import dependency on app.cook_llm.
class SelectionLLMProviderSelector:
    def __init__(self, clients: dict, default_provider: LLMProviderName):
        if default_provider not in clients:
            raise LLMProviderNotConfigured(default_provider)
        self._clients = clients
        self._default_provider: LLMProviderName = default_provider

    @property
    def available_providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._clients))

    @property
    def default_provider(self) -> LLMProviderName:
        return self._default_provider

    def for_provider(self, provider: str):
        try:
            return self._clients[provider]
        except KeyError as exc:
            raise LLMProviderNotConfigured(provider) from exc

    async def select_items(self, *, prompt: str):
        return await self.for_provider(self._default_provider).select_items(prompt=prompt)


class RecipeLLMProviderSelector:
    def __init__(self, clients: dict, default_provider: LLMProviderName):
        if default_provider not in clients:
            raise LLMProviderNotConfigured(default_provider)
        self._clients = clients
        self._default_provider: LLMProviderName = default_provider

    @property
    def available_providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._clients))

    @property
    def default_provider(self) -> LLMProviderName:
        return self._default_provider

    def for_provider(self, provider: str):
        try:
            return self._clients[provider]
        except KeyError as exc:
            raise LLMProviderNotConfigured(provider) from exc

    async def fetch_recipes(self, *, prompt: str):
        return await self.for_provider(self._default_provider).fetch_recipes(prompt=prompt)


class NutritionLLMProviderSelector:
    def __init__(self, clients: dict, default_provider: LLMProviderName):
        if default_provider not in clients:
            raise LLMProviderNotConfigured(default_provider)
        self._clients = clients
        self._default_provider: LLMProviderName = default_provider

    @property
    def available_providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._clients))

    @property
    def default_provider(self) -> LLMProviderName:
        return self._default_provider

    def for_provider(self, provider: str):
        try:
            return self._clients[provider]
        except KeyError as exc:
            raise LLMProviderNotConfigured(provider) from exc

    async def score(self, *, prompt: str):
        return await self.for_provider(self._default_provider).score(prompt=prompt)


class AnthropicLLMClient(LLMClient):
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._sdk = sdk
        self._model = model
        self._sleep = sleep

    async def _create_message(self, user_content):
        for attempt in range(3):
            try:
                return await self._sdk.messages.create(
                    model=self._model,
                    max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    tools=[_PARSE_RECEIPT_TOOL],
                    tool_choice={"type": "tool", "name": "parse_receipt"},
                    messages=[{"role": "user", "content": user_content}],
                )
            except Exception as exc:
                if attempt == 2:
                    log.warning(
                        "llm_transport_failed_final",
                        extra={"error_class": type(exc).__name__},
                    )
                    raise
                log.warning(
                    "llm_transport_failed_retrying",
                    extra={"error_class": type(exc).__name__},
                )
                await self._sleep(2**attempt)
        raise RuntimeError("unreachable")

    async def extract_items_from_image(
        self,
        image_bytes: bytes,
        *,
        image_media_type: str | None = None,
    ) -> LLMResult:
        encoded = base64.b64encode(image_bytes).decode()
        user_content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image_media_type or _detect_media_type(image_bytes),
                    "data": encoded,
                },
            },
            {"type": "text", "text": "Parse this receipt."},
        ]

        message = await self._create_message(user_content)
        cost = _cost_micros(message, self._model)

        try:
            parsed = ParseResult.model_validate(_extract_tool_input(message))
        except Exception as exc:
            log.warning(
                "llm_json_validation_failed_final",
                extra={"error_class": type(exc).__name__, "error": str(exc)},
            )
            raise

        return LLMResult(
            parse=parsed,
            cost_micros_usd=cost,
        )


class OpenAILLMClient(LLMClient):
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._sdk = sdk
        self._model = model
        self._sleep = sleep

    async def _create_response(self, user_content):
        for attempt in range(3):
            try:
                return await self._sdk.responses.parse(
                    model=self._model,
                    input=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    tools=[_OPENAI_WEB_SEARCH_TOOL],
                    reasoning=_OPENAI_REASONING,
                    text_format=ParseResult,
                    max_output_tokens=2048,
                )
            except Exception as exc:
                if attempt == 2:
                    log.warning(
                        "llm_transport_failed_final",
                        extra={"error_class": type(exc).__name__},
                    )
                    raise
                log.warning(
                    "llm_transport_failed_retrying",
                    extra={"error_class": type(exc).__name__},
                )
                await self._sleep(2**attempt)
        raise RuntimeError("unreachable")

    async def extract_items_from_image(
        self,
        image_bytes: bytes,
        *,
        image_media_type: str | None = None,
    ) -> LLMResult:
        media_type = image_media_type or _detect_media_type(image_bytes)
        encoded = base64.b64encode(image_bytes).decode()
        user_content = [
            {"type": "input_text", "text": "Parse this receipt."},
            {
                "type": "input_image",
                "image_url": f"data:{media_type};base64,{encoded}",
                "detail": "high",
            },
        ]

        response = await self._create_response(user_content)
        try:
            parsed = ParseResult.model_validate(_extract_openai_parsed(response))
        except Exception as exc:
            log.warning(
                "llm_json_validation_failed_final",
                extra={"error_class": type(exc).__name__, "error": str(exc)},
            )
            raise

        return LLMResult(
            parse=parsed,
            cost_micros_usd=_cost_micros(response, self._model),
            provider_usage=_usage_dict(response),
        )


CORRECTION_SYSTEM_PROMPT = """You parse a user-supplied correction for a single pantry item.
Return ONLY valid JSON with exactly these snake_case keys. No prose.

Output schema (all keys required; use null for unchanged fields):
{
  "name":            string or null,           // corrected item name, null if unchanged
  "category":        string or null,           // one of: dairy|produce|meat|seafood|bakery|pantry|frozen|beverage|other, null if unchanged
  "expires_on":      "YYYY-MM-DD" or null,     // new expiry date, null if not stated
  "shelf_life_days": integer 1..730 or null,   // new shelf life in days, null if not stated
  "cache_action":    "move"|"add_new"|"leave", // always required
  "rationale":       string,                   // always required: one short clause
  "confidence":      float 0.0..1.0            // always required: parse confidence
}

You receive (in the user message):
  - item_snapshot: {id, raw_name, normalized_name, category, qty, unit,
                    purchased_on, shelf_life_days, expires_on, status}
  - cache_snapshot: null OR {normalized_name, days, category,
                              source, confidence, learned_at}
  - today: YYYY-MM-DD in the user's local timezone
  - user_text: free-form correction message

Rules:
  - Set ONLY the fields the user actually wants to change. Leave the
    others null.
  - Never set both shelf_life_days and expires_on; prefer the one
    the user stated more explicitly.
  - cache_action="move" when the user clarifies a misidentified item.
    "add_new" when both names are legitimate but distinct. "leave" when
    only date/category/days changes, or when uncertain.
  - rationale: one short clause explaining the change.
  - confidence: 0.0-1.0 of your parse, not of the food domain.

TODO(user): tune the move-vs-add_new boundary and category mapping to
the user's typical correction patterns.
"""


ADD_SYSTEM_PROMPT = """You parse a user-supplied "add to pantry" message into one or
more discrete items. Return ONLY valid JSON: a list of items matching
the ProposedAddItem schema.

For each item:
  - name: clean, expanded ("Oat Milk", not "OM 1/2 gal").
  - category: one of dairy|produce|meat|seafood|bakery|pantry|frozen|
              beverage|other. Null if unsure.
  - qty / unit: as the user stated; default qty=1.0; unit may be null.
  - explicit_user_expiry: true if the user explicitly stated a shelf
                          life ("keeps 10 days", "expires June 5"),
                          else false.
  - shelf_life_days: integer 1..730 ONLY if explicit_user_expiry is
                     true. Null otherwise.
  - expires_on: YYYY-MM-DD if the user stated an absolute date.
  - estimated_shelf_life_days: conservative food-domain estimate
                under normal storage, even when the user did not
                state expiry. Null only if genuinely unknown.
  - confidence: 0.0-1.0 of your parse.

Comma, semicolon, "and", and newline are valid item separators. Do
NOT invent items the user didn't mention.

TODO(user): tune separator handling and the "do not invent" guidance
against the user's typical /add patterns.
"""

OPENAI_ADD_SYSTEM_PROMPT = """You parse a user-supplied "add to pantry" message into
one or more discrete items. Return ONLY valid JSON matching the supplied schema:
an object with an "items" array of ProposedAddItem objects.

For each item:
  - name: clean, expanded ("Oat Milk", not "OM 1/2 gal").
  - category: one of dairy|produce|meat|seafood|bakery|pantry|frozen|
              beverage|other. Null if unsure.
  - qty / unit: as the user stated; default qty=1.0; unit may be null.
  - explicit_user_expiry: true if the user explicitly stated a shelf
                          life ("keeps 10 days", "expires June 5"),
                          else false.
  - shelf_life_days: integer 1..730 ONLY if explicit_user_expiry is
                     true. Null otherwise.
  - expires_on: YYYY-MM-DD if the user stated an absolute date.
  - estimated_shelf_life_days: conservative food-domain estimate
                under normal storage, even when the user did not
                state expiry. Null only if genuinely unknown.
  - confidence: 0.0-1.0 of your parse.

Comma, semicolon, "and", and newline are valid item separators. Do
NOT invent items the user didn't mention.

TODO(user): tune separator handling and the "do not invent" guidance
against the user's typical /add patterns.
"""


PROFILE_SYSTEM_PROMPT = """You maintain a user's food profile. You are given the
current profile as JSON and a new sentence. Return ONLY the updated profile as
JSON matching this schema (merge, do not drop existing values unless the user
clearly retracts them):
{
  "diet": "none|vegetarian|vegan|pescatarian|halal|kosher|other",
  "exclusions": [string],          // allergies and hard-avoid ingredients (lowercase singular)
  "preferred_cuisines": [string],  // e.g. ["chinese","american"]
  "max_cook_minutes": integer or null,
  "household_size": integer >= 1,
  "note": string                   // free-text preferences that don't fit a field
}
Add any newly stated allergy to "exclusions". No prose.
"""


def _extract_json_text(message) -> str:
    chunks: list[str] = []
    for block in message.content:
        if getattr(block, "type", None) == "text":
            chunks.append(block.text)
    if not chunks:
        raise ValueError("no text block in text-LLM response")
    text = "\n".join(chunks).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


class AnthropicTextLLMClient(TextLLMClient):
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._sdk = sdk
        self._model = model
        self._sleep = sleep

    async def _create_message(self, system: str, user_content):
        for attempt in range(3):
            try:
                return await self._sdk.messages.create(
                    model=self._model,
                    max_tokens=1024,
                    system=system,
                    messages=[{"role": "user", "content": user_content}],
                )
            except Exception as exc:
                if attempt == 2:
                    log.warning(
                        "text_llm_transport_failed_final",
                        extra={"error_class": type(exc).__name__},
                    )
                    raise
                log.warning(
                    "text_llm_transport_failed_retrying",
                    extra={"error_class": type(exc).__name__},
                )
                await self._sleep(2**attempt)
        raise RuntimeError("unreachable")

    async def _call_with_schema(self, system: str, user_text: str, parse_fn):
        user_content = [{"type": "text", "text": user_text}]
        total_cost = 0
        unknown_cost = False
        for attempt in (0, 1):
            message = await self._create_message(system, user_content)
            cost = _cost_micros(message, self._model)
            if cost is None:
                unknown_cost = True
            else:
                total_cost += cost
            text = _extract_json_text(message)
            try:
                return parse_fn(text), None if unknown_cost else total_cost
            except Exception as exc:
                if attempt == 1:
                    log.warning(
                        "text_llm_schema_failed_final",
                        extra={"error_class": type(exc).__name__},
                    )
                    raise
                user_content = [
                    *user_content,
                    {
                        "type": "text",
                        "text": (
                            "Your last response did not match the schema "
                            f"(error: {type(exc).__name__}). Return ONLY valid "
                            "JSON matching the schema."
                        ),
                    },
                ]
        raise RuntimeError("unreachable")

    async def parse_correct(
        self,
        *,
        item_snapshot,
        cache_snapshot,
        user_text,
        today,
    ) -> tuple[CorrectionDiff, Optional[int]]:
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

        return await self._call_with_schema(CORRECTION_SYSTEM_PROMPT, user_msg, _parse)

    async def parse_add(
        self,
        *,
        user_text,
        today,
        tz,
    ) -> tuple[list[ProposedAddItem], Optional[int]]:
        user_msg = json.dumps(
            {
                "today": today.isoformat(),
                "tz": tz,
                "user_text": user_text,
            }
        )

        def _parse(text: str) -> list[ProposedAddItem]:
            data = json.loads(text)
            if not isinstance(data, list):
                raise ValueError("parse_add expected a JSON array")
            return [ProposedAddItem.model_validate(item) for item in data]

        return await self._call_with_schema(ADD_SYSTEM_PROMPT, user_msg, _parse)


class OpenAITextLLMClient(TextLLMClient):
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._sdk = sdk
        self._model = model
        self._sleep = sleep

    async def _create_response(self, system: str, user_text: str, text_format):
        for attempt in range(3):
            try:
                return await self._sdk.responses.parse(
                    model=self._model,
                    input=[
                        {"role": "system", "content": system},
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": user_text}],
                        },
                    ],
                    tools=[_OPENAI_WEB_SEARCH_TOOL],
                    reasoning=_OPENAI_REASONING,
                    text_format=text_format,
                    max_output_tokens=1024,
                )
            except Exception as exc:
                if attempt == 2:
                    log.warning(
                        "text_llm_transport_failed_final",
                        extra={"error_class": type(exc).__name__},
                    )
                    raise
                log.warning(
                    "text_llm_transport_failed_retrying",
                    extra={"error_class": type(exc).__name__},
                )
                await self._sleep(2**attempt)
        raise RuntimeError("unreachable")

    async def parse_correct(
        self,
        *,
        item_snapshot,
        cache_snapshot,
        user_text,
        today,
    ) -> tuple[CorrectionDiff, Optional[int]]:
        user_msg = json.dumps(
            {
                "item_snapshot": item_snapshot,
                "cache_snapshot": cache_snapshot,
                "today": today.isoformat(),
                "user_text": user_text,
            }
        )
        response = await self._create_response(
            CORRECTION_SYSTEM_PROMPT,
            user_msg,
            CorrectionDiff,
        )
        return (
            CorrectionDiff.model_validate(_extract_openai_parsed(response)),
            _cost_micros(response, self._model),
        )

    async def parse_add(
        self,
        *,
        user_text,
        today,
        tz,
    ) -> tuple[list[ProposedAddItem], Optional[int]]:
        user_msg = json.dumps(
            {
                "today": today.isoformat(),
                "tz": tz,
                "user_text": user_text,
            }
        )
        response = await self._create_response(
            OPENAI_ADD_SYSTEM_PROMPT,
            user_msg,
            ProposedAddItems,
        )
        parsed = ProposedAddItems.model_validate(_extract_openai_parsed(response))
        return parsed.items, _cost_micros(response, self._model)


class AnthropicProfileLLMClient(ProfileUpdateLLMClient):
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._delegate = AnthropicTextLLMClient(sdk, model, sleep)

    async def parse_profile_update(self, *, current, sentence):
        user_msg = json.dumps({"current": current.model_dump(), "sentence": sentence})

        def _parse(text: str) -> FoodProfile:
            return FoodProfile.model_validate(json.loads(text))

        return await self._delegate._call_with_schema(
            PROFILE_SYSTEM_PROMPT, user_msg, _parse
        )


class OpenAIProfileLLMClient(ProfileUpdateLLMClient):
    def __init__(self, sdk, model: str, sleep=asyncio.sleep):
        self._delegate = OpenAITextLLMClient(sdk, model, sleep)

    async def parse_profile_update(self, *, current, sentence):
        user_msg = json.dumps({"current": current.model_dump(), "sentence": sentence})
        response = await self._delegate._create_response(
            PROFILE_SYSTEM_PROMPT, user_msg, FoodProfile
        )
        return (
            FoodProfile.model_validate(_extract_openai_parsed(response)),
            _cost_micros(response, self._delegate._model),
        )


def _detect_media_type(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"
