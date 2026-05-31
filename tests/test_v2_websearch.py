import pytest
from datetime import date, datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from sqlmodel import SQLModel, Session, create_engine

import app.bot as bot_mod
from app.refine_service import _accrue_receipt_cost
from app.cache import get_cached
from app.correction_service import propose_add
from app.llm import LLMResult, ParseResult, ParsedItem, ProposedAddItem
from app.models import Household, PantryItem, Receipt, User
from app.pantry_service import compute_stats
from app.refine_service import (
    ShelfLifeSearchResult,
    resolve_search_days,
    SEARCH_MIN_CONFIDENCE,
    refine_receipt_items,
    AnthropicSearchClient,
)
from tests.fakes import FakeLLMClient, FakeSearchClient, FakeTextLLMClient


def test_resolve_accepts_confident_in_range():
    r = ShelfLifeSearchResult(days=14, confidence=0.9, cost_micros_usd=500)
    assert resolve_search_days(r) == 14


def test_resolve_rejects_low_confidence():
    r = ShelfLifeSearchResult(
        days=14, confidence=SEARCH_MIN_CONFIDENCE - 0.01, cost_micros_usd=500
    )
    assert resolve_search_days(r) is None


def test_resolve_rejects_out_of_range_or_missing():
    assert (
        resolve_search_days(
            ShelfLifeSearchResult(days=None, confidence=0.9, cost_micros_usd=0)
        )
        is None
    )
    assert (
        resolve_search_days(
            ShelfLifeSearchResult(days=0, confidence=0.9, cost_micros_usd=0)
        )
        is None
    )
    assert (
        resolve_search_days(
            ShelfLifeSearchResult(days=999, confidence=0.9, cost_micros_usd=0)
        )
        is None
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        db.add(User(telegram_id=1, chat_id=1, household_id=household.id, created_at=datetime.now(timezone.utc)))
        db.commit()
        yield db


def _item(session, name, *, days=5, status="active", source="llm", rid=None):
    item = PantryItem(
        household_id=1,
        raw_name=name,
        normalized_name=name.lower(),
        category="dairy",
        qty=1.0,
        unit=None,
        purchased_on=date(2026, 5, 28),
        shelf_life_days=days,
        shelf_life_source=source,
        ingest_shelf_life_source="llm",
        expires_on=date(2026, 5, 28) + timedelta(days=days),
        status=status,
        created_via="receipt",
        source_receipt_id=rid,
        created_at=datetime.now(timezone.utc),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


@pytest.mark.asyncio
async def test_refine_updates_untouched_item_and_writes_cache(session):
    item = _item(session, "Kefir", days=7)
    assert item.id is not None
    search = FakeSearchClient(
        by_name={
            "Kefir": ShelfLifeSearchResult(
                days=14, confidence=0.9, cost_micros_usd=400
            ),
        }
    )
    result = await refine_receipt_items(
        session,
        search,
        household_id=1,
        item_ids=[item.id],
        today=date(2026, 5, 28),
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
    assert corrected.id is not None and eaten.id is not None
    search = FakeSearchClient(
        default=ShelfLifeSearchResult(days=20, confidence=0.95, cost_micros_usd=100)
    )
    result = await refine_receipt_items(
        session,
        search,
        household_id=1,
        item_ids=[corrected.id, eaten.id],
        today=date(2026, 5, 28),
    )
    assert result.updated_ids == []
    assert search.calls == []


@pytest.mark.asyncio
async def test_refine_skips_low_confidence_keeps_estimate(session):
    item = _item(session, "Brie", days=7)
    assert item.id is not None
    search = FakeSearchClient(
        default=ShelfLifeSearchResult(days=30, confidence=0.4, cost_micros_usd=200)
    )
    result = await refine_receipt_items(
        session,
        search,
        household_id=1,
        item_ids=[item.id],
        today=date(2026, 5, 28),
    )
    assert result.updated_ids == []
    session.refresh(item)
    assert item.shelf_life_days == 7


# ---------------------------------------------------------------------------
# Integration: handle_photo spawns background refine and edits the message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_photo_spawns_refine_and_edits_message(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)

    # In-memory DB with a seeded user
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        db.add(User(telegram_id=1, chat_id=1, household_id=household.id,
                    created_at=datetime.now(timezone.utc)))
        db.commit()
    session_factory = lambda: Session(engine)

    # FakeLLMClient: returns one food item (Kefir, 7d, conf=0.9) → cache miss
    llm = FakeLLMClient(
        canned=LLMResult(
            parse=ParseResult(
                items=[
                    ParsedItem(
                        is_food=True,
                        name="Kefir",
                        category="dairy",
                        est_shelf_life_days=7,
                        confidence=0.9,
                        track_worthy=True,
                    )
                ]
            ),
            cost_micros_usd=100,
        )
    )

    # FakeSearchClient: returns 14d for any item
    search = FakeSearchClient(
        default=ShelfLifeSearchResult(days=14, confidence=0.95, cost_micros_usd=300)
    )

    # Stub bot with edit_message_text
    stub_bot = MagicMock()
    stub_bot.edit_message_text = AsyncMock()

    # spawn just captures the coroutine; does NOT run it
    captured: list = []

    def spawn(coro):
        captured.append(coro)

    # Stub message
    sent_msg = MagicMock()
    sent_msg.message_id = 99
    msg = MagicMock()
    msg.from_user = MagicMock(id=1)
    msg.chat = MagicMock(id=1, type="private")
    photo_obj = MagicMock()
    photo_obj.file_id = "fake_file_id"
    msg.photo = [photo_obj]
    msg.answer = AsyncMock(return_value=sent_msg)

    photo_downloader = AsyncMock(return_value=b"jpg")
    now_provider = lambda tz: datetime(2026, 5, 28, tzinfo=timezone.utc)

    await bot_mod.handle_photo(
        msg,
        session_factory=session_factory,
        now_provider=now_provider,
        llm=llm,
        photo_downloader=photo_downloader,
        search=search,
        spawn=spawn,
        bot=stub_bot,
    )

    # Fast reply was sent once
    msg.answer.assert_awaited_once()

    # Exactly one coroutine was captured (not yet run)
    assert len(captured) == 1

    # Now run the background coroutine deterministically
    await captured[0]

    # The bot should have edited the message
    stub_bot.edit_message_text.assert_awaited_once()
    call_kwargs = stub_bot.edit_message_text.await_args.kwargs
    edited_text = call_kwargs["text"]
    assert "Kefir" in edited_text
    assert "✓refined" in edited_text


@pytest.mark.asyncio
async def test_propose_add_uses_search_on_cache_miss(session):
    text_llm = FakeTextLLMClient(
        canned_add=(
            [
                ProposedAddItem(
                    name="Kefir",
                    explicit_user_expiry=False,
                    estimated_shelf_life_days=7,
                    confidence=0.9,
                )
            ],
            500,
        )
    )
    search = FakeSearchClient(
        default=ShelfLifeSearchResult(days=14, confidence=0.95, cost_micros_usd=200)
    )
    proposals, _ = await propose_add(
        session,
        llm=text_llm,
        household_id=1,
        user_text="kefir",
        today=date(2026, 5, 28),
        tz="America/Detroit",
        search=search,
    )
    assert proposals[0].payload.shelf_life_days == 14
    assert proposals[0].payload.shelf_life_source == "websearch"


@pytest.mark.asyncio
async def test_propose_add_skips_search_when_user_gave_expiry(session):
    text_llm = FakeTextLLMClient(
        canned_add=(
            [
                ProposedAddItem(
                    name="Kefir",
                    explicit_user_expiry=True,
                    shelf_life_days=3,
                    confidence=0.9,
                )
            ],
            500,
        )
    )
    search = FakeSearchClient(
        default=ShelfLifeSearchResult(days=14, confidence=0.95, cost_micros_usd=200)
    )
    proposals, _ = await propose_add(
        session,
        llm=text_llm,
        household_id=1,
        user_text="kefir keeps 3 days",
        today=date(2026, 5, 28),
        tz="America/Detroit",
        search=search,
    )
    assert proposals[0].payload.shelf_life_days == 3
    assert search.calls == []


@pytest.mark.asyncio
async def test_propose_add_no_search_client_uses_estimate(session):
    text_llm = FakeTextLLMClient(
        canned_add=(
            [
                ProposedAddItem(
                    name="Kumquat",
                    explicit_user_expiry=False,
                    estimated_shelf_life_days=9,
                    confidence=0.9,
                )
            ],
            500,
        )
    )
    proposals, _ = await propose_add(
        session,
        llm=text_llm,
        household_id=1,
        user_text="kumquat",
        today=date(2026, 5, 28),
        tz="America/Detroit",
    )
    # no search client passed -> falls through to LLM estimate (or default table if one exists)
    assert proposals[0].payload.shelf_life_days in (9,) or proposals[
        0
    ].payload.shelf_life_source in ("manual_fallback",)


@pytest.mark.asyncio
async def test_anthropic_search_client_parses_days_and_cost():
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text='{"days": 14, "confidence": 0.9}')]
    msg.usage = MagicMock(input_tokens=100, output_tokens=10)
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(return_value=msg)
    client = AnthropicSearchClient(sdk=sdk, model="claude-sonnet-4-6")
    result = await client.lookup_shelf_life(name="Kefir", category="dairy")
    assert result.days == 14
    assert result.confidence == 0.9
    assert result.cost_micros_usd == 450  # 100*3 + 10*15 (sonnet pricing)


@pytest.mark.asyncio
async def test_anthropic_search_client_handles_transport_error():
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(side_effect=RuntimeError("boom"))
    client = AnthropicSearchClient(sdk=sdk, model="claude-sonnet-4-6")
    result = await client.lookup_shelf_life(name="Kefir", category=None)
    assert result.days is None
    assert result.confidence == 0.0
    assert result.cost_micros_usd is None


def test_accrued_search_cost_shows_in_stats(session):
    r = Receipt(
        household_id=1,
        photo_file_id="r1",
        purchase_date=date(2026, 5, 28),
        purchase_date_source="receipt",
        scanned_at=datetime.now(timezone.utc),
        llm_cost_micros_usd=1000,
    )
    session.add(r)
    session.commit()
    _accrue_receipt_cost(session, r.id, 300)  # simulate refine search cost
    stats = compute_stats(session, household_id=1, now=datetime.now(timezone.utc))
    assert stats.total_cost_micros_usd == 1300


# ---------------------------------------------------------------------------
# Fix 1: /add inline websearch cost must flow into the proposal's cost_share
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_add_search_cost_added_to_cost_share(session):
    text_llm = FakeTextLLMClient(
        canned_add=(
            [
                ProposedAddItem(
                    name="Kefir",
                    explicit_user_expiry=False,
                    estimated_shelf_life_days=7,
                    confidence=0.9,
                )
            ],
            500,
        )
    )
    search = FakeSearchClient(
        default=ShelfLifeSearchResult(days=14, confidence=0.95, cost_micros_usd=200)
    )
    proposals, _ = await propose_add(
        session,
        llm=text_llm,
        household_id=1,
        user_text="kefir",
        today=date(2026, 5, 28),
        tz="America/Detroit",
        search=search,
    )
    # single item: parse share 500 + search 200 = 700
    assert proposals[0].cost_share == 700


@pytest.mark.asyncio
async def test_propose_add_search_cost_when_parse_cost_unknown(session):
    text_llm = FakeTextLLMClient(
        canned_add=(
            [
                ProposedAddItem(
                    name="Kefir",
                    explicit_user_expiry=False,
                    estimated_shelf_life_days=7,
                    confidence=0.9,
                )
            ],
            None,
        )
    )  # unknown parse cost
    search = FakeSearchClient(
        default=ShelfLifeSearchResult(days=14, confidence=0.95, cost_micros_usd=200)
    )
    proposals, _ = await propose_add(
        session,
        llm=text_llm,
        household_id=1,
        user_text="kefir",
        today=date(2026, 5, 28),
        tz="America/Detroit",
        search=search,
    )
    assert proposals[0].cost_share == 200  # parse None -> just search cost


# ---------------------------------------------------------------------------
# Fix 2: refine_receipt_items must re-check is_untouched AFTER the search await
# ---------------------------------------------------------------------------


class _RemovingSearch:
    def __init__(self, session, item_id):
        self._session = session
        self._item_id = item_id
        self.calls = []

    async def lookup_shelf_life(self, *, name, category):
        self.calls.append(name)
        # simulate an undo/eaten committed during the search await
        target = self._session.get(PantryItem, self._item_id)
        target.status = "removed"
        self._session.add(target)
        self._session.commit()
        return ShelfLifeSearchResult(days=14, confidence=0.95, cost_micros_usd=100)


@pytest.mark.asyncio
async def test_refine_skips_item_touched_during_search(session):
    item = _item(session, "Kefir", days=7)  # _item helper already exists in this file
    assert item.id is not None
    search = _RemovingSearch(session, item.id)
    result = await refine_receipt_items(
        session,
        search,
        household_id=1,
        item_ids=[item.id],
        today=date(2026, 5, 28),
    )
    assert result.updated_ids == []  # write skipped after re-check
    assert result.total_cost_micros == 100  # but the search cost is still accrued
    session.refresh(item)
    assert item.shelf_life_days == 7  # estimate preserved
    assert item.status == "removed"


@pytest.mark.asyncio
async def test_run_receipt_refine_refreshes_summary_and_accrues_cost():
    from sqlmodel import SQLModel, Session, create_engine
    from app.ingest_service import IngestSummary
    from app.refine_service import run_receipt_refine

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    factory = lambda: Session(engine)
    with factory() as s:
        household = Household(created_at=datetime.now(timezone.utc))
        s.add(household)
        s.commit()
        s.refresh(household)
        s.add(User(telegram_id=1, chat_id=1, household_id=household.id, created_at=datetime.now(timezone.utc)))
        r = Receipt(
            household_id=household.id,
            photo_file_id="r1",
            purchase_date=date(2026, 5, 28),
            purchase_date_source="receipt",
            scanned_at=datetime.now(timezone.utc),
            llm_cost_micros_usd=1000,
        )
        s.add(r)
        s.commit()
        s.refresh(r)
        item = PantryItem(
            household_id=household.id,
            raw_name="Kefir",
            normalized_name="kefir",
            category="dairy",
            qty=1.0,
            unit=None,
            purchased_on=date(2026, 5, 28),
            shelf_life_days=7,
            shelf_life_source="llm",
            ingest_shelf_life_source="llm",
            expires_on=date(2026, 6, 4),
            status="active",
            created_via="receipt",
            source_receipt_id=r.id,
            created_at=datetime.now(timezone.utc),
        )
        s.add(item)
        s.commit()
        s.refresh(item)
        receipt_id, item_id = r.id, item.id
        assert receipt_id is not None and item_id is not None

    summary = IngestSummary(
        receipt_id=receipt_id,
        inserted_food_count=1,
        inserted_item_ids=[item_id],
        inserted_item_names=["Kefir"],
        inserted_item_expires_on=[date(2026, 6, 4)],
        inserted_item_shelf_life_days=[7],
        purchase_date=date(2026, 5, 28),
        purchase_date_assumed=False,
        cost_micros_usd=1000,
        uncached_item_ids=[item_id],
    )
    search = FakeSearchClient(
        default=ShelfLifeSearchResult(days=14, confidence=0.95, cost_micros_usd=300)
    )

    refined = await run_receipt_refine(
        factory,
        search,
        item_ids=[item_id],
        summary=summary,
        household_id=1,
        receipt_id=receipt_id,
        today=date(2026, 5, 28),
    )
    assert refined == frozenset({item_id})
    # summary refreshed in place to the new shelf life/expiry
    assert summary.inserted_item_shelf_life_days == [14]
    assert summary.inserted_item_expires_on == [date(2026, 5, 28) + timedelta(days=14)]
    # cost accrued onto the receipt
    with factory() as s:
        rcpt = s.get(Receipt, receipt_id)
        assert rcpt is not None
        assert rcpt.llm_cost_micros_usd == 1300


@pytest.mark.asyncio
async def test_run_receipt_refine_suppresses_edit_when_receipt_undone():
    # If the receipt was fully undone (deleted) by the time the search returns,
    # run_receipt_refine must report nothing refined so the caller does not
    # resurrect the "Undone" message with a live Undo button.
    from sqlmodel import SQLModel, Session, create_engine
    from app.ingest_service import IngestSummary
    from app.refine_service import run_receipt_refine

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    factory = lambda: Session(engine)
    with factory() as s:
        household = Household(created_at=datetime.now(timezone.utc))
        s.add(household)
        s.commit()
        s.refresh(household)
        s.add(User(telegram_id=1, chat_id=1, household_id=household.id, created_at=datetime.now(timezone.utc)))
        r = Receipt(
            household_id=household.id,
            photo_file_id="r1",
            purchase_date=date(2026, 5, 28),
            purchase_date_source="receipt",
            scanned_at=datetime.now(timezone.utc),
            llm_cost_micros_usd=1000,
        )
        s.add(r)
        s.commit()
        s.refresh(r)
        item = PantryItem(
            household_id=household.id,
            raw_name="Kefir",
            normalized_name="kefir",
            category="dairy",
            qty=1.0,
            unit=None,
            purchased_on=date(2026, 5, 28),
            shelf_life_days=7,
            shelf_life_source="llm",
            ingest_shelf_life_source="llm",
            expires_on=date(2026, 6, 4),
            status="active",
            created_via="receipt",
            source_receipt_id=r.id,
            created_at=datetime.now(timezone.utc),
        )
        s.add(item)
        s.commit()
        s.refresh(item)
        receipt_id, item_id = r.id, item.id
        assert receipt_id is not None and item_id is not None

    summary = IngestSummary(
        receipt_id=receipt_id,
        inserted_food_count=1,
        inserted_item_ids=[item_id],
        inserted_item_names=["Kefir"],
        inserted_item_expires_on=[date(2026, 6, 4)],
        inserted_item_shelf_life_days=[7],
        purchase_date=date(2026, 5, 28),
        purchase_date_assumed=False,
        cost_micros_usd=1000,
        uncached_item_ids=[item_id],
    )
    search = FakeSearchClient(
        default=ShelfLifeSearchResult(days=14, confidence=0.95, cost_micros_usd=300)
    )

    # Receipt fully undone (deleted) before refine completes; item still active.
    with factory() as s:
        s.delete(s.get(Receipt, receipt_id))
        s.commit()

    refined = await run_receipt_refine(
        factory,
        search,
        item_ids=[item_id],
        summary=summary,
        household_id=1,
        receipt_id=receipt_id,
        today=date(2026, 5, 28),
    )
    assert refined == frozenset()


@pytest.mark.asyncio
async def test_run_receipt_refine_accrues_cost_even_when_nothing_refined():
    # When all searches return low-confidence results (no item updates),
    # the search cost must still be accrued on the receipt so /stats is accurate.
    from app.ingest_service import IngestSummary
    from app.refine_service import run_receipt_refine

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    factory = lambda: Session(engine)
    with factory() as s:
        household = Household(created_at=datetime.now(timezone.utc))
        s.add(household)
        s.commit()
        s.refresh(household)
        s.add(User(telegram_id=1, chat_id=1, household_id=household.id, created_at=datetime.now(timezone.utc)))
        r = Receipt(
            household_id=household.id,
            photo_file_id="r1",
            purchase_date=date(2026, 5, 28),
            purchase_date_source="receipt",
            scanned_at=datetime.now(timezone.utc),
            llm_cost_micros_usd=1000,
        )
        s.add(r)
        s.commit()
        s.refresh(r)
        item = PantryItem(
            household_id=household.id,
            raw_name="Kefir",
            normalized_name="kefir",
            category="dairy",
            qty=1.0,
            unit=None,
            purchased_on=date(2026, 5, 28),
            shelf_life_days=7,
            shelf_life_source="llm",
            ingest_shelf_life_source="llm",
            expires_on=date(2026, 6, 4),
            status="active",
            created_via="receipt",
            source_receipt_id=r.id,
            created_at=datetime.now(timezone.utc),
        )
        s.add(item)
        s.commit()
        s.refresh(item)
        receipt_id, item_id = r.id, item.id
        assert receipt_id is not None and item_id is not None

    summary = IngestSummary(
        receipt_id=receipt_id,
        inserted_food_count=1,
        inserted_item_ids=[item_id],
        inserted_item_names=["Kefir"],
        inserted_item_expires_on=[date(2026, 6, 4)],
        inserted_item_shelf_life_days=[7],
        purchase_date=date(2026, 5, 28),
        purchase_date_assumed=False,
        cost_micros_usd=1000,
        uncached_item_ids=[item_id],
    )
    # Low confidence — resolve_search_days returns None, so no item is updated.
    search = FakeSearchClient(
        default=ShelfLifeSearchResult(days=14, confidence=0.3, cost_micros_usd=200)
    )

    refined = await run_receipt_refine(
        factory,
        search,
        item_ids=[item_id],
        summary=summary,
        household_id=1,
        receipt_id=receipt_id,
        today=date(2026, 5, 28),
    )
    assert refined == frozenset()
    # Search was paid for even though nothing was updated.
    with factory() as s:
        rcpt = s.get(Receipt, receipt_id)
        assert rcpt is not None
        assert rcpt.llm_cost_micros_usd == 1200
