from __future__ import annotations

import logging
from typing import Iterable, Mapping

from sqlmodel import Session

from app.i18n import LANGS
from app.models import NameTranslation
from app.translation_llm import TranslationLLMClient

log = logging.getLogger(__name__)


def upsert_name_translations(
    session: Session,
    *,
    source_text: str,
    translations: Mapping[str, str],
) -> None:
    """Update cached display translations for one canonical English name.

    The caller controls the transaction. English is an identity render path, so
    only non-English supported languages are stored.
    """
    supported = set(LANGS)
    for lang, translated_text in translations.items():
        if lang == "en" or lang not in supported:
            continue
        translated_text = translated_text.strip()
        if not translated_text:
            continue
        row = session.get(NameTranslation, (lang, source_text))
        if row is None:
            session.add(
                NameTranslation(
                    lang=lang,
                    source_text=source_text,
                    translated_text=translated_text,
                )
            )
        else:
            row.translated_text = translated_text
            session.add(row)


async def translate_texts(
    session: Session,
    texts: Iterable[str],
    *,
    lang: str,
    llm: TranslationLLMClient,
) -> dict[str, str]:
    """Map each input string to its translation in `lang`.

    English (or any already-cached text) costs nothing. Misses are deduped and
    sent in one batched LLM call, then cached. Any failure yields an English
    (identity) result for the misses and caches nothing, so a translation outage
    never blocks the caller (e.g. the scheduled digest).

    Callers must not hold uncommitted writes on `session`: on a translation
    failure this calls `session.rollback()`, which would discard them. The bot's
    callers either only read or commit before translating.
    """
    unique = list(dict.fromkeys(texts))  # preserve order, dedupe
    if lang == "en" or not unique:
        return {text: text for text in unique}

    result: dict[str, str] = {}
    misses: list[str] = []
    for text in unique:
        row = session.get(NameTranslation, (lang, text))
        if row is not None:
            result[text] = row.translated_text
        else:
            misses.append(text)

    if misses:
        try:
            translations, _cost = await llm.translate(texts=misses, lang=lang)
            if len(translations) != len(misses):
                raise ValueError("translation count mismatch")
            session.add_all(
                NameTranslation(lang=lang, source_text=src, translated_text=dst)
                for src, dst in zip(misses, translations)
            )
            session.commit()
            # Populate the result only after a successful commit so a failure
            # leaves every miss to fall back to English consistently.
            for src, dst in zip(misses, translations):
                result[src] = dst
        except Exception as exc:
            session.rollback()
            log.warning(
                "translation_failed_fallback_english",
                extra={"lang": lang, "count": len(misses),
                       "error_class": type(exc).__name__},
            )
            for src in misses:
                result.setdefault(src, src)  # English fallback

    return result
