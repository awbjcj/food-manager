from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from app.billing.payment import LedgerRow
from app.billing.plans import Sku
from app.cook.models import NutritionScores, RecipeCandidates, SelectedItems
from app.llm import CorrectionDiff, LLMClient, LLMResult, ProposedAddItem
from app.profile_service import FoodProfile
from app.refine_service import ShelfLifeSearchClient, ShelfLifeSearchResult


@dataclass
class FakeLLMClient(LLMClient):
    canned: LLMResult | None = None
    canned_sequence: Iterator[LLMResult] | None = None
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
    canned_correct: tuple[CorrectionDiff, int | None] | None = None
    canned_add: tuple[list[ProposedAddItem], int | None] | None = None
    canned_correct_sequence: list[tuple[CorrectionDiff, int | None]] | None = None
    canned_add_sequence: list[tuple[list[ProposedAddItem], int | None]] | None = None
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
    canned: tuple["FoodProfile", int | None] | None = None
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
    canned: tuple[SelectedItems, int | None] | None = None
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
    canned: tuple[RecipeCandidates, int | None] | None = None
    canned_sequence: list[tuple[RecipeCandidates, int | None]] | None = None
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
    canned: tuple[NutritionScores, int | None] | None = None
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
    default: ShelfLifeSearchResult | None = None
    calls: list[str] = field(default_factory=list)

    async def lookup_shelf_life(self, *, name, category):
        self.calls.append(name)
        if name in self.by_name:
            return self.by_name[name]
        assert self.default is not None, f"no canned result for {name!r}"
        return self.default


@dataclass
class FakeTranslationLLM:
    table: dict[str, str] = field(default_factory=dict)
    raise_n_times: int = 0
    _raises: int = 0
    calls: list[tuple[tuple[str, ...], str]] = field(default_factory=list)

    async def translate(self, *, texts: list[str], lang: str) -> tuple[list[str], int | None]:
        self.calls.append((tuple(texts), lang))
        if self._raises < self.raise_n_times:
            self._raises += 1
            raise RuntimeError("simulated translation failure")
        result: list[str] = [self.table.get(t, t) for t in texts]
        return result, 0


@dataclass
class FakePaymentProvider:
    refund_succeeds: bool = True
    transactions: list[tuple[str, int]] = field(default_factory=list)
    checkouts: list[tuple[str, int]] = field(default_factory=list)
    refunds: list[tuple[int, str]] = field(default_factory=list)
    cancellations: list[tuple[int, str]] = field(default_factory=list)

    async def create_checkout(self, *, sku: Sku, household_id: int) -> str:
        self.checkouts.append((sku.code, household_id))
        return f"https://t.me/invoice/{sku.code}/{household_id}"

    async def refund(self, *, user_id: int, charge_id: str) -> bool:
        self.refunds.append((user_id, charge_id))
        return self.refund_succeeds

    async def cancel_subscription(self, *, user_id: int, charge_id: str) -> bool:
        self.cancellations.append((user_id, charge_id))
        return True

    async def list_transactions(self, *, offset: int, limit: int) -> list[LedgerRow]:
        rows = [LedgerRow(charge_id, stars, None) for charge_id, stars in self.transactions]
        return rows[offset : offset + limit]
