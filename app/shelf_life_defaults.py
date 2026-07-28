"""Conservative fallback values for manual /add cache misses.

TODO(user): per spec §10.3, expand the map to reflect your kitchen.
Keep these conservative. Real values get learned via /correct.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DefaultEntry:
    days: int
    category: str | None


_EXACT: dict[str, DefaultEntry] = {
    "whole milk": DefaultEntry(7, "dairy"),
    "milk": DefaultEntry(7, "dairy"),
    "eggs": DefaultEntry(21, "dairy"),
    "butter": DefaultEntry(30, "dairy"),
    "yogurt": DefaultEntry(14, "dairy"),
    "bread": DefaultEntry(5, "bakery"),
    "bananas": DefaultEntry(5, "produce"),
    "apples": DefaultEntry(21, "produce"),
    "chicken": DefaultEntry(2, "meat"),
    "ground beef": DefaultEntry(2, "meat"),
    "salmon": DefaultEntry(2, "seafood"),
}


def lookup_default(normalized_name: str) -> DefaultEntry | None:
    return _EXACT.get(normalized_name)
