"""Localized views composed above the pure synchronous renderers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from sqlmodel import Session

from app.renderer import (
    DIGEST_CAP,
    build_nl_picker_keyboard,
    render_cook_result,
    render_cooked_sheet,
    render_correct_menu,
    render_digest,
    render_favorites,
    render_ingest_reply,
    render_item_card,
    render_list,
    render_plan,
    render_recook,
    render_remove_confirm,
    render_shopping_list,
)
from app.translation_service import cached_name_translations, translate_texts


@dataclass(frozen=True)
class LocalizedView:
    text: str
    names: dict[str, str]


@dataclass(frozen=True)
class LocalizedDigestView:
    text: str
    rendered_items: list
    rendered_item_ids: list[int]
    rendered_count: int
    total_count: int
    has_more: bool
    names: dict[str, str]


@dataclass(frozen=True)
class LocalizedPicker:
    rows: list
    names: dict[str, str]


async def _names_for(
    session: Session,
    *,
    lang: str,
    texts: Iterable[str | None],
    translation_llm,
) -> dict[str, str]:
    filtered = [text for text in texts if text]
    if lang == "en" or translation_llm is None or not filtered:
        return {}
    return await translate_texts(
        session, filtered, lang=lang, llm=translation_llm
    )


def cached_names(
    session: Session, *, lang: str, texts: Iterable[str | None]
) -> dict[str, str]:
    return cached_name_translations(
        session, [text for text in texts if text], lang=lang
    )


def _digest_view(rendered, names: dict[str, str]) -> LocalizedDigestView:
    return LocalizedDigestView(
        text=rendered.text,
        rendered_items=rendered.rendered_items,
        rendered_item_ids=rendered.rendered_item_ids,
        rendered_count=rendered.rendered_count,
        total_count=rendered.total_count,
        has_more=rendered.has_more,
        names=names,
    )


async def digest(
    session: Session,
    items: list,
    *,
    user,
    today: date,
    translation_llm,
    cap: int | None = DIGEST_CAP,
) -> LocalizedDigestView:
    names = await _names_for(
        session,
        lang=user.lang,
        texts=(item.raw_name for item in items),
        translation_llm=translation_llm,
    )
    return _digest_view(
        render_digest(items, today=today, lang=user.lang, names=names, cap=cap),
        names,
    )


def digest_cached(
    session: Session,
    items: list,
    *,
    lang: str,
    today: date,
    cap: int | None = DIGEST_CAP,
) -> LocalizedDigestView:
    names = cached_names(
        session, lang=lang, texts=(item.raw_name for item in items)
    )
    return _digest_view(
        render_digest(items, today=today, lang=lang, names=names, cap=cap), names
    )


async def pantry_list(
    session: Session, items: list, *, user, today: date, translation_llm
) -> LocalizedView:
    names = await _names_for(
        session,
        lang=user.lang,
        texts=(item.raw_name for item in items),
        translation_llm=translation_llm,
    )
    return LocalizedView(
        render_list(items, today=today, lang=user.lang, names=names), names
    )


async def item_card(
    session: Session, item, *, user, today: date, translation_llm
) -> LocalizedView:
    names = await _names_for(
        session,
        lang=user.lang,
        texts=[item.raw_name],
        translation_llm=translation_llm,
    )
    return LocalizedView(
        render_item_card(item, today=today, lang=user.lang, names=names), names
    )


def item_card_cached(session: Session, item, *, lang: str, today: date) -> LocalizedView:
    names = cached_names(session, lang=lang, texts=[item.raw_name])
    return LocalizedView(
        render_item_card(item, today=today, lang=lang, names=names), names
    )


def correct_menu_cached(
    session: Session, item, *, lang: str, today: date
) -> LocalizedView:
    names = cached_names(session, lang=lang, texts=[item.raw_name])
    return LocalizedView(
        render_correct_menu(item, today=today, lang=lang, names=names), names
    )


def remove_confirm_cached(session: Session, item, *, lang: str) -> LocalizedView:
    names = cached_names(session, lang=lang, texts=[item.raw_name])
    return LocalizedView(render_remove_confirm(item, lang=lang, names=names), names)


async def nl_picker(
    session: Session, items: list, *, user, translation_llm
) -> LocalizedPicker:
    names = await _names_for(
        session,
        lang=user.lang,
        texts=(item.raw_name for item in items),
        translation_llm=translation_llm,
    )
    return LocalizedPicker(build_nl_picker_keyboard(items, names=names), names)


async def shopping(
    session: Session, items: list, *, user, translation_llm
) -> LocalizedView:
    names = await _names_for(
        session,
        lang=user.lang,
        texts=(item.name_raw for item in items),
        translation_llm=translation_llm,
    )
    return LocalizedView(render_shopping_list(items, lang=user.lang, names=names), names)


async def favorites(
    session: Session, recipes: list, *, user, translation_llm
) -> LocalizedView:
    names = await _names_for(
        session,
        lang=user.lang,
        texts=(
            text
            for recipe in recipes
            for text in (recipe.title, recipe.cuisine)
        ),
        translation_llm=translation_llm,
    )
    return LocalizedView(render_favorites(recipes, lang=user.lang, names=names), names)


async def plan(
    session: Session, rows: list, *, user, translation_llm
) -> LocalizedView:
    names = await _names_for(
        session,
        lang=user.lang,
        texts=(
            text
            for _day, candidate, _uses_expiring in rows
            for text in (candidate.recipe.title, candidate.recipe.cuisine)
        ),
        translation_llm=translation_llm,
    )
    return LocalizedView(render_plan(rows, lang=user.lang, names=names), names)


async def ingest_reply(
    session: Session, summary, *, user, today: date, translation_llm
) -> LocalizedView:
    names = await _names_for(
        session,
        lang=user.lang,
        texts=summary.inserted_item_names,
        translation_llm=translation_llm,
    )
    return LocalizedView(
        render_ingest_reply(summary, today=today, lang=user.lang, names=names), names
    )


def _cook_texts(cards: Sequence) -> Iterable[str]:
    for card in cards:
        recipe = card.recipe
        yield recipe.title
        yield recipe.cuisine
        yield recipe.method_gist
        yield from (ingredient.name for ingredient in recipe.ingredients)
        yield from (getattr(card, "shopping_list", None) or [])


async def cook_result(
    session: Session,
    cards: list,
    *,
    user,
    show_alternatives: bool,
    translation_llm,
) -> LocalizedView:
    names = await _names_for(
        session,
        lang=user.lang,
        texts=_cook_texts(cards),
        translation_llm=translation_llm,
    )
    return LocalizedView(
        render_cook_result(
            cards,
            show_alternatives=show_alternatives,
            lang=user.lang,
            names=names,
        ),
        names,
    )


async def recook(
    session: Session, recipe, *, shopping_items: list[str], user, translation_llm
) -> LocalizedView:
    texts = [
        recipe.title,
        recipe.cuisine,
        recipe.method_gist,
        *(ingredient.name for ingredient in recipe.ingredients),
        *shopping_items,
    ]
    names = await _names_for(
        session,
        lang=user.lang,
        texts=texts,
        translation_llm=translation_llm,
    )
    return LocalizedView(
        render_recook(
            recipe, shopping=shopping_items, lang=user.lang, names=names
        ),
        names,
    )


async def cooked_sheet(
    session: Session, sheet, *, user, translation_llm
) -> LocalizedView:
    names = await _names_for(
        session,
        lang=user.lang,
        texts=[sheet.recipe_title, *(c.raw_name for c in sheet.candidates)],
        translation_llm=translation_llm,
    )
    return LocalizedView(
        render_cooked_sheet(sheet, lang=user.lang, names=names), names
    )
