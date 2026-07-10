import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel, Session, create_engine

from app.models import CookSession, Household, PantryItem, User
from app.cook.models import (
    NutritionScore,
    NutritionScores,
    Purpose,
    RecipeCandidate,
    RecipeCandidates,
    RecipeCriteria,
    RecipeIngredient,
    ScoredCandidate,
    SelectedItems,
    SourcedRecipe,
)
from app.cook.logic import (
    BLEND_WEIGHTS,
    blended_score,
    expiry_utilization,
    shopping_list,
    violates_exclusions,
)
from app.cook.llm import (
    AnthropicNutritionLLM,
    AnthropicRecipeLLM,
    AnthropicSelectionLLM,
    OpenAINutritionLLM,
    OpenAIRecipeLLM,
    OpenAISelectionLLM,
    SCHEMA_REPAIR_INSTRUCTION,
)
from app.cook.service import (
    COOK_COST_CEILING_MICROS,
    MIN_USABLE_ITEMS,
    NotEnoughItems,
    run_cook,
)
from app.cook import service as cook_service
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


class FakeRecipeSource:
    def __init__(self, *responses):
        self.responses = list(responses) or [([], 0)]
        self.calls = []

    def available(self):
        return True

    async def search(self, criteria, *, remaining_cost_micros=None):
        self.calls.append(
            SimpleNamespace(
                criteria=criteria,
                remaining_cost_micros=remaining_cost_micros,
            )
        )
        if len(self.responses) == 1:
            return self.responses[0]
        return self.responses.pop(0)


def _sourced(
    title,
    *,
    ingredients,
    external_id,
    health=50,
    deliciousness=0.5,
):
    return SourcedRecipe(
        recipe=RecipeCandidate(
            title=title,
            cuisine="italian",
            source_url=f"https://recipes.test/{external_id}",
            ingredients=[RecipeIngredient(name=name) for name in ingredients],
            method_gist="Cook it.",
            deliciousness=deliciousness,
        ),
        nutrition=NutritionScore(
            health_score=health,
            effort="easy",
            est_minutes=20,
            rationale="source nutrition",
        ),
        external_id=external_id,
    )


def _item_ids(db):
    return [
        row.id
        for row in db.exec(__import__("sqlmodel").select(PantryItem)).all()
        if row.id is not None
    ]


def test_run_cook_guards_thin_pantry():
    db, today = _db_with_items(MIN_USABLE_ITEMS - 1, 2)
    cook = _cook_row(db)
    with pytest.raises(NotEnoughItems):
        asyncio.run(
            run_cook(
                db,
                cook=cook,
                profile=FoodProfile(),
                selection_llm=FakeSelectionLLM(
                    canned=(SelectedItems(item_ids=[]), 0)
                ),
                source=FakeRecipeSource(),
                today=today,
            )
        )


def test_run_cook_excludes_expired_items():
    db, today = _db_with_items(MIN_USABLE_ITEMS, -1)
    cook = _cook_row(db)
    with pytest.raises(NotEnoughItems):
        asyncio.run(
            run_cook(
                db,
                cook=cook,
                profile=FoodProfile(),
                selection_llm=FakeSelectionLLM(
                    canned=(SelectedItems(item_ids=[]), 0)
                ),
                source=FakeRecipeSource(),
                today=today,
            )
        )


