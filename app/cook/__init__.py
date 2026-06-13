"""Cook: the recipe slice — choose pantry items, find recipes, score and serve.

This package is the bounded slice that turns expiring pantry items into recipe
suggestions, tracks an in-progress cook session, and remembers favourites. Its
internals (item selection, recipe sourcing, nutrition scoring, the LLM clients,
the session and favourites stores) are submodules; this `__init__` is the slice's
public interface — the only surface the rest of the app should import.

Internal modules still import each other by their submodule path
(`app.cook.logic`, `app.cook.models`, ...); external callers should import from
`app.cook` so a change inside the slice stays inside the slice.
"""
from __future__ import annotations

from app.cook.feedback import set_feedback
from app.cook.favorites_service import (
    list_saved,
    load_saved,
    recipe_from_saved,
    recook_shopping_list,
    save_candidate,
)
from app.cook.logic import missing_ingredients, shopping_list, violates_exclusions
from app.cook.models import (
    RecipeCandidate,
    RecipeIngredient,
    ScoredCandidate,
)
from app.cook.service import NotEnoughItems, run_cook
from app.cook.session_service import (
    create_cook_session,
    load_cook_session,
    mark_status,
    set_message_id,
    sweep_expired_cooks,
)

__all__ = [
    "NotEnoughItems",
    "RecipeCandidate",
    "RecipeIngredient",
    "ScoredCandidate",
    "create_cook_session",
    "list_saved",
    "load_cook_session",
    "load_saved",
    "mark_status",
    "missing_ingredients",
    "recipe_from_saved",
    "recook_shopping_list",
    "run_cook",
    "save_candidate",
    "set_feedback",
    "set_message_id",
    "shopping_list",
    "sweep_expired_cooks",
    "violates_exclusions",
]
