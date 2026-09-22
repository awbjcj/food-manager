from __future__ import annotations

import asyncio
import base64
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlmodel import select

from app import handler_support
from app.client_set import PerUserClients
from app.cook.models import (
    NutritionScore,
    NutritionScores,
    RecipeCandidates,
    SelectedItems,
)
from app.llm import CorrectionDiff, LLMResult, ParsedItem, ParseResult, ProposedAddItem
from app.miniapp_workspace import COMMANDS, MAX_IMAGE_BYTES, WorkspaceRuntime
from app.models import (
    CookSession,
    GroupBinding,
    Household,
    PantryItem,
    PendingCorrection,
    Receipt,
    User,
)
from app.webapp import build_web_app
from tests.fakes import (
    FakeLLMClient,
    FakeNutritionLLM,
    FakeRecipeLLM,
    FakeSelectionLLM,
    FakeTextLLMClient,
)
from tests.test_calendar_export import _candidate, _plan_and_entry
from tests.test_plan_bot import FakeComposerSelector, _seed_pantry
from tests.test_webapp_api import TOKEN, _auth, web_state  # noqa: F401


@pytest.fixture
def kitchen(web_state, monkeypatch, tmp_path):  # noqa: F811 - imported pytest fixture
    sessions, payments, _app = web_state
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 42)
    monkeypatch.setattr(handler_support, "OPEN_REGISTRATION", False)
    monkeypatch.setattr(handler_support, "MULTI_TENANT_ENABLED", True)
    now = datetime.now(UTC)
    text = FakeTextLLMClient(
        canned_add=(
            [
                ProposedAddItem(
                    name="Apples",
                    category="produce",
                    explicit_user_expiry=True,
                    shelf_life_days=7,
                    confidence=1,
                )
            ],
            100,
        ),
        canned_correct=(
            CorrectionDiff(
                shelf_life_days=12, rationale="user correction", confidence=1
            ),
            100,
        ),
    )
    image = FakeLLMClient(
        canned=LLMResult(
            parse=ParseResult(
                items=[
                    ParsedItem(
                        name="Carrots",
                        is_food=True,
                        category="produce",
                        est_shelf_life_days=7,
                        confidence=1,
                    )
                ]
            )
        )
    )
    clients = PerUserClients.for_tests(
        image=image,
        text=text,
        selection=FakeSelectionLLM(canned=(SelectedItems(item_ids=[1, 2, 3]), 5)),
        recipe=FakeRecipeLLM(
            canned=(RecipeCandidates(candidates=[_candidate().recipe]), 5)
        ),
        nutrition=FakeNutritionLLM(
            canned=(
                NutritionScores(
                    scores=[
                        NutritionScore(
                            health_score=70,
                            effort="easy",
                            est_minutes=25,
                            rationale="balanced",
                        )
                    ]
                ),
                5,
            )
        ),
    )
    bot = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="food_test")),
        send_message=AsyncMock(),
        get_chat=AsyncMock(return_value=SimpleNamespace(type="supergroup")),
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status="administrator")),
    )
    reschedule, unschedule = Mock(), Mock()
    with sessions() as session:
        session.add(
            PantryItem(
                household_id=1,
                raw_name="Milk",
                normalized_name="milk",
                category="dairy",
                purchased_on=now.date(),
                shelf_life_days=5,
                shelf_life_source="manual",
                ingest_shelf_life_source="manual",
                expires_on=now.date() + timedelta(days=5),
                created_via="manual",
                created_at=now,
            )
        )
        session.add(Household(id=2, name="Other household", created_at=now))
        session.add(User(telegram_id=99, chat_id=99, household_id=2, created_at=now))
        session.commit()
    app = build_web_app(
        session_factory=sessions,
        bot_token=TOKEN,
        payments=payments,
        billing_enabled=True,
        available_providers=("anthropic", "gemini"),
        bot_username="food_test",
        static_dir=tmp_path / "missing",
        clients=clients,
        bot=bot,
        reschedule=reschedule,
        unschedule=unschedule,
        allowed_telegram_user_id=42,
        composer=FakeComposerSelector(error=RuntimeError("use deterministic fallback")),
    )
    return SimpleNamespace(
        app=app,
        sessions=sessions,
        clients=clients,
        image=image,
        text=text,
        bot=bot,
        reschedule=reschedule,
        unschedule=unschedule,
    )


