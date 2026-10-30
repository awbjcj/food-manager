"""Regression cases recovered from unresolved historical PR reviews."""
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from app.cache import get_cached, put_cached, write_user_correction
from app.correction_service import AddPayload, apply_add, propose_add
from app.frozen_shelf_life import resolve_storage_days, storage_cache_key
from app.ingest_service import DuplicateReceipt, ingest_photo
from app.llm import (
    LLMResult,
    NameTranslationItem,
    ParsedItem,
    ParseResult,
    ProposedAddItem,
)
from app.models import Household, PantryItem, PaymentEvent, Receipt, Subscription, User
from app.operator.tiers import PaidSubscriptionConflict, set_user_tier
from app.refine_service import refine_receipt_items
from app.shelf_life_search import ShelfLifeSearchResult
from tests.fakes import FakeLLMClient, FakeSearchClient, FakeTextLLMClient

TODAY = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def required[T](row: T | None) -> T:
    assert row is not None
    return row


@pytest.fixture
def engine():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Household(id=1, created_at=NOW))
        session.commit()
        session.add(User(telegram_id=1, chat_id=1, household_id=1, created_at=NOW))
        session.commit()
    yield engine
    engine.dispose()


def parsed(name="Milk", **kwargs):
    return ParsedItem(is_food=True, name=name, est_shelf_life_days=7,
                      confidence=0.9, category="dairy", **kwargs)


async def ingest(session, items, *, cost=100, search=None, llm=None):
    return await ingest_photo(
        session, llm or FakeLLMClient(canned=LLMResult(
            parse=ParseResult(items=items), cost_micros_usd=cost)),
        household_id=1, photo_file_id="distinct-photo", image_bytes=b"test",
        today=TODAY, scanned_at=NOW, search=search,
    )


async def test_receipt_display_translation_cannot_replace_canonical_identity(engine):
    with Session(engine) as session:
        summary = await ingest(session, [parsed("Carrots", name_translations=[
            NameTranslationItem(lang="en", text="Carottes"),
            NameTranslationItem(lang="fr", text="Carottes"),
        ])])
        item = required(session.get(PantryItem, summary.inserted_item_ids[0]))
        assert item.raw_name == "Carrots" and item.normalized_name == "carrots"
        assert get_cached(session, 1, "carrots") is not None
        assert get_cached(session, 1, "carottes") is None


@pytest.mark.parametrize(("state", "natural_name", "expected"), [
    ("frozen", "frozen pizza", 60), ("fridge", "fridge chicken", 2),
])
async def test_storage_namespace_does_not_trust_ambiguous_legacy_entries(
    engine, state, natural_name, expected,
):
    with Session(engine) as session:
        put_cached(session, 1, natural_name, days=3, category=None, confidence=1)
        decision = await resolve_storage_days(
            session, household_id=1, state=state,
            normalized_name=natural_name, food_name=natural_name,
        )
        assert decision.days == expected and not decision.cache_was_hit
        assert required(get_cached(session, 1, natural_name)).days == 3
        assert required(get_cached(session, 1, storage_cache_key(natural_name, state))).days == expected
        write_user_correction(session, 1, storage_cache_key(natural_name, state), days=40)
        again = await resolve_storage_days(
            session, household_id=1, state=state,
            normalized_name=natural_name, food_name=natural_name,
        )
        assert again.days == 40 and again.cache_was_hit


async def test_a_natural_name_cannot_impersonate_the_internal_storage_namespace(engine):
    with Session(engine) as session:
        await ingest(session, [parsed(storage_cache_key("pizza", "frozen"))])
        decision = await resolve_storage_days(session, household_id=1, state="frozen",
            normalized_name="pizza", food_name="Pizza")
        assert decision.days == 60 and not decision.cache_was_hit


@pytest.mark.parametrize("confidence", [0.95, 0.1])
async def test_frozen_lookup_cost_accrues_even_when_search_cannot_resolve(engine, confidence):
    with Session(engine) as session:
        search = FakeSearchClient(default=ShelfLifeSearchResult(
            days=120, confidence=confidence, cost_micros_usd=900,
        ))
        summary = await ingest(session, [parsed("Unknown frozen food", frozen=True)], search=search)
        assert summary.cost_micros_usd == 1000
        assert required(session.get(Receipt, summary.receipt_id)).llm_cost_micros_usd == 1000
        item = required(session.get(PantryItem, summary.inserted_item_ids[0]))
        assert item.shelf_life_days == (120 if confidence > 0.5 else 90)


async def test_refinement_replaces_an_automatic_cache_but_preserves_user_corrections(engine):
    with Session(engine) as session:
        summary = await ingest(session, [parsed()])
        await refine_receipt_items(session, FakeSearchClient(default=ShelfLifeSearchResult(
            days=3, confidence=0.95, cost_micros_usd=10)), household_id=1,
            item_ids=summary.uncached_item_ids, today=TODAY)
        assert required(session.get(PantryItem, summary.inserted_item_ids[0])).shelf_life_days == 3
        assert required(get_cached(session, 1, "milk")).days == 3
        write_user_correction(session, 1, "milk", days=2)
        put_cached(session, 1, "milk", days=10, category=None, confidence=1,
                   replace_automatic=True)
        assert required(get_cached(session, 1, "milk")).days == 2
        assert required(get_cached(session, 1, "milk")).source == "user_correction"


