from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import app.plan_service as plan_service_mod
from app import handler_support
from app.billing.meter import admit, commit
from app.callback_dispatch import (
    answer as dispatch_answer,
)
from app.callback_dispatch import (
    edit_or_resend,
)
from app.client_set import EMPTY_CLIENTS, PerUserClients
from app.commands import (
    CommandError,
    parse_callback,
)
from app.cook.recipe_source import ChainedRecipeSource
from app.handlers.plan import (
    _plan_entry_rows,
    _plan_source,
    _render_plan_message,
)
from app.i18n import t
from app.models import Household, MealPlan
from app.plan_service import aggregate_shopping, swap_day
from app.profile_service import profile_from_household
from app.shopping_service import add_missing

log = logging.getLogger(__name__)


_authorized_callback_user = handler_support.authorized_callback_query_user


async def handle_plan_callback(
    cb,
    *,
    session_factory,
    now_provider,
    clients: PerUserClients = EMPTY_CLIENTS,
    recipe_sources=(),
    translation_llm=None,
) -> None:
    try:
        action = parse_callback(cb.data or "")
    except CommandError:
        await dispatch_answer(cb, "unrecognized action")
        return
    if (
        action.verb not in ("plan_swap", "plan_shop", "plan_cancel")
        or action.item_id is None
    ):
        await dispatch_answer(cb, "unrecognized action")
        return

    with session_factory() as session:
        user = _authorized_callback_user(session, cb)
        if user is None:
            await dispatch_answer(cb, "not authorized")
            return
        plan = session.get(MealPlan, action.item_id)
        if (
            plan is None
            or plan.household_id != user.household_id
            or plan.status != "active"
        ):
            await dispatch_answer(cb, t("plan.expired", user.lang))
            return
        today = now_provider(user.tz).date()

        if action.verb == "plan_cancel":
            plan.status = "cancelled"
            session.add(plan)
            session.commit()
            await dispatch_answer(cb)
            await edit_or_resend(cb, t("plan.cancelled", user.lang))
            return

        if action.verb == "plan_shop":
            entries, _candidates = _plan_entry_rows(session, plan.id)
            missing_names = aggregate_shopping(entries)
            if not missing_names:
                await dispatch_answer(cb, t("plan.shopping_none", user.lang))
                return
            result = add_missing(
                session,
                household_id=user.household_id,
                ingredients=[SimpleNamespace(name=n) for n in missing_names],
                now=datetime.now(UTC),
            )
            if not result.added:
                await dispatch_answer(cb, t("plan.shopping_none", user.lang))
            else:
                await dispatch_answer(
                    cb, t("plan.shopping_added", user.lang, n=len(result.added))
                )
            return

        # plan_swap
        entries, _candidates = _plan_entry_rows(session, plan.id)
        entry = next((e for e in entries if e.day_index == action.option_index), None)
        if entry is None:
            await dispatch_answer(cb, "unrecognized action")
            return
        household = session.get(Household, user.household_id)
        if household is None:
            await dispatch_answer(cb, "couldn't load your household profile")
            return
        profile = profile_from_household(household)
        now = now_provider(user.tz)
        decision = admit(
            session,
            household_id=user.household_id,
            op="plan",
            provider=user.llm_provider,
            now=now,
        )
        source = (
            _plan_source(recipe_sources, clients, user)
            if decision.allowed
            else ChainedRecipeSource(list(recipe_sources))
        )
        cost_before = plan.cost_micros_usd
        await dispatch_answer(cb)
        try:
            updated = await swap_day(
                session,
                plan=plan,
                entry=entry,
                profile=profile,
                source=source,
                today=today,
                cost_ceiling_micros=plan_service_mod.PLAN_COST_CEILING_MICROS,
            )
        except Exception as exc:  # noqa: BLE001 - swap must never break the bot
            log.warning("plan_swap_failed", extra={"error_class": type(exc).__name__})
            updated = None
        if updated is None:
            await edit_or_resend(cb, t("plan.no_swap", user.lang))
            return
        if decision.allowed:
            commit(
                session,
                household_id=user.household_id,
                op="plan",
                provider=user.llm_provider,
                cost_micros=max(0, plan.cost_micros_usd - cost_before),
                now=now,
            )
        text, keyboard = await _render_plan_message(
            session, plan=plan, lang=user.lang, translation_llm=translation_llm
        )
        await edit_or_resend(cb, text, keyboard)
