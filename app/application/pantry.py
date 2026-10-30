"""Pantry presentation without transport messages or SDK keyboards."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlmodel import Session

from app import views
from app.batch_service import create_batch
from app.commands import CommandError, parse_pantry_arg
from app.i18n import t
from app.models import PantryItem
from app.pantry_service import ListFilter, PantrySort, list_active, list_digest_due
from app.renderer import CallbackButton, build_digest_keyboard, build_item_card_keyboard


@dataclass(frozen=True)
class PresentedView:
    text: str
    rows: list[list[CallbackButton]] = field(default_factory=list)


async def pantry_view(
    session: Session,
    *,
    user,
    args: Sequence[str],
    today: date,
    now: datetime,
    translation_llm=None,
) -> PresentedView:
    try:
        mode = parse_pantry_arg(list(args))
    except CommandError:
        return PresentedView(t("pantry.usage", user.lang))
    if mode == "batch":
        batch = create_batch(
            session,
            household_id=user.household_id,
            user_id=user.id,
            sort_by="store",
            today=today,
            now=now,
        )
        view = await views.prepare_pantry_batch(
            session,
            batch,
            user=user,
            now=now,
            translation_llm=translation_llm,
        )
        return PresentedView(view.text, view.rows)
    if isinstance(mode, int):
        item = session.get(PantryItem, mode)
        if item is None or item.household_id != user.household_id:
            return PresentedView(t("pantry.no_item", user.lang, id=mode))
        if item.status != "active":
            return PresentedView(
                t("pantry.item_inactive", user.lang, id=mode, status=item.status)
            )
        view = await views.item_card(
            session,
            item,
            user=user,
            today=today,
            translation_llm=translation_llm,
        )
        return PresentedView(
            view.text,
            build_item_card_keyboard(
                item,
                lang=user.lang,
                back_to="all",
                sort_by="receipt",
            ),
        )
    sort_by: PantrySort
    if mode == "digest":
        items = list_digest_due(session, household_id=user.household_id, today=today)
        back_to, cap, empty_key, sort_by = (
            "digest",
            10,
            "digest.pantry_clear",
            "expires",
        )
    else:
        sort_by = "receipt" if mode == "all" else mode
        items = list_active(
            session,
            household_id=user.household_id,
            f=ListFilter.default(),
            today=today,
            sort_by=sort_by,
        )
        back_to, cap, empty_key = "all", None, "pantry.all_clear"
    view = await views.digest(
        session,
        items,
        user=user,
        today=today,
        translation_llm=translation_llm,
        cap=cap,
        sort_by=sort_by,
    )
    if not view.text:
        return PresentedView(t(empty_key, user.lang))
    return PresentedView(
        view.text,
        build_digest_keyboard(
            view.rendered_items,
            has_more=view.has_more,
            today=today,
            lang=user.lang,
            names=view.names,
            back_to=back_to,
            sort_by=sort_by,
            page=view.page,
            page_count=view.page_count,
        ),
    )
