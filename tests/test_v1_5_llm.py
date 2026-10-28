import asyncio
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.llm import (
    AnthropicLLMClient,
    AnthropicTextLLMClient,
    CorrectionDiff,
    OpenAILLMClient,
    OpenAITextLLMClient,
    ParseResult,
    ProposedAddItem,
    ProposedAddItems,
    TextLLMClient,
)
from tests.fakes import FakeTextLLMClient


class _TextResponse:
    def __init__(self, text: str, input_tokens: int = 200, output_tokens: int = 60):
        self.content = [MagicMock(type="text", text=text)]
        self.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)


class _OpenAIParsedContent:
    type = "output_text"

    def __init__(self, parsed):
        self.parsed = parsed


class _OpenAIMessageOutput:
    type = "message"

    def __init__(self, parsed):
        self.content = [_OpenAIParsedContent(parsed)]


class _OpenAIParsedResponse:
    def __init__(self, parsed, input_tokens: int = 200, output_tokens: int = 60):
        self.output = [_OpenAIMessageOutput(parsed)]
        self.usage = SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )


def test_text_llm_models_validate_ranges():
    with pytest.raises(ValidationError):
        CorrectionDiff(shelf_life_days=0, cache_action="leave", rationale="x", confidence=0.5)
    with pytest.raises(ValidationError):
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
    client = AnthropicTextLLMClient(sdk=sdk, model="claude-haiku-5-5")

    diff, cost = await client.parse_correct(
        item_snapshot={"id": 42, "raw_name": "Milk"},
        cache_snapshot=None,
        user_text="actually heavy cream",
        today=date(2026, 5, 27),
    )

    assert diff.name == "Heavy Cream"
    assert diff.cache_action == "move"
    assert cost == 50


@pytest.mark.asyncio
async def test_anthropic_text_llm_retries_malformed_json_once():
    good = json.dumps({"cache_action": "leave", "rationale": "ok", "confidence": 0.5})
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(side_effect=[
        _TextResponse("not json"),
        _TextResponse(good),
    ])
    client = AnthropicTextLLMClient(sdk=sdk, model="claude-haiku-5-5")

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
    client = AnthropicTextLLMClient(sdk=sdk, model="claude-haiku-5-5")

    items, cost = await client.parse_add(
        user_text="oat milk 10d",
        today=date(2026, 5, 27),
        tz="America/Detroit",
    )

    assert len(items) == 1
    assert items[0].name == "Oat Milk"
    assert items[0].explicit_user_expiry is True
    assert cost == 50


@pytest.mark.asyncio
async def test_openai_llm_parse_receipt_image_and_usage():
    parsed = ParseResult(purchase_date=None, purchase_date_confidence=0.0, items=[])
    sdk = MagicMock()
    sdk.responses.parse = AsyncMock(
        return_value=_OpenAIParsedResponse(parsed, input_tokens=123, output_tokens=45)
    )
    client = OpenAILLMClient(sdk=sdk, model="gpt-6.1-terra")

    result = await client.extract_items_from_image(b"\x89PNG\r\n\x1a\npng")

    assert result.parse.items == []
    # Terra: 123*1 + 45*5 = 348 micro-USD.
    assert result.cost_micros_usd == 348
    assert result.provider_usage == {
        "input_tokens": 123,
        "output_tokens": 45,
        "total_tokens": 168,
    }
    kwargs = sdk.responses.parse.call_args.kwargs
    assert kwargs["model"] == "gpt-6.1-terra"
    assert kwargs["text_format"] is ParseResult
    assert kwargs["tools"] == [{"type": "web_search", "search_context_size": "low"}]
    assert kwargs["reasoning"] == {"effort": "low"}
    content = kwargs["input"][1]["content"]
    assert content[0] == {"type": "input_text", "text": "Parse this receipt."}
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/png;base64,")
    assert content[1]["detail"] == "high"


def test_cost_micros_prices_openai_models():
    from types import SimpleNamespace

    from app.llm import _cost_micros

    usage = SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=200))
    assert _cost_micros(usage, "gpt-6.1-terra") == 2000
    assert _cost_micros(usage, "gpt-6.1-sol") == 4000
    assert _cost_micros(usage, "claude-opus-5-5") == 8000
    assert _cost_micros(usage, "claude-sonnet-5-5") == 4000
    assert _cost_micros(usage, "claude-haiku-5-5") == 200
    # unknown model still yields None
    assert _cost_micros(usage, "gpt-unknown") is None


