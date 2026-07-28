import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from sqlmodel import Session, SQLModel, create_engine

import app.bot as bot_mod
import app.callbacks.cook as cook_callbacks
import app.callbacks.routes as callback_routes
from app import handler_support
from app.bot import handle_cook, handle_cook_callback, run_cook_and_render
from app.client_set import PerUserClients
from app.cook.models import (
    NutritionScore,
    NutritionScores,
    RecipeCandidate,
    RecipeCandidates,
    RecipeIngredient,
    SelectedItems,
)
from app.models import CookSession, Household, PantryItem, User
from tests.fakes import FakeNutritionLLM, FakeRecipeLLM, FakeSelectionLLM


def _engine_with_user():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        household = Household(created_at=datetime.now(UTC))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id is not None
        db.add(
            User(
                telegram_id=1,
                chat_id=1,
                household_id=household.id,
                created_at=datetime.now(UTC),
            )
        )
        db.commit()
    return engine


class _Msg:
    def __init__(self, text="/cook", user_id=1, chat_id=1):
        self.text = text
        self.from_user = type("U", (), {"id": user_id})
        self.chat = type("C", (), {"id": chat_id, "type": "private"})
        self.answer = AsyncMock(return_value=type("S", (), {"message_id": 99}))


class _CbMessage:
    def __init__(self, chat_id: int, message_id: int) -> None:
        self.chat = type("C", (), {"id": chat_id})
        self.message_id = message_id
        self.edit_text = AsyncMock()
        self.answer = AsyncMock()


class _Cb:
    def __init__(self, data, user_id=1, chat_id=1, message_id=99):
        self.data = data
        self.from_user = type("U", (), {"id": user_id})
        self.message = _CbMessage(chat_id, message_id)
        self.answer = AsyncMock()


def _recording_spawn():
    spawned = []

    def spawn(coro):
        spawned.append(coro)
        coro.close()

    return spawn, spawned


def _fakes():
    return {
        "clients": PerUserClients.for_tests(
            selection=FakeSelectionLLM(),
            recipe=FakeRecipeLLM(),
            nutrition=FakeNutritionLLM(),
        )
    }


def _NOW(tz):
    return datetime(2026, 5, 30, 12, 0, tzinfo=UTC)


def _seed_cook(engine, **overrides):
    now = _NOW("America/Detroit").replace(tzinfo=None)
    values = {
        "household_id": 1,
        "status": "done",
        "meal_type": "Dinner",
        "cuisine": "Italian",
        "purpose": "quick",
        "selected_item_ids": "[11, 12]",
        "candidates_json": "[]",
        "search_offset": 6,
        "chat_id": 1,
        "message_id": 99,
        "created_at": now - timedelta(minutes=1),
        "expires_at": now + timedelta(minutes=10),
    }
    values.update(overrides)
    with Session(engine) as db:
        cook = CookSession(**values)
        db.add(cook)
        db.commit()
        db.refresh(cook)
        assert cook.id is not None
        return cook.id


def test_cook_first_round_creates_session_and_asks_meal_type(monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    msg = _Msg()
    asyncio.run(
        handle_cook(
            msg,
            session_factory=lambda: Session(engine),
            now_provider=lambda tz: datetime(2026, 5, 30, 12, 0, tzinfo=UTC),
        )
    )
    msg.answer.assert_awaited()
    assert msg.answer.await_args is not None
    keyboard = msg.answer.await_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].callback_data.startswith("cookpick:")
    assert ":meal:" in keyboard.inline_keyboard[0][0].callback_data
    with Session(engine) as db:
        rows = db.exec(__import__("sqlmodel").select(CookSession)).all()
        assert len(rows) == 1 and rows[0].status == "collecting"


