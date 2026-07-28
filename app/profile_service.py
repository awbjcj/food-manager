from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field
from sqlmodel import Session

from app.models import Household

if TYPE_CHECKING:
    from app.llm import ProfileUpdateLLMClient


class FoodProfile(BaseModel):
    diet: str = "none"
    exclusions: list[str] = Field(default_factory=list)
    preferred_cuisines: list[str] = Field(default_factory=list)
    max_cook_minutes: int | None = None
    household_size: int = 1
    note: str = ""


def profile_from_household(household: Household) -> FoodProfile:
    return FoodProfile(
        diet=household.diet,
        exclusions=json.loads(household.exclusions_json or "[]"),
        preferred_cuisines=json.loads(household.preferred_cuisines_json or "[]"),
        max_cook_minutes=household.max_cook_minutes,
        household_size=household.household_size,
        note=household.profile_note,
    )


def apply_profile_to_household(household: Household, profile: FoodProfile) -> None:
    household.diet = profile.diet
    household.exclusions_json = json.dumps(profile.exclusions)
    household.preferred_cuisines_json = json.dumps(profile.preferred_cuisines)
    household.max_cook_minutes = profile.max_cook_minutes
    household.household_size = profile.household_size
    household.profile_note = profile.note


async def update_profile_from_sentence(
    session: Session,
    *,
    llm: ProfileUpdateLLMClient,
    household: Household,
    sentence: str,
) -> tuple[FoodProfile, int | None]:
    current = profile_from_household(household)
    merged, cost = await llm.parse_profile_update(current=current, sentence=sentence)
    apply_profile_to_household(household, merged)
    session.add(household)
    session.commit()
    session.refresh(household)
    return merged, cost
