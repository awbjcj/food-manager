"""Cooked-sheet callbacks (v5.5).

The entry point (`plan:cooked:*`) opens the sheet from a plan day; the sheet's
own callbacks use a `cooked:*` prefix keyed by CookedMeal id, so the sheet
survives independently of its plan origin when /cook cards are wired later.
"""
from __future__ import annotations

import logging

from sqlmodel import select

from app import handler_support, views
from app.callback_dispatch import answer as dispatch_answer
from app.callback_dispatch import edit_or_resend
from app.commands import CommandError, parse_callback
from app.cook import load_cook_session
from app.cook.cooked_service import confirm, open_cook_sheet, open_sheet, toggle
from app.i18n import t
from app.models import MealPlan, MealPlanEntry
from app.renderer import build_cooked_sheet_keyboard
from app.telegram_ui import to_aiogram_keyboard

log = logging.getLogger(__name__)

_authorized_callback_user = handler_support.authorized_callback_query_user

_VERBS = (
    "plan_cooked",
    "cook_cooked",
    "cooked_toggle",
    "cooked_confirm",
    "cooked_none",
)


async def _show(cb, session, *, user, sheet, translation_llm) -> None:
    view = await views.cooked_sheet(
        session, sheet, user=user, translation_llm=translation_llm
    )
    keyboard = to_aiogram_keyboard(
        build_cooked_sheet_keyboard(sheet, lang=user.lang, names=view.names)
    )
    await edit_or_resend(cb, view.text, keyboard)


async def handle_cooked_callback(
    cb, *, session_factory, now_provider, translation_llm=None
) -> None:
    try:
        action = parse_callback(cb.data or "")
    except CommandError:
        await dispatch_answer(cb, "unrecognized action")
        return
    if action.verb not in _VERBS or action.item_id is None:
        await dispatch_answer(cb, "unrecognized action")
        return

    with session_factory() as session:
        user = _authorized_callback_user(session, cb)
        if user is None:
            await dispatch_answer(cb, "not authorized")
            return
        now = now_provider(user.tz)
        today = now.date()

        if action.verb == "plan_cooked":
            plan = session.get(MealPlan, action.item_id)
            if (
                plan is None
                or plan.household_id != user.household_id
                or plan.status != "active"
            ):
                await dispatch_answer(cb, t("plan.expired", user.lang))
                return
            entry = session.exec(
                select(MealPlanEntry).where(
                    MealPlanEntry.plan_id == plan.id,
                    MealPlanEntry.day_index == action.option_index,
                )
            ).first()
            if entry is None:
                await dispatch_answer(cb, "unrecognized action")
                return
            await dispatch_answer(cb)
            sheet = open_sheet(
                session, household_id=user.household_id, entry=entry, today=today
            )
            await _show(cb, session, user=user, sheet=sheet, translation_llm=translation_llm)
            return

        if action.verb == "cook_cooked":
            cook = load_cook_session(
                session, household_id=user.household_id, cook_id=action.item_id
            )
            if cook is None or cook.status != "done":
                await dispatch_answer(cb, t("toast.cook_expired", user.lang))
                return
            try:
                sheet = open_cook_sheet(
                    session,
                    household_id=user.household_id,
                    cook=cook,
                    today=today,
                )
            except ValueError:
                await dispatch_answer(cb, t("toast.cook_expired", user.lang))
                return
            await dispatch_answer(cb)
            await _show(
                cb,
                session,
                user=user,
                sheet=sheet,
                translation_llm=translation_llm,
            )
            return

        if action.verb == "cooked_toggle":
            if action.option_index is None:
                await dispatch_answer(cb, "unrecognized action")
                return
            await dispatch_answer(cb)
            sheet = toggle(
                session,
                household_id=user.household_id,
                cooked_id=action.item_id,
                item_id=action.option_index,
                today=today,
            )
            if sheet is None:
                await edit_or_resend(cb, t("plan.expired", user.lang))
                return
            await _show(cb, session, user=user, sheet=sheet, translation_llm=translation_llm)
            return

        # cooked_confirm / cooked_none
        await dispatch_answer(cb)
        result = confirm(
            session,
            household_id=user.household_id,
            cooked_id=action.item_id,
            today=today,
            now=now,
            consume=action.verb == "cooked_confirm",
        )
        if result is None:
            await edit_or_resend(cb, t("plan.expired", user.lang))
            return
        if not result.eaten_names:
            await edit_or_resend(cb, t("cooked.done_none", user.lang))
            return
        await edit_or_resend(
            cb,
            t("cooked.done", user.lang, names=", ".join(result.eaten_names)),
        )
