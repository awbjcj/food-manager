from datetime import UTC, datetime
from types import UnionType
from typing import get_args

from sqlalchemy import DateTime
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Household, QuotaUsage, ShoppingList


def test_sqlite_timestamps_keep_naive_storage_contract():
    for mapper in SQLModel._sa_registry.mappers:
        for name, field in mapper.class_.model_fields.items():
            annotation = field.annotation
            if annotation is datetime or (
                isinstance(annotation, UnionType) and datetime in get_args(annotation)
            ):
                column = mapper.local_table.c[name]
                assert isinstance(column.type, DateTime), str(column)
                assert not column.type.timezone, str(column)


def test_existing_naive_timestamps_round_trip_and_filter():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    timestamp = datetime(2026, 10, 6, 12, tzinfo=UTC).replace(tzinfo=None)
    with Session(engine) as session:
        household = Household(created_at=timestamp)
        session.add(household)
        session.commit()
        session.refresh(household)
        assert household.id is not None
        session.add(QuotaUsage(household_id=household.id, period_start=timestamp))
        session.add(ShoppingList(
            household_id=household.id,
            name_raw="Apple",
            name_normalized="apple",
            added_at=timestamp,
        ))
        session.commit()
        session.expire_all()

        usage = session.get(QuotaUsage, (household.id, timestamp))
        assert usage is not None and usage.period_start == timestamp
        item = session.exec(select(ShoppingList).where(
            ShoppingList.added_at <= timestamp,
        )).one()
        assert item.added_at == timestamp and item.bought_at is None
        item.bought_at = timestamp
        session.commit()
        session.refresh(item)
        assert item.bought_at == timestamp
