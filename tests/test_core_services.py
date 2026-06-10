import asyncio
import base64
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from sqlmodel import SQLModel, Session, create_engine, select

from app.cache import get_cached, put_cached, write_user_correction
from app.db import make_engine, make_session_factory
from app.ingest_service import (
    DuplicateReceipt,
    compute_shelf_life,
    ingest_photo,
)
from app.llm import AnthropicLLMClient, LLMResult, ParseResult, ParsedItem
from app.models import Household, PantryItem, Receipt, ShelfLifeCache, User
from app.normalization import ALIASES, normalize
from app.pantry_service import (
    ALLOWED_CATEGORIES,
    ListFilter,
    NotOwnerOrMissing,
    compute_nudge_days,
    compute_stats,
    correct_item,
    list_active,
    list_digest_due,
    mark_eaten,
    mark_removed,
    snooze_item,
)
from app.settings import Settings
from app.shelf_life_defaults import lookup_default
from tests.fakes import FakeLLMClient


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id is not None
        db.add(User(telegram_id=1, chat_id=1, household_id=household.id, created_at=datetime.now(timezone.utc)))
        db.commit()
        yield db


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "12345")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key")
    settings = Settings()  # type: ignore[call-arg]
    assert settings.telegram_bot_token == "test-token"
    assert settings.allowed_telegram_user_id == 12345
    assert settings.llm_provider == "anthropic"
    assert settings.anthropic_model == "claude-sonnet-4-6"
    assert settings.anthropic_text_model == "claude-haiku-4-5-20251001"
    assert settings.openai_model == "gpt-5.4"
    assert settings.openai_text_model == "gpt-5.4-mini"


def test_settings_load_openai_provider_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "12345")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    settings = Settings()  # type: ignore[call-arg]
    assert settings.llm_provider == "openai"
    assert settings.openai_api_key == "test-openai-key"
    assert settings.openai_model == "gpt-5.4"
    assert settings.openai_text_model == "gpt-5.4-mini"


