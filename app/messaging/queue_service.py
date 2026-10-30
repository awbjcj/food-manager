"""Durable admission and explicit handling of ambiguous external outcomes."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.messaging.contracts import (
    DeliveryRequest,
    ErrorCode,
    IncomingEvent,
    MessagingError,
)
from app.models import MessagingInbox, MessagingOutbox, User
from app.pending_service import utc_naive


def admit_event(session: Session, event: IncomingEvent) -> bool:
    row = MessagingInbox(
        connection_id=event.actor.connection_id,
        event_key=event.event_key,
        event_json=event.model_dump_json(),
        created_at=utc_naive(event.received_at),
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        duplicate = session.exec(
            select(MessagingInbox).where(
                MessagingInbox.connection_id == event.actor.connection_id,
                MessagingInbox.event_key == event.event_key,
            )
        ).first()
        if duplicate is None:
            raise
        return False
    return True


def enqueue(
    session: Session, request: DeliveryRequest, *, user: User | None, now: datetime
) -> MessagingOutbox:
    existing = session.exec(
        select(MessagingOutbox).where(
            MessagingOutbox.delivery_key == request.delivery_key
        )
    ).first()
    if existing:
        if existing.request_json != request.model_dump_json():
            raise MessagingError(
                ErrorCode.CONFLICT, "Delivery key was reused with different content"
            )
        return existing
    row = MessagingOutbox(
        delivery_key=request.delivery_key,
        connection_id=request.conversation.connection_id,
        user_id=user.id if user else None,
        household_id=user.household_id if user else None,
        membership_generation=user.membership_generation if user else 0,
        request_json=request.model_dump_json(),
        created_at=utc_naive(now),
    )
    session.add(row)
    session.flush()
    return row


def recover_interrupted(session: Session, *, now: datetime) -> None:
    # A process restart cannot know whether an in-flight paid provider call or
    # send was accepted. Do not automatically repeat these operations.
    for row in session.exec(
        select(MessagingInbox).where(MessagingInbox.status == "processing")
    ).all():
        row.status, row.error_code = "unknown", "DELIVERY_UNKNOWN"
        session.add(row)
    for row in session.exec(
        select(MessagingOutbox).where(MessagingOutbox.status == "sending")
    ).all():
        row.status, row.error_code = "unknown", "DELIVERY_UNKNOWN"
        session.add(row)
    # Retain deduplication keys while removing old user content.
    for row in session.exec(
        select(MessagingInbox).where(
            MessagingInbox.created_at < utc_naive(now) - timedelta(days=30),
            col(MessagingInbox.status).in_(["complete", "failed", "unknown"]),
        )
    ).all():
        row.event_json = "{}"
        session.add(row)
