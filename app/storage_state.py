"""Storage State: the one place that knows how a Pantry Item's storage affects
its Shelf-Life Origin, expiry, and which moves are offered.

Storage State is an axis orthogonal to category: `default` (counter/pantry),
`fridge` (chilled), `frozen`. Transitions are one-way forward
(default -> fridge -> frozen); `frozen` is terminal. Every non-default state
records a single Storage Date (`PantryItem.stored_on`) which becomes the
Shelf-Life Origin, so expiry is one formula for every state.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Protocol


class _StorageTimedItem(Protocol):
    purchased_on: date
    stored_on: date | None
    shelf_life_days: int

DEFAULT = "default"
FRIDGE = "fridge"
FROZEN = "frozen"

# Forward-only transition graph. A state maps to the storage moves still offered.
_FORWARD: dict[str, tuple[str, ...]] = {
    DEFAULT: (FRIDGE, FROZEN),
    FRIDGE: (FROZEN,),
    FROZEN: (),
}


def next_storage_options(current: str) -> tuple[str, ...]:
    """Storage moves still available from `current` (forward-only)."""
    return _FORWARD.get(current, ())


def can_move_to(current: str, target: str) -> bool:
    return target in next_storage_options(current)


def shelf_life_origin(item: _StorageTimedItem) -> date:
    """The Shelf-Life Origin: the Storage Date once stored, else Purchase Date."""
    return item.stored_on or item.purchased_on


def compute_expiry(item: _StorageTimedItem) -> date:
    """Expiry from the unified origin; used after any shelf-life/storage change."""
    return shelf_life_origin(item) + timedelta(days=item.shelf_life_days)
