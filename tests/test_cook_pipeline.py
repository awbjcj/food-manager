import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel, Session, create_engine

from app.models import CookSession, Household, PantryItem, User
from app.cook_models import (
    NutritionScore,
    NutritionScores,
    RecipeCandidate,
    RecipeCandidates,
    RecipeIngredient,
    SelectedItems,
)
from app.cook_logic import (
    BLEND_WEIGHTS,
    blended_score,
    expiry_utilization,
    shopping_list,
    violates_exclusions,
)
from app.cook_llm import (
    AnthropicNutritionLLM,
    AnthropicRecipeLLM,
    AnthropicSelectionLLM,
    OpenAINutritionLLM,
    OpenAIRecipeLLM,
    OpenAISelectionLLM,
    SCHEMA_REPAIR_INSTRUCTION,
)
from app.cook_service import (
    COOK_COST_CEILING_MICROS,
    MIN_USABLE_ITEMS,
    NotEnoughItems,
    run_cook,
)
from app.profile_service import FoodProfile
from tests.fakes import FakeNutritionLLM, FakeRecipeLLM, FakeSelectionLLM


def _db_with_items(n, expiry_days):
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    db = Session(engine)
    household = Household(created_at=datetime.now(timezone.utc))
    db.add(household)
    db.commit()
    db.refresh(household)
    assert household.id is not None
    db.add(User(telegram_id=1, chat_id=1, household_id=household.id,
                created_at=datetime.now(timezone.utc)))
    today = date(2026, 5, 30)
    for i in range(n):
        db.add(PantryItem(
            household_id=1, raw_name=f"item{i}", normalized_name=f"item{i}",
            category="produce", qty=1.0, purchased_on=today,
            shelf_life_days=expiry_days, shelf_life_source="llm",
            ingest_shelf_life_source="llm",
            expires_on=today + timedelta(days=expiry_days),
            status="active", created_via="receipt", created_at=datetime.now(timezone.utc),
        ))
    db.commit()
    return db, today


def _cook_row(db):
    now = datetime(2026, 5, 30, 12, 0).replace(tzinfo=None)
    row = CookSession(household_id=1, status="ready", chat_id=1, meal_type="dinner",
                      cuisine="italian", selected_item_ids="[]",
                      created_at=now, expires_at=now + timedelta(minutes=10))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_run_cook_guards_thin_pantry():
    db, today = _db_with_items(MIN_USABLE_ITEMS - 1, 2)
    cook = _cook_row(db)
    with pytest.raises(NotEnoughItems):
        asyncio.run(run_cook(
            db, cook=cook, profile=FoodProfile(),
            selection_llm=FakeSelectionLLM(canned=(SelectedItems(item_ids=[]), 0)),
            recipe_llm=FakeRecipeLLM(canned=(RecipeCandidates(candidates=[]), 0)),
            nutrition_llm=FakeNutritionLLM(canned=(NutritionScores(scores=[]), 0)),
            today=today,
        ))


def test_run_cook_excludes_expired_items():
    # MIN_USABLE_ITEMS active rows that are all past their expiry date
    db, today = _db_with_items(MIN_USABLE_ITEMS, -1)
    cook = _cook_row(db)
    with pytest.raises(NotEnoughItems):
        asyncio.run(run_cook(
            db, cook=cook, profile=FoodProfile(),
            selection_llm=FakeSelectionLLM(canned=(SelectedItems(item_ids=[]), 0)),
            recipe_llm=FakeRecipeLLM(canned=(RecipeCandidates(candidates=[]), 0)),
            nutrition_llm=FakeNutritionLLM(canned=(NutritionScores(scores=[]), 0)),
            today=today,
        ))


