from __future__ import annotations

import logging
from datetime import date

from aiogram.types import ForceReply

from app import handler_support, views
from app.callback_dispatch import (
    answer as dispatch_answer,
)
from app.callback_dispatch import (
    edit_or_resend,
)
from app.commands import (
    CommandError,
    parse_item_callback,
)
from app.i18n import t
from app.models import PantryItem
from app.pantry_service import (
    ListFilter,
    compute_nudge_days,
    correct_item,
    list_active,
    list_digest_due,
    mark_removed,
)
from app.renderer import (
    build_correct_menu_keyboard,
    build_digest_keyboard,
    build_item_card_keyboard,
    build_remove_confirm_keyboard,
)
from app.storage_state import shelf_life_origin
from app.telegram_ui import to_aiogram_keyboard

log = logging.getLogger(__name__)


_authorized_callback_user = handler_support.authorized_callback_user


async def handle_item_callback(
    cb, *, session_factory, now_provider, translation_llm=None
) -> None:
    # Acknowledge first, render after: every branch answers the callback before
    # any translation or edit so the button never appears to "do nothing", and
    # all rendering goes through edit_or_resend so a failed in-place edit falls
    # back to a fresh message instead of being silently swallowed.
    try:
        action = parse_item_callback(cb.data)
    except CommandError:
        await dispatch_answer(cb, "unrecognized action")
        return

    with session_factory() as session:
        user = _authorized_callback_user(session, cb.from_user.id)
        if user is None:
            await dispatch_answer(cb, "not authorized")
            return
        today = now_provider(user.tz).date()

        async def refresh() -> None:
            await _refresh_digest_message(
                cb,
                session,
                user.household_id,
                today,
                lang=user.lang,
                translation_llm=translation_llm,
            )

        async def refresh_for_origin() -> None:
            if action.back_to == "all":
                await _refresh_pantry_message(
                    cb,
                    session,
                    user.household_id,
                    today,
                    lang=user.lang,
                    translation_llm=translation_llm,
                )
                return
            await refresh()

        if action.kind == "list":
            await dispatch_answer(cb)
            await refresh_for_origin()
            return

        item_id = action.item_id
        assert item_id is not None
        item = session.get(PantryItem, item_id)
        if item is None or item.household_id != user.household_id:
            await dispatch_answer(cb, "item not found")
            await refresh_for_origin()
            return
        if item.status != "active":
            await dispatch_answer(cb, f"#{item_id} already updated")
            await refresh_for_origin()
            return

        if action.kind == "nudge":
            assert action.nudge_code is not None
            new_days = compute_nudge_days(
                current_days=item.shelf_life_days,
                origin=shelf_life_origin(item),
                today=today,
                code=action.nudge_code,
            )
            item = correct_item(
                session,
                household_id=user.household_id,
                item_id=item_id,
                days=new_days,
                today=today,
            )
            await dispatch_answer(cb, "updated")
            view = views.correct_menu_cached(session, item, lang=user.lang, today=today)
            await edit_or_resend(
                cb,
                view.text,
                to_aiogram_keyboard(
                    build_correct_menu_keyboard(item_id, lang=user.lang)
                ),
            )
            return

        if action.kind == "rmok":
            mark_removed(
                session,
                household_id=user.household_id,
                item_id=item_id,
                today=today,
            )
            await dispatch_answer(cb, "removed")
            await refresh()
            return

        if action.kind == "ctext":
            await dispatch_answer(cb)
            names = views.cached_names(session, lang=user.lang, texts=[item.raw_name])
            display_name = names.get(item.raw_name, item.raw_name)
            prompt = t(
                "correct.freetext_prompt",
                user.lang,
                id=item_id,
                name=display_name,
            )
            try:
                await cb.message.answer(prompt, reply_markup=ForceReply())
            except Exception as exc:  # noqa: BLE001 - prompt send is best-effort
                log.warning(
                    "correct_prompt_failed",
                    extra={"error_class": type(exc).__name__},
                )
            return

        # Pure view changes: acknowledge, then render the requested screen.
        await dispatch_answer(cb)
        if action.kind == "open":
            view = views.item_card_cached(session, item, lang=user.lang, today=today)
            await edit_or_resend(
                cb,
                view.text,
                to_aiogram_keyboard(
                    build_item_card_keyboard(
                        item,
                        lang=user.lang,
                        back_to=action.back_to,
                    )
                ),
            )
        elif action.kind == "corr":
            view = views.correct_menu_cached(session, item, lang=user.lang, today=today)
            await edit_or_resend(
                cb,
                view.text,
                to_aiogram_keyboard(
                    build_correct_menu_keyboard(item_id, lang=user.lang)
                ),
            )
        elif action.kind == "rm":
            view = views.remove_confirm_cached(session, item, lang=user.lang)
            await edit_or_resend(
                cb,
                view.text,
                to_aiogram_keyboard(
                    build_remove_confirm_keyboard(item_id, lang=user.lang)
                ),
            )


async def _refresh_digest_message(
    cb,
    session,
    household_id: int,
    today: date,
    *,
    lang: str = "en",
    translation_llm=None,
) -> None:
    remaining = list_digest_due(session, household_id=household_id, today=today)
    if remaining:
        view = views.digest_cached(session, remaining, lang=lang, today=today)
        keyboard = to_aiogram_keyboard(
            build_digest_keyboard(
                view.rendered_items,
                has_more=view.has_more,
                today=today,
                lang=lang,
                names=view.names,
            )
        )
        await edit_or_resend(cb, view.text, keyboard)
        return
    await edit_or_resend(cb, t("digest.pantry_clear", lang))


async def _refresh_pantry_message(
    cb,
    session,
    household_id: int,
    today: date,
    *,
    lang: str = "en",
    translation_llm=None,
) -> None:
    remaining = list_active(
        session,
        household_id=household_id,
        f=ListFilter.default(),
        today=today,
    )
    if remaining:
        view = views.digest_cached(session, remaining, lang=lang, today=today, cap=None)
        keyboard = to_aiogram_keyboard(
            build_digest_keyboard(
                view.rendered_items,
                has_more=False,
                today=today,
                lang=lang,
                names=view.names,
                back_to="all",
            )
        )
        await edit_or_resend(cb, view.text, keyboard)
        return
    await edit_or_resend(cb, t("pantry.all_clear", lang))