async def state(client, user=42):
    response = await client.get(
        "/api/workspace", headers={"Authorization": _auth(user)}
    )
    assert response.status == 200, await response.text()
    return await response.json()


async def finished(client, user=42):
    for _ in range(100):
        result = await state(client, user)
        if not result["busy"]:
            assert result["error"] is None, result
            return result
        await asyncio.sleep(0.01)
    pytest.fail("workspace did not finish")


async def action(client, *, user=42, **body):
    current = await state(client, user)
    response = await client.post(
        "/api/workspace/actions",
        headers={"Authorization": _auth(user)},
        json={"workspaceId": current["id"], "requestId": str(uuid.uuid4()), **body},
    )
    assert response.status == 202, await response.text()
    return await finished(client, user)


def button(result, text):
    for card in reversed(result["cards"]):
        for row in card["buttons"]:
            for entry in row:
                if text.lower() in entry["text"].lower() and entry["action"]:
                    return {
                        "kind": "callback",
                        "cardId": card["id"],
                        "action": entry["action"],
                    }
    pytest.fail(f"button {text!r} missing: {result}")


@pytest.mark.asyncio
async def test_add_apply_and_retry_are_real_and_idempotent(kitchen):
    async with TestClient(TestServer(kitchen.app)) as client:
        result = await action(
            client, kind="command", command="add", text="apples for 7 days"
        )
        with kitchen.sessions() as session:
            assert len(session.exec(select(PantryItem)).all()) == 1
            assert len(session.exec(select(PendingCorrection)).all()) == 1
        payload = {
            "workspaceId": result["id"],
            "requestId": "same-apply",
            **button(result, "Apply"),
        }
        for _ in range(2):
            response = await client.post(
                "/api/workspace/actions",
                headers={"Authorization": _auth(42)},
                json=payload,
            )
            assert response.status == 202
            await finished(client)
        with kitchen.sessions() as session:
            assert len(session.exec(select(PantryItem)).all()) == 2
            assert session.exec(select(PendingCorrection)).one().status == "applied"


@pytest.mark.asyncio
async def test_pantry_buttons_correction_reply_and_stale_actions(kitchen):
    async with TestClient(TestServer(kitchen.app)) as client:
        result = await action(client, kind="command", command="pantry", text="1")
        old = button(result, "Correct")
        result = await action(client, **old)
        stale = await client.post(
            "/api/workspace/actions",
            headers={"Authorization": _auth(42)},
            json={"workspaceId": result["id"], "requestId": "stale", **old},
        )
        assert stale.status == 409
        # The free-text correction button creates a real ForceReply card.
        free = next(
            entry
            for row in result["cards"][-1]["buttons"]
            for entry in row
            if any(
                word in entry["text"].lower()
                for word in ("text", "describe", "other", "type", "something else")
            )
        )
        result = await action(
            client,
            kind="callback",
            cardId=result["cards"][-1]["id"],
            action=free["action"],
        )
        prompt = next(card for card in result["cards"] if card["reply"])
        result = await action(
            client, kind="reply", cardId=prompt["id"], text="lasts twelve days"
        )
        await action(client, **button(result, "Apply"))
        with kitchen.sessions() as session:
            assert session.get(PantryItem, 1).shelf_life_days == 12


@pytest.mark.asyncio
async def test_receipt_upload_and_undo(kitchen):
    async with TestClient(TestServer(kitchen.app)) as client:
        current = await state(client)
        response = await client.post(
            "/api/workspace/photo",
            headers={
                "Authorization": _auth(42),
                "X-Workspace-Id": current["id"],
                "X-Request-Id": "upload",
            },
            data=b"\xff\xd8\xffreceipt-test",
        )
        assert response.status == 202
        result = await finished(client)
        assert kitchen.image.calls == [b"\xff\xd8\xffreceipt-test"]
        with kitchen.sessions() as session:
            assert len(session.exec(select(Receipt)).all()) == 1
            assert (
                len(
                    session.exec(
                        select(PantryItem).where(PantryItem.status == "active")
                    ).all()
                )
                == 2
            )
        await action(client, **button(result, "Undo"))
        with kitchen.sessions() as session:
            assert (
                len(
                    session.exec(
                        select(PantryItem).where(PantryItem.status == "active")
                    ).all()
                )
                == 1
            )


