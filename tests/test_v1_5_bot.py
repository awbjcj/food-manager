from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

import app.bot as bot_mod
from app import handler_support
from app.client_set import PerUserClients
from app.correction_service import CorrectPayload, correct_payload_to_json
from app.llm import CorrectionDiff, ProposedAddItem
from app.models import Household, PantryItem, PendingCorrection, User
from app.pending_service import create_pending
from tests.fakes import FakeTextLLMClient


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    def make():
        return Session(engine)

    with make() as db:
        household = Household(created_at=datetime.now(UTC))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id is not None
        db.add(User(telegram_id=1, chat_id=99, household_id=household.id,
                    created_at=datetime.now(UTC)))
        db.commit()
    return make


def _msg(text: str):
    msg = MagicMock()
    msg.from_user = MagicMock(id=1)
    msg.chat = MagicMock(id=99, type="private")
    msg.text = text
    msg.answer = AsyncMock()
    return msg


def _reply_msg(text: str, *, reply_to_text: str):
    msg = _msg(text)
    msg.reply_to_message = MagicMock()
    msg.reply_to_message.text = reply_to_text
    return msg


def _cb(data: str):
    cb = MagicMock()
    cb.from_user = MagicMock(id=1)
    cb.data = data
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    return cb


def _item(session_factory) -> int:
    with session_factory() as db:
        item = PantryItem(
            household_id=1,
            raw_name="Milk",
            normalized_name="milk",
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
            created_at=datetime.now(UTC),
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        assert item.id is not None
        return item.id


def _pending_correct(session_factory) -> tuple[int, int]:
    item_id = _item(session_factory)
    payload = CorrectPayload(
        diff={
            "name": {"old": "Milk", "new": "Heavy Cream"},
            "category": None,
            "expires_on": None,
            "shelf_life_days": None,
        },
        cache_action="move",
        rationale="x",
        confidence=0.9,
    )
    with session_factory() as db:
        pending = create_pending(
            db,
            household_id=1,
            action_type="correct",
            item_id=item_id,
            proposed_json=correct_payload_to_json(payload),
            snapshot_json=None,
            cost_micros_usd=100,
            chat_id=99,
            now=datetime.now(UTC),
        )
        assert pending.id is not None
        return pending.id, item_id


@pytest.mark.asyncio
async def test_handle_correct_creates_pending_and_sends_diff(session_factory, monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    item_id = _item(session_factory)
    fake = FakeTextLLMClient(canned_correct=(
        CorrectionDiff(
            name="Heavy Cream",
            cache_action="move",
            rationale="x",
            confidence=0.9,
        ),
        150,
    ))
    msg = _msg(f"/correct {item_id} actually heavy cream")
    msg.answer.return_value = MagicMock(message_id=4242)

    await bot_mod.handle_correct(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 5, 27, tzinfo=UTC),
        clients=PerUserClients.for_tests(text=fake),
    )

    assert "Proposed correction" in msg.answer.await_args.args[0]
    assert "Heavy Cream" in msg.answer.await_args.args[0]
    assert "reply_markup" in msg.answer.await_args.kwargs
    with session_factory() as db:
        rows = list(db.exec(select(PendingCorrection)).all())
        assert len(rows) == 1
        assert rows[0].action_type == "correct"
        assert rows[0].item_id == item_id
        assert rows[0].message_id == 4242
        assert rows[0].llm_cost_micros_usd == 150
        assert '"raw_name": "Milk"' in rows[0].original_snapshot_json


@pytest.mark.asyncio
async def test_handle_correct_null_diff_does_not_create_pending(session_factory, monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    item_id = _item(session_factory)
    fake = FakeTextLLMClient(canned_correct=(
        CorrectionDiff(cache_action="leave", rationale="no change", confidence=0.5),
        100,
    ))
    msg = _msg(f"/correct {item_id} looks fine")

    await bot_mod.handle_correct(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 5, 27, tzinfo=UTC),
        clients=PerUserClients.for_tests(text=fake),
    )

    msg.answer.assert_awaited_with("no changes detected")
    with session_factory() as db:
        assert list(db.exec(select(PendingCorrection)).all()) == []


@pytest.mark.asyncio
async def test_correct_reply_routes_to_proposal(session_factory, monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    item_id = _item(session_factory)
    fake = FakeTextLLMClient(canned_correct=(
        CorrectionDiff(
            name="Heavy Cream",
            cache_action="move",
            rationale="x",
            confidence=0.9,
        ),
        150,
    ))
    msg = _reply_msg(
        "actually heavy cream",
        reply_to_text=f"Reply with the correction [correct:#{item_id}]",
    )
    msg.answer.return_value = MagicMock(message_id=4242)

    await bot_mod.handle_correct_reply(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 5, 27, tzinfo=UTC),
        clients=PerUserClients.for_tests(text=fake),
    )

    assert fake.correct_calls[0]["user_text"] == "actually heavy cream"
    assert "Proposed correction" in msg.answer.await_args.args[0]
    with session_factory() as db:
        rows = list(db.exec(select(PendingCorrection)).all())
        assert len(rows) == 1
        assert rows[0].action_type == "correct"
        assert rows[0].item_id == item_id
        assert rows[0].message_id == 4242


@pytest.mark.asyncio
async def test_correct_reply_ignores_non_marked_reply(session_factory, monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    fake = FakeTextLLMClient()
    msg = _reply_msg("actually heavy cream", reply_to_text="ordinary bot response")

    await bot_mod.handle_correct_reply(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 5, 27, tzinfo=UTC),
        clients=PerUserClients.for_tests(text=fake),
    )

    msg.answer.assert_not_awaited()
    assert fake.correct_calls == []
    with session_factory() as db:
        assert list(db.exec(select(PendingCorrection)).all()) == []


@pytest.mark.asyncio
async def test_handle_add_sends_one_pending_message_per_item(session_factory, monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    fake = FakeTextLLMClient(canned_add=([
        ProposedAddItem(
            name="Oat Milk",
            category="beverage",
            explicit_user_expiry=True,
            shelf_life_days=10,
            estimated_shelf_life_days=10,
            confidence=0.88,
        ),
        ProposedAddItem(
            name="Basil",
            category="produce",
            explicit_user_expiry=False,
            estimated_shelf_life_days=7,
            confidence=0.7,
        ),
    ], 200))
    msg = _msg("/add oat milk 10d, basil")
    msg.answer.side_effect = [
        MagicMock(message_id=1000),  # progress ack
        MagicMock(message_id=1001),
        MagicMock(message_id=1002),
    ]

    await bot_mod.handle_add(
        msg,
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 5, 27, tzinfo=UTC),
        clients=PerUserClients.for_tests(text=fake),
    )

    assert msg.answer.await_count == 3
    with session_factory() as db:
        rows = list(db.exec(select(PendingCorrection)).all())
        assert len(rows) == 2
        assert {row.action_type for row in rows} == {"add"}
        assert {row.message_id for row in rows} == {1001, 1002}
        assert sum(row.llm_cost_micros_usd for row in rows) == 200


@pytest.mark.asyncio
async def test_apply_and_cancel_callbacks(session_factory, monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    pending_id, item_id = _pending_correct(session_factory)

    await bot_mod.handle_callback(
        _cb(f"apply:{pending_id}"),
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 5, 27, tzinfo=UTC),
    )

    with session_factory() as db:
        pending = db.get(PendingCorrection, pending_id)
        item = db.get(PantryItem, item_id)
        assert pending.status == "applied"
        assert item.raw_name == "Heavy Cream"

    pending_id, item_id = _pending_correct(session_factory)
    await bot_mod.handle_callback(
        _cb(f"cancel:{pending_id}"),
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 5, 27, tzinfo=UTC),
    )
    with session_factory() as db:
        assert db.get(PendingCorrection, pending_id).status == "cancelled"
        assert db.get(PantryItem, item_id).raw_name == "Milk"


@pytest.mark.asyncio
async def test_apply_expired_pending_refuses_mutation(session_factory, monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    pending_id, item_id = _pending_correct(session_factory)
    with session_factory() as db:
        pending = db.get(PendingCorrection, pending_id)
        pending.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.add(pending)
        db.commit()

    await bot_mod.handle_callback(
        _cb(f"apply:{pending_id}"),
        session_factory=session_factory,
        now_provider=lambda tz: datetime(2026, 5, 27, tzinfo=UTC),
    )

    with session_factory() as db:
        assert db.get(PendingCorrection, pending_id).status == "expired"
        assert db.get(PantryItem, item_id).raw_name == "Milk"

