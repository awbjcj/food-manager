from __future__ import annotations

import logging
from typing import Iterable

from sqlmodel import Session

from app.models import NameTranslation
from app.translation_llm import TranslationLLMClient

log = logging.getLogger(__name__)


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
            for src, dst in zip(misses, translations):
                session.add(
                    NameTranslation(lang=lang, source_text=src, translated_text=dst)
                )
                result[src] = dst
            session.commit()
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
