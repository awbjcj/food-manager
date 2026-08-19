"""Fail-closed Telegram Mini App init-data validation."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl


class MiniAppAuthError(ValueError):
    """Raised when Telegram Mini App identity cannot be trusted."""


@dataclass(frozen=True)
class MiniAppIdentity:
    telegram_id: int
    first_name: str
    last_name: str
    language_code: str | None

    @property
    def display_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part)


def validate_init_data(
    raw: str,
    *,
    bot_token: str,
    now: datetime | None = None,
    max_age: timedelta = timedelta(minutes=10),
) -> MiniAppIdentity:
    """Validate signed ``Telegram.WebApp.initData`` and return its user."""

    if not raw:
        raise MiniAppAuthError("missing Telegram init data")
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    values = dict(pairs)
    if len(values) != len(pairs):
        raise MiniAppAuthError("duplicate init data fields")
    received_hash = values.pop("hash", None)
    if not received_hash:
        raise MiniAppAuthError("missing init data hash")

    check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(
        secret, check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(received_hash, expected_hash):
        raise MiniAppAuthError("invalid init data signature")

    try:
        auth_time = datetime.fromtimestamp(int(values["auth_date"]), UTC)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise MiniAppAuthError("invalid auth date") from exc
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    if auth_time > moment + timedelta(seconds=30) or moment - auth_time > max_age:
        raise MiniAppAuthError("expired init data")

    try:
        user = json.loads(values["user"])
        telegram_id = user["id"]
        if not isinstance(telegram_id, int):
            raise TypeError("user id must be an integer")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise MiniAppAuthError("invalid user data") from exc
    return MiniAppIdentity(
        telegram_id=telegram_id,
        first_name=str(user.get("first_name") or ""),
        last_name=str(user.get("last_name") or ""),
        language_code=(
            str(user["language_code"]) if user.get("language_code") else None
        ),
    )
