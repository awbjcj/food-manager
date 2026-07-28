import sqlite3
import subprocess
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.cache import get_cached, put_cached
from app.commands import parse_callback
from app.correction_service import CorrectPayload, apply_correct, propose_correct
from app.frozen_shelf_life import FROZEN_DEFAULT_DAYS, resolve_frozen_days
from app.ingest_service import ingest_photo
from app.llm import Category, CorrectionDiff, LLMResult, ParsedItem, ParseResult
from app.models import Household, PantryItem, User
from app.pantry_service import correct_item, freeze_item
from app.refine_service import ShelfLifeSearchResult, refine_receipt_items
from app.renderer import build_digest_keyboard, render_item_line
from tests.fakes import FakeLLMClient, FakeSearchClient, FakeTextLLMClient


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        hh = Household(created_at=datetime.now(UTC))
        db.add(hh)
        db.commit()
        db.refresh(hh)
        assert hh.id is not None
        db.add(User(
            telegram_id=1,
            chat_id=1,
            household_id=hh.id,
            created_at=datetime.now(UTC),
        ))
        db.commit()
        yield db


def test_pantryitem_storage_defaults(session):
    today = date(2026, 6, 8)
    item = PantryItem(
        household_id=1,
        raw_name="Chicken",
        normalized_name="chicken",
        category="meat",
        qty=1.0,
        purchased_on=today,
        shelf_life_days=2,
        shelf_life_source="llm",
        ingest_shelf_life_source="llm",
        expires_on=today,
        created_via="receipt",
        created_at=datetime.now(UTC),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    assert item.storage == "default"
    assert item.stored_on is None


def test_migration_unifies_stored_on(tmp_path, monkeypatch):
    db = tmp_path / "m.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    con = sqlite3.connect(str(db))
    cols = {row[1] for row in con.execute("PRAGMA table_info('pantryitem')").fetchall()}
    con.close()
    assert "storage" in cols
    assert "stored_on" in cols
    # 0014 unified the frozen-only column into stored_on.
    assert "frozen_on" not in cols


@pytest.mark.asyncio
async def test_resolver_foodkeeper_hit_and_caches(session):
    decision = await resolve_frozen_days(
        session,
        household_id=1,
        normalized_name="chicken",
        food_name="Chicken",
    )
    assert decision.source == "frozen_foodkeeper"
    assert decision.cache_was_hit is False
    assert decision.days == 270
    cached = get_cached(session, 1, "frozen chicken")
    assert cached is not None and cached.days == 270


@pytest.mark.asyncio
async def test_resolver_handles_already_frozen_normalized_names(session):
    decision = await resolve_frozen_days(
        session,
        household_id=1,
        normalized_name="frozen peas",
        food_name="Frozen Peas 12 oz",
    )
    assert decision.source == "frozen_foodkeeper"
    assert decision.days == 300
    assert get_cached(session, 1, "frozen frozen peas") is None
    cached = get_cached(session, 1, "frozen peas")
    assert cached is not None and cached.days == 300


@pytest.mark.asyncio
async def test_resolver_cache_hit(session):
    put_cached(
        session,
        1,
        "frozen chicken",
        days=200,
        category=None,
        confidence=0.9,
        source="llm",
    )
    decision = await resolve_frozen_days(
        session,
        household_id=1,
        normalized_name="chicken",
        food_name="Chicken",
    )
    assert decision.source == "cache"
    assert decision.cache_was_hit is True
    assert decision.days == 200


@pytest.mark.asyncio
async def test_resolver_search_fallback_for_unknown_food(session):
    search = FakeSearchClient(
        default=ShelfLifeSearchResult(
            days=150,
            confidence=0.9,
            cost_micros_usd=10,
        )
    )
    decision = await resolve_frozen_days(
        session,
        household_id=1,
        normalized_name="durian",
        food_name="Durian",
        search=search,
    )
    assert decision.source == "frozen_llm"
    assert decision.days == 150
    assert search.calls == ["frozen Durian"]
    assert get_cached(session, 1, "frozen durian") is not None


@pytest.mark.asyncio
async def test_resolver_search_name_does_not_double_frozen_prefix(session):
    search = FakeSearchClient(
        default=ShelfLifeSearchResult(
            days=150,
            confidence=0.9,
            cost_micros_usd=10,
        )
    )
    decision = await resolve_frozen_days(
        session,
        household_id=1,
        normalized_name="frozen durian",
        food_name="Frozen Durian",
        search=search,
    )
    assert decision.source == "frozen_llm"
    assert decision.days == 150
    assert search.calls == ["Frozen Durian"]


@pytest.mark.asyncio
async def test_resolver_default_when_no_search_and_unknown(session):
    decision = await resolve_frozen_days(
        session,
        household_id=1,
        normalized_name="durian",
        food_name="Durian",
    )
    assert decision.source == "frozen_default"
    assert decision.days == FROZEN_DEFAULT_DAYS
    cached = get_cached(session, 1, "frozen durian")
    assert cached is not None and cached.days == FROZEN_DEFAULT_DAYS


def _frozen_parse(name: str = "Ice Cream", category: Category = "dairy"):
    return LLMResult(
        parse=ParseResult(
            purchase_date=None,
            purchase_date_confidence=0.0,
            items=[
                ParsedItem(
                    is_food=True,
                    name=name,
                    qty=1.0,
                    unit=None,
                    category=category,
                    est_shelf_life_days=7,
                    confidence=0.9,
                    track_worthy=True,
                    frozen=True,
                ),
            ],
        ),
        cost_micros_usd=10,
    )


@pytest.mark.asyncio
async def test_ingest_marks_frozen_item_with_long_expiry(session):
    today = date(2026, 6, 8)
    llm = FakeLLMClient(canned=_frozen_parse())
    summary = await ingest_photo(
        session,
        llm,
        household_id=1,
        photo_file_id="p1",
        image_bytes=b"x",
        today=today,
    )
    assert summary.inserted_food_count == 1
    item = session.get(PantryItem, summary.inserted_item_ids[0])
    assert item is not None
    assert item.storage == "frozen"
    assert item.stored_on == today
    assert item.shelf_life_days == 60
    assert item.expires_on == today + timedelta(days=60)
    assert item.shelf_life_source == "frozen_foodkeeper"
    assert summary.uncached_item_ids == []


@pytest.mark.asyncio
async def test_ingest_frozen_foodkeeper_miss_uses_search(session):
    today = date(2026, 6, 8)
    llm = FakeLLMClient(canned=_frozen_parse(name="Durian", category="produce"))
    search = FakeSearchClient(
        default=ShelfLifeSearchResult(
            days=150,
            confidence=0.9,
            cost_micros_usd=10,
        )
    )
    summary = await ingest_photo(
        session,
        llm,
        household_id=1,
        photo_file_id="p2",
        image_bytes=b"x",
        today=today,
        search=search,
    )
    item = session.get(PantryItem, summary.inserted_item_ids[0])
    assert item is not None
    assert item.storage == "frozen"
    assert item.shelf_life_days == 150
    assert item.shelf_life_source == "frozen_llm"
    assert search.calls == ["frozen Durian"]
    assert summary.uncached_item_ids == []


def _fresh_item(
    session,
    *,
    name: str = "Chicken",
    normalized: str = "chicken",
    days: int = 2,
    today: date = date(2026, 6, 8),
    category: Category = "meat",
):
    item = PantryItem(
        household_id=1,
        raw_name=name,
        normalized_name=normalized,
        category=category,
        qty=1.0,
        purchased_on=today,
        shelf_life_days=days,
        shelf_life_source="llm",
        ingest_shelf_life_source="llm",
        expires_on=today + timedelta(days=days),
        status="active",
        created_via="receipt",
        created_at=datetime.now(UTC),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    assert item.id is not None
    return item


@pytest.mark.asyncio
async def test_freeze_item_recomputes_expiry_from_today(session):
    today = date(2026, 6, 8)
    item = _fresh_item(session, today=today)
    assert item.id is not None
    result = await freeze_item(session, household_id=1, item_id=item.id, today=today)
    assert result.applied is True
    session.refresh(item)
    assert item.storage == "frozen"
    assert item.stored_on == today
    assert item.shelf_life_days == 270
    assert item.expires_on == today + timedelta(days=270)
    assert item.shelf_life_source == "frozen_foodkeeper"


@pytest.mark.asyncio
async def test_freeze_item_idempotent(session):
    today = date(2026, 6, 8)
    item = _fresh_item(session, today=today)
    assert item.id is not None
    await freeze_item(session, household_id=1, item_id=item.id, today=today)
    again = await freeze_item(session, household_id=1, item_id=item.id, today=today)
    assert again.applied is False
    assert again.was_already is True


@pytest.mark.asyncio
async def test_freeze_item_rejects_non_active(session):
    today = date(2026, 6, 8)
    item = _fresh_item(session, today=today)
    assert item.id is not None
    item.status = "eaten"
    session.add(item)
    session.commit()
    result = await freeze_item(session, household_id=1, item_id=item.id, today=today)
    assert result.applied is False
    assert result.was_already is True


@pytest.mark.asyncio
async def test_freeze_item_clears_snooze(session):
    today = date(2026, 6, 8)
    item = _fresh_item(session, today=today)
    assert item.id is not None
    item.snoozed_until = today + timedelta(days=2)
    session.add(item)
    session.commit()
    await freeze_item(session, household_id=1, item_id=item.id, today=today)
    session.refresh(item)
    assert item.snoozed_until is None


def test_correct_item_uses_frozen_on_origin(session):
    purchase = date(2026, 6, 1)
    freeze_day = date(2026, 6, 8)
    item = _fresh_item(session, today=purchase)
    item.storage = "frozen"
    item.stored_on = freeze_day
    session.add(item)
    session.commit()
    assert item.id is not None
    corrected = correct_item(
        session,
        household_id=1,
        item_id=item.id,
        days=100,
        today=freeze_day,
    )
    assert corrected.expires_on == freeze_day + timedelta(days=100)


def test_correct_item_updates_frozen_cache_key(session):
    purchase = date(2026, 6, 1)
    freeze_day = date(2026, 6, 8)
    item = _fresh_item(session, today=purchase)
    item.storage = "frozen"
    item.stored_on = freeze_day
    session.add(item)
    session.commit()
    assert item.id is not None
    correct_item(
        session,
        household_id=1,
        item_id=item.id,
        days=100,
        today=freeze_day,
    )
    assert get_cached(session, 1, "chicken") is None
    cached = get_cached(session, 1, "frozen chicken")
    assert cached is not None and cached.days == 100


@pytest.mark.asyncio
async def test_propose_correct_uses_frozen_on_origin(session):
    purchase = date(2026, 6, 1)
    freeze_day = date(2026, 6, 8)
    item = _fresh_item(session, today=purchase)
    item.storage = "frozen"
    item.stored_on = freeze_day
    session.add(item)
    session.commit()
    put_cached(
        session,
        1,
        "frozen chicken",
        days=120,
        category="meat",
        confidence=1.0,
        source="user_correction",
    )
    llm = FakeTextLLMClient(
        canned_correct=(
            CorrectionDiff(
                expires_on=freeze_day + timedelta(days=100),
                cache_action="leave",
                rationale="freezer timing",
                confidence=0.9,
            ),
            5,
        )
    )
    payload, cost = await propose_correct(
        session,
        llm=llm,
        household_id=1,
        item=item,
        user_text="expires in 100 days from frozen",
        today=freeze_day,
    )
    assert cost == 5
    assert payload.back_computed_days is True
    assert payload.diff["shelf_life_days"] == {"old": 2, "new": 100}
    assert llm.correct_calls[0]["cache_snapshot"]["normalized_name"] == "frozen chicken"


def test_apply_correct_moves_frozen_cache_key(session):
    purchase = date(2026, 6, 1)
    freeze_day = date(2026, 6, 8)
    item = _fresh_item(session, today=purchase)
    item.storage = "frozen"
    item.stored_on = freeze_day
    session.add(item)
    put_cached(
        session,
        1,
        "frozen chicken",
        days=270,
        category="meat",
        confidence=1.0,
        source="user_correction",
    )
    payload = CorrectPayload(
        diff={
            "name": {"old": "Chicken", "new": "Turkey"},
            "category": None,
            "expires_on": None,
            "shelf_life_days": {"old": 2, "new": 240},
        },
        cache_action="move",
        rationale="corrected frozen item",
        confidence=0.9,
    )
    apply_correct(session, household_id=1, item=item, payload=payload)
    session.flush()
    assert get_cached(session, 1, "frozen chicken") is None
    assert get_cached(session, 1, "turkey") is None
    cached = get_cached(session, 1, "frozen turkey")
    assert cached is not None and cached.days == 240


@pytest.mark.asyncio
async def test_refine_receipt_items_skips_frozen_items(session):
    purchase = date(2026, 6, 1)
    freeze_day = date(2026, 6, 8)
    item = _fresh_item(session, today=purchase)
    item.storage = "frozen"
    item.stored_on = freeze_day
    original_expires = item.expires_on
    session.add(item)
    session.commit()
    assert item.id is not None
    search = FakeSearchClient(
        default=ShelfLifeSearchResult(
            days=100,
            confidence=0.9,
            cost_micros_usd=10,
        )
    )
    result = await refine_receipt_items(
        session,
        search,
        household_id=1,
        item_ids=[item.id],
        today=freeze_day,
    )
    session.refresh(item)
    assert result.updated_ids == []
    assert item.expires_on == original_expires
    assert search.calls == []


def test_parse_callback_freeze():
    action = parse_callback("act:freeze:42")
    assert action.verb == "freeze"
    assert action.item_id == 42


def test_digest_keyboard_opens_item_card():
    item = SimpleNamespace(
        id=7,
        raw_name="frozen peas",
        expires_on=date(2026, 6, 10),
        storage="frozen",
        qty=1,
        unit=None,
    )
    rows = build_digest_keyboard([item], has_more=False, today=date(2026, 6, 9), lang="en")
    assert rows[0][0].callback_data == "item:open:7"


def test_render_item_line_frozen_badge(session):
    today = date(2026, 6, 8)
    item = _fresh_item(session, name="Chicken", today=today)
    item.storage = "frozen"
    item.stored_on = today
    line = render_item_line(item, today=today, lang="en")
    assert "❄️" in line


def test_render_item_line_default_has_no_badge(session):
    today = date(2026, 6, 8)
    item = _fresh_item(
        session,
        name="Milk",
        normalized="milk",
        days=2,
        today=today,
        category="dairy",
    )
    line = render_item_line(item, today=today, lang="en")
    assert "❄️" not in line
