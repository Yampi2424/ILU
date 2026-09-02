"""
Ciclo de vida básico: la memoria de trabajo (volatile) no sobrevive a un
reinicio y la retención puede podar memorias temporales antiguas.
"""

from datetime import datetime, timedelta, timezone

from memory.router import MemoryRouter
from memory.backends import JsonBackend, InMemoryBackend


def _old_ts(days_ago):
    return (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).isoformat().replace("+00:00", "Z")


def _age_disk_records(backend, days_ago):
    """Envejece los registros YA persistidos (la retención lee del almacén)."""
    old = _old_ts(days_ago)

    data = backend._load()

    for key in data:
        data[key]["created_at"] = old
        data[key]["updated_at"] = old

    backend._write()


def test_working_memory_cleared_on_restart(tmp_path):
    # Primera "sesión": se guarda memoria de trabajo.
    path = str(tmp_path / "m.json")
    first = MemoryRouter(backend=JsonBackend(path=path))

    first.remember("contexto de la sesión", memory_type="working")
    first.remember("conocimiento permanente", memory_type="semantic")

    # "Reinicio": un router nuevo sobre el mismo archivo.
    second = MemoryRouter(backend=JsonBackend(path=path))

    working = second.list_by_type("working")
    semantic = second.list_by_type("semantic")

    assert working == []
    assert len(semantic) == 1


def test_working_memory_is_volatile_on_any_backend(tmp_path):
    router = MemoryRouter(backend=InMemoryBackend())

    router.remember("temp", memory_type="working")
    router.remember("perm", memory_type="semantic")

    # Al construir un router nuevo sobre el mismo almacén, lo volatile se va.
    fresh = MemoryRouter(backend=router.backend)

    assert fresh.list_by_type("working") == []
    assert len(fresh.list_by_type("semantic")) == 1


def test_prune_removes_old_temporal_keeps_permanent(tmp_path):
    backend = JsonBackend(path=str(tmp_path / "m.json"))
    router = MemoryRouter(backend=backend)

    router.remember("charla vieja", memory_type="conversation")
    router.remember("episodio viejo", memory_type="episodic")
    router.remember("conocimiento viejo", memory_type="semantic")

    _age_disk_records(backend, days_ago=90)

    removed = router.prune(days=30)

    assert removed == 2  # conversation + episodic
    assert router.list_by_type("conversation") == []
    assert router.list_by_type("episodic") == []
    # Lo permanente no se borra por retención.
    assert len(router.list_by_type("semantic")) == 1


def test_recent_temporal_is_not_pruned(tmp_path):
    router = MemoryRouter(
        backend=JsonBackend(path=str(tmp_path / "m.json"))
    )

    recent = router.remember("charla reciente", memory_type="conversation")

    removed = router.prune(days=30)

    assert removed == 0
    assert any(r.key == recent.key for r in router.list_by_type("conversation"))


def test_lifecycle_exposed_per_type(tmp_path):
    router = MemoryRouter(backend=InMemoryBackend())

    assert router.lifecycle("working") == "volatile"
    assert router.lifecycle("conversation") == "temporal"
    assert router.lifecycle("semantic") == "permanent"
