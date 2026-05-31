import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

from sqlmodel import Session, SQLModel, create_engine, select

import app.bot as bot_mod
from app.bot import handle_callback, run_cook_and_render
from app.cook_models import (
    NutritionScore,
    NutritionScores,
    RecipeCandidate,
    RecipeCandidates,
    RecipeIngredient,
    ScoredCandidate,
    SelectedItems,
)
from app.favorites_service import list_saved
from app.models import CookSession, Household, PantryItem, SavedRecipe, ShoppingList, User
from app.shopping_service import list_pending
from tests.fakes import FakeNutritionLLM, FakeRecipeLLM, FakeSelectionLLM


class _CbMessage:
    def __init__(self, chat_id=1, message_id=99):
        self.chat = type("C", (), {"id": chat_id})
        self.message_id = message_id
        self.edit_text = AsyncMock()
        self.answer = AsyncMock()


class _Cb:
    def __init__(self, data, user_id=1):
        self.data = data
        self.from_user = type("U", (), {"id": user_id})
        self.message = _CbMessage()
        self.answer = AsyncMock()


def _engine_with_user():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id is not None
        db.add(User(telegram_id=1, chat_id=1, household_id=household.id,
                    created_at=datetime.now(timezone.utc)))
        db.commit()
    return engine


def _NOW(tz):
    return datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)


def _scored(title="Pasta", ingredients=("pasta", "tomato"), shopping=("pasta",)):
    rec = RecipeCandidate(
        title=title, cuisine="italian", source_url="https://x",
        ingredients=[RecipeIngredient(name=n) for n in ingredients],
        method_gist="boil", deliciousness=0.7)
    nut = NutritionScore(health_score=80, effort="easy", est_minutes=20, rationale="ok")
    return ScoredCandidate(recipe=rec, nutrition=nut, expiry_use=0.5,
                           final_score=0.7, shopping_list=list(shopping))


def _add_done_cook(engine, candidates):
    now = datetime(2026, 5, 30, 12, 0)
    with Session(engine) as db:
        db.add(CookSession(
            household_id=1, status="done", chat_id=1, selected_item_ids="[]",
            candidates_json=json.dumps([c.model_dump() for c in candidates]),
            chosen_index=0, created_at=now, expires_at=now))
        db.commit()
        return db.exec(select(CookSession)).all()[0].id


