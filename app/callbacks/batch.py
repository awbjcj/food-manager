from __future__ import annotations

from typing import cast

from app import handler_support, views
from app.application.context import keyboard as to_keyboard
from app.batch_service import (
    BatchError,
    change_selection,
    create_batch,
    load_batch,
)
from app.callback_dispatch import answer, edit_or_resend
from app.commands import CommandError, parse_batch_callback
from app.i18n import t
from app.messaging.adapters.legacy import action_entry
from app.pantry_service import PantrySort
from app.renderer import CallbackButton


@action_entry
async def handle_batch_callback(cb, *, session_factory, now_provider, translation_llm=None) -> None:
    try:
        request = parse_batch_callback(cb.data or "")
    except CommandError:
        await answer(cb, "unrecognized action")
        return
    with session_factory() as session:
        user = handler_support.authorized_callback_query_user(session, cb)
        if user is None:
            await answer(cb, "not authorized")
            return
        # Answer before any database mutation or view work.
        await answer(cb)
        now = now_provider(user.tz)
        notice = ""
        try:
            if request.action == "start":
                batch = create_batch(
                    session,
                    household_id=user.household_id,
                    user_id=user.id,
                    sort_by=cast(PantrySort, request.value),
                    today=now.date(),
                    now=now,
                )
            else:
                assert request.batch_id is not None
                batch = load_batch(
                    session,
                    batch_id=request.batch_id,
                    household_id=user.household_id,
                    user_id=user.id,
                    now=now,
                )
                try:
                    batch = change_selection(
                        session,
                        batch,
                        version=request.version,
                        action=request.action,
                        value=request.value,
                        now=now,
                    )
                except BatchError as exc:
                    # A stale tap renders the current selection and fresh version;
                    # it can never confirm a different selection silently.
                    session.refresh(batch)
                    notice = t(str(exc), user.lang)
                else:
                    notice = ""
            if request.action == "start":
                view = await views.prepare_pantry_batch(
                    session, batch, user=user, now=now, translation_llm=translation_llm,
                )
            else:
                view = views.pantry_batch(session, batch, lang=user.lang)
            text, keyboard = view.text, view.rows
            if request.action != "start" and notice:
                text = notice + "\n\n" + text
        except BatchError as exc:
            text = t(str(exc), user.lang)
            keyboard = [[CallbackButton(t("btn.back", user.lang), "item:list:all")]]
        await edit_or_resend(cb, text, to_keyboard(keyboard))
