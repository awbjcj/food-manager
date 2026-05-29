import pytest
from datetime import date, datetime, timedelta, timezone
from sqlmodel import SQLModel, Session, create_engine
from app.models import PantryItem, Receipt, User
from app.commands import CallbackAction, CommandError, parse_callback
from app.pantry_service import UndoResult
from app.renderer import build_undo_keyboard, build_undo_add_keyboard, render_undo_result


def test_parse_undo_receipt_and_add():
    assert parse_callback("undo:receipt:12") == CallbackAction(verb="undo_receipt", item_id=12)
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
        db.add(User(telegram_id=1, chat_id=1, created_at=datetime.now(timezone.utc)))
        db.commit()
        yield db


def _receipt(session, *, scanned_at):
    r = Receipt(user_id=1, photo_file_id=f"p{scanned_at.timestamp()}",
                purchase_date=date(2026, 5, 28), purchase_date_source="receipt",
                scanned_at=scanned_at)
    session.add(r); session.commit(); session.refresh(r)
    return r


def _ritem(session, receipt_id, name, *, status="active", snoozed_until=None,
           source="llm", created_at=None):
    item = PantryItem(
        user_id=1, raw_name=name, normalized_name=name.lower(), category="produce",
        qty=1.0, unit=None, purchased_on=date(2026, 5, 28), shelf_life_days=5,
        shelf_life_source=source, ingest_shelf_life_source="llm",
        expires_on=date(2026, 6, 2), status=status, snoozed_until=snoozed_until,
        created_via="receipt", source_receipt_id=receipt_id,
        created_at=created_at or datetime.now(timezone.utc),
    )
    session.add(item); session.commit(); session.refresh(item)
    return item


def test_undo_receipt_full_removes_all_and_deletes_receipt(session):
    now = datetime.now(timezone.utc)
    r = _receipt(session, scanned_at=now)
    a = _ritem(session, r.id, "A")
    b = _ritem(session, r.id, "B")
    from app.pantry_service import undo_receipt
    result = undo_receipt(session, user_id=1, receipt_id=r.id, now=now)
    assert result.expired is False
    assert set(result.removed_ids) == {a.id, b.id}
    assert result.skipped == []
    assert result.receipt_deleted is True
    session.refresh(a); session.refresh(b)
    assert a.status == "removed" and b.status == "removed"
    assert a.source_receipt_id is None
    assert session.get(Receipt, r.id) is None


def test_undo_receipt_partial_keeps_receipt(session):
    now = datetime.now(timezone.utc)
    r = _receipt(session, scanned_at=now)
    a = _ritem(session, r.id, "A")
    eaten = _ritem(session, r.id, "B", status="eaten")
    from app.pantry_service import undo_receipt
    result = undo_receipt(session, user_id=1, receipt_id=r.id, now=now)
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
    from app.pantry_service import undo_receipt
    result = undo_receipt(session, user_id=1, receipt_id=r.id, now=now)
    assert result.expired is True
    assert result.removed_ids == []


def test_undo_add_single_item(session):
    now = datetime.now(timezone.utc)
    item = _ritem(session, None, "Solo", created_at=now)
    from app.pantry_service import undo_add
    result = undo_add(session, user_id=1, item_id=item.id, now=now)
    assert result.removed_ids == [item.id]
    assert result.receipt_deleted is False
    session.refresh(item)
    assert item.status == "removed"


def test_undo_add_skips_corrected(session):
    now = datetime.now(timezone.utc)
    item = _ritem(session, None, "Solo", source="user_correction", created_at=now)
    from app.pantry_service import undo_add
    result = undo_add(session, user_id=1, item_id=item.id, now=now)
    assert result.removed_ids == []
    assert result.skipped == [(item.id, "corrected")]


def test_undo_keyboards():
    assert build_undo_keyboard(receipt_id=12)[0][0].callback_data == "undo:receipt:12"
    assert build_undo_add_keyboard(item_id=7)[0][0].callback_data == "undo:add:7"


def test_render_undo_result_full_and_partial():
    full = render_undo_result(UndoResult([1, 2], [], receipt_deleted=True, expired=False))
    assert "Undone" in full and "2" in full
    partial = render_undo_result(
        UndoResult([1], [(3, "eaten")], receipt_deleted=False, expired=False)
    )
    assert "skipped #3 (eaten)" in partial
    expired = render_undo_result(UndoResult([], [], False, expired=True))
    assert "expired" in expired.lower()
