from datetime import date, datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.cache import get_cached, put_cached
from app.correction_service import (
    AddPayload,
    CorrectPayload,
    NullDiff,
    ProposeCorrectError,
    add_payload_from_json,
    add_payload_to_json,
    apply_add,
    apply_correct,
    item_snapshot_to_json,
    propose_add,
    propose_correct,
)
from app.llm import CorrectionDiff, ProposedAddItem
from app.models import PantryItem, PendingCorrection, User
from app.pantry_service import compute_stats, mark_eaten
from app.pending_service import (
    PENDING_TTL_MINUTES,
    create_pending,
    expire_for_item,
    load_pending,
    mark_applied,
    sweep_expired,
)
from tests.fakes import FakeTextLLMClient


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        db.add(User(telegram_id=2, chat_id=2, created_at=datetime.now(timezone.utc)))
        db.commit()
        yield db


def _item(session: Session, name: str = "Milk", norm: str = "milk") -> PantryItem:
    item = PantryItem(
        user_id=1,
        raw_name=name,
        normalized_name=norm,
        category="dairy",
        qty=1.0,
        unit="gal",
        purchased_on=date(2026, 5, 26),
        shelf_life_days=7,
        shelf_life_source="llm",
        ingest_shelf_life_source="llm",
        expires_on=date(2026, 6, 2),
        status="active",
        created_via="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def test_pending_create_load_terminal_expire_and_sweep(session):
    now = datetime(2026, 5, 27, 12, tzinfo=timezone.utc)
    pending = create_pending(
        session,
        user_id=1,
        action_type="correct",
        item_id=42,
        proposed_json="{}",
        snapshot_json='{"id":42}',
        cost_micros_usd=350,
        chat_id=1,
        now=now,
    )

    stored_now = now.replace(tzinfo=None)
    assert pending.expires_at == stored_now + timedelta(minutes=PENDING_TTL_MINUTES)
    assert pending.id is not None
    assert load_pending(session, user_id=2, pending_id=pending.id) is None
    found = load_pending(session, user_id=1, pending_id=pending.id)
    assert found is not None
    assert found.id == pending.id

    mark_applied(session, pending=pending)
    session.commit()
    session.refresh(pending)
    assert pending.status == "applied"

    old = create_pending(
        session,
        user_id=1,
        action_type="correct",
        item_id=42,
        proposed_json="{}",
        snapshot_json=None,
        cost_micros_usd=None,
        chat_id=1,
        now=now - timedelta(minutes=20),
    )
    fresh = create_pending(
        session,
        user_id=1,
        action_type="correct",
        item_id=42,
        proposed_json="{}",
        snapshot_json=None,
        cost_micros_usd=None,
        chat_id=1,
        now=now,
    )
    assert expire_for_item(session, user_id=1, item_id=42, exclude_pending_id=fresh.id) == 1
    session.commit()
    session.refresh(old)
    session.refresh(fresh)
    assert old.status == "stale"
    assert fresh.status == "pending"

    assert sweep_expired(session, now=now + timedelta(minutes=11)) == 1
    session.refresh(fresh)
    assert fresh.status == "expired"


@pytest.mark.asyncio
async def test_propose_correct_back_computes_days_and_snapshot(session):
    item = _item(session)
    fake = FakeTextLLMClient(canned_correct=(
        CorrectionDiff(
            expires_on=date(2026, 6, 5),
            cache_action="leave",
            rationale="date update",
            confidence=0.8,
        ),
        100,
    ))

    payload, cost = await propose_correct(
        session,
        llm=fake,
        user_id=1,
        item=item,
        user_text="expires June 5",
        today=date(2026, 5, 27),
    )

    assert payload.back_computed_days is True
    assert payload.diff["shelf_life_days"] is not None
    assert payload.diff["shelf_life_days"]["new"] == 10
    assert payload.diff["expires_on"] is not None
    assert payload.diff["expires_on"]["new"] == "2026-06-05"
    assert cost == 100
    assert '"raw_name": "Milk"' in item_snapshot_to_json(item)


@pytest.mark.asyncio
async def test_propose_correct_rejects_null_and_out_of_range(session):
    item = _item(session)
    null_fake = FakeTextLLMClient(canned_correct=(
        CorrectionDiff(cache_action="leave", rationale="no change", confidence=0.5),
        100,
    ))
    with pytest.raises(NullDiff):
        await propose_correct(
            session,
            llm=null_fake,
            user_id=1,
            item=item,
            user_text="looks fine",
            today=date(2026, 5, 27),
        )

    range_fake = FakeTextLLMClient(canned_correct=(
        CorrectionDiff(
            expires_on=date(2026, 5, 25),
            cache_action="leave",
            rationale="bad date",
            confidence=0.8,
        ),
        100,
    ))
    with pytest.raises(ProposeCorrectError):
        await propose_correct(
            session,
            llm=range_fake,
            user_id=1,
            item=item,
            user_text="expired yesterday",
            today=date(2026, 5, 27),
        )


def test_apply_correct_cache_actions(session):
    item = _item(session)
    put_cached(session, 1, "milk", days=7, category="dairy", confidence=0.9, source="llm")
    payload = CorrectPayload(
        diff={
            "name": {"old": "Milk", "new": "Heavy Cream"},
            "category": None,
            "expires_on": {"old": "2026-06-02", "new": "2026-06-05"},
            "shelf_life_days": {"old": 7, "new": 10},
        },
        cache_action="move",
        rationale="x",
        confidence=0.9,
    )

    apply_correct(session, user_id=1, item=item, payload=payload)
    session.commit()
    session.refresh(item)

    assert item.normalized_name == "heavy cream"
    assert item.shelf_life_source == "user_correction"
    assert get_cached(session, 1, "milk") is None
    heavy_cream_cache = get_cached(session, 1, "heavy cream")
    assert heavy_cream_cache is not None
    assert heavy_cream_cache.days == 10

    category_only = CorrectPayload(
        diff={
            "name": None,
            "category": {"old": "dairy", "new": "beverage"},
            "expires_on": None,
            "shelf_life_days": None,
        },
        cache_action="leave",
        rationale="category fix",
        confidence=0.9,
    )
    apply_correct(session, user_id=1, item=item, payload=category_only)
    session.commit()
    updated_cache = get_cached(session, 1, "heavy cream")
    assert updated_cache is not None
    assert updated_cache.category == "beverage"


@pytest.mark.asyncio
async def test_propose_add_payload_roundtrip_and_apply(session):
    put_cached(session, 1, "oat milk", days=14, category="beverage", confidence=0.9)
    fake = FakeTextLLMClient(canned_add=([
        ProposedAddItem(
            name="Oat Milk",
            category="beverage",
            explicit_user_expiry=False,
            estimated_shelf_life_days=8,
            confidence=0.7,
        ),
        ProposedAddItem(
            name="Star Fruit",
            category="produce",
            explicit_user_expiry=False,
            estimated_shelf_life_days=6,
            confidence=0.7,
        ),
    ], 201))

    proposals, total = await propose_add(
        session,
        llm=fake,
        user_id=1,
        user_text="oat milk, star fruit",
        today=date(2026, 5, 27),
        tz="America/Detroit",
    )

    assert total == 201
    assert [p.cost_share for p in proposals] == [101, 100]
    assert proposals[0].payload.shelf_life_source == "cache"
    assert proposals[0].payload.shelf_life_days == 14
    assert proposals[1].payload.shelf_life_source == "llm"

    blob = add_payload_to_json(proposals[0].payload)
    assert add_payload_from_json(blob).name == "Oat Milk"
    new_id = apply_add(
        session,
        user_id=1,
        payload=AddPayload(
            name="Basil",
            category="produce",
            shelf_life_days=7,
            expires_on=date(2026, 6, 3),
            shelf_life_source="user_correction",
            ingest_shelf_life_source="manual_user_hint",
            explicit_user_expiry=True,
            estimated_shelf_life_days=7,
            confidence=0.8,
        ),
        today=date(2026, 5, 27),
    )
    session.commit()
    assert session.get(PantryItem, new_id).normalized_name == "basil"
    basil_cache = get_cached(session, 1, "basil")
    assert basil_cache is not None
    assert basil_cache.source == "user_correction"


def test_pantry_mutation_marks_pending_stale_and_stats_include_text_cost(session):
    item = _item(session)
    pending = create_pending(
        session,
        user_id=1,
        action_type="correct",
        item_id=item.id,
        proposed_json="{}",
        snapshot_json=None,
        cost_micros_usd=300,
        chat_id=1,
        now=datetime.now(timezone.utc),
    )
    create_pending(
        session,
        user_id=1,
        action_type="add",
        item_id=None,
        proposed_json="{}",
        snapshot_json=None,
        cost_micros_usd=None,
        chat_id=1,
        now=datetime.now(timezone.utc),
    )

    assert item.id is not None
    mark_eaten(session, user_id=1, item_id=item.id, today=date(2026, 5, 27))
    session.refresh(pending)
    assert pending.status == "stale"

    stats = compute_stats(session, user_id=1, now=datetime.now(timezone.utc))
    assert stats.text_llm.correction_proposal_count == 1
    assert stats.text_llm.correction_cost_micros == 300
    assert stats.text_llm.add_proposal_count == 1
    assert stats.text_llm.add_unknown_cost_count == 1
