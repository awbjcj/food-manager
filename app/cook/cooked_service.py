"""Cooked-meal lifecycle (v5.5): open a consume sheet, toggle it, confirm it.

The `CookedMeal` row is both the pending sheet and the permanent record: it is
created unconfirmed on the first ✅ tap, mutated by each checkbox tap, and
finalized by `confirm`. Only confirmed rows count as cooked anywhere, so an
abandoned sheet is an absence of knowledge rather than a claim that a meal was
skipped. Telegram caps callback_data at 64 bytes, which is why the checkbox set
lives here rather than in the payload.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime

from sqlmodel import Session, select

from app.cook.models import ScoredCandidate
from app.cook.novelty import count_confirmed, recipe_key
from app.models import CookedMeal, MealPlanEntry, PantryItem
from app.normalization import normalize
from app.pantry_service import ListFilter, list_active, mark_eaten


@dataclass(frozen=True)
class ConsumeCandidate:
    item_id: int
    raw_name: str


@dataclass(frozen=True)
class CookedSheet:
    cooked_id: int
    recipe_title: str
    candidates: list[ConsumeCandidate]
    selected_ids: set[int]


@dataclass(frozen=True)
class ConfirmResult:
    eaten_names: list[str]
    skipped: int


def _matching_items(
    session: Session, *, household_id: int, candidate: ScoredCandidate, today: date
) -> list[PantryItem]:
    """Active pantry rows the recipe uses — the complement of `shopping_list`."""
    recipe_norm = {normalize(i.name) for i in candidate.recipe.ingredients}
    return [
        item
        for item in list_active(
            session, household_id=household_id, f=ListFilter.default(), today=today
        )
        if item.normalized_name in recipe_norm
    ]


def _sheet(session: Session, row: CookedMeal, *, today: date) -> CookedSheet:
    assert row.id is not None
    selected = set(json.loads(row.selection_json or "[]"))
    candidates = [
        ConsumeCandidate(item_id=item.id, raw_name=item.raw_name)
        for item in _candidates_for(session, row, today=today)
        if item.id is not None
    ]
    known = {c.item_id for c in candidates}
    return CookedSheet(
        cooked_id=row.id,
        recipe_title=row.recipe_title,
        candidates=candidates,
        selected_ids=selected & known,
    )


def _candidates_for(
    session: Session, row: CookedMeal, *, today: date
) -> list[PantryItem]:
    entry = (
        session.get(MealPlanEntry, row.plan_entry_id)
        if row.plan_entry_id is not None
        else None
    )
    if entry is None:
        return []
    candidate = ScoredCandidate.model_validate_json(entry.recipe_json)
    return _matching_items(
        session, household_id=row.household_id, candidate=candidate, today=today
    )


def open_sheet(
    session: Session, *, household_id: int, entry: MealPlanEntry, today: date
) -> CookedSheet:
    """Get-or-create the unconfirmed sheet for one planned day."""
    existing = session.exec(
        select(CookedMeal).where(
            CookedMeal.household_id == household_id,
            CookedMeal.plan_entry_id == entry.id,
            CookedMeal.confirmed_at.is_(None),  # type: ignore[union-attr]
        )
    ).first()
    if existing is not None:
        return _sheet(session, existing, today=today)

    candidate = ScoredCandidate.model_validate_json(entry.recipe_json)
    matched = _matching_items(
        session, household_id=household_id, candidate=candidate, today=today
    )
    row = CookedMeal(
        household_id=household_id,
        source="plan",
        plan_entry_id=entry.id,
        recipe_key=recipe_key(candidate),
        recipe_title=candidate.recipe.title,
        cooked_on=today,
        selection_json=json.dumps([i.id for i in matched if i.id is not None]),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _sheet(session, row, today=today)


def _load_row(
    session: Session, *, household_id: int, cooked_id: int
) -> CookedMeal | None:
    row = session.get(CookedMeal, cooked_id)
    if row is None or row.household_id != household_id or row.confirmed_at is not None:
        return None
    return row


def load_sheet(
    session: Session, *, household_id: int, cooked_id: int, today: date
) -> CookedSheet | None:
    row = _load_row(session, household_id=household_id, cooked_id=cooked_id)
    return None if row is None else _sheet(session, row, today=today)


def toggle(
    session: Session, *, household_id: int, cooked_id: int, item_id: int, today: date
) -> CookedSheet | None:
    row = _load_row(session, household_id=household_id, cooked_id=cooked_id)
    if row is None:
        return None
    selected = set(json.loads(row.selection_json or "[]"))
    selected.symmetric_difference_update({item_id})
    row.selection_json = json.dumps(sorted(selected))
    session.add(row)
    session.commit()
    session.refresh(row)
    return _sheet(session, row, today=today)


def confirm(
    session: Session,
    *,
    household_id: int,
    cooked_id: int,
    today: date,
    now: datetime,
    consume: bool = True,
) -> ConfirmResult | None:
    """Eat the checked rows and finalize the record.

    Iterates the raw `selection_json` rather than the sheet's intersected view:
    an item another member marked terminal between opening and confirming is no
    longer active, so it must still be counted as skipped rather than silently
    vanishing. `mark_eaten` returns `was_already` for those, never raising.

    `consume=False` is the "Nothing used" path — records the meal, touches no
    pantry row.
    """
    row = _load_row(session, household_id=household_id, cooked_id=cooked_id)
    if row is None:
        return None
    sheet = _sheet(session, row, today=today)
    by_id = {c.item_id: c.raw_name for c in sheet.candidates}
    eaten: list[str] = []
    skipped = 0
    if consume:
        for item_id in sorted(json.loads(row.selection_json or "[]")):
            result = mark_eaten(
                session, household_id=household_id, item_id=item_id, today=today
            )
            if result.applied:
                # applied implies the row was active, so it is in `by_id`
                eaten.append(by_id.get(item_id, ""))
            else:
                skipped += 1
    row.confirmed_at = now
    session.add(row)
    session.commit()
    return ConfirmResult(eaten_names=eaten, skipped=skipped)


__all__ = [
    "ConfirmResult",
    "ConsumeCandidate",
    "CookedSheet",
    "confirm",
    "count_confirmed",
    "load_sheet",
    "open_sheet",
    "toggle",
]
