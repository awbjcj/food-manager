"""Typed dependencies shared by callback route handlers."""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import Session

from app.application.context import ActionContext, Messenger
from app.client_set import PerUserClients
from app.cook.recipe_source import RecipeSource
from app.translation_llm import TranslationLLMClient

SessionFactory = Callable[[], AbstractContextManager[Session]]
NowProvider = Callable[[str], datetime]
Spawn = Callable[[Coroutine[Any, Any, object]], object]


@dataclass(frozen=True)
class CallbackContext:
    callback: ActionContext
    session_factory: SessionFactory
    now_provider: NowProvider
    clients: PerUserClients
    translation_llm: TranslationLLMClient | None = None
    bot: Messenger | None = None
    spawn: Spawn | None = None
    recipe_sources: Sequence[RecipeSource] = field(default_factory=tuple)
