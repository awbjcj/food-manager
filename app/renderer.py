from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.cook import RecipeCandidate, ScoredCandidate
from app.correction_service import AddPayload, CorrectPayload
from app.i18n import format_date, t, weekday_abbr
from app.ingest_service import IngestSummary
from app.models import SavedRecipe, ShoppingList
from app.pantry_service import Stats
from app.profile_service import FoodProfile
from app.storage_state import next_storage_options


def _name(names: Mapping[str, str] | None, text: str) -> str:
    return (names or {}).get(text, text)


def _fmt_date(value: date, *, today: date, lang: str = "en") -> str:
    return format_date(value, today=today, lang=lang)


URGENCY_SOON_DAYS = 3


def _urgency_icon(expires_on: date, *, today: date) -> str:
    delta = (expires_on - today).days
    if delta <= 0:
        return "🔴"
    if delta <= URGENCY_SOON_DAYS:
        return "🟡"
    return "🟢"


def _qty_prefix(qty: float, unit: str | None) -> str:
    qty_str = str(int(qty)) if float(qty).is_integer() else str(qty)
    if unit:
        return f"{qty_str} {unit} "
    if qty_str != "1":
        return f"{qty_str} "
    return ""


_STORAGE_BADGES = {"frozen": "❄️ ", "fridge": "🧊 "}


def _storage_badge(item) -> str:
    return _STORAGE_BADGES.get(getattr(item, "storage", "default"), "")


def render_item_line(item, *, today: date, lang: str = "en", names=None) -> str:
    icon = _urgency_icon(item.expires_on, today=today)
    badge = _storage_badge(item)
    qty = _qty_prefix(item.qty, item.unit)
    name = _name(names, item.raw_name)
    delta = (item.expires_on - today).days
    if delta < 0:
        tail = t("item.tail.expired", lang, n=-delta)
    elif delta == 0:
        tail = t("item.tail.today", lang)
    else:
        tail = f"{_fmt_date(item.expires_on, today=today, lang=lang)} {t('item.tail.days', lang, n=delta)}"
    return f"{icon} {badge}#{item.id} {qty}{name} - {tail}"


def _fmt_cost(micros: int | None, *, lang: str = "en") -> str:
    if micros is None:
        return t("cost.unavailable", lang)
    return t("cost.value", lang, amount=f"{micros / 1_000_000:.3f}")


def render_ingest_reply(
    summary: IngestSummary,
    *,
    today: date,
    refined_ids=frozenset(),
    lang: str = "en",
    names: Mapping[str, str] | None = None,
) -> str:
    lines: list[str] = []
    if summary.inserted_food_count == 0:
        if summary.skipped_low_confidence_count:
            lines.append(t("ingest.none_clear", lang, n=summary.skipped_low_confidence_count))
        else:
            lines.append(t("ingest.none_found", lang))
        if summary.skipped_excluded_count:
            exc_names = ", ".join(summary.skipped_excluded_names[:5])
            more = "" if len(summary.skipped_excluded_names) <= 5 else ", ..."
            lines.append(t("ingest.skipped_excluded", lang, names=exc_names, more=more))
            lines.append(t("ingest.want_tracked", lang))
        lines.append(_fmt_cost(summary.cost_micros_usd, lang=lang))
        return "\n".join(lines)

    lines.append(t("ingest.logged", lang, n=summary.inserted_food_count))
    for item_id, name, expires_on, shelf_life_days in zip(
        summary.inserted_item_ids,
        summary.inserted_item_names,
        summary.inserted_item_expires_on,
        summary.inserted_item_shelf_life_days,
    ):
        mark = t("ingest.refined_mark", lang) if item_id in refined_ids else ""
        lines.append(t(
            "ingest.item", lang,
            id=item_id,
            name=_name(names, name),
            date=_fmt_date(expires_on, today=today, lang=lang),
            days=shelf_life_days,
            mark=mark,
        ))

    if summary.purchase_date is not None and summary.purchase_date != today:
        lines.append(t("ingest.purchase_date", lang, date=_fmt_date(summary.purchase_date, today=today, lang=lang)))
    if summary.purchase_date_assumed and summary.purchase_date is not None:
        lines.append(t("ingest.purchase_date_assumed", lang, date=_fmt_date(summary.purchase_date, today=today, lang=lang)))

    if summary.low_confidence_inserted_ids:
        ids = ", ".join(f"#{item_id}" for item_id in summary.low_confidence_inserted_ids[:5])
        more = "" if len(summary.low_confidence_inserted_ids) <= 5 else " ..."
        lines.append(t("ingest.low_confidence", lang, ids=ids, more=more))

    if summary.skipped_low_confidence_count:
        skipped_names = ", ".join(summary.skipped_low_confidence_names[:3])
        more = "" if len(summary.skipped_low_confidence_names) <= 3 else ", ..."
        lines.append(t("ingest.skipped_unclear", lang, n=summary.skipped_low_confidence_count, names=skipped_names, more=more))

    if summary.skipped_excluded_count:
        exc_names = ", ".join(summary.skipped_excluded_names[:5])
        more = "" if len(summary.skipped_excluded_names) <= 5 else ", ..."
        lines.append(t("ingest.skipped_excluded", lang, names=exc_names, more=more))
        lines.append(t("ingest.want_tracked", lang))

    lines.append(_fmt_cost(summary.cost_micros_usd, lang=lang))
    return "\n".join(lines)