def test_run_cook_builds_exact_criteria_ranks_and_persists_source_results():
    db, today = _db_with_items(4, 2)
    cook = _cook_row(db)
    cook.purpose = Purpose.HEALTHY.value
    db.add(cook)
    db.commit()
    ids = _item_ids(db)
    selection = FakeSelectionLLM(
        canned=(SelectedItems(item_ids=ids[:2]), 5)
    )
    unsafe = _sourced(
        "Peanut Dish",
        ingredients=["peanut"],
        external_id="spoon:unsafe",
        health=100,
        deliciousness=1.0,
    )
    pantry_fit = _sourced(
        "Pantry Hash",
        ingredients=["item0", "item1"],
        external_id="mealdb:20",
        health=20,
        deliciousness=0.2,
    )
    healthy = _sourced(
        "Healthy Pasta",
        ingredients=["item0", "pasta"],
        external_id="spoon:10",
        health=100,
        deliciousness=1.0,
    )
    source = FakeRecipeSource(([unsafe, pantry_fit, healthy], 11))
    profile = FoodProfile(
        diet="vegetarian",
        exclusions=["peanut"],
        max_cook_minutes=25,
    )

    result = asyncio.run(
        run_cook(
            db,
            cook=cook,
            profile=profile,
            selection_llm=selection,
            source=source,
            today=today,
        )
    )

    assert json.loads(selection.calls[0])["purpose"] == Purpose.HEALTHY.value
    assert source.calls[0].criteria == RecipeCriteria(
        include_ingredients=["item0", "item1"],
        purpose=Purpose.HEALTHY,
        meal_type="dinner",
        cuisine="italian",
        diet="vegetarian",
        intolerances=["peanut"],
        exclude_ingredients=["peanut"],
        max_ready_minutes=25,
        number=6,
        offset=0,
    )
    assert source.calls[0].remaining_cost_micros == COOK_COST_CEILING_MICROS - 5
    assert [candidate.recipe.title for candidate in result] == [
        "Healthy Pasta",
        "Pantry Hash",
    ]
    assert result[0].final_score == blended_score(
        health_0_1=1.0,
        expiry_use=0.5,
        deliciousness=1.0,
        affinity_0_1=0.5,  # no feedback signals seeded -> neutral
    )
    assert result[0].external_id == "spoon:10"
    assert result[0].shopping_list == ["pasta"]
    db.refresh(cook)
    assert cook.llm_cost_micros_usd == 16
    assert cook.chosen_index == 0
    stored = json.loads(cook.candidates_json or "[]")
    assert [card["external_id"] for card in stored] == [
        "spoon:10",
        "mealdb:20",
    ]


def test_run_cook_reuses_stored_selected_items_after_adjust():
    db, today = _db_with_items(4, 2)
    cook = _cook_row(db)
    ids = _item_ids(db)
    cook.selected_item_ids = json.dumps(ids[:2])
    cook.purpose = Purpose.COMFORT.value
    cook.llm_cost_micros_usd = 7
    db.add(cook)
    db.commit()
    selection = FakeSelectionLLM(canned=(SelectedItems(item_ids=ids[2:]), 99))
    source = FakeRecipeSource(
        ([_sourced("Adjusted", ingredients=["item0"], external_id="A")], 3)
    )

    result = asyncio.run(
        run_cook(
            db,
            cook=cook,
            profile=FoodProfile(),
            selection_llm=selection,
            source=source,
            today=today,
        )
    )

    assert result
    assert selection.calls == []
    assert source.calls[0].criteria.include_ingredients == ["item0", "item1"]
    assert source.calls[0].criteria.purpose == Purpose.COMFORT
    assert source.calls[0].remaining_cost_micros == COOK_COST_CEILING_MICROS - 7
    db.refresh(cook)
    assert json.loads(cook.selected_item_ids) == ids[:2]
    assert cook.llm_cost_micros_usd == 10


def test_run_cook_halts_before_source_when_selection_exceeds_cost_ceiling():
    db, today = _db_with_items(4, 2)
    cook = _cook_row(db)
    ids = _item_ids(db)
    source = FakeRecipeSource()

    result = asyncio.run(
        run_cook(
            db,
            cook=cook,
            profile=FoodProfile(),
            selection_llm=FakeSelectionLLM(
                canned=(
                    SelectedItems(item_ids=ids),
                    COOK_COST_CEILING_MICROS + 1,
                )
            ),
            source=source,
            today=today,
        )
    )

    assert result == []
    assert source.calls == []


def test_run_cook_empty_selection_uses_all_active_items():
    db, today = _db_with_items(4, 2)
    cook = _cook_row(db)
    ids = _item_ids(db)
    source = FakeRecipeSource()

    result = asyncio.run(
        run_cook(
            db,
            cook=cook,
            profile=FoodProfile(),
            selection_llm=FakeSelectionLLM(
                canned=(SelectedItems(item_ids=[]), 5)
            ),
            source=source,
            today=today,
        )
    )

    assert result == []
    assert source.calls[0].criteria.include_ingredients == [
        "item0",
        "item1",
        "item2",
        "item3",
    ]
    db.refresh(cook)
    assert json.loads(cook.selected_item_ids) == ids


def _prepare_more_cook(db, *, selected_ids, old_candidate, cost=10):
    cook = _cook_row(db)
    cook.status = "done"
    cook.purpose = Purpose.USE_IT_UP.value
    cook.selected_item_ids = json.dumps(selected_ids)
    cook.candidates_json = json.dumps([old_candidate.model_dump()])
    cook.chosen_index = 0
    cook.llm_cost_micros_usd = cost
    db.add(cook)
    db.commit()
    return cook


