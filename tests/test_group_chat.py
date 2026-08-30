from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app import handler_support
from app.group_service import GroupBindingConflict, bind_group
from app.handlers.household import handle_bind, handle_invite, handle_join
from app.handlers.meta import handle_photo
from app.models import GroupBinding, Household, User


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        first = Household(created_at=datetime.now(UTC))
        second = Household(created_at=datetime.now(UTC))
        db.add(first)
        db.add(second)
        db.commit()
        db.refresh(first)
        db.refresh(second)
        assert first.id is not None
        assert second.id is not None
        db.add(
            User(
                telegram_id=1,
                chat_id=1,
                household_id=first.id,
                created_at=datetime.now(UTC),
            )
        )
        db.add(
            User(
                telegram_id=2,
                chat_id=2,
                household_id=second.id,
                created_at=datetime.now(UTC),
            )
        )
        db.commit()
        yield db


def _factory(session: Session):
    return lambda: session


def _msg(text: str, *, user_id: int = 1, chat_id: int = -100, chat_type="group"):
    msg = MagicMock()
    msg.text = text
    msg.from_user = MagicMock(id=user_id)
    msg.chat = MagicMock(id=chat_id, type=chat_type)
    msg.answer = AsyncMock()
    return msg


def test_binding_is_idempotent_and_cannot_be_hijacked(session):
    user = session.get(User, 1)
    other = session.get(User, 2)
    assert user is not None
    assert other is not None

    first = bind_group(
        session,
        chat_id=-100,
        household_id=user.household_id,
        bound_by_user_id=user.telegram_id,
        created_at=datetime.now(UTC),
    )
    repeated = bind_group(
        session,
        chat_id=-100,
        household_id=user.household_id,
        bound_by_user_id=user.telegram_id,
        created_at=datetime.now(UTC),
    )
    assert first.created is True
    assert repeated.created is False

    with pytest.raises(GroupBindingConflict):
        bind_group(
            session,
            chat_id=-100,
            household_id=other.household_id,
            bound_by_user_id=other.telegram_id,
            created_at=datetime.now(UTC),
        )


def test_group_authorization_requires_binding_and_matching_membership(session):
    unbound = handler_support.authorize_and_get_user(
        session,
        allowed_user_id=1,
        telegram_user_id=1,
        chat_id=-100,
        chat_type="group",
    )
    assert unbound.allowed is False
    assert "/bind" in unbound.reason

    user = session.get(User, 1)
    assert user is not None
    bind_group(
        session,
        chat_id=-100,
        household_id=user.household_id,
        bound_by_user_id=user.telegram_id,
        created_at=datetime.now(UTC),
    )
    allowed = handler_support.authorize_and_get_user(
        session,
        allowed_user_id=1,
        telegram_user_id=1,
        chat_id=-100,
        chat_type="supergroup",
    )
    rejected = handler_support.authorize_and_get_user(
        session,
        allowed_user_id=1,
        telegram_user_id=2,
        chat_id=-100,
        chat_type="group",
    )
    newcomer = handler_support.authorize_and_get_user(
        session,
        allowed_user_id=1,
        telegram_user_id=99,
        chat_id=-100,
        chat_type="group",
        open_registration=True,
    )
    assert allowed.allowed is True
    assert rejected.allowed is False
    assert newcomer.allowed is False
    assert session.get(User, 99) is None


def test_group_callback_uses_the_same_household_boundary(session, monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    user = session.get(User, 1)
    assert user is not None
    bind_group(
        session,
        chat_id=-100,
        household_id=user.household_id,
        bound_by_user_id=user.telegram_id,
        created_at=datetime.now(UTC),
    )
    cb = MagicMock()
    cb.from_user = MagicMock(id=1)
    cb.message.chat = MagicMock(id=-100, type="group")
    assert handler_support.authorized_callback_query_user(session, cb) == user
    cb.from_user = MagicMock(id=2)
    assert handler_support.authorized_callback_query_user(session, cb) is None


@pytest.mark.asyncio
async def test_bind_handler_connects_group_for_existing_member(session, monkeypatch):
    monkeypatch.setattr(handler_support, "ALLOWED_TELEGRAM_USER_ID", 1)
    msg = _msg("/bind")
    await handle_bind(msg, session_factory=_factory(session))
    binding = session.get(GroupBinding, -100)
    assert binding is not None
    assert binding.bound_by_user_id == 1
    assert "bound" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_invite_and_join_are_private_only(session):
    invite = _msg("/invite")
    join = _msg("/join code")
    await handle_invite(
        invite,
        session_factory=_factory(session),
        bot=MagicMock(),
    )
    await handle_join(join, session_factory=_factory(session))
    assert "private chat" in invite.answer.await_args.args[0]
    assert "private chat" in join.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_group_receipt_photo_is_rejected_before_download(session):
    msg = _msg("", chat_type="group")
    downloader = AsyncMock()
    await handle_photo(
        msg,
        session_factory=_factory(session),
        now_provider=MagicMock(),
        clients=MagicMock(),
        photo_downloader=downloader,
    )
    downloader.assert_not_awaited()
    assert "private chat" in msg.answer.await_args.args[0]
