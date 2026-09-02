"""
Proveniencia de los recuerdos: source y device_id se guardan y sobreviven
al persistir. Base para la futura sincronización entre ubicaciones.
"""

from memory.router import MemoryRouter
from memory.backends import JsonBackend, InMemoryBackend


def test_remember_stores_source_and_device_id(tmp_path):
    router = MemoryRouter(
        backend=JsonBackend(path=str(tmp_path / "m.json"))
    )

    record = router.remember(
        "aprendido de una conversación",
        memory_type="semantic",
        source="conversation",
        device_id="casa",
    )

    assert record.source == "conversation"
    assert record.device_id == "casa"


def test_source_defaults_to_user(tmp_path):
    router = MemoryRouter(
        backend=JsonBackend(path=str(tmp_path / "m.json"))
    )

    record = router.remember("dato directo")

    assert record.source == "user"


def test_source_and_device_id_survive_persistence(tmp_path):
    path = str(tmp_path / "m.json")
    router = MemoryRouter(backend=JsonBackend(path=path))

    record = router.remember(
        "recuerdo persistente",
        source="tool",
        device_id="trabajo",
    )

    # Releer con un router nuevo (mismo archivo).
    fresh = MemoryRouter(backend=JsonBackend(path=path))
    loaded = fresh.get(record.key)

    assert loaded.source == "tool"
    assert loaded.device_id == "trabajo"


def test_update_can_change_source(tmp_path):
    router = MemoryRouter(backend=InMemoryBackend())

    record = router.remember("dato", source="user")
    updated = router.update(record.key, content="dato v2", source="learning")

    assert updated.source == "learning"
