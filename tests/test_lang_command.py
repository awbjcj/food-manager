from datetime import UTC, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.commands import CommandError, parse_lang
from app.models import Household, User


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_user_lang_defaults_to_en():
    with _session() as s:
        hh = Household(created_at=datetime.now(UTC))
        s.add(hh)
        s.commit()
        s.refresh(hh)
        assert hh.id is not None
        u = User(telegram_id=1, chat_id=1, household_id=hh.id,
                 created_at=datetime.now(UTC))
        s.add(u)
        s.commit()
        s.refresh(u)
        assert u.lang == "en"


def test_parse_lang_none_when_no_args():
    assert parse_lang([]) is None


def test_parse_lang_lowercases_and_validates():
    assert parse_lang(["ZH"]) == "zh"
    assert parse_lang(["fr"]) == "fr"


def test_parse_lang_rejects_unknown():
    with pytest.raises(CommandError):
        parse_lang(["klingon"])


def test_parse_lang_rejects_too_many_args():
    with pytest.raises(CommandError):
        parse_lang(["en", "zh"])