@dataclass
class CallbackButton:
    text: str
    callback_data: str | None = None
    url: str | None = None


@dataclass
class DigestRender:
    text: str
    rendered_items: list = field(default_factory=list)
    rendered_item_ids: list[int] = field(default_factory=list)
    rendered_count: int = 0
    total_count: int = 0
    has_more: bool = False


DIGEST_CAP = 10


def render_digest(
    items: list,
    *,
    today: date,
    lang: str = "en",
    names=None,
    cap: int | None = DIGEST_CAP,
    tonight: str | None = None,
) -> DigestRender:
    total = len(items)
    if total == 0:
        return DigestRender(text="", rendered_count=0, total_count=0, has_more=False)

    capped = items if cap is None else items[:cap]
    buckets: dict[str, list] = {"expired": [], "today": [], "tomorrow": [], "this_week": []}
    for item in capped:
        if item.expires_on < today:
            buckets["expired"].append(item)
        elif item.expires_on == today:
            buckets["today"].append(item)
        elif item.expires_on == today + timedelta(days=1):
            buckets["tomorrow"].append(item)
        else:
            buckets["this_week"].append(item)

    def line_for(item) -> str:
        return "  " + render_item_line(item, today=today, lang=lang, names=names)

    title = t("digest.title", lang, weekday=weekday_abbr(today, lang=lang), date=_fmt_date(today, today=today, lang=lang))
    lines = [title, ""]
    for key in ("expired", "today", "tomorrow", "this_week"):
        if buckets[key]:
            lines.append(f"{t(f'digest.section.{key}', lang)} ({len(buckets[key])})")
            lines.extend(line_for(item) for item in buckets[key])
            lines.append("")

    has_more = cap is not None and total > cap
    if has_more:
        assert cap is not None  # narrowed by has_more; keeps `total - cap` type-safe
        lines.append(t("digest.more", lang, n=total - cap))

    text = "\n".join(lines).rstrip()
    if tonight:
        text = f"{text}\n{t('digest.tonight', lang, dish=tonight)}"

    return DigestRender(
        text=text,
        rendered_items=list(capped),
        rendered_item_ids=[item.id for item in capped],
        rendered_count=len(capped),
        total_count=total,
        has_more=has_more,
    )


def build_digest_keyboard(
    items: list, *, has_more: bool, today: date, lang: str = "en", names=None, back_to: str = "digest"
) -> list[list[CallbackButton]]:
    def _open_data(item_id: int) -> str:
        if back_to == "all":
            return f"item:open:{item_id}:all"
        return f"item:open:{item_id}"

    buttons = [
        CallbackButton(
            text=f"{_urgency_icon(item.expires_on, today=today)} #{item.id} {_name(names, item.raw_name)}",
            callback_data=_open_data(item.id),
        )
        for item in items
    ]
    rows: list[list[CallbackButton]] = [
        buttons[i:i + 2] for i in range(0, len(buttons), 2)
    ]
    if has_more:
        rows.append([CallbackButton(text=t("btn.show_all", lang), callback_data="show:all")])
    return rows


