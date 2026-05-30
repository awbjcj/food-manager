from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.correction_service import AddPayload, CorrectPayload
from app.cook_models import ScoredCandidate
from app.ingest_service import IngestSummary
from app.pantry_service import Stats
from app.profile_service import FoodProfile


def _fmt_date(value: date, *, today: date) -> str:
    base = f"{value:%b} {value.day}"
    if value.year != today.year:
        return f"{base} {value.year}"
    return base


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


def render_item_line(item, *, today: date) -> str:
    icon = _urgency_icon(item.expires_on, today=today)
    qty = _qty_prefix(item.qty, item.unit)
    delta = (item.expires_on - today).days
    if delta < 0:
        tail = f"expired {-delta}d"
    elif delta == 0:
        tail = "today"
    else:
        tail = f"{_fmt_date(item.expires_on, today=today)} ({delta}d)"
    return f"{icon} #{item.id} {qty}{item.raw_name} - {tail}"


def _fmt_cost(micros: int | None) -> str:
    if micros is None:
        return "Cost: unavailable"
    return f"Cost: ${micros / 1_000_000:.3f}"


def render_ingest_reply(summary: IngestSummary, *, today: date, refined_ids=frozenset()) -> str:
    lines: list[str] = []
    if summary.inserted_food_count == 0:
        if summary.skipped_low_confidence_count:
            lines.append(
                "No clear food items found "
                f"(skipped {summary.skipped_low_confidence_count} unclear items)."
            )
        else:
            lines.append("No food items found in this receipt.")
        if summary.skipped_excluded_count:
            names = ", ".join(summary.skipped_excluded_names[:5])
            more = "" if len(summary.skipped_excluded_names) <= 5 else ", ..."
            lines.append(f"Skipped (not tracked): {names}{more}")
            lines.append("Want one tracked? /add <name>")
        lines.append(_fmt_cost(summary.cost_micros_usd))
        return "\n".join(lines)

    lines.append(f"Logged {summary.inserted_food_count} items from this receipt:")
    for item_id, name, expires_on, shelf_life_days in zip(
        summary.inserted_item_ids,
        summary.inserted_item_names,
        summary.inserted_item_expires_on,
        summary.inserted_item_shelf_life_days,
    ):
        mark = " ✓refined" if item_id in refined_ids else ""
        lines.append(
            f"  - #{item_id} {name} - exp {_fmt_date(expires_on, today=today)} ({shelf_life_days}d){mark}"
        )

    if summary.purchase_date is not None and summary.purchase_date != today:
        lines.append(f"Purchase date: {_fmt_date(summary.purchase_date, today=today)}")
    if summary.purchase_date_assumed and summary.purchase_date is not None:
        lines.append(f"Purchase date assumed: {_fmt_date(summary.purchase_date, today=today)}")

    if summary.low_confidence_inserted_ids:
        ids = ", ".join(f"#{item_id}" for item_id in summary.low_confidence_inserted_ids[:5])
        more = "" if len(summary.low_confidence_inserted_ids) <= 5 else " ..."
        lines.append(f"Low confidence: {ids}{more} - review with /correct or /delete")

    if summary.skipped_low_confidence_count:
        names = ", ".join(summary.skipped_low_confidence_names[:3])
        more = "" if len(summary.skipped_low_confidence_names) <= 3 else ", ..."
        lines.append(
            f"(skipped {summary.skipped_low_confidence_count} unclear items: {names}{more})"
        )

    if summary.skipped_excluded_count:
        names = ", ".join(summary.skipped_excluded_names[:5])
        more = "" if len(summary.skipped_excluded_names) <= 5 else ", ..."
        lines.append(f"Skipped (not tracked): {names}{more}")
        lines.append("Want one tracked? /add <name>")

    lines.append(_fmt_cost(summary.cost_micros_usd))
    return "\n".join(lines)


@dataclass
class CallbackButton:
    text: str
    callback_data: str


@dataclass
class DigestRender:
    text: str
    rendered_item_ids: list[int] = field(default_factory=list)
    rendered_count: int = 0
    total_count: int = 0
    has_more: bool = False


DIGEST_CAP = 20


def render_digest(items: list, *, today: date) -> DigestRender:
    total = len(items)
    if total == 0:
        return DigestRender(text="", rendered_count=0, total_count=0, has_more=False)

    capped = items[:DIGEST_CAP]
    buckets = {"expired": [], "today": [], "tomorrow": [], "this_week": []}
    for item in capped:
        if item.expires_on < today:
            buckets["expired"].append(item)
        elif item.expires_on == today:
            buckets["today"].append(item)
        elif item.expires_on == today + timedelta(days=1):
            buckets["tomorrow"].append(item)
        else:
            buckets["this_week"].append(item)

    weekday_short = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

    def line_for(item) -> str:
        return "  " + render_item_line(item, today=today)

    lines = [f"Pantry digest - {weekday_short[today.weekday()]} {_fmt_date(today, today=today)}", ""]
    for key, header in (
        ("expired", "Expired"),
        ("today", "Today"),
        ("tomorrow", "Tomorrow"),
        ("this_week", "This week"),
    ):
        if buckets[key]:
            lines.append(f"{header} ({len(buckets[key])})")
            lines.extend(line_for(item) for item in buckets[key])
            lines.append("")

    has_more = total > DIGEST_CAP
    if has_more:
        lines.append(f"... and {total - DIGEST_CAP} more - tap [show all]")

    return DigestRender(
        text="\n".join(lines).rstrip(),
        rendered_item_ids=[item.id for item in capped],
        rendered_count=len(capped),
        total_count=total,
        has_more=has_more,
    )


