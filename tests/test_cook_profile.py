import asyncio
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import sqlalchemy as sa
from sqlmodel import SQLModel, Session, create_engine

import app.bot as bot_mod
from app.bot import handle_prefs
from app.models import Household, User
from app.profile_service import (
    FoodProfile,
    apply_profile_to_household,
    profile_from_household,
    update_profile_from_sentence,
)
from app.renderer import render_profile
from tests.fakes import FakeProfileLLMClient


def _session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _add_household_user(db: Session) -> Household:
    household = Household(created_at=datetime.now(timezone.utc))
    db.add(household)
    db.commit()
    db.refresh(household)
    assert household.id is not None
    db.add(User(telegram_id=1, chat_id=1, household_id=household.id,
                created_at=datetime.now(timezone.utc)))
    db.commit()
    return household


def test_household_has_profile_columns_with_defaults():
    with _session() as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.diet == "none"
        assert household.exclusions_json == "[]"
        assert household.preferred_cuisines_json == "[]"
        assert household.max_cook_minutes is None
        assert household.household_size == 1
        assert household.profile_note == ""


def test_migration_head_moves_profile_columns_to_household(tmp_path):
    db_path = tmp_path / "m.db"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**_env(), "DATABASE_PATH": str(db_path)},
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 0, result.stderr
    engine = sa.create_engine(f"sqlite:///{db_path}")
    inspector = sa.inspect(engine)
    household_cols = {c["name"] for c in inspector.get_columns("household")}
    assert {
        "diet",
        "exclusions_json",
        "preferred_cuisines_json",
        "max_cook_minutes",
        "household_size",
        "profile_note",
    } <= household_cols
    user_cols = {c["name"] for c in inspector.get_columns("user")}
    assert "diet" not in user_cols
    assert "profile_note" not in user_cols


def test_profile_round_trips_through_household():
    household = Household(name="x", created_at=datetime.now(timezone.utc))
    profile = FoodProfile(
        diet="vegetarian",
        exclusions=["peanut", "cilantro"],
        preferred_cuisines=["chinese", "american"],
        max_cook_minutes=30,
        household_size=2,
        note="prefer one-pot meals",
    )
    apply_profile_to_household(household, profile)
    assert household.diet == "vegetarian"
    assert household.exclusions_json == '["peanut", "cilantro"]'
    assert profile_from_household(household) == profile


def test_profile_defaults_from_blank_household():
    household = Household(created_at=datetime.now(timezone.utc))
    assert profile_from_household(household) == FoodProfile()


def test_render_profile_shows_fields():
    text = render_profile(FoodProfile(
        diet="vegetarian", exclusions=["peanut"], preferred_cuisines=["chinese"],
        max_cook_minutes=30, household_size=2, note="spicy ok",
    ))
    assert "vegetarian" in text
    assert "peanut" in text
    assert "chinese" in text
    assert "30" in text
    assert "spicy ok" in text


def test_fake_profile_client_returns_merged_profile():
    merged = FoodProfile(diet="vegan", exclusions=["peanut"])
    fake = FakeProfileLLMClient(canned=(merged, 42))
    result, cost = asyncio.run(
        fake.parse_profile_update(current=FoodProfile(), sentence="I'm vegan, no peanuts")
    )
    assert result == merged
    assert cost == 42
    assert fake.calls[0]["sentence"] == "I'm vegan, no peanuts"


def test_update_profile_persists_merge_and_keeps_allergy_structured():
    with _session() as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        merged = FoodProfile(
            diet="vegetarian",
            exclusions=["peanut"],
            preferred_cuisines=["chinese"],
            note="spicy ok",
        )
        fake = FakeProfileLLMClient(canned=(merged, 7))
        profile, cost = asyncio.run(
            update_profile_from_sentence(
                db,
                llm=fake,
                household=household,
                sentence="veggie, no peanuts, chinese, spicy ok",
            )
        )
        assert cost == 7
        db.refresh(household)
        assert profile_from_household(household) == merged
        assert "peanut" in profile.exclusions


class _Msg:
    def __init__(self, text, user_id=1, chat_id=1):
        self.text = text
        self.from_user = type("U", (), {"id": user_id})
        self.chat = type("C", (), {"id": chat_id, "type": "private"})
        self.answer = AsyncMock()


def test_handle_prefs_no_args_shows_profile(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        _add_household_user(db)
    msg = _Msg("/prefs")
    fake = FakeProfileLLMClient(canned=(FoodProfile(), None))
    asyncio.run(handle_prefs(
        msg, session_factory=lambda: Session(engine), profile_llm=fake,
    ))
    assert "food profile" in msg.answer.call_args[0][0].lower()
    assert fake.calls == []


def test_handle_prefs_with_sentence_updates(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        _add_household_user(db)
    msg = _Msg("/prefs I'm vegan")
    fake = FakeProfileLLMClient(canned=(FoodProfile(diet="vegan"), None))
    asyncio.run(handle_prefs(
        msg, session_factory=lambda: Session(engine), profile_llm=fake,
    ))
    assert fake.calls[0]["sentence"] == "I'm vegan"
    assert "vegan" in msg.answer.call_args[0][0]


def _env():
    import os

    return {k: v for k, v in os.environ.items()}
