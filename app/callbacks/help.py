from __future__ import annotations

import logging

from app import handler_support
from app.callback_dispatch import (
    answer as dispatch_answer,
)
from app.callback_dispatch import (
    edit_or_resend,
)
from app.handlers.meta import (
    HELP_TOPICS,
    _help_topics_keyboard,
)
from app.i18n import t
from app.renderer import (
    CallbackButton,
)
from app.telegram_ui import to_aiogram_keyboard

log = logging.getLogger(__name__)


_authorized_callback_user = handler_support.authorized_callback_user


async def handle_help_callback(cb, *, session_factory) -> None:
    with session_factory() as session:
        user = _authorized_callback_user(session, cb.from_user.id)
        if user is None:
            await dispatch_answer(cb, "not authorized")
            return
        lang = user.lang
    topic = (cb.data or "").split(":", 1)[1] if ":" in (cb.data or "") else "menu"
    await dispatch_answer(cb)
    if topic in HELP_TOPICS:
        keyboard = to_aiogram_keyboard(
            [[CallbackButton(text=t("btn.help.back", lang), callback_data="help:menu")]]
        )
        await edit_or_resend(cb, t(f"help.topic.{topic}", lang), keyboard)
    else:
        await edit_or_resend(cb, t("help.overview", lang), _help_topics_keyboard(lang))
