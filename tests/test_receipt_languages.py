from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlmodel import Session, create_engine, select

from app import views
from app.i18n import LANGS, t
from app.ingest_service import ingest_photo
from app.llm import LLMResult, NameTranslationItem, ParsedItem, ParseResult
from app.models import Household, NameTranslation, PantryItem
from app.renderer import render_item_card
from tests.fakes import FakeLLMClient

TODAY = date(2026, 10, 16)
NAMES = {"en": "Carrots", "zh": "胡萝卜", "fr": "Carottes", "es": "Zanahorias"}


@pytest.mark.parametrize("insert", [True, False])
async def test_recognition_caches_all_languages_for_rendering(sqlite_db_path, insert):
    parsed = ParsedItem(
        is_food=True, name="Carrots", unit="bunch", category="produce",
        est_shelf_life_days=7, confidence=0.9,
        name_translations=[NameTranslationItem(lang=lang, text=text) for lang, text in NAMES.items()],
        track_worthy=insert,
    )
    unclear = parsed.model_copy(update={"name": "Unclear carrots", "confidence": 0.1})
    # The parsed English name remains its stable identity.
    unclear.name = "Carrots?"
    unclear.name_translations = [
        NameTranslationItem(lang=lang, text=f"{text}?") for lang, text in NAMES.items()
    ]
    engine = create_engine(f"sqlite:///{sqlite_db_path.as_posix()}")
    try:
        with Session(engine) as session:
            session.add(Household(id=1, created_at=datetime(2026, 10, 16, tzinfo=UTC)))
            session.commit()
            summary = await ingest_photo(
                session, FakeLLMClient(canned=LLMResult(parse=ParseResult(items=[parsed, unclear]))),
                household_id=1, photo_file_id="languages", image_bytes=b"receipt",
                today=TODAY, scanned_at=datetime(2026, 10, 16, tzinfo=UTC),
            )
        # Fresh session proves caches persist even when no items were inserted.
        with Session(engine) as session:
            for lang in LANGS:
                if lang != "en":
                    cached = session.get(NameTranslation, (lang, "Carrots"))
                    assert cached is not None
                    assert cached.translated_text == NAMES[lang]
                view = await views.ingest_reply(
                    session, summary, user=SimpleNamespace(lang=lang),
                    today=TODAY, translation_llm=None,
                )
                assert NAMES[lang] in view.text
                assert f"{NAMES[lang]}?" in view.text
                if insert:
                    item = session.exec(select(PantryItem)).one()
                    assert item.raw_name == "Carrots" and item.normalized_name == "carrots"
                    card = await views.item_card(
                        session, item, user=SimpleNamespace(lang=lang),
                        today=TODAY, translation_llm=None,
                    )
                    assert NAMES[lang] in card.text
                    assert t("unit.bunch", lang) in card.text
                    assert t("item.detail.storage", lang, value=t("storage.default", lang)) in card.text
                    assert t("item.detail.shelf_life", lang, days=7) in card.text
                else:
                    assert session.exec(select(PantryItem)).all() == []
    finally:
        engine.dispose()


@pytest.mark.parametrize("lang", LANGS)
def test_quantity_and_details_follow_render_language(lang):
    item = SimpleNamespace(
        id=1, raw_name="Carrots", qty=2, unit="bunch", category="produce",
        shelf_life_days=7, expires_on=TODAY, storage="fridge",
    )
    text = render_item_card(item, today=TODAY, lang=lang, names={"Carrots": NAMES[lang]})
    assert f"2 {t('unit.bunch', lang)} {NAMES[lang]}" in text
    assert t("item.detail.quantity", lang, value=f"2 {t('unit.bunch', lang)}") in text
    assert t("item.detail.storage", lang, value=t("storage.fridge", lang)) in text
