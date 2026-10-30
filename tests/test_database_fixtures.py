"""Guard isolation and persistence when reusing the empty database schema."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect
from sqlmodel import Session, SQLModel, select

from app.db import make_engine
from app.models import Household


@pytest.mark.parametrize("_run", [1, 2])
def test_schema_copy_starts_empty_and_preserves_real_commits(sqlite_db_path, _run):
    # Repeating this test with the same primary key catches data leaking from
    # one test into the schema template or the next test's database.
    engine = make_engine(str(sqlite_db_path))
    try:
        assert set(inspect(engine).get_table_names()) == set(SQLModel.metadata.tables)
        with Session(engine) as session:
            assert session.exec(select(Household)).all() == []
            session.add(Household(id=1, created_at=datetime.now(UTC)))
            session.commit()
            session.add(Household(id=2, created_at=datetime.now(UTC)))
            session.flush()
            session.rollback()
    finally:
        engine.dispose()

    # A new engine must see committed data, but not the rolled-back row.
    reopened = make_engine(str(sqlite_db_path))
    try:
        with Session(reopened) as session:
            assert session.get(Household, 1) is not None
            assert session.get(Household, 2) is None
    finally:
        reopened.dispose()
