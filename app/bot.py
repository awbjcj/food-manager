from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from sqlmodel import Session

from app.commands import (
    CommandError,
    parse_callback_request,
)
from app.client_set import PerUserClients
from app.callbacks import CallbackContext
from app.callbacks.routes import build_callback_registry
from app.callbacks.actions import handle_callback  # noqa: F401 - compatibility re-export
from app.callbacks.cook import handle_cook_callback  # noqa: F401 - compatibility re-export
from app.callbacks.help import handle_help_callback  # noqa: F401 - compatibility re-export
from app.callbacks.items import handle_item_callback  # noqa: F401 - compatibility re-export
from app.callbacks.plan import handle_plan_callback  # noqa: F401 - compatibility re-export
from app.i18n import t
import app.handler_support as handler_support
from app.models import User
from app.callback_dispatch import (
    answer as dispatch_answer,
    apply as apply_callback_result,
)

from app.handlers.household import (
    handle_start,
    handle_invite,
    handle_join,
    handle_household,
    handle_leave,
    handle_remove,
    handle_tz,
    handle_lang,
    handle_digest_at,
)
from app.handlers.pantry import (
    handle_list,
    handle_pantry,
    handle_add,
    handle_ate,
    handle_toss,
    handle_delete,
    handle_snooze,
    handle_correct,
    handle_correct_reply,
    handle_stats,
)
from app.handlers.shopping import (
    handle_shopping,
    handle_favorites,
)
from app.handlers.plan import (
    handle_plan,
)
from app.handlers.meta import (
    _answer_shelf_life,  # noqa: F401 - compatibility re-export
    handle_nl_message,
    handle_llm,
    handle_prefs,
    handle_help,
    handle_photo,
)
from app.handlers.cook import (
    handle_cook,
    run_cook_and_render,  # noqa: F401 - compatibility re-export
)
from app.telegram_ui import to_aiogram_keyboard  # noqa: F401 - compatibility re-export


DEFAULT_TZ = "America/Detroit"
DEFAULT_DIGEST_HOUR = 8
DEFAULT_LLM_PROVIDER = "anthropic"
ALLOWED_TELEGRAM_USER_ID: int = 0
MEAL_TYPES = ["Dinner", "Lunch", "Breakfast", "Dessert", "Snack", "Surprise me"]
DEFAULT_CUISINES = ["Italian", "Mexican", "Chinese", "American", "Surprise me"]
SPOONACULAR_CUISINES = [
    "African",
    "Asian",
    "American",
    "British",
    "Cajun",
    "Caribbean",
    "Chinese",
    "Eastern European",
    "European",
    "French",
    "German",
    "Greek",
    "Indian",
    "Irish",
    "Italian",
    "Japanese",
    "Jewish",
    "Korean",
    "Latin American",
    "Mediterranean",
    "Mexican",
    "Middle Eastern",
    "Nordic",
    "Southern",
    "Spanish",
    "Thai",
    "Vietnamese",
]

_SessionFactory = Callable[[], Session]
NowProvider = Callable[[str], datetime]
log = logging.getLogger(__name__)


AuthDecision = handler_support.AuthDecision
AuthStatus = handler_support.AuthStatus
resolve_authorization = handler_support.resolve_authorization
authorize_and_get_user = handler_support.authorize_and_get_user
_noop_user_created = handler_support.noop_user_created
_require_user = handler_support.require_user
_require_today = handler_support.require_today
_available_llm_providers = handler_support.available_llm_providers
_render_llm_status = handler_support.render_llm_status
_authorized_callback_user = handler_support.authorized_callback_user
_guard = handler_support.guard
_RequestContext = handler_support.RequestContext
_request = handler_support.request


HELP_TEXT = t("help.body", "en")


HELP_TOPICS = ("pantry", "cook", "household", "settings")


