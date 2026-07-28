from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime

from sqlmodel import Session

from app import handler_support, views
from app.client_set import PerUserClients
from app.commands import (
    CommandError,
    parse_correct_reply_marker,
    parse_item_id_arg,
    parse_list_filter,
    parse_pantry_arg,
    parse_snooze_args,
)
from app.correction_service import (
    NullDiff,
    ProposeCorrectError,
    add_payload_to_json,
    correct_payload_to_json,
    item_snapshot_to_json,
    propose_add,
    propose_correct,
)
from app.i18n import t
from app.llm import LLMProviderNotConfigured
from app.models import PantryItem, User
from app.pantry_service import (
    ListFilter,
    NotOwnerOrMissing,
    compute_stats,
    list_active,
    list_digest_due,
    mark_eaten,
    mark_removed,
    mark_tossed,
    snooze_item,
)
from app.pending_service import (
    create_pending,
    mark_cancelled,
    set_message_id,
)
from app.progress import clear_progress, finish_progress, start_progress
from app.renderer import (
    build_apply_cancel_keyboard,
    build_digest_keyboard,
    build_item_card_keyboard,
    render_add_diff,
    render_correction_diff,
    render_stats,
)
from app.telegram_ui import to_aiogram_keyboard

_SessionFactory = Callable[[], Session]
NowProvider = Callable[[str], datetime]
log = logging.getLogger(__name__)


_noop_user_created = handler_support.noop_user_created
_require_today = handler_support.require_today
_request = handler_support.request


