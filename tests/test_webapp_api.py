from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlmodel import Session, SQLModel, create_engine

from app.models import Household, Subscription, User
from app.webapp import build_web_app
from tests.fakes import FakePaymentProvider

TOKEN = "123456:TEST_TOKEN"


def _auth(user_id: int) -> str:
    values = {
        "auth_date": str(int(datetime.now(UTC).timestamp())),
        "user": json.dumps(
            {"id": user_id, "first_name": "Alex", "last_name": "Chen"},
            separators=(",", ":"),
        ),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return f"tma {urlencode(values)}"


@pytest.fixture
def web_state(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'web.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)

    def sessions():
        return Session(engine)

    now = datetime.now(UTC).replace(tzinfo=None)
    with sessions() as session:
        household = Household(name="The Chen kitchen", created_at=now)
        session.add(household)
        session.commit()
        session.refresh(household)
        assert household.id is not None
        session.add(
            User(
                telegram_id=42,
                chat_id=42,
                household_id=household.id,
                role="owner",
                lang="en",
                tz="America/New_York",
                llm_provider="gemini",
                created_at=now,
            )
        )
        session.add(
            Subscription(
                household_id=household.id,
                tier="free",
                status="active",
                period_start=now,
                period_end=now + timedelta(days=30),
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    payments = FakePaymentProvider()
    app = build_web_app(
        session_factory=sessions,
        bot_token=TOKEN,
        payments=payments,
        billing_enabled=True,
        available_providers=("gemini", "openai"),
        bot_username="food_manager_bot",
        static_dir=Path(tmp_path / "missing-static"),
    )
    return sessions, payments, app


@pytest.mark.asyncio
async def test_account_api_uses_signed_identity_and_canonical_billing_data(web_state):
    _sessions, _payments, app = web_state
    async with TestClient(TestServer(app)) as client:
        response = await client.get(
            "/api/account", headers={"Authorization": _auth(42)}
        )
        assert response.status == 200
        body = await response.json()
        assert body["user"]["name"] == "Alex Chen"
        assert body["household"]["name"] == "The Chen kitchen"
        assert body["plan"]["tier"] == "free"
        assert body["plans"][1]["code"] == "family_monthly"


@pytest.mark.asyncio
async def test_account_update_validates_and_persists_preferences(web_state):
    sessions, _payments, app = web_state
    payload = {
        "householdName": "Chen family pantry",
        "digestHour": 9,
        "timeZone": "America/Chicago",
        "language": "fr",
        "provider": "openai",
    }
    async with TestClient(TestServer(app)) as client:
        response = await client.patch(
            "/api/account", headers={"Authorization": _auth(42)}, json=payload
        )
        assert response.status == 200
    with sessions() as session:
        user = session.get(User, 42)
        assert user is not None
        household = session.get(Household, user.household_id)
        assert household is not None
        assert household.name == "Chen family pantry"
        assert (user.digest_hour, user.tz, user.lang, user.llm_provider) == (
            9,
            "America/Chicago",
            "fr",
            "openai",
        )


@pytest.mark.asyncio
async def test_checkout_and_cancel_renewal_preserve_paid_period(web_state):
    sessions, payments, app = web_state
    async with TestClient(TestServer(app)) as client:
        checkout = await client.post(
            "/api/checkout",
            headers={"Authorization": _auth(42)},
            json={"sku": "family_monthly"},
        )
        assert checkout.status == 200
        assert (await checkout.json())["invoiceUrl"].endswith("family_monthly/1")
        assert payments.checkouts == [("family_monthly", 1)]

        with sessions() as session:
            sub = session.get(Subscription, 1)
            assert sub is not None
            sub.tier = "family"
            sub.telegram_charge_id = "charge-1"
            sub.payer_telegram_id = 42
            session.add(sub)
            session.commit()

        cancelled = await client.post(
            "/api/subscription/cancel", headers={"Authorization": _auth(42)}
        )
        assert cancelled.status == 200
    with sessions() as session:
        sub = session.get(Subscription, 1)
        assert sub is not None
        assert sub.tier == "family" and sub.status == "active"
        assert sub.cancel_at_period_end is True
        assert payments.cancellations == [(42, "charge-1")]


@pytest.mark.asyncio
async def test_api_rejects_missing_or_tampered_telegram_identity(web_state):
    _sessions, _payments, app = web_state
    async with TestClient(TestServer(app)) as client:
        missing = await client.get("/api/account")
        tampered = await client.get(
            "/api/account",
            headers={"Authorization": _auth(42).replace("Alex", "Mallory")},
        )
        assert missing.status == 401
        assert tampered.status == 401
