import asyncio
from types import SimpleNamespace

from app.cook_models import (
    NutritionScore,
    NutritionScores,
    RecipeCandidate,
    RecipeCandidates,
    RecipeIngredient,
    ScoredCandidate,
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
from tests.fakes import FakeNutritionLLM, FakeRecipeLLM, FakeSelectionLLM


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
