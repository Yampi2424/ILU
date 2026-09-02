"""
Bloque 8 — Tareas que esperan autorización (pause/resume).

Si dentro de una tarea en segundo plano se lanza AuthorizationRequired,
la tarea queda PAUSADA con waiting_authorization y se abre una solicitud
en AuthorizationRequestStore (otras tareas independientes siguen).
"""

import threading

import app.__main__ as main
from security.authorization_request import AuthorizationRequired


def reset_app_state(tmp_path):
    """Apuntar el singleton del servidor a tmp y limpiar estado."""
    main.task_manager.path = str(tmp_path / "tasks.json")
    main.task_manager.tasks = {}
    main.task_manager._load()

    main.core.auth_requests.path = str(tmp_path / "requests.jsonl")
    main.core.auth_requests.requests = {}
    main.core.auth_requests._load()


def test_wait_for_authorization_helper(tmp_path):
    from tasks import TaskManager

    manager = TaskManager(path=str(tmp_path / "tasks.json"))
    task = manager.create(title="tarea")

    updated = manager.wait_for_authorization(task["id"], "req_abc")

    assert updated["state"] == "paused"
    assert updated["waiting_authorization"] == "req_abc"


def test_pause_on_authorization_required(tmp_path):
    reset_app_state(tmp_path)

    def _tarea_que_necesita_permiso():
        raise AuthorizationRequired(
            capability="write_file",
            reason="escribir el informe",
        )

    task = main.task_manager.create(
        title="tarea con autorización",
        max_retries=0,
    )

    main._run_task(task["id"], _tarea_que_necesita_permiso)

    stored = main.task_manager.get(task["id"])

    assert stored["state"] == "paused"
    assert stored["waiting_authorization"] is not None

    # La solicitud se abrió con la capacidad necesaria y vinculada a la
    # tarea pausada.
    pending = main.core.auth_requests.pending()

    assert len(pending) == 1
    assert pending[0]["capability"] == "write_file"
    assert pending[0]["task_id"] == task["id"]
    assert pending[0]["request_id"] == stored["waiting_authorization"]


def test_no_retries_after_pause(tmp_path):
    reset_app_state(tmp_path)

    def _falla():
        raise AuthorizationRequired(capability="notify")

    task = main.task_manager.create(
        title="tarea",
        max_retries=3,  # aunque hubiera reintentos, se PAUSA.
    )

    main._run_task(task["id"], _falla)

    assert main.task_manager.get(task["id"])["state"] == "paused"
    assert main.task_manager.get(task["id"])["retries"] == 0


def test_task_success_untouched(tmp_path):
    reset_app_state(tmp_path)

    def _ok():
        return {"done": True}

    task = main.task_manager.create(title="tarea", max_retries=0)

    main._run_task(task["id"], _ok)

    assert main.task_manager.get(task["id"])["state"] == "completed"
    assert main.task_manager.get(task["id"])["result"] == {"done": True}
    assert main.task_manager.get(task["id"]).get(
        "waiting_authorization"
    ) is None