"""Meal-plan orchestrator (v5.2): gather -> compose -> fetch-with-allocation -> persist.

Deterministic Python owns the pipeline; the only LLM step is the WeekComposer
(app/week_composer.py), and its failure degrades to heuristic_compose so /plan
always answers. Day N is planned against the pantry pool minus what days
1..N-1 consumed, so expiring items are cooked early and the aggregated
shopping list is correct by construction.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from app.cook.affinity import affinity, list_recent_signals, steering_summary
from app.cook.logic import (
    blended_score,
    expiry_utilization,
    shopping_list,
    violates_exclusions,
)
from app.cook.models import Purpose, ScoredCandidate
from app.cook.novelty import list_recent_cooks, novelty, recipe_key
from app.cook.recipe_source import build_criteria
from app.cook.service import MIN_USABLE_ITEMS, URGENT_DAYS
from app.models import MealPlan, MealPlanEntry
from app.normalization import normalize
from app.pantry_service import ListFilter, list_active
from app.week_composer import DaySpec, heuristic_compose

log = logging.getLogger(__name__)

PLAN_PAGE = 6  # candidates per search page; swap advances offset by this
PLAN_COST_CEILING_MICROS = 150_000  # overwritten from Settings at bootstrap


class NotEnoughItemsToPlan(Exception):
    pass


def tonight_entry(
    session: Session, *, household_id: int, today: date
) -> MealPlanEntry | None:
    """The active plan's entry for today, if any."""
    return session.exec(
        select(MealPlanEntry)
        .join(MealPlan, MealPlan.id == MealPlanEntry.plan_id)  # type: ignore[arg-type]
        .where(
            MealPlan.household_id == household_id,
            MealPlan.status == "active",
            MealPlanEntry.date == today,
        )
    ).first()


def cancel_active_plans(session: Session, *, household_id: int) -> int:
    rows = session.exec(
        select(MealPlan).where(
            MealPlan.household_id == household_id, MealPlan.status == "active"
        )
    ).all()
    for row in rows:
        row.status = "cancelled"
        session.add(row)
    session.commit()
    return len(rows)


