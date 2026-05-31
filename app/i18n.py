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
    "item.tail.today": {"en": "today", "zh": "今天", "fr": "aujourd'hui", "es": "hoy"},
    "item.tail.expired": {"en": "expired {n}d", "zh": "已过期 {n}天",
                          "fr": "périmé {n}j", "es": "caducado hace {n}d"},
    "item.tail.days": {"en": "({n}d)", "zh": "({n}天)", "fr": "({n}j)", "es": "({n}d)"},
    "list.empty": {"en": "no items match this filter"},
    "digest.title": {"en": "Pantry digest - {weekday} {date}",
                     "zh": "食品储藏提醒 - {weekday} {date}",
                     "fr": "Inventaire du garde-manger - {weekday} {date}",
                     "es": "Resumen de despensa - {weekday} {date}"},
    "category.produce": {"en": "Produce", "zh": "果蔬", "fr": "Fruits et légumes", "es": "Frutas y verduras"},
    "category.dairy": {"en": "Dairy", "zh": "乳制品", "fr": "Produits laitiers", "es": "Lácteos"},
    "category.meat": {"en": "Meat", "zh": "肉类", "fr": "Viande", "es": "Carne"},
    "category.seafood": {"en": "Seafood", "zh": "海鲜", "fr": "Fruits de mer", "es": "Mariscos"},
    "category.bakery": {"en": "Bakery", "zh": "烘焙", "fr": "Boulangerie", "es": "Panadería"},
    "category.frozen": {"en": "Frozen", "zh": "冷冻", "fr": "Surgelés", "es": "Congelados"},
    "category.beverage": {"en": "Beverage", "zh": "饮料", "fr": "Boissons", "es": "Bebidas"},
    "category.pantry": {"en": "Pantry", "zh": "杂货", "fr": "Garde-manger", "es": "Despensa"},
    "category.other": {"en": "Other", "zh": "其他", "fr": "Autre", "es": "Otro"},
    "lang.set": {
        "en": "Language set to {lang}.",
        "zh": "语言已设置为 {lang}。",
        "fr": "Langue définie sur {lang}.",
        "es": "Idioma configurado a {lang}.",
    },
    "lang.current": {
        "en": "Current language: {lang}. Change with /lang [{choices}]",
        "zh": "当前语言：{lang}。使用 /lang [{choices}] 更改",
        "fr": "Langue actuelle : {lang}. Changez avec /lang [{choices}]",
        "es": "Idioma actual: {lang}. Cambia con /lang [{choices}]",
    },
    "cost.value": {"en": "Cost: ${amount}", "zh": "费用：${amount}",
                   "fr": "Coût : {amount} $", "es": "Costo: ${amount}"},
    "cost.unavailable": {"en": "Cost: unavailable", "zh": "费用：不可用",
                         "fr": "Coût : indisponible", "es": "Costo: no disponible"},
    "ingest.none_found": {"en": "No food items found in this receipt.",
                          "zh": "此收据中未找到食品。",
                          "fr": "Aucun aliment trouvé sur ce reçu.",
                          "es": "No se encontraron alimentos en este recibo."},
    "ingest.none_clear": {"en": "No clear food items found (skipped {n} unclear items).",
                          "zh": "未找到清晰的食品（跳过 {n} 个不明项）。",
                          "fr": "Aucun aliment clair trouvé (ignoré {n} articles).",
                          "es": "No se hallaron alimentos claros (se omitieron {n})."},
    "ingest.logged": {"en": "Logged {n} items from this receipt:",
                      "zh": "已从此收据记录 {n} 项：",
                      "fr": "{n} articles enregistrés depuis ce reçu :",
                      "es": "Se registraron {n} artículos de este recibo:"},
    "ingest.refined_mark": {"en": " ✓refined", "zh": " ✓已优化", "fr": " ✓affiné", "es": " ✓refinado"},
    "ingest.purchase_date": {"en": "Purchase date: {date}", "zh": "购买日期：{date}",
                             "fr": "Date d'achat : {date}", "es": "Fecha de compra: {date}"},
    "ingest.purchase_date_assumed": {"en": "Purchase date assumed: {date}",
                                     "zh": "假定购买日期：{date}",
                                     "fr": "Date d'achat supposée : {date}",
                                     "es": "Fecha de compra asumida: {date}"},
    "ingest.low_confidence": {"en": "Low confidence: {ids}{more} - review with /correct or /delete",
                              "zh": "低置信度：{ids}{more} - 用 /correct 或 /delete 复核",
                              "fr": "Faible confiance : {ids}{more} - vérifiez avec /correct ou /delete",
                              "es": "Baja confianza: {ids}{more} - revisa con /correct o /delete"},
    "ingest.skipped_unclear": {"en": "(skipped {n} unclear items: {names}{more})",
                               "zh": "（跳过 {n} 个不明项：{names}{more}）",
                               "fr": "(ignoré {n} articles : {names}{more})",
                               "es": "(omitidos {n} artículos: {names}{more})"},
    "ingest.skipped_excluded": {"en": "Skipped (not tracked): {names}{more}",
                                "zh": "已跳过（未跟踪）：{names}{more}",
                                "fr": "Ignoré (non suivi) : {names}{more}",
                                "es": "Omitido (sin seguimiento): {names}{more}"},
    "ingest.want_tracked": {"en": "Want one tracked? /add <name>",
                            "zh": "想跟踪某项？/add <名称>",
                            "fr": "Suivre un article ? /add <nom>",
                            "es": "¿Seguir uno? /add <nombre>"},
    "ingest.item": {"en": "  - #{id} {name} - exp {date} ({days}d){mark}",
                    "zh": "  - #{id} {name} - 到期 {date} ({days}天){mark}",
                    "fr": "  - #{id} {name} - exp {date} ({days}j){mark}",
                    "es": "  - #{id} {name} - vence {date} ({days}d){mark}"},
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
