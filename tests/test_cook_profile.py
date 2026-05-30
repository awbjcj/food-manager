from datetime import datetime, timezone

from sqlmodel import SQLModel, Session, create_engine

from app.models import User


def _session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_user_has_profile_columns_with_defaults():
    with _session() as db:
        user = User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc))
        db.add(user)
        db.commit()
        db.refresh(user)
        assert user.diet == "none"
        assert user.exclusions_json == "[]"
        assert user.preferred_cuisines_json == "[]"
        assert user.max_cook_minutes is None
        assert user.household_size == 1
        assert user.profile_note == ""
