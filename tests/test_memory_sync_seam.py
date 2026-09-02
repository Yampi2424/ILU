"""
Costura de sincronización: la interfaz queda preparada para el SyncEngine
futuro, sin implementarlo. Se verifican los primitivos no destructivos
(changes_since / apply_changes con guarda de versión).
"""

from memory.router import MemoryRouter
from memory.backends import JsonBackend


def test_changes_since_lists_modified_records(tmp_path):
    router = MemoryRouter(
        backend=JsonBackend(path=str(tmp_path / "m.json"))
    )

    router.remember("uno", memory_type="semantic")
    router.remember("dos", memory_type="semantic")

    changes = router.changes_since()

    assert len(changes) == 2
    assert all(record.updated_at for record in changes)


def test_apply_changes_only_accepts_newer_version(tmp_path):
    router = MemoryRouter(
        backend=JsonBackend(path=str(tmp_path / "m.json"))
    )

    router.remember("valor local v1", key="k1", memory_type="semantic")

    # Versión más nueva entra.
    applied = router.apply_changes([
        {"key": "k1", "content": "valor remoto v2", "version": 2,
         "memory_type": "semantic", "source": "sync"},
    ])

    assert applied == 1
    assert router.get("k1").content == "valor remoto v2"

    # Versión igual o menor no entra (no pisa lo local).
    applied = router.apply_changes([
        {"key": "k1", "content": "valor obsoleto", "version": 1,
         "memory_type": "semantic"},
    ])

    assert applied == 0
    assert router.get("k1").content == "valor remoto v2"


def test_apply_changes_creates_unknown_keys(tmp_path):
    router = MemoryRouter(
        backend=JsonBackend(path=str(tmp_path / "m.json"))
    )

    applied = router.apply_changes([
        {"key": "nuevo", "content": "recién llegado", "version": 1,
         "memory_type": "episodic", "device_id": "otra-casa"},
    ])

    assert applied == 1
    assert router.get("nuevo").content == "recién llegado"
    assert router.get("nuevo").memory_type == "episodic"