def test_recipe_api_keys_optional(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    settings = Settings()  # type: ignore[call-arg]
    assert settings.spoonacular_api_key is None
    monkeypatch.setenv("SPOONACULAR_API_KEY", "spk")
    assert Settings().spoonacular_api_key == "spk"  # type: ignore[call-arg]


def test_make_engine_and_session_factory(tmp_path):
    db = tmp_path / "t.db"
    engine = make_engine(str(db))
    assert engine.url.database == str(db)
    with make_session_factory(engine)() as db_session:
        assert db_session is not None


def test_models_insert_and_cache_composite_pk(session):
    receipt = Receipt(
        household_id=1,
        photo_file_id="abc",
        purchase_date=date(2026, 5, 26),
        purchase_date_source="receipt",
        scanned_at=datetime.now(timezone.utc),
    )
    session.add(receipt)
    session.commit()
    row = ShelfLifeCache(
        household_id=1,
        normalized_name="whole milk",
        days=7,
        category="dairy",
        confidence=0.9,
        learned_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.commit()
    assert session.get(ShelfLifeCache, (1, "whole milk")).days == 7


@pytest.mark.parametrize("raw,expected", [
    ("Whole Milk 1 gal", "whole milk"),
    ("WHOLE MILK", "whole milk"),
    ("  whole   milk  ", "whole milk"),
    ("Bananas, 6 ct", "bananas"),
    ("Sliced Bread 24 oz", "sliced bread"),
    ("Greek Yogurt 32 oz", "greek yogurt"),
    ("Organic Whole Milk 1 gal", "whole milk"),
    ("Frozen Peas 12 oz", "frozen peas"),
])
def test_normalize_baseline_rules(raw, expected):
    assert normalize(raw) == expected
    assert isinstance(ALIASES, dict)


def test_cache_user_correction_priority_and_scope(session):
    assert get_cached(session, 1, "whole milk") is None
    put_cached(session, 1, "whole milk", days=7, category="dairy", confidence=0.9)
    put_cached(session, 1, "whole milk", days=10, category="dairy", confidence=0.7)
    cached = get_cached(session, 1, "whole milk")
    assert cached is not None and cached.days == 7
    write_user_correction(session, 1, "whole milk", days=5)
    cached = get_cached(session, 1, "whole milk")
    assert cached is not None and cached.source == "user_correction"
    session.add(User(telegram_id=2, chat_id=2, household_id=1, created_at=datetime.now(timezone.utc)))
    session.commit()
    assert get_cached(session, 2, "whole milk") is None


def test_shelf_life_defaults():
    default = lookup_default("whole milk")
    assert default is not None and default.days == 7
    assert lookup_default("kefir lime leaves") is None


def _parsed_item(name="Whole Milk 1 gal", days=7, conf=0.95, is_food=True):
    return ParsedItem(
        is_food=is_food,
        name=name,
        qty=1.0,
        unit="gal",
        category="dairy",
        est_shelf_life_days=days,
        confidence=conf,
    )


def _llm_result(items, *, purchase_date: date | None = date(2026, 5, 26), confidence=0.9, cost=18000):
    return LLMResult(
        parse=ParseResult(
            purchase_date=purchase_date,
            purchase_date_confidence=confidence,
            items=items,
        ),
        cost_micros_usd=cost,
    )


def test_fake_llm_client_returns_canned_result():
    fake = FakeLLMClient(canned=_llm_result([_parsed_item()]))
    result = asyncio.run(fake.extract_items_from_image(b"fake"))
    assert result.parse.items[0].name == "Whole Milk 1 gal"


def test_compute_shelf_life_cache_policy(session):
    decision = compute_shelf_life(session, household_id=1, parsed=_parsed_item())
    assert decision.days == 7 and decision.source == "llm"
    cached = get_cached(session, 1, "whole milk")
    assert cached is not None and cached.days == 7
    write_user_correction(session, 1, "whole milk", days=5)
    decision = compute_shelf_life(session, household_id=1, parsed=_parsed_item(days=10))
    assert decision.days == 5 and decision.source == "cache"


def test_compute_shelf_life_medium_confidence_does_not_write_cache(session):
    compute_shelf_life(session, household_id=1, parsed=_parsed_item(conf=0.5))
    assert get_cached(session, 1, "whole milk") is None


@pytest.mark.asyncio
async def test_ingest_photo_happy_path_and_duplicate_guard(session):
    llm = FakeLLMClient(canned=_llm_result([
        _parsed_item(),
        _parsed_item(name="Bananas", days=5),
        _parsed_item(name="Paper Towels", is_food=False),
    ]))
    summary = await ingest_photo(
        session,
        llm,
        household_id=1,
        photo_file_id="fid",
        image_bytes=b"jpg",
        today=date(2026, 5, 26),
    )
    assert summary.inserted_food_count == 2
    assert summary.skipped_non_food_count == 1
    assert {i.normalized_name for i in session.exec(select(PantryItem)).all()} == {
        "whole milk",
        "bananas",
    }
    with pytest.raises(DuplicateReceipt):
        await ingest_photo(
            session,
            llm,
            household_id=1,
            photo_file_id="fid",
            image_bytes=b"jpg",
            today=date(2026, 5, 26),
        )


@pytest.mark.asyncio
async def test_ingest_photo_confidence_and_purchase_date_fallback(session):
    llm = FakeLLMClient(canned=_llm_result(
        [_parsed_item(conf=0.45), _parsed_item(name="Mystery", conf=0.2)],
        purchase_date=None,
        confidence=0.0,
    ))
    summary = await ingest_photo(
        session, llm, household_id=1, photo_file_id="fid2", image_bytes=b"jpg",
        today=date(2026, 5, 26),
    )
    assert summary.purchase_date_assumed is True
    assert summary.inserted_food_count == 1
    assert summary.skipped_low_confidence_names == ["Mystery"]
    assert len(summary.low_confidence_inserted_ids) == 1


def _item(session, name, days_from_today, *, today=date(2026, 5, 26), status="active",
          category="produce", snoozed_until=None, created_via="manual",
          ingest_source="llm") -> PantryItem:
    pantry_item = PantryItem(
        household_id=1,
        raw_name=name,
        normalized_name=name.lower(),
        category=category,
        qty=1.0,
        unit=None,
        purchased_on=today,
        shelf_life_days=days_from_today,
        shelf_life_source="llm",
        ingest_shelf_life_source=ingest_source,
        expires_on=today + timedelta(days=days_from_today),
        status=status,
        snoozed_until=snoozed_until,
        created_via=created_via,
        created_at=datetime.now(timezone.utc),
    )
    session.add(pantry_item)
    session.commit()
    session.refresh(pantry_item)
    assert pantry_item.id is not None
    return pantry_item


def test_pantry_list_filters_and_digest_due(session):
    today = date(2026, 5, 26)
    _item(session, "C", 5, today=today)
    _item(session, "A", 1, today=today)
    _item(session, "B", 3, today=today)
    _item(session, "Z", 2, today=today, status="eaten")
    assert [r.raw_name for r in list_active(session, household_id=1, f=ListFilter.default(), today=today)] == ["A", "B", "C"]
    assert [r.raw_name for r in list_active(session, household_id=1, f=ListFilter(window="week"), today=today)] == ["A", "B", "C"]
    _item(session, "expired", -1, today=today)
    _item(session, "future", 8, today=today)
    _item(session, "snoozed", 3, today=today, snoozed_until=today + timedelta(days=2))
    assert [r.raw_name for r in list_digest_due(session, household_id=1, today=today)] == ["expired", "A", "B", "C"]
    assert "dairy" in ALLOWED_CATEGORIES and "wine" not in ALLOWED_CATEGORIES


def test_pantry_mutations_and_correction(session):
    today = date(2026, 5, 26)
    pantry_item = _item(session, "Whole Milk", 7, today=today, category="dairy")
    assert pantry_item.id is not None
    snooze_item(session, household_id=1, item_id=pantry_item.id, today=today, days=2)
    session.refresh(pantry_item)
    assert pantry_item.snoozed_until == today + timedelta(days=2)
    mark_eaten(session, household_id=1, item_id=pantry_item.id, today=today)
    session.refresh(pantry_item)
    assert pantry_item.status == "eaten" and pantry_item.snoozed_until is None
    correct_item(session, household_id=1, item_id=pantry_item.id, days=5, today=today)
    session.refresh(pantry_item)
    assert pantry_item.expires_on == today + timedelta(days=5)
    removed = _item(session, "Bad Import", 3, status="active")
    assert removed.id is not None
    mark_removed(session, household_id=1, item_id=removed.id, today=today)
    with pytest.raises(ValueError):
        correct_item(session, household_id=1, item_id=removed.id, days=5, today=today)
    with pytest.raises(NotOwnerOrMissing):
        mark_eaten(session, household_id=2, item_id=pantry_item.id, today=today)


def test_compute_nudge_days_relative_and_clamps():
    origin = date(2026, 6, 1)
    today = date(2026, 6, 9)
    assert compute_nudge_days(current_days=7, origin=origin, today=today, code="p7") == 14
    assert compute_nudge_days(current_days=7, origin=origin, today=today, code="p3") == 10
    assert compute_nudge_days(current_days=7, origin=origin, today=today, code="m3") == 4
    assert compute_nudge_days(current_days=2, origin=origin, today=today, code="m3") == 1
    assert compute_nudge_days(current_days=30, origin=origin, today=today, code="today") == 8
    assert compute_nudge_days(current_days=30, origin=today, today=today, code="today") == 1


def test_compute_nudge_days_rejects_unknown_code():
    with pytest.raises(ValueError):
        compute_nudge_days(
            current_days=5,
            origin=date(2026, 6, 1),
            today=date(2026, 6, 9),
            code="zz",
        )


def test_compute_stats(session):
    today = date(2026, 5, 26)
    session.add(Receipt(
        household_id=1,
        photo_file_id="r1",
        purchase_date=today,
        purchase_date_source="receipt",
        scanned_at=datetime.now(timezone.utc),
        llm_cost_micros_usd=15000,
    ))
    session.add(Receipt(
        household_id=1,
        photo_file_id="r2",
        purchase_date=today,
        purchase_date_source="receipt",
        scanned_at=datetime.now(timezone.utc),
        llm_cost_micros_usd=None,
    ))
    _item(session, "active", 1, today=today, created_via="receipt", ingest_source="cache")
    _item(session, "expired", -1, today=today, created_via="receipt", ingest_source="llm")
    _item(session, "eaten", 2, today=today, status="eaten")
    _item(session, "tossed", 2, today=today, status="tossed")
    _item(session, "removed", 2, today=today, status="removed")
    stats = compute_stats(session, household_id=1, now=datetime.now(timezone.utc))
    assert stats.receipt_count == 2
    assert stats.tracked_item_count == 4
    assert stats.removed_item_count == 1
    assert stats.total_cost_micros_usd == 15000
    assert stats.avg_cost_micros_usd == 15000
    assert stats.unknown_cost_receipt_count == 1
    assert stats.cache_hit_percent == 50.0
    assert stats.waste_rate_percent == 50.0


class _StubMessage:
    def __init__(self, tool_input: dict, usage: dict):
        self.content = [MagicMock(type="tool_use", input=tool_input)]
        self.usage = MagicMock(input_tokens=usage["in"], output_tokens=usage["out"])


@pytest.mark.asyncio
async def test_anthropic_client_parse_retry_transport_retry_and_unknown_cost():
    good = {
        "purchase_date": "2026-05-26",
        "purchase_date_confidence": 0.9,
        "items": [{
            "is_food": True,
            "name": "Whole Milk 1 gal",
            "qty": 1.0,
            "unit": "gal",
            "category": "dairy",
            "est_shelf_life_days": 7,
            "confidence": 0.95,
        }],
    }
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(side_effect=[
        RuntimeError("temporary"),
        _StubMessage(good, {"in": 100, "out": 10}),
    ])
    sleep = AsyncMock()
    client = AnthropicLLMClient(sdk=sdk, model="claude-sonnet-4-6", sleep=sleep)
    result = await client.extract_items_from_image(b"\xff\xd8\xffjpeg")
    assert result.parse.items[0].name == "Whole Milk 1 gal"
    assert result.cost_micros_usd == 450
    assert sleep.await_count == 1
    content = sdk.messages.create.call_args.kwargs["messages"][0]["content"]
    assert content[0]["source"]["data"] == base64.b64encode(b"\xff\xd8\xffjpeg").decode()
    assert content[0]["source"]["media_type"] == "image/jpeg"

    sdk2 = MagicMock()
    sdk2.messages.create = AsyncMock(return_value=_StubMessage(good, {"in": 100, "out": 10}))
    assert (await AnthropicLLMClient(sdk2, "future-model").extract_items_from_image(b"x")).cost_micros_usd is None


@pytest.mark.asyncio
async def test_anthropic_client_validates_purchase_date_confidence():
    bad_confidence = {
        "purchase_date": "2026-05-26",
        "purchase_date_confidence": 85,
        "items": [{
            "is_food": True,
            "name": "Whole Milk 1 gal",
            "qty": 1.0,
            "unit": "gal",
            "category": "dairy",
            "est_shelf_life_days": 7,
            "confidence": 0.95,
        }],
    }
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(return_value=_StubMessage(bad_confidence, {"in": 100, "out": 10}))

    with pytest.raises(ValidationError):
        await AnthropicLLMClient(sdk=sdk, model="claude-sonnet-4-6").extract_items_from_image(b"\xff\xd8\xffjpeg")


@pytest.mark.asyncio
async def test_anthropic_client_preserves_png_media_type():
    good = {
        "purchase_date": None,
        "purchase_date_confidence": 0.0,
        "items": [],
    }
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(return_value=_StubMessage(good, {"in": 100, "out": 10}))

    await AnthropicLLMClient(sdk=sdk, model="claude-sonnet-4-6").extract_items_from_image(
        b"\x89PNG\r\n\x1a\npng",
    )

    content = sdk.messages.create.call_args.kwargs["messages"][0]["content"]
    assert content[0]["source"]["media_type"] == "image/png"
