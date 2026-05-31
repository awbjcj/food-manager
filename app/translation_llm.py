from __future__ import annotations

from typing import Optional, Protocol


class TranslationLLMClient(Protocol):
    async def translate(
        self, *, texts: list[str], lang: str
    ) -> tuple[list[str], Optional[int]]:
        """Return translations in the same order as `texts`, plus cost micros."""
        ...
