from __future__ import annotations

import json as _json
import logging
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import update
from sqlmodel import Session

from app import handler_support, views
from app.billing.meter import admit, commit
from app.callback_dispatch import edit_or_resend
from app.client_set import PerUserClients
from app.commands import (
    CommandError,
    parse_callback,
)
from app.cook import (
    ScoredCandidate,
    load_cook_session,
    run_cook_more,
)
from app.cook.options import (
    MEAL_TYPES,
    MORE_CUISINES,
    localized_cuisines,
    more_cuisines,
)
from app.cook.recipe_source import ChainedRecipeSource, LlmRecipeSource
from app.handlers.cook import (
    _cuisine_options,
    _cuisine_round_keyboard,
    run_cook_and_render,
)
from app.i18n import DEFAULT_LANG, t
from app.models import CookSession, Household
from app.pending_service import (
    utc_naive,
)
from app.profile_service import profile_from_household
from app.renderer import (
    PURPOSE_OPTIONS,
    build_cook_result_keyboard,
    build_cook_round_keyboard,
)
from app.telegram_ui import to_aiogram_keyboard

# Compatibility export: callers historically imported this name from the
# callback module for the expanded cuisine menu.
SPOONACULAR_CUISINES = MORE_CUISINES

_SessionFactory = Callable[[], Session]
NowProvider = Callable[[str], datetime]
log = logging.getLogger(__name__)


_authorized_callback_user = handler_support.authorized_callback_user


