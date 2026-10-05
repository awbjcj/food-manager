"""Pure, localized batch selection and confirmation cards."""

from __future__ import annotations

from app.batch_service import MAX_SELECTED, PAGE_SIZE
from app.i18n import t
from app.models import PantryBatch, PantryItem
from app.renderer import CallbackButton


def render_batch(
    batch: PantryBatch,
    items: list[PantryItem],
    *,
    total: int,
    selected: set[int],
    lang: str,
    names: dict[str, str],
    stores: dict[int, str],
) -> tuple[str, list[list[CallbackButton]]]:
    prefix = f"batch:{batch.id}:{batch.version}:"
    back = CallbackButton(t("btn.back", lang), f"item:list:all:{batch.sort_by}")
    if batch.status == "applied":
        return t(
            "batch.done",
            lang,
            n=batch.applied_count,
            skipped=batch.skipped_count,
            status=t(f"batch.status.{batch.target_status}", lang),
        ), [[back]]
    if batch.status == "cancelled":
        return t("batch.cancelled", lang), [[back]]
    confirming = batch.status == "confirming"
    title = (
        t(
            "batch.confirm",
            lang,
            n=len(selected),
            status=t(f"batch.status.{batch.target_status}", lang),
        )
        if confirming
        else t("batch.heading", lang, n=len(selected), limit=MAX_SELECTED)
    )
    lines = [title, ""]
    rows = []
    previous_store = object()
    for item in items:
        name = names.get(item.raw_name, item.raw_name)
        # Telegram text limit and button widths must not depend on OCR length.
        name = name if len(name) <= 80 else name[:77] + "…"
        if batch.sort_by == "store":
            store = stores.get(item.source_receipt_id or 0) or t(
                "pantry.unknown_store", lang
            )
            if store.casefold() != previous_store:
                lines.append("🏪 " + store)
                previous_store = store.casefold()
        check = "☑" if item.id in selected else "☐"
        label = f"{check} #{item.id} {name}"
        if item.status != "active":
            label += " · " + t(f"batch.status.{item.status}", lang)
        lines.append(label)
        if not confirming and (item.status == "active" or item.id in selected):
            rows.append([CallbackButton(label, prefix + f"toggle:{item.id}")])
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    lines.extend(["", t("batch.page", lang, page=batch.page + 1, pages=pages)])
    nav = []
    if batch.page > 0:
        nav.append(CallbackButton("‹", prefix + f"page:{batch.page - 1}"))
    if batch.page + 1 < pages:
        nav.append(CallbackButton("›", prefix + f"page:{batch.page + 1}"))
    if nav:
        rows.append(nav)
    if confirming:
        rows.append([CallbackButton(t("batch.apply", lang), prefix + "apply")])
        rows.append([CallbackButton(t("batch.edit", lang), prefix + "edit")])
    else:
        rows.append(
            [
                CallbackButton(t("batch.select_page", lang), prefix + "select_page"),
                CallbackButton(t("batch.clear", lang), prefix + "clear"),
            ]
        )
        if selected:
            rows.append(
                [
                    CallbackButton(t("btn.ate", lang), prefix + "confirm:eaten"),
                    CallbackButton(t("btn.tossed", lang), prefix + "confirm:tossed"),
                ]
            )
            rows.append(
                [CallbackButton(t("btn.remove", lang), prefix + "confirm:removed")]
            )
    rows.append([CallbackButton(t("btn.cancel", lang), prefix + "cancel")])
    return "\n".join(lines), rows