def test_insert_only_cache_write_is_safe_after_a_lost_check_insert_race(engine):
    with Session(engine) as stale, Session(engine) as winner:
        assert get_cached(stale, 1, "milk") is None
        put_cached(winner, 1, "milk", days=5, category=None, confidence=1)
        with patch("app.cache.get_cached", return_value=None):
            row = put_cached(stale, 1, "milk", days=7, category=None, confidence=1)
        assert row.days == 5


async def test_receipt_uniqueness_race_still_maps_to_duplicate(engine):
    class RacingLLM:
        async def extract_items_from_image(self, *args, **kwargs):
            with Session(engine) as winner:
                winner.add(Receipt(household_id=1, photo_file_id="distinct-photo",
                    purchase_date=TODAY, purchase_date_source="scan_fallback", scanned_at=NOW))
                winner.commit()
            return LLMResult(parse=ParseResult(items=[parsed()]))

    with Session(engine) as session:
        with pytest.raises(DuplicateReceipt):
            await ingest(session, [], llm=RacingLLM())
        assert len(session.exec(select(Receipt)).all()) == 1
        assert not session.exec(select(PantryItem)).all()


async def test_other_integrity_failures_are_not_reported_as_duplicate_receipts(engine):
    error = IntegrityError("unrelated constraint", {}, Exception("cache failure"))
    with Session(engine) as session:
        with patch("app.ingest_service.compute_shelf_life", side_effect=error), \
             pytest.raises(IntegrityError, match="unrelated constraint"):
            await ingest(session, [parsed()])
        assert not session.exec(select(Receipt)).all()
        assert not session.exec(select(PantryItem)).all()


@pytest.mark.parametrize("days_later", [1, 31])
def test_family_grant_can_be_renewed_before_or_after_its_expiry(engine, days_later):
    with Session(engine) as session:
        set_user_tier(session, telegram_id=1, tier="family", operator_telegram_id=1,
                      now=NOW, duration_days=30)
        later = NOW + timedelta(days=days_later)
        change = set_user_tier(session, telegram_id=1, tier="family", operator_telegram_id=1,
                               now=later, duration_days=90)
        assert change.changed
        sub = required(session.get(Subscription, 1))
        assert sub.period_end == (later + timedelta(days=90)).replace(tzinfo=None)
        assert len(session.exec(select(PaymentEvent)).all()) == 2
        unchanged = set_user_tier(session, telegram_id=1, tier="family", operator_telegram_id=1,
                                  now=later, duration_days=90)
        assert not unchanged.changed
        sub.telegram_charge_id = "paid-charge"
        session.commit()
        with pytest.raises(PaidSubscriptionConflict):
            set_user_tier(session, telegram_id=1, tier="family", operator_telegram_id=1,
                          now=later, duration_days=100)


@pytest.mark.parametrize("legacy", [False, True])
async def test_add_origin_is_preserved_when_applied_across_local_midnight(engine, legacy):
    with Session(engine) as session:
        proposals, _ = await propose_add(session, household_id=1,
            llm=FakeTextLLMClient(canned_add=([ProposedAddItem(
                name="Milk", explicit_user_expiry=True, shelf_life_days=3, confidence=1,
            )], 10)), user_text="milk lasts 3 days", today=TODAY, tz="America/New_York")
        payload = proposals[0].payload
        assert payload.purchased_on == TODAY
        if legacy:
            payload = AddPayload.model_validate_json(payload.model_dump_json(exclude={"purchased_on"}))
        item_id = apply_add(session, household_id=1, payload=payload, today=TODAY + timedelta(days=1))
        item = required(session.get(PantryItem, item_id))
        assert item.purchased_on == TODAY
        assert item.expires_on == TODAY + timedelta(days=3)
        assert (item.expires_on - item.purchased_on).days == item.shelf_life_days == 3


@pytest.mark.parametrize(("image_cost", "search_cost", "expected"), [
    (None, 900, 900), (100, None, 100), (None, None, None),
])
async def test_storage_cost_retains_known_charges_when_an_estimate_is_unknown(
    engine, image_cost, search_cost, expected,
):
    with Session(engine) as session:
        summary = await ingest(session, [parsed("Unknown frozen food", frozen=True)], cost=image_cost,
            search=FakeSearchClient(default=ShelfLifeSearchResult(
                days=120, confidence=1, cost_micros_usd=search_cost)))
        assert summary.cost_micros_usd == expected
        assert required(session.get(Receipt, summary.receipt_id)).llm_cost_micros_usd == expected