def test_run_cook_ranks_and_filters_allergens():
    import asyncio
    db, today = _db_with_items(4, 2)
    cook = _cook_row(db)
    ids = [r.id for r in db.exec(__import__("sqlmodel").select(PantryItem)).all()]
    candidates = RecipeCandidates(candidates=[
        RecipeCandidate(title="Peanut Dish", cuisine="thai", source_url="u",
                        ingredients=[RecipeIngredient(name="peanut")],
                        method_gist="x", deliciousness=0.9),
        RecipeCandidate(title="Safe Dish", cuisine="italian", source_url="u",
                        ingredients=[RecipeIngredient(name="item0"), RecipeIngredient(name="pasta")],
                        method_gist="y", deliciousness=0.5),
    ])
    scores = NutritionScores(scores=[
        NutritionScore(health_score=90, effort="easy", est_minutes=20, rationale="a"),
    ])
    nutrition = FakeNutritionLLM(canned=(scores, 3))
    result = asyncio.run(run_cook(
        db, cook=cook, profile=FoodProfile(exclusions=["peanut"]),
        selection_llm=FakeSelectionLLM(canned=(SelectedItems(item_ids=ids), 5)),
        recipe_llm=FakeRecipeLLM(canned=(candidates, 9)),
        nutrition_llm=nutrition,
        today=today,
    ))
    assert [c.recipe.title for c in result] == ["Safe Dish"]  # peanut dish filtered out
    nutrition_prompt = json.loads(nutrition.calls[0])
    assert [c["title"] for c in nutrition_prompt["candidates"]] == ["Safe Dish"]
    assert "pasta" in result[0].shopping_list
    db.refresh(cook)
    assert cook.llm_cost_micros_usd == 17  # 5 + 9 + 3


def test_run_cook_halts_on_cost_ceiling():
    import asyncio
    db, today = _db_with_items(4, 2)
    cook = _cook_row(db)
    ids = [r.id for r in db.exec(__import__("sqlmodel").select(PantryItem)).all()]
    recipe = FakeRecipeLLM(canned=(RecipeCandidates(candidates=[]), 0))
    result = asyncio.run(run_cook(
        db, cook=cook, profile=FoodProfile(),
        selection_llm=FakeSelectionLLM(
            canned=(SelectedItems(item_ids=ids), COOK_COST_CEILING_MICROS + 1)),
        recipe_llm=recipe,
        nutrition_llm=FakeNutritionLLM(canned=(NutritionScores(scores=[]), 0)),
        today=today,
    ))
    assert result == []
    assert recipe.calls == []  # recipe stage never reached after ceiling hit


def test_run_cook_falls_back_to_active_items_for_empty_selection():
    import asyncio
    db, today = _db_with_items(4, 2)
    cook = _cook_row(db)
    active = db.exec(__import__("sqlmodel").select(PantryItem)).all()
    recipe = FakeRecipeLLM(canned=(RecipeCandidates(candidates=[]), 9))
    result = asyncio.run(run_cook(
        db, cook=cook, profile=FoodProfile(),
        selection_llm=FakeSelectionLLM(canned=(SelectedItems(item_ids=[]), 5)),
        recipe_llm=recipe,
        nutrition_llm=FakeNutritionLLM(canned=(NutritionScores(scores=[]), 0)),
        today=today,
    ))
    assert result == []
    assert len(recipe.calls) == 1
    recipe_prompt = json.loads(recipe.calls[0])
    assert "items" not in recipe_prompt
    assert [ingredient["id"] for ingredient in recipe_prompt["ingredients"]] == [
        item.id for item in active
    ]
    assert [ingredient["name"] for ingredient in recipe_prompt["ingredients"]] == [
        item.normalized_name for item in active
    ]
    db.refresh(cook)
    assert json.loads(cook.selected_item_ids) == [item.id for item in active]


def test_run_cook_halts_before_regeneration_when_recipe_cost_exceeds_ceiling():
    import asyncio
    db, today = _db_with_items(4, 2)
    cook = _cook_row(db)
    ids = [r.id for r in db.exec(__import__("sqlmodel").select(PantryItem)).all()]
    peanut_only = RecipeCandidates(candidates=[
        RecipeCandidate(title="Peanut", cuisine="thai", source_url="u",
                        ingredients=[RecipeIngredient(name="peanut")],
                        method_gist="x", deliciousness=0.9)])
    recipe = FakeRecipeLLM(canned=(peanut_only, COOK_COST_CEILING_MICROS))
    nutrition = FakeNutritionLLM(canned=(NutritionScores(scores=[]), 3))
    result = asyncio.run(run_cook(
        db, cook=cook, profile=FoodProfile(exclusions=["peanut"]),
        selection_llm=FakeSelectionLLM(canned=(SelectedItems(item_ids=ids), 1)),
        recipe_llm=recipe, nutrition_llm=nutrition, today=today,
    ))
    assert result == []
    assert len(recipe.calls) == 1
    assert nutrition.calls == []