def test_cook_pick_advances_cuisine_to_purpose_then_ready_once(monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    asyncio.run(
        handle_cook(_Msg(), session_factory=lambda: Session(engine), now_provider=_NOW)
    )
    with Session(engine) as db:
        cook_id = db.exec(__import__("sqlmodel").select(CookSession)).all()[0].id

    spawn, spawned = _recording_spawn()

    meal_cb = _Cb(f"cookpick:{cook_id}:meal:0")
    asyncio.run(
        handle_cook_callback(
            meal_cb,
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook is not None
        assert cook.meal_type == "Dinner" and cook.cuisine is None
        assert cook.status == "collecting"
    assert spawned == []
    assert meal_cb.message.edit_text.await_args is not None
    quick_cuisine_keyboard = meal_cb.message.edit_text.await_args.kwargs["reply_markup"]
    quick_callbacks = [
        button.callback_data
        for row in quick_cuisine_keyboard.inline_keyboard
        for button in row
    ]
    assert f"cookmore:{cook_id}:cuisine_full" in quick_callbacks

    cuisine_cb = _Cb(f"cookpick:{cook_id}:cuisine:0")
    asyncio.run(
        handle_cook_callback(
            cuisine_cb,
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook is not None
        assert cook.cuisine == "Italian"
        assert cook.purpose is None
        assert cook.status == "collecting"
    assert spawned == []
    assert cuisine_cb.message.edit_text.await_args is not None
    purpose_keyboard = cuisine_cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert all(
        button.callback_data.startswith(f"cookpick:{cook_id}:purpose:")
        for row in purpose_keyboard.inline_keyboard
        for button in row
    )

    asyncio.run(
        handle_cook_callback(
            _Cb(f"cookpick:{cook_id}:purpose:0"),
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook is not None
        assert cook.purpose == "use_it_up"
        assert cook.status == "ready"
    assert len(spawned) == 1

    asyncio.run(
        handle_cook_callback(
            _Cb(f"cookpick:{cook_id}:purpose:0"),
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )
    assert len(spawned) == 1


def test_more_cuisines_selection_uses_the_full_list_index(monkeypatch):
    from app.bot import SPOONACULAR_CUISINES

    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    asyncio.run(
        handle_cook(_Msg(), session_factory=lambda: Session(engine), now_provider=_NOW)
    )
    with Session(engine) as db:
        cook_id = db.exec(__import__("sqlmodel").select(CookSession)).one().id

    spawn, spawned = _recording_spawn()
    asyncio.run(
        handle_cook_callback(
            _Cb(f"cookpick:{cook_id}:meal:0"),
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )

    expand_cb = _Cb(f"cookmore:{cook_id}:cuisine_full")
    asyncio.run(
        handle_cook_callback(
            expand_cb,
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )
    assert expand_cb.message.edit_text.await_args is not None
    keyboard = expand_cb.message.edit_text.await_args.kwargs["reply_markup"]
    full_callbacks = [
        button.callback_data for row in keyboard.inline_keyboard for button in row
    ]
    assert f"cookpick:{cook_id}:cuisine_full:7" in full_callbacks

    asyncio.run(
        handle_cook_callback(
            _Cb(f"cookpick:{cook_id}:cuisine_full:7"),
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )

    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook is not None
        assert cook.cuisine == SPOONACULAR_CUISINES[7]
        assert cook.purpose is None
        assert cook.status == "collecting"
    assert spawned == []


def test_adjust_resets_choices_but_preserves_selected_item_ids(monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    cook_id = _seed_cook(engine)
    cb = _Cb(f"cookadj:{cook_id}")
    spawn, spawned = _recording_spawn()

    asyncio.run(
        handle_cook_callback(
            cb,
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )

    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook is not None
        assert cook.status == "collecting"
        assert cook.meal_type == "Dinner"
        assert cook.cuisine is None
        assert cook.purpose is None
        assert cook.search_offset == 0
        assert cook.selected_item_ids == "[11, 12]"
    assert spawned == []
    assert cb.message.edit_text.await_args is not None
    keyboard = cb.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = [
        button.callback_data for row in keyboard.inline_keyboard for button in row
    ]
    assert f"cookmore:{cook_id}:cuisine_full" in callbacks


def test_cook_stale_meal_tap_after_round_one_does_not_pick_cuisine(monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    asyncio.run(
        handle_cook(_Msg(), session_factory=lambda: Session(engine), now_provider=_NOW)
    )
    with Session(engine) as db:
        cook_id = db.exec(__import__("sqlmodel").select(CookSession)).all()[0].id

    spawn, spawned = _recording_spawn()
    asyncio.run(
        handle_cook_callback(
            _Cb(f"cookpick:{cook_id}:meal:0"),
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )
    asyncio.run(
        handle_cook_callback(
            _Cb(f"cookpick:{cook_id}:meal:0"),
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )

    assert spawned == []
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook is not None
        assert cook.meal_type == "Dinner"
        assert cook.cuisine is None
        assert cook.status == "collecting"


def test_cook_legacy_stale_meal_tap_after_round_one_does_not_pick_cuisine(monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    asyncio.run(
        handle_cook(_Msg(), session_factory=lambda: Session(engine), now_provider=_NOW)
    )
    with Session(engine) as db:
        cook_id = db.exec(__import__("sqlmodel").select(CookSession)).all()[0].id

    spawn, spawned = _recording_spawn()
    asyncio.run(
        handle_cook_callback(
            _Cb(f"cookpick:{cook_id}:meal:0"),
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )
    asyncio.run(
        handle_cook_callback(
            _Cb(f"cookpick:{cook_id}:0"),
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )

    assert spawned == []
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook is not None
        assert cook.meal_type == "Dinner"
        assert cook.cuisine is None
        assert cook.status == "collecting"


def test_cook_first_round_edit_failure_resends_and_commits_meal_type(monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    asyncio.run(
        handle_cook(_Msg(), session_factory=lambda: Session(engine), now_provider=_NOW)
    )
    with Session(engine) as db:
        cook_id = db.exec(__import__("sqlmodel").select(CookSession)).all()[0].id

    cb = _Cb(f"cookpick:{cook_id}:meal:0")
    cb.message.edit_text.side_effect = RuntimeError("edit failed")
    spawn, spawned = _recording_spawn()
    asyncio.run(
        handle_cook_callback(
            cb,
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )

    cb.message.answer.assert_awaited_once()
    cb.answer.assert_awaited_with()
    assert spawned == []
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook is not None
        assert cook.meal_type == "Dinner"
        assert cook.status == "collecting"


def test_cook_pick_expires_collecting_session_without_spawning(monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    with Session(engine) as db:
        now = _NOW("America/Detroit").replace(tzinfo=None)
        db.add(
            CookSession(
                household_id=1,
                status="collecting",
                chat_id=1,
                selected_item_ids="[]",
                created_at=now - timedelta(minutes=20),
                expires_at=now - timedelta(minutes=1),
            )
        )
        db.commit()
        cook_id = db.exec(__import__("sqlmodel").select(CookSession)).all()[0].id

    cb = _Cb(f"cookpick:{cook_id}:meal:0")
    spawn, spawned = _recording_spawn()
    asyncio.run(
        handle_cook_callback(
            cb,
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )

    cb.answer.assert_awaited_with("this cook session expired - start a new /cook")
    assert spawned == []
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook is not None
        assert cook.status == "expired"


def test_expired_done_session_blocks_more_without_searching(monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    run_more = AsyncMock(return_value=[])
    monkeypatch.setattr(cook_callbacks, "run_cook_more", run_more)
    engine = _engine_with_user()
    now = _NOW("America/Detroit").replace(tzinfo=None)
    cook_id = _seed_cook(engine, expires_at=now - timedelta(seconds=1))
    cb = _Cb(f"cookmore2:{cook_id}")
    spawn, spawned = _recording_spawn()

    asyncio.run(
        handle_cook_callback(
            cb,
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )

    run_more.assert_not_awaited()
    assert spawned == []
    assert cb.answer.await_args is not None
    assert "expired" in str(cb.answer.await_args).lower()


def test_more_tap_shows_error_and_restores_done_when_search_raises(monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)

    async def failing_run_cook_more(*args, **kwargs):
        raise RuntimeError("source exploded")

    monkeypatch.setattr(cook_callbacks, "run_cook_more", failing_run_cook_more)
    engine = _engine_with_user()
    cook_id = _seed_cook(engine)
    cb = _Cb(f"cookmore2:{cook_id}")
    spawn, spawned = _recording_spawn()

    asyncio.run(
        handle_cook_callback(
            cb,
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )

    assert spawned == []
    cb.message.edit_text.assert_awaited()
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook is not None
        assert cook.status == "done"


def test_double_more_tap_claims_done_session_for_only_one_search(monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    cook_id = _seed_cook(engine)
    entered = asyncio.Event()
    release = asyncio.Event()
    search_calls = 0

    async def fake_run_cook_more(*args, **kwargs):
        nonlocal search_calls
        search_calls += 1
        entered.set()
        await release.wait()
        return []

    monkeypatch.setattr(cook_callbacks, "run_cook_more", fake_run_cook_more)
    first = _Cb(f"cookmore2:{cook_id}")
    second = _Cb(f"cookmore2:{cook_id}")
    spawn, spawned = _recording_spawn()

    async def double_tap():
        first_task = asyncio.create_task(
            handle_cook_callback(
                first,
                session_factory=lambda: Session(engine),
                now_provider=_NOW,
                spawn=spawn,
                bot=None,
                **_fakes(),
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        second_task = asyncio.create_task(
            handle_cook_callback(
                second,
                session_factory=lambda: Session(engine),
                now_provider=_NOW,
                spawn=spawn,
                bot=None,
                **_fakes(),
            )
        )
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first_task, second_task)

    asyncio.run(double_tap())

    assert search_calls == 1
    assert spawned == []
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook is not None
        assert cook.status == "done"


def test_cook_callback_rejects_unauthorized(monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    cb = _Cb("cookpick:1:0", user_id=999)
    spawn, spawned = _recording_spawn()
    asyncio.run(
        handle_cook_callback(
            cb,
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )
    cb.answer.assert_awaited_with("not authorized", show_alert=False)
    assert spawned == []


def test_cook_alt_still_works_on_a_done_session_older_than_the_collect_ttl(monkeypatch):
    # A "done" cook's expires_at is never extended past its original 10-minute
    # collecting-round TTL, so "Show alternatives" must stay available on an
    # old done session (only More/Adjust are gated by that timestamp).
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    cook_id = _seed_cook(
        engine,
        expires_at=_NOW("America/Detroit").replace(tzinfo=None) - timedelta(hours=2),
    )
    cb = _Cb(f"cookalt:{cook_id}")
    spawn, _spawned = _recording_spawn()

    asyncio.run(
        handle_cook_callback(
            cb,
            session_factory=lambda: Session(engine),
            now_provider=_NOW,
            spawn=spawn,
            bot=None,
            **_fakes(),
        )
    )

    cb.answer.assert_awaited_with("showing alternatives")
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook is not None
        assert cook.status == "done"


def test_run_cook_and_render_completes_and_edits(monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    engine = _engine_with_user()
    today_dt = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
    today = today_dt.date()
    with Session(engine) as db:
        for i in range(4):
            db.add(
                PantryItem(
                    household_id=1,
                    raw_name=f"item{i}",
                    normalized_name=f"item{i}",
                    category="produce",
                    qty=1.0,
                    purchased_on=today,
                    shelf_life_days=2,
                    shelf_life_source="llm",
                    ingest_shelf_life_source="llm",
                    expires_on=today + timedelta(days=2),
                    status="active",
                    created_via="receipt",
                    created_at=datetime.now(UTC),
                )
            )
        now = today_dt.replace(tzinfo=None)
        db.add(
            CookSession(
                household_id=1,
                status="ready",
                chat_id=1,
                meal_type="Dinner",
                cuisine="Italian",
                selected_item_ids="[]",
                message_id=99,
                created_at=now,
                expires_at=now + timedelta(minutes=10),
            )
        )
        db.commit()
        cook_id = db.exec(__import__("sqlmodel").select(CookSession)).all()[0].id

    selection = FakeSelectionLLM(canned=(SelectedItems(item_ids=[]), 5))
    recipe = FakeRecipeLLM(
        canned=(
            RecipeCandidates(
                candidates=[
                    RecipeCandidate(
                        title="Safe",
                        cuisine="italian",
                        source_url="u",
                        ingredients=[
                            RecipeIngredient(name="item0"),
                            RecipeIngredient(name="pasta"),
                        ],
                        method_gist="x",
                        deliciousness=0.6,
                    )
                ]
            ),
            9,
        )
    )
    nutrition = FakeNutritionLLM(
        canned=(
            NutritionScores(
                scores=[
                    NutritionScore(
                        health_score=80, effort="easy", est_minutes=20, rationale="ok"
                    )
                ]
            ),
            3,
        )
    )
    bot = type("B", (), {"edit_message_text": AsyncMock()})()

    asyncio.run(
        run_cook_and_render(
            lambda: Session(engine),
            user_id=1,
            household_id=1,
            user_tz="America/Detroit",
            cook_id=cook_id,
            clients=PerUserClients.for_tests(
                selection=selection, recipe=recipe, nutrition=nutrition
            ),
            now_provider=lambda tz: today_dt,
            bot=bot,
        )
    )
    with Session(engine) as db:
        cook = db.get(CookSession, cook_id)
        assert cook is not None
        assert cook.status == "done"
        assert "Safe" in (cook.candidates_json or "")
    bot.edit_message_text.assert_awaited()  # type: ignore[attr-defined]


def test_dispatcher_routes_every_v49_cook_prefix_with_static_sources(monkeypatch):
    cook_callback = AsyncMock()
    generic_callback = AsyncMock()
    monkeypatch.setattr(callback_routes, "handle_cook_callback", cook_callback)
    monkeypatch.setattr(callback_routes, "handle_callback", generic_callback)
    static_source = object()

    dispatcher = bot_mod.build_dispatcher(
        bot=object(),  # type: ignore[arg-type]
        session_factory=lambda: None,  # type: ignore[arg-type,return-value]
        clients=PerUserClients.for_tests(
            image=object(),
            text=object(),
            profile=object(),
            selection=object(),
            recipe=object(),
            nutrition=object(),
        ),
        now_provider=_NOW,
        on_user_created=lambda user: None,
        reschedule=lambda user: None,
        recipe_sources=(static_source,),
    )
    registered = dispatcher.callback_query.handlers[0].callback

    async def route_all():
        for data in (
            "cookmore:1:cuisine_full",
            "cookmore2:1",
            "cookadj:1",
        ):
            await registered(_Cb(data))

    asyncio.run(route_all())

    assert cook_callback.await_count == 3
    generic_callback.assert_not_awaited()
    for call in cook_callback.await_args_list:
        assert call.kwargs["recipe_sources"] == (static_source,)


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
