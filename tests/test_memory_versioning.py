"""
Versionado real de recuerdos: corregir/actualizar NO destruye el historial.
Se prueba sobre dos almacenes distintos para confirmar que no depende del JSON.
"""

import pytest

from memory.router import MemoryRouter
from memory.backends import JsonBackend, InMemoryBackend


@pytest.mark.parametrize(
    "backend_factory",
    [
        lambda tmp_path: InMemoryBackend(),
        lambda tmp_path: JsonBackend(path=str(tmp_path / "m.json")),
    ],
)
def test_update_versions_content_without_losing_history(
    backend_factory,
    tmp_path,
):
    router = MemoryRouter(backend=backend_factory(tmp_path))

    record = router.remember(
        "el cumpleaños de ana es en mayo",
        memory_type="personal",
    )

    updated = router.update(
        record.key,
        content="el cumpleaños de ana es en julio",
        why="corrección del usuario",
    )

    assert updated.version == 2
    assert len(updated.revisions) == 1

    revision = updated.revisions[0]
    assert revision["content"] == "el cumpleaños de ana es en mayo"
    assert revision["version"] == 1
    assert revision["why"] == "corrección del usuario"

    # El valor vigente es el nuevo; el historial conserva el anterior.
    assert router.get(record.key).content == "el cumpleaños de ana es en julio"
    assert router.get(record.key).revisions[0]["content"] == \
        "el cumpleaños de ana es en mayo"


@pytest.mark.parametrize(
    "backend_factory",
    [
        lambda tmp_path: InMemoryBackend(),
        lambda tmp_path: JsonBackend(path=str(tmp_path / "m.json")),
    ],
)
def test_update_with_same_content_does_not_add_revision(
    backend_factory,
    tmp_path,
):
    router = MemoryRouter(backend=backend_factory(tmp_path))

    record = router.remember("el color favorito es azul")

    updated = router.update(record.key, content="el color favorito es azul")

    assert updated.version == 1
    assert updated.revisions == []


def test_correct_versions_and_preserves_history(tmp_path):
    router = MemoryRouter(
        backend=JsonBackend(path=str(tmp_path / "m.json"))
    )

    router.remember("el puerto es 8000", memory_type="knowledge")

    updated = router.correct("puerto", "el puerto es 8080")

    assert updated is not None
    assert updated.version == 2
    assert "8080" in updated.content
    assert "8000" not in updated.content
    assert updated.revisions[0]["content"] == "el puerto es 8000"


def test_update_preserves_created_at(tmp_path):
    # Regresión: actualizar no debe resetear el momento de creación.
    router = MemoryRouter(
        backend=JsonBackend(path=str(tmp_path / "m.json"))
    )

    record = router.remember("dato estable", memory_type="knowledge")
    created = record.created_at

    updated = router.update(record.key, content="dato estable v2")

    assert updated.created_at == created
    assert router.get(record.key).created_at == created


def test_multiple_updates_keep_full_history(tmp_path):
    router = MemoryRouter(
        backend=JsonBackend(path=str(tmp_path / "m.json"))
    )

    record = router.remember("v1")
    record = router.update(record.key, content="v2")
    record = router.update(record.key, content="v3")

    assert record.version == 3
    contents = [revision["content"] for revision in record.revisions]
    assert contents == ["v1", "v2"]