def render_item_card(item, *, today: date, lang: str = "en", names=None) -> str:
    return render_item_line(item, today=today, lang=lang, names=names)


def render_correct_menu(item, *, today: date, lang: str = "en", names=None) -> str:
    return t(
        "correct.menu_header",
        lang,
        id=item.id,
        name=_name(names, item.raw_name),
        days=item.shelf_life_days,
        date=_fmt_date(item.expires_on, today=today, lang=lang),
    )


def render_remove_confirm(item, *, lang: str = "en", names=None) -> str:
    return t("remove.confirm", lang, id=item.id, name=_name(names, item.raw_name))


def build_item_card_keyboard(item, *, lang: str = "en", back_to: str = "digest") -> list[list[CallbackButton]]:
    item_id = item.id
    back_data = "item:list:all" if back_to == "all" else "item:list"
    act_suffix = ":all" if back_to == "all" else ""
    rows: list[list[CallbackButton]] = [
        [
            CallbackButton(text=t("btn.ate", lang), callback_data=f"act:ate:{item_id}{act_suffix}"),
            CallbackButton(text=t("btn.tossed", lang), callback_data=f"act:toss:{item_id}{act_suffix}"),
        ]
    ]
    second = [
        CallbackButton(text=t("btn.snooze2", lang), callback_data=f"act:snooze2:{item_id}{act_suffix}")
    ]
    # Forward-only storage moves (default -> fridge -> frozen).
    _STORAGE_BUTTONS = {
        "fridge": ("btn.fridge", f"act:fridge:{item_id}{act_suffix}"),
        "frozen": ("btn.freeze", f"act:freeze:{item_id}{act_suffix}"),
    }
    for target in next_storage_options(getattr(item, "storage", "default")):
        key, data = _STORAGE_BUTTONS[target]
        second.append(CallbackButton(text=t(key, lang), callback_data=data))
    rows.append(second)
    rows.append([
        CallbackButton(text=t("btn.correct", lang), callback_data=f"item:corr:{item_id}"),
        CallbackButton(text=t("btn.remove", lang), callback_data=f"item:rm:{item_id}"),
    ])
    rows.append([CallbackButton(text=t("btn.back_to_list", lang), callback_data=back_data)])
    return rows


def build_correct_menu_keyboard(item_id: int, *, lang: str = "en") -> list[list[CallbackButton]]:
    return [
        [
            CallbackButton(text=t("btn.nudge_plus_week", lang), callback_data=f"item:nudge:{item_id}:p7"),
            CallbackButton(text=t("btn.nudge_plus_3d", lang), callback_data=f"item:nudge:{item_id}:p3"),
        ],
        [
            CallbackButton(text=t("btn.nudge_minus_3d", lang), callback_data=f"item:nudge:{item_id}:m3"),
            CallbackButton(text=t("btn.nudge_use_today", lang), callback_data=f"item:nudge:{item_id}:today"),
        ],
        [CallbackButton(text=t("btn.correct_other", lang), callback_data=f"item:ctext:{item_id}")],
        [CallbackButton(text=t("btn.back", lang), callback_data=f"item:open:{item_id}")],
    ]


def build_remove_confirm_keyboard(item_id: int, *, lang: str = "en") -> list[list[CallbackButton]]:
    return [[
        CallbackButton(text=t("btn.remove_yes", lang), callback_data=f"item:rmok:{item_id}"),
        CallbackButton(text=t("btn.cancel", lang), callback_data=f"item:open:{item_id}"),
    ]]


# TODO(user): tune correction/add diff wording and field order against the
# messages you actually want to read in Telegram.
def render_correction_diff(
    *,
    pending_id: int,
    payload: CorrectPayload,
    item_id: int,
    item_raw_name: str,
    lang: str = "en",
) -> str:
    lines = [t("correction.header", lang, item_id=item_id, item_raw_name=item_raw_name)]
    for field_name in ("name", "category", "expires_on", "shelf_life_days"):
        change = payload.diff.get(field_name)
        if change is None:
            continue
        suffix = ""
        if field_name == "shelf_life_days" and payload.back_computed_days:
            suffix = t("correction.back_computed", lang)
        lines.append(t("correction.field", lang,
                       field_name=field_name, old=change["old"], new=change["new"], suffix=suffix))
    lines.append(t("correction.cache", lang, cache_action=payload.cache_action))
    lines.append("")
    lines.append(t("correction.rationale", lang, rationale=payload.rationale))
    lines.append(t("correction.expires", lang))
    return "\n".join(lines)


