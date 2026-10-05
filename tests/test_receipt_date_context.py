from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.gemini_llm import GeminiLLMClient
from app.ingest_service import ingest_photo
from app.llm import (
    AnthropicLLMClient,
    LLMProviderSelector,
    LLMResult,
    OpenAILLMClient,
    ParsedItem,
    ParseResult,
)
from app.models import Household, PantryItem, Receipt
from tests.fakes import FakeLLMClient


@pytest.mark.parametrize("provider", ["anthropic", "openai", "gemini"])
async def test_receipt_parsers_receive_local_scan_date_through_selector(provider):
    parsed = ParseResult(store_name="Costco", items=[])
    sdk = MagicMock()
    if provider == "anthropic":
        sdk.messages.create = AsyncMock(
            return_value=SimpleNamespace(
                content=[SimpleNamespace(type="tool_use", input=parsed.model_dump())],
                usage=SimpleNamespace(input_tokens=0, output_tokens=0),
            )
        )
        client = AnthropicLLMClient(sdk, "claude-sonnet-4-6")
        call = sdk.messages.create
    elif provider == "openai":
        sdk.responses.parse = AsyncMock(
            return_value=SimpleNamespace(
                output_parsed=parsed,
                output=[],
                usage=None,
            )
        )
        client = OpenAILLMClient(sdk, "gpt-6-sol")
        call = sdk.responses.parse
    else:
        sdk.aio.models.generate_content = AsyncMock(
            return_value=SimpleNamespace(
                parsed=parsed,
                usage_metadata=None,
            )
        )
        client = GeminiLLMClient(sdk, "gemini-3.8-flash")
        call = sdk.aio.models.generate_content

    selector = LLMProviderSelector({provider: client}, provider)
    result = await selector.extract_items_from_image(
        b"\xff\xd8\xffimage", today=date(2026, 9, 23)
    )

    kwargs = call.call_args.kwargs
    if provider == "anthropic":
        text = kwargs["messages"][0]["content"][1]["text"]
    elif provider == "openai":
        text = kwargs["input"][1]["content"][0]["text"]
    else:
        text = kwargs["contents"][1]
    assert "2026-09-23" in text
    assert result.parse.store_name == "Costco"
    # Every provider receives the shared retailer extraction instructions.
    if provider == "anthropic":
        system = kwargs["system"]
    elif provider == "openai":
        system = kwargs["input"][0]["content"]
    else:
        system = kwargs["config"].system_instruction
    assert "store_name" in str(system)


@pytest.mark.parametrize("purchase_date", [date(2026, 9, 22), date(2024, 9, 22)])
async def test_ingest_passes_scan_context_and_preserves_receipt_date(
    tmp_path, purchase_date
):
    reference_dates = []

    class RecordingLLM(FakeLLMClient):
        async def extract_items_from_image(
            self, image_bytes, *, image_media_type=None, today=None
        ):
            reference_dates.append(today)
            return await super().extract_items_from_image(image_bytes)

    parsed = ParseResult(
        purchase_date=purchase_date,
        purchase_date_confidence=0.95,
        items=[
            ParsedItem(
                is_food=True,
                name="Brussels Sprouts",
                category="produce",
                est_shelf_life_days=7,
                confidence=0.95,
            )
        ],
    )
    engine = create_engine(f"sqlite:///{(tmp_path / 'food.db').as_posix()}")
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            session.add(Household(created_at=datetime.now(UTC)))
            session.commit()
            summary = await ingest_photo(
                session,
                RecordingLLM(canned=LLMResult(parse=parsed)),
                household_id=1,
                photo_file_id="receipt-40",
                image_bytes=b"receipt",
                today=date(2026, 9, 23),
                scanned_at=datetime(2026, 9, 23, 13, 30, tzinfo=UTC),
            )

        with Session(engine) as session:
            item = session.get(PantryItem, summary.inserted_item_ids[0])
            receipt = session.get(Receipt, summary.receipt_id)
            assert item is not None and receipt is not None
            assert receipt.purchase_date == item.purchased_on == purchase_date
            assert item.expires_on == date(purchase_date.year, 9, 29)
        assert reference_dates == [date(2026, 9, 23)]
        assert summary.purchase_date_assumed is False
    finally:
        engine.dispose()
