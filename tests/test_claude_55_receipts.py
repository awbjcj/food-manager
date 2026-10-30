"""Current Claude receipt clients using the installed SDK and local HTTP mocks."""

import json

import httpx2
import pytest
from anthropic import AsyncAnthropic

from app.llm import AnthropicLLMClient


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-sonnet-5-5"])
@pytest.mark.parametrize("tool_call", [False, True])
async def test_receipt_accepts_auto_tool_or_json_response(model, tool_call):
    requests = []

    def handle(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert payload["tool_choice"] == {"type": "auto"}
        content = (
            {"type": "tool_use", "id": "tool_test", "name": "parse_receipt", "input": {"items": []}}
            if tool_call else {"type": "text", "text": '{"items": []}'}
        )
        return httpx2.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": model,
            "content": [content], "stop_sequence": None,
            "stop_reason": "tool_use" if tool_call else "end_turn",
            "usage": {"input_tokens": 200, "output_tokens": 60},
        })

    async with AsyncAnthropic(
        api_key="test-only", base_url="https://anthropic.test", max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
    ) as sdk:
        result = await AnthropicLLMClient(sdk, model).extract_items_from_image(b"receipt")

    assert result.parse.items == []
    assert result.cost_micros_usd == (2000 if model == "claude-opus-5-5" else 1000)
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload["model"] == model
    assert payload["max_tokens"] == 8192
    assert "Call the parse_receipt tool" in payload["system"]
    assert not {"temperature", "top_p", "top_k"} & payload.keys()


@pytest.mark.parametrize("tool_call", [False, True])
@pytest.mark.parametrize("repair_succeeds", [False, True])
async def test_receipt_schema_repair_is_bounded_and_accounts_both_calls(tool_call, repair_succeeds):
    from pydantic import ValidationError

    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        valid = repair_succeeds and len(requests) == 2
        data = {"items": []} if valid else {"items": [{
            "is_food": True, "name": "Milk", "est_shelf_life_days": -1, "confidence": 1,
        }]}
        content = (
            {"type": "tool_use", "id": "tool_test", "name": "parse_receipt", "input": data}
            if tool_call else {"type": "text", "text": json.dumps(data)}
        )
        return httpx2.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant",
            "model": "claude-sonnet-5-5", "content": [content],
            "stop_sequence": None, "stop_reason": "tool_use" if tool_call else "end_turn",
            "usage": {"input_tokens": 200, "output_tokens": 60},
        })

    async with AsyncAnthropic(
        api_key="test-only", base_url="https://anthropic.test", max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
    ) as sdk:
        client = AnthropicLLMClient(sdk, "claude-sonnet-5-5")
        if repair_succeeds:
            result = await client.extract_items_from_image(b"receipt")
            assert result.parse.items == [] and result.cost_micros_usd == 2000
        else:
            with pytest.raises(ValidationError):
                await client.extract_items_from_image(b"receipt")
    assert len(requests) == 2
    original = requests[0]["messages"][0]["content"]
    repaired = requests[1]["messages"][0]["content"]
    assert repaired[:len(original)] == original
    assert "est_shelf_life_days" in repaired[-1]["text"]
    assert "failed validation" in repaired[-1]["text"]
    assert requests[1]["tool_choice"] == {"type": "auto"}
