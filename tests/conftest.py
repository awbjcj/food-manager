"""Shared test infrastructure; database contents remain isolated per test."""

from pathlib import Path
from shutil import copyfile

import pytest
from sqlmodel import SQLModel, create_engine

import app.models  # noqa: F401 - register every table even for a focused test run


@pytest.fixture(scope="session")
def _empty_sqlite_schema(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build only the schema once per process, never sharing seeded data."""
    path = tmp_path_factory.mktemp("sqlite-schema") / "empty.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        SQLModel.metadata.create_all(engine)
    finally:
        # Close all connections before copying, including on Windows. This
        # template uses SQLite's default rollback journal, not a WAL sidecar.
        engine.dispose()
    return path


@pytest.fixture
def sqlite_db_path(_empty_sqlite_schema: Path, tmp_path: Path) -> Path:
    """A fresh on-disk database with real commits and independent connections.

    Copying a closed, empty schema avoids repeating dozens of CREATE TABLE /
    CREATE INDEX disk writes per test. Each test still owns a separate file,
    and its usual engine factory controls connection pragmas and pooling.
    Migration tests must start with an empty path instead of using this fixture.
    """
    path = tmp_path / "test.db"
    copyfile(_empty_sqlite_schema, path)
    return path
