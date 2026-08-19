from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest

from app.webapp_auth import MiniAppAuthError, validate_init_data

TOKEN = "123456:TEST_TOKEN"
NOW = datetime(2026, 8, 18, 16, 0, tzinfo=UTC)


def _signed(*, auth_date: datetime = NOW, user_id: int = 42) -> str:
    values = {
        "auth_date": str(int(auth_date.timestamp())),
        "query_id": "AAE-test",
        "user": json.dumps(
            {
                "id": user_id,
                "first_name": "Alex",
                "last_name": "Chen",
                "language_code": "en",
            },
            separators=(",", ":"),
        ),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_validate_init_data_returns_signed_telegram_identity():
    identity = validate_init_data(_signed(), bot_token=TOKEN, now=NOW)
    assert identity.telegram_id == 42
    assert identity.display_name == "Alex Chen"
    assert identity.language_code == "en"


def test_validate_init_data_rejects_tampering():
    raw = _signed().replace("Alex", "Mallory")
    with pytest.raises(MiniAppAuthError, match="signature"):
        validate_init_data(raw, bot_token=TOKEN, now=NOW)


def test_validate_init_data_rejects_stale_and_future_sessions():
    with pytest.raises(MiniAppAuthError, match="expired"):
        validate_init_data(
            _signed(auth_date=NOW - timedelta(minutes=11)),
            bot_token=TOKEN,
            now=NOW,
        )
    with pytest.raises(MiniAppAuthError, match="expired"):
        validate_init_data(
            _signed(auth_date=NOW + timedelta(minutes=1)),
            bot_token=TOKEN,
            now=NOW,
        )


def test_validate_init_data_rejects_duplicate_fields():
    with pytest.raises(MiniAppAuthError, match="duplicate"):
        validate_init_data(
            f"{_signed()}&auth_date={int(NOW.timestamp())}",
            bot_token=TOKEN,
            now=NOW,
        )
