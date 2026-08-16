from __future__ import annotations

import json as _json
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlmodel import Session

from app import handler_support, views
from app.billing.meter import admit, commit
from app.client_set import PerUserClients
from app.cook import (
    NotEnoughItems,
    create_cook_session,
    load_cook_session,
    mark_status,
    run_cook,
)
from app.cook import (
    set_message_id as set_cook_message_id,
)
from app.cook.options import (
    DEFAULT_CUISINES,
    MEAL_TYPES,  # noqa: F401 - compatibility re-export
    SURPRISE_CUISINE,
    canonical_cuisine,
    localized_cuisines,
    localized_meal_types,
)
from app.cook.recipe_source import ChainedRecipeSource, LlmRecipeSource
from app.i18n import t
from app.models import Household, User
from app.profile_service import profile_from_household
from app.renderer import (
    CallbackButton,
    build_cook_result_keyboard,
    build_cook_round_keyboard,
)
from app.telegram_ui import to_aiogram_keyboard

_SessionFactory = Callable[[], Session]
NowProvider = Callable[[str], datetime]
log = logging.getLogger(__name__)


_noop_user_created = handler_support.noop_user_created
_request = handler_support.request


async def handle_cook(
    msg,
    *,
    session_factory: _SessionFactory,
    now_provider: NowProvider,
    on_user_created: Callable[[User], None] = _noop_user_created,
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
    ) as ctx:
        if ctx is None:
            return
        session, user = ctx.session, ctx.user
        now = now_provider(user.tz)
        cook = create_cook_session(
            session,
            household_id=user.household_id,
            chat_id=msg.chat.id,
            now=now.astimezone(UTC),
        )
        assert cook.id is not None
        keyboard = to_aiogram_keyboard(
            build_cook_round_keyboard(
                cook.id, localized_meal_types(user.lang), round_name="meal"
            )
        )
        sent = await msg.answer(t("cook.what_cooking", user.lang), reply_markup=keyboard)
        set_cook_message_id(session, cook=cook, message_id=sent.message_id)


def _cuisine_options(household: Household) -> list[str]:
    try:
        prefs = _json.loads(household.preferred_cuisines_json or "[]")
    except (TypeError, ValueError):
        prefs = []
    options: list[str] = []
    seen: set[str] = set()
    for preference in prefs:
        cuisine = canonical_cuisine(preference)
        if cuisine is None or cuisine.casefold() in seen:
            continue
        seen.add(cuisine.casefold())
        options.append(cuisine)
    if not options:
        options = list(DEFAULT_CUISINES)
    elif SURPRISE_CUISINE not in options:
        options = options[: len(DEFAULT_CUISINES) - 1] + [SURPRISE_CUISINE]
    return options[: len(DEFAULT_CUISINES)]


def _cuisine_round_keyboard(
    cook_id: int, options: list[str], *, lang: str = "en"
) -> list[list[CallbackButton]]:
    rows = build_cook_round_keyboard(
        cook_id, localized_cuisines(options, lang), round_name="cuisine"
    )
    rows.append(
        [
            CallbackButton(
                text=t("btn.more_cuisines", lang),
                callback_data=f"cookmore:{cook_id}:cuisine_full",
            )
        ]
    )
    return rows


async def _safe_edit_bot(
    bot, *, chat_id: int, message_id: int | None, text: str, keyboard=None
) -> None:
    if bot is None or message_id is None:
        return
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=keyboard,
        )
    except Exception as exc:  # noqa: BLE001 - background edit is best-effort
        log.warning("cook_edit_failed", extra={"error_class": type(exc).__name__})


async def run_cook_and_render(
    session_factory: _SessionFactory,
    *,
    user_id: int,
    household_id: int,
    user_tz: str,
    cook_id: int,
    clients: PerUserClients,
    now_provider: NowProvider,
    bot,
    translation_llm=None,
    recipe_sources=(),
) -> None:
    with session_factory() as session:
        user = session.get(User, user_id)
        if user is None:
            return
        cook = load_cook_session(session, household_id=household_id, cook_id=cook_id)
        if cook is None or cook.status != "ready":
            return
        household = session.get(Household, household_id)
        if household is None:
            mark_status(session, cook=cook, status="cancelled")
            await _safe_edit_bot(
                bot,
                chat_id=cook.chat_id,
                message_id=cook.message_id,
                text=t("cook.no_profile_body", user.lang),
            )
            return
        profile = profile_from_household(household)
        chat_id = cook.chat_id
        message_id = cook.message_id
        today = now_provider(user_tz).date()
        now = now_provider(user_tz)
        decision = admit(
            session,
            household_id=household_id,
            op="cook",
            provider=user.llm_provider,
            now=now,
        )
        if not decision.allowed:
            mark_status(session, cook=cook, status="cancelled")
            await _safe_edit_bot(
                bot,
                chat_id=chat_id,
                message_id=message_id,
                text=t("quota.degraded.cook", user.lang),
            )
            return
        selected_selection_llm = clients.selection(user)
        selected_recipe_llm = clients.recipe(user)
        selected_nutrition_llm = clients.nutrition(user)
        source = ChainedRecipeSource(
            [
                *recipe_sources,
                LlmRecipeSource(
                    recipe_llm=selected_recipe_llm, nutrition_llm=selected_nutrition_llm
                ),
            ]
        )
        try:
            cards = await run_cook(
                session,
                cook=cook,
                profile=profile,
                selection_llm=selected_selection_llm,  # type: ignore[arg-type]
                source=source,
                today=today,
            )
        except NotEnoughItems:
            mark_status(session, cook=cook, status="cancelled")
            await _safe_edit_bot(
                bot,
                chat_id=chat_id,
                message_id=message_id,
                text=t("cook.not_enough_items", user.lang),
            )
            return
        except Exception as exc:  # noqa: BLE001 - /cook must never crash the bot
            log.warning(
                "cook_pipeline_failed", extra={"error_class": type(exc).__name__}
            )
            mark_status(session, cook=cook, status="cancelled")
            await _safe_edit_bot(
                bot,
                chat_id=chat_id,
                message_id=message_id,
                text=t("cook.build_failed", user.lang),
            )
            return
        finally:
            commit(
                session,
                household_id=household_id,
                op="cook",
                provider=user.llm_provider,
                cost_micros=cook.llm_cost_micros_usd,
                now=now,
            )

        mark_status(session, cook=cook, status="done")
        view = await views.cook_result(
            session,
            cards,
            user=user,
            show_alternatives=False,
            translation_llm=translation_llm,
        )
        text = view.text
        keyboard = (
            to_aiogram_keyboard(
                build_cook_result_keyboard(
                    cook_id,
                    has_alternatives=len(cards) > 1,
                    lang=user.lang,
                    source_url=cards[0].recipe.source_url,
                )
            )
            if cards
            else None
        )
        await _safe_edit_bot(
            bot, chat_id=chat_id, message_id=message_id, text=text, keyboard=keyboard
        )


COMMANDS = (
    ("cook", handle_cook, ("session_factory", "now_provider", "on_user_created")),
)
