"""Model retirement applies to deployed environment overrides and Agno seams."""

import pytest

from app.agno_models import build_agno_model
from app.providers import ProviderCredentials
from app.settings import Settings


@pytest.mark.parametrize("variable,attribute,old,replacement", [
    ("OPENAI_MODEL", "openai_model", "gpt-6-sol", "gpt-6.1-terra"),
    ("OPENAI_MODEL", "openai_model", "gpt-5.6-sol", "gpt-6.1-sol"),
    ("OPENAI_TEXT_MODEL", "openai_text_model", "gpt-5.6-luna", "gpt-6.1-terra"),
    ("OPENAI_TEXT_MODEL", "openai_text_model", "gpt-6-luna", "gpt-6.1-terra"),
    ("OPENAI_TEXT_MODEL", "openai_text_model", " GPT-5.6-Luna ", "gpt-6.1-terra"),
    ("ANTHROPIC_MODEL", "anthropic_model", "claude-opus-4-6", "claude-opus-5-5"),
    ("ANTHROPIC_MODEL", "anthropic_model", "claude-opus-5-5", "claude-opus-5-5"),
    ("ANTHROPIC_SEARCH_MODEL", "anthropic_search_model", "claude-opus-5-5", "claude-opus-5-5"),
    ("ANTHROPIC_MODEL", "anthropic_model", "claude-sonnet-4-6", "claude-sonnet-5-5"),
    ("ANTHROPIC_SEARCH_MODEL", "anthropic_search_model", "claude-sonnet-5", "claude-sonnet-5-5"),
    ("ANTHROPIC_TEXT_MODEL", "anthropic_text_model", "claude-haiku-4-5-20251001", "claude-haiku-5-5"),
    ("OPENAI_MODEL", "openai_model", "gpt-6.1-sol", "gpt-6.1-sol"),
    ("OPENAI_MODEL", "openai_model", "my-gateway-deployment", "my-gateway-deployment"),
])
def test_settings_upgrade_explicit_environment_models(
    monkeypatch, variable, attribute, old, replacement,
):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-only")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "1")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    monkeypatch.setenv(variable, old)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert getattr(settings, attribute) == replacement


@pytest.mark.parametrize("provider,old,replacement", [
    ("openai", "gpt-5.6-luna", "gpt-6.1-terra"),
    ("anthropic", "claude-sonnet-4-5-20250929", "claude-sonnet-5-5"),
])
def test_agno_construction_also_resolves_legacy_configuration(provider, old, replacement):
    credentials = ProviderCredentials(provider=provider, mode="api", api_key="test-only")
    assert build_agno_model(model_id=old, credentials=credentials).id == replacement
