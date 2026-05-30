from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlmodel import Session, select

from app.models import ShoppingList
from app.normalization import normalize
from app.pending_service import utc_naive


@dataclass
class AddShoppingResult:
    added: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)


def _pending_normalized(session: Session, *, user_id: int) -> set[str]:
    rows = session.exec(
        select(ShoppingList).where(
            ShoppingList.user_id == user_id,
            ShoppingList.bought_at.is_(None),  # type: ignore[union-attr]
        )
    ).all()
    return {row.name_normalized for row in rows}


def add_missing(
    session: Session, *, user_id: int, ingredients, now: datetime
) -> AddShoppingResult:
    existing = _pending_normalized(session, user_id=user_id)
    added_now = utc_naive(now)
    result = AddShoppingResult()
    for ingredient in ingredients:
        normalized = normalize(ingredient.name)
        if not normalized or normalized in existing:
            result.already.append(ingredient.name)
            continue
        existing.add(normalized)
        session.add(
            ShoppingList(
                user_id=user_id,
                name_raw=ingredient.name,
                name_normalized=normalized,
                qty=getattr(ingredient, "qty", None),
                unit=getattr(ingredient, "unit", None),
                added_at=added_now,
            )
        )
        result.added.append(ingredient.name)
    session.commit()
    return result


def list_pending(session: Session, *, user_id: int) -> list[ShoppingList]:
    return list(
        session.exec(
            select(ShoppingList)
            .where(
                ShoppingList.user_id == user_id,
                ShoppingList.bought_at.is_(None),  # type: ignore[union-attr]
            )
            .order_by(ShoppingList.added_at)  # type: ignore[arg-type]
        ).all()
    )


def check_off(
    session: Session, *, user_id: int, shopping_id: int, now: datetime
) -> bool:
    row = session.get(ShoppingList, shopping_id)
    if row is None or row.user_id != user_id or row.bought_at is not None:
        return False
    row.bought_at = utc_naive(now)
    session.add(row)
    session.commit()
    return True
