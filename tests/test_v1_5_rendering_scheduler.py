from datetime import date, datetime, timedelta, timezone

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import Session, SQLModel, create_engine, select

from app.commands import CommandError, parse_callback
from app.correction_service import AddPayload, CorrectPayload
from app.models import PendingCorrection, User
from app.pantry_service import Stats, TextLLMCost
from app.pending_service import create_pending
from app.renderer import (
    build_apply_cancel_keyboard,
    render_add_diff,
    render_applied_add,
    render_applied_correction,
    render_correction_diff,
    render_stats,
    render_terminal_state,
)
from app.scheduler import _sweep_job, register_sweep_expired_pendings


def test_parse_callback_apply_cancel_and_bad_id():
    assert parse_callback("apply:123").verb == "apply"
    assert parse_callback("cancel:7").item_id == 7
    with pytest.raises(CommandError):
        parse_callback("apply:notanint")


def test_render_correction_add_and_terminal_states():
    correct = CorrectPayload(
        diff={
            "name": {"old": "Milk", "new": "Heavy Cream"},
            "category": None,
            "expires_on": {"old": "2026-06-02", "new": "2026-06-05"},
            "shelf_life_days": {"old": 7, "new": 10},
        },
        cache_action="move",
        rationale="user clarified identity",
        confidence=0.92,
        back_computed_days=True,
    )
    text = render_correction_diff(
        pending_id=123,
        payload=correct,
        item_id=42,
        item_raw_name="Milk",
    )
    assert "#42" in text
    assert "name: Milk -> Heavy Cream" in text
    assert "back-computed" in text
    assert "move" in text
    assert "apply:123" in {
        button.callback_data
        for row in build_apply_cancel_keyboard(pending_id=123)
        for button in row
    }
    assert "Heavy Cream" in render_applied_correction(item_id=42, payload=correct)

    add = AddPayload(
        name="Oat Milk",
        category="beverage",
        qty=0.5,
        unit="gal",
        shelf_life_days=10,
        expires_on=date(2026, 6, 6),
        shelf_life_source="user_correction",
        ingest_shelf_life_source="manual_user_hint",
        explicit_user_expiry=True,
        estimated_shelf_life_days=10,
        confidence=0.88,
    )
    add_text = render_add_diff(pending_id=1, payload=add)
    assert "Oat Milk" in add_text
    assert "0.5 gal" in add_text
    assert "user_correction" in add_text
    assert "#99" in render_applied_add(item_id=99, payload=add)
    assert "Cancelled" in render_terminal_state("cancelled")
    assert "expired" in render_terminal_state("expired")
    assert "stale" in render_terminal_state("stale")


def test_render_stats_includes_text_llm_buckets():
    stats = Stats(
        receipt_count=2,
        tracked_item_count=10,
        removed_item_count=0,
        cache_hit_percent=50.0,
        total_cost_micros_usd=180_000,
        avg_cost_micros_usd=90_000,
        unknown_cost_receipt_count=0,
        waste_rate_percent=0.0,
        text_llm=TextLLMCost(
            correction_proposal_count=3,
            correction_cost_micros=320,
            correction_unknown_cost_count=1,
            add_proposal_count=2,
            add_cost_micros=190,
            add_unknown_cost_count=0,
        ),
    )
    text = render_stats(stats)
    assert "Corrections" in text
    assert "Adds" in text
    assert "1 unknown" in text


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    def make():
        return Session(engine)

    with make() as db:
        db.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        db.commit()
    return make


def test_register_sweep_expired_pendings_and_job_marks_rows(session_factory):
    scheduler = AsyncIOScheduler()
    register_sweep_expired_pendings(scheduler, session_factory=session_factory)
    job = scheduler.get_job("sweep_expired_pendings")
    assert job is not None
    assert "*/5" in str(job.trigger)

    with session_factory() as db:
        create_pending(
            db,
            user_id=1,
            action_type="correct",
            item_id=1,
            proposed_json="{}",
            snapshot_json=None,
            cost_micros_usd=None,
            chat_id=1,
            now=datetime.now(timezone.utc) - timedelta(minutes=20),
        )

    _sweep_job(session_factory)

    with session_factory() as db:
        row = db.exec(select(PendingCorrection)).first()
        assert row.status == "expired"
