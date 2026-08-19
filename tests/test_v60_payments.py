from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.billing.entitlement import get_or_create_usage
from app.billing.ledger import find_event, revenue_stars
from app.billing.payment import StarsPaymentProvider, invoice_payload
from app.billing.plans import SKUS
from app.handlers.billing import handle_pre_checkout, handle_successful_payment
from app.models import Household, PaymentEvent, Subscription, User

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


@pytest.fixture
def factory():
    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as db:
        SQLModel.metadata.create_all(engine)
        household = Household(created_at=NOW)
        db.add(household)
        db.commit()
        assert household.id is not None
        db.add(
            User(telegram_id=42, chat_id=42, household_id=household.id, created_at=NOW)
        )
        db.add(
            User(
                telegram_id=99,
                chat_id=99,
                household_id=household.id + 1,
                created_at=NOW,
            )
        )
        db.commit()
    return lambda: Session(engine)


def _query(payload: str, *, user_id: int = 42, amount: int = 500):
    query = MagicMock()
    query.from_user.id = user_id
    query.invoice_payload = payload
    query.currency = "XTR"
    query.total_amount = amount
    query.answer = AsyncMock()
    return query


def _message(household_id: int, *, charge: str = "chg-1", sku: str = "family_monthly"):
    payment = MagicMock()
    payment.currency = "XTR"
    payment.total_amount = SKUS[sku].stars
    payment.invoice_payload = invoice_payload(sku, household_id)
    payment.telegram_payment_charge_id = charge
    payment.is_recurring = sku == "family_monthly"
    payment.is_first_recurring = bool(payment.is_recurring)
    payment.subscription_expiration_date = (
        int((NOW + timedelta(days=30)).timestamp()) if payment.is_recurring else None
    )
    msg = MagicMock()
    msg.from_user.id = 42
    msg.successful_payment = payment
    msg.answer = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_checkout_rejects_a_payer_from_another_household(factory):
    query = _query(invoice_payload("family_monthly", 1), user_id=99)
    await handle_pre_checkout(query, session_factory=factory)
    assert query.answer.await_args.kwargs["ok"] is False


@pytest.mark.asyncio
async def test_checkout_rejects_wrong_amount(factory):
    query = _query(invoice_payload("family_monthly", 1), amount=1)
    await handle_pre_checkout(query, session_factory=factory)
    assert query.answer.await_args.kwargs["ok"] is False


@pytest.mark.asyncio
async def test_successful_payment_is_atomic_and_idempotent(factory):
    msg = _message(1)
    await handle_successful_payment(
        msg, session_factory=factory, now_provider=lambda _tz: NOW
    )
    await handle_successful_payment(
        msg, session_factory=factory, now_provider=lambda _tz: NOW
    )
    with factory() as db:
        assert len(db.exec(select(PaymentEvent)).all()) == 1
        assert db.get(Subscription, 1).tier == "family"


@pytest.mark.asyncio
async def test_replayed_topup_is_not_double_granted(factory):
    msg = _message(1, charge="topup-1", sku="topup_receipts_50")
    await handle_successful_payment(
        msg, session_factory=factory, now_provider=lambda _tz: NOW
    )
    await handle_successful_payment(
        msg, session_factory=factory, now_provider=lambda _tz: NOW
    )
    with factory() as db:
        sub = db.get(Subscription, 1)
        usage = get_or_create_usage(db, household_id=1, period_start=sub.period_start)
        assert usage.receipts_granted == 50


@pytest.mark.asyncio
async def test_expired_subscription_confirmation_does_not_grant_entitlement(factory):
    msg = _message(1, charge="expired")
    msg.successful_payment.subscription_expiration_date = int(
        (NOW - timedelta(seconds=1)).timestamp()
    )

    await handle_successful_payment(
        msg, session_factory=factory, now_provider=lambda _tz: NOW
    )

    with factory() as db:
        assert find_event(db, charge_id="expired") is None
        assert db.get(Subscription, 1) is None


def test_refunds_subtract_from_revenue(factory):
    with factory() as db:
        db.add(
            PaymentEvent(
                household_id=1,
                telegram_charge_id="buy",
                kind="subscription",
                sku="family_monthly",
                stars=500,
                payer_telegram_id=42,
                payload_json="{}",
                created_at=NOW,
            )
        )
        db.add(
            PaymentEvent(
                household_id=1,
                telegram_charge_id="refund:buy",
                kind="refund",
                sku="family_monthly",
                stars=500,
                payer_telegram_id=42,
                payload_json="{}",
                created_at=NOW,
            )
        )
        db.commit()
        assert revenue_stars(db, since=NOW - timedelta(days=1)) == 0
        assert find_event(db, charge_id="refund:buy") is not None


@pytest.mark.asyncio
async def test_stars_provider_uses_the_documented_invoice_shape():
    bot = MagicMock()
    bot.create_invoice_link = AsyncMock(return_value="https://t.me/i/1")
    provider = StarsPaymentProvider(bot)
    await provider.create_checkout(sku=SKUS["family_monthly"], household_id=1)
    kwargs = bot.create_invoice_link.await_args.kwargs
    assert kwargs["currency"] == "XTR"
    assert "provider_token" not in kwargs
    assert kwargs["subscription_period"] == 2_592_000
    assert len(kwargs["prices"]) == 1
