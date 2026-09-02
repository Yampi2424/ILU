import pytest

from memory.backends import (
    JsonBackend,
    InMemoryBackend,
    create_backend,
)
from memory.router import MemoryRouter


def test_default_backend_is_json_local(monkeypatch, tmp_path):
    # Por defecto, I.L.U. usa memoria local y funciona sin Internet.
    monkeypatch.delenv("ILU_MEMORY_BACKEND", raising=False)
    monkeypatch.setenv("ILU_MEMORY_PATH", str(tmp_path / "data.json"))

    backend = create_backend()

    assert isinstance(backend, JsonBackend)
    assert backend.path.name == "data.json"


def test_create_backend_fails_loudly_if_postgres_without_url(monkeypatch):
    # Si se pide Postgres sin credenciales, falla de forma explícita
    # (la regla de I.L.U.: no ocultar el fallo).
    monkeypatch.setenv("ILU_MEMORY_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_POOLED", raising=False)

    with pytest.raises(RuntimeError):
        create_backend()


@pytest.mark.parametrize(
    "backend_factory",
    [
        lambda tmp_path: InMemoryBackend(),
        lambda tmp_path: JsonBackend(path=str(tmp_path / "memory.json")),
    ],
)
def test_complete_memory_lifecycle_works_on_any_backend(
    backend_factory,
    tmp_path,
):
    # Almacenar -> recuperar -> relacionar -> actualizar -> consultar
    # sobre cualquier almacén, con el MISMO MemoryRouter.
    router = MemoryRouter(backend=backend_factory(tmp_path))

    conocimiento = router.remember(
        "el servidor escucha en el puerto 8000",
        memory_type="knowledge",
        tags=["ilu", "infra"],
    )

    error = router.remember(
        "el puerto 8000 estaba ocupado una vez",
        memory_type="error",
    )

    router.link(conocimiento.key, error.key, relation="relacionado con")

    assert router.get(conocimiento.key).memory_type == "knowledge"
    assert len(router.related(conocimiento.key)) == 1

    updated = router.update(
        conocimiento.key,
        content="el servidor escucha en el puerto 8080",
    )
    assert "8080" in updated.content

    results = router.query("8080", types=["knowledge"])
    assert any(r.key == conocimiento.key for r in results)

    stats = router.stats()
    assert stats["total"] == 2

    router.forget("puerto 8000 ocupado")
    assert router.stats()["total"] == 1