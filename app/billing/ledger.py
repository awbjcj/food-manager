from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

from app.billing.entitlement import utc_naive
from app.models import PaymentEvent


def find_event(session: Session, *, charge_id: str) -> PaymentEvent | None:
    return session.exec(
        select(PaymentEvent).where(PaymentEvent.telegram_charge_id == charge_id)
    ).first()


def record_payment(
    session: Session,
    *,
    household_id: int,
    charge_id: str,
    kind: str,
    sku: str,
    stars: int,
    payer_telegram_id: int,
    payload_json: str,
    now: datetime,
) -> bool:
    """Stage one ledger row; the caller owns the transaction commit."""
    if find_event(session, charge_id=charge_id) is not None:
        return False
    session.add(
        PaymentEvent(
            household_id=household_id,
            telegram_charge_id=charge_id,
            kind=kind,
            sku=sku,
            stars=stars,
            payer_telegram_id=payer_telegram_id,
            payload_json=payload_json,
            created_at=utc_naive(now),
        )
    )
    session.flush()
    return True


def revenue_stars(session: Session, *, since: datetime) -> int:
    events = session.exec(
        select(PaymentEvent).where(PaymentEvent.created_at >= utc_naive(since))
    ).all()
    purchases = sum(e.stars for e in events if e.kind in {"subscription", "topup"})
    refunds = sum(e.stars for e in events if e.kind == "refund")
    return purchases - refunds
