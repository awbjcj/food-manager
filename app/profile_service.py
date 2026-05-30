from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field
from sqlmodel import Session

from app.models import User

if TYPE_CHECKING:
    from app.llm import ProfileUpdateLLMClient


class FoodProfile(BaseModel):
    diet: str = "none"
    exclusions: list[str] = Field(default_factory=list)
    preferred_cuisines: list[str] = Field(default_factory=list)
    max_cook_minutes: Optional[int] = None
    household_size: int = 1
    note: str = ""


def profile_from_user(user: User) -> FoodProfile:
    return FoodProfile(
        diet=user.diet,
        exclusions=json.loads(user.exclusions_json or "[]"),
        preferred_cuisines=json.loads(user.preferred_cuisines_json or "[]"),
        max_cook_minutes=user.max_cook_minutes,
        household_size=user.household_size,
        note=user.profile_note,
    )


def apply_profile_to_user(user: User, profile: FoodProfile) -> None:
    user.diet = profile.diet
    user.exclusions_json = json.dumps(profile.exclusions)
    user.preferred_cuisines_json = json.dumps(profile.preferred_cuisines)
    user.max_cook_minutes = profile.max_cook_minutes
    user.household_size = profile.household_size
    user.profile_note = profile.note


async def update_profile_from_sentence(
    session: Session,
    *,
    llm: "ProfileUpdateLLMClient",
    user: User,
    sentence: str,
) -> tuple[FoodProfile, Optional[int]]:
    current = profile_from_user(user)
    merged, cost = await llm.parse_profile_update(current=current, sentence=sentence)
    apply_profile_to_user(user, merged)
    session.add(user)
    session.commit()
    session.refresh(user)
    return merged, cost
