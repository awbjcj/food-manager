from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Sequence, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.i18n import LANGS
from app.pantry_service import (
    ALLOWED_CATEGORIES,
    ListFilter,
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


def parse_snooze_args(args: Sequence[str]) -> tuple[int, int]:
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


def parse_list_filter(args: Sequence[str]) -> ListFilter:
    if not args:
        return ListFilter.default()
    if len(args) > 1:
        raise CommandError("usage: /list [category|week|expired]")
    token = args[0].lower()
    if token in {"week", "expired"}:
        return ListFilter(window=cast(Literal["week", "expired"], token))
    if token in ALLOWED_CATEGORIES:
        return ListFilter(category=token)
    raise CommandError(
        f"unknown /list filter {token!r}. Try a category "
        f"({', '.join(sorted(ALLOWED_CATEGORIES))}) or 'week' / 'expired'."
    )


LLMProviderName = Literal["anthropic", "openai"]
ALLOWED_LLM_PROVIDERS: tuple[LLMProviderName, ...] = ("anthropic", "openai")


def parse_llm_provider(args: Sequence[str]) -> Optional[LLMProviderName]:
    if len(args) > 1:
        raise CommandError("usage: /llm [anthropic|openai]")
    if not args:
        return None
    token = args[0].lower()
    if token not in ALLOWED_LLM_PROVIDERS:
        raise CommandError("usage: /llm [anthropic|openai]")
    return cast(LLMProviderName, token)


def parse_lang(args: Sequence[str]) -> Optional[str]:
    if len(args) > 1:
        raise CommandError(f"usage: /lang [{'|'.join(LANGS)}]")
    if not args:
        return None
    token = args[0].lower()
    if token not in LANGS:
        raise CommandError(f"usage: /lang [{'|'.join(LANGS)}]")
    return token


def parse_invite_mode(args: Sequence[str]) -> Optional[int]:
    """Parse ``/invite`` arguments into a ``max_uses`` value.

    No argument -> 1 (single-use). ``family`` -> None (reusable until expiry).
    """
    if not args:
        return 1
    if len(args) != 1 or args[0].lower() != "family":
        raise CommandError("usage: /invite [family]")
    return None


def parse_invite_token(args: Sequence[str]) -> str:
    if len(args) != 1 or not args[0].strip():
        raise CommandError("usage: /join <invite-code>")
    return args[0].strip()


def parse_member_id(args: Sequence[str]) -> int:
    if len(args) != 1:
        raise CommandError("usage: /remove <member-id>")
    try:
        return int(args[0].strip())
    except ValueError as exc:
        raise CommandError(f"expected a numeric member id, got {args[0]!r}") from exc


Verb = Literal[
    "ate",
    "toss",
    "snooze2",
    "freeze",
    "show_all",
    "apply",
    "cancel",
    "undo_receipt",
    "undo_add",
    "cook_pick",
    "cook_alt",
    "cook_like",
    "cook_dislike",
    "cook_save",
    "cook_shop",
    "shop_done",
    "fav_cook",
]


@dataclass(frozen=True)
class CallbackAction:
    verb: Verb
    item_id: Optional[int]
    option_index: Optional[int] = None
    round_name: Optional[str] = None


def parse_callback(data: str) -> CallbackAction:
    if data == "show:all":
        return CallbackAction(verb="show_all", item_id=None)
    if data.startswith("apply:") or data.startswith("cancel:"):
        verb, _, raw_id = data.partition(":")
        try:
            pending_id = int(raw_id)
        except ValueError as exc:
            raise CommandError(f"bad pending id {raw_id!r}") from exc
        return CallbackAction(verb=cast(Verb, verb), item_id=pending_id)
    if data.startswith("undo:"):
        _, _, rest = data.partition(":")
        kind, _, raw_id = rest.partition(":")
        if kind not in ("receipt", "add"):
            raise CommandError(f"unknown undo kind {kind!r}")
        try:
            target_id = int(raw_id)
        except ValueError as exc:
            raise CommandError(f"bad undo id {raw_id!r}") from exc
        return CallbackAction(verb=cast(Verb, f"undo_{kind}"), item_id=target_id)
    if data.startswith("cookfb:"):
        parts = data.split(":")
        if len(parts) != 3 or parts[2] not in ("liked", "disliked"):
            raise CommandError(f"bad cookfb data {data!r}")
        try:
            cook_id = int(parts[1])
        except ValueError as exc:
            raise CommandError(f"bad cook id {parts[1]!r}") from exc
        verb = "cook_like" if parts[2] == "liked" else "cook_dislike"
        return CallbackAction(verb=cast(Verb, verb), item_id=cook_id)
    for prefix, verb_name in (
        ("cooksave:", "cook_save"),
        ("cookshop:", "cook_shop"),
        ("shopdone:", "shop_done"),
        ("favcook:", "fav_cook"),
    ):
        if data.startswith(prefix):
            _, _, raw_id = data.partition(":")
            try:
                target_id = int(raw_id)
            except ValueError as exc:
                raise CommandError(f"bad id {raw_id!r}") from exc
            return CallbackAction(verb=cast(Verb, verb_name), item_id=target_id)
    if data.startswith("cookalt:"):
        _, _, raw_id = data.partition(":")
        try:
            cook_id = int(raw_id)
        except ValueError as exc:
            raise CommandError(f"bad cook id {raw_id!r}") from exc
        return CallbackAction(verb="cook_alt", item_id=cook_id)
    if data.startswith("cookpick:"):
        parts = data.split(":")
        if len(parts) not in (3, 4):
            raise CommandError(f"bad cookpick data {data!r}")
        if len(parts) == 3:
            _, raw_id, raw_idx = parts
            round_name = None
        else:
            _, raw_id, round_name, raw_idx = parts
            if round_name not in ("meal", "cuisine"):
                raise CommandError(f"bad cookpick data {data!r}")
        try:
            option_index = int(raw_idx)
            if option_index < 0:
                raise CommandError(f"bad cookpick data {data!r}")
            return CallbackAction(
                verb="cook_pick",
                item_id=int(raw_id),
                option_index=option_index,
                round_name=round_name,
            )
        except ValueError as exc:
            raise CommandError(f"bad cookpick data {data!r}") from exc
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "act":
        raise CommandError(f"unrecognized callback data {data!r}")
    verb = parts[1]
    if verb not in ("ate", "toss", "snooze2", "freeze"):
        raise CommandError(f"unknown verb {verb!r}")
    try:
        item_id = int(parts[2])
    except ValueError as exc:
        raise CommandError(f"bad item id {parts[2]!r}") from exc
    return CallbackAction(verb=verb, item_id=item_id)
