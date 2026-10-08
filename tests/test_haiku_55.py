"""Haiku 5.5 request compatibility and Standard-tier cost accounting."""

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.llm import AnthropicTextLLMClient, _cost_micros
from app.translation_llm import AnthropicTranslationLLMClient


@pytest.mark.parametrize("translation", [False, True])
async def test_haiku_55_text_clients_accept_thinking_before_json(translation):
    payload = (
        ["牛奶"]
        if translation
        else [{"name": "Milk", "confidence": 0.9, "explicit_user_expiry": False}]
    )
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking="", signature="opaque"),
            SimpleNamespace(type="text", text=json.dumps(payload)),
        ],
        usage=SimpleNamespace(input_tokens=200, output_tokens=60),
    )
    create = AsyncMock(return_value=response)
    sdk = SimpleNamespace(messages=SimpleNamespace(create=create))
    if translation:
        client = AnthropicTranslationLLMClient(sdk, "claude-haiku-5-5")
        result, cost = await client.translate(texts=["Milk"], lang="zh")
        assert result == ["牛奶"]
    else:
        client = AnthropicTextLLMClient(sdk, "claude-haiku-5-5")
        result, cost = await client.parse_add(
            user_text="milk", today=date(2026, 10, 8), tz="America/New_York"
        )
        assert result[0].name == "Milk"
    assert cost == 50
    kwargs = create.call_args.kwargs
    assert kwargs["model"] == "claude-haiku-5-5"
    assert kwargs["max_tokens"] == 8192
    assert not {"temperature", "top_p", "top_k", "thinking"} & kwargs.keys()
    assert kwargs["messages"][-1]["role"] == "user"


@pytest.mark.parametrize(
    ("input_tokens", "cache_read", "cache_write_5m", "cache_write_1h", "expected"),
    [
        (100_000, 0, 0, 0, 10_500),
        (100_001, 0, 0, 0, 52_500),
        (1000, 2000, 3000, 4000, 1795),
        (1, 100_000, 0, 0, 7500),
        (0, 0, 50_000, 50_001, 83_751),
    ],
)
def test_haiku_55_prices_full_prompt_tier_and_cache_categories(
    input_tokens, cache_read, cache_write_5m, cache_write_1h, expected
):
    usage = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=1000,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write_5m + cache_write_1h,
        cache_creation=SimpleNamespace(ephemeral_1h_input_tokens=cache_write_1h),
    )
    assert _cost_micros(SimpleNamespace(usage=usage), "claude-haiku-5-5") == expected
