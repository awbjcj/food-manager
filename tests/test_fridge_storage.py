"""Fridge Storage State: chilling extends durability via a refrigerator table,
shares the unified Storage Date origin, and follows forward-only transitions.
"""
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.cache import get_cached, put_cached
from app.commands import parse_callback
from app.frozen_shelf_life import FRIDGE_DEFAULT_DAYS, resolve_storage_days
from app.models import Household, PantryItem, User
from app.pantry_service import move_to_storage
from app.refine_service import ShelfLifeSearchResult
from app.renderer import build_item_card_keyboard, render_item_line
from app.storage_state import (
    can_move_to,
    compute_expiry,
    next_storage_options,
    shelf_life_origin,
)
from tests.fakes import FakeSearchClient


@dataclass
class _StorageTimedStub:
    purchased_on: date
    stored_on: date | None
    shelf_life_days: int


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
            telegram_id=1, chat_id=1, household_id=hh.id,
            created_at=datetime.now(UTC),
        ))
        db.commit()
        yield db


def _fresh_item(session, *, name="Chicken", normalized="chicken", days=2,
                today=date(2026, 6, 8), category="meat"):
    item = PantryItem(
        household_id=1, raw_name=name, normalized_name=normalized,
        category=category, qty=1.0, purchased_on=today, shelf_life_days=days,
        shelf_life_source="llm", ingest_shelf_life_source="llm",
        expires_on=today + timedelta(days=days), status="active",
        created_via="receipt", created_at=datetime.now(UTC),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    assert item.id is not None
    return item


# --- storage_state module (the deep seam) ---------------------------------

def test_forward_only_transitions():
    assert next_storage_options("default") == ("fridge", "frozen")
    assert next_storage_options("fridge") == ("frozen",)
    assert next_storage_options("frozen") == ()
    assert can_move_to("default", "fridge")
    assert can_move_to("fridge", "frozen")
    assert not can_move_to("fridge", "default")   # no thaw-back
    assert not can_move_to("frozen", "fridge")    # frozen is terminal


def test_origin_and_expiry_use_stored_on_when_present():
    purchase = date(2026, 6, 1)
    chilled = date(2026, 6, 5)
    item = _StorageTimedStub(purchased_on=purchase, stored_on=None, shelf_life_days=3)
    assert shelf_life_origin(item) == purchase
    assert compute_expiry(item) == purchase + timedelta(days=3)
    item.stored_on = chilled
    assert shelf_life_origin(item) == chilled
    assert compute_expiry(item) == chilled + timedelta(days=3)


# --- fridge resolver ------------------------------------------------------

@pytest.mark.asyncio
async def test_fridge_resolver_foodkeeper_hit_and_caches(session):
    decision = await resolve_storage_days(
        session, household_id=1, state="fridge",
        normalized_name="chicken", food_name="Chicken",
    )
    assert decision.source == "fridge_foodkeeper"
    assert decision.days == 2
    cached = get_cached(session, 1, "fridge chicken")
    assert cached is not None and cached.days == 2


@pytest.mark.asyncio
async def test_fridge_resolver_cache_key_isolated_from_frozen(session):
    put_cached(session, 1, "fridge chicken", days=4, category=None,
               confidence=0.9, source="llm")
    decision = await resolve_storage_days(
        session, household_id=1, state="fridge",
        normalized_name="chicken", food_name="Chicken",
    )
    assert decision.source == "cache"
    assert decision.days == 4
    # The frozen entry is a different key, untouched.
    assert get_cached(session, 1, "frozen chicken") is None


@pytest.mark.asyncio
async def test_fridge_resolver_search_uses_refrigerated_phrase(session):
    search = FakeSearchClient(
        default=ShelfLifeSearchResult(days=9, confidence=0.9, cost_micros_usd=10)
    )
    decision = await resolve_storage_days(
        session, household_id=1, state="fridge",
        normalized_name="durian", food_name="Durian", search=search,
    )
    assert decision.source == "fridge_llm"
    assert decision.days == 9
    assert search.calls == ["refrigerated Durian"]


@pytest.mark.asyncio
async def test_fridge_resolver_default_when_unknown(session):
    decision = await resolve_storage_days(
        session, household_id=1, state="fridge",
        normalized_name="durian", food_name="Durian",
    )
    assert decision.source == "fridge_default"
    assert decision.days == FRIDGE_DEFAULT_DAYS


# --- move_to_storage ------------------------------------------------------

@pytest.mark.asyncio
async def test_chill_item_sets_stored_on_and_fridge_shelf_life(session):
    today = date(2026, 6, 8)
    item = _fresh_item(session, today=today)
    assert item.id is not None
    result = await move_to_storage(
        session, household_id=1, item_id=item.id, state="fridge", today=today,
    )
    assert result.applied is True
    session.refresh(item)
    assert item.storage == "fridge"
    assert item.stored_on == today
    assert item.shelf_life_days == 2  # fridge FoodKeeper chicken
    assert item.expires_on == today + timedelta(days=2)
    assert item.shelf_life_source == "fridge_foodkeeper"


@pytest.mark.asyncio
async def test_chill_then_freeze_resets_origin_and_shelf_life(session):
    chill_day = date(2026, 6, 8)
    freeze_day = date(2026, 6, 10)
    item = _fresh_item(session, today=chill_day)
    assert item.id is not None
    await move_to_storage(session, household_id=1, item_id=item.id,
                          state="fridge", today=chill_day)
    result = await move_to_storage(session, household_id=1, item_id=item.id,
                                   state="frozen", today=freeze_day)
    assert result.applied is True
    session.refresh(item)
    assert item.storage == "frozen"
    assert item.stored_on == freeze_day
    assert item.shelf_life_days == 270  # frozen FoodKeeper chicken
    assert item.expires_on == freeze_day + timedelta(days=270)


@pytest.mark.asyncio
async def test_cannot_chill_a_frozen_item(session):
    today = date(2026, 6, 8)
    item = _fresh_item(session, today=today)
    assert item.id is not None
    await move_to_storage(session, household_id=1, item_id=item.id,
                          state="frozen", today=today)
    result = await move_to_storage(session, household_id=1, item_id=item.id,
                                   state="fridge", today=today)
    assert result.applied is False
    assert result.was_already is True


@pytest.mark.asyncio
async def test_chill_is_idempotent(session):
    today = date(2026, 6, 8)
    item = _fresh_item(session, today=today)
    assert item.id is not None
    await move_to_storage(session, household_id=1, item_id=item.id,
                          state="fridge", today=today)
    again = await move_to_storage(session, household_id=1, item_id=item.id,
                                  state="fridge", today=today)
    assert again.applied is False


# --- renderer + parsing ---------------------------------------------------

def test_fridge_badge_rendered(session):
    today = date(2026, 6, 8)
    item = _fresh_item(session, today=today)
    item.storage = "fridge"
    line = render_item_line(item, today=today, lang="en")
    assert "🧊" in line
    assert "❄️" not in line


def test_card_keyboard_offers_forward_storage_moves():
    def card_data(storage):
        item = SimpleNamespace(id=5, storage=storage)
        rows = build_item_card_keyboard(item, lang="en")
        return {b.callback_data for row in rows for b in row}

    default_data = card_data("default")
    assert "act:fridge:5" in default_data and "act:freeze:5" in default_data

    fridge_data = card_data("fridge")
    assert "act:freeze:5" in fridge_data and "act:fridge:5" not in fridge_data

    frozen_data = card_data("frozen")
    assert "act:fridge:5" not in frozen_data and "act:freeze:5" not in frozen_data


def test_parse_callback_fridge():
    action = parse_callback("act:fridge:42")
    assert action.verb == "fridge"
    assert action.item_id == 42
