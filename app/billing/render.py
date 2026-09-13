from app.billing.meter import QuotaSnapshot
from app.billing.plans import ACTION_WEIGHTS
from app.i18n import t


def render_quota(snapshot: QuotaSnapshot, *, days_left: int, lang: str = "en") -> str:
    plan = t(f"quota.plan.{snapshot.tier}", lang)
    receipts_limit = (
        snapshot.receipts_limit if snapshot.receipts_limit is not None else "∞"
    )
    actions_limit = (
        snapshot.actions_limit if snapshot.actions_limit is not None else "∞"
    )
    lines = [
        t("quota.title", lang, plan=plan, days=days_left),
        "",
        f"{t('quota.receipts', lang)}  {snapshot.receipts_used} / {receipts_limit}",
        f"{t('quota.actions', lang)}  {snapshot.actions_used} / {actions_limit}",
    ]
    for op in ("cook", "plan", "edit", "chat", "search"):
        count = snapshot.per_op.get(op, 0)
        weight = ACTION_WEIGHTS[op]
        lines.append(
            f"  {t(f'quota.op.{op}', lang)}  {count} × {weight} = {count * weight}"
        )
    lines.append(t("quota.multiplier_note", lang))
    if snapshot.tier == "unlimited":
        return "\n".join(lines)
    lines.extend(["", t("quota.need_more", lang)])
    if snapshot.tier == "free":
        lines.append(t("quota.free_upsell", lang))
    return "\n".join(lines)
