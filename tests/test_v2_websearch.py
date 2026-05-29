import pytest
from datetime import date, datetime, timezone, timedelta

from sqlmodel import SQLModel, Session, create_engine

from app.cache import get_cached
from app.models import PantryItem, Receipt, User
from app.refine_service import ShelfLifeSearchResult, resolve_search_days, SEARCH_MIN_CONFIDENCE, refine_receipt_items
from tests.fakes import FakeSearchClient


def test_resolve_accepts_confident_in_range():
    r = ShelfLifeSearchResult(days=14, confidence=0.9, cost_micros_usd=500)
    assert resolve_search_days(r) == 14


def test_resolve_rejects_low_confidence():
    r = ShelfLifeSearchResult(days=14, confidence=SEARCH_MIN_CONFIDENCE - 0.01, cost_micros_usd=500)
    assert resolve_search_days(r) is None


def test_resolve_rejects_out_of_range_or_missing():
    assert resolve_search_days(ShelfLifeSearchResult(days=None, confidence=0.9, cost_micros_usd=0)) is None
    assert resolve_search_days(ShelfLifeSearchResult(days=0, confidence=0.9, cost_micros_usd=0)) is None
    assert resolve_search_days(ShelfLifeSearchResult(days=999, confidence=0.9, cost_micros_usd=0)) is None


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        db.commit()
        yield db


def _item(session, name, *, days=5, status="active", source="llm", rid=None):
    item = PantryItem(
        user_id=1, raw_name=name, normalized_name=name.lower(), category="dairy",
        qty=1.0, unit=None, purchased_on=date(2026, 5, 28), shelf_life_days=days,
        shelf_life_source=source, ingest_shelf_life_source="llm",
        expires_on=date(2026, 5, 28) + timedelta(days=days), status=status,
        created_via="receipt", source_receipt_id=rid,
        created_at=datetime.now(timezone.utc),
    )
    session.add(item); session.commit(); session.refresh(item)
    return item


@pytest.mark.asyncio
async def test_refine_updates_untouched_item_and_writes_cache(session):
    item = _item(session, "Kefir", days=7)
    search = FakeSearchClient(by_name={
        "Kefir": ShelfLifeSearchResult(days=14, confidence=0.9, cost_micros_usd=400),
    })
    result = await refine_receipt_items(
        session, search, user_id=1, item_ids=[item.id], today=date(2026, 5, 28),
    )
    assert result.updated_ids == [item.id]
    assert result.total_cost_micros == 400
    session.refresh(item)
    assert item.shelf_life_days == 14
    assert item.expires_on == date(2026, 5, 28) + timedelta(days=14)
    cached = get_cached(session, 1, "kefir")
    assert cached is not None and cached.days == 14


@pytest.mark.asyncio
async def test_refine_skips_touched_items(session):
    corrected = _item(session, "Tofu", source="user_correction")
    eaten = _item(session, "Milk", status="eaten")
    search = FakeSearchClient(default=ShelfLifeSearchResult(days=20, confidence=0.95, cost_micros_usd=100))
    result = await refine_receipt_items(
        session, search, user_id=1, item_ids=[corrected.id, eaten.id], today=date(2026, 5, 28),
    )
    assert result.updated_ids == []
    assert search.calls == []


@pytest.mark.asyncio
async def test_refine_skips_low_confidence_keeps_estimate(session):
    item = _item(session, "Brie", days=7)
    search = FakeSearchClient(default=ShelfLifeSearchResult(days=30, confidence=0.4, cost_micros_usd=200))
    result = await refine_receipt_items(
        session, search, user_id=1, item_ids=[item.id], today=date(2026, 5, 28),
    )
    assert result.updated_ids == []
    session.refresh(item)
    assert item.shelf_life_days == 7
