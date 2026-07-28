from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from sqlmodel import Session

from app.callback_dispatch import edit_or_resend
from app.correction_service import (
    add_payload_from_json,
    apply_add,
    apply_correct,
    correct_payload_from_json,
)
from app.models import PantryItem
from app.pending_service import (
    expire_for_item,
    load_pending,
    mark_applied,
    mark_cancelled,
    utc_naive,
)
from app.renderer import (
    build_undo_add_keyboard,
    render_applied_add,
    render_applied_correction,
    render_terminal_state,
)
from app.telegram_ui import to_aiogram_keyboard

log = logging.getLogger(__name__)


async def _handle_pending_callback(
    cb,
    *,
    session: Session,
    today: date,
    household_id: int,
    pending_id: int,
    verb: str,
    lang: str = "en",
) -> None:
    pending = load_pending(session, household_id=household_id, pending_id=pending_id)
    if pending is None:
        await cb.answer("not found")
        return

    now = utc_naive(datetime.now(UTC))
    if pending.status != "pending" or pending.expires_at <= now:
        terminal = pending.status if pending.status != "pending" else "expired"
        if terminal == "expired" and pending.status == "pending":
            pending.status = "expired"
            session.add(pending)
            session.commit()
        await edit_or_resend(cb, render_terminal_state(terminal, lang=lang))
        await cb.answer(f"already {terminal}")
        return

    if verb == "cancel":
        mark_cancelled(session, pending=pending)
        session.commit()
        await edit_or_resend(cb, render_terminal_state("cancelled", lang=lang))
        await cb.answer("cancelled")
        return

    if pending.action_type == "correct":
        payload = correct_payload_from_json(pending.proposed_json)
        assert pending.item_id is not None
        item = session.get(PantryItem, pending.item_id)
        if item is None:
            mark_cancelled(session, pending=pending)
            session.commit()
            await edit_or_resend(cb, "Item gone - proposal cancelled.")
            await cb.answer("item gone")
            return
        if item.status != "active":
            mark_cancelled(session, pending=pending)
            session.commit()
            await edit_or_resend(cb, "Item is no longer active - proposal cancelled.")
            await cb.answer("item no longer active")
            return
        assert item.id is not None
        expire_for_item(
            session,
            household_id=household_id,
            item_id=item.id,
            exclude_pending_id=pending.id,
        )
        apply_correct(session, household_id=household_id, item=item, payload=payload)
        mark_applied(session, pending=pending)
        session.commit()
        await edit_or_resend(
            cb, render_applied_correction(item_id=item.id, payload=payload, lang=lang)
        )
        log.info(
            "item_action_applied",
            extra={
                "user_id": cb.from_user.id,
                "item_id": item.id,
                "action": "correct",
            },
        )
        await cb.answer("applied")
        return

    if pending.action_type != "add":
        log.warning(
            "unknown_pending_action_type", extra={"action_type": pending.action_type}
        )
        await cb.answer("unknown action")
        return
    payload = add_payload_from_json(pending.proposed_json)
    new_id = apply_add(session, household_id=household_id, payload=payload, today=today)
    mark_applied(session, pending=pending)
    session.commit()
    await edit_or_resend(
        cb,
        render_applied_add(item_id=new_id, payload=payload, lang=lang),
        to_aiogram_keyboard(build_undo_add_keyboard(item_id=new_id, lang=lang)),
    )
    log.info(
        "item_action_applied",
        extra={"user_id": cb.from_user.id, "item_id": new_id, "action": "add"},
    )
    await cb.answer("added")
