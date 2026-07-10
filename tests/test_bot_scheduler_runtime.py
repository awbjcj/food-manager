from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import SQLModel, Session, create_engine

from app.backup import BackupError, pre_migration_backup
from app.bot import (
    authorize_and_get_user,
    build_dispatcher,
    handle_ate,
    handle_item_callback,
    handle_callback,
    handle_help,
    handle_list,
    handle_llm,
    handle_start,
)
from app.llm import (
    LLMProviderSelector,
    LLMResult,
    ParseResult,
    ProfileLLMProviderSelector,
    TextLLMProviderSelector,
)
from app.models import Household, PantryItem, User
from app.scheduler import (
    build_digest_payload,
    register_all_user_digests,
    schedule_user_digest,
    send_digest_once,
)
from tests.fakes import FakeLLMClient, FakeProfileLLMClient, FakeTextLLMClient


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        assert household.id is not None
        db.add(User(telegram_id=1, chat_id=999, household_id=household.id,
                    created_at=datetime.now(timezone.utc)))
        db.commit()
        yield db


def _msg(text: str, *, user_id=1, chat_id=1, chat_type="private"):
    msg = MagicMock()
    msg.from_user = MagicMock(id=user_id)
    msg.chat = MagicMock(id=chat_id, type=chat_type)
    msg.text = text
    msg.answer = AsyncMock()
    return msg


def _cb(data: str, *, user_id=1):
    cb = MagicMock()
    cb.from_user = MagicMock(id=user_id)
    cb.data = data
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.answer = AsyncMock()
    return cb


