"""Exercise the installed OpenAI SDK against a mocked DeepSeek HTTP boundary."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI

from app.deepseek_llm import DeepSeekSearchClient, DeepSeekTextLLMClient
from app.llm import _cost_micros

_PEAK = int(datetime(2026, 10, 19, 2, tzinfo=UTC).timestamp())
_RESULTS = {
    "results": [
        {
            "title": "Food safety",
            "url": "https://example.org",
            "summary": "Milk keeps 7 days.",
        }
    ]
}


def _function(call_id="call_1", query="milk refrigerated shelf life"):
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": "web_search",
        "arguments": json.dumps({"query": query}),
        "status": "completed",
    }


def _response(*, calls=None, text='{"days":7,"confidence":0.9}', usage=True):
    output = (
        calls
        if calls is not None
        else [
            {
                "type": "message",
                "id": "msg_1",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ]
    )
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": _PEAK,
        "status": "completed",
        "model": "deepseek-flash",
        "output": output,
        "usage": {
            "input_tokens": 1000,
            "input_tokens_details": {"cached_tokens": 500},
            "output_tokens": 1000,
            "output_tokens_details": {"reasoning_tokens": 900},
            "total_tokens": 2000,
        }
        if usage
        else None,
    }


@asynccontextmanager
async def _sdk(replies, *, base_url="https://deepseek.test"):
    requests = []
    pending = iter(replies)

    def handle(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer test-only"
        body = json.loads(request.content)
        if (
            request.url.path.endswith("/responses")
            and body.get("reasoning", {}).get("effort") != "none"
        ):
            assert body.get("tool_choice") in {None, "auto", "none"}, (
                "DeepSeek thinking rejects forced tool choices"
            )
        reply = next(pending)
        if isinstance(reply, tuple):
            status, body = reply
        else:
            status, body = 200, reply
        return httpx.Response(status, json=body)

    async with AsyncOpenAI(
        api_key="test-only",
        base_url=base_url,
        max_retries=0,
        # SDK 3 accepts httpx 1 clients at runtime for compatibility, but its
        # annotation names only httpx2. Exercise that supported HTTP boundary.
        http_client=cast(Any, httpx.AsyncClient(transport=httpx.MockTransport(handle))),
    ) as sdk:
        yield sdk, requests


@pytest.mark.parametrize(
    "model,expected",
    [
        ("deepseek-flash", 1353),
        ("deepseek-v4-flash", 1353),
        ("deepseek-v4-flash-vision-exp", 1353),
        ("deepseek-v4-pro", 4642),
    ],
)
def test_token_rates_include_cache_and_reasoning_once(model, expected):
    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=1000,
        input_tokens_details=SimpleNamespace(cached_tokens=500),
        output_tokens_details=SimpleNamespace(reasoning_tokens=900),
    )
    assert (
        _cost_micros(SimpleNamespace(usage=usage, created_at=_PEAK), model) == expected
    )


@pytest.mark.parametrize(
    "day,hour,peak",
    [
        (19, 0, False),
        (19, 1, True),
        (19, 3, True),
        (19, 4, False),
        (19, 5, False),
        (19, 6, True),
        (19, 9, True),
        (19, 10, False),
        (17, 2, False),
        (18, 7, False),
    ],
)
def test_peak_windows_are_utc_and_exclude_weekends(day, hour, peak):
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=1000, output_tokens=0),
        created_at=datetime(2026, 10, day, hour, tzinfo=UTC).timestamp(),
    )
    assert _cost_micros(response, "deepseek-v4-pro") == (1320 if peak else 660)


def test_missing_timestamp_uses_peak_and_unknown_usage_stays_unknown():
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=1000, output_tokens=0)
    )
    assert _cost_micros(response, "deepseek-flash") == 300
    assert _cost_micros(response, "deepseek-unpriced") is None
    assert _cost_micros(SimpleNamespace(), "deepseek-flash") is None
    response.usage.input_tokens_details = SimpleNamespace(cached_tokens=1001)
    assert _cost_micros(response, "deepseek-flash") is None


@pytest.mark.parametrize("model", ["deepseek-flash", "deepseek-v4-pro"])
@pytest.mark.parametrize(
    "base_url", ["https://deepseek.test", "https://deepseek.test/v1"]
)
async def test_standalone_search_wire_payload_and_reasoning_replay(
    model, base_url, caplog
):
    reasoning = {
        "type": "reasoning",
        "id": "rs_1",
        "content": [{"type": "reasoning_text", "text": "private continuity marker"}],
        "summary": [],
    }
    async with _sdk(
        [_response(calls=[reasoning, _function()]), _RESULTS, _response()],
        base_url=base_url,
    ) as (sdk, requests):
        result = await DeepSeekSearchClient(sdk, model).lookup_shelf_life(
            name="milk", category="dairy"
        )
    assert result.days == 7
    assert result.confidence == 0.9
    assert result.cost_micros_usd == 750 + (2706 if model == "deepseek-flash" else 9284)
    assert requests[1].url.path == "/v1/tools/search"
    assert json.loads(requests[1].content) == {
        "query": "milk refrigerated shelf life",
        "count": 5,
        "need_content": False,
    }
    first, final = (json.loads(requests[n].content) for n in (0, 2))
    assert first["tools"][0]["type"] == "function"
    assert first["tool_choice"] == "auto"
    assert "Call the web_search function" in first["input"][0]["content"]
    assert final["reasoning"] == {"effort": "low"}
    assert final["max_output_tokens"] == 9216
    assert final["input"][2]["content"] == reasoning["content"]
    tool_output = final["input"][-1]
    assert tool_output["type"] == "function_call_output"
    assert tool_output["call_id"] == "call_1"
    assert json.loads(tool_output["output"]) == _RESULTS
    assert "private continuity marker" not in caplog.text


async def test_text_structured_parse_executes_search_and_totals_all_turns():
    final = _response(text='{"items":[]}')
    async with _sdk([_response(calls=[_function()]), _RESULTS, final]) as (
        sdk,
        requests,
    ):
        items, cost = await DeepSeekTextLLMClient(sdk, "deepseek-flash").parse_add(
            user_text="milk", today=date(2026, 10, 18), tz="UTC"
        )
    assert items == []
    assert cost == 3456
    for index in (0, 2):
        body = json.loads(requests[index].content)
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["reasoning"] == {"effort": "low"}


@pytest.mark.parametrize(
    "search_reply",
    [
        {"results": []},
        (400, {"error": {"message": "bad query"}}),
        (402, {"error": {"message": "insufficient balance"}}),
    ],
)
async def test_empty_or_failed_search_has_no_fee_and_no_ungrounded_answer(search_reply):
    sleep = AsyncMock()
    async with _sdk([_response(calls=[_function()]), search_reply, _response()]) as (
        sdk,
        requests,
    ):
        result = await DeepSeekSearchClient(
            sdk, "deepseek-flash", sleep=sleep
        ).lookup_shelf_life(name="milk", category=None)
    assert result.days is None
    assert result.confidence == 0
    assert result.cost_micros_usd == 2706
    assert len(requests) == 3
    sleep.assert_not_awaited()


async def test_transient_search_failure_retries_and_charges_only_success():
    sleep = AsyncMock()
    async with _sdk(
        [
            _response(calls=[_function()]),
            (429, {"error": {"message": "rate limited"}}),
            _RESULTS,
            _response(),
        ]
    ) as (sdk, requests):
        result = await DeepSeekSearchClient(
            sdk, "deepseek-flash", sleep=sleep
        ).lookup_shelf_life(name="milk", category=None)
    assert len(requests) == 4
    assert result.cost_micros_usd == 3456
    sleep.assert_awaited_once_with(1)


async def test_parallel_tool_calls_are_bounded_and_paired():
    calls = [_function(str(index)) for index in range(4)]
    async with _sdk(
        [_response(calls=calls), _RESULTS, _RESULTS, _RESULTS, _response()]
    ) as (sdk, requests):
        result = await DeepSeekSearchClient(sdk, "deepseek-flash").lookup_shelf_life(
            name="milk", category=None
        )
    assert result.days == 7
    assert result.cost_micros_usd == 4956
    final = json.loads(requests[-1].content)
    assert final["tools"] == []
    assert final["tool_choice"] == "none"
    outputs = [
        item for item in final["input"] if item.get("type") == "function_call_output"
    ]
    assert [item["call_id"] for item in outputs] == ["0", "1", "2", "3"]
    assert "error" in json.loads(outputs[-1]["output"])


@pytest.mark.parametrize("query", ["", " " * 10, "x" * 129, 123])
async def test_invalid_query_does_not_reach_search_api(query):
    async with _sdk([_response(calls=[_function(query=query)]), _response()]) as (
        sdk,
        requests,
    ):
        result = await DeepSeekSearchClient(sdk, "deepseek-flash").lookup_shelf_life(
            name="milk", category=None
        )
    assert len(requests) == 2
    assert result.days is None
    assert result.cost_micros_usd == 2706


async def test_missing_token_usage_keeps_total_unknown_after_paid_search():
    async with _sdk(
        [_response(calls=[_function()], usage=False), _RESULTS, _response()]
    ) as (sdk, _requests):
        result = await DeepSeekSearchClient(sdk, "deepseek-flash").lookup_shelf_life(
            name="milk", category=None
        )
    assert result.days == 7
    assert result.cost_micros_usd is None


async def test_malformed_model_answer_retains_search_and_token_costs():
    async with _sdk(
        [_response(calls=[_function()]), _RESULTS, _response(text="not json")]
    ) as (sdk, _requests):
        result = await DeepSeekSearchClient(sdk, "deepseek-flash").lookup_shelf_life(
            name="milk", category=None
        )
    assert result.days is None
    assert result.cost_micros_usd == 3456


async def test_sequential_search_turns_stop_at_three_and_replay_all_outputs():
    replies = []
    for index in range(3):
        replies.extend([_response(calls=[_function(str(index))]), _RESULTS])
    replies.append(_response())
    async with _sdk(replies) as (sdk, requests):
        result = await DeepSeekSearchClient(sdk, "deepseek-flash").lookup_shelf_life(
            name="milk", category=None
        )
    assert len(requests) == 7
    assert result.cost_micros_usd == 7662
    final = json.loads(requests[-1].content)
    assert final["tools"] == []
    assert final["tool_choice"] == "none"
    replayed = [
        item["call_id"]
        for item in final["input"]
        if item.get("type") == "function_call"
    ]
    assert replayed == ["0", "1", "2"]


async def test_unknown_function_cannot_execute_another_endpoint():
    call = _function()
    call["name"] = "fetch_private_data"
    async with _sdk([_response(calls=[call]), _response()]) as (sdk, requests):
        result = await DeepSeekSearchClient(sdk, "deepseek-flash").lookup_shelf_life(
            name="milk", category=None
        )
    assert len(requests) == 2
    assert result.days is None
    assert result.cost_micros_usd == 2706


async def test_thinking_search_without_a_tool_call_cannot_claim_web_evidence():
    async with _sdk([_response()]) as (sdk, requests):
        result = await DeepSeekSearchClient(sdk, "deepseek-flash").lookup_shelf_life(
            name="milk", category=None
        )
    assert len(requests) == 1
    assert result.days is None
    assert result.confidence == 0
    assert result.cost_micros_usd == 1353
