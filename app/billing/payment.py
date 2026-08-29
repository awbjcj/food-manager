from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from aiogram import Bot
from aiogram.types import LabeledPrice

from app.billing.plans import Sku


@dataclass(frozen=True)
class LedgerRow:
    charge_id: str
    stars: int
    payer_telegram_id: int | None


class PaymentProvider(Protocol):
    async def create_checkout(self, *, sku: Sku, household_id: int) -> str: ...
    async def refund(self, *, user_id: int, charge_id: str) -> bool: ...
    async def cancel_subscription(self, *, user_id: int, charge_id: str) -> bool: ...
    async def list_transactions(
        self, *, offset: int, limit: int
    ) -> list[LedgerRow]: ...


def invoice_payload(sku_code: str, household_id: int) -> str:
    return json.dumps({"sku": sku_code, "hh": household_id}, separators=(",", ":"))


def parse_payload(raw: str) -> tuple[str, int]:
    try:
        value = json.loads(raw)
        sku = value["sku"]
        household_id = value["hh"]
        if not isinstance(sku, str) or not isinstance(household_id, int):
            raise TypeError("invalid payload types")
        return sku, household_id
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid invoice payload") from exc


class StarsPaymentProvider:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def create_checkout(self, *, sku: Sku, household_id: int) -> str:
        return await self._bot.create_invoice_link(
            title=sku.title,
            description=sku.description,
            payload=invoice_payload(sku.code, household_id),
            currency="XTR",
            provider_token="",
            prices=[LabeledPrice(label=sku.title, amount=sku.stars)],
            subscription_period=sku.subscription_period,
        )

    async def refund(self, *, user_id: int, charge_id: str) -> bool:
        return await self._bot.refund_star_payment(
            user_id=user_id, telegram_payment_charge_id=charge_id
        )

    async def cancel_subscription(self, *, user_id: int, charge_id: str) -> bool:
        return await self._bot.edit_user_star_subscription(
            user_id=user_id,
            telegram_payment_charge_id=charge_id,
            is_canceled=True,
        )

    async def list_transactions(self, *, offset: int, limit: int) -> list[LedgerRow]:
        page = await self._bot.get_star_transactions(offset=offset, limit=limit)
        rows = []
        for transaction in page.transactions:
            user = getattr(getattr(transaction, "source", None), "user", None)
            rows.append(
                LedgerRow(
                    transaction.id,
                    transaction.amount,
                    getattr(user, "id", None),
                )
            )
        return rows
