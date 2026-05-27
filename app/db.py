from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, create_engine


def make_engine(database_path: str) -> Engine:
    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        f"sqlite:///{database_path}",
        echo=False,
        connect_args={"check_same_thread": False},
    )


def make_session_factory(engine: Engine):
    def factory() -> Session:
        return Session(engine)

    return factory