def test_feedback_callback_records_liked(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    cook_id = _add_done_cook(engine, [_scored()])
    asyncio.run(handle_callback(
        _Cb(f"cookfb:{cook_id}:liked"),
        session_factory=lambda: Session(engine), now_provider=_NOW))
    with Session(engine) as db:
        cs = db.get(CookSession, cook_id)
        assert cs is not None
        assert cs.feedback == "liked"


def test_feedback_callback_on_expired_session_is_rejected(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    now = datetime(2026, 5, 30, 12, 0)
    with Session(engine) as db:
        db.add(CookSession(household_id=1, status="expired", chat_id=1,
                           selected_item_ids="[]", created_at=now, expires_at=now))
        db.commit()
        cook_id = db.exec(select(CookSession)).all()[0].id
    cb = _Cb(f"cookfb:{cook_id}:liked")
    asyncio.run(handle_callback(
        cb, session_factory=lambda: Session(engine), now_provider=_NOW))
    cb.answer.assert_awaited_with("this cook session expired - start a new /cook")
    with Session(engine) as db:
        cs = db.get(CookSession, cook_id)
        assert cs is not None
        assert cs.feedback == "none"


def test_save_callback_creates_favorite(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    cook_id = _add_done_cook(engine, [_scored()])
    cb = _Cb(f"cooksave:{cook_id}")
    asyncio.run(handle_callback(
        cb, session_factory=lambda: Session(engine), now_provider=_NOW))
    with Session(engine) as db:
        saved = list_saved(db, household_id=1)
        assert len(saved) == 1 and saved[0].title == "Pasta"
    cb.answer.assert_awaited()


def test_shop_callback_adds_missing_against_pantry(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    today = date(2026, 5, 30)
    with Session(engine) as db:
        db.add(PantryItem(
            household_id=1, raw_name="tomato", normalized_name="tomato", category="produce",
            qty=1.0, purchased_on=today, shelf_life_days=5, shelf_life_source="llm",
            ingest_shelf_life_source="llm", expires_on=date(2026, 6, 5), status="active",
            created_via="receipt", created_at=datetime.now(timezone.utc)))
        db.commit()
    cook_id = _add_done_cook(engine, [_scored(ingredients=("pasta", "tomato"))])
    asyncio.run(handle_callback(
        _Cb(f"cookshop:{cook_id}"),
        session_factory=lambda: Session(engine), now_provider=_NOW))
    with Session(engine) as db:
        pending = list_pending(db, household_id=1)
        assert [p.name_normalized for p in pending] == ["pasta"]  # tomato already owned


def test_shopdone_callback_checks_off(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    now = datetime(2026, 5, 30, 12, 0)
    with Session(engine) as db:
        db.add(ShoppingList(household_id=1, name_raw="Eggs", name_normalized="eggs",
                            added_at=now))
        db.commit()
        sid = db.exec(select(ShoppingList)).all()[0].id
    asyncio.run(handle_callback(
        _Cb(f"shopdone:{sid}"),
        session_factory=lambda: Session(engine), now_provider=_NOW))
    with Session(engine) as db:
        assert list_pending(db, household_id=1) == []


def test_favcook_callback_replies_with_recipe(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    now = datetime(2026, 5, 30, 12, 0)
    with Session(engine) as db:
        db.add(SavedRecipe(household_id=1, title="Pasta", cuisine="italian", source_url="u",
                           ingredients_json=json.dumps([{"name": "pasta"}]),
                           method_gist="boil", saved_at=now))
        db.commit()
        rid = db.exec(select(SavedRecipe)).all()[0].id
    cb = _Cb(f"favcook:{rid}")
    asyncio.run(handle_callback(
        cb, session_factory=lambda: Session(engine), now_provider=_NOW))
    cb.message.answer.assert_awaited()
    assert cb.message.answer.await_args is not None
    assert "Pasta" in cb.message.answer.await_args.args[0]


def test_result_keyboard_attached_on_render(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    today_dt = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
    today = today_dt.date()
    with Session(engine) as db:
        for i in range(4):
            db.add(PantryItem(
                household_id=1, raw_name=f"item{i}", normalized_name=f"item{i}",
                category="produce", qty=1.0, purchased_on=today, shelf_life_days=2,
                shelf_life_source="llm", ingest_shelf_life_source="llm",
                expires_on=today + timedelta(days=2), status="active",
                created_via="receipt", created_at=datetime.now(timezone.utc)))
        now = today_dt.replace(tzinfo=None)
        db.add(CookSession(household_id=1, status="ready", chat_id=1, meal_type="Dinner",
                           cuisine="Italian", selected_item_ids="[]", message_id=99,
                           created_at=now, expires_at=now + timedelta(minutes=10)))
        db.commit()
        cook_id = db.exec(select(CookSession)).all()[0].id

    recipe = FakeRecipeLLM(canned=(RecipeCandidates(candidates=[
        RecipeCandidate(title="Safe", cuisine="italian", source_url="u",
                        ingredients=[RecipeIngredient(name="item0")],
                        method_gist="x", deliciousness=0.6)]), 9))
    nutrition = FakeNutritionLLM(canned=(NutritionScores(scores=[
        NutritionScore(health_score=80, effort="easy", est_minutes=20, rationale="ok")]), 3))
    edit_mock = AsyncMock()
    bot = type("B", (), {"edit_message_text": edit_mock})()
    assert cook_id is not None
    asyncio.run(run_cook_and_render(
        lambda: Session(engine), user_id=1, household_id=1, user_tz="America/Detroit",
        cook_id=cook_id,
        selection_llm=FakeSelectionLLM(canned=(SelectedItems(item_ids=[]), 5)),
        recipe_llm=recipe, nutrition_llm=nutrition,
        now_provider=lambda tz: today_dt, bot=bot))
    assert edit_mock.await_args is not None
    kwargs = edit_mock.await_args.kwargs
    data = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert f"cooksave:{cook_id}" in data
    assert f"cookfb:{cook_id}:liked" in data


def test_no_result_keyboard_when_no_recipe_found(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    today_dt = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
    today = today_dt.date()
    with Session(engine) as db:
        for i in range(4):
            db.add(PantryItem(
                household_id=1, raw_name=f"item{i}", normalized_name=f"item{i}",
                category="produce", qty=1.0, purchased_on=today, shelf_life_days=2,
                shelf_life_source="llm", ingest_shelf_life_source="llm",
                expires_on=today + timedelta(days=2), status="active",
                created_via="receipt", created_at=datetime.now(timezone.utc)))
        now = today_dt.replace(tzinfo=None)
        db.add(CookSession(household_id=1, status="ready", chat_id=1, meal_type="Dinner",
                           cuisine="Italian", selected_item_ids="[]", message_id=99,
                           created_at=now, expires_at=now + timedelta(minutes=10)))
        db.commit()
        cook_id = db.exec(select(CookSession)).all()[0].id

    # Recipe stage returns zero candidates -> run_cook returns [] (no recipe found).
    recipe = FakeRecipeLLM(canned=(RecipeCandidates(candidates=[]), 9))
    nutrition = FakeNutritionLLM(canned=(NutritionScores(scores=[]), 3))
    edit_mock = AsyncMock()
    bot = type("B", (), {"edit_message_text": edit_mock})()
    assert cook_id is not None
    asyncio.run(run_cook_and_render(
        lambda: Session(engine), user_id=1, household_id=1, user_tz="America/Detroit",
        cook_id=cook_id,
        selection_llm=FakeSelectionLLM(canned=(SelectedItems(item_ids=[]), 5)),
        recipe_llm=recipe, nutrition_llm=nutrition,
        now_provider=lambda tz: today_dt, bot=bot))
    assert edit_mock.await_args is not None
    kwargs = edit_mock.await_args.kwargs
    # No action buttons on a "couldn't find a recipe" message.
    assert kwargs["reply_markup"] is None
