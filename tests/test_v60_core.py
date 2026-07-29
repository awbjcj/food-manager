from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.billing import meter
from app.billing.entitlement import (
    get_or_create_subscription,
    get_or_create_usage,
    roll_period_if_due,
)
from app.billing.plans import TIERS, units_for
from app.billing.render import render_quota
from app.handler_support import resolve_authorization
from app.household_service import provision_solo_household
from app.invite_service import HouseholdFull, create_invite, redeem_invite
from app.models import Household, Subscription, User

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _household(db: Session) -> int:
    household = Household(created_at=NOW)
    db.add(household)
    db.commit()
    assert household.id is not None
    return household.id


def test_plan_catalog_prices_receipts_separately():
    assert TIERS["free"].receipts == 5
    assert TIERS["family"].receipts == 100
    assert units_for("receipt", "anthropic") == 0
    assert units_for("cook", "anthropic") == 30


def test_household_provisioning_always_creates_subscription(db):
    user = User(telegram_id=1, chat_id=1, household_id=0, created_at=NOW)
    household = provision_solo_household(db, user, created_at=NOW)
    assert household.id is not None
    assert db.get(Subscription, household.id) is not None


def test_paid_subscription_expires_without_a_renewal(db):
    household_id = _household(db)
    sub = get_or_create_subscription(db, household_id=household_id, now=NOW)
    sub.tier = "family"
    sub.telegram_charge_id = "original"
    db.add(sub)
    db.commit()

    rolled = roll_period_if_due(db, sub=sub, now=NOW + timedelta(days=31))

    assert rolled.status == "expired"
    assert rolled.period_start == (NOW + timedelta(days=30)).replace(tzinfo=None)


def test_expired_paid_subscription_catches_up_after_long_inactivity(db):
    household_id = _household(db)
    sub = get_or_create_subscription(db, household_id=household_id, now=NOW)
    sub.tier = "family"
    sub.telegram_charge_id = "original"
    db.add(sub)
    db.commit()

    rolled = roll_period_if_due(db, sub=sub, now=NOW + timedelta(days=95))

    assert rolled.status == "expired"
    assert rolled.period_start == (NOW + timedelta(days=90)).replace(tzinfo=None)
    assert rolled.period_end == (NOW + timedelta(days=120)).replace(tzinfo=None)


def test_quota_card_is_localized_and_includes_raw_operation_breakdown():
    card = meter.QuotaSnapshot(
        receipts_used=2,
        receipts_limit=5,
        actions_used=13,
        actions_limit=30,
        per_op={"cook": 1, "plan": 0, "edit": 2, "chat": 1, "search": 0},
        period_end=NOW.replace(tzinfo=None),
        tier="free",
    )

    rendered = render_quota(card, days_left=7, lang="fr")

    assert "Quota Gratuit" in rendered
    assert "Reçus" in rendered
    assert "Cuisine" in rendered
    assert "1 × 10 = 10" in rendered
    assert "/buy" in rendered


def test_meter_rejects_negative_cost(db):
    household_id = _household(db)
    with pytest.raises(ValueError, match="non-negative"):
        meter.commit(
            db,
            household_id=household_id,
            op="chat",
            provider="gemini",
            cost_micros=-1,
            now=NOW,
        )


def test_meter_blocks_an_operation_that_would_overshoot(db, monkeypatch):
    monkeypatch.setattr(meter, "BILLING_ENABLED", True)
    household_id = _household(db)
    sub = get_or_create_subscription(db, household_id=household_id, now=NOW)
    usage = get_or_create_usage(
        db, household_id=household_id, period_start=sub.period_start
    )
    usage.actions_used = 25
    db.add(usage)
    db.commit()
    decision = meter.admit(
        db, household_id=household_id, op="cook", provider="gemini", now=NOW
    )
    assert decision.allowed is False
    assert decision.degrade is True


def test_open_registration_never_resurrects_a_banned_user(db):
    household_id = _household(db)
    db.add(
        User(
            telegram_id=42,
            chat_id=42,
            household_id=household_id,
            banned=True,
            created_at=NOW,
        )
    )
    db.commit()
    status = resolve_authorization(
        db,
        allowed_user_id=7,
        telegram_user_id=42,
        open_registration=True,
    )
    assert status.allowed is False


def test_invite_rechecks_the_seat_cap_at_redemption(db):
    household_id = _household(db)
    sub = get_or_create_subscription(db, household_id=household_id, now=NOW)
    sub.seat_cap = 2
    db.add(sub)
    db.add(User(telegram_id=1, chat_id=1, household_id=household_id, created_at=NOW))
    db.commit()
    invite = create_invite(
        db, household_id=household_id, created_by=1, now=NOW, max_uses=None
    )
    redeem_invite(
        db,
        token=invite.token,
        telegram_user_id=2,
        chat_id=2,
        now=NOW,
        tz="UTC",
        digest_hour=8,
        llm_provider="gemini",
    )
    with pytest.raises(HouseholdFull):
        redeem_invite(
            db,
            token=invite.token,
            telegram_user_id=3,
            chat_id=3,
            now=NOW,
            tz="UTC",
            digest_hour=8,
            llm_provider="gemini",
        )
