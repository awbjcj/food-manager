"""Stable /cook option values and their localized display labels.

The values in this module are the canonical values written to ``CookSession``
and sent to recipe providers.  Callers should pass the localized labels to
Telegram keyboards, but keep the canonical list for callback-index lookups.
"""

from __future__ import annotations

import string
from collections.abc import Iterable

from app.i18n import t

MEAL_TYPES = ["Dinner", "Lunch", "Breakfast", "Dessert", "Snack", "Surprise me"]
MEAL_TYPE_I18N_KEYS = {
    "Dinner": "meal.dinner",
    "Lunch": "meal.lunch",
    "Breakfast": "meal.breakfast",
    "Dessert": "meal.dessert",
    "Snack": "meal.snack",
    "Surprise me": "meal.surprise",
}


# Keep the provider's spelling/order for the canonical values.  The first
# level intentionally promotes the most common cuisines; the expanded menu
# is derived below so a cuisine can never appear in both tiers.
ALL_CUISINES = [
    "African",
    "Asian",
    "American",
    "British",
    "Cajun",
    "Caribbean",
    "Chinese",
    "Eastern European",
    "European",
    "French",
    "German",
    "Greek",
    "Indian",
    "Irish",
    "Italian",
    "Japanese",
    "Jewish",
    "Korean",
    "Latin American",
    "Mediterranean",
    "Mexican",
    "Middle Eastern",
    "Nordic",
    "Southern",
    "Spanish",
    "Thai",
    "Vietnamese",
]

FEATURED_CUISINES = [
    "Italian",
    "Mexican",
    "Chinese",
    "American",
    "French",
    "Indian",
    "Japanese",
    "Thai",
    "Mediterranean",
    "Greek",
    "Spanish",
    "Korean",
]
SURPRISE_CUISINE = "Surprise me"
DEFAULT_CUISINES = [*FEATURED_CUISINES, SURPRISE_CUISINE]
MORE_CUISINES = [cuisine for cuisine in ALL_CUISINES if cuisine not in FEATURED_CUISINES]

_CUISINE_BY_NORMALIZED = {
    " ".join(cuisine.split()).casefold(): cuisine for cuisine in ALL_CUISINES
}
_CUISINE_BY_NORMALIZED[SURPRISE_CUISINE.casefold()] = SURPRISE_CUISINE


def canonical_cuisine(value: object) -> str | None:
    """Return the stable display/provider spelling for a preference value."""

    text = str(value).strip()
    if not text:
        return None
    normalized = " ".join(text.split()).casefold()
    return _CUISINE_BY_NORMALIZED.get(normalized, string.capwords(text))


def localized_cuisine(cuisine: str, lang: str) -> str:
    """Translate a known cuisine while preserving unknown custom preferences."""

    canonical = _CUISINE_BY_NORMALIZED.get(" ".join(cuisine.split()).casefold())
    if canonical is None:
        return cuisine
    slug = "surprise" if canonical == SURPRISE_CUISINE else canonical.casefold().replace(" ", "_")
    return t(f"cuisine.{slug}", lang)


def localized_cuisines(cuisines: Iterable[str], lang: str) -> list[str]:
    return [localized_cuisine(cuisine, lang) for cuisine in cuisines]


def more_cuisines(first_level: Iterable[str]) -> list[str]:
    """Return provider cuisines not already shown in the quick menu."""

    excluded = {
        canonical.casefold()
        for value in first_level
        if (canonical := canonical_cuisine(value)) is not None
    }
    return [cuisine for cuisine in ALL_CUISINES if cuisine.casefold() not in excluded]


def localized_meal_types(lang: str) -> list[str]:
    return [t(MEAL_TYPE_I18N_KEYS[meal_type], lang) for meal_type in MEAL_TYPES]
