"""
Bloque 8 — Solicitudes de autorización.

Cuando I.L.U. necesita un permiso que no posee, abre una solicitud
reversible y auditable; la tarea que la espera queda pausada.
"""

from security.authorization_request import (
    AuthorizationRequest,
    AuthorizationRequestStore,
    AuthorizationRequired,
    request_id,
)


def test_open_creates_pending_request(tmp_path):
    store = AuthorizationRequestStore(
        path=str(tmp_path / "requests.jsonl")
    )

    request = store.open(
        capability="write_file",
        reason="necesito escribir",
        principal="owner",
        task_id="tarea_1",
    )

    assert request.status == "open"
    assert request.capability == "write_file"
    assert request.task_id == "tarea_1"
    assert request.key.startswith("req_")
    assert len(store.pending()) == 1


def test_resolve_granted(tmp_path):
    store = AuthorizationRequestStore(
        path=str(tmp_path / "requests.jsonl")
    )

    request = store.open(capability="write_file")
    store.resolve(request.key, "granted", "owner", grant_id="gr_x")

    reloaded = store.get(request.key)
    assert reloaded.status == "granted"
    assert reloaded.grant_id == "gr_x"
    assert reloaded.resolved_by == "owner"
    assert store.pending() == []


def test_resolve_denied(tmp_path):
    store = AuthorizationRequestStore(
        path=str(tmp_path / "requests.jsonl")
    )

    request = store.open(capability="write_file")
    store.resolve(request.key, "denied", "owner")

    assert store.get(request.key).status == "denied"
    assert store.get(request.key).grant_id is None


def test_resolution_is_one_shot(tmp_path):
    store = AuthorizationRequestStore(
        path=str(tmp_path / "requests.jsonl")
    )

    request = store.open(capability="write_file")

    assert store.resolve(request.key, "granted", "owner") is not None
    # Segunda resolución: ya no está abierta -> None.
    assert store.resolve(request.key, "denied", "owner") is None


def test_invalid_status_rejected(tmp_path):
    store = AuthorizationRequestStore(
        path=str(tmp_path / "requests.jsonl")
    )

    request = store.open(capability="write_file")

    should_fail = request.resolve("cancelado", "owner")
    assert should_fail is False
    assert request.status == "open"


def test_persistence_roundtrip(tmp_path):
    path = str(tmp_path / "requests.jsonl")

    store = AuthorizationRequestStore(path=path)
    request = store.open(
        capability="write_file",
        reason="motivo",
        task_id="t",
    )
    store.resolve(request.key, "granted", "owner", grant_id="gr_x")

    reloaded = AuthorizationRequestStore(path=path)
    restored = reloaded.get(request.key)

    assert restored.status == "granted"
    assert restored.reason == "motivo"
    assert restored.grant_id == "gr_x"


def test_list_sorted_newest_first(tmp_path):
    store = AuthorizationRequestStore(
        path=str(tmp_path / "requests.jsonl")
    )

    first = store.open(capability="a")
    second = store.open(capability="b")

    items = store.list()

    assert items[0]["request_id"] == second.key
    assert items[1]["request_id"] == first.key


def test_request_store_path_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ILU_AUTHREQ_PATH", str(tmp_path / "custom.jsonl"))

    store = AuthorizationRequestStore()

    assert str(tmp_path / "custom.jsonl") == store.path


def test_authorization_required_carries_context():
    error = AuthorizationRequired(
        capability="write_file",
        reason="para el informe",
        task_id="tarea_3",
        scope={"type": "tool"},
    )

    assert error.capability == "write_file"
    assert error.reason == "para el informe"
    assert error.task_id == "tarea_3"
    assert error.scope == {"type": "tool"}


def test_request_id_generator_unique():
    a = request_id()
    b = request_id()

    assert a != b
    assert a.startswith("req_")