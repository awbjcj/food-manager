from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from app import handler_support
from app.batch_service import change_selection, create_batch
from app.client_set import EMPTY_CLIENTS, PerUserClients
from app.cook.affinity import affinity, steering_summary
from app.db import make_engine
from app.handlers.meta import handle_prefs
from app.models import Household, NameTranslation, PantryItem, PantryOutcome, User
from app.pantry_service import NotOwnerOrMissing, mark_eaten, mark_removed, mark_tossed
from app.preference_service import build_preference_profile, outcome_signal
from tests.fakes import FakeTranslationLLM

ORIGIN = date(2026, 10, 1)
NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)


@pytest.fixture
def sessions(tmp_path):
    engine = make_engine(str(tmp_path / "preferences.db"))
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([Household(id=n, created_at=NOW) for n in (1, 2)])
        db.commit()
        db.add_all(
            [
                User(
                    telegram_id=n,
                    chat_id=n,
                    household_id=1 if n < 3 else 2,
                    created_at=NOW,
                )
                for n in (1, 2, 3)
            ]
        )
        db.commit()
    yield lambda: Session(engine)
    engine.dispose()


def _item(
    db,
    name="apple",
    *,
    household_id=1,
    storage="default",
    stored_on=None,
    status="active",
):
    item = PantryItem(
        household_id=household_id,
        raw_name=name,
        normalized_name=name,
        purchased_on=ORIGIN,
        shelf_life_days=10,
        shelf_life_source="llm",
        ingest_shelf_life_source="llm",
        expires_on=ORIGIN + timedelta(days=10),
        storage=storage,
        stored_on=stored_on,
        created_via="manual",
        created_at=NOW,
        status=status,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    assert item.id is not None
    return item.id


@pytest.mark.parametrize(
    "status,days,expected",
    [
        ("eaten", 1, 0.9),
        ("eaten", 8, 0.2),
        ("eaten", 10, 0),
        ("eaten", 11, 0),
        ("tossed", 1, -1),
        ("tossed", 9, -1),
        ("tossed", 10, 0),
        ("tossed", 11, 0),
        ("eaten", -1, 0),
        ("tossed", -1, 0),
    ],
)
def test_signal_expiry_boundaries(status, days, expected):
    outcome = PantryOutcome(
        item_id=1,
        household_id=1,
        normalized_name="apple",
        status=status,
        origin_on=ORIGIN,
        expires_on=ORIGIN + timedelta(days=10),
        occurred_on=ORIGIN + timedelta(days=days),
    )
    assert outcome_signal(outcome) == pytest.approx(expected)


def test_personal_and_household_profiles_are_scoped_and_rebuild_after_restart(sessions):
    with sessions() as db:
        early = _item(db, "Organic Apple")
        late = _item(db, "pear")
        disliked = _item(db, "broccoli")
        spoiled = _item(db, "milk")
        foreign = _item(db, "foreign", household_id=2)
        mark_eaten(
            db,
            household_id=1,
            item_id=early,
            today=ORIGIN + timedelta(days=1),
            user_id=1,
        )
        mark_eaten(
            db,
            household_id=1,
            item_id=late,
            today=ORIGIN + timedelta(days=8),
            user_id=1,
        )
        mark_tossed(
            db,
            household_id=1,
            item_id=disliked,
            today=ORIGIN + timedelta(days=2),
            user_id=2,
        )
        mark_tossed(
            db,
            household_id=1,
            item_id=spoiled,
            today=ORIGIN + timedelta(days=11),
            user_id=2,
        )
        mark_tossed(
            db,
            household_id=2,
            item_id=foreign,
            today=ORIGIN + timedelta(days=2),
            user_id=3,
        )
        original = build_preference_profile(db, household_id=1)
    with sessions() as db:
        household = build_preference_profile(db, household_id=1)
        assert household == original
        assert household.history_count == 4
        assert [food.name for food in household.liked] == ["apple", "pear"]
        assert household.liked[0].score > household.liked[1].score > 0
        assert [food.name for food in household.disliked] == ["broccoli"]
        personal = build_preference_profile(db, household_id=1, user_id=1)
        assert personal.history_count == 2 and not personal.disliked
        assert (
            build_preference_profile(db, household_id=1, user_id=3).history_count == 0
        )
        assert build_preference_profile(db, household_id=2).history_count == 1
        member = db.get(User, 2)
        assert member is not None
        db.delete(member)
        db.commit()
        assert build_preference_profile(db, household_id=1) == original


def test_repeated_food_evidence_aggregates_positive_and_negative_signals(sessions):
    with sessions() as db:
        first, second = _item(db), _item(db, "Organic Apple")
        mark_eaten(db, household_id=1, item_id=first, today=ORIGIN, user_id=1)
        mark_tossed(db, household_id=1, item_id=second, today=ORIGIN, user_id=2)
        profile = build_preference_profile(db, household_id=1)
        assert len(profile.foods) == 1
        food = profile.foods[0]
        assert (food.eaten_count, food.tossed_count, food.evidence_count) == (1, 1, 2)
        assert food.score == 0
        assert not profile.liked and not profile.disliked


def test_storage_origin_and_expiry_are_snapshotted(sessions):
    with sessions() as db:
        stored = ORIGIN + timedelta(days=5)
        item_id = _item(db, storage="frozen", stored_on=stored)
        mark_eaten(
            db,
            household_id=1,
            item_id=item_id,
            today=stored + timedelta(days=1),
            user_id=1,
        )
        outcome = db.get(PantryOutcome, item_id)
        assert outcome is not None and outcome.origin_on == stored
        assert outcome_signal(outcome) == 0.8
        before = build_preference_profile(db, household_id=1)
        item = db.get(PantryItem, item_id)
        assert item is not None
        item.expires_on = ORIGIN
        item.normalized_name = "changed"
        db.add(item)
        db.commit()
        assert build_preference_profile(db, household_id=1) == before


def test_repeated_and_stale_actions_do_not_duplicate_or_reassign_history(sessions):
    with sessions() as db:
        item_id = _item(db)
    with sessions() as stale, sessions() as winner:
        stale_item = stale.get(PantryItem, item_id)
        assert stale_item is not None and stale_item.status == "active"
        assert mark_eaten(
            winner, household_id=1, item_id=item_id, today=ORIGIN, user_id=1
        ).applied
        assert mark_tossed(
            stale, household_id=1, item_id=item_id, today=ORIGIN, user_id=2
        ).was_already
    with sessions() as db:
        assert len(db.exec(select(PantryOutcome)).all()) == 1
        outcome = db.get(PantryOutcome, item_id)
        assert outcome is not None and (outcome.status, outcome.user_id) == ("eaten", 1)


def test_actor_and_item_household_checks_reject_foreign_history(sessions):
    with sessions() as db:
        item_id = _item(db)
        for household_id, user_id in ((1, 3), (2, 3), (1, 99)):
            with pytest.raises(NotOwnerOrMissing):
                mark_eaten(
                    db,
                    household_id=household_id,
                    item_id=item_id,
                    today=ORIGIN,
                    user_id=user_id,
                )
        item = db.get(PantryItem, item_id)
        assert item is not None and item.status == "active"
        assert db.exec(select(PantryOutcome)).all() == []


def test_failed_outcome_insert_rolls_back_the_item_transition(sessions):
    with sessions() as db:
        item_id = _item(db)
        db.connection().execute(
            text("""CREATE TRIGGER fail_outcome BEFORE INSERT ON pantryoutcome
            BEGIN SELECT RAISE(ABORT, 'simulated outcome failure'); END""")
        )
        db.commit()
        with pytest.raises(IntegrityError):
            mark_eaten(db, household_id=1, item_id=item_id, today=ORIGIN, user_id=1)
        item = db.get(PantryItem, item_id)
        assert item is not None and item.status == "active"
        assert db.exec(select(PantryOutcome)).all() == []


def test_removed_and_undated_legacy_items_do_not_create_preferences(sessions):
    with sessions() as db:
        removed = _item(db)
        _item(db, "legacy-eaten", status="eaten")
        _item(db, "legacy-tossed", status="tossed")
        mark_removed(db, household_id=1, item_id=removed, today=ORIGIN)
        assert not build_preference_profile(db, household_id=1).foods


@pytest.mark.parametrize("terminal", [mark_eaten, mark_tossed])
def test_removal_preserves_recorded_outcome_even_with_a_stale_item(sessions, terminal):
    with sessions() as db:
        item_id = _item(db)
    with sessions() as stale, sessions() as winner:
        item = stale.get(PantryItem, item_id)
        assert item is not None and item.status == "active"
        terminal(winner, household_id=1, item_id=item_id, today=ORIGIN, user_id=1)
        before = build_preference_profile(winner, household_id=1)
        result = mark_removed(
            stale, household_id=1, item_id=item_id, today=ORIGIN, user_id=2
        )
        assert not result.applied and result.was_already
        stale.refresh(item)
        outcome = stale.get(PantryOutcome, item_id)
        assert outcome is not None and item.status == outcome.status
        assert outcome.user_id == 1
        assert build_preference_profile(stale, household_id=1) == before


def test_individual_removal_is_neutral_and_idempotent(sessions):
    with sessions() as db:
        item_id = _item(db)
        assert mark_removed(
            db, household_id=1, item_id=item_id, today=ORIGIN, user_id=1
        ).applied
        assert mark_removed(
            db, household_id=1, item_id=item_id, today=ORIGIN, user_id=1
        ).was_already
        assert db.get(PantryOutcome, item_id) is None
        assert build_preference_profile(db, household_id=1).history_count == 0


@pytest.mark.parametrize("status", ["eaten", "tossed", "removed"])
def test_batch_records_actor_local_date_and_atomic_outcomes(sessions, status):
    with sessions() as db:
        for name in ("apple", "pear"):
            _item(db, name)
        # Local date differs from UTC, and the outcome must use the local day.
        from zoneinfo import ZoneInfo

        now = datetime(2026, 10, 3, 23, 30, tzinfo=ZoneInfo("America/New_York"))
        batch = create_batch(
            db, household_id=1, user_id=2, sort_by="expires", today=now.date(), now=now
        )
        for action, value in (
            ("select_page", None),
            ("confirm", status),
            ("apply", None),
        ):
            change_selection(
                db, batch, version=batch.version, action=action, value=value, now=now
            )
        outcomes = db.exec(select(PantryOutcome)).all()
        assert len(outcomes) == (0 if status == "removed" else 2)
        assert all(
            (row.user_id, row.occurred_on, row.status) == (2, now.date(), status)
            for row in outcomes
        )


def test_food_history_softly_ranks_recipes_and_steers_prompts(sessions):
    with sessions() as db:
        liked, disliked = _item(db, "apple"), _item(db, "pear")
        mark_eaten(db, household_id=1, item_id=liked, today=ORIGIN, user_id=1)
        mark_tossed(db, household_id=1, item_id=disliked, today=ORIGIN, user_id=1)
        profile = build_preference_profile(db, household_id=1)
        scores = [
            affinity(
                cuisine=None, ingredient_names=[name], signals=[], preferences=profile
            )
            for name in ("organic apple", "unknown", "pear")
        ]
        assert scores[0] > scores[1] == 0.5 > scores[2] > 0
        assert (
            steering_summary([], preferences=profile)
            == "Food history: likes apple; dislikes pear."
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["en", "zh", "fr", "es"])
async def test_prefs_shows_personal_and_household_history_without_llm(
    sessions, monkeypatch, lang
):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    monkeypatch.setattr(handler_support, "MULTI_TENANT_ENABLED", True)
    with sessions() as db:
        liked, disliked = _item(db, "apple"), _item(db, "pear")
        mark_eaten(db, household_id=1, item_id=liked, today=ORIGIN, user_id=1)
        mark_tossed(db, household_id=1, item_id=disliked, today=ORIGIN, user_id=2)
        user = db.get(User, 1)
        assert user is not None
        user.lang = lang
        db.add(user)
        if lang != "en":
            db.add(
                NameTranslation(
                    lang=lang, source_text="apple", translated_text="translated-apple"
                )
            )
        db.commit()
    msg = SimpleNamespace(
        text="/prefs",
        from_user=SimpleNamespace(id=1),
        chat=SimpleNamespace(id=1, type="private"),
        answer=AsyncMock(),
    )
    await handle_prefs(msg, session_factory=sessions, clients=EMPTY_CLIENTS)
    rendered = msg.answer.await_args.args[0]
    from app.i18n import t

    personal, household = rendered.split(t("preferences.household", lang))
    assert t("preferences.personal", lang) in personal
    assert "pear" not in personal and "pear" in household
    assert ("apple" if lang == "en" else "translated-apple") in personal


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["zh", "fr", "es"])
@pytest.mark.parametrize("fails", [False, True])
async def test_prefs_resolves_translation_misses_and_falls_back(
    sessions, monkeypatch, lang, fails
):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    monkeypatch.setattr(handler_support, "MULTI_TENANT_ENABLED", True)
    with sessions() as db:
        liked, disliked = _item(db, "apple"), _item(db, "pear")
        mark_eaten(db, household_id=1, item_id=liked, today=ORIGIN, user_id=1)
        mark_tossed(db, household_id=1, item_id=disliked, today=ORIGIN, user_id=2)
        user = db.get(User, 1)
        assert user is not None
        user.lang = lang
        db.add(user)
        db.add(
            NameTranslation(
                lang=lang, source_text="apple", translated_text="cached-apple"
            )
        )
        db.commit()
    translator = FakeTranslationLLM(
        table={"pear": "translated-pear"}, raise_n_times=int(fails)
    )
    msg = SimpleNamespace(
        text="/prefs",
        from_user=SimpleNamespace(id=1),
        chat=SimpleNamespace(id=1, type="private"),
        answer=AsyncMock(),
    )
    clients = PerUserClients.for_tests(translation=translator)
    await handle_prefs(msg, session_factory=sessions, clients=clients)
    rendered = msg.answer.await_args.args[0]
    assert translator.calls == [(("pear",), lang)]
    assert "cached-apple" in rendered
    assert ("pear" if fails else "translated-pear") in rendered
    with sessions() as db:
        cached = db.get(NameTranslation, (lang, "pear"))
        assert (cached is None) == fails
        assert build_preference_profile(db, household_id=1).history_count == 2
    if not fails:
        await handle_prefs(msg, session_factory=sessions, clients=clients)
        assert len(translator.calls) == 1
