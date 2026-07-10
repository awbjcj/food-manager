from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.profile_service import FoodProfile
from app.week_composer import (
    AgnoWeekComposer,
    DaySpec,
    WeekPlanSpec,
    heuristic_compose,
)


def _profile(**kw):
    base: dict[str, Any] = dict(
        diet="none", exclusions=[], preferred_cuisines=[],
        max_cook_minutes=None, household_size=2, note="",
    )
    base.update(kw)
    return FoodProfile(**base)


def test_heuristic_assigns_expiring_items_earliest():
    pantry = [("yogurt", 1), ("chicken", 2), ("rice", 300), ("beans", 200)]
    specs = heuristic_compose(pantry=pantry, profile=_profile(), days=2)
    assert len(specs) == 2
    assert specs[0].feature_items == ["yogurt", "chicken"]
    assert specs[0].purpose == "use_it_up"


def test_heuristic_rotates_preferred_cuisines():
    pantry = [("rice", 30)] * 6
    specs = heuristic_compose(
        pantry=pantry, profile=_profile(preferred_cuisines=["thai", "mexican"]), days=3
    )
    assert [s.cuisine for s in specs] == ["thai", "mexican", "thai"]


@pytest.mark.asyncio
async def test_agno_composer_returns_specs_padded_to_days():
    spec = WeekPlanSpec(days=[DaySpec(day_index=0, cuisine="thai")])
    inner = SimpleNamespace(
        arun=AsyncMock(return_value=SimpleNamespace(content=spec))
    )
    composer = AgnoWeekComposer(inner)
    specs = await composer.compose(
        pantry=[("rice", 5)], profile=_profile(), days=3, today=date(2026, 7, 9)
    )
    assert len(specs) == 3                      # padded via heuristic tail
    assert specs[0].cuisine == "thai"
    prompt = inner.arun.await_args.args[0]
    assert "rice" in prompt and "3" in prompt


@pytest.mark.asyncio
async def test_agno_composer_rejects_unstructured_content():
    inner = SimpleNamespace(
        arun=AsyncMock(return_value=SimpleNamespace(content="nope"))
    )
    with pytest.raises(ValueError):
        await AgnoWeekComposer(inner).compose(
            pantry=[], profile=_profile(), days=2, today=date(2026, 7, 9)
        )
