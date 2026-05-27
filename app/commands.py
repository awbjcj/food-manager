from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.pantry_service import (
    ALLOWED_CATEGORIES,
    ListFilter,
    SHELF_LIFE_DAYS_MAX,
    SHELF_LIFE_DAYS_MIN,
    SNOOZE_DAYS_DEFAULT,
    SNOOZE_DAYS_MAX,
    SNOOZE_DAYS_MIN,
)


class CommandError(Exception):
    pass


def parse_tz(arg: str) -> str:
    if arg.upper() in {"EST", "EDT", "CST", "CDT", "MST", "MDT", "PST", "PDT"}:
        raise CommandError(
            f"unknown IANA timezone {arg!r}. Examples: America/Detroit, America/New_York"
        )
    try:
        ZoneInfo(arg)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CommandError(
            f"unknown IANA timezone {arg!r}. Examples: America/Detroit, America/New_York"
        ) from exc
    return arg


def parse_digest_at(arg: str) -> int:
    try:
        hour = int(arg)
    except ValueError as exc:
        raise CommandError("digest_at expects an integer hour 0..23") from exc
    if hour < 0 or hour > 23:
        raise CommandError("digest_at expects an integer hour 0..23")
    return hour


def parse_item_id_arg(arg: str) -> int:
    try:
        return int(arg.lstrip("#"))
    except ValueError as exc:
        raise CommandError(f"expected item id like 42 or #42, got {arg!r}") from exc


def parse_snooze_args(args: list[str]) -> tuple[int, int]:
    if not args or len(args) > 2:
        raise CommandError("usage: /snooze <item_id> [days]")
    item_id = parse_item_id_arg(args[0])
    if len(args) == 1:
        return item_id, SNOOZE_DAYS_DEFAULT
    try:
        days = int(args[1])
    except ValueError as exc:
        raise CommandError("days must be an integer") from exc
    if days < SNOOZE_DAYS_MIN or days > SNOOZE_DAYS_MAX:
        raise CommandError(f"days must be in [{SNOOZE_DAYS_MIN}, {SNOOZE_DAYS_MAX}]")
    return item_id, days


def parse_correct_args(args: list[str]) -> tuple[int, int]:
    if len(args) != 2:
        raise CommandError("usage: /correct <item_id> <shelf_life_days>")
    item_id = parse_item_id_arg(args[0])
    try:
        days = int(args[1])
    except ValueError as exc:
        raise CommandError("shelf_life_days must be an integer") from exc
    if days < SHELF_LIFE_DAYS_MIN or days > SHELF_LIFE_DAYS_MAX:
        raise CommandError(
            f"shelf_life_days must be in [{SHELF_LIFE_DAYS_MIN}, {SHELF_LIFE_DAYS_MAX}]"
        )
    return item_id, days


def parse_list_filter(args: list[str]) -> ListFilter:
    if not args:
        return ListFilter.default()
    if len(args) > 1:
        raise CommandError("usage: /list [category|week|expired]")
    token = args[0].lower()
    if token in {"week", "expired"}:
        return ListFilter(window=token)
    if token in ALLOWED_CATEGORIES:
        return ListFilter(category=token)
    raise CommandError(
        f"unknown /list filter {token!r}. Try a category "
        f"({', '.join(sorted(ALLOWED_CATEGORIES))}) or 'week' / 'expired'."
    )


Verb = Literal["ate", "toss", "snooze2", "show_all"]


@dataclass(frozen=True)
class CallbackAction:
    verb: Verb
    item_id: Optional[int]


def parse_callback(data: str) -> CallbackAction:
    if data == "show:all":
        return CallbackAction(verb="show_all", item_id=None)
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "act":
        raise CommandError(f"unrecognized callback data {data!r}")
    verb = parts[1]
    if verb not in ("ate", "toss", "snooze2"):
        raise CommandError(f"unknown verb {verb!r}")
    try:
        item_id = int(parts[2])
    except ValueError as exc:
        raise CommandError(f"bad item id {parts[2]!r}") from exc
    return CallbackAction(verb=verb, item_id=item_id)
