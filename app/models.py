from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel


Category = Literal[
    "dairy", "produce", "meat", "seafood", "bakery",
    "pantry", "frozen", "beverage", "other",
]

Status = Literal["active", "eaten", "tossed", "removed"]
ShelfLifeSource = Literal["cache", "llm", "manual_fallback", "user_correction", "websearch"]
IngestShelfLifeSource = Literal["cache", "llm", "manual_fallback", "manual_user_hint"]
CreatedVia = Literal["receipt", "manual"]
PurchaseDateSource = Literal["receipt", "scan_fallback"]
CacheSource = Literal["llm", "user_correction"]
PendingActionType = Literal["correct", "add"]
PendingStatus = Literal["pending", "applied", "cancelled", "expired", "stale"]
CacheAction = Literal["move", "add_new", "leave"]
CookStatus = Literal["collecting", "ready", "done", "cancelled", "expired"]


class User(SQLModel, table=True):
    telegram_id: int = Field(primary_key=True)
    chat_id: int
    tz: str = "America/Detroit"
    digest_hour: int = 8
    llm_provider: str = "anthropic"
    diet: str = "none"
    exclusions_json: str = "[]"
    preferred_cuisines_json: str = "[]"
    max_cook_minutes: Optional[int] = None
    household_size: int = 1
    profile_note: str = ""
    created_at: datetime


class Receipt(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("user_id", "photo_file_id", name="uq_receipt_user_photo"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.telegram_id", index=True)
    photo_file_id: str
    purchase_date: date
    purchase_date_source: str
    scanned_at: datetime
    llm_cost_micros_usd: Optional[int] = None


class PantryItem(SQLModel, table=True):
    __table_args__ = (
        Index("ix_pantry_user_status_expires", "user_id", "status", "expires_on"),
        Index(
            "ix_pantry_user_status_category_expires",
            "user_id", "status", "category", "expires_on",
        ),
        Index("ix_pantry_source_receipt", "source_receipt_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.telegram_id", index=True)
    raw_name: str
    normalized_name: str = Field(index=True)
    category: Optional[str] = Field(default=None, index=True)
    qty: float = 1.0
    unit: Optional[str] = None
    purchased_on: date
    shelf_life_days: int
    shelf_life_source: str
    ingest_shelf_life_source: str
    expires_on: date
    status: str = "active"
    snoozed_until: Optional[date] = None
    created_via: str
    source_receipt_id: Optional[int] = Field(default=None, foreign_key="receipt.id")
    created_at: datetime


class ShelfLifeCache(SQLModel, table=True):
    user_id: int = Field(foreign_key="user.telegram_id", primary_key=True)
    normalized_name: str = Field(primary_key=True)
    days: int
    category: Optional[str] = None
    confidence: float
    learned_at: datetime
    source: str = "llm"


class PendingCorrection(SQLModel, table=True):
    __table_args__ = (
        Index("ix_pending_user_status_created", "user_id", "status", "created_at"),
        Index("ix_pending_item", "item_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.telegram_id", index=True)
    action_type: str
    item_id: Optional[int] = Field(default=None, foreign_key="pantryitem.id")
    proposed_json: str
    original_snapshot_json: Optional[str] = None
    llm_cost_micros_usd: Optional[int] = None
    chat_id: int
    message_id: Optional[int] = None
    status: str = "pending"
    created_at: datetime
    expires_at: datetime


class CookSession(SQLModel, table=True):
    __table_args__ = (
        Index("ix_cook_user_status_created", "user_id", "status", "created_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.telegram_id", index=True)
    status: str = "collecting"
    meal_type: Optional[str] = None
    cuisine: Optional[str] = None
    selected_item_ids: str = "[]"
    candidates_json: Optional[str] = None
    chosen_index: Optional[int] = None
    chat_id: int
    message_id: Optional[int] = None
    llm_cost_micros_usd: Optional[int] = None
    created_at: datetime
    expires_at: datetime
