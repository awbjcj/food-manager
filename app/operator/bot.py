from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta

from aiogram import Dispatcher
from aiogram.filters import Command
from sqlmodel import select

from app.billing.entitlement import apply_refund, apply_topup, revoke_topup
from app.billing.ledger import find_event, record_payment, revenue_stars
from app.billing.plans import Sku, sku_for
from app.models import Household, PaymentEvent, User
from app.operator.auth import require_operator
from app.operator.queries import describe_household

log = logging.getLogger(__name__)
_MAX_GRANT = 1_000_000


def _parts(text: str | None) -> list[str]:
    return (text or "").split()


def _int_arg(text: str | None) -> int | None:
    parts = _parts(text)
    try:
        return int(parts[1])
    except (IndexError, ValueError):
        return None


async def handle_whois(msg, *, session_factory, now_provider):
    if not await require_operator(msg):
        return
    telegram_id = _int_arg(msg.text)
    if telegram_id is None:
        await msg.answer("usage: /whois <telegram_id>")
        return
    with session_factory() as session:
        report = describe_household(
            session, telegram_id=telegram_id, now=now_provider("UTC")
        )
    if report is None:
        await msg.answer(f"no user {telegram_id}")
        return
    await msg.answer(
        f"user {telegram_id}\nhousehold {report.household_id}\n"
        f"tier {report.tier} ({report.status})\nmembers {len(report.members)}/{report.seat_cap}\n"
        f"banned {report.banned_members or 'none'}\nreceipts {report.receipts_used}\n"
        f"actions {report.actions_used}\nspend {report.cost_micros_used} micros\n"
        f"period ends {report.period_end:%Y-%m-%d}"
    )


async def handle_grant(msg, *, session_factory, now_provider):
    if not await require_operator(msg):
        return
    parts = _parts(msg.text)
    try:
        household_id, receipts, actions = map(int, parts[1:4])
        valid = (
            len(parts) == 4
            and 0 <= receipts <= _MAX_GRANT
            and 0 <= actions <= _MAX_GRANT
            and (receipts > 0 or actions > 0)
        )
        if not valid:
            raise ValueError
    except (ValueError, TypeError):
        await msg.answer("usage: /grant <household_id> <receipts> <actions>")
        return
    now = now_provider("UTC")
    cost_headroom = receipts * 25_000 + actions * 8_334
    grant = Sku(
        "operator_grant",
        "Operator grant",
        "Comped quota",
        0,
        "grant",
        grants_receipts=receipts,
        grants_actions=actions,
        grants_cost_micros=cost_headroom,
    )
    try:
        with session_factory() as session:
            if session.get(Household, household_id) is None:
                raise ValueError("unknown household")
            record_payment(
                session,
                household_id=household_id,
                charge_id=f"grant:{uuid.uuid4()}",
                kind="grant",
                sku=grant.code,
                stars=0,
                payer_telegram_id=msg.from_user.id,
                payload_json=json.dumps({"receipts": receipts, "actions": actions}),
                now=now,
            )
            apply_topup(session, household_id=household_id, sku=grant, now=now)
            session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("grant_failed", extra={"error_class": type(exc).__name__})
        await msg.answer("grant failed")
        return
    await msg.answer(
        f"granted household {household_id}: +{receipts} receipts, +{actions} actions"
    )


async def handle_refund(msg, *, session_factory, now_provider, payments=None):
    if not await require_operator(msg):
        return
    parts = _parts(msg.text)
    if len(parts) != 2:
        await msg.answer("usage: /refund <telegram_payment_charge_id>")
        return
    if payments is None:
        await msg.answer("payments are not configured")
        return
    charge_id = parts[1]
    refund_id = f"refund:{charge_id}"
    with session_factory() as session:
        if find_event(session, charge_id=refund_id) is not None:
            await msg.answer(f"{charge_id} was already refunded")
            return
        original = find_event(session, charge_id=charge_id)
        if original is None or original.kind not in {"subscription", "topup"}:
            await msg.answer(f"unknown charge {charge_id}")
            return
        household_id = original.household_id
        payer_id = original.payer_telegram_id
        sku = original.sku
        original_kind = original.kind
        stars = original.stars
    if not await payments.refund(user_id=payer_id, charge_id=charge_id):
        await msg.answer("the rail refused the refund; nothing changed")
        return
    now = now_provider("UTC")
    with session_factory() as session:
        record_payment(
            session,
            household_id=household_id,
            charge_id=refund_id,
            kind="refund",
            sku=sku,
            stars=stars,
            payer_telegram_id=payer_id,
            payload_json="{}",
            now=now,
        )
        if original_kind == "subscription":
            apply_refund(session, household_id=household_id, now=now)
        else:
            topup = sku_for(sku)
            if topup is None:
                raise ValueError(f"unknown top-up SKU {sku}")
            revoke_topup(session, household_id=household_id, sku=topup, now=now)
        session.commit()
    await msg.answer(f"refunded {charge_id}; household {household_id} downgraded")


