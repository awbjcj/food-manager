import string
from datetime import date

import pytest

from app.i18n import DEFAULT_LANG, LANGS, MESSAGES, format_date, t, weekday_abbr


def test_langs_and_default():
    assert DEFAULT_LANG == "en"
    assert LANGS == ("en", "zh", "fr", "es")


def test_t_returns_requested_language():
    assert t("digest.section.today", "en") == "Today"


def test_t_falls_back_to_english_when_lang_missing():
    assert t("list.empty", "zh") == MESSAGES["list.empty"]["en"]


def test_t_interpolates_named_placeholders():
    assert t("digest.more", "en", n=3) == "... and 3 more - tap [show all]"


def test_t_unknown_lang_falls_back_to_english():
    assert t("digest.section.today", "de") == "Today"


def test_t_missing_kwarg_raises_to_surface_caller_bug():
    with pytest.raises(KeyError):
        t("digest.more", "en")  # caller forgot n= -> must surface, not swallow


def test_t_malformed_translation_falls_back_to_english(monkeypatch):
    monkeypatch.setitem(MESSAGES, "_test.key", {"en": "{n} items", "zh": "{m} items"})
    # broken zh placeholder must not crash; falls back to the English render
    assert t("_test.key", "zh", n=3) == "3 items"


def test_format_date_en_same_year_matches_legacy():
    assert format_date(date(2026, 6, 2), today=date(2026, 5, 28), lang="en") == "Jun 2"


def test_format_date_en_other_year_matches_legacy():
    assert format_date(date(2027, 6, 2), today=date(2026, 5, 28), lang="en") == "Jun 2 2027"


def test_format_date_zh_uses_localized_month():
    assert format_date(date(2026, 6, 2), today=date(2026, 5, 28), lang="zh") == "6月 2"


def test_weekday_abbr_en_matches_legacy():
    assert weekday_abbr(date(2026, 5, 28), lang="en") == "Thu"  # 2026-05-28 is a Thursday


def _placeholders(template: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(template) if f}


def test_every_key_has_english():
    for key, variants in MESSAGES.items():
        assert "en" in variants, f"missing en for {key}"


def test_translations_use_only_known_langs():
    for key, variants in MESSAGES.items():
        for lang in variants:
            assert lang in LANGS, f"{key} has unknown lang {lang!r}"


def test_translation_placeholders_match_english():
    for key, variants in MESSAGES.items():
        en_ph = _placeholders(variants["en"])
        for lang, template in variants.items():
            assert _placeholders(template) == en_ph, f"{key}/{lang} placeholder mismatch"
