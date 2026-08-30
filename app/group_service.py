from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, select

from app.models import GroupBinding, User


class GroupBindingError(ValueError):
    pass


class GroupBindingUnauthorized(GroupBindingError):
    pass


@dataclass(frozen=True)
class BindResult:
    binding: GroupBinding
    created: bool


def get_group_binding(session: Session, *, chat_id: int) -> GroupBinding | None:
    return session.get(GroupBinding, chat_id)


def bind_group(
    session: Session,
    *,
    chat_id: int,
    household_id: int,
    bound_by_user_id: int,
    created_at: datetime,
) -> BindResult:
    actor = session.get(User, bound_by_user_id)
    if (
        actor is None
        or actor.banned
        or actor.household_id != household_id
    ):
        raise GroupBindingUnauthorized("binder is not an active household member")

    existing = get_group_binding(session, chat_id=chat_id)
    if existing is not None:
        existing.household_id = household_id
        existing.bound_by_user_id = bound_by_user_id
        existing.created_at = created_at
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return BindResult(existing, created=False)

    binding = GroupBinding(
        chat_id=chat_id,
        household_id=household_id,
        bound_by_user_id=bound_by_user_id,
        created_at=created_at,
    )
    session.add(binding)
    session.commit()
    session.refresh(binding)
    return BindResult(binding, created=True)


def transfer_group_bindings(
    session: Session,
    *,
    household_id: int,
    from_user_id: int,
    to_user_id: int,
) -> int:
    """Keep group-binding audit references valid when a member leaves."""
    replacement = session.get(User, to_user_id)
    if replacement is None or replacement.household_id != household_id:
        raise GroupBindingUnauthorized("replacement is not a household member")
    rows = session.exec(
        select(GroupBinding).where(
            GroupBinding.household_id == household_id,
            GroupBinding.bound_by_user_id == from_user_id,
        )
    ).all()
    for row in rows:
        row.bound_by_user_id = to_user_id
        session.add(row)
    return len(rows)
