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
Storage = Literal["default", "fridge", "frozen"]
ShelfLifeSource = Literal[
    "cache",
    "llm",
    "manual_fallback",
    "user_correction",
    "websearch",
    "frozen_foodkeeper",
    "frozen_llm",
    "frozen_default",
    "fridge_foodkeeper",
    "fridge_llm",
    "fridge_default",
]
IngestShelfLifeSource = Literal["cache", "llm", "manual_fallback", "manual_user_hint"]
CreatedVia = Literal["receipt", "manual"]
PurchaseDateSource = Literal["receipt", "scan_fallback"]
CacheSource = Literal["llm", "user_correction"]
PendingActionType = Literal["correct", "add"]
PendingStatus = Literal["pending", "applied", "cancelled", "expired", "stale"]
CacheAction = Literal["move", "add_new", "leave"]
CookStatus = Literal["collecting", "ready", "done", "cancelled", "expired"]
CookFeedback = Literal["none", "liked", "disliked"]
Role = Literal["owner", "member"]


class Household(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = "My Household"
    diet: str = "none"
    exclusions_json: str = "[]"
    preferred_cuisines_json: str = "[]"
    max_cook_minutes: Optional[int] = None
    household_size: int = 1
    profile_note: str = ""
    created_at: datetime


class User(SQLModel, table=True):
    telegram_id: int = Field(primary_key=True)
    chat_id: int
    household_id: int = Field(foreign_key="household.id", index=True)
    tz: str = "America/Detroit"
    digest_hour: int = 8
    llm_provider: str = "anthropic"
    lang: str = "en"
    role: str = "member"  # "owner" | "member"; owner is the household creator
    created_at: datetime
    # Last date (user tz) a digest run completed — silent days count. None = never.
    last_digest_date: Optional[date] = None


class Receipt(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("household_id", "photo_file_id", name="uq_receipt_household_photo"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
    photo_file_id: str
    purchase_date: date
    purchase_date_source: str
    scanned_at: datetime
    llm_cost_micros_usd: Optional[int] = None


class PantryItem(SQLModel, table=True):
    __table_args__ = (
        Index("ix_pantry_household_status_expires", "household_id", "status", "expires_on"),
        Index(
            "ix_pantry_household_status_category_expires",
            "household_id", "status", "category", "expires_on",
        ),
        Index("ix_pantry_source_receipt", "source_receipt_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
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
    storage: str = "default"
    # Date the item entered its current non-default Storage State (fridge/frozen).
    # Unified Shelf-Life Origin for every state; None for "default".
    stored_on: Optional[date] = None
    created_via: str
    source_receipt_id: Optional[int] = Field(default=None, foreign_key="receipt.id")
    created_at: datetime


class ShelfLifeCache(SQLModel, table=True):
    household_id: int = Field(foreign_key="household.id", primary_key=True)
    normalized_name: str = Field(primary_key=True)
    days: int
    category: Optional[str] = None
    confidence: float
    learned_at: datetime
    source: str = "llm"


class PendingCorrection(SQLModel, table=True):
    __table_args__ = (
        Index("ix_pending_household_status_created", "household_id", "status", "created_at"),
        Index("ix_pending_item", "item_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
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
        Index("ix_cook_household_status_created", "household_id", "status", "created_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
    status: str = "collecting"
    meal_type: Optional[str] = None
    cuisine: Optional[str] = None
    selected_item_ids: str = "[]"
    candidates_json: Optional[str] = None
    chosen_index: Optional[int] = None
    purpose: Optional[str] = None
    search_offset: int = 0
    chat_id: int
    message_id: Optional[int] = None
    llm_cost_micros_usd: Optional[int] = None
    feedback: str = "none"
    feedback_at: Optional[datetime] = None
    created_at: datetime
    expires_at: datetime


class ShoppingList(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
    name_raw: str
    name_normalized: str = Field(index=True)
    qty: Optional[float] = None
    unit: Optional[str] = None
    added_at: datetime
    bought_at: Optional[datetime] = None


class SavedRecipe(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
    title: str
    cuisine: str
    source_url: Optional[str] = None
    ingredients_json: str
    method_gist: str
    saved_at: datetime


class NameTranslation(SQLModel, table=True):
    # Global (household-agnostic): a translation of a display string is a language
    # fact reusable across households. Keyed by (lang, source_text).
    lang: str = Field(primary_key=True)
    source_text: str = Field(primary_key=True)
    translated_text: str


class HouseholdInvite(SQLModel, table=True):
    # A single-use, time-limited token that lets a new Telegram user join an
    # existing household. Redeeming links the new user to ``household_id`` and
    # stamps ``redeemed_by``/``redeemed_at`` so the token cannot be reused.
    id: Optional[int] = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
    token: str = Field(index=True, unique=True)
    created_by: int  # telegram_id of the inviting member
    created_at: datetime
    expires_at: datetime
    # None = unlimited redemptions until expiry. Default is None (not 1) so an
    # explicit None survives insert: a SQLAlchemy column default of 1 would
    # coerce None back to 1. create_invite always passes max_uses explicitly.
    max_uses: Optional[int] = None
    uses: int = 0  # redemptions so far
    redeemed_by: Optional[int] = None  # telegram_id of the last joiner; None = unused
    redeemed_at: Optional[datetime] = None
