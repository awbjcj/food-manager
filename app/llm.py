from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import date
from typing import Any, Literal, Optional, Protocol

from pydantic import BaseModel, Field


Category = Literal[
    "dairy", "produce", "meat", "seafood", "bakery",
    "pantry", "frozen", "beverage", "other",
]


class ParsedItem(BaseModel):
    is_food: bool
    name: str
    qty: float = Field(default=1.0, gt=0)
    unit: Optional[str] = None
    category: Optional[Category] = None
    est_shelf_life_days: int = Field(ge=1, le=730)
    confidence: float = Field(ge=0.0, le=1.0)


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


log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You parse grocery receipt photos.
Return ONLY valid JSON matching the schema. No prose.

Receipt-level fields:
  - purchase_date: YYYY-MM-DD date shown on the receipt, or null if unreadable
  - purchase_date_confidence: 0.0-1.0 how sure you are about purchase_date

Return all recognizable purchased line items, excluding store metadata,
subtotals, totals, taxes, discounts, coupons, and payment lines. For each
returned line item:
  - is_food: true if this is a pantry-relevant food item, false for purchased
    non-food items such as paper towels or bags
  - name: clean human-readable name ("Whole Milk 1 gal"), expand abbreviations
  - qty: display-oriented purchased quantity (1.0 if ambiguous)
  - unit: "gal"|"lb"|"oz"|"g"|"kg"|"ml"|"l"|"ct"|"bunch"|"each"|null
  - category: "dairy"|"produce"|"meat"|"seafood"|"bakery"|"pantry"|"frozen"|"beverage"|"other"
  - est_shelf_life_days: integer 1..730. Conservative estimates. Examples:
        whole milk = 7, fresh chicken = 2, bananas = 5,
        canned beans = 365, fresh bread = 4, eggs = 28
  - confidence: 0.0-1.0

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
}


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
        return (
            usage.input_tokens * price["input"]
            + usage.output_tokens * price["output"]
        )
    except Exception:
        return None


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
                await self._sleep(2 ** attempt)
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


CORRECTION_SYSTEM_PROMPT = """You parse a user-supplied correction for a single pantry item.
Return ONLY valid JSON matching the CorrectionDiff schema. No prose.

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
                await self._sleep(2 ** attempt)
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
        user_msg = json.dumps({
            "item_snapshot": item_snapshot,
            "cache_snapshot": cache_snapshot,
            "today": today.isoformat(),
            "user_text": user_text,
        })

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
        user_msg = json.dumps({
            "today": today.isoformat(),
            "tz": tz,
            "user_text": user_text,
        })

        def _parse(text: str) -> list[ProposedAddItem]:
            data = json.loads(text)
            if not isinstance(data, list):
                raise ValueError("parse_add expected a JSON array")
            return [ProposedAddItem.model_validate(item) for item in data]

        return await self._call_with_schema(ADD_SYSTEM_PROMPT, user_msg, _parse)


def _detect_media_type(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"
