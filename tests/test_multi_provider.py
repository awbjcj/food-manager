"""Tests for the gemini/deepseek provider expansion (Approach C).

Covers the central provider plumbing (capabilities + fallback selector), the
widened command/settings surface, the run.py wiring matrix, and a happy-path +
schema-repair pass through the new Gemini and DeepSeek clients with faked SDKs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.commands import CommandError, parse_llm_provider
from app.deepseek_llm import DeepSeekSelectionLLM, DeepSeekTextLLMClient
from app.gemini_llm import GeminiLLMClient, GeminiSearchClient, GeminiSelectionLLM
from app.llm import ParseResult, ProposedAddItems
from app.providers import (
    ALL_PROVIDERS,
    LLMProviderNotConfigured,
    ProviderSelector,
    supports,
)
from app.settings import Settings


# --------------------------------------------------------------------------- #
# providers.py: capabilities + generic selector fallback
# --------------------------------------------------------------------------- #
def test_capability_matrix():
    assert supports("gemini", "image") and supports("gemini", "search")
    assert supports("deepseek", "text")
    assert supports("deepseek", "search")
    assert not supports("deepseek", "image")
    assert set(ALL_PROVIDERS) == {"anthropic", "openai", "gemini", "deepseek"}


def test_selector_returns_exact_when_present():
    a, g = object(), object()
    sel = ProviderSelector({"anthropic": a, "gemini": g}, "anthropic", fallback=True)
    assert sel.for_provider("gemini") is g
    assert sel.available_providers == ("anthropic", "gemini")


def test_selector_fallback_routes_to_default():
    a, g = object(), object()
    # default present -> fall back to default
    sel = ProviderSelector({"anthropic": a, "gemini": g}, "anthropic", fallback=True)
    assert sel.for_provider("deepseek") is a
    # single-provider map -> any miss falls back to that lone default
    sel2 = ProviderSelector({"gemini": g}, "gemini", fallback=True)
    assert sel2.for_provider("deepseek") is g


def test_selector_without_fallback_raises():
    sel = ProviderSelector({"anthropic": object()}, "anthropic")
    with pytest.raises(LLMProviderNotConfigured):
        sel.for_provider("deepseek")


# --------------------------------------------------------------------------- #
# commands.py: /llm accepts the new providers
# --------------------------------------------------------------------------- #
def test_parse_llm_provider_accepts_new_providers():
    assert parse_llm_provider(["gemini"]) == "gemini"
    assert parse_llm_provider(["DeepSeek"]) == "deepseek"
    with pytest.raises(CommandError):
        parse_llm_provider(["mistral"])


# --------------------------------------------------------------------------- #
# settings.py: validation around a text-only default provider
# --------------------------------------------------------------------------- #
def _settings(**overrides):
    base = {
        "TELEGRAM_BOT_TOKEN": "token",
        "ALLOWED_TELEGRAM_USER_ID": 1,
        "ANTHROPIC_API_KEY": None,
        "OPENAI_API_KEY": None,
        "GEMINI_API_KEY": None,
        "DEEPSEEK_API_KEY": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_settings_requires_chosen_provider_key():
    with pytest.raises(ValueError, match="GEMINI_API_KEY is required"):
        _settings(LLM_PROVIDER="gemini")


def test_settings_deepseek_default_needs_image_capable_key():
    # DeepSeek can't read photos; without a capable provider the bot can't ingest.
    with pytest.raises(ValueError, match="cannot process images"):
        _settings(LLM_PROVIDER="deepseek", DEEPSEEK_API_KEY="ds-key")


def test_settings_deepseek_default_ok_with_capable_key():
    s = _settings(
        LLM_PROVIDER="deepseek",
        DEEPSEEK_API_KEY="ds-key",
        ANTHROPIC_API_KEY="a-key",
    )
    assert s.llm_provider == "deepseek"


def test_settings_gemini_default_ok():
    s = _settings(LLM_PROVIDER="gemini", GEMINI_API_KEY="g-key")
    assert s.llm_provider == "gemini"


# --------------------------------------------------------------------------- #
# run.py: the wiring matrix across all four providers
# --------------------------------------------------------------------------- #
def test_build_llm_clients_capability_matrix():
    from bin.run import _build_llm_clients

    settings = _settings(
        LLM_PROVIDER="anthropic",
        ANTHROPIC_API_KEY="a",
        OPENAI_API_KEY="o",
        GEMINI_API_KEY="g",
        DEEPSEEK_API_KEY="d",
    )
    bundle = _build_llm_clients(settings)

    # Text is the floor every provider meets.
    assert bundle.text.available_providers == ("anthropic", "deepseek", "gemini", "openai")
    # Image excludes deepseek (still no image input).
    assert bundle.image.available_providers == ("anthropic", "gemini", "openai")
    # Search now wired for deepseek too (native web_search tool).
    assert bundle.search is not None
    assert bundle.search.available_providers == ("anthropic", "deepseek", "gemini")
    # Recipe (search-backed) excludes deepseek — no image, so no recipe client.
    assert "deepseek" not in bundle.recipe.available_providers

    # A deepseek user's text and search tasks use deepseek directly; a photo falls back.
    assert type(bundle.text.for_provider("deepseek")).__name__ == "DeepSeekTextLLMClient"
    assert type(bundle.search.for_provider("deepseek")).__name__ == "DeepSeekSearchClient"
    assert bundle.image.for_provider("deepseek") is bundle.image.for_provider("anthropic")


def test_build_llm_clients_deepseek_default_seeds_capable_image():
    from bin.run import _build_llm_clients

    settings = _settings(
        LLM_PROVIDER="deepseek",
        DEEPSEEK_API_KEY="d",
        GEMINI_API_KEY="g",
    )
    bundle = _build_llm_clients(settings)
    # Image seed default must be capable even though the global default
    # (deepseek) is not — it falls back to gemini.
    assert bundle.image.for_provider("deepseek") is bundle.image.for_provider("gemini")
    # Search is deepseek's own native client now — deepseek is capable, so the
    # global default itself seeds the search selector; no fallback needed.
    assert bundle.search is not None
    assert type(bundle.search.for_provider("deepseek")).__name__ == "DeepSeekSearchClient"
    assert bundle.search.default_provider == "deepseek"


# --------------------------------------------------------------------------- #
# DeepSeek client: Responses API structured output (faked SDK)
# --------------------------------------------------------------------------- #
class _FakeResponsesOutputContent:
    type = "output_text"

    def __init__(self, parsed):
        self.parsed = parsed


class _FakeResponsesMessageOutput:
    type = "message"

    def __init__(self, parsed):
        self.content = [_FakeResponsesOutputContent(parsed)]


class _FakeDeepSeekResponse:
    def __init__(
        self, parsed, *, input_tokens=10, output_tokens=5, web_search_calls=0
    ):
        self.output = [_FakeResponsesMessageOutput(parsed)] + [
            SimpleNamespace(type="web_search_call") for _ in range(web_search_calls)
        ]
        self.output_parsed = None  # force _extract_openai_parsed to walk .output
        self.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)


class _FakeResponses:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class FakeDeepSeekSDK:
    def __init__(self, responses):
        self.responses = _FakeResponses(responses)


async def test_deepseek_parse_add_happy_path():
    from app.llm import ProposedAddItem

    sdk = FakeDeepSeekSDK(
        [
            _FakeDeepSeekResponse(
                ProposedAddItems(
                    items=[
                        ProposedAddItem(
                            name="Oat Milk",
                            explicit_user_expiry=False,
                            confidence=0.9,
                        )
                    ]
                )
            )
        ]
    )
    client = DeepSeekTextLLMClient(sdk, "deepseek-v4-flash")
    from datetime import date

    items, cost = await client.parse_add(
        user_text="oat milk", today=date(2026, 6, 29), tz="UTC"
    )
    assert [i.name for i in items] == ["Oat Milk"]
    assert cost == round(10 * 0.14 + 5 * 0.28)  # deepseek-v4-flash pricing
    # every text call carries the native web_search tool
    assert sdk.responses.calls[0]["tools"] == [
        {"type": "web_search", "search_context_size": "low"}
    ]


async def test_deepseek_selection_has_no_web_search_tool():
    from app.cook.models import SelectedItems

    sdk = FakeDeepSeekSDK(
        [_FakeDeepSeekResponse(SelectedItems(item_ids=[1, 2], rationale="x"))]
    )
    client = DeepSeekSelectionLLM(sdk, "deepseek-v4-flash")
    selected, _cost = await client.select_items(prompt="pick stuff")
    assert selected.item_ids == [1, 2]
    # selection is not a web-search-eligible seam, matching OpenAISelectionLLM
    assert sdk.responses.calls[0]["tools"] == []


async def test_deepseek_selection_requests_headroom_beyond_the_reply_budget():
    """DeepSeek's reasoning tokens count against max_output_tokens, so the
    request must ask for more than the intended JSON reply size or a real
    call can truncate mid-JSON and fail to parse (observed as /cook failing
    right after a 200 OK, reasoning-only response)."""
    from app.cook.models import SelectedItems
    from app.deepseek_llm import _REASONING_HEADROOM_TOKENS

    sdk = FakeDeepSeekSDK(
        [_FakeDeepSeekResponse(SelectedItems(item_ids=[1, 2], rationale="x"))]
    )
    client = DeepSeekSelectionLLM(sdk, "deepseek-v4-flash")
    await client.select_items(prompt="pick stuff")

    assert sdk.responses.calls[0]["reasoning"] == {"effort": "low"}
    assert sdk.responses.calls[0]["max_output_tokens"] == 1024 + _REASONING_HEADROOM_TOKENS


async def test_deepseek_selection_accepts_responses_output_text():
    """DeepSeek returns structured JSON in output_text, not parsed fields."""
    sdk = FakeDeepSeekSDK(
        [
            SimpleNamespace(
                output_parsed=None,
                output=[],
                output_text='{"item_ids": [1, 2], "rationale": "coherent"}',
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )
        ]
    )
    client = DeepSeekSelectionLLM(sdk, "deepseek-v4-flash")

    selected, _cost = await client.select_items(prompt="pick stuff")

    assert selected.item_ids == [1, 2]


async def test_deepseek_selection_raises_on_malformed_output_text():
    """A non-JSON output_text must fail loudly with the parse error, not be
    swallowed into the generic "no parsed content" message."""
    sdk = FakeDeepSeekSDK(
        [
            SimpleNamespace(
                output_parsed=None,
                output=[],
                output_text="not json",
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )
        ]
    )
    client = DeepSeekSelectionLLM(sdk, "deepseek-v4-flash")

    with pytest.raises(ValueError, match="not valid JSON"):
        await client.select_items(prompt="pick stuff")


async def test_deepseek_search_tool_cost_is_unknown_when_invoked():
    """DeepSeek doesn't publish a web_search price, so a response with a
    web_search_call in it must report cost as unknown, not silently
    under-priced at token cost alone."""
    from app.deepseek_llm import DeepSeekSearchClient

    sdk = FakeDeepSeekSDK(
        [_FakeDeepSeekResponse({"days": 10, "confidence": 0.9}, web_search_calls=1)]
    )
    sdk.responses._responses[0].output_text = '{"days": 10, "confidence": 0.9}'
    client = DeepSeekSearchClient(sdk, "deepseek-v4-flash")
    result = await client.lookup_shelf_life(name="milk", category="dairy")
    assert result.days == 10
    assert result.confidence == 0.9
    assert result.cost_micros_usd is None


# --------------------------------------------------------------------------- #
# Gemini client: structured output, image, and search (faked genai client)
# --------------------------------------------------------------------------- #
class _FakeAioModels:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class FakeGenAIClient:
    def __init__(self, responses):
        self.aio = SimpleNamespace(models=_FakeAioModels(responses))


def _gemini_response(text, *, parsed=None, in_tokens=10, out_tokens=4):
    return SimpleNamespace(
        text=text,
        parsed=parsed,
        usage_metadata=SimpleNamespace(
            prompt_token_count=in_tokens,
            candidates_token_count=out_tokens,
            total_token_count=in_tokens + out_tokens,
        ),
    )


async def test_gemini_selection_structured_from_text():
    client = FakeGenAIClient([_gemini_response('{"item_ids":[3],"rationale":"r"}')])
    selected, cost = await GeminiSelectionLLM(client, "gemini-2.5-flash").select_items(
        prompt="x"
    )
    assert selected.item_ids == [3]
    assert cost == round(10 * 0.3 + 4 * 2.5)  # 13
    # structured (non-search) call must request JSON mode, not a search tool.
    cfg = client.aio.models.calls[0]["config"]
    assert cfg.response_mime_type == "application/json"
    assert not cfg.tools


async def test_gemini_image_extract():
    client = FakeGenAIClient([_gemini_response('{"items":[]}')])
    result = await GeminiLLMClient(client, "gemini-2.5-flash").extract_items_from_image(
        b"\x89PNG\r\n\x1a\n", image_media_type="image/png"
    )
    assert result.parse.items == []
    assert result.cost_micros_usd == round(10 * 0.3 + 4 * 2.5)


@pytest.mark.parametrize("model_cls", [ParseResult, ProposedAddItems])
def test_gemini_structured_models_produce_valid_schema(model_cls):
    """Every model_cls passed as Gemini's response_schema must survive the SDK's
    real ``t_schema`` conversion — the fakes above stub out the SDK entirely, so
    they never catch schema fields Gemini's API rejects (e.g. Pydantic's
    ``gt=``/``lt=`` constraints emit ``exclusiveMinimum``, which ``types.Schema``
    has no field for and rejects with a ValidationError)."""
    from google.genai._transformers import t_schema

    t_schema(None, model_cls)


async def test_gemini_search_uses_grounding_and_parses_json():
    client = FakeGenAIClient(
        [_gemini_response('Here you go: {"days": 5, "confidence": 0.9}')]
    )
    res = await GeminiSearchClient(client, "gemini-2.5-flash").lookup_shelf_life(
        name="milk", category="dairy"
    )
    assert res.days == 5 and res.confidence == 0.9
    # search path enables the google_search tool (no structured-output schema).
    cfg = client.aio.models.calls[0]["config"]
    assert cfg.tools
    assert cfg.response_schema is None


async def test_gemini_search_adds_grounding_query_fee_for_v3_model():
    response = _gemini_response('{"days": 5, "confidence": 0.9}')
    response.candidates = [
        SimpleNamespace(
            grounding_metadata=SimpleNamespace(web_search_queries=["milk shelf life", ""])
        )
    ]
    client = FakeGenAIClient([response])
    res = await GeminiSearchClient(client, "gemini-3.1-pro-preview").lookup_shelf_life(
        name="milk", category="dairy"
    )
    # tokens: 10*2.0 + 4*12.0 = 68; grounding: 1 non-empty query * $14/1000 = 14_000
    assert res.cost_micros_usd == 68 + 14_000


async def test_gemini_search_adds_grounding_prompt_fee_for_legacy_model():
    response = _gemini_response('{"days": 5, "confidence": 0.9}')
    response.candidates = [
        SimpleNamespace(
            grounding_metadata=SimpleNamespace(
                web_search_queries=["milk shelf life", "milk expiry"]
            )
        )
    ]
    client = FakeGenAIClient([response])
    res = await GeminiSearchClient(client, "gemini-2.5-flash").lookup_shelf_life(
        name="milk", category="dairy"
    )
    # tokens: 10*0.3 + 4*2.5 = 13; grounding: billed per PROMPT (not per query) = $35/1000
    assert res.cost_micros_usd == 13 + 35_000