@pytest.mark.asyncio
async def test_cook_wizard_and_calendar_download_stay_in_app(kitchen):
    with kitchen.sessions() as session:
        plan, entry = _plan_and_entry()
        session.add(plan)
        session.add(entry)
        session.commit()
    async with TestClient(TestServer(kitchen.app)) as client:
        result = await action(client, kind="command", command="cook")
        result = await action(client, **button(result, "Dinner"))
        with kitchen.sessions() as session:
            cook = session.exec(select(CookSession)).one()
            assert cook.meal_type == "Dinner" and cook.message_id < 0
        assert result["cards"][-1]["buttons"]
        result = await action(client, kind="command", command="calendar")
        doc = result["cards"][-1]["document"]
        assert doc["name"].endswith(".ics")
        content = base64.b64decode(doc["data"]).decode()
        assert "BEGIN:VCALENDAR" in content and "Pasta" in content
        kitchen.bot.send_message.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,text",
    [
        ("list", "week"),
        ("stats", ""),
        ("shopping", ""),
        ("favorites", ""),
        ("history", ""),
        ("prefs", ""),
        ("help", ""),
        ("household", ""),
        ("invite", "family"),
        ("quota", ""),
        ("billing", ""),
        ("buy", ""),
        ("plan_current", ""),
    ],
)
async def test_read_workflows_have_in_app_results(kitchen, command, text):
    async with TestClient(TestServer(kitchen.app)) as client:
        result = await action(client, kind="command", command=command, text=text)
        assert result["cards"] and result["cards"][-1]["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,text,field,expected",
    [
        ("ate", "1", "status", "eaten"),
        ("toss", "1", "status", "tossed"),
        ("delete", "1", "status", "removed"),
        (
            "snooze",
            "1 3",
            "snoozed_until",
            (datetime.now(ZoneInfo("America/New_York")) + timedelta(days=3)).date(),
        ),
    ],
)
async def test_pantry_mutations_persist(kitchen, command, text, field, expected):
    async with TestClient(TestServer(kitchen.app)) as client:
        await action(client, kind="command", command=command, text=text)
        with kitchen.sessions() as session:
            assert getattr(session.get(PantryItem, 1), field) == expected


@pytest.mark.asyncio
async def test_account_commands_reschedule_and_persist(kitchen):
    async with TestClient(TestServer(kitchen.app)) as client:
        for command, text in (
            ("tz", "Europe/Paris"),
            ("digest_at", "11"),
            ("llm", "anthropic"),
            ("lang", "fr"),
        ):
            await action(client, kind="command", command=command, text=text)
    assert kitchen.reschedule.call_count == 2
    with kitchen.sessions() as session:
        user = session.get(User, 42)
        assert (user.tz, user.digest_hour, user.llm_provider, user.lang) == (
            "Europe/Paris",
            11,
            "anthropic",
            "fr",
        )


@pytest.mark.asyncio
async def test_workspace_identity_household_and_callback_isolation(kitchen):
    async with TestClient(TestServer(kitchen.app)) as client:
        missing = await client.get("/api/workspace")
        assert missing.status == 401
        result = await action(client, kind="command", command="pantry", text="1")
        other = await state(client, 99)
        assert other["cards"] == []
        forged = await client.post(
            "/api/workspace/actions",
            headers={"Authorization": _auth(99)},
            json={
                "workspaceId": other["id"],
                "requestId": "steal",
                **button(result, "Correct"),
            },
        )
        assert forged.status == 409
        await action(client, user=99, kind="command", command="ate", text="1")
        with kitchen.sessions() as session:
            assert session.get(PantryItem, 1).status == "active"
            user = session.get(User, 42)
            user.banned = True
            session.add(user)
            session.commit()
        for method, path in (
            ("get", "/api/workspace"),
            ("post", "/api/workspace/actions"),
            ("post", "/api/workspace/photo"),
        ):
            response = await getattr(client, method)(
                path, headers={"Authorization": _auth(42)}
            )
            assert response.status == 401


