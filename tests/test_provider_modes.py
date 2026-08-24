"""Credential modes: sub2api subscription routing vs each provider's own API.

Covers the four layers the feature spans: settings-level credential
resolution, the DB-backed override service, the ``bin.run`` client-construction
matrix (including the live rebuild-and-adopt path), and the operator commands.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import ProviderModeOverride
from app.operator import auth as operator_auth
from app.operator.bot import handle_provider, handle_providers
from app.provider_mode_service import (
    ProviderModeAdmin,
    ProviderModeError,
    clear_override,
    describe_modes,
    effective_modes,
    set_mode,
)
from app.providers import LLMProviderNotConfigured, ProviderSelector
from app.settings import Settings

GATEWAY = "https://gw.example"
NOW = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


def _settings(**overrides) -> Settings:
    base = {
        "TELEGRAM_BOT_TOKEN": "token",
        "ALLOWED_TELEGRAM_USER_ID": 1,
        "ANTHROPIC_API_KEY": None,
        "OPENAI_API_KEY": None,
        "GEMINI_API_KEY": None,
        "DEEPSEEK_API_KEY": None,
        "SUB2API_BASE_URL": None,
        "SUB2API_ANTHROPIC_TOKEN": None,
        "SUB2API_OPENAI_TOKEN": None,
        "SUB2API_GEMINI_TOKEN": None,
        "SUB2API_DEEPSEEK_TOKEN": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


# --------------------------------------------------------------------------- #
# settings: credential resolution and the config-derived default mode
# --------------------------------------------------------------------------- #
def test_subscription_credentials_carry_the_gateway():
    s = _settings(SUB2API_BASE_URL=GATEWAY, SUB2API_ANTHROPIC_TOKEN="sub-a")
    creds = s.credentials_for("anthropic", "subscription")
    assert creds is not None
    assert (creds.api_key, creds.base_url, creds.mode) == (
        "sub-a",
        GATEWAY,
        "subscription",
    )


def test_api_credentials_use_the_providers_own_endpoint():
    s = _settings(ANTHROPIC_API_KEY="a-key")
    creds = s.credentials_for("anthropic", "api")
    assert creds is not None
    # None means "SDK default" — anthropic.com, not the gateway.
    assert (creds.api_key, creds.base_url) == ("a-key", None)


def test_deepseek_api_mode_keeps_its_own_base_url():
    s = _settings(LLM_PROVIDER="deepseek", DEEPSEEK_API_KEY="d", GEMINI_API_KEY="g")
    creds = s.credentials_for("deepseek", "api")
    assert creds is not None
    assert creds.base_url == "https://api.deepseek.com"


def test_default_mode_prefers_subscription_only_where_a_token_exists():
    s = _settings(
        SUB2API_BASE_URL=GATEWAY,
        SUB2API_ANTHROPIC_TOKEN="sub-a",
        ANTHROPIC_API_KEY="a-key",
        GEMINI_API_KEY="g-key",
        DEEPSEEK_API_KEY="d-key",
    )
    # Subscribed -> gateway by default.
    assert s.default_credential_mode("anthropic") == "subscription"
    # No subscription for these; they stay on their own metered API.
    assert s.default_credential_mode("gemini") == "api"
    assert s.default_credential_mode("deepseek") == "api"


def test_subscription_token_alone_satisfies_provider_validation():
    # No ANTHROPIC_API_KEY at all: a subscription-only deploy must still boot.
    s = _settings(
        LLM_PROVIDER="anthropic",
        SUB2API_BASE_URL=GATEWAY,
        SUB2API_ANTHROPIC_TOKEN="sub-a",
    )
    assert s.has_credentials("anthropic")
    assert s.default_credential_mode("anthropic") == "subscription"


def test_token_without_a_gateway_url_is_rejected_at_boot():
    with pytest.raises(ValueError, match="SUB2API_BASE_URL is required"):
        _settings(ANTHROPIC_API_KEY="a", SUB2API_ANTHROPIC_TOKEN="sub-a")


@pytest.mark.parametrize("url", ["gw.example", "http://gw.example"])
def test_gateway_url_must_be_https(url):
    with pytest.raises(ValueError, match="must be an HTTPS"):
        _settings(ANTHROPIC_API_KEY="a", SUB2API_BASE_URL=url)


def test_settings_validation_errors_hide_secret_inputs():
    secrets = {
        "TELEGRAM_BOT_TOKEN": "telegram-canary-secret",
        "ANTHROPIC_API_KEY": "anthropic-canary-secret",
        "SUB2API_ANTHROPIC_TOKEN": "sub2api-canary-secret",
    }
    with pytest.raises(ValueError) as caught:
        _settings(SUB2API_BASE_URL="http://gw.example", **secrets)
    rendered = str(caught.value)
    assert all(secret not in rendered for secret in secrets.values())


def test_ingest_provider_resolves_from_subscription_only_credentials():
    s = _settings(
        LLM_PROVIDER="anthropic",
        SUB2API_BASE_URL=GATEWAY,
        SUB2API_GEMINI_TOKEN="sub-g",
        SUB2API_ANTHROPIC_TOKEN="sub-a",
    )
    # Gemini leads _INGEST_PREFERENCE and is credentialled via the gateway.
    assert s.ingest_provider == "gemini"


# --------------------------------------------------------------------------- #
# provider_mode_service: overrides layered onto config defaults
# --------------------------------------------------------------------------- #
def _both_modes_settings() -> Settings:
    return _settings(
        LLM_PROVIDER="anthropic",
        ANTHROPIC_API_KEY="a-key",
        SUB2API_BASE_URL=GATEWAY,
        SUB2API_ANTHROPIC_TOKEN="sub-a",
        GEMINI_API_KEY="g-key",
    )


def test_effective_modes_default_to_config(session):
    modes = effective_modes(session, _both_modes_settings())
    assert modes["anthropic"] == "subscription"
    assert modes["gemini"] == "api"


def test_override_wins_over_config(session):
    settings = _both_modes_settings()
    status = set_mode(
        session, settings, provider="anthropic", mode="api", actor=7, now=NOW
    )
    assert (status.mode, status.source, status.updated_by) == ("api", "override", 7)
    assert effective_modes(session, settings)["anthropic"] == "api"


def test_set_mode_is_idempotent_upsert(session):
    settings = _both_modes_settings()
    set_mode(session, settings, provider="anthropic", mode="api", actor=7, now=NOW)
    set_mode(
        session, settings, provider="anthropic", mode="subscription", actor=8, now=NOW
    )
    rows = session.exec(select(ProviderModeOverride)).all()
    assert len(rows) == 1 and rows[0].mode == "subscription"


def test_set_mode_rejects_a_mode_without_credentials(session):
    settings = _both_modes_settings()
    with pytest.raises(ProviderModeError, match="SUB2API_GEMINI_TOKEN"):
        set_mode(
            session, settings, provider="gemini", mode="subscription", actor=1, now=NOW
        )


def test_set_mode_rejects_unknown_provider_and_mode(session):
    settings = _both_modes_settings()
    with pytest.raises(ProviderModeError, match="unknown provider"):
        set_mode(session, settings, provider="mistral", mode="api", actor=1, now=NOW)
    with pytest.raises(ProviderModeError, match="unknown mode"):
        set_mode(session, settings, provider="anthropic", mode="free", actor=1, now=NOW)


def test_stale_override_falls_back_to_config(session):
    """An override outliving its credentials must not strand the provider."""
    settings = _both_modes_settings()
    set_mode(
        session, settings, provider="anthropic", mode="subscription", actor=1, now=NOW
    )
    # The token is later removed from the environment.
    without_token = _settings(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="a-key")
    status = next(
        s for s in describe_modes(session, without_token) if s.provider == "anthropic"
    )
    assert (status.mode, status.source) == ("api", "config")


def test_describe_reports_unusable_providers(session):
    settings = _both_modes_settings()
    openai_status = next(
        s for s in describe_modes(session, settings) if s.provider == "openai"
    )
    assert not openai_status.usable
    assert openai_status.available_modes == ()


def test_clear_override_returns_to_config(session):
    settings = _both_modes_settings()
    set_mode(session, settings, provider="anthropic", mode="api", actor=1, now=NOW)
    assert clear_override(session, provider="anthropic") is True
    assert effective_modes(session, settings)["anthropic"] == "subscription"
    assert clear_override(session, provider="anthropic") is False


# --------------------------------------------------------------------------- #
# providers.py: in-place adoption is what makes a flip take effect live
# --------------------------------------------------------------------------- #
def test_adopt_from_swaps_clients_behind_existing_references():
    old_client, new_client = object(), object()
    live = ProviderSelector({"anthropic": old_client}, "anthropic")
    holder = live  # stands in for PerUserClients holding a reference

    live.adopt_from(ProviderSelector({"anthropic": new_client}, "anthropic"))

    assert holder.for_provider("anthropic") is new_client


def test_adopt_rejects_a_default_missing_from_the_new_map():
    live = ProviderSelector({"anthropic": object()}, "anthropic")
    with pytest.raises(LLMProviderNotConfigured):
        live.adopt({"gemini": object()}, "anthropic")


# --------------------------------------------------------------------------- #
# bin/run.py: which endpoint each SDK actually gets pointed at
# --------------------------------------------------------------------------- #
def effective_modes_stub(settings) -> dict[str, str]:
    """Config-derived modes without touching a database."""
    from app.providers import ALL_PROVIDERS

    return {p: settings.default_credential_mode(p) for p in ALL_PROVIDERS}


def test_build_llm_clients_routes_subscription_providers_to_the_gateway():
    from bin.run import _build_llm_clients

    settings = _settings(
        LLM_PROVIDER="anthropic",
        ANTHROPIC_API_KEY="a-key",
        GEMINI_API_KEY="g-key",
        SUB2API_BASE_URL=GATEWAY,
        SUB2API_ANTHROPIC_TOKEN="sub-a",
    )
    bundle = _build_llm_clients(settings, effective_modes_stub(settings))

    anthropic_sdk = bundle.text.for_provider("anthropic")._sdk
    assert str(anthropic_sdk.base_url).startswith(GATEWAY)
    assert anthropic_sdk.api_key == "sub-a"
    # Gemini has no subscription token, so it stays on Google's own API.
    assert bundle.text.available_providers == ("anthropic", "gemini")


def test_build_llm_clients_honours_an_api_mode_override():
    from bin.run import _build_llm_clients

    settings = _settings(
        LLM_PROVIDER="anthropic",
        ANTHROPIC_API_KEY="a-key",
        SUB2API_BASE_URL=GATEWAY,
        SUB2API_ANTHROPIC_TOKEN="sub-a",
    )
    bundle = _build_llm_clients(settings, {"anthropic": "api"})
    sdk = bundle.text.for_provider("anthropic")._sdk
    assert not str(sdk.base_url).startswith(GATEWAY)
    assert sdk.api_key == "a-key"


def test_build_llm_clients_drops_a_provider_lacking_that_modes_credentials():
    from bin.run import _build_llm_clients

    settings = _settings(
        LLM_PROVIDER="gemini",
        GEMINI_API_KEY="g-key",
        SUB2API_BASE_URL=GATEWAY,
        SUB2API_ANTHROPIC_TOKEN="sub-a",
    )
    # Anthropic has a gateway token but no API key: forcing api mode removes it.
    bundle = _build_llm_clients(settings, {"anthropic": "api", "gemini": "api"})
    assert bundle.text.available_providers == ("gemini",)


def test_adopting_a_rebuilt_bundle_repoints_the_live_selectors():
    from bin.run import _adopt_bundle, _build_llm_clients

    settings = _settings(
        LLM_PROVIDER="anthropic",
        ANTHROPIC_API_KEY="a-key",
        SUB2API_BASE_URL=GATEWAY,
        SUB2API_ANTHROPIC_TOKEN="sub-a",
    )
    live = _build_llm_clients(settings, {"anthropic": "subscription"})
    live_text_selector = live.text  # the reference PerUserClients would hold
    assert str(live_text_selector.for_provider("anthropic")._sdk.base_url).startswith(
        GATEWAY
    )

    _adopt_bundle(live, _build_llm_clients(settings, {"anthropic": "api"}))

    sdk = live_text_selector.for_provider("anthropic")._sdk
    assert not str(sdk.base_url).startswith(GATEWAY)
    assert sdk.api_key == "a-key"


# --------------------------------------------------------------------------- #
# operator commands
# --------------------------------------------------------------------------- #
class _Msg:
    def __init__(self, text: str, sender: int = 99):
        self.text = text
        self.from_user = type("U", (), {"id": sender})()
        self.replies: list[str] = []

    async def answer(self, text: str) -> None:
        self.replies.append(text)


@pytest.fixture
def operator(monkeypatch):
    monkeypatch.setattr(operator_auth, "OPERATOR_IDS", frozenset({99}))


def _admin(session, applied: list) -> ProviderModeAdmin:
    class _NonClosing:
        """Hand the same in-memory session to every call without closing it."""

        def __enter__(self):
            return session

        def __exit__(self, *exc):
            return False

    return ProviderModeAdmin(
        settings=_both_modes_settings(),
        session_factory=lambda: _NonClosing(),
        apply=applied.append,
    )


@pytest.mark.asyncio
async def test_providers_command_lists_modes_and_alternatives(session, operator):
    msg = _Msg("/providers")
    await handle_providers(msg, provider_modes=_admin(session, []))
    reply = msg.replies[0]
    assert "anthropic: subscription (via config)" in reply
    assert "can switch to: api" in reply
    assert "openai: (no credentials configured)" in reply


@pytest.mark.asyncio
async def test_provider_command_switches_and_pushes_live(session, operator):
    applied: list = []
    msg = _Msg("/provider anthropic api")
    await handle_provider(
        msg, provider_modes=_admin(session, applied), now_provider=lambda tz: NOW
    )
    assert "anthropic now uses api credentials" in msg.replies[0]
    # The rebuild hook fired with the newly effective mode map.
    assert applied and applied[0]["anthropic"] == "api"


@pytest.mark.asyncio
async def test_provider_command_reports_missing_credentials(session, operator):
    applied: list = []
    msg = _Msg("/provider gemini subscription")
    await handle_provider(
        msg, provider_modes=_admin(session, applied), now_provider=lambda tz: NOW
    )
    assert "cannot switch" in msg.replies[0]
    assert "SUB2API_GEMINI_TOKEN" in msg.replies[0]
    assert applied == []


@pytest.mark.asyncio
async def test_provider_command_rolls_back_when_live_rebuild_fails(session, operator):
    calls = 0

    def fail_new_rebuild(_modes):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("bad endpoint containing secret-token")

    admin = _admin(session, [])
    admin.apply = fail_new_rebuild
    msg = _Msg("/provider anthropic api")

    await handle_provider(msg, provider_modes=admin, now_provider=lambda tz: NOW)

    assert "previous mode remains active" in msg.replies[0]
    assert "secret-token" not in msg.replies[0]
    assert session.get(ProviderModeOverride, "anthropic") is None


@pytest.mark.asyncio
async def test_provider_command_requires_restart_when_runtime_rollback_fails(
    session, operator
):
    admin = _admin(session, [])
    admin.apply = lambda _modes: (_ for _ in ()).throw(RuntimeError("secret-token"))
    msg = _Msg("/provider anthropic api")

    await handle_provider(msg, provider_modes=admin, now_provider=lambda tz: NOW)

    assert "restart the service" in msg.replies[0]
    assert "secret-token" not in msg.replies[0]
    assert session.get(ProviderModeOverride, "anthropic") is None


@pytest.mark.asyncio
async def test_provider_command_usage_on_bad_arity(session, operator):
    msg = _Msg("/provider anthropic")
    await handle_provider(
        msg, provider_modes=_admin(session, []), now_provider=lambda tz: NOW
    )
    assert msg.replies[0].startswith("usage:")


@pytest.mark.asyncio
async def test_provider_commands_are_operator_gated(session, monkeypatch):
    monkeypatch.setattr(operator_auth, "OPERATOR_IDS", frozenset({1}))
    msg = _Msg("/providers", sender=99)
    await handle_providers(msg, provider_modes=_admin(session, []))
    assert msg.replies == []
