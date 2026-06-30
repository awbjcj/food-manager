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
    assert not supports("deepseek", "image")
    assert not supports("deepseek", "search")
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
    base = dict(
        TELEGRAM_BOT_TOKEN="token",
        ALLOWED_TELEGRAM_USER_ID=1,
        ANTHROPIC_API_KEY=None,
        OPENAI_API_KEY=None,
        GEMINI_API_KEY=None,
        DEEPSEEK_API_KEY=None,
    )
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
    # Image excludes deepseek (text-only).
    assert bundle.image.available_providers == ("anthropic", "gemini", "openai")
    # Search wired for anthropic + gemini only.
    assert bundle.search is not None
    assert bundle.search.available_providers == ("anthropic", "gemini")
    # Recipe (search-backed) excludes deepseek.
    assert "deepseek" not in bundle.recipe.available_providers

    # A deepseek user's text task uses deepseek, but a photo falls back.
    assert type(bundle.text.for_provider("deepseek")).__name__ == "DeepSeekTextLLMClient"
    assert bundle.image.for_provider("deepseek") is bundle.image.for_provider("anthropic")
    assert bundle.search.for_provider("deepseek") is bundle.search.for_provider("anthropic")


def test_build_llm_clients_deepseek_default_seeds_capable_image():
    from bin.run import _build_llm_clients

    settings = _settings(
        LLM_PROVIDER="deepseek",
        DEEPSEEK_API_KEY="d",
        GEMINI_API_KEY="g",
    )
    bundle = _build_llm_clients(settings)
    # Image/search seed defaults must be capable even though the global default
    # (deepseek) is not — they fall back to gemini.
    assert bundle.search is not None
    assert bundle.image.for_provider("deepseek") is bundle.image.for_provider("gemini")
    assert bundle.search.for_provider("deepseek") is bundle.search.for_provider("gemini")


# --------------------------------------------------------------------------- #
# DeepSeek client: chat.completions JSON + schema repair (faked SDK)
# --------------------------------------------------------------------------- #
class _FakeChatCompletions:
    def __init__(self, contents):
        self._contents = list(contents)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self._contents.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )


class FakeOpenAISDK:
    def __init__(self, contents):
        self.chat = SimpleNamespace(completions=_FakeChatCompletions(contents))


async def test_deepseek_parse_add_happy_path():
    sdk = FakeOpenAISDK(
        ['{"items":[{"name":"Oat Milk","explicit_user_expiry":false,"confidence":0.9}]}']
    )
    client = DeepSeekTextLLMClient(sdk, "deepseek-chat")
    from datetime import date

    items, cost = await client.parse_add(
        user_text="oat milk", today=date(2026, 6, 29), tz="UTC"
    )
    assert [i.name for i in items] == ["Oat Milk"]
    assert cost == round(10 * 0.27 + 5 * 1.1)  # 8
    # response_format pins JSON object mode
    assert sdk.chat.completions.calls[0]["response_format"] == {"type": "json_object"}


async def test_deepseek_schema_repair_retries_once():
    sdk = FakeOpenAISDK(['not json', '{"item_ids":[1,2],"rationale":"x"}'])
    client = DeepSeekSelectionLLM(sdk, "deepseek-chat")
    selected, cost = await client.select_items(prompt="pick stuff")
    assert selected.item_ids == [1, 2]
    assert len(sdk.chat.completions.calls) == 2  # one repair round-trip


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
