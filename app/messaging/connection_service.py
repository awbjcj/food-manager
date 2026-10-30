"""Deployment-owned configuration with write-only credentials."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from sqlmodel import Session, select

from app.messaging.contracts import ErrorCode, MessagingError, MessagingProvider
from app.models import MessagingConnection
from app.pending_service import utc_naive


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    provider: MessagingProvider
    label: str = Field(min_length=1, max_length=80)
    settings: dict = Field(default_factory=dict)
    credentials: dict[str, SecretStr] = Field(default_factory=dict, repr=False)

    @model_validator(mode="after")
    def validate_provider(self):
        required = {
            "telegram": {"botToken"},
            "slack": {"clientId", "clientSecret", "signingSecret"},
            "whatsapp": {"accessToken", "appSecret", "verifyToken"},
        }[self.provider]
        allowed_credentials = required | (
            {"botToken"} if self.provider == "slack" else set()
        )
        if set(self.credentials) - allowed_credentials:
            raise ValueError("Unknown credential field")
        if any(
            not self.credentials.get(k)
            or not self.credentials[k].get_secret_value().strip()
            for k in required
        ):
            raise ValueError("Required provider credentials are missing")
        if any(len(v.get_secret_value()) > 8192 for v in self.credentials.values()):
            raise ValueError("Credential exceeds allowed length")
        schema = {
            "telegram": TelegramConfig,
            "slack": SlackConfig,
            "whatsapp": WhatsAppConfig,
        }[self.provider]
        self.settings = schema.model_validate(self.settings).model_dump()
        return self


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    bootstrapUserId: str = Field(min_length=1, max_length=128)


class TelegramConfig(Config):
    bootstrapUserId: str = Field(pattern=r"^[1-9]\d{0,19}$")
    mode: Literal["polling"] = "polling"


class SlackConfig(Config):
    bootstrapUserId: str = Field(pattern=r"^[UW][A-Z0-9]+$")
    mode: Literal["http"] = "http"


class Template(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str = Field(pattern=r"^[a-z]{2,3}(_[A-Z]{2})?$")
    name: str = Field(pattern=r"^[a-z0-9_]{1,512}$")


class WhatsAppConfig(Config):
    bootstrapUserId: str = Field(pattern=r"^[1-9]\d{6,14}$")
    businessAccountId: str = Field(pattern=r"^\d+$")
    phoneNumberId: str = Field(pattern=r"^\d+$")
    apiVersion: str = Field(pattern=r"^v\d+\.\d+$")
    digestTemplates: list[Template] = Field(default_factory=list, max_length=20)


class ConnectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    label: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None
    settings: dict | None = None
    credentials: dict[str, SecretStr] | None = Field(default=None, repr=False)


class SecretStore:
    def __init__(self, key: str):
        self._fernet = Fernet(key.encode()) if key else None

    def encrypt(self, credentials: dict[str, str]) -> str:
        if self._fernet is None:
            raise MessagingError(
                ErrorCode.AUTH_FAILED, "Configure the connection encryption key"
            )
        return self._fernet.encrypt(json.dumps(credentials).encode()).decode()

    def decrypt(self, value: str | None) -> dict[str, str]:
        if self._fernet is None or value is None:
            raise MessagingError(
                ErrorCode.AUTH_FAILED, "Credentials are not configured"
            )
        try:
            return json.loads(self._fernet.decrypt(value.encode()))
        except (InvalidToken, ValueError):
            raise MessagingError(
                ErrorCode.AUTH_FAILED, "Credentials could not be decrypted"
            ) from None


def public_connection(
    row: MessagingConnection, *, environment_credentials_present: bool = False
) -> dict:
    capabilities = ["images", "interactive_actions"]
    if row.provider in {"telegram", "slack"}:
        capabilities.append("message_editing")
    return {
        "id": row.id,
        "provider": row.provider,
        "label": row.label,
        "enabled": row.enabled,
        "state": row.state,
        "externalAccount": row.external_account,
        "revision": row.revision,
        "credentialsPresent": bool(row.secret_ciphertext)
        or (row.credential_source == "environment" and environment_credentials_present),
        "credentialSource": row.credential_source,
        "lastCheckedAt": row.last_checked_at.isoformat()
        if row.last_checked_at
        else None,
        "capabilities": capabilities,
        "lastErrorCode": row.last_error_code,
        "inboundVerified": row.inbound_verified,
    }


def create_connection(
    session: Session, *, value: ConnectionInput, secrets: SecretStore, now: datetime
) -> MessagingConnection:
    row = MessagingConnection(
        provider=value.provider,
        label=value.label,
        settings_json=json.dumps(value.settings),
        secret_ciphertext=secrets.encrypt(
            {k: v.get_secret_value() for k, v in value.credentials.items()}
        ),
        created_at=utc_naive(now),
        state="awaiting_installation" if value.provider == "slack" else "unconfigured",
    )
    session.add(row)
    session.flush()
    return row


def require_connection(session: Session, connection_id: str) -> MessagingConnection:
    row = session.get(MessagingConnection, connection_id)
    if row is None:
        raise MessagingError(ErrorCode.NOT_FOUND, "Connection not found")
    return row


def set_verified_account(
    session: Session, *, row: MessagingConnection, external_account: str, now: datetime
) -> None:
    if row.external_account and row.external_account != external_account:
        raise MessagingError(
            ErrorCode.CONFLICT,
            "Credentials belong to another account; create a new connection",
        )
    other = session.exec(
        select(MessagingConnection).where(
            MessagingConnection.provider == row.provider,
            MessagingConnection.external_account == external_account,
            MessagingConnection.id != row.id,
        )
    ).first()
    if other:
        raise MessagingError(ErrorCode.CONFLICT, "This account is already connected")
    row.external_account, row.last_checked_at = external_account, utc_naive(now)
    row.last_error_code = None
    row.state = "ready" if row.inbound_verified else "awaiting_inbound_verification"
    session.add(row)
    session.flush()
