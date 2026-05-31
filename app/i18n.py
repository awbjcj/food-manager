from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger(__name__)

LANGS: tuple[str, ...] = ("en", "zh", "fr", "es")
DEFAULT_LANG = "en"

# Catalog. English is mandatory for every key; other languages are optional and
# fall back to English. Keys are added incrementally by later tasks.
MESSAGES: dict[str, dict[str, str]] = {
    "digest.section.expired": {"en": "Expired", "zh": "已过期", "fr": "Périmé", "es": "Caducado"},
    "digest.section.today": {"en": "Today", "zh": "今天", "fr": "Aujourd'hui", "es": "Hoy"},
    "digest.section.tomorrow": {"en": "Tomorrow", "zh": "明天", "fr": "Demain", "es": "Mañana"},
    "digest.section.this_week": {"en": "This week", "zh": "本周", "fr": "Cette semaine", "es": "Esta semana"},
    "digest.more": {
        "en": "... and {n} more - tap [show all]",
        "zh": "... 还有 {n} 项 - 点击 [show all]",
        "fr": "... et {n} de plus - appuyez sur [show all]",
        "es": "... y {n} más - toca [show all]",
    },
    "list.empty": {"en": "no items match this filter"},
}


def t(key: str, lang: str, /, **kwargs: object) -> str:
    variants = MESSAGES[key]
    en_result = variants["en"].format(**kwargs)
    if lang == DEFAULT_LANG or lang not in variants:
        return en_result
    try:
        return variants[lang].format(**kwargs)
    except (KeyError, IndexError):
        log.warning("i18n_format_failed", extra={"key": key, "lang": lang})
        return en_result


_MONTH_ABBR: dict[str, tuple[str, ...]] = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "zh": ("1月", "2月", "3月", "4月", "5月", "6月",
           "7月", "8月", "9月", "10月", "11月", "12月"),
    "fr": ("janv.", "févr.", "mars", "avr.", "mai", "juin",
           "juil.", "août", "sept.", "oct.", "nov.", "déc."),
    "es": ("ene", "feb", "mar", "abr", "may", "jun",
           "jul", "ago", "sep", "oct", "nov", "dic"),
}

_WEEKDAY_ABBR: dict[str, tuple[str, ...]] = {
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "zh": ("周一", "周二", "周三", "周四", "周五", "周六", "周日"),
    "fr": ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."),
    "es": ("lun", "mar", "mié", "jue", "vie", "sáb", "dom"),
}


def _months(lang: str) -> tuple[str, ...]:
    return _MONTH_ABBR.get(lang, _MONTH_ABBR["en"])


def weekday_abbr(value: date, *, lang: str) -> str:
    table = _WEEKDAY_ABBR.get(lang, _WEEKDAY_ABBR["en"])
    return table[value.weekday()]


def format_date(value: date, *, today: date, lang: str) -> str:
    base = f"{_months(lang)[value.month - 1]} {value.day}"
    if value.year != today.year:
        return f"{base} {value.year}"
    return base
