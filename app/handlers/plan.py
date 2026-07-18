from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Callable

from sqlmodel import Session, select

from app.commands import (
    CommandError,
    parse_plan_arg,
)
from app.client_set import PerUserClients
from app.i18n import t
from app.cook import (
    ScoredCandidate,
)
from app.cook.recipe_source import ChainedRecipeSource, LlmRecipeSource
import app.plan_service as plan_service_mod
import app.handler_support as handler_support
import app.views as views
from app.plan_service import NotEnoughItemsToPlan, build_plan
from app.week_composer import DaySpec
from app.models import Household, MealPlan, MealPlanEntry, User
from app.telegram_ui import to_aiogram_keyboard
from app.progress import finish_progress, start_progress
from app.renderer import (
    build_plan_keyboard,
)
from app.profile_service import profile_from_household


_SessionFactory = Callable[[], Session]
NowProvider = Callable[[str], datetime]
log = logging.getLogger(__name__)


_noop_user_created = handler_support.noop_user_created
_require_today = handler_support.require_today
_request = handler_support.request


def _plan_uses_expiring(entry: MealPlanEntry) -> bool:
    try:
        return DaySpec.model_validate_json(entry.spec_json).purpose == "use_it_up"
    except ValueError:
        return False


def _plan_source(
    recipe_sources, clients: PerUserClients, user: User
) -> ChainedRecipeSource:
    recipe_llm = clients.recipe_if_configured(user)
    nutrition_llm = clients.nutrition_if_configured(user)
    llm_sources = (
        [LlmRecipeSource(recipe_llm=recipe_llm, nutrition_llm=nutrition_llm)]
        if recipe_llm is not None and nutrition_llm is not None
        else []
    )
    return ChainedRecipeSource(
        [
            *recipe_sources,
            *llm_sources,
        ]
    )


async def handle_plan(
    msg,
    *,
    session_factory: _SessionFactory,
    now_provider: NowProvider,
    composer,
    clients: PerUserClients = PerUserClients(),
    recipe_sources=(),
    on_user_created: Callable[[User], None] = _noop_user_created,
    translation_llm=None,
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
        now_provider=now_provider,
    ) as ctx:
        if ctx is None:
            return
        session, user, today = ctx.session, ctx.user, _require_today(ctx.today)
        if composer is None:
            await msg.answer("plan is not configured yet")
            return
        try:
            days = parse_plan_arg((msg.text or "").split()[1:])
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        household = session.get(Household, user.household_id)
        if household is None:
            await msg.answer("couldn't load your household profile")
            return
        profile = profile_from_household(household)
        progress = await start_progress(msg, t("plan.progress", user.lang))
        had_active = (
            session.exec(
                select(MealPlan).where(
                    MealPlan.household_id == user.household_id,
                    MealPlan.status == "active",
                )
            ).first()
            is not None
        )
        selected_composer = composer.for_provider(user.llm_provider)
        source = _plan_source(recipe_sources, clients, user)
        try:
            plan, entries = await build_plan(
                session,
                household_id=user.household_id,
                days=days,
                profile=profile,
                composer=selected_composer,
                source=source,
                today=today,
                chat_id=msg.chat.id,
                cost_ceiling_micros=plan_service_mod.PLAN_COST_CEILING_MICROS,
                created_at=datetime.now(timezone.utc),
            )
        except NotEnoughItemsToPlan:
            await finish_progress(progress, msg, t("plan.not_enough", user.lang))
            return
        except Exception as exc:  # noqa: BLE001 - /plan must never crash the bot
            log.warning(
                "plan_build_failed",
                extra={"user_id": user.telegram_id, "error_class": type(exc).__name__},
            )
            await finish_progress(
                progress, msg, "couldn't build a plan right now - try /plan again"
            )
            return
        if not entries:
            await finish_progress(progress, msg, t("plan.not_enough", user.lang))
            return

        candidates = [
            ScoredCandidate.model_validate_json(entry.recipe_json) for entry in entries
        ]
        rows = [
            (entry.date, candidate, _plan_uses_expiring(entry))
            for entry, candidate in zip(entries, candidates)
        ]
        view = await views.plan(
            session, rows, user=user, translation_llm=translation_llm
        )
        text = view.text
        if had_active:
            text = f"{text}\n{t('plan.superseded', user.lang)}"
        assert plan.id is not None
        keyboard = to_aiogram_keyboard(
            build_plan_keyboard(
                plan.id,
                [(entry.day_index, entry.date) for entry in entries],
                lang=user.lang,
            )
        )
        sent = await finish_progress(progress, msg, text, keyboard)
        plan.message_id = sent.message_id
        session.add(plan)
        session.commit()


def _plan_entry_rows(session, plan_id: int):
    entries = list(
        session.exec(
            select(MealPlanEntry)
            .where(MealPlanEntry.plan_id == plan_id)
            .order_by(MealPlanEntry.day_index)  # type: ignore[arg-type]
        ).all()
    )
    candidates = [ScoredCandidate.model_validate_json(e.recipe_json) for e in entries]
    return entries, candidates


async def _render_plan_message(session, *, plan, lang, translation_llm):
    entries, candidates = _plan_entry_rows(session, plan.id)
    rows = [
        (entry.date, candidate, _plan_uses_expiring(entry))
        for entry, candidate in zip(entries, candidates)
    ]
    view = await views.plan(
        session,
        rows,
        user=SimpleNamespace(lang=lang),
        translation_llm=translation_llm,
    )
    text = view.text
    keyboard = to_aiogram_keyboard(
        build_plan_keyboard(
            plan.id, [(entry.day_index, entry.date) for entry in entries], lang=lang
        )
    )
    return text, keyboard


COMMANDS = (
    (
        "plan",
        handle_plan,
        (
            "session_factory",
            "now_provider",
            "composer",
            "clients",
            "recipe_sources",
            "on_user_created",
            "translation_llm",
        ),
    ),
)
