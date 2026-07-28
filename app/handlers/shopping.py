from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from sqlmodel import Session

from app import handler_support, views
from app.cook import (
    list_saved,
)
from app.models import User
from app.renderer import (
    build_favorites_keyboard,
    build_shopping_keyboard,
)
from app.shopping_service import list_pending
from app.telegram_ui import to_aiogram_keyboard

_SessionFactory = Callable[[], Session]
NowProvider = Callable[[str], datetime]
log = logging.getLogger(__name__)


_noop_user_created = handler_support.noop_user_created
_request = handler_support.request


async def handle_shopping(
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
    ) as ctx:
        if ctx is None:
            return
        session, user = ctx.session, ctx.user
        items = list_pending(session, household_id=user.household_id)
        view = await views.shopping(
            session, items, user=user, translation_llm=translation_llm
        )
        keyboard = (
            to_aiogram_keyboard(
                build_shopping_keyboard([i.id for i in items if i.id], lang=user.lang)
            )
            if items
            else None
        )
        await msg.answer(view.text, reply_markup=keyboard)


async def handle_favorites(
    msg,
    *,
    session_factory: _SessionFactory,
    on_user_created: Callable[[User], None] = _noop_user_created,
    translation_llm=None,
) -> None:
    async with _request(
        msg,
        session_factory=session_factory,
        on_user_created=on_user_created,
    ) as ctx:
        if ctx is None:
            return
        session, user = ctx.session, ctx.user
        recipes = list_saved(session, household_id=user.household_id)
        view = await views.favorites(
            session, recipes, user=user, translation_llm=translation_llm
        )
        keyboard = (
            to_aiogram_keyboard(
                build_favorites_keyboard(
                    [r.id for r in recipes if r.id], lang=user.lang
                )
            )
            if recipes
            else None
        )
        await msg.answer(view.text, reply_markup=keyboard)


COMMANDS = (
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
)
