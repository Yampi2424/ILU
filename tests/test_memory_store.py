import json

from memory.store import MemoryStore


def clear_database_env(monkeypatch):
    """Fuerza la rama JSON: sin URL de base de datos."""
    monkeypatch.delenv("DATABASE_URL_POOLED", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_memory_json_save_and_get(monkeypatch, tmp_path):
    clear_database_env(monkeypatch)

    store = MemoryStore(path=str(tmp_path / "data.json"))

    assert store.database_url is None

    store.save("clave", "contenido de prueba")

    assert store.get("clave") == "contenido de prueba"
    assert store.get("no-existe") is None
    assert store.get("no-existe", "default") == "default"


def test_memory_writes_to_disk(monkeypatch, tmp_path):
    clear_database_env(monkeypatch)

    path = tmp_path / "data.json"
    store = MemoryStore(path=str(path))

    store.save("clave", "contenido")

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["clave"]["content"] == "contenido"


def test_memory_search(monkeypatch, tmp_path):
    clear_database_env(monkeypatch)

    store = MemoryStore(path=str(tmp_path / "data.json"))

    store.save("gustos", "a la familia le gusta el té", memory_type="personal")
    store.save("color", "el color favorito es azul", memory_type="personal")

    results = store.search("té")

    assert any("té" in item["content"] for item in results)


def test_memory_search_empty_query(monkeypatch, tmp_path):
    clear_database_env(monkeypatch)

    store = MemoryStore(path=str(tmp_path / "data.json"))

    assert store.search("") == []
    assert store.search("   ") == []


def test_memory_load_empty_file(monkeypatch, tmp_path):
    clear_database_env(monkeypatch)

    store = MemoryStore(path=str(tmp_path / "no-existe.json"))

    assert store.load_all() == {}