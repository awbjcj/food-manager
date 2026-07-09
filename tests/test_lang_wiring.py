"""Tests that user.lang is threaded into the display handlers."""
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

import app.bot as bot_mod
from app.bot import (
    handle_favorites,
    handle_list,
    handle_photo,
    handle_shopping,
    run_cook_and_render,
)
from app.cook.models import (
    NutritionScore,
    NutritionScores,
    RecipeCandidate,
    RecipeCandidates,
    RecipeIngredient,
    SelectedItems,
)
from app.cook.favorites_service import save_candidate
from app.llm import LLMResult, ParsedItem, ParseResult
from app.models import CookSession, Household, PantryItem, User
from app.shopping_service import add_missing
from tests.fakes import (
    FakeLLMClient,
    FakeNutritionLLM,
    FakeRecipeLLM,
    FakeSelectionLLM,
    FakeTranslationLLM,
)


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


@pytest.mark.asyncio
async def test_favorites_keyboard_button_translated_for_zh_user(
    session_factory, monkeypatch
):
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
    markup = msg.answer.call_args.kwargs["reply_markup"]
    button_text = markup.inline_keyboard[0][0].text
    assert button_text == "再做一次"


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


# ---------------------------------------------------------------------------
# handle_photo (ingest reply)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_reply_renders_zh_name_for_zh_user(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    _set_user_lang(session_factory, "zh")

    llm = FakeLLMClient(
        canned=LLMResult(
            parse=ParseResult(
                items=[
                    ParsedItem(
                        is_food=True,
                        name="Kefir",
                        category="dairy",
                        est_shelf_life_days=7,
                        confidence=0.9,
                        track_worthy=True,
                    )
                ]
            ),
            cost_micros_usd=100,
        )
    )

    sent_msg = MagicMock()
    sent_msg.message_id = 99
    msg = _msg("")
    photo_obj = MagicMock()
    photo_obj.file_id = "fake_file_id"
    msg.photo = [photo_obj]
    msg.answer = AsyncMock(return_value=sent_msg)

    fake = FakeTranslationLLM(table={"Kefir": "开菲尔"})
    await handle_photo(
        msg,
        session_factory=session_factory,
        now_provider=_now,
        llm=llm,
        photo_downloader=AsyncMock(return_value=b"jpg"),
        translation_llm=fake,
    )
    text = msg.answer.call_args.args[0]
    assert "开菲尔" in text


