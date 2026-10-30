from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, col, select

from app.billing.meter import admit, commit
from app.i18n import LANGS
from app.models import NameTranslation, User
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
        session.flush()
        session.connection().execute(insert(NameTranslation).values(
            lang=lang, source_text=source_text, translated_text=translated_text,
        ).on_conflict_do_update(
            index_elements=["lang", "source_text"],
            set_={"translated_text": translated_text},
        ))
        row = session.get(NameTranslation, (lang, source_text))
        if row is not None:
            session.refresh(row)


def cached_name_translations(
    session: Session,
    texts: Iterable[str],
    *,
    lang: str,
) -> dict[str, str]:
    """Return cached display translations only.

    Misses are omitted so renderers naturally fall back to English. This is the
    low-latency path for callback rendering, where a button tap should never
    wait on an LLM/network translation miss.
    """
    unique = [text for text in dict.fromkeys(texts) if text]
    if lang == "en" or not unique:
        return {}
    rows = session.exec(
        select(NameTranslation).where(
            NameTranslation.lang == lang,
            col(NameTranslation.source_text).in_(unique),
        )
    ).all()
    return {row.source_text: row.translated_text for row in rows}


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

    result = cached_name_translations(session, unique, lang=lang)
    misses = [text for text in unique if text not in result]

    if misses:
        try:
            translations, _cost = await llm.translate(texts=misses, lang=lang)
            if len(translations) != len(misses):
                raise ValueError("translation count mismatch")
            for src, dst in zip(misses, translations):
                upsert_name_translations(session, source_text=src, translations={lang: dst})
            session.commit()
            # Populate the result only after a successful commit so a failure
            # leaves every miss to fall back to English consistently.
            for src, dst in zip(misses, translations):
                result[src] = dst
        except Exception as exc:  # noqa: BLE001 - translation falls back to English, never blocks
            session.rollback()
            log.warning(
                "translation_failed_fallback_english",
                extra={"lang": lang, "count": len(misses),
                       "error_class": type(exc).__name__},
            )
            for src in misses:
                result.setdefault(src, src)  # English fallback

    return result


@dataclass
class _TranslationAttempt:
    client: TranslationLLMClient
    attempted: bool = False
    cost: int | None = None

    async def translate(self, *, texts: list[str], lang: str) -> tuple[list[str], int | None]:
        self.attempted = True
        translated, self.cost = await self.client.translate(texts=texts, lang=lang)
        return translated, self.cost


async def translate_for_user(
    session: Session,
    texts: Iterable[str],
    *,
    user: User,
    now: datetime,
    llm: TranslationLLMClient | None,
) -> dict[str, str]:
    """Translate admitted misses and meter attempts, including failed responses.

    Cached/English rendering remains free. Callers commit domain writes before
    entering this seam, since translation may roll back on a provider failure.
    """
    unique = list(dict.fromkeys(text for text in texts if text))
    cached = cached_name_translations(session, unique, lang=user.lang)
    if user.lang == "en" or llm is None or all(text in cached for text in unique):
        return cached
    provider = getattr(llm, "_default_provider", user.llm_provider)
    decision = admit(session, household_id=user.household_id, op="edit", provider=provider, now=now)
    if not decision.allowed:
        session.commit()
        return cached
    attempt = _TranslationAttempt(llm)
    try:
        return await translate_texts(session, unique, lang=user.lang, llm=attempt)
    finally:
        if attempt.attempted:
            commit(session, household_id=user.household_id, op="edit", provider=provider,
                   cost_micros=attempt.cost, now=now)
