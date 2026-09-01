"""The Agno seams' contract with the vendor SDKs they actually call.

`tests/fakes.py` stands in for the Agno agents everywhere else, which is why
agno 2.9.0 kept emitting Anthropic's removed `output_format` parameter against
anthropic 1.0.0 for a week without a single red test: the fakes verify our
contract with the seam, never the seam's contract with the SDK underneath it.

These tests close that gap by driving the real adapter.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import BaseModel

from app.agno_models import build_agno_model
from app.providers import ProviderCredentials


class _Schema(BaseModel):
    """Stands in for NLIntent / WeekPlanSpec: any structured-output request."""

    verdict: str


def _credentials(provider: str, base_url: str | None = None) -> ProviderCredentials:
    return ProviderCredentials(
        provider=provider,  # type: ignore[arg-type]
        mode="api",
        api_key="test-key-not-used-no-request-is-sent",
        base_url=base_url,
    )


def test_claude_structured_output_kwargs_are_accepted_by_installed_anthropic_sdk():
    """Every kwarg Agno emits for an `output_schema` run must exist on create().

    Both Agno seams set `output_schema`, so this is the exact request shape
    `/plan` and every natural-language message build. The Anthropic SDK's
    `create()` takes no `**kwargs`, so an argument it does not declare is a
    TypeError raised before the request is ever sent -- which Agent.arun then
    swallows, degrading the NL seam to its "I didn't understand" hint while
    still charging the user quota.

    Structured output makes Agno add a beta header and dispatch to the beta
    endpoint, so the kwargs are checked against whichever endpoint Agno would
    actually have called rather than always against the stable one.
    """
    from anthropic.resources.beta.messages.messages import Messages as BetaMessages
    from anthropic.resources.messages.messages import Messages as StableMessages

    model = build_agno_model(
        model_id="claude-sonnet-4-5-20250929",
        credentials=_credentials("anthropic"),
    )

    build_kwargs = getattr(model, "_prepare_request_kwargs", None)
    assert build_kwargs is not None, (
        "Agno's Claude adapter no longer exposes _prepare_request_kwargs; "
        "re-point this test at whatever now builds the create() kwargs."
    )

    emitted = build_kwargs(
        system_message="You classify messages.", response_format=_Schema
    )

    uses_beta = model._has_beta_features(response_format=_Schema, tools=None)
    endpoint = BetaMessages if uses_beta else StableMessages

    accepted = set(inspect.signature(endpoint.create).parameters)
    unexpected = set(emitted) - accepted
    assert not unexpected, (
        f"Agno sends {sorted(unexpected)} to anthropic's "
        f"{'beta' if uses_beta else 'stable'} create(), which does not accept it. "
        f"This raises TypeError before any request is sent."
    )


@pytest.mark.parametrize(
    ("provider", "model_id"),
    [
        ("anthropic", "claude-sonnet-4-5-20250929"),
        ("openai", "gpt-5.2"),
        ("gemini", "gemini-3-flash-preview"),
        ("deepseek", "deepseek-chat"),
    ],
)
@pytest.mark.parametrize("base_url", [None, "https://gateway.example/v1"])
def test_every_provider_builds_in_both_credential_modes(provider, model_id, base_url):
    """`api` and `sub2api` are spelled differently per provider (see agno_models).

    A model class that renames `base_url` or `client_params` breaks only the
    gateway path, which no other test covers.
    """
    model = build_agno_model(
        model_id=model_id, credentials=_credentials(provider, base_url=base_url)
    )
    assert model.id == model_id
