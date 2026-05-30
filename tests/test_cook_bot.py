import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from sqlmodel import SQLModel, Session, create_engine

import app.bot as bot_mod
from app.bot import handle_cook, handle_cook_callback, run_cook_and_render
from app.cook_models import (
    NutritionScore,
    NutritionScores,
    RecipeCandidate,
    RecipeCandidates,
    RecipeIngredient,
    SelectedItems,
)
from app.models import CookSession, PantryItem, User
from tests.fakes import FakeNutritionLLM, FakeRecipeLLM, FakeSelectionLLM


def _engine_with_user():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        db.commit()
    return engine


class _Msg:
    def __init__(self, text="/cook", user_id=1, chat_id=1):
        self.text = text
        self.from_user = type("U", (), {"id": user_id})
        self.chat = type("C", (), {"id": chat_id, "type": "private"})
        self.answer = AsyncMock(return_value=type("S", (), {"message_id": 99}))


class _Cb:
    def __init__(self, data, user_id=1, chat_id=1, message_id=99):
        self.data = data
        self.from_user = type("U", (), {"id": user_id})
        self.message = type("M", (), {
            "chat": type("C", (), {"id": chat_id}),
            "message_id": message_id,
            "edit_text": AsyncMock(),
            "answer": AsyncMock(),
        })()
        self.answer = AsyncMock()


def _recording_spawn():
    spawned = []

    def spawn(coro):
        spawned.append(coro)
        coro.close()

    return spawn, spawned


def _fakes():
    return dict(
        selection_llm=FakeSelectionLLM(),
        recipe_llm=FakeRecipeLLM(),
        nutrition_llm=FakeNutritionLLM(),
    )


_NOW = lambda tz: datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)


def test_cook_first_round_creates_session_and_asks_meal_type(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    msg = _Msg()
    asyncio.run(handle_cook(
        msg, session_factory=lambda: Session(engine),
        now_provider=lambda tz: datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc),
    ))
    msg.answer.assert_awaited()
    keyboard = msg.answer.await_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].callback_data.startswith("cookpick:")
    assert ":meal:" in keyboard.inline_keyboard[0][0].callback_data
    with Session(engine) as db:
        rows = db.exec(__import__("sqlmodel").select(CookSession)).all()
        assert len(rows) == 1 and rows[0].status == "collecting"


