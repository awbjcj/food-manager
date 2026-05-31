from datetime import datetime, timezone
from sqlmodel import Session, SQLModel, create_engine
from app.models import User, Household


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_user_lang_defaults_to_en():
    with _session() as s:
        hh = Household(created_at=datetime.now(timezone.utc))
        s.add(hh)
        s.commit()
        s.refresh(hh)
        assert hh.id is not None
        u = User(telegram_id=1, chat_id=1, household_id=hh.id,
                 created_at=datetime.now(timezone.utc))
        s.add(u)
        s.commit()
        s.refresh(u)
        assert u.lang == "en"