async def _set_banned(msg, *, session_factory, banned: bool):
    if not await require_operator(msg):
        return
    telegram_id = _int_arg(msg.text)
    if telegram_id is None:
        await msg.answer(
            "usage: /ban <telegram_id>" if banned else "usage: /unban <telegram_id>"
        )
        return
    with session_factory() as session:
        user = session.get(User, telegram_id)
        if user is None:
            await msg.answer(f"no user {telegram_id}")
            return
        user.banned = banned
        session.add(user)
        session.commit()
    await msg.answer(("banned" if banned else "unbanned") + f" {telegram_id}")


async def handle_ban(msg, *, session_factory):
    await _set_banned(msg, session_factory=session_factory, banned=True)


async def handle_unban(msg, *, session_factory):
    await _set_banned(msg, session_factory=session_factory, banned=False)


async def handle_revenue(msg, *, session_factory, now_provider):
    if not await require_operator(msg):
        return
    days = _int_arg(msg.text) or 30
    if not 1 <= days <= 3650:
        await msg.answer("usage: /revenue [days: 1..3650]")
        return
    with session_factory() as session:
        stars = revenue_stars(session, since=now_provider("UTC") - timedelta(days=days))
    await msg.answer(f"last {days}d: {stars} Stars")


async def handle_reconcile(msg, *, session_factory, payments=None):
    if not await require_operator(msg):
        return
    if payments is None:
        await msg.answer("payments are not configured")
        return
    remote: dict[str, int] = {}
    offset = 0
    page_size = 100
    while True:
        page = await payments.list_transactions(offset=offset, limit=page_size)
        for row in page:
            if row.stars > 0:
                remote[row.charge_id] = row.stars
        if len(page) < page_size:
            break
        offset += len(page)
    with session_factory() as session:
        events = session.exec(select(PaymentEvent)).all()
    local = {
        event.telegram_charge_id: event.stars
        for event in events
        if event.kind in {"subscription", "topup"}
    }
    remote_only = sorted(remote.keys() - local.keys())
    local_only = sorted(local.keys() - remote.keys())
    mismatched = sorted(
        key for key in remote.keys() & local.keys() if remote[key] != local[key]
    )
    if not (remote_only or local_only or mismatched):
        await msg.answer(f"no drift ({len(remote)} transactions)")
        return
    lines = []
    if remote_only:
        lines.append("remote only: " + ", ".join(remote_only))
    if local_only:
        lines.append("local only: " + ", ".join(local_only))
    if mismatched:
        lines.append("amount mismatch: " + ", ".join(mismatched))
    await msg.answer("\n".join(lines))


OPERATOR_COMMANDS = (
    (
        "whois",
        handle_whois,
        ("session_factory", "now_provider"),
        "<telegram_id> - look up a user's household: tier, seat usage, bans, quota spend",
    ),
    (
        "grant",
        handle_grant,
        ("session_factory", "now_provider"),
        "<household_id> <receipts> <actions> - comp extra quota to a household",
    ),
    (
        "refund",
        handle_refund,
        ("session_factory", "now_provider", "payments"),
        "<telegram_payment_charge_id> - refund a Stars payment via the rail and downgrade the household",
    ),
    (
        "ban",
        handle_ban,
        ("session_factory",),
        "<telegram_id> - ban a user; they are deauthorized on their next message",
    ),
    (
        "unban",
        handle_unban,
        ("session_factory",),
        "<telegram_id> - lift a ban on a user",
    ),
    (
        "revenue",
        handle_revenue,
        ("session_factory", "now_provider"),
        "[days=30] - total Stars revenue over the trailing window",
    ),
    (
        "reconcile",
        handle_reconcile,
        ("session_factory", "payments"),
        "compare the local ledger against the payment rail and report any drift",
    ),
)


def _help_text() -> str:
    lines = [f"/{name} {usage}" for name, _, _, usage in OPERATOR_COMMANDS]
    return "operator commands:\n" + "\n".join(lines)


async def handle_help(msg):
    if not await require_operator(msg):
        return
    await msg.answer(_help_text())


async def handle_unknown(msg):
    if not await require_operator(msg):
        return
    await msg.answer(f"unknown command.\n{_help_text()}")


def build_operator_dispatcher(
    *, session_factory, now_provider, payments=None
) -> Dispatcher:
    dispatcher = Dispatcher()
    deps = {
        "session_factory": session_factory,
        "now_provider": now_provider,
        "payments": payments,
    }
    dispatcher.message.register(handle_help, Command("help"))
    for name, handler, dep_names, _usage in OPERATOR_COMMANDS:
        kwargs = {key: deps[key] for key in dep_names}

        async def registered(event, _handler=handler, _kwargs=kwargs):
            await _handler(event, **_kwargs)

        dispatcher.message.register(registered, Command(name))
    dispatcher.message.register(handle_unknown)
    return dispatcher
