from dataclasses import dataclass, field
from typing import Iterator, Optional

from app.llm import LLMResult


@dataclass
class FakeLLMClient:
    canned: Optional[LLMResult] = None
    canned_sequence: Optional[Iterator[LLMResult]] = None
    calls: list[bytes] = field(default_factory=list)
    raise_n_times: int = 0
    _raises: int = 0

    async def extract_items_from_image(
        self,
        image_bytes: bytes,
        *,
        image_media_type: str | None = None,
    ) -> LLMResult:
        self.calls.append(image_bytes)
        if self._raises < self.raise_n_times:
            self._raises += 1
            raise RuntimeError("simulated LLM failure")
        if self.canned_sequence is not None:
            return next(self.canned_sequence)
        assert self.canned is not None
        return self.canned