def render_add_diff(*, pending_id: int, payload: AddPayload, lang: str = "en") -> str:
    category = payload.category if payload.category is not None else "(unknown)"
    unit = f" {payload.unit}" if payload.unit else ""
    return "\n".join([
        t("add.header", lang, name=payload.name),
        t("add.category", lang, category=category),
        t("add.qty_unit", lang, qty=payload.qty, unit=unit),
        t("add.expires_on", lang, expires_on=payload.expires_on.isoformat()),
        t("add.shelf_life", lang,
          shelf_life_days=payload.shelf_life_days,
          shelf_life_source=payload.shelf_life_source),
        "",
        t("add.confidence", lang, confidence=f"{payload.confidence:.2f}"),
        t("correction.expires", lang),
    ])


def build_undo_keyboard(*, receipt_id: int, lang: str = "en") -> list[list[CallbackButton]]:
    return [[CallbackButton(text=t("btn.undo", lang), callback_data=f"undo:receipt:{receipt_id}")]]


def build_undo_add_keyboard(*, item_id: int, lang: str = "en") -> list[list[CallbackButton]]:
    return [[CallbackButton(text=t("btn.undo", lang), callback_data=f"undo:add:{item_id}")]]


def render_undo_result(result, lang: str = "en") -> str:
    if result.expired:
        return t("undo.expired", lang)
    if not result.removed_ids and not result.skipped:
        return t("undo.nothing", lang)
    parts = [t("undo.removed", lang, n=len(result.removed_ids))]
    if result.skipped:
        skipped = ", ".join(f"#{i} ({why})" for i, why in result.skipped)
        parts.append(t("undo.skipped", lang, skipped=skipped))
    return " ".join(parts)


def build_apply_cancel_keyboard(*, pending_id: int, lang: str = "en") -> list[list[CallbackButton]]:
    return [[
        CallbackButton(text=t("btn.apply", lang), callback_data=f"apply:{pending_id}"),
        CallbackButton(text=t("btn.cancel", lang), callback_data=f"cancel:{pending_id}"),
    ]]


def _render_card(
    card: ScoredCandidate,
    *,
    rank: int,
    lang: str = "en",
    names: Mapping[str, str] | None = None,
) -> str:
    r = card.recipe
    n = card.nutrition
    header = f"{'* ' if rank == 0 else ''}{_name(names, r.title)} ({_name(names, r.cuisine)})"
    lines = [
        header,
        t("cook.health", lang, score=n.health_score, effort=n.effort, minutes=n.est_minutes),
    ]
    ingredients = ", ".join(_name(names, i.name) for i in r.ingredients)
    if ingredients:
        lines.append(t("cook.ingredients", lang, items=ingredients))
    lines.append(f"  {_name(names, r.method_gist)}")
    if r.source_url:
        lines.append(t("cook.recipe_link", lang, url=r.source_url))
    if rank == 0 and card.shopping_list:
        items = ", ".join(_name(names, s) for s in card.shopping_list)
        lines.append(t("cook.need_buy", lang, items=items))
    elif rank == 0:
        lines.append(t("cook.need_buy_none", lang))
    return "\n".join(lines)


def render_cook_result(
    cards: list[ScoredCandidate],
    *,
    show_alternatives: bool,
    lang: str = "en",
    names: Mapping[str, str] | None = None,
) -> str:
    if not cards:
        return t("cook.none", lang)
    blocks = [_render_card(cards[0], rank=0, lang=lang, names=names)]
    if show_alternatives:
        for idx, card in enumerate(cards[1:], start=1):
            blocks.append(_render_card(card, rank=idx, lang=lang, names=names))
    return "\n\n".join(blocks)


def render_plan(
    rows: list, *, lang: str = "en", names: Mapping[str, str] | None = None
) -> str:
    lines = [t("plan.header", lang, n=len(rows))]
    for day, candidate, uses_expiring in rows:
        lines.append(
            t(
                "plan.day_line",
                lang,
                weekday=weekday_abbr(day, lang=lang),
                title=_name(names, candidate.recipe.title),
                cuisine=_name(names, candidate.recipe.cuisine),
                minutes=candidate.nutrition.est_minutes,
                fire="🔥" if uses_expiring else "",
            )
        )
        if candidate.recipe.source_url:
            lines.append(t("cook.recipe_link", lang, url=candidate.recipe.source_url))
    return "\n".join(lines)


