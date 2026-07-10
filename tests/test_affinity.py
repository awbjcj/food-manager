import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.cook.affinity import affinity, list_recent_signals
from app.cook.feedback import FeedbackSignal
from app.cook.models import NutritionScore, RecipeCandidate, RecipeIngredient, ScoredCandidate
from app.models import CookSession, Household, User


def _sig(verdict: str, cuisine: str = "thai", ingredients=("tofu", "lime")):
    return FeedbackSignal(cuisine=cuisine, ingredients=list(ingredients), verdict=verdict)


def test_affinity_empty_signals_is_neutral_half():
    assert affinity(cuisine="thai", ingredient_names=["tofu"], signals=[]) == 0.5


def test_liked_similar_beats_neutral_beats_disliked_similar():
    liked = [_sig("liked")]
    disliked = [_sig("disliked")]
    similar = affinity(cuisine="thai", ingredient_names=["tofu", "lime"], signals=liked)
    unrelated = affinity(cuisine="german", ingredient_names=["pork", "cabbage"], signals=liked)
    disliked_similar = affinity(
        cuisine="thai", ingredient_names=["tofu", "lime"], signals=disliked
    )
    assert similar > unrelated
    assert unrelated > disliked_similar


def test_dislike_is_soft_never_zero():
    score = affinity(
        cuisine="thai", ingredient_names=["tofu", "lime"], signals=[_sig("disliked")]
    )
    assert 0.0 < score < 0.5


def test_affinity_is_deterministic():
    cuisine = "thai"
    ingredient_names = ["tofu", "basil"]
    signals = [_sig("liked"), _sig("disliked", cuisine="french", ingredients=("cream",))]
    assert affinity(
        cuisine=cuisine, ingredient_names=ingredient_names, signals=signals
    ) == affinity(cuisine=cuisine, ingredient_names=ingredient_names, signals=signals)


def _scored(title="Pasta", cuisine="italian", ingredients=("pasta", "tomato")):
    rec = RecipeCandidate(
        title=title, cuisine=cuisine, source_url="https://x",
        ingredients=[RecipeIngredient(name=n) for n in ingredients],
        method_gist="boil", deliciousness=0.7,
    )
    nut = NutritionScore(health_score=80, effort="easy", est_minutes=20, rationale="ok")
    return ScoredCandidate(recipe=rec, nutrition=nut, expiry_use=0.5, final_score=0.7)


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
                chat_id=1,
                household_id=household.id,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    return make


def _seed_cook(session_factory, *, feedback, feedback_at, title, cuisine, ingredients):
    with session_factory() as db:
        now = feedback_at
        candidates = [_scored(title=title, cuisine=cuisine, ingredients=ingredients)]
        db.add(CookSession(
            household_id=1, status="done", chat_id=1, selected_item_ids="[]",
            candidates_json=json.dumps([c.model_dump() for c in candidates]),
            chosen_index=0, created_at=now, expires_at=now,
            feedback=feedback, feedback_at=feedback_at,
        ))
        db.commit()


def test_list_recent_signals_windows_and_orders(session_factory):
    t0 = datetime(2026, 5, 30, 10, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=1)
    t2 = t0 + timedelta(hours=2)
    _seed_cook(session_factory, feedback="liked", feedback_at=t0, title="Old", cuisine="italian", ingredients=("a",))
    _seed_cook(session_factory, feedback="liked", feedback_at=t1, title="Mid", cuisine="thai", ingredients=("b",))
    _seed_cook(session_factory, feedback="disliked", feedback_at=t2, title="New", cuisine="french", ingredients=("c",))
    with session_factory() as session:
        signals = list_recent_signals(session, household_id=1, limit=2)
    assert len(signals) == 2
    assert {s.cuisine for s in signals} == {"thai", "french"}


def test_blend_weights_sum_to_one_and_include_affinity():
    from app.cook.logic import BLEND_WEIGHTS

    assert set(BLEND_WEIGHTS) == {"health", "expiry", "deliciousness", "affinity"}
    assert abs(sum(BLEND_WEIGHTS.values()) - 1.0) < 1e-9


def test_blended_score_uses_affinity_term():
    from app.cook.logic import blended_score

    base = dict(health_0_1=0.5, expiry_use=0.5, deliciousness=0.5)
    assert blended_score(**base, affinity_0_1=1.0) > blended_score(
        **base, affinity_0_1=0.0
    )


def test_neutral_affinity_preserves_ranking_order():
    from app.cook.logic import blended_score

    a = blended_score(health_0_1=0.9, expiry_use=0.2, deliciousness=0.5, affinity_0_1=0.5)
    b = blended_score(health_0_1=0.2, expiry_use=0.9, deliciousness=0.5, affinity_0_1=0.5)
    c = blended_score(health_0_1=0.1, expiry_use=0.1, deliciousness=0.1, affinity_0_1=0.5)
    assert sorted([a, b, c], reverse=True)[-1] == c  # order driven by non-affinity terms


def test_steering_summary_deterministic_and_capped():
    from app.cook.affinity import steering_summary

    signals = [
        _sig("liked", cuisine="thai", ingredients=("tofu", "lime")),
        _sig("liked", cuisine="thai", ingredients=("tofu", "basil")),
        _sig("liked", cuisine="mexican", ingredients=("beans",)),
        _sig("disliked", cuisine="french", ingredients=("cream",)),
    ]
    out = steering_summary(signals)
    assert out == steering_summary(signals)  # deterministic
    assert len(out) <= 200
    assert "thai" in out and "tofu" in out and "cream" in out


def test_steering_summary_empty_signals_is_empty_string():
    from app.cook.affinity import steering_summary

    assert steering_summary([]) == ""
