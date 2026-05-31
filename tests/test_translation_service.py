from sqlmodel import Session, SQLModel, create_engine
from app.models import NameTranslation
from app.translation_service import translate_texts
from tests.fakes import FakeTranslationLLM


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


async def test_fake_translation_llm_returns_mapping_in_order():
    fake = FakeTranslationLLM(table={"Milk": "牛奶", "Eggs": "鸡蛋"})
    out, cost = await fake.translate(texts=["Milk", "Eggs"], lang="zh")
    assert out == ["牛奶", "鸡蛋"]
    assert fake.calls == [(("Milk", "Eggs"), "zh")]


async def test_en_is_identity_no_llm_no_db():
    fake = FakeTranslationLLM(table={"Milk": "X"})
    with _session() as s:
        out = await translate_texts(s, ["Milk", "Eggs"], lang="en", llm=fake)
    assert out == {"Milk": "Milk", "Eggs": "Eggs"}
    assert fake.calls == []


async def test_translates_misses_and_caches():
    fake = FakeTranslationLLM(table={"Milk": "牛奶", "Eggs": "鸡蛋"})
    with _session() as s:
        out = await translate_texts(s, ["Milk", "Eggs", "Milk"], lang="zh", llm=fake)
        assert out == {"Milk": "牛奶", "Eggs": "鸡蛋"}
        # deduped: only the two unique texts, in one batched call
        assert fake.calls == [(("Milk", "Eggs"), "zh")]
        assert s.get(NameTranslation, ("zh", "Milk")).translated_text == "牛奶"


async def test_cache_hit_skips_llm():
    with _session() as s:
        s.add(NameTranslation(lang="zh", source_text="Milk", translated_text="牛奶"))
        s.commit()
        fake = FakeTranslationLLM(table={})
        out = await translate_texts(s, ["Milk"], lang="zh", llm=fake)
        assert out == {"Milk": "牛奶"}
        assert fake.calls == []


async def test_partial_cache_only_sends_misses():
    with _session() as s:
        s.add(NameTranslation(lang="zh", source_text="Milk", translated_text="牛奶"))
        s.commit()
        fake = FakeTranslationLLM(table={"Eggs": "鸡蛋"})
        out = await translate_texts(s, ["Milk", "Eggs"], lang="zh", llm=fake)
        assert out == {"Milk": "牛奶", "Eggs": "鸡蛋"}
        assert fake.calls == [(("Eggs",), "zh")]  # only the miss


async def test_failure_falls_back_to_english_without_caching():
    fake = FakeTranslationLLM(table={"Milk": "牛奶"}, raise_n_times=1)
    with _session() as s:
        out = await translate_texts(s, ["Milk"], lang="zh", llm=fake)
        assert out == {"Milk": "Milk"}                       # English fallback
        assert s.get(NameTranslation, ("zh", "Milk")) is None  # nothing cached


async def test_empty_input_returns_empty():
    fake = FakeTranslationLLM(table={})
    with _session() as s:
        out = await translate_texts(s, [], lang="zh", llm=fake)
    assert out == {}
    assert fake.calls == []