async def handle_list(
    msg,
    *,
    session_factory: _SessionFactory,
    now_provider: NowProvider,
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
        try:
            list_filter = parse_list_filter(list((msg.text or "").split()[1:]))
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        items = list_active(
            session, household_id=user.household_id, f=list_filter, today=today
        )
        view = await views.pantry_list(
            session,
            items,
            user=user,
            today=today,
            translation_llm=translation_llm,
        )
        await msg.answer(view.text)


async def handle_pantry(
    msg,
    *,
    session_factory: _SessionFactory,
    now_provider: NowProvider,
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
        parts = (msg.text or "").split(maxsplit=1)
        args = parts[1].split() if len(parts) == 2 else []
        try:
            mode = parse_pantry_arg(args)
        except CommandError:
            await msg.answer(t("pantry.usage", user.lang))
            return

        if isinstance(mode, int):
            item = session.get(PantryItem, mode)
            if item is None or item.household_id != user.household_id:
                await msg.answer(t("pantry.no_item", user.lang, id=mode))
                return
            if item.status != "active":
                await msg.answer(
                    t("pantry.item_inactive", user.lang, id=mode, status=item.status)
                )
                return
            view = await views.item_card(
                session,
                item,
                user=user,
                today=today,
                translation_llm=translation_llm,
            )
            await msg.answer(
                view.text,
                reply_markup=to_aiogram_keyboard(
                    build_item_card_keyboard(item, lang=user.lang, back_to="all")
                ),
            )
            return

        if mode == "digest":
            items = list_digest_due(
                session, household_id=user.household_id, today=today
            )
            back_to = "digest"
            cap = 10
            empty_key = "digest.pantry_clear"
        else:
            items = list_active(
                session,
                household_id=user.household_id,
                f=ListFilter.default(),
                today=today,
            )
            back_to = "all"
            cap = None
            empty_key = "pantry.all_clear"

        view = await views.digest(
            session,
            items,
            user=user,
            today=today,
            translation_llm=translation_llm,
            cap=cap,
        )
        if not view.text:
            await msg.answer(t(empty_key, user.lang))
            return
        keyboard = to_aiogram_keyboard(
            build_digest_keyboard(
                view.rendered_items,
                has_more=view.has_more,
                today=today,
                lang=user.lang,
                names=view.names,
                back_to=back_to,
            )
        )
        await msg.answer(view.text, reply_markup=keyboard)


async def _run_add_flow(
    msg,
    *,
    session,
    user,
    today,
    raw_text: str,
    clients: PerUserClients,
    progress,
) -> None:
    try:
        selected_text_llm = clients.text(user)
        selected_search = clients.search(user)
        proposals, _ = await propose_add(
            session,
            llm=selected_text_llm,
            household_id=user.household_id,
            user_text=raw_text,
            today=today,
            tz=user.tz,
            search=selected_search,
        )
    except LLMProviderNotConfigured:
        await finish_progress(
            progress,
            msg,
            f"LLM provider {user.llm_provider!r} is not configured. Use /llm.",
        )
        return
    except Exception as exc:  # noqa: BLE001 - /add must never crash the bot
        log.warning(
            "add_propose_failed",
            extra={
                "user_id": user.telegram_id,
                "error_class": type(exc).__name__,
            },
        )
        await finish_progress(
            progress, msg, "couldn't parse that add - try simpler wording"
        )
        return
    if not proposals:
        await finish_progress(
            progress, msg, "usage: /add <free text - name, category, expiry>"
        )
        return

    await clear_progress(progress)
    for proposal in proposals:
        pending = create_pending(
            session,
            household_id=user.household_id,
            action_type="add",
            item_id=None,
            proposed_json=add_payload_to_json(proposal.payload),
            snapshot_json=None,
            cost_micros_usd=proposal.cost_share,
            chat_id=msg.chat.id,
            now=datetime.now(UTC),
        )
        assert pending.id is not None
        text = render_add_diff(
            pending_id=pending.id, payload=proposal.payload, lang=user.lang
        )
        keyboard = to_aiogram_keyboard(
            build_apply_cancel_keyboard(pending_id=pending.id, lang=user.lang)
        )
        try:
            sent = await msg.answer(text, reply_markup=keyboard)
        except Exception as exc:  # noqa: BLE001 - message send is best-effort
            log.warning(
                "add_send_failed",
                extra={"pending_id": pending.id, "error_class": type(exc).__name__},
            )
            mark_cancelled(session, pending=pending)
            session.commit()
            continue
        set_message_id(session, pending=pending, message_id=sent.message_id)


async def handle_add(
    msg,
    *,
    session_factory: _SessionFactory,
    now_provider: NowProvider,
    clients: PerUserClients,
    on_user_created: Callable[[User], None] = _noop_user_created,
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
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await msg.answer("usage: /add <free text - name, category, expiry>")
            return
        progress = await start_progress(msg, t("progress.parsing_add", user.lang))
        await _run_add_flow(
            msg,
            session=session,
            user=user,
            today=today,
            raw_text=parts[1].strip(),
            clients=clients,
            progress=progress,
        )


async def _terminal_cmd(
    msg,
    session_factory,
    now_provider,
    *,
    fn,
    action_word,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
        now_provider=now_provider,
    ) as ctx:
        if ctx is None:
            return
        session, user, today = ctx.session, ctx.user, _require_today(ctx.today)
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2:
            await msg.answer(f"usage: /{action_word} <item_id>")
            return
        item_id: int
        try:
            item_id = parse_item_id_arg(parts[1].strip())
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        try:
            result = fn(
                session,
                household_id=user.household_id,
                item_id=item_id,
                today=today,
            )
        except NotOwnerOrMissing:
            await msg.answer(f"no item #{item_id}")
            return
        if result.applied:
            log.info(
                "item_action_applied",
                extra={
                    "user_id": user.telegram_id,
                    "item_id": item_id,
                    "action": action_word,
                },
            )
            await msg.answer(f"#{item_id} marked {action_word}")
        elif result.was_already:
            await msg.answer(f"#{item_id} was already non-active")


async def handle_ate(
    msg,
    *,
    session_factory,
    now_provider,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    await _terminal_cmd(
        msg,
        session_factory,
        now_provider,
        fn=mark_eaten,
        action_word="ate",
        on_user_created=on_user_created,
    )


async def handle_toss(
    msg,
    *,
    session_factory,
    now_provider,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    await _terminal_cmd(
        msg,
        session_factory,
        now_provider,
        fn=mark_tossed,
        action_word="toss",
        on_user_created=on_user_created,
    )


async def handle_delete(
    msg,
    *,
    session_factory,
    now_provider,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    await _terminal_cmd(
        msg,
        session_factory,
        now_provider,
        fn=mark_removed,
        action_word="delete",
        on_user_created=on_user_created,
    )


async def handle_snooze(
    msg,
    *,
    session_factory,
    now_provider,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
        now_provider=now_provider,
    ) as ctx:
        if ctx is None:
            return
        session, user, today = ctx.session, ctx.user, _require_today(ctx.today)
        item_id: int
        try:
            item_id, days = parse_snooze_args(list((msg.text or "").split()[1:]))
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        try:
            result = snooze_item(
                session,
                household_id=user.household_id,
                item_id=item_id,
                today=today,
                days=days,
            )
        except NotOwnerOrMissing:
            await msg.answer(f"no item #{item_id}")
            return
        except ValueError as exc:
            await msg.answer(str(exc))
            return
        if result.applied:
            log.info(
                "item_action_applied",
                extra={
                    "user_id": user.telegram_id,
                    "item_id": item_id,
                    "action": "snooze",
                },
            )
            await msg.answer(f"#{item_id} snoozed for {days}d")
        else:
            await msg.answer(f"#{item_id} is not active")


async def _propose_and_send_correction(
    msg,
    *,
    session: Session,
    user: User,
    item: PantryItem,
    user_text: str,
    today: date,
    clients: PerUserClients,
) -> None:
    assert item.id is not None
    item_id = item.id
    try:
        selected_text_llm = clients.text(user)
        payload, cost = await propose_correct(
            session,
            llm=selected_text_llm,
            household_id=user.household_id,
            item=item,
            user_text=user_text,
            today=today,
        )
    except LLMProviderNotConfigured:
        await msg.answer(
            f"LLM provider {user.llm_provider!r} is not configured. Use /llm."
        )
        return
    except NullDiff:
        await msg.answer("no changes detected")
        return
    except ProposeCorrectError as exc:
        await msg.answer(str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - /correct must never crash the bot
        log.warning(
            "correction_propose_failed",
            extra={
                "user_id": user.telegram_id,
                "item_id": item_id,
                "error_class": type(exc).__name__,
            },
        )
        await msg.answer("couldn't parse that correction - try simpler wording")
        return

    pending = create_pending(
        session,
        household_id=user.household_id,
        action_type="correct",
        item_id=item_id,
        proposed_json=correct_payload_to_json(payload),
        snapshot_json=item_snapshot_to_json(item),
        cost_micros_usd=cost,
        chat_id=msg.chat.id,
        now=datetime.now(UTC),
    )
    assert pending.id is not None
    text = render_correction_diff(
        pending_id=pending.id,
        payload=payload,
        item_id=item_id,
        item_raw_name=item.raw_name,
        lang=user.lang,
    )
    keyboard = to_aiogram_keyboard(
        build_apply_cancel_keyboard(pending_id=pending.id, lang=user.lang)
    )
    try:
        sent = await msg.answer(text, reply_markup=keyboard)
    except Exception as exc:  # noqa: BLE001 - message send is best-effort
        log.warning(
            "correct_send_failed",
            extra={"pending_id": pending.id, "error_class": type(exc).__name__},
        )
        mark_cancelled(session, pending=pending)
        session.commit()
        return
    set_message_id(session, pending=pending, message_id=sent.message_id)


async def handle_correct(
    msg,
    *,
    session_factory,
    now_provider,
    clients: PerUserClients,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
        now_provider=now_provider,
    ) as ctx:
        if ctx is None:
            return
        session, user, today = ctx.session, ctx.user, _require_today(ctx.today)
        parts = (msg.text or "").split(maxsplit=2)
        if len(parts) < 3 or not parts[2].strip():
            await msg.answer("usage: /correct <item_id> <free text>")
            return
        try:
            item_id = parse_item_id_arg(parts[1].strip())
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        item = session.get(PantryItem, item_id)
        if item is None or item.household_id != user.household_id:
            await msg.answer(f"no item #{item_id}")
            return
        if item.status != "active":
            await msg.answer(f"#{item_id} is {item.status}; cannot correct")
            return
        await _propose_and_send_correction(
            msg,
            session=session,
            user=user,
            item=item,
            user_text=parts[2].strip(),
            today=today,
            clients=clients,
        )


async def handle_correct_reply(
    msg,
    *,
    session_factory,
    now_provider,
    clients: PerUserClients,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    marker_text = getattr(getattr(msg, "reply_to_message", None), "text", None)
    item_id = parse_correct_reply_marker(marker_text)
    if item_id is None:
        return

    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
        now_provider=now_provider,
    ) as ctx:
        if ctx is None:
            return
        session, user, today = ctx.session, ctx.user, _require_today(ctx.today)
        item = session.get(PantryItem, item_id)
        if item is None or item.household_id != user.household_id:
            await msg.answer(f"no item #{item_id}")
            return
        if item.status != "active":
            await msg.answer(f"#{item_id} is {item.status}; cannot correct")
            return
        user_text = (msg.text or "").strip()
        if not user_text:
            await msg.answer("reply with the correction text")
            return
        await _propose_and_send_correction(
            msg,
            session=session,
            user=user,
            item=item,
            user_text=user_text,
            today=today,
            clients=clients,
        )


async def handle_stats(
    msg,
    *,
    session_factory,
    now_provider,
    on_user_created: Callable[[User], None] = _noop_user_created,
):
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
    ) as ctx:
        if ctx is None:
            return
        session, user = ctx.session, ctx.user
        now = now_provider(user.tz)
        stats = compute_stats(
            session,
            household_id=user.household_id,
            now=now.astimezone(UTC),
        )
        await msg.answer(render_stats(stats, lang=user.lang))


COMMANDS = (
    (
        "list",
        handle_list,
        ("session_factory", "now_provider", "on_user_created", "translation_llm"),
    ),
    (
        "pantry",
        handle_pantry,
        ("session_factory", "now_provider", "on_user_created", "translation_llm"),
    ),
    (
        "add",
        handle_add,
        ("session_factory", "now_provider", "clients", "on_user_created"),
    ),
    ("ate", handle_ate, ("session_factory", "now_provider", "on_user_created")),
    ("toss", handle_toss, ("session_factory", "now_provider", "on_user_created")),
    ("delete", handle_delete, ("session_factory", "now_provider", "on_user_created")),
    ("snooze", handle_snooze, ("session_factory", "now_provider", "on_user_created")),
    (
        "correct",
        handle_correct,
        ("session_factory", "now_provider", "clients", "on_user_created"),
    ),
    ("stats", handle_stats, ("session_factory", "now_provider", "on_user_created")),
)
