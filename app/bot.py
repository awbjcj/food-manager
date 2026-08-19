from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from sqlmodel import Session

from app import handler_support
from app.callback_dispatch import (
    answer as dispatch_answer,
)
from app.callback_dispatch import (
    apply as apply_callback_result,
)
from app.callbacks import CallbackContext
from app.callbacks.actions import (
    handle_callback,  # noqa: F401 - compatibility re-export
)
from app.callbacks.cook import (
    handle_cook_callback,  # noqa: F401 - compatibility re-export
)
from app.callbacks.help import (
    handle_help_callback,  # noqa: F401 - compatibility re-export
)
from app.callbacks.items import (
    handle_item_callback,  # noqa: F401 - compatibility re-export
)
from app.callbacks.plan import (
    handle_plan_callback,  # noqa: F401 - compatibility re-export
)
from app.callbacks.routes import build_callback_registry
from app.client_set import PerUserClients
from app.commands import (
    CommandError,
    parse_callback_request,
)
from app.handlers.billing import (
    COMMANDS as BILLING_COMMANDS,
)
from app.handlers.billing import (
    handle_pre_checkout,
    handle_successful_payment,
)
from app.handlers.cook import (
    COMMANDS as COOK_COMMANDS,
)
from app.handlers.cook import (
    handle_cook,  # noqa: F401 - compatibility re-export
    run_cook_and_render,  # noqa: F401 - compatibility re-export
)
from app.handlers.household import (
    COMMANDS as HOUSEHOLD_COMMANDS,
)
from app.handlers.household import (
    handle_digest_at,  # noqa: F401 - compatibility re-export
    handle_household,  # noqa: F401 - compatibility re-export
    handle_invite,  # noqa: F401 - compatibility re-export
    handle_join,  # noqa: F401 - compatibility re-export
    handle_lang,  # noqa: F401 - compatibility re-export
    handle_leave,  # noqa: F401 - compatibility re-export
    handle_remove,  # noqa: F401 - compatibility re-export
    handle_start,  # noqa: F401 - compatibility re-export
    handle_tz,  # noqa: F401 - compatibility re-export
)
from app.handlers.meta import (
    COMMANDS as META_COMMANDS,
)
from app.handlers.meta import (
    _answer_shelf_life,  # noqa: F401 - compatibility re-export
    handle_help,  # noqa: F401 - compatibility re-export
    handle_llm,  # noqa: F401 - compatibility re-export
    handle_nl_message,
    handle_photo,
    handle_prefs,  # noqa: F401 - compatibility re-export
)
from app.handlers.pantry import (
    COMMANDS as PANTRY_COMMANDS,
)
from app.handlers.pantry import (
    handle_add,  # noqa: F401 - compatibility re-export
    handle_ate,  # noqa: F401 - compatibility re-export
    handle_correct,  # noqa: F401 - compatibility re-export
    handle_correct_reply,
    handle_delete,  # noqa: F401 - compatibility re-export
    handle_list,  # noqa: F401 - compatibility re-export
    handle_pantry,
    handle_snooze,  # noqa: F401 - compatibility re-export
    handle_stats,  # noqa: F401 - compatibility re-export
    handle_toss,  # noqa: F401 - compatibility re-export
)
from app.handlers.plan import (
    COMMANDS as PLAN_COMMANDS,
)
from app.handlers.plan import (
    handle_plan,  # noqa: F401 - compatibility re-export
    handle_plan_current,
)
from app.handlers.shopping import (
    COMMANDS as SHOPPING_COMMANDS,
)
from app.handlers.shopping import (
    handle_favorites,
    handle_shopping,
)
from app.i18n import t
from app.models import User
from app.telegram_ui import to_aiogram_keyboard  # noqa: F401 - compatibility re-export

DEFAULT_TZ = "America/Detroit"
DEFAULT_DIGEST_HOUR = 8
DEFAULT_LLM_PROVIDER = "anthropic"
ALLOWED_TELEGRAM_USER_ID: int = 0
OPEN_REGISTRATION: bool = False
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


# Each handler group owns its command specs; the dispatcher only composes them.
_MESSAGE_COMMANDS: tuple[
    tuple[str, Callable[..., Awaitable[None]], tuple[str, ...]], ...
] = (
    *HOUSEHOLD_COMMANDS,
    *PANTRY_COMMANDS,
    *COOK_COMMANDS,
    *PLAN_COMMANDS,
    *SHOPPING_COMMANDS,
    *META_COMMANDS,
    *BILLING_COMMANDS,
)

_QUICK_ACCESS_COMMANDS = {
    "qa_pantry": (
        "pantry",
        handle_pantry,
        ("session_factory", "now_provider", "on_user_created", "translation_llm"),
    ),
    "qa_plan": (
        "plan",
        handle_plan_current,
        ("session_factory", "on_user_created", "translation_llm"),
    ),
    "qa_shopping": (
        "shopping",
        handle_shopping,
        ("session_factory", "now_provider", "on_user_created", "translation_llm"),
    ),
    "qa_favorites": (
        "favorites",
        handle_favorites,
        ("session_factory", "on_user_created", "translation_llm"),
    ),
}


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
    payments=None,
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
        "payments": payments,
    }

    def _bind(handler, *dep_names):
        kwargs = {name: deps[name] for name in dep_names}

        async def _registered(event):
            await handler(event, **kwargs)

        return _registered

    async def quick_access(payload: str, event) -> bool:
        route = _QUICK_ACCESS_COMMANDS.get(payload)
        if route is None:
            return False
        command, handler, dep_names = route
        command_event = event.model_copy(update={"text": f"/{command}"})
        await _bind(handler, *dep_names)(command_event)
        return True

    deps["quick_access"] = quick_access

    for _name, _handler, _dep_names in _MESSAGE_COMMANDS:
        dispatcher.message.register(_bind(_handler, *_dep_names), Command(_name))

    dispatcher.pre_checkout_query.register(
        _bind(handle_pre_checkout, "session_factory")
    )
    dispatcher.message.register(
        _bind(handle_successful_payment, "session_factory", "now_provider"),
        F.successful_payment,
    )

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
                "composer",
                "recipe_sources",
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
