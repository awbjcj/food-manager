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
    qty: float = 1.0
    unit: Optional[str] = None
    category: Optional[Category] = None
    est_shelf_life_days: int = Field(ge=1, le=730)
    confidence: float = Field(ge=0.0, le=1.0)


class ParseResult(BaseModel):
    purchase_date: Optional[date] = None
    purchase_date_confidence: float = 0.0
    items: list[ParsedItem]


class LLMResult(BaseModel):
    parse: ParseResult
    cost_micros_usd: Optional[int] = None
    provider_usage: Optional[dict[str, Any]] = None


class LLMClient(Protocol):
    async def extract_items_from_image(self, image_bytes: bytes) -> LLMResult: ...


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


_PRICE_MICROS_PER_TOKEN_BY_MODEL = {
    "claude-sonnet-4-6": {"input": 3, "output": 15},
}


def _extract_json_text(message) -> str:
    parts = [b.text for b in message.content if getattr(b, "type", None) == "text"]
    return "".join(parts).strip()


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


class AnthropicLLMClient:
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

    async def extract_items_from_image(self, image_bytes: bytes) -> LLMResult:
        encoded = base64.b64encode(image_bytes).decode()
        user_content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": encoded,
                },
            },
            {"type": "text", "text": "Parse this receipt."},
        ]

        total_cost = 0
        unknown_cost = False
        for attempt in (0, 1):
            message = await self._create_message(user_content)
            cost = _cost_micros(message, self._model)
            if cost is None:
                unknown_cost = True
            else:
                total_cost += cost

            text = _extract_json_text(message)
            try:
                parsed = ParseResult.model_validate(json.loads(text))
                if not parsed.items:
                    raise ValueError("empty items")
                return LLMResult(
                    parse=parsed,
                    cost_micros_usd=None if unknown_cost else total_cost,
                )
            except Exception as exc:
                if attempt == 1:
                    log.warning(
                        "llm_json_validation_failed_final",
                        extra={"error_class": type(exc).__name__},
                    )
                    raise
                user_content = [
                    *user_content,
                    {
                        "type": "text",
                        "text": (
                            "Your last response did not match the schema "
                            f"(error: {type(exc).__name__}). "
                            "Return ONLY valid JSON matching the schema."
                        ),
                    },
                ]
        raise RuntimeError("unreachable")
