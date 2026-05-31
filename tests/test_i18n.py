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