async def handle_cook_callback(
    cb,
    *,
    session_factory: _SessionFactory,
    now_provider: NowProvider,
    clients: PerUserClients,
    spawn,
    bot,
    translation_llm=None,
    recipe_sources=(),
) -> None:
    try:
        action = parse_callback(cb.data or "")
    except CommandError:
        await cb.answer(t("toast.unrecognized_action", DEFAULT_LANG))
        return
    if (
        action.verb
        not in ("cook_pick", "cook_alt", "cook_more_opts", "cook_more", "cook_adjust")
        or action.item_id is None
    ):
        await cb.answer(t("toast.unrecognized_action", DEFAULT_LANG))
        return

    with session_factory() as session:
        user = _authorized_callback_user(session, cb.from_user.id)
        if user is None:
            log.info(
                "unauthorized_update_rejected",
                extra={"telegram_user_id": cb.from_user.id},
            )
            await cb.answer("not authorized", show_alert=False)
            return
        cook = load_cook_session(
            session, household_id=user.household_id, cook_id=action.item_id
        )
        if cook is None or cook.status not in ("collecting", "ready", "done"):
            await cb.answer(t("toast.cook_expired", user.lang))
            return
        now = utc_naive(now_provider(user.tz))
        if cook.status in ("collecting", "ready") and cook.expires_at <= now:
            cook.status = "expired"
            session.add(cook)
            session.commit()
            await cb.answer(t("toast.cook_expired", user.lang))
            return

        if action.verb == "cook_alt":
            try:
                raw_cards = _json.loads(cook.candidates_json or "[]")
                cards = [ScoredCandidate.model_validate(card) for card in raw_cards]
            except (TypeError, ValueError):
                cards = []
            assert cook.id is not None
            view = await views.cook_result(
                session,
                cards,
                user=user,
                show_alternatives=True,
                translation_llm=translation_llm,
            )
            await edit_or_resend(
                cb,
                view.text,
                to_aiogram_keyboard(
                    build_cook_result_keyboard(
                        cook.id,
                        has_alternatives=False,
                        lang=user.lang,
                        source_url=cards[0].recipe.source_url if cards else None,
                    )
                ),
            )
            await cb.answer(t("toast.showing_alternatives", user.lang))
            return

        if action.verb == "cook_more":
            if cook.status != "done":
                await cb.answer(t("toast.cook_in_progress", user.lang))
                return
            if cook.expires_at <= now:
                cook.status = "expired"
                session.add(cook)
                session.commit()
                await cb.answer(t("toast.cook_expired", user.lang))
                return
            decision = admit(
                session,
                household_id=user.household_id,
                op="cook",
                provider=user.llm_provider,
                now=now,
            )
            if not decision.allowed:
                await cb.answer(t("quota.degraded.more", user.lang), show_alert=True)
                return
            cost_before = cook.llm_cost_micros_usd or 0
            assert cook.id is not None
            claim = session.exec(
                update(CookSession)
                .where(
                    CookSession.id == cook.id,  # type: ignore[arg-type]
                    CookSession.household_id == user.household_id,  # type: ignore[arg-type]
                    CookSession.status == "done",  # type: ignore[arg-type]
                )
                .values(status="ready")
            )
            session.commit()
            if claim.rowcount == 0:
                await cb.answer(t("toast.already_searching", user.lang))
                return
            await cb.answer()
            cook = load_cook_session(
                session, household_id=user.household_id, cook_id=cook.id
            )
            if cook is None:
                return
            assert cook.id is not None
            try:
                try:
                    household = session.get(Household, user.household_id)
                    if household is None:
                        cards = []
                    else:
                        profile = profile_from_household(household)
                        today = now_provider(user.tz).date()
                        selected_recipe_llm = clients.recipe(user)
                        selected_nutrition_llm = clients.nutrition(user)
                        source = ChainedRecipeSource(
                            [
                                *recipe_sources,
                                LlmRecipeSource(
                                    recipe_llm=selected_recipe_llm,
                                    nutrition_llm=selected_nutrition_llm,
                                ),
                            ]
                        )
                        cards = await run_cook_more(
                            session,
                            cook=cook,
                            profile=profile,
                            source=source,
                            today=today,
                        )
                except Exception as exc:  # noqa: BLE001 - show-more must never crash the bot
                    log.warning(
                        "cook_more_failed", extra={"error_class": type(exc).__name__}
                    )
                    await edit_or_resend(
                        cb, t("cook.fetch_more_failed", user.lang)
                    )
                    return
                if not cards:
                    await edit_or_resend(cb, t("cook.no_more", user.lang))
                else:
                    view = await views.cook_result(
                        session,
                        cards,
                        user=user,
                        show_alternatives=False,
                        translation_llm=translation_llm,
                    )
                    await edit_or_resend(
                        cb,
                        view.text,
                        to_aiogram_keyboard(
                            build_cook_result_keyboard(
                                cook.id,
                                has_alternatives=len(cards) > 1,
                                lang=user.lang,
                                source_url=cards[0].recipe.source_url
                                if cards
                                else None,
                            )
                        ),
                    )
            finally:
                commit(
                    session,
                    household_id=user.household_id,
                    op="cook",
                    provider=user.llm_provider,
                    cost_micros=max(0, (cook.llm_cost_micros_usd or 0) - cost_before),
                    now=now,
                )
                cook.status = "done"
                session.add(cook)
                session.commit()
            return

        if action.verb == "cook_adjust":
            if cook.status != "done":
                await cb.answer(t("toast.cook_in_progress", user.lang))
                return
            if cook.expires_at <= now:
                cook.status = "expired"
                session.add(cook)
                session.commit()
                await cb.answer(t("toast.cook_expired", user.lang))
                return
            assert cook.id is not None
            claim = session.exec(
                update(CookSession)
                .where(
                    CookSession.id == cook.id,  # type: ignore[arg-type]
                    CookSession.household_id == user.household_id,  # type: ignore[arg-type]
                    CookSession.status == "done",  # type: ignore[arg-type]
                )
                .values(
                    status="collecting", cuisine=None, purpose=None, search_offset=0
                )
            )
            session.commit()
            if claim.rowcount == 0:
                await cb.answer(t("toast.already_cooking", user.lang))
                return
            household = session.get(Household, user.household_id)
            if household is None:
                await cb.answer(t("toast.no_household_profile", user.lang))
                return
            await edit_or_resend(
                cb,
                t("cook.which_cuisine", user.lang),
                to_aiogram_keyboard(
                    _cuisine_round_keyboard(
                        cook.id, _cuisine_options(household), lang=user.lang
                    )
                ),
            )
            await cb.answer()
            return

        if action.verb == "cook_more_opts":
            if (
                cook.status != "collecting"
                or cook.meal_type is None
                or cook.cuisine is not None
                or action.round_name != "cuisine_full"
            ):
                await cb.answer(t("toast.unrecognized_action", user.lang))
                return
            assert cook.id is not None
            household = session.get(Household, user.household_id)
            if household is None:
                await cb.answer(t("toast.no_household_profile", user.lang))
                return
            expanded_cuisines = more_cuisines(_cuisine_options(household))
            rows = build_cook_round_keyboard(
                cook.id,
                localized_cuisines(expanded_cuisines, user.lang),
                round_name="cuisine_full",
            )
            await edit_or_resend(cb, t("cook.which_cuisine", user.lang), to_aiogram_keyboard(rows))
            await cb.answer()
            return

        option_index = action.option_index
        if option_index is None:
            await cb.answer(t("toast.unrecognized_action", user.lang))
            return

        household = session.get(Household, user.household_id)
        if household is None:
            await cb.answer(t("toast.no_household_profile", user.lang))
            return

        if cook.meal_type is None:
            if action.round_name == "cuisine":
                await cb.answer(t("toast.unrecognized_action", user.lang))
                return
            if option_index < 0 or option_index >= len(MEAL_TYPES):
                await cb.answer(t("toast.unrecognized_action", user.lang))
                return
            assert cook.id is not None
            keyboard = to_aiogram_keyboard(
                _cuisine_round_keyboard(
                    cook.id, _cuisine_options(household), lang=user.lang
                )
            )
            if not await edit_or_resend(
                cb,
                t("cook.which_cuisine", user.lang),
                keyboard,
            ):
                await cb.answer(t("toast.cook_update_failed", user.lang))
                return
            cook.meal_type = MEAL_TYPES[option_index]
            session.add(cook)
            session.commit()
            await cb.answer()
            return

        if action.round_name == "meal":
            await cb.answer(t("toast.already_answered", user.lang))
            return

        if cook.cuisine is None:
            if action.round_name not in ("cuisine", "cuisine_full"):
                await cb.answer(t("toast.unrecognized_action", user.lang))
                return
            cuisine_options = (
                _cuisine_options(household)
                if action.round_name == "cuisine"
                else more_cuisines(_cuisine_options(household))
            )
            if option_index < 0 or option_index >= len(cuisine_options):
                await cb.answer(t("toast.unrecognized_action", user.lang))
                return
            chosen_cuisine = cuisine_options[option_index]
            result = session.exec(
                update(CookSession)
                .where(
                    CookSession.id == cook.id,  # type: ignore[arg-type]
                    CookSession.household_id == user.household_id,  # type: ignore[arg-type]
                    CookSession.status == "collecting",  # type: ignore[arg-type]
                    CookSession.meal_type.is_not(None),  # type: ignore[union-attr]
                    CookSession.cuisine.is_(None),  # type: ignore[union-attr]
                )
                .values(cuisine=chosen_cuisine)
            )
            session.commit()
            if result.rowcount == 0:
                await cb.answer(t("toast.already_cooking", user.lang))
                return
            assert cook.id is not None
            keyboard = to_aiogram_keyboard(
                build_cook_round_keyboard(
                    cook.id,
                    [t(key, user.lang) for _code, key in PURPOSE_OPTIONS],
                    round_name="purpose",
                )
            )
            await edit_or_resend(cb, t("cook.round.purpose", user.lang), keyboard)
            await cb.answer()
            return

        if cook.purpose is not None:
            await cb.answer()
            return

        if action.round_name != "purpose":
            await cb.answer(t("toast.unrecognized_action", user.lang))
            return

        if option_index < 0 or option_index >= len(PURPOSE_OPTIONS):
            await cb.answer(t("toast.unrecognized_action", user.lang))
            return
        chosen_purpose = PURPOSE_OPTIONS[option_index][0]
        result = session.exec(
            update(CookSession)
            .where(
                CookSession.id == cook.id,  # type: ignore[arg-type]
                CookSession.household_id == user.household_id,  # type: ignore[arg-type]
                CookSession.status == "collecting",  # type: ignore[arg-type]
                CookSession.cuisine.is_not(None),  # type: ignore[union-attr]
                CookSession.purpose.is_(None),  # type: ignore[union-attr]
            )
            .values(purpose=chosen_purpose, status="ready")
        )
        session.commit()
        if result.rowcount == 0:
            await cb.answer(t("toast.already_cooking", user.lang))
            return
        assert cook.id is not None
        cook = load_cook_session(
            session, household_id=user.household_id, cook_id=cook.id
        )
        if cook is None:
            await cb.answer(t("toast.cook_expired", user.lang))
            return
        await edit_or_resend(cb, t("cook.thinking", user.lang))
        await cb.answer()
        user_id = user.telegram_id
        household_id = user.household_id
        user_tz = user.tz
        cook_id = cook.id

    if cook_id is None:
        return
    spawn(
        run_cook_and_render(
            session_factory,
            user_id=user_id,
            household_id=household_id,
            user_tz=user_tz,
            cook_id=cook_id,
            clients=clients,
            now_provider=now_provider,
            bot=bot,
            translation_llm=translation_llm,
            recipe_sources=recipe_sources,
        )
    )
