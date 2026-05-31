import pytest
from app.i18n import LANGS, DEFAULT_LANG, t, MESSAGES


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


def test_t_bad_placeholder_falls_back_to_english_text_not_crash():
    assert t("digest.more", "en", n=5) == "... and 5 more - tap [show all]"


from datetime import date
from app.i18n import format_date, weekday_abbr


def test_format_date_en_same_year_matches_legacy():
    assert format_date(date(2026, 6, 2), today=date(2026, 5, 28), lang="en") == "Jun 2"


def test_format_date_en_other_year_matches_legacy():
    assert format_date(date(2027, 6, 2), today=date(2026, 5, 28), lang="en") == "Jun 2 2027"


def test_format_date_zh_uses_localized_month():
    assert format_date(date(2026, 6, 2), today=date(2026, 5, 28), lang="zh") == "6月 2"


def test_weekday_abbr_en_matches_legacy():
    assert weekday_abbr(date(2026, 5, 28), lang="en") == "Thu"  # 2026-05-28 is a Thursday