def test_run_cook_expiry_utilization_uses_selected_urgent_items_only():
    import asyncio
    db, today = _db_with_items(4, 2)
    cook = _cook_row(db)
    ids = [r.id for r in db.exec(__import__("sqlmodel").select(PantryItem)).all()]
    candidates = RecipeCandidates(candidates=[
        RecipeCandidate(title="Selected Urgent Dish", cuisine="italian", source_url="u",
                        ingredients=[RecipeIngredient(name="item0")],
                        method_gist="x", deliciousness=0.5),
    ])
    scores = NutritionScores(scores=[
        NutritionScore(health_score=80, effort="easy", est_minutes=20, rationale="a"),
    ])
    result = asyncio.run(run_cook(
        db, cook=cook, profile=FoodProfile(),
        selection_llm=FakeSelectionLLM(canned=(SelectedItems(item_ids=[ids[0]]), 5)),
        recipe_llm=FakeRecipeLLM(canned=(candidates, 9)),
        nutrition_llm=FakeNutritionLLM(canned=(scores, 3)),
        today=today,
    ))
    assert result[0].expiry_use == 1.0


def test_run_cook_regenerates_once_then_refuses_on_allergen_wipeout():
    import asyncio
    db, today = _db_with_items(4, 2)
    cook = _cook_row(db)
    ids = [r.id for r in db.exec(__import__("sqlmodel").select(PantryItem)).all()]
    peanut_only = RecipeCandidates(candidates=[
        RecipeCandidate(title="Peanut", cuisine="thai", source_url="u",
                        ingredients=[RecipeIngredient(name="peanut")],
                        method_gist="x", deliciousness=0.9)])
    recipe = FakeRecipeLLM(canned_sequence=[(peanut_only, 9), (peanut_only, 9)])
    nutrition = FakeNutritionLLM(canned=(NutritionScores(scores=[]), 3))
    result = asyncio.run(run_cook(
        db, cook=cook, profile=FoodProfile(exclusions=["peanut"]),
        selection_llm=FakeSelectionLLM(canned=(SelectedItems(item_ids=ids), 5)),
        recipe_llm=recipe, nutrition_llm=nutrition, today=today,
    ))
    assert result == []
    assert len(recipe.calls) == 2  # regenerated exactly once
    regenerate_prompt = json.loads(recipe.calls[1])
    assert "items" not in regenerate_prompt
    assert regenerate_prompt["ingredients"]
    assert regenerate_prompt["must_avoid"] == ["peanut"]
    # the re-prompt must name the specific ingredients that triggered the filter (spec §8)
    assert regenerate_prompt["violated_ingredients"] == ["peanut"]
    assert nutrition.calls == []   # never reached the nutrition stage


class _FakeOpenAIResponses:
    def __init__(self):
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        text_format = kwargs["text_format"]
        if text_format is SelectedItems:
            return SimpleNamespace(output_parsed=SelectedItems(item_ids=[]))
        if text_format is RecipeCandidates:
            return SimpleNamespace(output_parsed=RecipeCandidates(candidates=[]))
        if text_format is NutritionScores:
            return SimpleNamespace(output_parsed=NutritionScores(scores=[]))
        raise AssertionError(f"unexpected text_format: {text_format!r}")


class _FakeOpenAISDK:
    def __init__(self):
        self.responses = _FakeOpenAIResponses()


class _FakeAnthropicMessages:
    def __init__(self, texts):
        self.calls = []
        self._texts = list(texts)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self._texts.pop(0)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=2, output_tokens=3),
        )


class _FakeAnthropicSDK:
    def __init__(self, texts):
        self.messages = _FakeAnthropicMessages(texts)


