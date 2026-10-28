"""Run the real Agno agents and vendor SDKs with HTTP responses supplied locally.

These tests exercise request serialization, structured-output parsing, and session
isolation without credentials, provider charges, or Telegram/database side effects.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date

import httpx
import httpx2
import pytest

from app.nl_intent import NLIntent, build_intent_agent
from app.profile_service import FoodProfile
from app.providers import ProviderCredentials
from app.week_composer import DaySpec, WeekPlanSpec, build_week_composer

PROVIDERS = [
    ("anthropic", "claude-haiku-5-5", "api.anthropic.com"),
    ("anthropic", "claude-opus-5-5", "api.anthropic.com"),
    ("anthropic", "claude-sonnet-5-5", "api.anthropic.com"),
    ("openai", "gpt-6.1-terra", "api.openai.com"),
    ("openai", "gpt-6.1-sol", "api.openai.com"),
    ("gemini", "gemini-3.8-flash", "generativelanguage.googleapis.com"),
    ("deepseek", "deepseek-v4-pro", "api.deepseek.com"),
]
TODAY = date(2026, 10, 15)


@pytest.fixture(autouse=True)
def disable_telemetry(monkeypatch):
    monkeypatch.setenv("AGNO_TELEMETRY", "false")


def _expected(seam):
    if seam == "intent":
        return NLIntent(kind="mark", mark_action="ate", item_name="yogurt")
    return WeekPlanSpec(
        days=[DaySpec(day_index=0, cuisine="thai", feature_items=["yogurt"])]
    )


def _response(provider, text):
    if provider == "anthropic":
        return {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-5-5",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }
    if provider == "gemini":
        return {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": text}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20},
        }
    return {
        "id": "chatcmpl_test",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


@asynccontextmanager
async def _agent(seam, provider, model_id, base_url=None, *, text=None, status=200):
    requests: list[httpx.Request | httpx2.Request] = []

    async def respond(request: httpx.Request | httpx2.Request):
        requests.append(request)
        # Let concurrently scheduled calls overlap at the transport boundary.
        await asyncio.sleep(0)
        body = (
            _response(
                provider,
                text if text is not None else _expected(seam).model_dump_json(),
            )
            if status == 200
            else {
                "error": {
                    "message": "Invalid API key",
                    "type": "authentication_error",
                    "code": 401,
                }
            }
        )
        return body

    # Keep the transport and response types paired for each HTTP library.
    async def respond_httpx(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=await respond(request))

    async def respond_httpx2(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json=await respond(request))

    credentials = ProviderCredentials(
        provider=provider,
        mode="subscription" if base_url else "api",
        api_key="test-key-not-real",
        base_url=base_url,
    )
    build = build_intent_agent if seam == "intent" else build_week_composer
    wrapper = build(model_id=model_id, credentials=credentials)
    model = wrapper._agent.model
    # Anthropic requires httpx2; the other adapters also accept an httpx client.
    client = (
        httpx2.AsyncClient(transport=httpx2.MockTransport(respond_httpx2))
        if provider == "anthropic"
        else httpx.AsyncClient(transport=httpx.MockTransport(respond_httpx))
    )
    async with client:
        # Preserve the builder's endpoint configuration and replace only transport.
        params = dict(model.client_params or {})
        if provider == "gemini":
            params["http_options"] = {
                **params.get("http_options", {}),
                "httpx_async_client": client,
            }
        else:
            params["http_client"] = client
        model.client_params = params
        try:
            yield wrapper, requests
        finally:
            if provider == "gemini" and model.client is not None:
                await model.client.aio.aclose()
                model.client.close()


async def _run(wrapper, seam, food="yogurt"):
    if seam == "intent":
        return await wrapper.parse(f"ate the {food}", today=TODAY, pantry_names=[food])
    return await wrapper.compose(
        pantry=[(food, 1)],
        profile=FoodProfile(),
        days=1,
        today=TODAY,
    )


@pytest.mark.parametrize(("provider", "model_id", "host"), PROVIDERS)
@pytest.mark.parametrize("seam", ["intent", "composer"])
@pytest.mark.parametrize("base_url", [None, "https://gateway.example/v1"])
async def test_real_agents_round_trip_structured_output(
    provider, model_id, host, seam, base_url
):
    async with _agent(seam, provider, model_id, base_url) as (wrapper, requests):
        result = await _run(wrapper, seam)
    expected = _expected(seam)
    assert result == (expected if seam == "intent" else expected.days)
    assert len(requests) == 1
    assert requests[0].url.host == ("gateway.example" if base_url else host)
    payload = json.loads(requests[0].content)
    if provider == "deepseek":
        # DeepSeek supports JSON mode, not OpenAI's strict json_schema format.
        assert payload["response_format"] == {"type": "json_object"}
        assert "properties" in json.dumps(payload["messages"])
    elif provider == "openai":
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True
    elif provider == "anthropic":
        assert payload["output_config"]["format"]["type"] == "json_schema"
        assert "output_format" not in payload
    else:
        assert payload["generationConfig"]["responseMimeType"] == "application/json"
        assert "properties" in payload["generationConfig"]["responseSchema"]


@pytest.mark.parametrize(("provider", "model_id", "_host"), PROVIDERS)
@pytest.mark.parametrize("seam", ["intent", "composer"])
@pytest.mark.parametrize("text", ["not JSON", '{"unexpected": "shape"}'])
async def test_real_agents_reject_malformed_output(
    provider, model_id, _host, seam, text
):
    async with _agent(seam, provider, model_id, text=text) as (wrapper, requests):
        with pytest.raises(ValueError, match="no structured"):
            await _run(wrapper, seam)
    assert len(requests) == 1


@pytest.mark.parametrize(("provider", "model_id", "_host"), PROVIDERS)
@pytest.mark.parametrize("seam", ["intent", "composer"])
async def test_provider_failure_does_not_become_an_action(
    provider, model_id, _host, seam
):
    async with _agent(seam, provider, model_id, status=401) as (wrapper, requests):
        with pytest.raises(ValueError, match="no structured"):
            await _run(wrapper, seam)
    assert len(requests) == 1


@pytest.mark.parametrize("seam", ["intent", "composer"])
async def test_reused_agents_isolate_sequential_and_concurrent_requests(
    seam, monkeypatch
):
    async with _agent(seam, "openai", "gpt-6.1-terra") as (wrapper, requests):
        outputs = []
        arun = wrapper._agent.arun

        async def record(*args, **kwargs):
            result = await arun(*args, **kwargs)
            outputs.append(result)
            return result

        monkeypatch.setattr(wrapper._agent, "arun", record)
        await _run(wrapper, seam, "yogurt")
        await asyncio.gather(_run(wrapper, seam, "milk"), _run(wrapper, seam, "rice"))

    assert len({output.session_id for output in outputs}) == 3
    assert len(requests) == 3
    prompts = []
    for request in requests:
        messages = json.loads(request.content)["messages"]
        users = [message for message in messages if message["role"] == "user"]
        assert len(users) == 1
        prompts.append(users[0]["content"])
    for food in ["yogurt", "milk", "rice"]:
        assert sum(food in prompt for prompt in prompts) == 1
