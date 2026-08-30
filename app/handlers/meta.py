from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from types import SimpleNamespace

from app import handler_support, views
from app.billing.meter import admit, commit
from app.cache import get_cached
from app.client_set import EMPTY_CLIENTS, PerUserClients
from app.commands import (
    CommandError,
    parse_llm_provider,
)
from app.cook.cooked_service import open_sheet
from app.cook.models import RecipeIngredient
from app.handlers.cook import handle_cook
from app.handlers.pantry import _propose_and_send_correction, _run_add_flow
from app.handlers.plan import handle_plan
from app.i18n import DEFAULT_LANG, t
from app.ingest_service import DuplicateReceipt, ingest_photo
from app.llm import LLMProviderNotConfigured
from app.models import Household, User
from app.nl_intent import MAX_CONTEXT_NAMES, match_items
from app.normalization import normalize
from app.pantry_service import (
    ListFilter,
    freeze_item,
    list_active,
    list_digest_due,
    mark_eaten,
    mark_tossed,
    snooze_item,
)
from app.plan_service import tonight_entry
from app.profile_service import profile_from_household, update_profile_from_sentence
from app.progress import clear_progress, finish_progress, start_progress
from app.refine_service import run_receipt_refine
from app.renderer import (
    CallbackButton,
    build_cooked_sheet_keyboard,
    build_digest_keyboard,
    build_shopping_keyboard,
    build_undo_keyboard,
    render_ingest_reply,
    render_profile,
)
from app.shelf_life_defaults import lookup_default
from app.shelf_life_search import resolve_search_days
from app.shopping_service import (
    add_missing,
    check_off_purchased_names,
    list_pending,
)
from app.telegram_ui import to_aiogram_keyboard

log = logging.getLogger(__name__)
HELP_TOPICS = ("pantry", "cook", "household", "settings")


_noop_user_created = handler_support.noop_user_created
_require_today = handler_support.require_today
_available_llm_providers = handler_support.available_llm_providers
_render_llm_status = handler_support.render_llm_status
_request = handler_support.request


async def handle_nl_message(
    msg,
    *,
    session_factory,
    now_provider,
    intent_agent,
    clients: PerUserClients = EMPTY_CLIENTS,
    on_user_created: Callable[[User], None] = _noop_user_created,
    translation_llm=None,
    composer=None,
    recipe_sources=(),
) -> None:
    text = (msg.text or "").strip()
    if not text or text.startswith("/"):
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
        now = now_provider(user.tz)
        decision = admit(
            session,
            household_id=user.household_id,
            op="chat",
            provider=user.llm_provider,
            now=now,
        )
        if not decision.allowed:
            await msg.answer(t("nl.hint", user.lang))
            return
        progress = await start_progress(msg, t("nl.thinking", user.lang))
        try:
            agent = intent_agent.for_provider(user.llm_provider)
            pantry = list_active(
                session,
                household_id=user.household_id,
                f=ListFilter.default(),
                today=today,
            )
            intent = await agent.parse(
                text,
                today=today,
                pantry_names=[i.normalized_name for i in pantry][:MAX_CONTEXT_NAMES],
            )
        except Exception as exc:  # noqa: BLE001 - NL must never break the bot
            commit(
                session,
                household_id=user.household_id,
                op="chat",
                provider=user.llm_provider,
                cost_micros=None,
                now=now,
            )
            log.warning(
                "nl_intent_failed",
                extra={
                    "user_id": user.telegram_id,
                    "error_class": type(exc).__name__,
                },
            )
            await finish_progress(progress, msg, t("nl.hint", user.lang))
            return
        commit(
            session,
            household_id=user.household_id,
            op="chat",
            provider=user.llm_provider,
            cost_micros=None,
            now=now,
        )
        try:
            await _dispatch_nl_intent(
                msg,
                session=session,
                session_factory=session_factory,
                now_provider=now_provider,
                user=user,
                today=today,
                text=text,
                intent=intent,
                pantry=pantry,
                clients=clients,
                translation_llm=translation_llm,
                progress=progress,
                composer=composer,
                recipe_sources=recipe_sources,
                on_user_created=on_user_created,
            )
        except Exception as exc:  # noqa: BLE001 - NL must never break the bot
            log.warning(
                "nl_dispatch_failed",
                extra={
                    "user_id": user.telegram_id,
                    "intent_kind": intent.kind,
                    "error_class": type(exc).__name__,
                },
            )
            await finish_progress(progress, msg, t("nl.hint", user.lang))