def test_run_cook_more_paginates_dedups_and_retains_old_cards():
    db, today = _db_with_items(4, 2)
    ids = _item_ids(db)
    old = ScoredCandidate(
        recipe=_sourced(
            "Old Card", ingredients=["item0"], external_id="A"
        ).recipe,
        nutrition=NutritionScore(
            health_score=60,
            effort="easy",
            est_minutes=20,
            rationale="old",
        ),
        expiry_use=0.5,
        final_score=0.5,
        external_id="A",
        shopping_list=["old missing"],
    )
    cook = _prepare_more_cook(db, selected_ids=ids[:2], old_candidate=old)
    duplicate = _sourced("Duplicate A", ingredients=["item0"], external_id="A")
    fresh = _sourced(
        "Fresh B", ingredients=["item0", "pasta"], external_id="B", health=90
    )
    source = FakeRecipeSource(([duplicate, fresh], 13))

    result = asyncio.run(
        cook_service.run_cook_more(
            db,
            cook=cook,
            profile=FoodProfile(exclusions=["peanut"]),
            source=source,
            today=today,
        )
    )

    assert [candidate.external_id for candidate in result] == ["B"]
    call = source.calls[0]
    assert call.criteria.offset == 6
    assert call.criteria.number == 6
    assert call.criteria.include_ingredients == ["item0", "item1"]
    assert call.criteria.purpose == Purpose.USE_IT_UP
    assert call.criteria.exclude_ingredients == ["peanut"]
    assert call.remaining_cost_micros == COOK_COST_CEILING_MICROS - 10
    db.refresh(cook)
    assert cook.search_offset == 6
    assert cook.llm_cost_micros_usd == 23
    stored = json.loads(cook.candidates_json or "[]")
    assert [card["external_id"] for card in stored] == ["B", "A"]
    assert stored[1]["recipe"]["title"] == "Old Card"
    assert stored[1]["shopping_list"] == ["old missing"]
    assert stored[0]["shopping_list"] == ["pasta"]


def test_run_cook_more_does_not_search_when_cost_is_already_over_ceiling():
    db, today = _db_with_items(4, 2)
    ids = _item_ids(db)
    old = ScoredCandidate(
        recipe=_sourced("Old", ingredients=["item0"], external_id="A").recipe,
        nutrition=NutritionScore(
            health_score=50,
            effort="easy",
            est_minutes=20,
            rationale="old",
        ),
        expiry_use=1.0,
        final_score=0.5,
        external_id="A",
    )
    cook = _prepare_more_cook(
        db,
        selected_ids=ids[:2],
        old_candidate=old,
        cost=COOK_COST_CEILING_MICROS + 1,
    )
    source = FakeRecipeSource()
    original_cards = cook.candidates_json

    result = asyncio.run(
        cook_service.run_cook_more(
            db,
            cook=cook,
            profile=FoodProfile(),
            source=source,
            today=today,
        )
    )

    assert result == []
    assert source.calls == []
    db.refresh(cook)
    assert cook.search_offset == 0
    assert cook.candidates_json == original_cards


def test_run_cook_more_keeps_old_cards_when_source_pushes_cost_over_ceiling():
    db, today = _db_with_items(4, 2)
    ids = _item_ids(db)
    old = ScoredCandidate(
        recipe=_sourced("Old", ingredients=["item0"], external_id="A").recipe,
        nutrition=NutritionScore(
            health_score=50,
            effort="easy",
            est_minutes=20,
            rationale="old",
        ),
        expiry_use=1.0,
        final_score=0.5,
        external_id="A",
    )
    cook = _prepare_more_cook(
        db,
        selected_ids=ids[:2],
        old_candidate=old,
        cost=COOK_COST_CEILING_MICROS - 2,
    )
    original_cards = cook.candidates_json
    source = FakeRecipeSource(
        ([_sourced("Fresh", ingredients=["item0"], external_id="B")], 3)
    )

    result = asyncio.run(
        cook_service.run_cook_more(
            db,
            cook=cook,
            profile=FoodProfile(),
            source=source,
            today=today,
        )
    )

    assert result == []
    assert source.calls[0].remaining_cost_micros == 2
    db.refresh(cook)
    assert cook.llm_cost_micros_usd == COOK_COST_CEILING_MICROS + 1
    assert cook.candidates_json == original_cards


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
    score = blended_score(
        health_0_1=1.0, expiry_use=0.0, deliciousness=0.0, affinity_0_1=0.0
    )
    assert abs(score - BLEND_WEIGHTS["health"]) < 1e-9


def test_shopping_list_excludes_pantry_items():
    missing = shopping_list(
        recipe_names=["tomato", "pasta", "basil"],
        pantry_normalized=["tomato", "basil"],
    )
    assert missing == ["pasta"]
