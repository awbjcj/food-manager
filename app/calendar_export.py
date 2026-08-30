"""RFC 5545 calendar export for active household meal plans."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from app.cook.models import ScoredCandidate
from app.models import MealPlan, MealPlanEntry


def _escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
    )


def _fold_content_line(line: str) -> list[str]:
    """Fold one RFC 5545 content line without splitting UTF-8 code points."""
    parts: list[str] = []
    current = ""
    byte_limit = 75
    for char in line:
        candidate = current + char
        if current and len(candidate.encode("utf-8")) > byte_limit:
            parts.append(current)
            current = char
            byte_limit = 74  # continuation line's leading space counts
        else:
            current = candidate
    parts.append(current)
    return [parts[0], *(f" {part}" for part in parts[1:])]


def _utc_stamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def build_plan_calendar(
    plan: MealPlan, entries: Sequence[MealPlanEntry]
) -> str:
    """Serialize meal-plan entries as portable all-day iCalendar events."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Food Manager//Meal Plan//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for entry in sorted(entries, key=lambda row: (row.date, row.day_index)):
        candidate = ScoredCandidate.model_validate_json(entry.recipe_json)
        recipe = candidate.recipe
        entry_id = entry.id if entry.id is not None else entry.day_index
        description = (
            f"Cuisine: {recipe.cuisine}\n"
            f"Ingredients: {', '.join(item.name for item in recipe.ingredients)}\n"
            f"Method: {recipe.method_gist}"
        )
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:meal-plan-{plan.id}-{entry_id}@food-manager",
                f"DTSTAMP:{_utc_stamp(plan.created_at)}",
                f"DTSTART;VALUE=DATE:{entry.date:%Y%m%d}",
                f"DTEND;VALUE=DATE:{entry.date + timedelta(days=1):%Y%m%d}",
                f"SUMMARY:{_escape(recipe.title)}",
                f"DESCRIPTION:{_escape(description)}",
            ]
        )
        if recipe.source_url:
            lines.append(f"URL:{recipe.source_url}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    folded = [physical for line in lines for physical in _fold_content_line(line)]
    return "\r\n".join(folded) + "\r\n"


__all__ = ["build_plan_calendar"]