async def _apply_mark(session, *, user, item_id, action, today, clients):
    if action == "ate":
        return mark_eaten(
            session, household_id=user.household_id, item_id=item_id, today=today
        )
    if action == "tossed":
        return mark_tossed(
            session, household_id=user.household_id, item_id=item_id, today=today
        )
    if action == "snooze":
        return snooze_item(
            session, household_id=user.household_id, item_id=item_id, today=today
        )
    return await freeze_item(
        session,
        household_id=user.household_id,
        item_id=item_id,
        today=today,
        search=clients.search(user),
    )


def _nl_command_msg(msg, text: str):
    return SimpleNamespace(
        text=text,
        from_user=msg.from_user,
        chat=msg.chat,
        answer=msg.answer,
        photo=None,
        reply_to_message=None,
        bot=getattr(msg, "bot", None),
    )


async def _run_cook_from_nl(
    msg, *, session_factory, now_provider, on_user_created
) -> None:
    await handle_cook(
        _nl_command_msg(msg, "/cook"),
        session_factory=session_factory,
        now_provider=now_provider,
        on_user_created=on_user_created,
    )


async def _run_plan_from_nl(
    msg,
    *,
    session_factory,
    now_provider,
    composer,
    clients,
    recipe_sources,
    on_user_created,
    translation_llm,
    days,
) -> None:
    text = "/plan" if days is None else f"/plan {days}"
    await handle_plan(
        _nl_command_msg(msg, text),
        session_factory=session_factory,
        now_provider=now_provider,
        composer=composer,
        clients=clients,
        recipe_sources=recipe_sources,
        on_user_created=on_user_created,
        translation_llm=translation_llm,
    )


