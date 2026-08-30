from datetime import UTC, date, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.ingest_service import IngestSummary, ingest_photo
from app.llm import LLMResult, ParsedItem, ParseResult
from app.models import Household, PantryItem, User
from app.renderer import render_ingest_reply
from tests.fakes import FakeLLMClient


def test_parsed_item_defaults_track_worthy_true():
    item = ParsedItem(
        is_food=True, name="Whole Milk", est_shelf_life_days=7, confidence=0.9
    )
    assert item.track_worthy is True
    assert item.exclusion_reason is None


def test_parsed_item_can_be_excluded():
    item = ParsedItem(
        is_food=True, name="Ketchup", est_shelf_life_days=365, confidence=0.9,
        track_worthy=False, exclusion_reason="shelf_stable",
    )
    assert item.track_worthy is False
    assert item.exclusion_reason == "shelf_stable"


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        household = Household(created_at=datetime.now(UTC))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id is not None
        db.add(User(
            telegram_id=1,
            chat_id=1,
            household_id=household.id,
            created_at=datetime.now(UTC),
        ))
        db.commit()
        yield db


@pytest.mark.asyncio
async def test_ingest_excludes_non_trackable_and_reports(session):
    llm = FakeLLMClient(canned=LLMResult(parse=ParseResult(
        purchase_date=date(2026, 5, 28), purchase_date_confidence=0.9,
        items=[
            ParsedItem(is_food=True, name="Whole Milk", est_shelf_life_days=7, confidence=0.9),
            ParsedItem(is_food=True, name="Ketchup", est_shelf_life_days=365, confidence=0.9,
                       track_worthy=False, exclusion_reason="shelf_stable"),
            ParsedItem(is_food=False, name="Advil", est_shelf_life_days=365, confidence=0.9,
                       track_worthy=False, exclusion_reason="non_food"),
        ],
    ), cost_micros_usd=1000))
    summary = await ingest_photo(
        session, llm, household_id=1, photo_file_id="fid", image_bytes=b"jpg",
        today=date(2026, 5, 28),
        scanned_at=datetime(2026, 5, 28, 12, tzinfo=UTC),
    )
    assert summary.inserted_food_count == 1
    assert summary.skipped_excluded_count == 2
    assert set(summary.skipped_excluded_names) == {"Ketchup", "Advil"}
    names = {i.normalized_name for i in session.exec(select(PantryItem)).all()}
    assert names == {"whole milk"}
    # the one inserted item was a cache miss -> recorded for later refine
    assert len(summary.uncached_item_ids) == 1


def test_render_ingest_reply_lists_excluded():
    summary = IngestSummary(
        receipt_id=1, inserted_food_count=1,
        inserted_item_ids=[1], inserted_item_names=["Whole Milk"],
        inserted_item_expires_on=[date(2026, 6, 4)], inserted_item_shelf_life_days=[7],
        purchase_date=date(2026, 5, 28), purchase_date_assumed=False,
        cost_micros_usd=1000,
        skipped_excluded_count=2, skipped_excluded_names=["Ketchup", "Advil"],
    )
    text = render_ingest_reply(summary, today=date(2026, 5, 28))
    assert "Skipped (not tracked): Ketchup, Advil" in text
    assert "/add" in text


def test_render_ingest_reply_excluded_only_receipt():
    # A receipt containing only non-tracked items (medicines, condiments) has
    # inserted_food_count==0; the excluded-items hint must still be shown so the
    # user knows why nothing was logged and can /add if needed.
    summary = IngestSummary(
        receipt_id=1, inserted_food_count=0,
        inserted_item_ids=[], inserted_item_names=[],
        inserted_item_expires_on=[], inserted_item_shelf_life_days=[],
        purchase_date=date(2026, 5, 28), purchase_date_assumed=False,
        cost_micros_usd=500,
        skipped_excluded_count=3, skipped_excluded_names=["Advil", "Ketchup", "Bleach"],
    )
    text = render_ingest_reply(summary, today=date(2026, 5, 28))
    assert "No food items found" in text
    assert "Skipped (not tracked): Advil, Ketchup, Bleach" in text
    assert "/add" in text
