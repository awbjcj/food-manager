"""Terra Standard pricing and installed-SDK requests against a local HTTP mock."""

import json
from types import SimpleNamespace

import httpx2
import pytest
from openai import AsyncOpenAI

from app.cook.llm import _OpenAIJSONClient
from app.llm import OpenAILLMClient, ParseResult, _cost_micros


@pytest.mark.parametrize("cook", [False, True])
async def test_terra_responses_payload_and_structured_output(cook):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(200, json={
            "id": "resp_terra",
            "object": "response",
            "created_at": 1793145600,
            "status": "completed",
            "model": "gpt-6.1-terra",
            "output": [{
                "type": "message", "id": "msg_terra", "status": "completed",
                "role": "assistant",
                "content": [{
                    "type": "output_text", "text": '{"items": []}', "annotations": [],
                }],
            }],
            "usage": {
                "input_tokens": 1000,
                "input_tokens_details": {
                    "cached_tokens": 200, "cache_write_tokens": 400,
                    "cache_write_tokens_details": {"ttl_30m_tokens": 300, "ttl_12h_tokens": 100},
                },
                "output_tokens": 200,
                "output_tokens_details": {"reasoning_tokens": 100},
                "total_tokens": 1200,
            },
        })

    async with AsyncOpenAI(
        api_key="test-only", base_url="https://openai.test/v1", max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
    ) as sdk:
        if cook:
            parsed, cost = await _OpenAIJSONClient(
                sdk, "gpt-6.1-terra", web_search=True, max_tool_calls=1,
            )._call("Return receipt data.", "milk", ParseResult)
        else:
            result = await OpenAILLMClient(sdk, "gpt-6.1-terra").extract_items_from_image(
                b"\x89PNG\r\n\x1a\npng"
            )
            parsed, cost = result.parse, result.cost_micros_usd

    assert parsed.items == []
    assert cost == 2035  # 400 ordinary + 10 read + 375/250 writes + 1000 output
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/responses"
    body = json.loads(requests[0].content)
    assert body["model"] == "gpt-6.1-terra"
    assert body["reasoning"] == {"effort": "low"}
    assert body["max_output_tokens"] == 8192
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    assert body["tools"] == [{"type": "web_search", "search_context_size": "low"}]
    assert not {"temperature", "top_p", "top_logprobs"} & body.keys()
    if cook:
        assert body["max_tool_calls"] == 1
    else:
        assert body["input"][1]["content"][1]["type"] == "input_image"


@pytest.mark.parametrize(
    "input_tokens,cache_read,write_30m,write_12h,expected",
    [
        (1000, 0, 0, 0, 2000),
        (1000, 200, 300, 100, 2035),
        (272_000, 0, 0, 0, 273_000),
        (272_001, 0, 0, 0, 545_502),
        (272_001, 200, 300, 100, 545_572),
    ],
)
def test_terra_prices_disjoint_cache_buckets_and_full_request_tier(
    input_tokens, cache_read, write_30m, write_12h, expected,
):
    usage = SimpleNamespace(
        input_tokens=input_tokens, output_tokens=200,
        input_tokens_details=SimpleNamespace(
            cached_tokens=cache_read, cache_write_tokens=write_30m + write_12h,
            cache_write_tokens_details=SimpleNamespace(
                ttl_30m_tokens=write_30m, ttl_12h_tokens=write_12h,
            ),
        ),
        output_tokens_details=SimpleNamespace(reasoning_tokens=100),
    )
    assert _cost_micros(SimpleNamespace(usage=usage), "gpt-6.1-terra") == expected


@pytest.mark.parametrize("details", [
    SimpleNamespace(cached_tokens=1100, cache_write_tokens=0),
    SimpleNamespace(cached_tokens=0, cache_write_tokens=500),
    SimpleNamespace(
        cached_tokens=0, cache_write_tokens=500,
        cache_write_tokens_details=SimpleNamespace(ttl_30m_tokens=600, ttl_12h_tokens=0),
    ),
])
def test_terra_invalid_or_unspecified_write_usage_stays_unknown(details):
    usage = SimpleNamespace(input_tokens=1000, output_tokens=200, input_tokens_details=details)
    assert _cost_micros(SimpleNamespace(usage=usage), "gpt-6.1-terra") is None