async def _dispatch_nl_intent(
    msg,
    *,
    session,
    session_factory,
    now_provider,
    user,
    today,
    text,
    intent,
    pantry,
    clients,
    translation_llm,
    progress,
    composer,
    recipe_sources,
    on_user_created,
) -> None:
    if intent.kind == "mark" and intent.mark_action and intent.item_name:
        matches = match_items(intent.item_name, pantry)
        if not matches:
            await finish_progress(
                progress, msg, t("nl.not_found", user.lang, name=intent.item_name)
            )
            return
        if len(matches) > 1:
            picker = await views.nl_picker(
                session,
                matches,
                user=user,
                translation_llm=translation_llm,
            )
            keyboard = to_aiogram_keyboard(picker.rows)
            await finish_progress(progress, msg, t("nl.which_one", user.lang), keyboard)
            return
        item = matches[0]
        assert item.id is not None
        result = await _apply_mark(
            session,
            user=user,
            item_id=item.id,
            action=intent.mark_action,
            today=today,
            clients=clients,
        )
        key = f"nl.done.{intent.mark_action}" if result.applied else "nl.already_done"
        await finish_progress(progress, msg, t(key, user.lang, name=item.raw_name))
        return

    if intent.kind == "add":
        await clear_progress(progress)
        await _run_add_flow(
            msg,
            session=session,
            user=user,
            today=today,
            raw_text=text,
            clients=clients,
            progress=None,
        )
        return
    if intent.kind == "correct" and intent.item_name:
        matches = match_items(intent.item_name, pantry)
        if not matches:
            await finish_progress(
                progress, msg, t("nl.not_found", user.lang, name=intent.item_name)
            )
            return
        if len(matches) > 1:
            picker = await views.nl_picker(
                session, matches, user=user, translation_llm=translation_llm
            )
            await finish_progress(
                progress,
                msg,
                t("nl.which_one", user.lang),
                to_aiogram_keyboard(picker.rows),
            )
            return
        now = now_provider(user.tz)
        decision = admit(
            session,
            household_id=user.household_id,
            op="edit",
            provider=user.llm_provider,
            now=now,
        )
        if not decision.allowed:
            await finish_progress(
                progress, msg, t("quota.degraded.correction", user.lang)
            )
            return
        await clear_progress(progress)
        await _propose_and_send_correction(
            msg,
            session=session,
            user=user,
            item=matches[0],
            user_text=text,
            today=today,
            clients=clients,
        )
        commit(
            session,
            household_id=user.household_id,
            op="edit",
            provider=user.llm_provider,
            cost_micros=None,
            now=now,
        )
        return
    if intent.kind == "shopping" and intent.shopping_action:
        action = intent.shopping_action
        now = now_provider(user.tz)
        if action == "show":
            items = list_pending(session, household_id=user.household_id)
            view = await views.shopping(
                session, items, user=user, translation_llm=translation_llm
            )
            keyboard = (
                to_aiogram_keyboard(
                    build_shopping_keyboard(
                        [item.id for item in items if item.id], lang=user.lang
                    )
                )
                if items
                else None
            )
            await finish_progress(progress, msg, view.text, keyboard)
            return
        if not intent.shopping_items:
            await finish_progress(progress, msg, t("nl.shopping.invalid", user.lang))
            return
        if action == "add":
            result = add_missing(
                session,
                household_id=user.household_id,
                ingredients=[
                    RecipeIngredient(name=name) for name in intent.shopping_items
                ],
                now=now,
            )
            key = "nl.shopping.added" if result.added else "nl.shopping.already"
            await finish_progress(
                progress,
                msg,
                t(key, user.lang, n=len(result.added or result.already)),
            )
            return
        removed = check_off_purchased_names(
            session,
            household_id=user.household_id,
            names=intent.shopping_items,
            now=now,
        )
        key = "nl.shopping.removed" if removed else "nl.shopping.not_found"
        await finish_progress(progress, msg, t(key, user.lang, n=len(removed)))
        return
    if intent.kind == "shelf_life_question" and intent.food:
        answer = await _answer_shelf_life(
            session,
            household_id=user.household_id,
            food=intent.food,
            lang=user.lang,
            search=clients.search(user),
        )
        await finish_progress(progress, msg, answer)
        return
    if intent.kind == "pantry_query":
        rows = list_digest_due(session, household_id=user.household_id, today=today)
        if not rows:
            await finish_progress(progress, msg, t("digest.pantry_clear", user.lang))
            return
        view = await views.digest(
            session,
            rows,
            user=user,
            today=today,
            translation_llm=translation_llm,
        )
        keyboard = to_aiogram_keyboard(
            build_digest_keyboard(
                view.rendered_items,
                has_more=view.has_more,
                today=today,
                lang=user.lang,
                names=view.names,
            )
        )
        await finish_progress(progress, msg, view.text, keyboard)
        return

    if intent.kind == "cooked":
        entry = tonight_entry(session, household_id=user.household_id, today=today)
        if entry is None:
            await finish_progress(progress, msg, t("nl.no_plan_today", user.lang))
            return
        sheet = open_sheet(
            session, household_id=user.household_id, entry=entry, today=today
        )
        view = await views.cooked_sheet(
            session, sheet, user=user, translation_llm=translation_llm
        )
        keyboard = to_aiogram_keyboard(
            build_cooked_sheet_keyboard(sheet, lang=user.lang, names=view.names)
        )
        await finish_progress(progress, msg, view.text, keyboard)
        return

    if intent.kind == "cook":
        await clear_progress(progress)
        await _run_cook_from_nl(
            msg,
            session_factory=session_factory,
            now_provider=now_provider,
            on_user_created=on_user_created,
        )
        return

    if intent.kind == "plan":
        if composer is None:
            await finish_progress(progress, msg, t("nl.hint", user.lang))
            return
        await clear_progress(progress)
        await _run_plan_from_nl(
            msg,
            session_factory=session_factory,
            now_provider=now_provider,
            composer=composer,
            clients=clients,
            recipe_sources=recipe_sources,
            on_user_created=on_user_created,
            translation_llm=translation_llm,
            days=intent.days,
        )
        return

    await finish_progress(progress, msg, t("nl.hint", user.lang))


