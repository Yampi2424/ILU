import json

from memory.backends import (
    JsonBackend,
    InMemoryBackend,
    MemoryRecord,
)


def make_json(tmp_path):
    return JsonBackend(path=str(tmp_path / "memory.json"))


def test_json_save_and_get(tmp_path):
    backend = make_json(tmp_path)

    backend.save(MemoryRecord(key="k1", content="el té le gusta a la familia"))

    record = backend.get("k1")

    assert record is not None
    assert record.content == "el té le gusta a la familia"


def test_json_persists_across_instances(tmp_path):
    path = tmp_path / "memory.json"

    JsonBackend(path=str(path)).save(
        MemoryRecord(key="k1", content="persistente")
    )

    reloaded = JsonBackend(path=str(path)).get("k1")

    assert reloaded.content == "persistente"


def test_json_delete(tmp_path):
    backend = make_json(tmp_path)

    backend.save(MemoryRecord(key="k1", content="algo"))

    assert backend.delete("k1") is True
    assert backend.get("k1") is None
    assert backend.delete("k1") is False


def test_json_reads_legacy_memorystore_format(tmp_path):
    # Compatibilidad con el formato del antiguo MemoryStore:
    # {key: {"type": ..., "content": ..., "importance": ...}}.
    path = tmp_path / "memory.json"

    with path.open("w", encoding="utf-8") as file:
        json.dump({
            "legacy": {
                "type": "personal",
                "content": "a papá le gusta el mate",
                "importance": 8,
            }
        }, file, ensure_ascii=False)

    backend = make_json(tmp_path)
    record = backend.get("legacy")

    assert record.memory_type == "personal"
    assert record.content == "a papá le gusta el mate"
    assert record.importance == 8
    assert record.tags == []
    assert record.links == []


def test_json_preserves_tags_links_metadata(tmp_path):
    backend = make_json(tmp_path)

    backend.save(MemoryRecord(
        key="k1",
        content="python avanzado",
        memory_type="skill",
        tags=["programacion", "ilu"],
        links=[{"key": "k2", "relation": "requiere"}],
        metadata={"source": "test"},
    ))

    record = backend.get("k1")

    assert record.tags == ["programacion", "ilu"]
    assert record.links == [{"key": "k2", "relation": "requiere"}]
    assert record.metadata == {"source": "test"}


def test_json_search_ranks_by_relevance(tmp_path):
    backend = make_json(tmp_path)

    backend.save(MemoryRecord(
        key="exact",
        content="té negro con leche",
        memory_type="personal",
        importance=8,
    ))
    backend.save(MemoryRecord(
        key="far",
        content="otra cosa totalmente distinta",
        memory_type="conversation",
        importance=3,
    ))

    results = backend.search("té")

    assert results
    assert results[0].key == "exact"


def test_json_search_filters_by_type(tmp_path):
    backend = make_json(tmp_path)

    backend.save(MemoryRecord(key="k1", content="reparar la caldera", memory_type="task"))
    backend.save(MemoryRecord(key="k2", content="reparar la caldera", memory_type="experience"))

    skills = backend.search("reparar", types=["skill"])
    assert skills == []

    tasks = backend.search("reparar", types=["task"])
    assert len(tasks) == 1
    assert tasks[0].key == "k1"


def test_json_list_by_type_and_limit(tmp_path):
    backend = make_json(tmp_path)

    for i in range(5):
        backend.save(MemoryRecord(
            key=f"skill_{i}",
            content=f"habilidad {i}",
            memory_type="skill",
        ))

    skills = backend.list(types=["skill"], limit=3)

    assert len(skills) == 3


def test_all_returns_records(tmp_path):
    backend = make_json(tmp_path)

    backend.save(MemoryRecord(key="a", content="uno"))
    backend.save(MemoryRecord(key="b", content="dos"))

    records = backend.all()

    assert set(records.keys()) == {"a", "b"}
    assert all(isinstance(r, MemoryRecord) for r in records.values())


def test_in_memory_backend_works_same_way(tmp_path):
    backend = InMemoryBackend()

    backend.save(MemoryRecord(key="k1", content="hola mundo"))
    assert backend.get("k1").content == "hola mundo"

    results = backend.search("hola")
    assert results[0].key == "k1"

    assert backend.delete("k1") is True
    assert backend.get("k1") is None