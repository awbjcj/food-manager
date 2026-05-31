from __future__ import annotations

from datetime import datetime

from sqlmodel import Session

from app.models import Household, User


def provision_solo_household(
    session: Session, user: User, *, created_at: datetime
) -> Household:
    """Create a household-of-one and link the user to it."""
    household = Household(name="My Household", created_at=created_at)
    session.add(household)
    session.flush()
    assert household.id is not None
    user.household_id = household.id
    session.add(user)
    session.commit()
    session.refresh(household)
    session.refresh(user)
    return household


def restore_household_for_user(
    session: Session, user: User, *, created_at: datetime
) -> Household:
    """Recreate the household row a user already references.

    Recovery path for when a user's ``household_id`` points at a missing row.
    Reuses the existing ``household_id`` so rows already keyed to it stay
    linked, rather than orphaning them under a freshly minted household.
    """
    household = Household(
        id=user.household_id, name="My Household", created_at=created_at
    )
    session.add(household)
    session.commit()
    session.refresh(household)
    return household
