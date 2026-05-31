"""Tests that user.lang is threaded into the display handlers."""
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

import app.bot as bot_mod
from app.bot import handle_favorites, handle_list, handle_shopping
from app.cook_models import RecipeCandidate, RecipeIngredient
from app.favorites_service import save_candidate
from app.models import Household, PantryItem, User
from app.shopping_service import add_missing
from tests.fakes import FakeTranslationLLM


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    def make():
        return Session(engine)

    with make() as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id is not None
        db.add(
            User(
                telegram_id=1,
                chat_id=99,
                household_id=household.id,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    return make


def _msg(text: str):
    msg = MagicMock()
    msg.from_user = MagicMock(id=1)
    msg.chat = MagicMock(id=99, type="private")
    msg.text = text
    msg.answer = AsyncMock()
    return msg


def _now(tz):
    return datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _add_pantry_item(session_factory, household_id: int, name: str) -> None:
    with session_factory() as db:
        item = PantryItem(
            household_id=household_id,
            raw_name=name,
            normalized_name=name.lower(),
            category="dairy",
            qty=1.0,
            unit="unit",
            purchased_on=date(2026, 5, 30),
            shelf_life_days=14,
            shelf_life_source="llm",
            ingest_shelf_life_source="llm",
            expires_on=date(2026, 6, 14),
            status="active",
            created_via="manual",
            created_at=datetime.now(timezone.utc),
        )
        db.add(item)
        db.commit()


def _set_user_lang(session_factory, lang: str) -> int:
    """Set lang on user 1 and return their household_id."""
    with session_factory() as db:
        user = db.get(User, 1)
        assert user is not None
        user.lang = lang
        db.add(user)
        db.commit()
        return user.household_id


# ---------------------------------------------------------------------------
# handle_list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_renders_zh_names_for_zh_user(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    household_id = _set_user_lang(session_factory, "zh")
    _add_pantry_item(session_factory, household_id, "Milk")

    fake = FakeTranslationLLM(table={"Milk": "牛奶"})
    msg = _msg("/list")
    await handle_list(
        msg,
        session_factory=session_factory,
        now_provider=_now,
        translation_llm=fake,
    )
    text = msg.answer.call_args.args[0]
    assert "牛奶" in text


@pytest.mark.asyncio
async def test_list_english_user_unaffected(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    household_id = _set_user_lang(session_factory, "en")
    _add_pantry_item(session_factory, household_id, "Milk")

    # No translation_llm — English user, should see English name unchanged.
    msg = _msg("/list")
    await handle_list(
        msg,
        session_factory=session_factory,
        now_provider=_now,
    )
    text = msg.answer.call_args.args[0]
    assert "Milk" in text


@pytest.mark.asyncio
async def test_list_zh_user_no_translation_llm_falls_back_to_english(
    session_factory, monkeypatch
):
    """translation_llm=None must never crash even for non-en users."""
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    household_id = _set_user_lang(session_factory, "zh")
    _add_pantry_item(session_factory, household_id, "Milk")

    msg = _msg("/list")
    await handle_list(
        msg,
        session_factory=session_factory,
        now_provider=_now,
        translation_llm=None,
    )
    text = msg.answer.call_args.args[0]
    assert "Milk" in text


# ---------------------------------------------------------------------------
# handle_favorites
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_favorites_renders_zh_names_for_zh_user(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    household_id = _set_user_lang(session_factory, "zh")

    with session_factory() as db:
        save_candidate(
            db,
            household_id=household_id,
            candidate=RecipeCandidate(
                title="Pasta",
                cuisine="italian",
                source_url="u",
                ingredients=[RecipeIngredient(name="pasta")],
                method_gist="boil",
            ),
            now=_now("x"),
        )

    fake = FakeTranslationLLM(table={"Pasta": "意面", "italian": "意大利菜"})
    msg = _msg("/favorites")
    await handle_favorites(
        msg,
        session_factory=session_factory,
        translation_llm=fake,
    )
    text = msg.answer.call_args.args[0]
    assert "意面" in text


@pytest.mark.asyncio
async def test_favorites_english_user_unaffected(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    household_id = _set_user_lang(session_factory, "en")

    with session_factory() as db:
        save_candidate(
            db,
            household_id=household_id,
            candidate=RecipeCandidate(
                title="Pasta",
                cuisine="italian",
                source_url="u",
                ingredients=[RecipeIngredient(name="pasta")],
                method_gist="boil",
            ),
            now=_now("x"),
        )

    msg = _msg("/favorites")
    await handle_favorites(msg, session_factory=session_factory)
    text = msg.answer.call_args.args[0]
    assert "Pasta" in text


# ---------------------------------------------------------------------------
# handle_shopping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shopping_renders_zh_names_for_zh_user(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    household_id = _set_user_lang(session_factory, "zh")

    with session_factory() as db:
        add_missing(
            db,
            household_id=household_id,
            ingredients=[RecipeIngredient(name="Eggs")],
            now=_now("x"),
        )

    fake = FakeTranslationLLM(table={"Eggs": "鸡蛋"})
    msg = _msg("/shopping")
    await handle_shopping(
        msg,
        session_factory=session_factory,
        now_provider=_now,
        translation_llm=fake,
    )
    text = msg.answer.call_args.args[0]
    assert "鸡蛋" in text