def test_cook_pick_advances_rounds_without_running_pipeline(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    asyncio.run(handle_cook(_Msg(), session_factory=lambda: Session(engine),
                            now_provider=_NOW))
    with Session(engine) as db:
        cook_id = db.exec(__import__("sqlmodel").select(CookSession)).all()[0].id

    spawn, spawned = _recording_spawn()

    asyncio.run(handle_cook_callback(
        _Cb(f"cookpick:{cook_id}:meal:0"), session_factory=lambda: Session(engine),
        now_provider=_NOW, spawn=spawn, bot=None, **_fakes()))
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook.meal_type == "Dinner" and cook.cuisine is None
        assert cook.status == "collecting"
    assert spawned == []

    asyncio.run(handle_cook_callback(
        _Cb(f"cookpick:{cook_id}:cuisine:0"), session_factory=lambda: Session(engine),
        now_provider=_NOW, spawn=spawn, bot=None, **_fakes()))
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook.cuisine == "Italian" and cook.status == "ready"
    assert len(spawned) == 1

    asyncio.run(handle_cook_callback(
        _Cb(f"cookpick:{cook_id}:cuisine:0"), session_factory=lambda: Session(engine),
        now_provider=_NOW, spawn=spawn, bot=None, **_fakes()))
    assert len(spawned) == 1


def test_cook_stale_meal_tap_after_round_one_does_not_pick_cuisine(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    asyncio.run(handle_cook(_Msg(), session_factory=lambda: Session(engine),
                            now_provider=_NOW))
    with Session(engine) as db:
        cook_id = db.exec(__import__("sqlmodel").select(CookSession)).all()[0].id

    spawn, spawned = _recording_spawn()
    asyncio.run(handle_cook_callback(
        _Cb(f"cookpick:{cook_id}:meal:0"), session_factory=lambda: Session(engine),
        now_provider=_NOW, spawn=spawn, bot=None, **_fakes()))
    asyncio.run(handle_cook_callback(
        _Cb(f"cookpick:{cook_id}:meal:0"), session_factory=lambda: Session(engine),
        now_provider=_NOW, spawn=spawn, bot=None, **_fakes()))

    assert spawned == []
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook.meal_type == "Dinner"
        assert cook.cuisine is None
        assert cook.status == "collecting"


def test_cook_legacy_stale_meal_tap_after_round_one_does_not_pick_cuisine(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    asyncio.run(handle_cook(_Msg(), session_factory=lambda: Session(engine),
                            now_provider=_NOW))
    with Session(engine) as db:
        cook_id = db.exec(__import__("sqlmodel").select(CookSession)).all()[0].id

    spawn, spawned = _recording_spawn()
    asyncio.run(handle_cook_callback(
        _Cb(f"cookpick:{cook_id}:meal:0"), session_factory=lambda: Session(engine),
        now_provider=_NOW, spawn=spawn, bot=None, **_fakes()))
    asyncio.run(handle_cook_callback(
        _Cb(f"cookpick:{cook_id}:0"), session_factory=lambda: Session(engine),
        now_provider=_NOW, spawn=spawn, bot=None, **_fakes()))

    assert spawned == []
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook.meal_type == "Dinner"
        assert cook.cuisine is None
        assert cook.status == "collecting"


def test_cook_first_round_edit_failure_does_not_commit_meal_type(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    asyncio.run(handle_cook(_Msg(), session_factory=lambda: Session(engine),
                            now_provider=_NOW))
    with Session(engine) as db:
        cook_id = db.exec(__import__("sqlmodel").select(CookSession)).all()[0].id

    cb = _Cb(f"cookpick:{cook_id}:meal:0")
    cb.message.edit_text.side_effect = RuntimeError("edit failed")
    spawn, spawned = _recording_spawn()
    asyncio.run(handle_cook_callback(
        cb, session_factory=lambda: Session(engine), now_provider=_NOW,
        spawn=spawn, bot=None, **_fakes()))

    cb.answer.assert_awaited_with("couldn't update this cook session - try /cook again")
    assert spawned == []
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook.meal_type is None
        assert cook.status == "collecting"


def test_cook_pick_expires_collecting_session_without_spawning(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    with Session(engine) as db:
        now = _NOW("America/Detroit").replace(tzinfo=None)
        db.add(CookSession(
            user_id=1,
            status="collecting",
            chat_id=1,
            selected_item_ids="[]",
            created_at=now - timedelta(minutes=20),
            expires_at=now - timedelta(minutes=1),
        ))
        db.commit()
        cook_id = db.exec(__import__("sqlmodel").select(CookSession)).all()[0].id

    cb = _Cb(f"cookpick:{cook_id}:meal:0")
    spawn, spawned = _recording_spawn()
    asyncio.run(handle_cook_callback(
        cb, session_factory=lambda: Session(engine), now_provider=_NOW,
        spawn=spawn, bot=None, **_fakes()))

    cb.answer.assert_awaited_with("this cook session expired - start a new /cook")
    assert spawned == []
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook.status == "expired"


def test_cook_callback_rejects_unauthorized(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    cb = _Cb("cookpick:1:0", user_id=999)
    spawn, spawned = _recording_spawn()
    asyncio.run(handle_cook_callback(
        cb, session_factory=lambda: Session(engine), now_provider=_NOW,
        spawn=spawn, bot=None, **_fakes()))
    cb.answer.assert_awaited_with("not authorized", show_alert=False)
    assert spawned == []


def test_run_cook_and_render_completes_and_edits(monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    today_dt = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
    today = today_dt.date()
    with Session(engine) as db:
        for i in range(4):
            db.add(PantryItem(
                user_id=1, raw_name=f"item{i}", normalized_name=f"item{i}",
                category="produce", qty=1.0, purchased_on=today, shelf_life_days=2,
                shelf_life_source="llm", ingest_shelf_life_source="llm",
                expires_on=today + timedelta(days=2), status="active",
                created_via="receipt", created_at=datetime.now(timezone.utc)))
        now = today_dt.replace(tzinfo=None)
        db.add(CookSession(user_id=1, status="ready", chat_id=1, meal_type="Dinner",
                           cuisine="Italian", selected_item_ids="[]", message_id=99,
                           created_at=now, expires_at=now + timedelta(minutes=10)))
        db.commit()
        cook_id = db.exec(__import__("sqlmodel").select(CookSession)).all()[0].id

    selection = FakeSelectionLLM(canned=(SelectedItems(item_ids=[]), 5))
    recipe = FakeRecipeLLM(canned=(RecipeCandidates(candidates=[
        RecipeCandidate(title="Safe", cuisine="italian", source_url="u",
                        ingredients=[
                            RecipeIngredient(name="item0"),
                            RecipeIngredient(name="pasta"),
                        ],
                        method_gist="x", deliciousness=0.6)]), 9))
    nutrition = FakeNutritionLLM(canned=(NutritionScores(scores=[
        NutritionScore(health_score=80, effort="easy", est_minutes=20, rationale="ok")]), 3))
    bot = type("B", (), {"edit_message_text": AsyncMock()})()

    asyncio.run(run_cook_and_render(
        lambda: Session(engine), user_id=1, user_tz="America/Detroit", cook_id=cook_id,
        selection_llm=selection, recipe_llm=recipe, nutrition_llm=nutrition,
        now_provider=lambda tz: today_dt, bot=bot,
    ))
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook.status == "done"
        assert "Safe" in (cook.candidates_json or "")
    bot.edit_message_text.assert_awaited()


def test_build_llm_clients_returns_cook_clients(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_ID", "1")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    from app.settings import Settings
    from bin.run import _build_llm_clients

    bundle = _build_llm_clients(Settings())  # type: ignore[call-arg]
    assert bundle.selection.default_provider == "anthropic"
    assert bundle.recipe.default_provider == "anthropic"
    assert bundle.nutrition.default_provider == "anthropic"
    assert bundle.profile.default_provider == "anthropic"
