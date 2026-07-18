"""Typed dependencies shared by callback route handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Coroutine, Protocol, Sequence

from aiogram import Bot
from sqlmodel import Session

from app.client_set import PerUserClients
from app.cook.recipe_source import RecipeSource
from app.translation_llm import TranslationLLMClient


class CallbackUser(Protocol):
    @property
    def id(self) -> int: ...


class CallbackQuery(Protocol):
    data: str | None
    message: object | None

    @property
    def from_user(self) -> CallbackUser: ...

    async def answer(self, text: str = "", *, show_alert: bool = False) -> object: ...


SessionFactory = Callable[[], Session]
NowProvider = Callable[[str], datetime]
Spawn = Callable[[Coroutine[Any, Any, object]], object]


@dataclass(frozen=True)
class CallbackContext:
    callback: CallbackQuery
    session_factory: SessionFactory
    now_provider: NowProvider
    clients: PerUserClients
    translation_llm: TranslationLLMClient | None = None
    bot: Bot | None = None
    spawn: Spawn | None = None
    recipe_sources: Sequence[RecipeSource] = field(default_factory=tuple)
