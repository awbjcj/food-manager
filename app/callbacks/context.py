"""Typed dependencies shared by callback route handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, Protocol, Sequence

from sqlmodel import Session

from app.client_set import PerUserClients
from app.cook.recipe_source import RecipeSource
from app.translation_llm import TranslationLLMClient


class CallbackUser(Protocol):
    id: int


class CallbackQuery(Protocol):
    data: str | None
    from_user: CallbackUser
    message: object | None

    async def answer(self, text: str = "", *, show_alert: bool = False) -> object: ...


class BotClient(Protocol):
    async def edit_message_text(self, **kwargs) -> object: ...


SessionFactory = Callable[[], Session]
NowProvider = Callable[[str], datetime]
Spawn = Callable[[Awaitable[object]], object]


@dataclass(frozen=True)
class CallbackContext:
    callback: CallbackQuery
    session_factory: SessionFactory
    now_provider: NowProvider
    clients: PerUserClients
    translation_llm: TranslationLLMClient | None = None
    bot: BotClient | None = None
    spawn: Spawn | None = None
    recipe_sources: Sequence[RecipeSource] = field(default_factory=tuple)