def aggregate_shopping(entries: Sequence[MealPlanEntry]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for entry in entries:
        for name in json.loads(entry.shopping_json or "[]"):
            key = normalize(name)
            if key in seen:
                continue
            seen.add(key)
            out.append(name)
    return out


def _score(sourced, *, urgent_names: list[str], signals, cooks, today: date) -> ScoredCandidate:
    names = [i.name for i in sourced.recipe.ingredients]
    expiry_use = expiry_utilization(recipe_names=names, urgent_names=urgent_names)
    candidate = ScoredCandidate(
        recipe=sourced.recipe,
        nutrition=sourced.nutrition,
        expiry_use=expiry_use,
        external_id=sourced.external_id,
        final_score=0.0,
    )
    return candidate.model_copy(
        update={
            "final_score": blended_score(
                health_0_1=sourced.nutrition.health_score / 100.0,
                expiry_use=expiry_use,
                deliciousness=sourced.recipe.deliciousness,
                affinity_0_1=affinity(
                    cuisine=sourced.recipe.cuisine, ingredient_names=names, signals=signals
                ),
                novelty_0_1=novelty(recipe_key(candidate), cooks, today),
            )
        }
    )


def _pick(
    sourced_list,
    *,
    exclusions,
    taken_ids: set[str],
    urgent_names: list[str],
    signals,
    cooks,
    today: date,
) -> ScoredCandidate | None:
    safe = [
        s
        for s in sourced_list
        if not violates_exclusions(
            [i.name for i in s.recipe.ingredients], exclusions=exclusions
        )
        and (s.external_id is None or s.external_id not in taken_ids)
    ]
    scored = sorted(
        (
            _score(s, urgent_names=urgent_names, signals=signals, cooks=cooks, today=today)
            for s in safe
        ),
        key=lambda c: c.final_score,
        reverse=True,
    )
    return scored[0] if scored else None


async def _search_day(
    source,
    *,
    spec: DaySpec,
    include: list[str],
    profile,
    offset: int,
    remaining_cost_micros: int | None,
    steering: str | None = None,
):
    criteria = build_criteria(
        include_ingredients=include,
        meal_type="dinner",
        cuisine=spec.cuisine,
        purpose=Purpose(spec.purpose),
        profile=profile,
        offset=offset,
        steering=steering,
    )
    sourced, cost = await source.search(
        criteria, remaining_cost_micros=remaining_cost_micros
    )
    if not sourced and spec.cuisine is not None:  # relax cuisine once
        criteria = build_criteria(
            include_ingredients=include,
            meal_type="dinner",
            cuisine=None,
            purpose=Purpose(spec.purpose),
            profile=profile,
            offset=offset,
            steering=steering,
        )
        remaining_after = (
            None
            if remaining_cost_micros is None
            else max(0, remaining_cost_micros - (cost or 0))
        )
        more, extra = await source.search(
            criteria, remaining_cost_micros=remaining_after
        )
        return more, (cost or 0) + (extra or 0)
    return sourced, cost


async def build_plan(
    session: Session,
    *,
    household_id: int,
    days: int,
    profile,
    composer,
    source,
    today: date,
    chat_id: int,
    cost_ceiling_micros: int,
    created_at: datetime,
) -> tuple[MealPlan, list[MealPlanEntry]]:
    items = [
        item
        for item in list_active(
            session, household_id=household_id, f=ListFilter.default(), today=today
        )
        if item.expires_on >= today
    ]
    if len(items) < MIN_USABLE_ITEMS:
        raise NotEnoughItemsToPlan()
    pantry = [(i.normalized_name, (i.expires_on - today).days) for i in items]

    cost = 0
    try:
        if composer is None:
            raise RuntimeError("composer disabled")
        specs = await composer.compose(
            pantry=pantry, profile=profile, days=days, today=today
        )
    except Exception as exc:  # noqa: BLE001 - composer is best-effort
        log.warning("plan_composer_failed", extra={"error_class": type(exc).__name__})
        specs = heuristic_compose(pantry=pantry, profile=profile, days=days)

    cancel_active_plans(session, household_id=household_id)
    plan = MealPlan(
        household_id=household_id,
        start_date=today,
        days=days,
        chat_id=chat_id,
        created_at=created_at,
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)
    assert plan.id is not None

    pool = sorted(pantry, key=lambda pair: pair[1])  # (name, days_left), expiring first
    taken_ids: set[str] = set()
    entries: list[MealPlanEntry] = []
    signals = list_recent_signals(session, household_id=household_id)
    cooks = list_recent_cooks(session, household_id=household_id, today=today)
    for spec in specs[:days]:
        pool_names = [name for name, _ in pool]
        include = [f for f in spec.feature_items if normalize(f) in pool_names]
        if not include:
            include = pool_names[:2]
        remaining = max(0, cost_ceiling_micros - cost)
        sourced, day_cost = await _search_day(
            source,
            spec=spec,
            include=include,
            profile=profile,
            offset=0,
            remaining_cost_micros=remaining,
            steering=steering_summary(signals) or None,
        )
        cost += day_cost or 0
        urgent = [n for n, d in pool if d <= URGENT_DAYS]
        candidate = _pick(
            sourced,
            exclusions=profile.exclusions,
            taken_ids=taken_ids,
            urgent_names=urgent,
            signals=signals,
            cooks=cooks,
            today=today,
        )
        if candidate is None:
            if cost > cost_ceiling_micros:
                break
            continue  # skip the day rather than fail the plan
        if candidate.external_id:
            taken_ids.add(candidate.external_id)
        recipe_norm = {normalize(i.name) for i in candidate.recipe.ingredients}
        gap = shopping_list(
            recipe_names=[i.name for i in candidate.recipe.ingredients],
            pantry_normalized=pool_names,
        )
        pool = [pair for pair in pool if pair[0] not in recipe_norm]
        entries.append(
            MealPlanEntry(
                plan_id=plan.id,
                day_index=spec.day_index,
                date=today + timedelta(days=spec.day_index),
                recipe_json=candidate.model_dump_json(),
                spec_json=spec.model_dump_json(),
                shopping_json=json.dumps(gap),
            )
        )
        if cost > cost_ceiling_micros:
            log.warning("plan_cost_ceiling", extra={"plan_id": plan.id})
            break

    plan.status = "active"
    plan.cost_micros_usd = cost
    session.add(plan)
    for entry in entries:
        session.add(entry)
    session.commit()
    session.refresh(plan)
    for entry in entries:
        session.refresh(entry)
    return plan, entries


async def swap_day(
    session: Session,
    *,
    plan: MealPlan,
    entry: MealPlanEntry,
    profile,
    source,
    today: date,
    cost_ceiling_micros: int,
) -> MealPlanEntry | None:
    spec = DaySpec.model_validate_json(entry.spec_json)
    siblings = session.exec(
        select(MealPlanEntry).where(MealPlanEntry.plan_id == plan.id)
    ).all()
    taken_ids: set[str] = set()
    for e in siblings:
        if e.id == entry.id:
            continue
        ext_id = ScoredCandidate.model_validate_json(e.recipe_json).external_id
        if ext_id is not None:
            taken_ids.add(ext_id)
    current = ScoredCandidate.model_validate_json(entry.recipe_json)
    if current.external_id:
        taken_ids.add(current.external_id)

    entry.search_offset += PLAN_PAGE
    pantry = [
        (i.normalized_name, (i.expires_on - today).days)
        for i in list_active(
            session, household_id=plan.household_id, f=ListFilter.default(), today=today
        )
        if i.expires_on >= today
    ]
    include = [f for f in spec.feature_items if normalize(f) in {n for n, _ in pantry}]
    remaining = max(0, cost_ceiling_micros - (plan.cost_micros_usd or 0))
    signals = list_recent_signals(session, household_id=plan.household_id)
    cooks = list_recent_cooks(session, household_id=plan.household_id, today=today)
    sourced, cost = await _search_day(
        source,
        spec=spec,
        include=include or [n for n, _ in pantry][:2],
        profile=profile,
        offset=entry.search_offset,
        remaining_cost_micros=remaining,
        steering=steering_summary(signals) or None,
    )
    plan.cost_micros_usd = (plan.cost_micros_usd or 0) + (cost or 0)
    urgent = [n for n, d in pantry if d <= URGENT_DAYS]
    candidate = _pick(
        sourced,
        exclusions=profile.exclusions,
        taken_ids=taken_ids,
        urgent_names=urgent,
        signals=signals,
        cooks=cooks,
        today=today,
    )
    session.add(plan)
    if candidate is None:
        session.add(entry)  # persist the advanced offset anyway
        session.commit()
        return None
    entry.recipe_json = candidate.model_dump_json()
    entry.shopping_json = json.dumps(
        shopping_list(
            recipe_names=[i.name for i in candidate.recipe.ingredients],
            pantry_normalized=[n for n, _ in pantry],
        )
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry
