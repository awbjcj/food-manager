from __future__ import annotations

import json
import logging
from typing import Optional, Protocol

from pydantic import BaseModel

from app.llm import (
    LLMProviderName,
    LLMProviderNotConfigured,
    _cost_micros,
    _extract_json_text,
    _extract_openai_parsed,
)

log = logging.getLogger(__name__)


class TranslationLLMClient(Protocol):
    async def translate(
        self, *, texts: list[str], lang: str
    ) -> tuple[list[str], Optional[int]]:
        """Return translations in the same order as `texts`, plus cost micros."""
        ...


_LANG_NAMES = {"zh": "Chinese", "fr": "French", "es": "Spanish", "en": "English"}

_TRANSLATE_SYSTEM_PROMPT = """You translate short grocery and recipe strings into a target language.
You receive a JSON object {"target_language": <name>, "strings": [<english strings>]}.
Translate each string to the target language, keeping brand names, quantities, and units
natural. Return the translations in the SAME order, one per input string."""

_ANTHROPIC_TRANSLATE_SUFFIX = (
    "\nReturn ONLY a JSON array of the translated strings "
    "(same length and order as the input). No prose."
)


class TranslationList(BaseModel):
    items: list[str]


def _user_msg(texts: list[str], lang: str) -> str:
    return json.dumps({"target_language": _LANG_NAMES.get(lang, lang), "strings": texts})


class AnthropicTranslationLLMClient:
    def __init__(self, sdk, model: str):
        self._sdk = sdk
        self._model = model

    async def translate(self, *, texts: list[str], lang: str) -> tuple[list[str], Optional[int]]:
        message = await self._sdk.messages.create(
            model=self._model,
            max_tokens=1024,
            system=_TRANSLATE_SYSTEM_PROMPT + _ANTHROPIC_TRANSLATE_SUFFIX,
            messages=[{"role": "user", "content": _user_msg(texts, lang)}],
        )
        data = json.loads(_extract_json_text(message))
        if not isinstance(data, list):
            raise ValueError("translation response was not a JSON array")
        return [str(x) for x in data], _cost_micros(message, self._model)


class OpenAITranslationLLMClient:
    def __init__(self, sdk, model: str):
        self._sdk = sdk
        self._model = model

    async def translate(self, *, texts: list[str], lang: str) -> tuple[list[str], Optional[int]]:
        response = await self._sdk.responses.parse(
            model=self._model,
            input=[
                {"role": "system", "content": _TRANSLATE_SYSTEM_PROMPT},
                {"role": "user", "content": [{"type": "input_text", "text": _user_msg(texts, lang)}]},
            ],
            text_format=TranslationList,
            max_output_tokens=1024,
        )
        parsed = TranslationList.model_validate(_extract_openai_parsed(response))
        return [str(x) for x in parsed.items], _cost_micros(response, self._model)


class TranslationLLMProviderSelector:
    def __init__(self, clients: dict, default_provider: LLMProviderName):
        if default_provider not in clients:
            raise LLMProviderNotConfigured(default_provider)
        self._clients = clients
        self._default_provider: LLMProviderName = default_provider

    @property
    def available_providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._clients))

    @property
    def default_provider(self) -> LLMProviderName:
        return self._default_provider

    def for_provider(self, provider: str):
        try:
            return self._clients[provider]
        except KeyError as exc:
            raise LLMProviderNotConfigured(provider) from exc

    async def translate(self, *, texts: list[str], lang: str):
        return await self._clients[self._default_provider].translate(texts=texts, lang=lang)
