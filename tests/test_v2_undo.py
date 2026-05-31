import pytest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from sqlmodel import SQLModel, Session, create_engine
from app.models import Household, PantryItem, Receipt, User
from app.commands import CallbackAction, CommandError, parse_callback
from app.pantry_service import UndoResult
from app.renderer import (
    build_undo_keyboard,
    build_undo_add_keyboard,
    render_undo_result,
)
import app.bot as bot_mod


def test_parse_undo_receipt_and_add():
    assert parse_callback("undo:receipt:12") == CallbackAction(
        verb="undo_receipt", item_id=12
    )
    assert parse_callback("undo:add:7") == CallbackAction(verb="undo_add", item_id=7)


def test_parse_undo_bad_id():
    with pytest.raises(CommandError):
        parse_callback("undo:receipt:x")


def test_parse_undo_bad_kind():
    with pytest.raises(CommandError):
        parse_callback("undo:bogus:1")


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        db.add(User(telegram_id=1, chat_id=1, household_id=household.id, created_at=datetime.now(timezone.utc)))
        db.commit()
        yield db


def _receipt(session, *, scanned_at):
    r = Receipt(
        household_id=1,
        photo_file_id=f"p{scanned_at.timestamp()}",
        purchase_date=date(2026, 5, 28),
        purchase_date_source="receipt",
        scanned_at=scanned_at,
    )
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