def build_digest_keyboard(
    item_ids: list[int], *, has_more: bool
) -> list[list[CallbackButton]]:
    rows: list[list[CallbackButton]] = []
    for item_id in item_ids:
        rows.append([
            CallbackButton(text="Ate", callback_data=f"act:ate:{item_id}"),
            CallbackButton(text="Tossed", callback_data=f"act:toss:{item_id}"),
            CallbackButton(text="Remind +2d", callback_data=f"act:snooze2:{item_id}"),
        ])
    if has_more:
        rows.append([CallbackButton(text="show all", callback_data="show:all")])
    return rows


# TODO(user): tune correction/add diff wording and field order against the
# messages you actually want to read in Telegram.
def render_correction_diff(
    *,
    pending_id: int,
    payload: CorrectPayload,
    item_id: int,
    item_raw_name: str,
) -> str:
    lines = [f"Proposed correction for #{item_id} {item_raw_name}:"]
    for field_name in ("name", "category", "expires_on", "shelf_life_days"):
        change = payload.diff.get(field_name)
        if change is None:
            continue
        suffix = ""
        if field_name == "shelf_life_days" and payload.back_computed_days:
            suffix = "  (back-computed from expires_on)"
        lines.append(f"  - {field_name}: {change['old']} -> {change['new']}{suffix}")
    lines.append(f"  - cache: {payload.cache_action}")
    lines.append("")
    lines.append(f"Reason: {payload.rationale}")
    lines.append("Expires in 10 min.")
    return "\n".join(lines)


def render_add_diff(*, pending_id: int, payload: AddPayload) -> str:
    category = payload.category if payload.category is not None else "(unknown)"
    unit = f" {payload.unit}" if payload.unit else ""
    return "\n".join([
        f"Proposed add - {payload.name}:",
        f"  - category: {category}",
        f"  - qty / unit: {payload.qty}{unit}",
        f"  - expires_on: {payload.expires_on.isoformat()}",
        f"  - shelf_life_days: {payload.shelf_life_days} (source: {payload.shelf_life_source})",
        "",
        f"Confidence: {payload.confidence:.2f}",
        "Expires in 10 min.",
    ])


def build_undo_keyboard(*, receipt_id: int) -> list[list[CallbackButton]]:
    return [[CallbackButton(text="Undo", callback_data=f"undo:receipt:{receipt_id}")]]


def build_undo_add_keyboard(*, item_id: int) -> list[list[CallbackButton]]:
    return [[CallbackButton(text="Undo", callback_data=f"undo:add:{item_id}")]]


def render_undo_result(result) -> str:
    if result.expired:
        return "Undo window expired (10 min) - use /delete <id> instead."
    if not result.removed_ids and not result.skipped:
        return "Nothing to undo."
    parts = [f"Undone: removed {len(result.removed_ids)} item(s)."]
    if result.skipped:
        skipped = ", ".join(f"#{i} ({why})" for i, why in result.skipped)
        parts.append(f"skipped {skipped}.")
    return " ".join(parts)


def build_apply_cancel_keyboard(*, pending_id: int) -> list[list[CallbackButton]]:
    return [[
        CallbackButton(text="Apply", callback_data=f"apply:{pending_id}"),
        CallbackButton(text="Cancel", callback_data=f"cancel:{pending_id}"),
    ]]


def _render_card(card: ScoredCandidate, *, rank: int) -> str:
    r = card.recipe
    n = card.nutrition
    header = f"{'* ' if rank == 0 else ''}{r.title} ({r.cuisine})"
    lines = [
        header,
        f"  Health {n.health_score}/100 - {n.effort} - {n.est_minutes} min",
        f"  {r.method_gist}",
    ]
    if r.source_url:
        lines.append(f"  Recipe: {r.source_url}")
    if rank == 0 and card.shopping_list:
        lines.append("  Need to buy: " + ", ".join(card.shopping_list))
    elif rank == 0:
        lines.append("  Need to buy: nothing - you have it all!")
    return "\n".join(lines)


def render_cook_result(cards: list[ScoredCandidate], *, show_alternatives: bool) -> str:
    if not cards:
        return "Couldn't find a recipe that fits your pantry and restrictions."
    blocks = [_render_card(cards[0], rank=0)]
    if show_alternatives:
        for idx, card in enumerate(cards[1:], start=1):
            blocks.append(_render_card(card, rank=idx))
    return "\n\n".join(blocks)