def test_fakes_return_canned():
    sel = FakeSelectionLLM(canned=(SelectedItems(item_ids=[1, 2]), 5))
    rec = FakeRecipeLLM(canned=(RecipeCandidates(candidates=[]), 9))
    nut = FakeNutritionLLM(canned=(NutritionScores(scores=[]), 3))

    selected, selection_cost = asyncio.run(sel.select_items(prompt="x"))
    recipes, recipe_cost = asyncio.run(rec.fetch_recipes(prompt="x"))
    nutrition, nutrition_cost = asyncio.run(nut.score(prompt="x"))

    assert selected.item_ids == [1, 2]
    assert selection_cost == 5
    assert recipes.candidates == []
    assert recipe_cost == 9
    assert nutrition.scores == []
    assert nutrition_cost == 3


def test_openai_recipe_caps_web_search_tool_calls():
    sdk = _FakeOpenAISDK()

    asyncio.run(OpenAISelectionLLM(sdk, "gpt-test").select_items(prompt="x"))
    asyncio.run(OpenAIRecipeLLM(sdk, "gpt-test").fetch_recipes(prompt="x"))
    asyncio.run(OpenAINutritionLLM(sdk, "gpt-test").score(prompt="x"))

    selection_call, recipe_call, nutrition_call = sdk.responses.calls
    assert "max_tool_calls" not in selection_call
    assert recipe_call["max_tool_calls"] == 3
    assert "max_tool_calls" not in nutrition_call


def test_anthropic_recipe_uses_capped_web_search_tool_only_for_recipes():
    sdk = _FakeAnthropicSDK([
        '{"item_ids":[]}',
        '{"candidates":[]}',
        '{"scores":[]}',
    ])

    asyncio.run(AnthropicSelectionLLM(sdk, "claude-test").select_items(prompt="x"))
    asyncio.run(AnthropicRecipeLLM(sdk, "claude-test").fetch_recipes(prompt="x"))
    asyncio.run(AnthropicNutritionLLM(sdk, "claude-test").score(prompt="x"))

    selection_call, recipe_call, nutrition_call = sdk.messages.calls
    assert selection_call["tools"] == []
    assert recipe_call["tools"] == [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
    ]
    assert nutrition_call["tools"] == []


def test_anthropic_schema_repair_retry_accumulates_known_cost():
    sdk = _FakeAnthropicSDK([
        "not-json",
        '{"item_ids":[4],"rationale":"works"}',
    ])

    selected, cost = asyncio.run(
        AnthropicSelectionLLM(sdk, "claude-sonnet-4-6").select_items(prompt="x")
    )

    assert selected.item_ids == [4]
    assert cost == 102
    assert len(sdk.messages.calls) == 2
    retry_content = sdk.messages.calls[1]["messages"][0]["content"]
    assert retry_content[-1]["text"] == SCHEMA_REPAIR_INSTRUCTION


def test_recipe_candidate_validates():
    c = RecipeCandidate(
        title="Tomato Pasta",
        cuisine="italian",
        source_url="https://x/y",
        ingredients=[
            RecipeIngredient(name="tomato", qty=2, unit="ct"),
            RecipeIngredient(name="pasta"),
        ],
        method_gist="Boil pasta, make sauce.",
        deliciousness=0.8,
    )
    assert c.ingredients[1].qty is None
    assert 0.0 <= c.deliciousness <= 1.0


def test_violates_exclusions_matches_normalized_substring():
    assert violates_exclusions(["peanut butter", "jam"], exclusions=["peanut"])
    assert not violates_exclusions(["almond butter"], exclusions=["peanut"])


def test_expiry_utilization_fraction_of_urgent_items_used():
    # urgent item names: tomato (1d), spinach (2d); recipe uses tomato only
    used = expiry_utilization(
        recipe_names=["tomato", "pasta"],
        urgent_names=["tomato", "spinach"],
    )
    assert used == 0.5


def test_blended_score_weights():
    score = blended_score(health_0_1=1.0, expiry_use=0.0, deliciousness=0.0)
    assert abs(score - BLEND_WEIGHTS["health"]) < 1e-9


def test_shopping_list_excludes_pantry_items():
    missing = shopping_list(
        recipe_names=["tomato", "pasta", "basil"],
        pantry_normalized=["tomato", "basil"],
    )
    assert missing == ["pasta"]