@pytest.mark.asyncio
async def test_unregistered_can_join_but_cannot_execute_other_commands(kitchen):
    async with TestClient(TestServer(kitchen.app)) as client:
        unknown = await state(client, 77)
        assert not unknown["registered"]
        denied = await client.post(
            "/api/workspace/actions",
            headers={"Authorization": _auth(77)},
            json={
                "workspaceId": unknown["id"],
                "requestId": "bad",
                "kind": "command",
                "command": "add",
                "text": "apples",
            },
        )
        assert denied.status == 401
        result = await action(client, kind="command", command="invite")
        from app.models import HouseholdInvite

        with kitchen.sessions() as session:
            token = session.exec(select(HouseholdInvite)).one().token
        result = await action(
            client, user=77, kind="command", command="join", text=token
        )
        assert result["registered"]
        with kitchen.sessions() as session:
            assert session.get(User, 77).household_id == 1
        kitchen.reschedule.assert_called()


@pytest.mark.asyncio
async def test_group_binding_verifies_telegram_admin_membership(kitchen):
    async with TestClient(TestServer(kitchen.app)) as client:
        await action(client, kind="command", command="bind", text="-100123")
        kitchen.bot.get_chat_member.assert_awaited_once_with(-100123, 42)
        with kitchen.sessions() as session:
            assert session.get(GroupBinding, -100123).household_id == 1


@pytest.mark.asyncio
async def test_receipt_rejects_unsupported_and_oversize_files(kitchen):
    async with TestClient(TestServer(kitchen.app)) as client:
        current = await state(client)
        headers = {
            "Authorization": _auth(42),
            "X-Workspace-Id": current["id"],
            "X-Request-Id": "invalid",
        }
        wrong = await client.post(
            "/api/workspace/photo", headers=headers, data=b"not an image"
        )
        assert wrong.status == 400
        large = await client.post(
            "/api/workspace/photo",
            headers=headers,
            data=b"\xff\xd8\xff" + b"x" * MAX_IMAGE_BYTES,
        )
        assert large.status == 413
        assert kitchen.image.calls == []


def test_ui_catalog_covers_every_bot_command():
    source = Path("web/src/Workspace.tsx").read_text(encoding="utf-8")
    commands = set(re.findall(r"command: '([^']+)'", source))
    assert set(COMMANDS) <= commands