def build_plan_keyboard(
    plan_id: int, day_rows: list, *, lang: str = "en"
) -> list[list[CallbackButton]]:
    rows = [
        [
            CallbackButton(
                text=t("btn.plan.swap", lang, weekday=weekday_abbr(day, lang=lang)),
                callback_data=f"plan:swap:{plan_id}:{day_index}",
            ),
            CallbackButton(
                text=t("btn.plan.cooked", lang, weekday=weekday_abbr(day, lang=lang)),
                callback_data=f"plan:cooked:{plan_id}:{day_index}",
            ),
        ]
        for day_index, day in day_rows
    ]
    rows.append(
        [
            CallbackButton(text=t("btn.plan.shop", lang), callback_data=f"plan:shop:{plan_id}"),
            CallbackButton(text=t("btn.plan.cancel", lang), callback_data=f"plan:cancel:{plan_id}"),
        ]
    )
    return rows


def render_cooked_sheet(sheet, *, lang: str = "en", names=None) -> str:
    resolved = names or {}
    dish = resolved.get(sheet.recipe_title, sheet.recipe_title)
    key = "cooked.header" if sheet.candidates else "cooked.empty"
    return t(key, lang, dish=dish)


def build_cooked_sheet_keyboard(
    sheet, *, lang: str = "en", names=None
) -> list[list[CallbackButton]]:
    resolved = names or {}
    rows = [
        [
            CallbackButton(
                text=f"{'✅' if c.item_id in sheet.selected_ids else '⬜'} "
                f"{resolved.get(c.raw_name, c.raw_name)}",
                callback_data=f"cooked:tog:{sheet.cooked_id}:{c.item_id}",
            )
        ]
        for c in sheet.candidates
    ]
    rows.append(
        [
            CallbackButton(
                text=t("btn.cooked.confirm", lang),
                callback_data=f"cooked:ok:{sheet.cooked_id}",
            ),
            CallbackButton(
                text=t("btn.cooked.none", lang),
                callback_data=f"cooked:none:{sheet.cooked_id}",
            ),
        ]
    )
    return rows


def build_nl_picker_keyboard(items, *, names=None) -> list[list[CallbackButton]]:
    """One labeled row per candidate item; tapping opens its v4.8 card."""
    resolved = names or {}
    return [
        [
            CallbackButton(
                text=f"#{item.id} {resolved.get(item.raw_name, item.raw_name)}",
                callback_data=f"item:open:{item.id}",
            )
        ]
        for item in items[:8]
    ]


def build_cook_alternatives_keyboard(cook_id: int, lang: str = "en") -> list[list[CallbackButton]]:
    return [[CallbackButton(text=t("btn.show_alternatives", lang), callback_data=f"cookalt:{cook_id}")]]


#: (code, i18n key) pairs for the /cook purpose intake round, in display order.
PURPOSE_OPTIONS = [
    ("use_it_up", "purpose.use_it_up"),
    ("quick", "purpose.quick"),
    ("healthy", "purpose.healthy"),
    ("comfort", "purpose.comfort"),
    ("surprise", "purpose.surprise"),
]


def build_cook_result_keyboard(
    cook_id: int, *, has_alternatives: bool, lang: str = "en", source_url: str | None = None
) -> list[list[CallbackButton]]:
    rows = [
        [
            CallbackButton(text=t("btn.liked", lang), callback_data=f"cookfb:{cook_id}:liked"),
            CallbackButton(text=t("btn.disliked", lang), callback_data=f"cookfb:{cook_id}:disliked"),
        ],
        [
            CallbackButton(text=t("btn.save", lang), callback_data=f"cooksave:{cook_id}"),
            CallbackButton(text=t("btn.shopping", lang), callback_data=f"cookshop:{cook_id}"),
        ],
        [
            CallbackButton(text=t("btn.more_recipes", lang), callback_data=f"cookmore2:{cook_id}"),
            CallbackButton(text=t("btn.adjust", lang), callback_data=f"cookadj:{cook_id}"),
        ],
    ]
    if source_url:
        rows.append([CallbackButton(text=t("btn.open_recipe", lang), url=source_url)])
    if has_alternatives:
        rows.append(
            [CallbackButton(text=t("btn.show_alternatives", lang), callback_data=f"cookalt:{cook_id}")]
        )
    return rows


