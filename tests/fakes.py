from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from app.cook_models import NutritionScores, RecipeCandidates, SelectedItems
from app.llm import CorrectionDiff, LLMClient, LLMResult, ProposedAddItem
from app.profile_service import FoodProfile
from app.refine_service import ShelfLifeSearchClient, ShelfLifeSearchResult


@dataclass
class FakeLLMClient(LLMClient):
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


@dataclass
class FakeTextLLMClient:
    canned_correct: Optional[tuple[CorrectionDiff, Optional[int]]] = None
    canned_add: Optional[tuple[list[ProposedAddItem], Optional[int]]] = None
    canned_correct_sequence: Optional[list[tuple[CorrectionDiff, Optional[int]]]] = None
    canned_add_sequence: Optional[list[tuple[list[ProposedAddItem], Optional[int]]]] = None
    raise_n_times: int = 0
    _raises: int = 0
    correct_calls: list[dict[str, Any]] = field(default_factory=list)
    add_calls: list[dict[str, Any]] = field(default_factory=list)

    async def parse_correct(self, *, item_snapshot, cache_snapshot, user_text, today):
        self.correct_calls.append({
            "item_snapshot": item_snapshot,
            "cache_snapshot": cache_snapshot,
            "user_text": user_text,
            "today": today,
        })
        if self._raises < self.raise_n_times:
            self._raises += 1
            raise RuntimeError("simulated text-llm failure")
        if self.canned_correct_sequence:
            return self.canned_correct_sequence.pop(0)
        assert self.canned_correct is not None
        return self.canned_correct

    async def parse_add(self, *, user_text, today, tz):
        self.add_calls.append({"user_text": user_text, "today": today, "tz": tz})
        if self._raises < self.raise_n_times:
            self._raises += 1
            raise RuntimeError("simulated text-llm failure")
        if self.canned_add_sequence:
            return self.canned_add_sequence.pop(0)
        assert self.canned_add is not None
        return self.canned_add


@dataclass
class FakeProfileLLMClient:
    canned: Optional[tuple["FoodProfile", Optional[int]]] = None
    raise_n_times: int = 0
    _raises: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def parse_profile_update(self, *, current, sentence):
        self.calls.append({"current": current, "sentence": sentence})
        if self._raises < self.raise_n_times:
            self._raises += 1
            raise RuntimeError("simulated profile-llm failure")
        assert self.canned is not None
        return self.canned


@dataclass
class FakeSelectionLLM:
    canned: Optional[tuple[SelectedItems, Optional[int]]] = None
    raise_n_times: int = 0
    _raises: int = 0
    calls: list[str] = field(default_factory=list)

    async def select_items(self, *, prompt):
        self.calls.append(prompt)
        if self._raises < self.raise_n_times:
            self._raises += 1
            raise RuntimeError("simulated selection failure")
        assert self.canned is not None
        return self.canned


@dataclass
class FakeRecipeLLM:
    canned: Optional[tuple[RecipeCandidates, Optional[int]]] = None
    canned_sequence: Optional[list[tuple[RecipeCandidates, Optional[int]]]] = None
    raise_n_times: int = 0
    _raises: int = 0
    calls: list[str] = field(default_factory=list)

    async def fetch_recipes(self, *, prompt):
        self.calls.append(prompt)
        if self._raises < self.raise_n_times:
            self._raises += 1
            raise RuntimeError("simulated recipe failure")
        if self.canned_sequence:
            return self.canned_sequence.pop(0)
        assert self.canned is not None
        return self.canned


@dataclass
class FakeNutritionLLM:
    canned: Optional[tuple[NutritionScores, Optional[int]]] = None
    raise_n_times: int = 0
    _raises: int = 0
    calls: list[str] = field(default_factory=list)

    async def score(self, *, prompt):
        self.calls.append(prompt)
        if self._raises < self.raise_n_times:
            self._raises += 1
            raise RuntimeError("simulated nutrition failure")
        assert self.canned is not None
        return self.canned


@dataclass
class FakeSearchClient(ShelfLifeSearchClient):
    by_name: dict[str, ShelfLifeSearchResult] = field(default_factory=dict)
    default: Optional[ShelfLifeSearchResult] = None
    calls: list[str] = field(default_factory=list)

    async def lookup_shelf_life(self, *, name, category):
        self.calls.append(name)
        if name in self.by_name:
            return self.by_name[name]
        assert self.default is not None, f"no canned result for {name!r}"
        return self.default
