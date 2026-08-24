"""One place to turn provider credentials into an Agno model.

Both Agno seams — the NL intent agent (v5.1) and the week composer (v5.2) —
need the same thing: a provider, a model id, and credentials. They carried
byte-identical construction blocks, and the sub2api credential mode made that
duplication expensive, because pointing a provider at a gateway is spelled
differently in every Agno model class:

* ``OpenAIChat`` and ``DeepSeek`` take ``base_url`` directly;
* ``Claude`` merges ``client_params`` into the Anthropic SDK constructor;
* ``Gemini`` merges ``client_params`` into ``genai.Client``, which nests its
  base URL under ``http_options``.

Imports stay lazy and inside each branch, matching the seams' existing cost
model: only the configured provider's Agno model class is ever imported.
"""

from __future__ import annotations

from typing import Any

from app.providers import ProviderCredentials


def build_agno_model(
    provider: str, *, model_id: str, credentials: ProviderCredentials
) -> Any:
    """Construct the Agno model for ``provider`` honouring its credential mode."""
    api_key = credentials.api_key
    base_url = credentials.base_url

    if provider == "anthropic":
        from agno.models.anthropic import Claude

        return Claude(
            id=model_id,
            api_key=api_key,
            client_params={"base_url": base_url} if base_url else None,
        )
    if provider == "openai":
        from agno.models.openai import OpenAIChat

        return OpenAIChat(id=model_id, api_key=api_key, base_url=base_url)
    if provider == "gemini":
        from agno.models.google import Gemini

        return Gemini(
            id=model_id,
            api_key=api_key,
            client_params={"http_options": {"base_url": base_url}}
            if base_url
            else None,
        )
    if provider == "deepseek":
        from agno.models.deepseek import DeepSeek

        return DeepSeek(id=model_id, api_key=api_key, base_url=base_url)
    raise ValueError(f"unknown agno provider {provider!r}")
