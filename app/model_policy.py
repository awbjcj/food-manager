"""Application model retirement policy, checked against provider docs 2026-10-28.

This retires older IDs in this application; it does not assert vendor shutdown.
Unknown IDs stay available for explicit custom/gateway configurations.
"""

CURRENT_OPENAI_MODELS = frozenset({"gpt-6.1-terra", "gpt-6.1-sol"})
CURRENT_CLAUDE_MODELS = frozenset({
    "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5",
})

RETIRED_OPENAI_MODELS = {
    "gpt-5": "gpt-6.1-terra",
    "gpt-5.1": "gpt-6.1-terra",
    "gpt-5.2": "gpt-6.1-terra",
    "gpt-5.4": "gpt-6.1-terra",
    "gpt-5.4-mini": "gpt-6.1-terra",
    "gpt-5.4-nano": "gpt-6.1-terra",
    "gpt-5.5": "gpt-6.1-terra",
    "gpt-5.5-pro": "gpt-6.1-sol",
    "gpt-5.6-sol": "gpt-6.1-sol",
    "gpt-5.6-terra": "gpt-6.1-terra",
    "gpt-5.6-luna": "gpt-6.1-terra",
    "gpt-6-sol": "gpt-6.1-terra",
    "gpt-6-luna": "gpt-6.1-terra",
}
RETIRED_CLAUDE_MODELS = {
    "claude-opus-4-5-20251101": "claude-opus-5-5",
    "claude-opus-4-6": "claude-opus-5-5",
    "claude-opus-4-7": "claude-opus-5-5",
    "claude-opus-4-8": "claude-opus-5-5",
    "claude-opus-5": "claude-opus-5-5",
    "claude-sonnet-4-5-20250929": "claude-sonnet-5-5",
    "claude-sonnet-4-6": "claude-sonnet-5-5",
    "claude-sonnet-5": "claude-sonnet-5-5",
    "claude-haiku-4-5-20251001": "claude-haiku-5-5",
}


def resolve_model_id(provider: str, model_id: str) -> str:
    replacements = {
        "openai": RETIRED_OPENAI_MODELS,
        "anthropic": RETIRED_CLAUDE_MODELS,
    }.get(provider, {})
    return replacements.get(model_id.strip().lower(), model_id)