def _ritem(
    session,
    receipt_id,
    name,
    *,
    status="active",
    snoozed_until=None,
    source="llm",
    created_at=None,
):
    item = PantryItem(
        household_id=1,
        raw_name=name,
        normalized_name=name.lower(),
        category="produce",
        qty=1.0,
        unit=None,
        purchased_on=date(2026, 5, 28),
        shelf_life_days=5,
        shelf_life_source=source,
        ingest_shelf_life_source="llm",
        expires_on=date(2026, 6, 2),
        status=status,
        snoozed_until=snoozed_until,
        created_via="receipt",
        source_receipt_id=receipt_id,
        created_at=created_at or datetime.now(timezone.utc),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def test_undo_receipt_full_removes_all_and_deletes_receipt(session):
    now = datetime.now(timezone.utc)
    r = _receipt(session, scanned_at=now)
    a = _ritem(session, r.id, "A")
    b = _ritem(session, r.id, "B")
    from app.pantry_service import undo_receipt

    assert r.id is not None
    result = undo_receipt(session, household_id=1, receipt_id=r.id, now=now)
    assert result.expired is False
    assert set(result.removed_ids) == {a.id, b.id}
    assert result.skipped == []
    assert result.receipt_deleted is True
    session.refresh(a)
    session.refresh(b)
    assert a.status == "removed" and b.status == "removed"
    assert a.source_receipt_id is None
    assert session.get(Receipt, r.id) is None


def test_undo_receipt_partial_keeps_receipt(session):
    now = datetime.now(timezone.utc)
    r = _receipt(session, scanned_at=now)
    a = _ritem(session, r.id, "A")
    eaten = _ritem(session, r.id, "B", status="eaten")
    assert r.id is not None
    from app.pantry_service import undo_receipt

    result = undo_receipt(session, household_id=1, receipt_id=r.id, now=now)
    assert result.removed_ids == [a.id]
    assert result.skipped == [(eaten.id, "eaten")]
    assert result.receipt_deleted is False
    assert session.get(Receipt, r.id) is not None
    session.refresh(a)
    assert a.status == "removed" and a.source_receipt_id == r.id


def test_undo_receipt_expired_after_ttl(session):
    now = datetime.now(timezone.utc)
    r = _receipt(session, scanned_at=now - timedelta(minutes=11))
    _ritem(session, r.id, "A")
    assert r.id is not None
    from app.pantry_service import undo_receipt

    result = undo_receipt(session, household_id=1, receipt_id=r.id, now=now)
    assert result.expired is True
    assert result.removed_ids == []


def test_undo_add_single_item(session):
    now = datetime.now(timezone.utc)
    item = _ritem(session, None, "Solo", created_at=now)
    from app.pantry_service import undo_add

    assert item.id is not None
    result = undo_add(session, household_id=1, item_id=item.id, now=now)
    assert result.removed_ids == [item.id]
    assert result.receipt_deleted is False
    session.refresh(item)
    assert item.status == "removed"


def test_undo_add_explicit_expiry_is_undoable(session):
    # A manual /add with an explicit expiry is born shelf_life_source=
    # "user_correction"; its own Undo button must still remove it (regression:
    # it used to be wrongly reported as "skipped (corrected)").
    now = datetime.now(timezone.utc)
    item = _ritem(session, None, "Solo", source="user_correction", created_at=now)
    from app.pantry_service import undo_add

    assert item.id is not None
    result = undo_add(session, household_id=1, item_id=item.id, now=now)
    assert result.removed_ids == [item.id]
    assert result.skipped == []
    session.refresh(item)
    assert item.status == "removed"


def test_undo_add_skips_eaten(session):
    now = datetime.now(timezone.utc)
    item = _ritem(session, None, "Solo", status="eaten", created_at=now)
    from app.pantry_service import undo_add

    assert item.id is not None
    result = undo_add(session, household_id=1, item_id=item.id, now=now)
    assert result.removed_ids == []
    assert result.skipped == [(item.id, "eaten")]


def test_undo_keyboards():
    assert build_undo_keyboard(receipt_id=12)[0][0].callback_data == "undo:receipt:12"
    assert build_undo_add_keyboard(item_id=7)[0][0].callback_data == "undo:add:7"


def test_render_undo_result_full_and_partial():
    full = render_undo_result(
        UndoResult([1, 2], [], receipt_deleted=True, expired=False)
    )
    assert "Undone" in full and "2" in full
    partial = render_undo_result(
        UndoResult([1], [(3, "eaten")], receipt_deleted=False, expired=False)
    )
    assert "skipped #3 (eaten)" in partial
    expired = render_undo_result(UndoResult([], [], False, expired=True))
    assert "expired" in expired.lower()


# ---------------------------------------------------------------------------
# Handler integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def handler_engine():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        household = Household(created_at=datetime.now(timezone.utc))
        db.add(household)
        db.commit()
        db.refresh(household)
        db.add(User(telegram_id=1, chat_id=99, household_id=household.id, created_at=datetime.now(timezone.utc)))
        db.commit()
    return engine


def _cb(data: str, *, user_id: int = 1):
    cb = MagicMock()
    cb.from_user = MagicMock(id=user_id)
    cb.data = data
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    return cb


@pytest.mark.asyncio
async def test_handle_callback_undo_receipt(handler_engine, monkeypatch):
    monkeypatch.setattr(bot_mod, "ALLOWED_TELEGRAM_USER_ID", 1)
    now = datetime.now(timezone.utc)

    # Insert a receipt and an active item referencing it
    with Session(handler_engine) as setup_db:
        receipt = Receipt(
            household_id=1,
            photo_file_id="ph_undo_test",
            purchase_date=date(2026, 5, 28),
            purchase_date_source="receipt",
            scanned_at=now,
        )
        setup_db.add(receipt)
        setup_db.commit()
        setup_db.refresh(receipt)
        receipt_id = receipt.id
        assert receipt_id is not None

        item = PantryItem(
            household_id=1,
            raw_name="Eggs",
            normalized_name="eggs",
            category="dairy",
            qty=1.0,
            unit=None,
            purchased_on=date(2026, 5, 28),
            shelf_life_days=14,
            shelf_life_source="llm",
            ingest_shelf_life_source="llm",
            expires_on=date(2026, 6, 11),
            status="active",
            created_via="receipt",
            source_receipt_id=receipt_id,
            created_at=now,
        )
        setup_db.add(item)
        setup_db.commit()
        setup_db.refresh(item)
        item_id = item.id
        assert item_id is not None

    cb = _cb(f"undo:receipt:{receipt_id}")

    await bot_mod.handle_callback(
        cb,
        session_factory=lambda: Session(handler_engine),
        now_provider=lambda tz: datetime(2026, 5, 29, tzinfo=timezone.utc),
    )

    # edit_text should have been called with a message containing "Undone"
    cb.message.edit_text.assert_awaited_once()
    edited_text = cb.message.edit_text.await_args.args[0]
    assert "Undone" in edited_text

    # The item should now be status="removed" — verify in a fresh session
    with Session(handler_engine) as verify_db:
        refreshed = verify_db.get(PantryItem, item_id)
        assert refreshed is not None
        assert refreshed.status == "removed"

    # cb.answer should have been called with "undone"
    cb.answer.assert_awaited_once_with("undone")