def render_shopping_list(
    items: list[ShoppingList],
    *,
    lang: str = "en",
    names: Mapping[str, str] | None = None,
) -> str:
    if not items:
        return t("shopping.empty", lang)
    lines = [t("shopping.title", lang)]
    for item in items:
        qty = _qty_prefix(item.qty, item.unit) if item.qty is not None else ""
        lines.append(f"  - {qty}{_name(names, item.name_raw)}")
    return "\n".join(lines)


def build_shopping_keyboard(item_ids: list[int], lang: str = "en") -> list[list[CallbackButton]]:
    return [
        [CallbackButton(text=t("btn.bought", lang), callback_data=f"shopdone:{item_id}")]
        for item_id in item_ids
    ]


def render_favorites(
    recipes: list[SavedRecipe],
    *,
    lang: str = "en",
    names: Mapping[str, str] | None = None,
) -> str:
    if not recipes:
        return t("favorites.empty", lang)
    lines = [t("favorites.title", lang)]
    for recipe in recipes:
        lines.append(f"  #{recipe.id} {_name(names, recipe.title)} ({_name(names, recipe.cuisine)})")
    return "\n".join(lines)


def build_favorites_keyboard(recipe_ids: list[int], lang: str = "en") -> list[list[CallbackButton]]:
    return [
        [CallbackButton(text=t("btn.cook_again", lang), callback_data=f"favcook:{recipe_id}")]
        for recipe_id in recipe_ids
    ]


def render_recook(
    recipe: RecipeCandidate,
    *,
    shopping: list[str],
    lang: str = "en",
    names: Mapping[str, str] | None = None,
) -> str:
    lines = [f"{_name(names, recipe.title)} ({_name(names, recipe.cuisine)})"]
    ingredients = ", ".join(_name(names, i.name) for i in recipe.ingredients)
    if ingredients:
        lines.append(t("cook.ingredients", lang, items=ingredients))
    lines.append(f"  {_name(names, recipe.method_gist)}")
    if recipe.source_url:
        lines.append(t("cook.recipe_link", lang, url=recipe.source_url))
    if shopping:
        items = ", ".join(_name(names, s) for s in shopping)
        lines.append(t("cook.need_buy", lang, items=items))
    else:
        lines.append(t("cook.need_buy_none", lang))
    return "\n".join(lines)


def build_cook_round_keyboard(
    cook_id: int, options: list[str], *, round_name: str | None = None
) -> list[list[CallbackButton]]:
    def callback_data(idx: int) -> str:
        if round_name is None:
            return f"cookpick:{cook_id}:{idx}"
        return f"cookpick:{cook_id}:{round_name}:{idx}"

    return [
        [CallbackButton(text=option, callback_data=callback_data(idx))]
        for idx, option in enumerate(options)
    ]


def render_applied_correction(*, item_id: int, payload: CorrectPayload, lang: str = "en") -> str:
    changes = []
    for field_name in ("name", "category", "expires_on", "shelf_life_days"):
        change = payload.diff.get(field_name)
        if change is not None:
            changes.append(f"{field_name}={change['new']}")
    suffix = ", ".join(changes) if changes else t("applied.correction.no_changes", lang)
    return t("applied.correction", lang, item_id=item_id, suffix=suffix)


def render_applied_add(*, item_id: int, payload: AddPayload, lang: str = "en") -> str:
    return t("applied.add", lang,
             item_id=item_id, name=payload.name,
             expires_on=payload.expires_on.isoformat())


_KNOWN_TERMINAL_STATUSES = frozenset(("cancelled", "expired", "stale", "applied"))


def render_terminal_state(status: str, lang: str = "en") -> str:
    if status in _KNOWN_TERMINAL_STATUSES:
        return t(f"terminal.{status}", lang)
    return t("terminal.unknown", lang, status=status)


CATEGORY_ORDER = (
    "produce", "dairy", "meat", "seafood", "bakery",
    "frozen", "beverage", "pantry", "other",
)


