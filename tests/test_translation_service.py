from sqlmodel import Session, SQLModel, create_engine
from app.models import NameTranslation


def _session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_name_translation_roundtrip():
    with _session() as s:
        s.add(NameTranslation(lang="zh", source_text="Whole Milk", translated_text="全脂牛奶"))
        s.commit()
        row = s.get(NameTranslation, ("zh", "Whole Milk"))
        assert row is not None
        assert row.translated_text == "全脂牛奶"