def build_cook_alternatives_keyboard(cook_id: int) -> list[list[CallbackButton]]:
    return [[CallbackButton(text="Show alternatives", callback_data=f"cookalt:{cook_id}")]]


def build_cook_round_keyboard(cook_id: int, options: list[str]) -> list[list[CallbackButton]]:
    return [
        [CallbackButton(text=option, callback_data=f"cookpick:{cook_id}:{idx}")]
        for idx, option in enumerate(options)
    ]


def render_applied_correction(*, item_id: int, payload: CorrectPayload) -> str:
    changes = []
    for field_name in ("name", "category", "expires_on", "shelf_life_days"):
        change = payload.diff.get(field_name)
        if change is not None:
            changes.append(f"{field_name}={change['new']}")
    suffix = ", ".join(changes) if changes else "no changes"
    return f"Applied to #{item_id}: {suffix}"


def render_applied_add(*, item_id: int, payload: AddPayload) -> str:
    return f"Added #{item_id} {payload.name} (expires {payload.expires_on.isoformat()})"


_TERMINAL_LABELS = {
    "cancelled": "Cancelled.",
    "expired": "This proposal has expired - re-run the command.",
    "stale": "This proposal is stale (the item changed) - re-run the command.",
    "applied": "This proposal was already applied.",
}


def render_terminal_state(status: str) -> str:
    return _TERMINAL_LABELS.get(status, f"This proposal is no longer pending ({status}).")


CATEGORY_ORDER = (
    "produce", "dairy", "meat", "seafood", "bakery",
    "frozen", "beverage", "pantry", "other",
)


def render_list(items: list, *, today: date) -> str:
    if not items:
        return "no items match this filter"
    by_cat: dict[str, list] = {}
    for item in items:
        key = item.category or "other"
        by_cat.setdefault(key, []).append(item)
    ordered = sorted(
        by_cat.keys(),
        key=lambda c: CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else len(CATEGORY_ORDER),
    )
    lines: list[str] = []
    for cat in ordered:
        group = sorted(by_cat[cat], key=lambda i: i.expires_on)
        lines.append(f"{cat.capitalize()} ({len(group)})")
        lines.extend(f"  {render_item_line(i, today=today)}" for i in group)
    return "\n".join(lines)


def _fmt_cost_short(micros: int | None) -> str:
    if micros is None:
        return "-"
    return f"${micros / 1_000_000:.3f}"


def render_stats(stats: Stats) -> str:
    cache_hit = "-" if stats.cache_hit_percent is None else f"{stats.cache_hit_percent:.1f}%"
    waste_rate = "-" if stats.waste_rate_percent is None else f"{stats.waste_rate_percent:.1f}%"
    avg_cost = _fmt_cost_short(stats.avg_cost_micros_usd)
    total_cost = "$0.000" if stats.total_cost_micros_usd == 0 else _fmt_cost_short(stats.total_cost_micros_usd)
    lines = [
        "Last 30 days",
        f"Receipts: {stats.receipt_count} (unknown-cost: {stats.unknown_cost_receipt_count})",
        f"Tracked items: {stats.tracked_item_count}",
        f"Removed (wrong import): {stats.removed_item_count}",
        f"Cache hit rate: {cache_hit}",
        f"LLM spend: total {total_cost}  avg {avg_cost} / receipt",
    ]
    tl = stats.text_llm
    corr_unknown = (
        f", {tl.correction_unknown_cost_count} unknown"
        if tl.correction_unknown_cost_count
        else ""
    )
    add_unknown = (
        f", {tl.add_unknown_cost_count} unknown"
        if tl.add_unknown_cost_count
        else ""
    )
    lines.append(
        f"  Corrections: {tl.correction_proposal_count}  "
        f"(${tl.correction_cost_micros / 1_000_000:.4f} total{corr_unknown})"
    )
    lines.append(
        f"  Adds:        {tl.add_proposal_count}  "
        f"(${tl.add_cost_micros / 1_000_000:.4f} total{add_unknown})"
    )
    lines.append(f"Waste rate: {waste_rate}")
    return "\n".join(lines)


def render_profile(profile: FoodProfile) -> str:
    exclusions = ", ".join(profile.exclusions) or "none"
    cuisines = ", ".join(profile.preferred_cuisines) or "any"
    cook = f"{profile.max_cook_minutes} min" if profile.max_cook_minutes else "no limit"
    note = profile.note or "(none)"
    return (
        "Your food profile:\n"
        f"  Diet: {profile.diet}\n"
        f"  Avoid: {exclusions}\n"
        f"  Cuisines: {cuisines}\n"
        f"  Max cook time: {cook}\n"
        f"  Household size: {profile.household_size}\n"
        f"  Notes: {note}\n"
        "Update by typing: /prefs <sentence>  (e.g. /prefs I'm vegan, no peanuts)"
    )