def render_list(items: list, *, today: date, lang: str = "en", names=None) -> str:
    if not items:
        return t("list.empty", lang)
    by_cat: dict[str, list] = {}
    for item in items:
        key = item.category or "other"
        by_cat.setdefault(key, []).append(item)
    ordered = sorted(
        by_cat.keys(),
        key=lambda c: CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else len(CATEGORY_ORDER),
    )
    blocks: list[str] = []
    for cat in ordered:
        group = sorted(by_cat[cat], key=lambda i: i.expires_on)
        cat_label = t(f"category.{cat}", lang) if cat in CATEGORY_ORDER else cat.capitalize()
        block = [f"{cat_label} ({len(group)})"]
        block.extend(f"  {render_item_line(i, today=today, lang=lang, names=names)}" for i in group)
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)


def _fmt_cost_short(micros: int | None) -> str:
    if micros is None:
        return "-"
    return f"${micros / 1_000_000:.3f}"


def render_stats(stats: Stats, lang: str = "en") -> str:
    cache_hit = "-" if stats.cache_hit_percent is None else f"{stats.cache_hit_percent:.1f}%"
    waste_rate = "-" if stats.waste_rate_percent is None else f"{stats.waste_rate_percent:.1f}%"
    avg_cost = _fmt_cost_short(stats.avg_cost_micros_usd)
    total_cost = "$0.000" if stats.total_cost_micros_usd == 0 else _fmt_cost_short(stats.total_cost_micros_usd)
    lines = [
        t("stats.header", lang),
        t("stats.receipts", lang,
          receipt_count=stats.receipt_count,
          unknown_cost_receipt_count=stats.unknown_cost_receipt_count),
        t("stats.tracked", lang, tracked_item_count=stats.tracked_item_count),
        t("stats.removed", lang, removed_item_count=stats.removed_item_count),
        t("stats.cache_hit", lang, cache_hit=cache_hit),
        t("stats.llm_spend", lang, total_cost=total_cost, avg_cost=avg_cost),
    ]
    tl = stats.text_llm
    corr_unknown = (
        t("stats.unknown_suffix", lang, n=tl.correction_unknown_cost_count)
        if tl.correction_unknown_cost_count
        else ""
    )
    add_unknown = (
        t("stats.unknown_suffix", lang, n=tl.add_unknown_cost_count)
        if tl.add_unknown_cost_count
        else ""
    )
    lines.append(t("stats.corrections", lang,
                   count=tl.correction_proposal_count,
                   cost_total=f"{tl.correction_cost_micros / 1_000_000:.4f}",
                   unknown=corr_unknown))
    lines.append(t("stats.adds", lang,
                   count=tl.add_proposal_count,
                   cost_total=f"{tl.add_cost_micros / 1_000_000:.4f}",
                   unknown=add_unknown))
    lines.append(t("stats.cook_sessions", lang,
                   count=stats.cook_count,
                   cost=f"{stats.cook_cost_micros_usd / 1_000_000:.3f}"))
    lines.append(t("stats.cooked", lang,
                   feedback_count=stats.cook_feedback_count,
                   liked_count=stats.cook_liked_count))
    lines.append(t("stats.meals_cooked", lang, count=stats.meals_cooked_count))
    lines.append(t("stats.waste_rate", lang, waste_rate=waste_rate))
    return "\n".join(lines)


def render_profile(profile: FoodProfile, lang: str = "en") -> str:
    exclusions = ", ".join(profile.exclusions) or t("profile.none_value", lang)
    cuisines = ", ".join(profile.preferred_cuisines) or t("profile.any_value", lang)
    cook = (
        t("profile.cook_minutes", lang, minutes=profile.max_cook_minutes)
        if profile.max_cook_minutes
        else t("profile.no_limit", lang)
    )
    note = profile.note or t("profile.note_none", lang)
    lines = [
        t("profile.header", lang),
        t("profile.diet", lang, diet=profile.diet),
        t("profile.avoid", lang, exclusions=exclusions),
        t("profile.cuisines", lang, cuisines=cuisines),
        t("profile.max_cook", lang, cook=cook),
        t("profile.household_size", lang, household_size=profile.household_size),
        t("profile.notes", lang, note=note),
        t("profile.update_hint", lang),
    ]
    return "\n".join(lines)
