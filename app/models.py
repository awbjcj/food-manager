from __future__ import annotations

from datetime import date, datetime
from typing import Literal

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
    id: int | None = Field(default=None, primary_key=True)
    name: str = "My Household"
    diet: str = "none"
    exclusions_json: str = "[]"
    preferred_cuisines_json: str = "[]"
    max_cook_minutes: int | None = None
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
    last_digest_date: date | None = None


class Receipt(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("household_id", "photo_file_id", name="uq_receipt_household_photo"),
    )

    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
    photo_file_id: str
    purchase_date: date
    purchase_date_source: str
    scanned_at: datetime
    llm_cost_micros_usd: int | None = None


class PantryItem(SQLModel, table=True):
    __table_args__ = (
        Index("ix_pantry_household_status_expires", "household_id", "status", "expires_on"),
        Index(
            "ix_pantry_household_status_category_expires",
            "household_id", "status", "category", "expires_on",
        ),
        Index("ix_pantry_source_receipt", "source_receipt_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
    raw_name: str
    normalized_name: str = Field(index=True)
    category: str | None = Field(default=None, index=True)
    qty: float = 1.0
    unit: str | None = None
    purchased_on: date
    shelf_life_days: int
    shelf_life_source: str
    ingest_shelf_life_source: str
    expires_on: date
    status: str = "active"
    snoozed_until: date | None = None
    storage: str = "default"
    # Date the item entered its current non-default Storage State (fridge/frozen).
    # Unified Shelf-Life Origin for every state; None for "default".
    stored_on: date | None = None
    created_via: str
    source_receipt_id: int | None = Field(default=None, foreign_key="receipt.id")
    created_at: datetime


class ShelfLifeCache(SQLModel, table=True):
    household_id: int = Field(foreign_key="household.id", primary_key=True)
    normalized_name: str = Field(primary_key=True)
    days: int
    category: str | None = None
    confidence: float
    learned_at: datetime
    source: str = "llm"


class PendingCorrection(SQLModel, table=True):
    __table_args__ = (
        Index("ix_pending_household_status_created", "household_id", "status", "created_at"),
        Index("ix_pending_item", "item_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
    action_type: str
    item_id: int | None = Field(default=None, foreign_key="pantryitem.id")
    proposed_json: str
    original_snapshot_json: str | None = None
    llm_cost_micros_usd: int | None = None
    chat_id: int
    message_id: int | None = None
    status: str = "pending"
    created_at: datetime
    expires_at: datetime


class CookSession(SQLModel, table=True):
    __table_args__ = (
        Index("ix_cook_household_status_created", "household_id", "status", "created_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
    status: str = "collecting"
    meal_type: str | None = None
    cuisine: str | None = None
    selected_item_ids: str = "[]"
    candidates_json: str | None = None
    chosen_index: int | None = None
    purpose: str | None = None
    search_offset: int = 0
    chat_id: int
    message_id: int | None = None
    llm_cost_micros_usd: int | None = None
    feedback: str = "none"
    feedback_at: datetime | None = None
    created_at: datetime
    expires_at: datetime


class MealPlan(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
    start_date: date
    days: int
    status: str = "draft"  # draft | active | cancelled
    cost_micros_usd: int = 0
    chat_id: int
    message_id: int | None = None
    created_at: datetime


class MealPlanEntry(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    plan_id: int = Field(foreign_key="mealplan.id", index=True)
    day_index: int
    date: date
    recipe_json: str          # one ScoredCandidate payload (same shape /cook persists)
    spec_json: str            # the DaySpec that produced it (swap rebuilds criteria from this)
    shopping_json: str = "[]"  # that day's ingredient gap (list[str])
    search_offset: int = 0    # advanced by swap for pagination


class ShoppingList(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
    name_raw: str
    name_normalized: str = Field(index=True)
    qty: float | None = None
    unit: str | None = None
    added_at: datetime
    bought_at: datetime | None = None


class SavedRecipe(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
    title: str
    cuisine: str
    source_url: str | None = None
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
    id: int | None = Field(default=None, primary_key=True)
    household_id: int = Field(foreign_key="household.id", index=True)
    token: str = Field(index=True, unique=True)
    created_by: int  # telegram_id of the inviting member
    created_at: datetime
    expires_at: datetime
    # None = unlimited redemptions until expiry. Default is None (not 1) so an
    # explicit None survives insert: a SQLAlchemy column default of 1 would
    # coerce None back to 1. create_invite always passes max_uses explicitly.
    max_uses: int | None = None
    uses: int = 0  # redemptions so far
    redeemed_by: int | None = None  # telegram_id of the last joiner; None = unused
    redeemed_at: datetime | None = None
