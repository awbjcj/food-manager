from datetime import datetime, timezone

from sqlmodel import Session, SQLModel, create_engine, select

from app.household_service import provision_solo_household
from app.models import Household, User


def test_provision_creates_one_household_and_links_user():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(telegram_id=5, chat_id=5, household_id=0,
                    created_at=datetime.now(timezone.utc))
        household = provision_solo_household(db, user)
        assert household.id is not None
        assert user.household_id == household.id
        assert db.exec(select(Household)).all() == [household]
