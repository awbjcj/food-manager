"""Provider identity and capability plumbing, shared by every LLM seam.

This is the single source of truth for *which* model providers exist and *what*
each one can do. The codebase historically hardwired ``Literal["anthropic",
"openai"]`` in three places (settings, commands, llm); they now all import
``Provider`` from here so adding a provider is a one-line change.

It also hosts the generic :class:`ProviderSelector`. Every capability seam
(image, text, profile, cook, translation, search) maps a provider name to a
concrete client; this base captures that mapping plus the *fallback* policy used
when a user's chosen provider cannot serve a capability (e.g. a text-only
provider asked to read a photo). Keeping the base here — a leaf module with no
domain imports — lets both ``app.llm`` and ``app.shelf_life_search`` reuse it
without an import cycle.
"""

from __future__ import annotations

import logging
from typing import Literal, TypeVar

log = logging.getLogger(__name__)

Provider = Literal["anthropic", "openai", "gemini", "deepseek"]
ALL_PROVIDERS: tuple[Provider, ...] = ("anthropic", "openai", "gemini", "deepseek")

# A coarse description of what each provider's API can serve. "text" covers the
# JSON tasks every provider can do (corrections, /add, profile, cook selection &
# nutrition, translation). "image" is receipt photo understanding; "search" is a
# built-in web-search tool. DeepSeek's public API has no image input, so image
# capability still falls back to another provider; it does have a native
# web_search tool on its Responses API (app.deepseek_llm), so "search" is no
# longer fallback-only for it.
Capability = Literal["image", "text", "search"]

PROVIDER_CAPABILITIES: dict[Provider, frozenset[Capability]] = {
    "anthropic": frozenset({"image", "text", "search"}),
    "openai": frozenset({"image", "text", "search"}),
    "gemini": frozenset({"image", "text", "search"}),
    "deepseek": frozenset({"text", "search"}),
}


def supports(provider: str, capability: Capability) -> bool:
    """True if ``provider``'s API can serve ``capability``."""
    return capability in PROVIDER_CAPABILITIES.get(provider, frozenset())  # type: ignore[arg-type]


class LLMProviderNotConfigured(ValueError):
    """Raised when a requested provider has no client for a capability.

    Defined here (rather than in ``app.llm``) so the search seam can raise it
    too; ``app.llm`` re-exports it for backwards compatibility.
    """


T = TypeVar("T")


class ProviderSelector[T]:
    """Maps a provider name to a capability client, with optional fallback.

    ``clients`` is ``{provider_name: client}`` for the providers that can serve
    this capability and are configured. ``default_provider`` is the system
    default and must be present in ``clients``.

    ``for_provider(name)`` returns the matching client. When ``name`` is absent:
    if ``fallback`` is True the call is routed to a capable provider (the default
    if it serves this capability, else the first available in deterministic
    order) and the substitution is logged; if ``fallback`` is False a
    :class:`LLMProviderNotConfigured` is raised.

    Fallback is enabled for capabilities a provider may legitimately lack
    (image, search) and disabled for text capabilities, where the user's choice
    is always honoured.
    """

    def __init__(
        self,
        clients: dict[str, T],
        default_provider: str,
        *,
        fallback: bool = False,
    ):
        if default_provider not in clients:
            raise LLMProviderNotConfigured(default_provider)
        self._clients = clients
        self._default_provider = default_provider
        self._fallback = fallback

    @property
    def available_providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._clients))

    @property
    def default_provider(self) -> str:
        return self._default_provider

    def for_provider(self, provider: str) -> T:
        client = self._clients.get(provider)
        if client is not None:
            return client
        if self._fallback:
            return self._fallback_client(provider)
        raise LLMProviderNotConfigured(provider)

    def _fallback_client(self, provider: str) -> T:
        # The constructor guarantees default_provider is always present in the
        # client map, so it is the natural (and only) fallback target.
        target = self._default_provider
        log.info(
            "llm_provider_fallback",
            extra={"requested": provider, "using": target},
        )
        return self._clients[target]