@pytest.mark.asyncio
async def test_new_reasoning_models_leave_room_for_visible_output():
    anthropic_sdk = MagicMock()
    anthropic_sdk.messages.create = AsyncMock(return_value=MagicMock())
    await AnthropicLLMClient(anthropic_sdk, "claude-opus-5-5")._create_message("receipt")
    assert anthropic_sdk.messages.create.call_args.kwargs["max_tokens"] == 8192
    await AnthropicLLMClient(anthropic_sdk, "claude-sonnet-5-5")._create_message("receipt")
    assert anthropic_sdk.messages.create.call_args.kwargs["max_tokens"] == 8192
    assert anthropic_sdk.messages.create.call_args.kwargs["tool_choice"] == {"type": "auto"}

    openai_sdk = MagicMock()
    openai_sdk.responses.parse = AsyncMock(return_value=MagicMock())
    await OpenAILLMClient(openai_sdk, "gpt-6.1-terra")._create_response("receipt")
    assert openai_sdk.responses.parse.call_args.kwargs["max_output_tokens"] == 8192
    await OpenAITextLLMClient(openai_sdk, "gpt-6.1-terra")._create_response(
        "system", "message", CorrectionDiff
    )
    assert openai_sdk.responses.parse.call_args.kwargs["max_output_tokens"] == 8192


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-sonnet-5-5"])
async def test_claude_5_5_receipt_parser_accepts_json_text_when_auto_tool_is_skipped(model):
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(return_value=_TextResponse(json.dumps({"items": []})))

    result = await AnthropicLLMClient(sdk, model).extract_items_from_image(
        b"receipt"
    )

    assert result.parse.items == []
    assert result.cost_micros_usd == (2000 if model == "claude-opus-5-5" else 1000)
    assert sdk.messages.create.call_args.kwargs["tool_choice"] == {"type": "auto"}


def test_openai_search_cost_micros_counts_web_search_call_items():
    from types import SimpleNamespace

    from app.llm import _add_cost, _cost_micros, _openai_search_cost_micros

    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        output=[
            SimpleNamespace(type="web_search_call"),
            SimpleNamespace(type="message"),
            SimpleNamespace(type="web_search_call"),
        ],
    )
    # 2 web_search_call items * $10/1000 = 20_000 micros
    assert _openai_search_cost_micros(response) == 20_000
    # Terra tokens cost 2000 micros, plus the 20_000 search fee.
    assert _add_cost(_cost_micros(response, "gpt-6.1-terra"), _openai_search_cost_micros(response)) == 22_000

    no_search = SimpleNamespace(output=[SimpleNamespace(type="message")])
    assert _openai_search_cost_micros(no_search) == 0

    no_output = SimpleNamespace()
    assert _openai_search_cost_micros(no_output) == 0


def test_correction_diff_schema_has_no_openai_incompatible_dict_fields():
    """OpenAI's structured-output strict mode requires additionalProperties:
    false on every object; a bare dict[str, str] field compiles to
    additionalProperties: {"type": "string"}, which the API rejects with a
    400 (this is what broke every /correct call on the OpenAI provider)."""
    schema = CorrectionDiff.model_json_schema()

    def _walk(node):
        if isinstance(node, dict):
            additional = node.get("additionalProperties")
            if additional not in (None, False):
                raise AssertionError(f"disallowed additionalProperties: {node}")
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(schema)


@pytest.mark.asyncio
async def test_openai_text_llm_parse_add_uses_terra_and_wrapper_schema():
    parsed = ProposedAddItems(items=[
        ProposedAddItem(
            name="Oat Milk",
            category="beverage",
            qty=1.0,
            explicit_user_expiry=False,
            estimated_shelf_life_days=10,
            confidence=0.88,
        )
    ])
    sdk = MagicMock()
    sdk.responses.parse = AsyncMock(return_value=_OpenAIParsedResponse(parsed))
    client = OpenAITextLLMClient(sdk=sdk, model="gpt-6.1-terra")

    items, cost = await client.parse_add(
        user_text="oat milk",
        today=date(2026, 5, 27),
        tz="America/Detroit",
    )

    assert [item.name for item in items] == ["Oat Milk"]
    # Terra: 200*1 + 60*5 = 500 micro-USD.
    assert cost == 500
    kwargs = sdk.responses.parse.call_args.kwargs
    assert kwargs["model"] == "gpt-6.1-terra"
    assert kwargs["text_format"] is ProposedAddItems
    assert kwargs["tools"] == [{"type": "web_search", "search_context_size": "low"}]
    assert kwargs["reasoning"] == {"effort": "low"}
