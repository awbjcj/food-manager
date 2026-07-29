from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.billing.entitlement import apply_subscription, apply_topup, effective_tier
from app.billing.ledger import find_event, record_payment
from app.billing.plans import SKUS
from app.models import Household, PaymentEvent, QuotaUsage, Subscription, User
from app.operator import auth
from app.operator.bot import handle_grant, handle_reconcile, handle_refund
from tests.fakes import FakePaymentProvider

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


@pytest.fixture
def factory(monkeypatch):
    monkeypatch.setattr(auth, "OPERATOR_IDS", frozenset({7}))
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        household = Household(created_at=NOW)
        db.add(household)
        db.commit()
        assert household.id is not None
        db.add(
            User(telegram_id=42, chat_id=42, household_id=household.id, created_at=NOW)
        )
        db.commit()
    return lambda: Session(engine)


def _message(text: str, sender: int = 7):
    msg = MagicMock()
    msg.from_user.id = sender
    msg.text = text
    msg.answer = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_grant_rejects_negative_quota(factory):
    msg = _message("/grant 1 -1 10")
    await handle_grant(msg, session_factory=factory, now_provider=lambda _tz: NOW)
    with factory() as db:
        assert db.exec(select(PaymentEvent)).all() == []


@pytest.mark.asyncio
async def test_grant_rejects_an_unknown_household(factory):
    msg = _message("/grant 999 1 1")
    await handle_grant(msg, session_factory=factory, now_provider=lambda _tz: NOW)
    with factory() as db:
        assert db.exec(select(PaymentEvent)).all() == []


@pytest.mark.asyncio
async def test_refund_retry_does_not_call_the_rail_twice(factory):
    with factory() as db:
        record_payment(
            db,
            household_id=1,
            charge_id="charge-1",
            kind="subscription",
            sku="family_monthly",
            stars=500,
            payer_telegram_id=42,
            payload_json="{}",
            now=NOW,
        )
        apply_subscription(
            db,
            household_id=1,
            sku=SKUS["family_monthly"],
            charge_id="charge-1",
            payer_telegram_id=42,
            expires_at=NOW + timedelta(days=30),
            now=NOW,
        )
        db.commit()
    payments = FakePaymentProvider()
    for _ in range(2):
        await handle_refund(
            _message("/refund charge-1"),
            session_factory=factory,
            now_provider=lambda _tz: NOW,
            payments=payments,
        )
    assert payments.refunds == [(42, "charge-1")]
    with factory() as db:
        assert find_event(db, charge_id="refund:charge-1") is not None
        assert effective_tier(db.get(Subscription, 1)) == "free"


@pytest.mark.asyncio
async def test_refunding_a_topup_does_not_cancel_a_family_subscription(factory):
    with factory() as db:
        apply_subscription(
            db,
            household_id=1,
            sku=SKUS["family_monthly"],
            charge_id="subscription",
            payer_telegram_id=42,
            expires_at=NOW + timedelta(days=30),
            now=NOW,
        )
        apply_topup(db, household_id=1, sku=SKUS["topup_receipts_50"], now=NOW)
        record_payment(
            db,
            household_id=1,
            charge_id="topup",
            kind="topup",
            sku="topup_receipts_50",
            stars=250,
            payer_telegram_id=42,
            payload_json="{}",
            now=NOW,
        )
        db.commit()
    await handle_refund(
        _message("/refund topup"),
        session_factory=factory,
        now_provider=lambda _tz: NOW,
        payments=FakePaymentProvider(),
    )
    with factory() as db:
        sub = db.get(Subscription, 1)
        assert effective_tier(sub) == "family"
        usage = db.get(QuotaUsage, (1, sub.period_start))
        assert usage.receipts_granted == 0


@pytest.mark.asyncio
async def test_reconcile_paginates_and_reports_amount_mismatch(factory):
    class PagedPayments(FakePaymentProvider):
        async def list_transactions(self, *, offset: int, limit: int):
            from app.billing.payment import LedgerRow

            rows = [LedgerRow(f"remote-{index}", 1, 42) for index in range(100)]
            rows.append(LedgerRow("local", 999, 42))
            return rows[offset : offset + limit]

    with factory() as db:
        record_payment(
            db,
            household_id=1,
            charge_id="local",
            kind="topup",
            sku="topup_actions_150",
            stars=250,
            payer_telegram_id=42,
            payload_json="{}",
            now=NOW,
        )
        db.commit()
    msg = _message("/reconcile")
    await handle_reconcile(msg, session_factory=factory, payments=PagedPayments())
    text = msg.answer.await_args.args[0]
    assert "amount" in text.lower()
    assert "remote-99" in text