@pytest.mark.asyncio
async def test_busy_rejects_duplicate_work_and_cleanup_cancels_children(kitchen):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def slow(_message, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    from unittest.mock import patch

    runtime = WorkspaceRuntime(
        session_factory=kitchen.sessions, clients=kitchen.clients
    )
    workspace = runtime.get(42, 1)
    body = {
        "workspaceId": workspace.id,
        "requestId": "slow",
        "kind": "command",
        "command": "help",
    }
    with patch.dict(COMMANDS, help=(slow, ())):
        runtime.submit(workspace, body)
        await started.wait()
        runtime.submit(workspace, body)  # retry is idempotent even while running
        from aiohttp import web

        with pytest.raises(web.HTTPConflict):
            runtime.submit(workspace, {**body, "requestId": "second"})
        await runtime.close(None)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_cook_generation_save_shopping_and_feedback(kitchen):
    from app.models import SavedRecipe, ShoppingList

    _seed_pantry(kitchen.sessions, datetime.now(UTC).date())
    async with TestClient(TestServer(kitchen.app)) as client:
        result = await action(client, kind="command", command="cook")
        # Follow each emitted wizard choice, including the final background job.
        for _ in range(3):
            card = result["cards"][-1]
            result = await action(
                client,
                kind="callback",
                cardId=card["id"],
                action=card["buttons"][0][0]["action"],
            )
        with kitchen.sessions() as session:
            cook = session.exec(select(CookSession)).one()
            assert cook.status == "done"
            assert "Pasta" in cook.candidates_json
        assert "Pasta" in result["cards"][-1]["text"]
        result = await action(client, **button(result, "Save"))
        result = await action(client, **button(result, "Shopping list"))
        result = await action(client, **button(result, "Liked"))
        with kitchen.sessions() as session:
            assert len(session.exec(select(SavedRecipe)).all()) == 1
            assert session.exec(select(ShoppingList)).all()
            assert session.exec(select(CookSession)).one().feedback == "liked"
        result = await action(client, kind="command", command="shopping")
        card = result["cards"][-1]
        await action(
            client,
            kind="callback",
            cardId=card["id"],
            action=card["buttons"][0][0]["action"],
        )
        kitchen.bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_plan_generation_shopping_and_cancel(kitchen):
    from app.models import MealPlan

    _seed_pantry(kitchen.sessions, datetime.now(UTC).date())
    async with TestClient(TestServer(kitchen.app)) as client:
        result = await action(client, kind="command", command="plan", text="3")
        with kitchen.sessions() as session:
            plan = session.exec(select(MealPlan)).one()
            assert plan.status == "active" and plan.message_id < 0
        assert "Pasta" in result["cards"][-1]["text"]
        result = await action(client, **button(result, "Shopping"))
        await action(client, **button(result, "Cancel"))
        with kitchen.sessions() as session:
            assert session.exec(select(MealPlan)).one().status == "cancelled"


@pytest.mark.asyncio
async def test_household_leave_remove_and_old_cards_are_invalidated(kitchen):
    with kitchen.sessions() as session:
        session.add(
            User(
                telegram_id=77,
                chat_id=77,
                household_id=1,
                role="member",
                created_at=datetime.now(UTC),
            )
        )
        session.commit()
    async with TestClient(TestServer(kitchen.app)) as client:
        result = await action(
            client, user=77, kind="command", command="pantry", text="1"
        )
        old = button(result, "Correct")
        # Members cannot remove other household members.
        await action(client, user=77, kind="command", command="remove", text="42")
        with kitchen.sessions() as session:
            assert session.get(User, 42) is not None
        await action(client, user=77, kind="command", command="leave")
        result = await state(client, 77)
        assert not result["registered"] and result["cards"] == []
        kitchen.unschedule.assert_called_once_with(77)
        denied = await client.post(
            "/api/workspace/actions",
            headers={"Authorization": _auth(77)},
            json={"workspaceId": result["id"], "requestId": "after-leave", **old},
        )
        assert denied.status == 401


@pytest.mark.asyncio
async def test_group_non_admin_cannot_bind(kitchen):
    kitchen.bot.get_chat_member.return_value = SimpleNamespace(status="member")
    async with TestClient(TestServer(kitchen.app)) as client:
        current = await state(client)
        response = await client.post(
            "/api/workspace/actions",
            headers={"Authorization": _auth(42)},
            json={
                "workspaceId": current["id"],
                "requestId": "no-admin",
                "kind": "command",
                "command": "bind",
                "text": "-100123",
            },
        )
        assert response.status == 202
        result = current
        for _ in range(100):
            result = await state(client)
            if not result["busy"]:
                break
            await asyncio.sleep(0.01)
        assert result["error"]
        with kitchen.sessions() as session:
            assert session.get(GroupBinding, -100123) is None


@pytest.mark.asyncio
async def test_local_workspace_gates_households_and_strangers(kitchen, tmp_path):
    app = build_web_app(
        session_factory=kitchen.sessions,
        bot_token=TOKEN,
        payments=None,
        billing_enabled=False,
        available_providers=("anthropic",),
        bot_username=None,
        static_dir=tmp_path / "missing",
        hosted_features_enabled=False,
        allowed_telegram_user_id=42,
    )
    async with TestClient(TestServer(app)) as client:
        response = await client.get(
            "/api/workspace", headers={"Authorization": _auth(99)}
        )
        assert response.status == 401
        result = await action(client, kind="command", command="invite")
        assert "hosted" in result["cards"][-1]["text"].lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"kind": []},
        {"kind": "command", "command": []},
        {"kind": "callback", "cardId": []},
        {"kind": "command", "command": "operator"},
        {"kind": "text", "text": "x" * 4001},
    ],
)
async def test_workspace_rejects_malformed_actions(kitchen, payload):
    async with TestClient(TestServer(kitchen.app)) as client:
        current = await state(client)
        response = await client.post(
            "/api/workspace/actions",
            headers={"Authorization": _auth(42)},
            json={"workspaceId": current["id"], "requestId": "invalid", **payload},
        )
        assert response.status == 400
