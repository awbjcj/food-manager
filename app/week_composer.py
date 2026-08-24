"""Meal-plan week composer seam (v5.2): the plan's one Agno surface.

Mirrors `app/nl_intent.py`'s pattern: a structured-output Agno agent proposes
a `WeekPlanSpec` (one `DaySpec` per day), built once per configured provider
at bootstrap. The composer only proposes cuisine/purpose/feature-item shape —
`app/plan_service.py` owns every DB read/write and recipe search. Any
composer failure (missing provider, malformed output, network error) degrades
to `heuristic_compose`, a pure deterministic fallback, so `/plan` always
answers.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from typing import Literal, Protocol

from pydantic import BaseModel

from app.agno_models import build_agno_model
from app.providers import ProviderCredentials, ProviderSelector

log = logging.getLogger(__name__)

ITEMS_PER_DAY = 2  # heuristic: expiring items featured per day


class DaySpec(BaseModel):
    day_index: int
    cuisine: str | None = None
    purpose: Literal["use_it_up", "quick", "healthy", "comfort", "surprise"] = "use_it_up"
    feature_items: list[str] = []


class WeekPlanSpec(BaseModel):
    days: list[DaySpec]


class WeekComposer(Protocol):
    async def compose(
        self, *, pantry: Sequence[tuple[str, int]], profile, days: int, today: date
    ) -> list[DaySpec]: ...


_INSTRUCTIONS = """\
You compose a varied dinner plan for a household pantry tracker.

Input: today's date, a number of days, the household food profile, and the
pantry as "name (expires in Nd)" pairs.

Output one DaySpec per day:
- Spread soon-to-expire items across the EARLIEST days (feature_items, using
  the exact pantry names given, canonical English).
- Vary cuisine across the week; prefer the profile's preferred cuisines; never
  the same cuisine twice in a row unless the profile lists only one.
- purpose: "use_it_up" for days featuring expiring items, otherwise pick what
  fits the profile.
- Respect diet/exclusions: never feature an excluded item.
"""


def heuristic_compose(*, pantry, profile, days: int) -> list[DaySpec]:
    """Deterministic fallback: earliest-expiry items first, cuisines rotated."""
    by_expiry = sorted(pantry, key=lambda pair: pair[1])
    cuisines = [c for c in profile.preferred_cuisines if c and c.strip()] or [None]
    specs: list[DaySpec] = []
    for day in range(days):
        feature = [
            name for name, _ in by_expiry[day * ITEMS_PER_DAY : (day + 1) * ITEMS_PER_DAY]
        ]
        specs.append(
            DaySpec(
                day_index=day,
                cuisine=cuisines[day % len(cuisines)],
                purpose="use_it_up",
                feature_items=feature,
            )
        )
    return specs


class AgnoWeekComposer:
    """Wraps one Agno Agent (built once at bootstrap) behind WeekComposer."""

    def __init__(self, agent) -> None:
        self._agent = agent

    async def compose(self, *, pantry, profile, days: int, today: date) -> list[DaySpec]:
        pantry_text = ", ".join(f"{name} (expires in {d}d)" for name, d in pantry)
        prompt = (
            f"today: {today.isoformat()}\n"
            f"days: {days}\n"
            f"profile: diet={profile.diet}; exclusions={profile.exclusions}; "
            f"preferred_cuisines={profile.preferred_cuisines}; "
            f"max_cook_minutes={profile.max_cook_minutes}\n"
            f"pantry: {pantry_text or '(empty)'}"
        )
        response = await self._agent.arun(prompt)
        spec = getattr(response, "content", None)
        if not isinstance(spec, WeekPlanSpec):
            raise ValueError("week composer returned no structured WeekPlanSpec")  # noqa: TRY004 - JSON-shape contract, not a type check
        specs = list(spec.days[:days])
        if len(specs) < days:  # pad missing days deterministically
            specs.extend(heuristic_compose(pantry=pantry, profile=profile, days=days)[len(specs):])
        for index, one in enumerate(specs):
            one.day_index = index
        return specs


def build_week_composer(
    provider: str, *, model_id: str, credentials: ProviderCredentials
) -> AgnoWeekComposer:
    from agno.agent import Agent

    model = build_agno_model(provider, model_id=model_id, credentials=credentials)
    return AgnoWeekComposer(
        Agent(model=model, description=_INSTRUCTIONS, output_schema=WeekPlanSpec)
    )


class WeekComposerSelector(ProviderSelector[AgnoWeekComposer]):
    """Per-user composer routing; no fallback (text-seam convention)."""
