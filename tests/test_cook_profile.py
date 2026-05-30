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
from app.models import User
from app.profile_service import (
    FoodProfile,
    apply_profile_to_user,
    profile_from_user,
    update_profile_from_sentence,
)
from app.renderer import render_profile
from tests.fakes import FakeProfileLLMClient


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


def test_migration_0004_adds_profile_columns(tmp_path):
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
    cols = {c["name"] for c in sa.inspect(engine).get_columns("user")}
    assert {
        "diet",
        "exclusions_json",
        "preferred_cuisines_json",
        "max_cook_minutes",
        "household_size",
        "profile_note",
    } <= cols


def test_profile_round_trips_through_user():
    user = User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc))
    profile = FoodProfile(
        diet="vegetarian",
        exclusions=["peanut", "cilantro"],
        preferred_cuisines=["chinese", "american"],
        max_cook_minutes=30,
        household_size=2,
        note="prefer one-pot meals",
    )
    apply_profile_to_user(user, profile)
    assert user.diet == "vegetarian"
    assert user.exclusions_json == '["peanut", "cilantro"]'
    assert profile_from_user(user) == profile


def test_profile_defaults_from_blank_user():
    user = User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc))
    assert profile_from_user(user) == FoodProfile()


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
        user = User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc))
        db.add(user)
        db.commit()
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
                user=user,
                sentence="veggie, no peanuts, chinese, spicy ok",
            )
        )
        assert cost == 7
        db.refresh(user)
        assert profile_from_user(user) == merged
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
        db.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        db.commit()
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
        db.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        db.commit()
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
