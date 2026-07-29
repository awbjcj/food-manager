"""Resolve the configured capability clients for one user's provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar, cast

from app.cook.llm import NutritionLLMClient, RecipeLLMClient, SelectionLLMClient
from app.llm import LLMClient, ProfileUpdateLLMClient, TextLLMClient
from app.shelf_life_search import ShelfLifeSearchClient
from app.translation_llm import TranslationLLMClient

INGEST_PROVIDER = "gemini"


class UserProvider(Protocol):
    @property
    def llm_provider(self) -> str: ...


T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


class ClientSelector(Protocol[T_co]):
    def for_provider(self, provider: str) -> T_co: ...


type ClientSource[T] = T | ClientSelector[T]


def resolve[T](client: ClientSource[T] | None, provider: str) -> T | None:
    """Resolve selectors by provider while preserving bare clients and ``None``."""
    selector = getattr(client, "for_provider", None)
    if callable(selector):
        return cast(T, selector(provider))
    return cast(T | None, client)


def _required[T](client: T | None, capability: str) -> T:
    if client is None:
        raise RuntimeError(f"{capability} client is not configured")
    return client


@dataclass(frozen=True)
class PerUserClients:
    """The application's LLM capability seams, resolved in one place."""

    _image: ClientSource[LLMClient] | None = None
    _text: ClientSource[TextLLMClient] | None = None
    _profile: ClientSource[ProfileUpdateLLMClient] | None = None
    _selection: ClientSource[SelectionLLMClient] | None = None
    _recipe: ClientSource[RecipeLLMClient] | None = None
    _nutrition: ClientSource[NutritionLLMClient] | None = None
    _search: ClientSource[ShelfLifeSearchClient] | None = None
    _translation: TranslationLLMClient | None = None

    @classmethod
    def create(
        cls,
        *,
        image: ClientSource[LLMClient],
        text: ClientSource[TextLLMClient],
        profile: ClientSource[ProfileUpdateLLMClient],
        selection: ClientSource[SelectionLLMClient],
        recipe: ClientSource[RecipeLLMClient],
        nutrition: ClientSource[NutritionLLMClient],
        search: ClientSource[ShelfLifeSearchClient] | None = None,
        translation: TranslationLLMClient | None = None,
    ) -> PerUserClients:
        return cls(
            _image=image,
            _text=text,
            _profile=profile,
            _selection=selection,
            _recipe=recipe,
            _nutrition=nutrition,
            _search=search,
            _translation=translation,
        )

    @classmethod
    def for_tests(
        cls,
        *,
        image=None,
        text=None,
        profile=None,
        selection=None,
        recipe=None,
        nutrition=None,
        search=None,
        translation=None,
    ) -> PerUserClients:
        return cls(
            _image=image,
            _text=text,
            _profile=profile,
            _selection=selection,
            _recipe=recipe,
            _nutrition=nutrition,
            _search=search,
            _translation=translation,
        )

    def image(self, user: UserProvider) -> LLMClient:
        return _required(resolve(self._image, user.llm_provider), "image")

    def image_for_ingest(self) -> LLMClient:
        return _required(resolve(self._image, INGEST_PROVIDER), "image")

    def text(self, user: UserProvider) -> TextLLMClient:
        return _required(resolve(self._text, user.llm_provider), "text")

    def profile(self, user: UserProvider) -> ProfileUpdateLLMClient:
        return _required(resolve(self._profile, user.llm_provider), "profile")

    def selection(self, user: UserProvider) -> SelectionLLMClient:
        return _required(resolve(self._selection, user.llm_provider), "selection")

    def recipe(self, user: UserProvider) -> RecipeLLMClient:
        return _required(resolve(self._recipe, user.llm_provider), "recipe")

    def recipe_if_configured(self, user: UserProvider) -> RecipeLLMClient | None:
        return resolve(self._recipe, user.llm_provider)

    def nutrition(self, user: UserProvider) -> NutritionLLMClient:
        return _required(resolve(self._nutrition, user.llm_provider), "nutrition")

    def nutrition_if_configured(self, user: UserProvider) -> NutritionLLMClient | None:
        return resolve(self._nutrition, user.llm_provider)

    def search(self, user: UserProvider) -> ShelfLifeSearchClient | None:
        return resolve(self._search, user.llm_provider)

    @property
    def translation(self) -> TranslationLLMClient | None:
        return self._translation

    @property
    def available_text_providers(self) -> tuple[str, ...]:
        return tuple(sorted(getattr(self._text, "available_providers", ("anthropic",))))

    @property
    def cook_configured(self) -> bool:
        return all(
            client is not None
            for client in (self._selection, self._recipe, self._nutrition)
        )


EMPTY_CLIENTS = PerUserClients()