def _edited_keyboard_datas(cb):
    markup = cb.message.edit_text.await_args.kwargs["reply_markup"]
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def _add_item(
    session,
    *,
    household_id=1,
    name="Milk",
    days=2,
    status="active",
    snoozed_until=None,
):
    today = date(2026, 5, 26)
    item = PantryItem(
        household_id=household_id,
        raw_name=name,
        normalized_name=name.lower(),
        category="dairy",
        qty=1.0,
        unit="gal",
        purchased_on=today,
        shelf_life_days=days,
        shelf_life_source="llm",
        ingest_shelf_life_source="llm",
        expires_on=today + timedelta(days=days),
        status=status,
        snoozed_until=snoozed_until,
        created_via="manual",
        created_at=datetime.now(timezone.utc),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def test_authorize_and_get_user(session):
    assert authorize_and_get_user(
        session,
        allowed_user_id=1,
        telegram_user_id=99,
        chat_id=99,
        chat_type="private",
    ).allowed is False
    assert authorize_and_get_user(
        session,
        allowed_user_id=1,
        telegram_user_id=1,
        chat_id=-100,
        chat_type="group",
    ).allowed is False
    session.delete(session.get(User, 1))
    session.commit()
    decision = authorize_and_get_user(
        session,
        allowed_user_id=1,
        telegram_user_id=1,
        chat_id=1,
        chat_type="private",
    )
    assert decision.allowed is True
    assert decision.created is True
    assert decision.user is not None
    assert decision.user.tz == "America/Detroit"
    assert decision.user.digest_hour == 8


@pytest.mark.asyncio
async def test_handlers_smoke(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)

    def factory() -> Session:
        return session

    def now(tz):
        return datetime(2026, 5, 26, tzinfo=timezone.utc)

    start = _msg("/start")
    await handle_start(start, session_factory=factory, on_user_created=lambda user: None)
    assert "/tz" in start.answer.await_args.args[0]

    listing = _msg("/list")
    await handle_list(listing, session_factory=factory, now_provider=now)
    assert "no items" in listing.answer.await_args.args[0].lower()

    item = _add_item(session)
    item_id = item.id
    ate = _msg(f"/ate {item_id}")
    await handle_ate(ate, session_factory=factory, now_provider=now)
    assert session.get(PantryItem, item_id).status == "eaten"

    help_msg = _msg("/help")
    await handle_help(help_msg, session_factory=factory)
    from app.i18n import t

    assert "/pantry" in help_msg.answer.await_args.args[0]
    assert "/correct" in t("help.topic.pantry", "en")


@pytest.mark.asyncio
async def test_handle_llm_shows_and_switches_provider(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    llm = LLMProviderSelector(
        {
            "anthropic": FakeLLMClient(canned=LLMResult(parse=ParseResult(items=[]))),
            "openai": FakeLLMClient(canned=LLMResult(parse=ParseResult(items=[]))),
        },
        "anthropic",
    )
    text_llm = TextLLMProviderSelector(
        {
            "anthropic": FakeTextLLMClient(),
            "openai": FakeTextLLMClient(),
        },
        "anthropic",
    )

    status = _msg("/llm")
    await handle_llm(
        status,
        session_factory=lambda: session,
        llm=llm,
        text_llm=text_llm,
    )
    assert "LLM provider: anthropic" in status.answer.await_args.args[0]

    switch = _msg("/llm openai")
    await handle_llm(
        switch,
        session_factory=lambda: session,
        llm=llm,
        text_llm=text_llm,
    )
    assert session.get(User, 1).llm_provider == "openai"
    assert "set to openai" in switch.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_handle_llm_rejects_unconfigured_provider(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    llm = LLMProviderSelector(
        {"anthropic": FakeLLMClient(canned=LLMResult(parse=ParseResult(items=[])))},
        "anthropic",
    )
    text_llm = TextLLMProviderSelector({"anthropic": FakeTextLLMClient()}, "anthropic")

    msg = _msg("/llm openai")
    await handle_llm(
        msg,
        session_factory=lambda: session,
        llm=llm,
        text_llm=text_llm,
    )

    assert session.get(User, 1).llm_provider == "anthropic"
    assert "not configured" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_act_ate_callback_edits_digest_in_place(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    today = date(2026, 5, 26)
    eaten_id = _add_item(session, name="Milk", days=2).id
    _add_item(session, name="Bread", days=3)
    cb = _cb(f"act:ate:{eaten_id}")
    cb.message.edit_text = AsyncMock()
    await handle_callback(
        cb,
        session_factory=lambda: session,
        now_provider=lambda tz: datetime.combine(today, datetime.min.time(), timezone.utc),
    )
    assert session.get(PantryItem, eaten_id).status == "eaten"
    cb.message.edit_text.assert_awaited_once()
    assert cb.message.edit_text.await_args is not None
    edited_text = cb.message.edit_text.await_args.args[0]
    assert "Bread" in edited_text
    assert "Milk" not in edited_text


@pytest.mark.asyncio
async def test_act_callback_clears_digest_when_no_items_remain(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    today = date(2026, 5, 26)
    last_id = _add_item(session, name="Last Milk", days=1).id
    cb = _cb(f"act:ate:{last_id}")
    cb.message.edit_text = AsyncMock()
    await handle_callback(
        cb,
        session_factory=lambda: session,
        now_provider=lambda tz: datetime.combine(today, datetime.min.time(), timezone.utc),
    )
    cb.message.edit_text.assert_awaited_once()
    assert cb.message.edit_text.await_args is not None
    assert "clear" in cb.message.edit_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_act_callback_refreshes_digest_when_already_eaten(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    today = date(2026, 5, 26)
    already_id = _add_item(session, name="Stale", days=2, status="eaten").id
    cb = _cb(f"act:ate:{already_id}")
    cb.message.edit_text = AsyncMock()
    await handle_callback(
        cb,
        session_factory=lambda: session,
        now_provider=lambda tz: datetime.combine(today, datetime.min.time(), timezone.utc),
    )
    cb.message.edit_text.assert_awaited_once()
    assert cb.message.edit_text.await_args is not None
    assert "clear" in cb.message.edit_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_act_freeze_callback_extends_expiry(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    today = date(2026, 5, 26)
    item = _add_item(session, name="Chicken", days=2)
    cb = _cb(f"act:freeze:{item.id}")
    cb.message.edit_text = AsyncMock()
    await handle_callback(
        cb,
        session_factory=lambda: session,
        now_provider=lambda tz: datetime.combine(
            today,
            datetime.min.time(),
            timezone.utc,
        ),
    )
    frozen = session.get(PantryItem, item.id)
    assert frozen is not None
    assert frozen.storage == "frozen"
    assert frozen.stored_on == today
    assert frozen.expires_on == today + timedelta(days=270)


@pytest.mark.asyncio
async def test_act_fridge_callback_chills_and_extends_expiry(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    today = date(2026, 5, 26)
    item = _add_item(session, name="Chicken", days=2)
    cb = _cb(f"act:fridge:{item.id}")
    cb.message.edit_text = AsyncMock()
    await handle_callback(
        cb,
        session_factory=lambda: session,
        now_provider=lambda tz: datetime.combine(
            today, datetime.min.time(), timezone.utc
        ),
    )
    chilled = session.get(PantryItem, item.id)
    assert chilled is not None
    assert chilled.storage == "fridge"
    assert chilled.stored_on == today
    # Acknowledged the tap (answer-first) so the button never appears stuck.
    cb.answer.assert_awaited()


@pytest.mark.asyncio
async def test_show_all_callback_edits_due_items_in_place(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    today = date(2026, 5, 26)
    for idx in range(25):
        _add_item(session, name=f"Item {idx}", days=2)
    cb = _cb("show:all")
    cb.message.edit_text = AsyncMock()
    await handle_callback(
        cb,
        session_factory=lambda: session,
        now_provider=lambda tz: datetime.combine(today, datetime.min.time(), timezone.utc),
    )
    cb.message.answer.assert_not_awaited()
    cb.message.edit_text.assert_awaited_once()
    assert cb.message.edit_text.await_args is not None
    assert "Item 24" in cb.message.edit_text.await_args.args[0]
    datas = {
        button.callback_data
        for row in cb.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
        for button in row
    }
    assert any(data.startswith("item:open:") for data in datas)


@pytest.mark.asyncio
async def test_item_open_renders_card(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    today = date(2026, 5, 26)
    item_id = _add_item(session, name="Milk", days=1).id
    assert item_id is not None
    cb = _cb(f"item:open:{item_id}")
    cb.message.edit_text = AsyncMock()
    await handle_item_callback(
        cb,
        session_factory=lambda: session,
        now_provider=lambda tz: datetime.combine(today, datetime.min.time(), timezone.utc),
    )
    datas = _edited_keyboard_datas(cb)
    assert f"item:corr:{item_id}" in datas
    assert f"item:rm:{item_id}" in datas


@pytest.mark.asyncio
async def test_item_nudge_extends_expiry(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    today = date(2026, 5, 26)
    item = _add_item(session, name="Milk", days=7)
    item_id = item.id
    assert item_id is not None
    cb = _cb(f"item:nudge:{item_id}:p7")
    cb.message.edit_text = AsyncMock()
    await handle_item_callback(
        cb,
        session_factory=lambda: session,
        now_provider=lambda tz: datetime.combine(today, datetime.min.time(), timezone.utc),
    )
    updated = session.get(PantryItem, item_id)
    assert updated is not None
    assert updated.shelf_life_days == 14


@pytest.mark.asyncio
async def test_item_rmok_removes_and_refreshes(session, monkeypatch):
    monkeypatch.setattr("app.bot.ALLOWED_TELEGRAM_USER_ID", 1)
    today = date(2026, 5, 26)
    item_id = _add_item(session, name="Milk", days=1).id
    assert item_id is not None
    cb = _cb(f"item:rmok:{item_id}")
    cb.message.edit_text = AsyncMock()
    await handle_item_callback(
        cb,
        session_factory=lambda: session,
        now_provider=lambda tz: datetime.combine(today, datetime.min.time(), timezone.utc),
    )
    removed = session.get(PantryItem, item_id)
    assert removed is not None
    assert removed.status == "removed"


def test_build_dispatcher_imports_and_registers():
    fake_bot = MagicMock()
    fake_llm = FakeLLMClient(canned=LLMResult(parse=ParseResult(items=[])))
    dispatcher = build_dispatcher(
        bot=fake_bot,
        session_factory=lambda: MagicMock(),
        llm=fake_llm,
        text_llm=FakeTextLLMClient(),
        profile_llm=FakeProfileLLMClient(),
        now_provider=lambda tz: datetime.now(timezone.utc),
        on_user_created=lambda user: None,
        reschedule=lambda user: None,
    )
    assert dispatcher is not None


def test_build_llm_clients_includes_profile_llm():
    from bin.run import _build_llm_clients
    from app.settings import Settings

    settings = Settings(
        TELEGRAM_BOT_TOKEN="token",
        ALLOWED_TELEGRAM_USER_ID=1,
        LLM_PROVIDER="anthropic",
        ANTHROPIC_API_KEY="anthropic-key",
        OPENAI_API_KEY=None,
    )
    bundle = _build_llm_clients(settings)

    assert isinstance(bundle.image, LLMProviderSelector)
    assert isinstance(bundle.text, TextLLMProviderSelector)
    assert isinstance(bundle.profile, ProfileLLMProviderSelector)
    assert bundle.profile.available_providers == ("anthropic",)
    assert bundle.search is not None


def test_scheduler_payload_and_registration(session):
    today = date(2026, 5, 26)
    _add_item(session, days=-1)
    _add_item(session, days=7)
    _add_item(session, days=8)
    _add_item(session, days=3, status="eaten")
    _add_item(session, days=2, snoozed_until=today + timedelta(days=1))
    payload = build_digest_payload(session, user_id=1, today=today)
    assert payload is not None
    assert len(payload.items) == 2

    scheduler = AsyncIOScheduler()
    user = session.get(User, 1)
    schedule_user_digest(scheduler, user, send=AsyncMock())
    user.digest_hour = 9
    schedule_user_digest(scheduler, user, send=AsyncMock())
    jobs = scheduler.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == "digest:1"
    assert jobs[0].trigger.fields[jobs[0].trigger.FIELD_NAMES.index("hour")].expressions[0].first == 9
    register_all_user_digests(scheduler, session_factory=lambda: session, send=AsyncMock())
    assert {job.id for job in scheduler.get_jobs()} == {"digest:1"}


@pytest.mark.asyncio
async def test_send_digest_once(session):
    _add_item(session, name="Milk", days=2)
    bot = MagicMock()
    bot.send_message = AsyncMock()
    sent = await send_digest_once(
        user_id=1,
        bot=bot,
        session_factory=lambda: session,
        today_provider=lambda tz: date(2026, 5, 26),
    )
    assert sent is True
    assert bot.send_message.await_args.kwargs["chat_id"] == 999
    assert "Milk" in bot.send_message.await_args.kwargs["text"]


def test_backup_helper(tmp_path, monkeypatch):
    db = tmp_path / "f.db"
    db.write_bytes(b"sqlite-bytes")
    backup = pre_migration_backup(str(db), keep=5)
    assert backup is not None and Path(backup).read_bytes() == b"sqlite-bytes"
    assert pre_migration_backup(str(tmp_path / "missing.db"), keep=5) is None

    for idx in range(4):
        db.write_bytes(f"v{idx}".encode())
        pre_migration_backup(str(db), keep=3)
    assert len(sorted(tmp_path.glob("f.db.backup-*"))) == 3

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("shutil.copy2", boom)
    with pytest.raises(BackupError):
        pre_migration_backup(str(db), keep=3)


def test_runtime_imports_parse():
    import bin.eval_receipts  # noqa: F401
    import bin.run  # noqa: F401
