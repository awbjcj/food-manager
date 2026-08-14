from __future__ import annotations

import json as _json
import logging
from datetime import UTC, datetime

from app import handler_support, views
from app.billing.meter import admit, commit
from app.callback_dispatch import (
    answer as dispatch_answer,
)
from app.callback_dispatch import (
    edit_or_resend,
)
from app.callbacks.items import _refresh_digest_message, _refresh_pantry_message
from app.callbacks.pending import _handle_pending_callback
from app.client_set import EMPTY_CLIENTS, PerUserClients
from app.commands import (
    CommandError,
    parse_callback,
)
from app.cook import (
    ScoredCandidate,
    load_cook_session,
    load_saved,
    missing_ingredients,
    recipe_from_saved,
    recook_shopping_list,
    save_candidate,
    set_feedback,
)
from app.pantry_service import (
    NotOwnerOrMissing,
    active_pantry_names,
    list_digest_due,
    mark_eaten,
    mark_tossed,
    move_to_storage,
    snooze_item,
    undo_add,
    undo_receipt,
)
from app.renderer import (
    build_digest_keyboard,
    build_shopping_keyboard,
    render_undo_result,
)
from app.shopping_service import add_missing, check_off, list_pending
from app.telegram_ui import to_aiogram_keyboard

log = logging.getLogger(__name__)


_authorized_callback_user = handler_support.authorized_callback_user