@pytest.mark.asyncio
async def test_ingest_reply_english_user_unaffected(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    _set_user_lang(session_factory, "en")

    llm = FakeLLMClient(
        canned=LLMResult(
            parse=ParseResult(
                items=[
                    ParsedItem(
                        is_food=True,
                        name="Kefir",
                        category="dairy",
                        est_shelf_life_days=7,
                        confidence=0.9,
                        track_worthy=True,
                    )
                ]
            ),
            cost_micros_usd=100,
        )
    )

    sent_msg = MagicMock()
    sent_msg.message_id = 99
    msg = _msg("")
    photo_obj = MagicMock()
    photo_obj.file_id = "fake_file_id"
    msg.photo = [photo_obj]
    msg.answer = AsyncMock(return_value=sent_msg)

    await handle_photo(
        msg,
        session_factory=session_factory,
        now_provider=_now,
        llm=llm,
        photo_downloader=AsyncMock(return_value=b"jpg"),
    )
    text = msg.answer.call_args.args[0]
    assert "Kefir" in text


# ---------------------------------------------------------------------------
# run_cook_and_render (cook result)
# ---------------------------------------------------------------------------

def _seed_ready_cook(session_factory, household_id: int) -> int:
    today = date(2026, 6, 1)
    with session_factory() as db:
        for i in range(4):
            db.add(
                PantryItem(
                    household_id=household_id,
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
                    created_at=datetime.now(timezone.utc),
                )
            )
        now = datetime(2026, 6, 1, 12, 0)
        cook = CookSession(
            household_id=household_id,
            status="ready",
            chat_id=99,
            meal_type="Dinner",
            cuisine="Italian",
            selected_item_ids="[]",
            message_id=99,
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        db.add(cook)
        db.commit()
        db.refresh(cook)
        assert cook.id is not None
        return cook.id


def _cook_llms():
    selection = FakeSelectionLLM(canned=(SelectedItems(item_ids=[]), 5))
    recipe = FakeRecipeLLM(
        canned=(
            RecipeCandidates(
                candidates=[
                    RecipeCandidate(
                        title="Pasta",
                        cuisine="italian",
                        source_url="u",
                        ingredients=[RecipeIngredient(name="item0")],
                        method_gist="boil",
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
    return selection, recipe, nutrition


@pytest.mark.asyncio
async def test_cook_result_renders_zh_title_for_zh_user(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    household_id = _set_user_lang(session_factory, "zh")
    cook_id = _seed_ready_cook(session_factory, household_id)

    selection, recipe, nutrition = _cook_llms()
    edits: list = []
    bot = MagicMock()
    bot.edit_message_text = AsyncMock(
        side_effect=lambda **kw: edits.append(kw["text"])
    )
    fake = FakeTranslationLLM(table={"Pasta": "意面"})

    await run_cook_and_render(
        session_factory,
        user_id=1,
        household_id=household_id,
        user_tz="America/Detroit",
        cook_id=cook_id,
        selection_llm=selection,
        recipe_llm=recipe,
        nutrition_llm=nutrition,
        now_provider=_now,
        bot=bot,
        translation_llm=fake,
    )
    assert edits
    assert "意面" in edits[-1]


@pytest.mark.asyncio
async def test_cook_result_english_user_unaffected(session_factory, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    household_id = _set_user_lang(session_factory, "en")
    cook_id = _seed_ready_cook(session_factory, household_id)

    selection, recipe, nutrition = _cook_llms()
    edits: list = []
    bot = MagicMock()
    bot.edit_message_text = AsyncMock(
        side_effect=lambda **kw: edits.append(kw["text"])
    )

    await run_cook_and_render(
        session_factory,
        user_id=1,
        household_id=household_id,
        user_tz="America/Detroit",
        cook_id=cook_id,
        selection_llm=selection,
        recipe_llm=recipe,
        nutrition_llm=nutrition,
        now_provider=_now,
        bot=bot,
    )
    assert edits
    assert "Pasta" in edits[-1]


@pytest.mark.asyncio
async def test_photo_sends_progress_ack_then_edits_result(session_factory, monkeypatch):
    from app.i18n import t

    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    _set_user_lang(session_factory, "en")

    llm = FakeLLMClient(
        canned=LLMResult(
            parse=ParseResult(
                items=[
                    ParsedItem(
                        is_food=True,
                        name="Kefir",
                        category="dairy",
                        est_shelf_life_days=7,
                        confidence=0.9,
                        track_worthy=True,
                    )
                ]
            ),
            cost_micros_usd=100,
        )
    )

    sent_msg = MagicMock()
    sent_msg.message_id = 99
    sent_msg.edit_text = AsyncMock()
    msg = _msg("")
    photo_obj = MagicMock()
    photo_obj.file_id = "fake_file_id"
    msg.photo = [photo_obj]
    msg.answer = AsyncMock(return_value=sent_msg)

    await handle_photo(
        msg,
        session_factory=session_factory,
        now_provider=_now,
        llm=llm,
        photo_downloader=AsyncMock(return_value=b"jpg"),
    )

    # The ack goes out first, as its own message...
    assert msg.answer.await_args_list[0].args[0] == t("progress.reading_receipt", "en")
    # ...and is edited into the ingest reply.
    sent_msg.edit_text.assert_awaited_once()
    assert "Kefir" in sent_msg.edit_text.await_args.args[0]