async def handle_llm(
    msg,
    *,
    session_factory,
    clients: PerUserClients,
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
        try:
            provider = parse_llm_provider((msg.text or "").split()[1:])
        except CommandError as exc:
            await msg.answer(str(exc))
            return
        if provider is None:
            await msg.answer(_render_llm_status(user, clients))
            return
        available = _available_llm_providers(clients)
        if provider not in available:
            await msg.answer(
                f"LLM provider {provider!r} is not configured. "
                f"Available: {', '.join(available) if available else 'none'}"
            )
            return
        user.llm_provider = provider
        session.add(user)
        session.commit()
        await msg.answer(f"LLM provider set to {provider}")


async def handle_prefs(
    msg,
    *,
    session_factory,
    clients: PerUserClients,
    now_provider=None,
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
        household = session.get(Household, user.household_id)
        if household is None:
            await msg.answer("couldn't load your household profile")
            return
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await msg.answer(
                render_profile(profile_from_household(household), lang=user.lang)
            )
            return
        now = now_provider(user.tz) if now_provider is not None else datetime.now(UTC)
        decision = admit(
            session,
            household_id=user.household_id,
            op="edit",
            provider=user.llm_provider,
            now=now,
        )
        if not decision.allowed:
            await msg.answer(
                render_profile(profile_from_household(household), lang=user.lang)
                + "\n\n"
                + t("quota.degraded.profile", user.lang)
            )
            return
        try:
            selected = clients.profile(user)
            profile, cost = await update_profile_from_sentence(
                session,
                llm=selected,
                household=household,
                sentence=parts[1].strip(),
            )
        except LLMProviderNotConfigured:
            await msg.answer(
                f"LLM provider {user.llm_provider!r} is not configured. Use /llm."
            )
            return
        except Exception as exc:  # noqa: BLE001 - prefs parsing must never crash the bot
            log.warning(
                "prefs_update_failed",
                extra={
                    "user_id": user.telegram_id,
                    "error_class": type(exc).__name__,
                },
            )
            await msg.answer("couldn't update your profile - try simpler wording")
            return
        commit(
            session,
            household_id=user.household_id,
            op="edit",
            provider=user.llm_provider,
            cost_micros=cost,
            now=now,
        )
        await msg.answer(
            t("prefs.updated", user.lang)
            + "\n\n"
            + render_profile(profile, lang=user.lang)
        )


def _help_topics_keyboard(lang: str):
    topics = (
        HELP_TOPICS
        if handler_support.MULTI_TENANT_ENABLED
        else tuple(topic for topic in HELP_TOPICS if topic != "household")
    )
    return to_aiogram_keyboard(
        [
            [
                CallbackButton(
                    text=t(f"btn.help.{topic}", lang), callback_data=f"help:{topic}"
                )
                for topic in topics
            ]
        ]
    )


async def handle_help(
    msg,
    *,
    session_factory,
    on_user_created: Callable[[User], None] = _noop_user_created,
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
    ) as ctx:
        if ctx is None:
            return
        user = ctx.user
    await msg.answer(
        t("help.overview", user.lang), reply_markup=_help_topics_keyboard(user.lang)
    )


async def handle_photo(
    msg,
    *,
    session_factory,
    now_provider,
    clients: PerUserClients,
    photo_downloader: Callable[[str], Awaitable[bytes]],
    on_user_created: Callable[[User], None] = _noop_user_created,
    spawn=None,
    bot=None,
    translation_llm=None,
) -> None:
    if msg.chat.type != "private":
        with session_factory() as session:
            existing = session.get(User, msg.from_user.id)
            lang = existing.lang if existing is not None else DEFAULT_LANG
        await msg.answer(t("group.receipts_private", lang))
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
        now = now_provider(user.tz)
        decision = admit(
            session,
            household_id=user.household_id,
            op="receipt",
            provider=user.llm_provider,
            now=now,
        )
        if not decision.allowed:
            await msg.answer(
                t(
                    "quota.receipts_exhausted",
                    user.lang,
                    limit=decision.snapshot.receipts_limit,
                )
            )
            return
        if not msg.photo:
            await msg.answer("send a photo of a receipt")
            return
        file_id = msg.photo[-1].file_id
        log.info(
            "receipt_ingest_started",
            extra={"user_id": user.telegram_id, "photo_file_id": file_id},
        )
        progress = await start_progress(msg, t("progress.reading_receipt", user.lang))
        try:
            selected_llm = clients.image_for_ingest()
            selected_search = clients.search(user)
            summary = await ingest_photo(
                session,
                selected_llm,
                household_id=user.household_id,
                photo_file_id=file_id,
                image_bytes=await photo_downloader(file_id),
                today=today,
                search=selected_search,
            )
        except LLMProviderNotConfigured:
            await finish_progress(
                progress,
                msg,
                f"LLM provider {user.llm_provider!r} is not configured. Use /llm.",
            )
            return
        except DuplicateReceipt:
            await finish_progress(progress, msg, "this receipt was already logged")
            return
        except Exception as exc:  # noqa: BLE001 - ingest must never crash the bot
            log.warning(
                "receipt_ingest_failed",
                extra={
                    "user_id": user.telegram_id,
                    "photo_file_id": file_id,
                    "error_class": type(exc).__name__,
                },
            )
            await finish_progress(
                progress,
                msg,
                "couldn't read that one - try a clearer photo or /add <items> manually",
            )
            return
        commit(
            session,
            household_id=user.household_id,
            op="receipt",
            provider=user.llm_provider,
            cost_micros=summary.cost_micros_usd,
            now=now,
        )
        log.info(
            "receipt_ingest_succeeded",
            extra={
                "user_id": user.telegram_id,
                "receipt_id": summary.receipt_id,
                "inserted_food_count": summary.inserted_food_count,
            },
        )
        user_lang = user.lang
        view = await views.ingest_reply(
            session,
            summary,
            user=user,
            today=today,
            translation_llm=translation_llm,
        )
        keyboard = (
            to_aiogram_keyboard(
                build_undo_keyboard(receipt_id=summary.receipt_id, lang=user_lang)
            )
            if summary.receipt_id is not None and summary.inserted_food_count
            else None
        )
        refine_household_id = user.household_id
        refine_search = selected_search
        sent = await finish_progress(
            progress,
            msg,
            view.text,
            keyboard,
        )

    if (
        refine_search is not None
        and spawn is not None
        and bot is not None
        and summary.receipt_id is not None
        and summary.uncached_item_ids
    ):
        chat_id = msg.chat.id
        message_id = sent.message_id
        receipt_id = summary.receipt_id
        item_ids = list(summary.uncached_item_ids)

        async def _run_refine():
            refined = await run_receipt_refine(
                session_factory,
                refine_search,
                item_ids=item_ids,
                summary=summary,
                household_id=refine_household_id,
                receipt_id=receipt_id,
                today=today,
            )
            if not refined:
                return
            text = render_ingest_reply(
                summary,
                today=today,
                refined_ids=refined,
                lang=user_lang,
                names=view.names,
            )
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=to_aiogram_keyboard(
                        build_undo_keyboard(receipt_id=receipt_id, lang=user_lang)
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - background edit is best-effort
                log.warning(
                    "refine_edit_failed", extra={"error_class": type(exc).__name__}
                )

        spawn(_run_refine())


async def _answer_shelf_life(
    session, *, household_id: int, food: str, lang: str, search=None
) -> str:
    """Answer "how long does X keep?" — cache, defaults table, then web search."""
    needle = normalize(food)
    cached = get_cached(session, household_id, needle)
    if cached is not None:
        return t("nl.shelf_life", lang, name=food, days=cached.days)
    default = lookup_default(needle)
    if default is not None:
        return t("nl.shelf_life", lang, name=food, days=default.days)
    if search is not None:
        try:
            result = await search.lookup_shelf_life(name=needle, category=None)
            days = resolve_search_days(result)
            if days is not None:
                return t("nl.shelf_life", lang, name=food, days=days)
        except Exception as exc:  # noqa: BLE001 - degrade to honest unknown
            log.info(
                "nl_shelf_life_search_failed",
                extra={"error_class": type(exc).__name__},
            )
    return t("nl.shelf_life_unknown", lang, name=food)


COMMANDS = (
    ("llm", handle_llm, ("session_factory", "clients", "on_user_created")),
    (
        "prefs",
        handle_prefs,
        ("session_factory", "clients", "now_provider", "on_user_created"),
    ),
    ("help", handle_help, ("session_factory", "on_user_created")),
)
