from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from sqlmodel import Session, select

from app.cook_logic import shopping_list
from app.cook_models import RecipeCandidate, RecipeIngredient
from app.models import SavedRecipe
from app.pantry_service import ListFilter, list_active
from app.pending_service import utc_naive


@dataclass
class SaveResult:
    saved: bool
    recipe_id: Optional[int]
    duplicate: bool


def save_candidate(
    session: Session, *, user_id: int, candidate: RecipeCandidate, now: datetime
) -> SaveResult:
    existing = session.exec(
        select(SavedRecipe).where(
            SavedRecipe.user_id == user_id,
            SavedRecipe.title == candidate.title,
            SavedRecipe.source_url == candidate.source_url,
        )
    ).first()
    if existing is not None:
        return SaveResult(saved=False, recipe_id=existing.id, duplicate=True)
    row = SavedRecipe(
        user_id=user_id,
        title=candidate.title,
        cuisine=candidate.cuisine,
        source_url=candidate.source_url,
        ingredients_json=json.dumps([i.model_dump() for i in candidate.ingredients]),
        method_gist=candidate.method_gist,
        saved_at=utc_naive(now),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return SaveResult(saved=True, recipe_id=row.id, duplicate=False)


def list_saved(session: Session, *, user_id: int) -> list[SavedRecipe]:
    return list(
        session.exec(
            select(SavedRecipe)
            .where(SavedRecipe.user_id == user_id)
            .order_by(SavedRecipe.saved_at)  # type: ignore[arg-type]
        ).all()
    )


def load_saved(
    session: Session, *, user_id: int, recipe_id: int
) -> Optional[SavedRecipe]:
    row = session.get(SavedRecipe, recipe_id)
    if row is None or row.user_id != user_id:
        return None
    return row


def recipe_from_saved(saved: SavedRecipe) -> RecipeCandidate:
    ingredients = [
        RecipeIngredient.model_validate(i) for i in json.loads(saved.ingredients_json)
    ]
    return RecipeCandidate(
        title=saved.title,
        cuisine=saved.cuisine,
        source_url=saved.source_url,
        ingredients=ingredients,
        method_gist=saved.method_gist,
    )


def recook_shopping_list(
    session: Session, *, user_id: int, saved: SavedRecipe, today: date
) -> list[str]:
    recipe = recipe_from_saved(saved)
    pantry = [
        item.normalized_name
        for item in list_active(session, user_id=user_id, f=ListFilter.default(), today=today)
        if item.expires_on >= today
    ]
    return shopping_list(
        recipe_names=[i.name for i in recipe.ingredients],
        pantry_normalized=pantry,
    )
