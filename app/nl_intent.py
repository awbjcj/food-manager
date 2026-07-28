"""Natural-language intent seam (v5.1): the bot's one Agno surface.

A plain-text message is classified into a typed ``NLIntent`` by an Agno agent
with a structured output schema. The agent classifies only — it never touches
the database or Telegram; the bot handler dispatches each intent to the
existing service functions, so the NL path inherits the same tests, auth, and
session/today conventions as every command.

One agent per configured provider is built at bootstrap (never per message)
and selected per user by ``IntentAgentSelector`` with no fallback, matching
the v4.7 text-seam rule: the user's provider choice is always honoured.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from typing import Literal, Protocol

from pydantic import BaseModel

from app.normalization import normalize
from app.providers import ProviderSelector

log = logging.getLogger(__name__)

#: Cap on pantry names injected into the prompt.
MAX_CONTEXT_NAMES = 100


class NLIntent(BaseModel):
    kind: Literal["add", "mark", "shelf_life_question", "pantry_query", "unknown"]
    mark_action: Literal["ate", "tossed", "snooze", "freeze"] | None = None
    item_name: str | None = None
    food: str | None = None


class IntentAgent(Protocol):
    async def parse(
        self, text: str, *, today: date, pantry_names: Sequence[str]
    ) -> NLIntent: ...


_INSTRUCTIONS = """\
You classify one Telegram message from a household pantry-tracker user into an intent.

Kinds:
- "add": the user bought/acquired food to track ("bought milk and two avocados").
- "mark": the user finished, discarded, postponed, or froze ONE tracked item
  ("ate the yogurt", "threw out the spinach", "freeze the chicken",
  "remind me about the ham later"). Set mark_action to one of
  ate|tossed|snooze|freeze and item_name to the item.
- "shelf_life_question": a question about how long a food keeps
  ("how long does salmon keep?"). Set food.
- "pantry_query": asking what's in the pantry or what's expiring.
- "unknown": anything else, or when you are unsure.

Rules:
- The message may be in English, Chinese, French, or Spanish. Always return
  item_name and food as lowercase canonical ENGLISH food names.
- A list of the user's current pantry item names is provided; when the message
  refers to one of them, return that exact name as item_name.
- Never invent an intent; prefer "unknown" over guessing.
- Negated, future/hypothetical, questions about marking, or messages naming
  multiple different mark actions must classify as "unknown" — never mutate on
  an uncertain read.
"""


class AgnoIntentAgent:
    """Wraps one Agno ``Agent`` (built once at bootstrap) behind ``IntentAgent``."""

    def __init__(self, agent) -> None:
        self._agent = agent

    async def parse(
        self, text: str, *, today: date, pantry_names: Sequence[str]
    ) -> NLIntent:
        prompt = (
            f"today: {today.isoformat()}\n"
            f"pantry items: {', '.join(pantry_names) if pantry_names else '(empty)'}\n"
            f"message: {text}"
        )
        response = await self._agent.arun(prompt)
        intent = getattr(response, "content", None)
        if not isinstance(intent, NLIntent):
            raise ValueError("intent agent returned no structured NLIntent")  # noqa: TRY004 - JSON-shape contract, not a type check
        return intent


def build_intent_agent(
    provider: str, *, model_id: str, api_key: str, base_url: str | None = None
) -> AgnoIntentAgent:
    from agno.agent import Agent

    if provider == "anthropic":
        from agno.models.anthropic import Claude

        model = Claude(id=model_id, api_key=api_key)
    elif provider == "openai":
        from agno.models.openai import OpenAIChat

        model = OpenAIChat(id=model_id, api_key=api_key)
    elif provider == "gemini":
        from agno.models.google import Gemini

        model = Gemini(id=model_id, api_key=api_key)
    elif provider == "deepseek":
        from agno.models.deepseek import DeepSeek

        if base_url:
            model = DeepSeek(id=model_id, api_key=api_key, base_url=base_url)
        else:
            model = DeepSeek(id=model_id, api_key=api_key)
    else:
        raise ValueError(f"unknown intent provider {provider!r}")
    return AgnoIntentAgent(
        Agent(model=model, description=_INSTRUCTIONS, output_schema=NLIntent)
    )


class IntentAgentSelector(ProviderSelector[AgnoIntentAgent]):
    """Per-user agent routing; no fallback (text-seam convention)."""


def match_items(name: str, items: Sequence) -> list:
    """Active pantry items matching an English name: exact first, else substring."""
    needle = normalize(name)
    if not needle:
        return []
    exact = [i for i in items if i.normalized_name == needle]
    if exact:
        return exact
    return [
        i
        for i in items
        if needle in i.normalized_name or i.normalized_name in needle
    ]
