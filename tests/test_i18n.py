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
    assert t("digest.more", "en", n=3) == "+ 3 more items"


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


def test_help_mentions_every_registered_command_in_each_language():
    from app.bot import _MESSAGE_COMMANDS

    commands = [name for name, *_ in _MESSAGE_COMMANDS]
    for lang in LANGS:
        text = t("help.body", lang)
        missing = [f"/{name}" for name in commands if f"/{name}" not in text]
        assert missing == [], f"{lang} help missing commands: {missing}"


def test_v48_card_keys_present_en():
    assert t("btn.correct", "en") == "✏️ Correct"
    assert t("btn.remove", "en") == "❌ Remove"
    assert t("btn.back_to_list", "en") == "⬅ Back to list"
    assert t("btn.back", "en") == "⬅ Back"
    assert t("btn.remove_yes", "en") == "✅ Yes, remove"
    assert t("btn.correct_other", "en") == "💬 Something else…"
    assert "[correct:#7]" in t("correct.freetext_prompt", "en", id=7, name="milk")
    assert t("remove.confirm", "en", id=3, name="spinach") == (
        "Remove #3 spinach?\nThis can't be undone here."
    )


def test_v49_keys_present_en():
    assert t("cook.round.purpose", "en") == "What's the goal?"
    assert t("purpose.use_it_up", "en") == "Use it up"
    assert t("purpose.quick", "en") == "Quick (≤30m)"
    assert t("purpose.healthy", "en") == "Healthy"
    assert t("purpose.comfort", "en") == "Comfort"
    assert t("purpose.surprise", "en") == "Surprise me"
    assert t("btn.more_recipes", "en") == "🔄 More"
    assert t("btn.adjust", "en") == "🎛 Adjust"
    assert t("btn.more_cuisines", "en") == "More cuisines »"
    assert t("cook.no_more", "en") == "No more recipes for these filters — try 🎛 Adjust."
