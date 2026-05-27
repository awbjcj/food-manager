from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.ingest_service import IngestSummary
from app.pantry_service import Stats


def _fmt_date(value: date) -> str:
    return f"{value:%b} {value.day}"


def _fmt_cost(micros: int | None) -> str:
    if micros is None:
        return "Cost: unavailable"
    return f"Cost: ${micros / 1_000_000:.3f}"


def render_ingest_reply(summary: IngestSummary, *, today: date) -> str:
    lines: list[str] = []
    if summary.inserted_food_count == 0:
        if summary.skipped_low_confidence_count:
            lines.append(
                "No clear food items found "
                f"(skipped {summary.skipped_low_confidence_count} unclear items)."
            )
        else:
            lines.append("No food items found in this receipt.")
        lines.append(_fmt_cost(summary.cost_micros_usd))
        return "\n".join(lines)

    lines.append(f"Logged {summary.inserted_food_count} items from this receipt:")
    for item_id, name, expires_on, shelf_life_days in zip(
        summary.inserted_item_ids,
        summary.inserted_item_names,
        summary.inserted_item_expires_on,
        summary.inserted_item_shelf_life_days,
    ):
        lines.append(
            f"  - #{item_id} {name} - exp {_fmt_date(expires_on)} ({shelf_life_days}d)"
        )

    if summary.purchase_date is not None and summary.purchase_date != today:
        lines.append(f"Purchase date: {_fmt_date(summary.purchase_date)}")
    if summary.purchase_date_assumed:
        lines.append(f"Purchase date assumed: {_fmt_date(summary.purchase_date)}")

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
        delta = (item.expires_on - today).days
        if delta < 0:
            days = -delta
            tag = f"({days}d ago)" if days > 1 else "(yesterday)"
            return f"  - #{item.id} {item.raw_name} {tag}"
        if delta in (0, 1):
            return f"  - #{item.id} {item.raw_name}"
        return f"  - #{item.id} {item.raw_name} - {weekday_short[item.expires_on.weekday()]}"

    lines = [f"Pantry digest - {weekday_short[today.weekday()]} {_fmt_date(today)}", ""]
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


def render_list(items: list, *, today: date) -> str:
    if not items:
        return "no items match this filter"
    lines = []
    for item in items:
        delta = (item.expires_on - today).days
        if delta < 0:
            tag = f"expired {-delta}d ago"
        elif delta == 0:
            tag = "expires today"
        elif delta == 1:
            tag = "expires tomorrow"
        else:
            tag = f"expires {_fmt_date(item.expires_on)} ({delta}d)"
        lines.append(f"#{item.id} {item.raw_name} - {tag}")
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
    return "\n".join([
        "Last 30 days",
        f"Receipts: {stats.receipt_count} (unknown-cost: {stats.unknown_cost_receipt_count})",
        f"Tracked items: {stats.tracked_item_count}",
        f"Removed (wrong import): {stats.removed_item_count}",
        f"Cache hit rate: {cache_hit}",
        f"LLM spend: total {total_cost}  avg {avg_cost} / receipt",
        f"Waste rate: {waste_rate}",
    ])