# The command roster: each row is (command name, handler, the injected deps the
# handler needs). `build_dispatcher` binds the live deps and registers each row,
# so adding a command is one line here instead of a forwarding closure plus a
# separate `register` call. The two non-command message routes (correction reply,
# photo) and the callback router are wired explicitly below because their triggers
# and dispatch differ.
_MESSAGE_COMMANDS: tuple[
    tuple[str, Callable[..., Awaitable[None]], tuple[str, ...]], ...
] = (
    ("start", handle_start, ("session_factory", "on_user_created", "bot")),
    ("invite", handle_invite, ("session_factory", "bot", "on_user_created")),
    ("join", handle_join, ("session_factory", "on_user_created", "bot")),
    ("household", handle_household, ("session_factory", "on_user_created")),
    ("leave", handle_leave, ("session_factory", "unschedule", "on_user_created")),
    ("remove", handle_remove, ("session_factory", "unschedule", "on_user_created")),
    ("tz", handle_tz, ("session_factory", "reschedule")),
    ("lang", handle_lang, ("session_factory", "on_user_created")),
    ("digest_at", handle_digest_at, ("session_factory", "reschedule")),
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
    ("cook", handle_cook, ("session_factory", "now_provider", "on_user_created")),
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
    (
        "shopping",
        handle_shopping,
        ("session_factory", "now_provider", "on_user_created", "translation_llm"),
    ),
    (
        "favorites",
        handle_favorites,
        ("session_factory", "on_user_created", "translation_llm"),
    ),
    ("llm", handle_llm, ("session_factory", "clients", "on_user_created")),
    ("prefs", handle_prefs, ("session_factory", "clients", "on_user_created")),
    ("help", handle_help, ("session_factory", "on_user_created")),
)


def build_dispatcher(
    *,
    bot: Bot,
    session_factory: _SessionFactory,
    clients: PerUserClients,
    now_provider: NowProvider,
    on_user_created: Callable[[User], None],
    reschedule: Callable[[User], None],
    unschedule: Callable[[int], None] = lambda _telegram_id: None,
    translation_llm=None,
    alerter=None,
    recipe_sources=(),
    intent_agent=None,
    composer=None,
) -> Dispatcher:
    dispatcher = Dispatcher()

    async def downloader(file_id: str) -> bytes:
        telegram_file = await bot.get_file(file_id)
        if telegram_file.file_path is None:
            raise RuntimeError("telegram file path missing")
        downloaded = await bot.download_file(telegram_file.file_path)
        if downloaded is None:
            raise RuntimeError("telegram download returned no file object")
        return downloaded.read()

    deps = {
        "session_factory": session_factory,
        "now_provider": now_provider,
        "on_user_created": on_user_created,
        "reschedule": reschedule,
        "unschedule": unschedule,
        "clients": clients,
        "translation_llm": translation_llm,
        "intent_agent": intent_agent,
        "bot": bot,
        "photo_downloader": downloader,
        "spawn": asyncio.create_task,
        "composer": composer,
        "recipe_sources": recipe_sources,
    }

    def _bind(handler, *dep_names):
        kwargs = {name: deps[name] for name in dep_names}

        async def _registered(event):
            await handler(event, **kwargs)

        return _registered

    for _name, _handler, _dep_names in _MESSAGE_COMMANDS:
        dispatcher.message.register(_bind(_handler, *_dep_names), Command(_name))

    dispatcher.message.register(
        _bind(
            handle_correct_reply,
            "session_factory",
            "now_provider",
            "clients",
            "on_user_created",
        ),
        F.text & F.reply_to_message,
    )
    dispatcher.message.register(
        _bind(
            handle_photo,
            "session_factory",
            "now_provider",
            "clients",
            "photo_downloader",
            "on_user_created",
            "spawn",
            "bot",
            "translation_llm",
        ),
        F.photo,
    )

    if intent_agent is not None:
        dispatcher.message.register(
            _bind(
                handle_nl_message,
                "session_factory",
                "now_provider",
                "intent_agent",
                "clients",
                "on_user_created",
                "translation_llm",
            ),
            F.text & ~F.text.startswith("/") & ~F.reply_to_message,
        )

    callback_registry = build_callback_registry()

    async def on_callback(callback):
        try:
            request = parse_callback_request(callback.data or "")
        except CommandError:
            await dispatch_answer(callback, "unrecognized action")
            return
        context = CallbackContext(
            callback=callback,
            session_factory=session_factory,
            now_provider=now_provider,
            clients=clients,
            translation_llm=translation_llm,
            bot=bot,
            spawn=asyncio.create_task,
            recipe_sources=recipe_sources,
        )
        result = await callback_registry.dispatch(request, context)
        await apply_callback_result(callback, result)

    async def on_error(event) -> bool:
        exc = event.exception
        log.error(
            "unhandled_update_error",
            extra={"error_class": type(exc).__name__, "error": str(exc)},
        )
        if alerter is not None:
            await alerter.alert("handler_error", f"{type(exc).__name__}: {exc}")
        return True  # handled: aiogram must not escalate

    dispatcher.errors.register(on_error)
    dispatcher.callback_query.register(on_callback)
    return dispatcher
