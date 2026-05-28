import asyncio
import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.llm import (
    AnthropicTextLLMClient,
    CorrectionDiff,
    ProposedAddItem,
    TextLLMClient,
)
from tests.fakes import FakeTextLLMClient


class _TextResponse:
    def __init__(self, text: str, input_tokens: int = 200, output_tokens: int = 60):
        self.content = [MagicMock(type="text", text=text)]
        self.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)


def test_text_llm_models_validate_ranges():
    with pytest.raises(Exception):
        CorrectionDiff(shelf_life_days=0, cache_action="leave", rationale="x", confidence=0.5)
    with pytest.raises(Exception):
        ProposedAddItem(name="Milk", explicit_user_expiry=False, confidence=1.5)


def test_fake_text_llm_matches_protocol():
    canned = CorrectionDiff(
        name="Heavy Cream",
        category="dairy",
        shelf_life_days=10,
        cache_action="move",
        rationale="user clarified item",
        confidence=0.92,
    )
    fake: TextLLMClient = FakeTextLLMClient(canned_correct=(canned, 350))
    diff, cost = asyncio.run(
        fake.parse_correct(
            item_snapshot={"id": 42},
            cache_snapshot=None,
            user_text="actually heavy cream",
            today=date(2026, 5, 27),
        )
    )
    assert diff.name == "Heavy Cream"
    assert cost == 350


@pytest.mark.asyncio
async def test_anthropic_text_llm_parse_correct_and_cost():
    raw = json.dumps({
        "name": "Heavy Cream",
        "category": "dairy",
        "shelf_life_days": 10,
        "cache_action": "move",
        "rationale": "user clarified identity",
        "confidence": 0.92,
    })
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(return_value=_TextResponse(raw))
    client = AnthropicTextLLMClient(sdk=sdk, model="claude-haiku-4-5-20251001")

    diff, cost = await client.parse_correct(
        item_snapshot={"id": 42, "raw_name": "Milk"},
        cache_snapshot=None,
        user_text="actually heavy cream",
        today=date(2026, 5, 27),
    )

    assert diff.name == "Heavy Cream"
    assert diff.cache_action == "move"
    assert cost == 500


@pytest.mark.asyncio
async def test_anthropic_text_llm_retries_malformed_json_once():
    good = json.dumps({"cache_action": "leave", "rationale": "ok", "confidence": 0.5})
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(side_effect=[
        _TextResponse("not json"),
        _TextResponse(good),
    ])
    client = AnthropicTextLLMClient(sdk=sdk, model="claude-haiku-4-5-20251001")

    diff, _ = await client.parse_correct(
        item_snapshot={},
        cache_snapshot=None,
        user_text="x",
        today=date(2026, 5, 27),
    )

    assert diff.name is None
    assert sdk.messages.create.await_count == 2


@pytest.mark.asyncio
async def test_anthropic_text_llm_parse_add_list():
    raw = json.dumps([
        {
            "name": "Oat Milk",
            "category": "beverage",
            "qty": 0.5,
            "unit": "gal",
            "explicit_user_expiry": True,
            "shelf_life_days": 10,
            "estimated_shelf_life_days": 10,
            "confidence": 0.88,
        }
    ])
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(return_value=_TextResponse(raw))
    client = AnthropicTextLLMClient(sdk=sdk, model="claude-haiku-4-5-20251001")

    items, cost = await client.parse_add(
        user_text="oat milk 10d",
        today=date(2026, 5, 27),
        tz="America/Detroit",
    )

    assert len(items) == 1
    assert items[0].name == "Oat Milk"
    assert items[0].explicit_user_expiry is True
    assert cost == 500