async def handle_callback(
    cb,
    *,
    session_factory,
    now_provider,
    clients: PerUserClients = EMPTY_CLIENTS,
    translation_llm=None,
) -> None:
    try:
        action = parse_callback(cb.data)
    except CommandError:
        await cb.answer("unrecognized action")
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
        now = now_provider(user.tz)
        today = now.date()

        async def refresh_items_for_origin() -> None:
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
            await _refresh_digest_message(
                cb,
                session,
                user.household_id,
                today,
                lang=user.lang,
                translation_llm=translation_llm,
            )

        if action.verb in ("cook_like", "cook_dislike"):
            cook_id = action.item_id
            assert cook_id is not None
            cook = load_cook_session(
                session, household_id=user.household_id, cook_id=cook_id
            )
            if cook is None or cook.status != "done":
                await cb.answer("this cook session expired - start a new /cook")
                return
            verdict = "liked" if action.verb == "cook_like" else "disliked"
            set_feedback(
                session, cook=cook, feedback=verdict, now=now_provider(user.tz)
            )
            await cb.answer("got it 👍" if verdict == "liked" else "noted 👎")
            return

        if action.verb in ("cook_save", "cook_shop"):
            cook_id = action.item_id
            assert cook_id is not None
            cook = load_cook_session(
                session, household_id=user.household_id, cook_id=cook_id
            )
            if cook is None or cook.status != "done":
                await cb.answer("this cook session expired - start a new /cook")
                return
            try:
                raw_cards = _json.loads(cook.candidates_json or "[]")
                cards = [ScoredCandidate.model_validate(c) for c in raw_cards]
            except (TypeError, ValueError):
                cards = []
            if not cards:
                await cb.answer("nothing to use here")
                return
            index = cook.chosen_index or 0
            if index < 0 or index >= len(cards):
                index = 0
            candidate = cards[index].recipe
            if action.verb == "cook_save":
                result = save_candidate(
                    session,
                    household_id=user.household_id,
                    candidate=candidate,
                    now=now_provider(user.tz),
                )
                await cb.answer("already saved" if result.duplicate else "saved ★")
                return
            pantry = active_pantry_names(
                session, household_id=user.household_id, today=today
            )
            missing = missing_ingredients(
                ingredients=candidate.ingredients, pantry_normalized=pantry
            )
            add_result = add_missing(
                session,
                household_id=user.household_id,
                ingredients=missing,
                now=now_provider(user.tz),
            )
            if add_result.added:
                await cb.answer(f"added {len(add_result.added)} to shopping list")
            elif add_result.already:
                await cb.answer("already on your list")
            else:
                await cb.answer("you have everything!")
            return

        if action.verb == "shop_done":
            shopping_id = action.item_id
            assert shopping_id is not None
            ok = check_off(
                session,
                household_id=user.household_id,
                shopping_id=shopping_id,
                now=now_provider(user.tz),
            )
            remaining = list_pending(session, household_id=user.household_id)
            view = await views.shopping(
                session,
                remaining,
                user=user,
                translation_llm=translation_llm,
            )
            keyboard = (
                to_aiogram_keyboard(
                    build_shopping_keyboard(
                        [i.id for i in remaining if i.id], lang=user.lang
                    )
                )
                if remaining
                else None
            )
            await edit_or_resend(cb, view.text, keyboard)
            await cb.answer("bought ✓" if ok else "already done")
            return

        if action.verb == "fav_cook":
            recipe_id = action.item_id
            assert recipe_id is not None
            saved = load_saved(
                session, household_id=user.household_id, recipe_id=recipe_id
            )
            if saved is None:
                await cb.answer("not found")
                return
            shopping = recook_shopping_list(
                session, household_id=user.household_id, saved=saved, today=today
            )
            recipe = recipe_from_saved(saved)
            view = await views.recook(
                session,
                recipe,
                shopping_items=shopping,
                user=user,
                translation_llm=translation_llm,
            )
            await cb.message.answer(view.text)
            await cb.answer("here's the plan")
            return

        if action.verb == "show_all":
            rows = list_digest_due(session, household_id=user.household_id, today=today)
            if not rows:
                await cb.answer("nothing due")
                return
            view = views.digest_cached(
                session,
                rows,
                lang=user.lang,
                today=today,
                household_id=user.household_id,
                cap=None,
            )
            keyboard = to_aiogram_keyboard(
                build_digest_keyboard(
                    view.rendered_items,
                    has_more=False,
                    today=today,
                    lang=user.lang,
                    names=view.names,
                )
            )
            await edit_or_resend(cb, view.text, keyboard)
            await cb.answer()
            return

        if action.verb in ("apply", "cancel"):
            pending_id = action.item_id
            assert pending_id is not None
            await _handle_pending_callback(
                cb,
                session=session,
                today=today,
                household_id=user.household_id,
                pending_id=pending_id,
                verb=action.verb,
                lang=user.lang,
            )
            return

        if action.verb in ("undo_receipt", "undo_add"):
            target_id = action.item_id
            assert target_id is not None
            now = datetime.now(UTC)
            if action.verb == "undo_receipt":
                result = undo_receipt(
                    session,
                    household_id=user.household_id,
                    receipt_id=target_id,
                    now=now,
                )
            else:
                result = undo_add(
                    session,
                    household_id=user.household_id,
                    item_id=target_id,
                    now=now,
                )
            await edit_or_resend(cb, render_undo_result(result, lang=user.lang))
            await cb.answer("undone" if result.removed_ids else "nothing undone")
            return

        item_id = action.item_id
        assert item_id is not None
        try:
            if action.verb == "ate":
                result = mark_eaten(
                    session,
                    household_id=user.household_id,
                    item_id=item_id,
                    today=today,
                )
            elif action.verb == "toss":
                result = mark_tossed(
                    session,
                    household_id=user.household_id,
                    item_id=item_id,
                    today=today,
                )
            elif action.verb == "snooze2":
                result = snooze_item(
                    session,
                    household_id=user.household_id,
                    item_id=item_id,
                    today=today,
                    days=2,
                )
            elif action.verb in ("freeze", "fridge"):
                decision = admit(
                    session,
                    household_id=user.household_id,
                    op="search",
                    provider=user.llm_provider,
                    now=now,
                )
                result = await move_to_storage(
                    session,
                    household_id=user.household_id,
                    item_id=item_id,
                    state="frozen" if action.verb == "freeze" else "fridge",
                    today=today,
                    search=clients.search(user) if decision.allowed else None,
                )
                if decision.allowed:
                    commit(
                        session,
                        household_id=user.household_id,
                        op="search",
                        provider=user.llm_provider,
                        cost_micros=None,
                        now=now,
                    )
            else:
                await dispatch_answer(cb, "unrecognized action")
                return
        except NotOwnerOrMissing:
            await dispatch_answer(cb, "item not found")
            await refresh_items_for_origin()
            return
        if result.applied:
            log.info(
                "item_action_applied",
                extra={
                    "user_id": cb.from_user.id,
                    "item_id": item_id,
                    "action": action.verb,
                },
            )
        # Acknowledge before the (slow) translate+render in the refresh.
        await dispatch_answer(
            cb,
            f"#{item_id} -> {action.verb}"
            if result.applied
            else f"#{item_id} already updated",
        )
        await refresh_items_for_origin()
