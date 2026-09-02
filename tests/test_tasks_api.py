import json
import threading
import time
import urllib.request
import urllib.error

from app.audit import AuditLog

import app.__main__ as main


def start_server(tmp_path):
    """
    Levanta el servidor HTTP de I.L.U. en un puerto libre y en un
    hilo en segundo plano, con TaskManager y auditoría en tmp_path
    (nunca toca memory/ del repo).
    """
    main.task_manager.path = str(tmp_path / "tasks.json")
    main.task_manager._load()

    main.core.audit = AuditLog(
        path=str(tmp_path / "audit.jsonl")
    )

    # Puerto efímero: 0 pide un puerto libre al SO.
    server = main.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        main.ILUHandler
    )

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True
    )
    thread.start()

    port = server.server_address[1]

    return server, port


def request(port, method, path, body=None):
    url = f"http://127.0.0.1:{port}{path}"

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.loads(
                response.read().decode("utf-8")
            )
    except urllib.error.HTTPError as error:
        return error.code, json.loads(
            error.read().decode("utf-8")
        )


def test_create_and_list_tasks(tmp_path):
    server, port = start_server(tmp_path)

    try:
        status, created = request(
            port,
            "POST",
            "/tasks",
            {"title": "investigar omni", "priority": 2}
        )

        assert status == 200
        assert created["success"] is True
        task_id = created["task"]["id"]

        status, fetched = request(port, "GET", f"/tasks/{task_id}")
        assert status == 200
        assert fetched["title"] == "investigar omni"

        status, listing = request(port, "GET", "/tasks")
        assert status == 200
        assert listing["count"] == 1
        assert listing["tasks"][0]["id"] == task_id

    finally:
        server.shutdown()


def test_update_task_state(tmp_path):
    server, port = start_server(tmp_path)

    try:
        _, created = request(
            port,
            "POST",
            "/tasks",
            {"title": "tarea"}
        )
        task_id = created["task"]["id"]

        status, updated = request(
            port,
            "PUT",
            f"/tasks/{task_id}/state",
            {"state": "running"}
        )
        assert status == 200
        assert updated["task"]["state"] == "running"

        status, progressed = request(
            port,
            "PUT",
            f"/tasks/{task_id}/progress",
            {"progress": 50}
        )
        assert status == 200
        assert progressed["task"]["progress"] == 50

    finally:
        server.shutdown()


def test_task_not_found(tmp_path):
    server, port = start_server(tmp_path)

    try:
        status, data = request(port, "GET", "/tasks/inexistente")
        assert status == 404
        assert data["error"] == "task_not_found"

    finally:
        server.shutdown()


def test_background_task_executes_and_completes(tmp_path):
    # Registramos una tarea ejecutable en segundo plano.
    executed = {"value": None}

    def job():
        time.sleep(0.05)
        executed["value"] = "hecho"
        return {"ok": True}

    main.register_background_task("test_job", job)

    server, port = start_server(tmp_path)

    try:
        status, created = request(
            port,
            "POST",
            "/tasks",
            {
                "title": "tarea en segundo plano",
                "callable": "test_job"
            }
        )
        assert status == 200
        assert "segundo plano" in created["message"]

        task_id = created["task"]["id"]

        # La tarea corre en un hilo; esperamos a que termine.
        for _ in range(50):
            time.sleep(0.05)

            _, task = request(port, "GET", f"/tasks/{task_id}")

            if task["state"] == "completed":
                break

        assert executed["value"] == "hecho"
        assert task["state"] == "completed"
        assert task["result"] == {"ok": True}
        assert task["progress"] == 100

    finally:
        server.shutdown()
        main._REGISTERED_TASKS.pop("test_job", None)


def test_background_task_error_marks_failed(tmp_path):
    def bad_job():
        raise RuntimeError("falló la tarea")

    main.register_background_task("test_bad_job", bad_job)

    server, port = start_server(tmp_path)

    try:
        _, created = request(
            port,
            "POST",
            "/tasks",
            {
                "title": "tarea que falla",
                "callable": "test_bad_job",
                "max_retries": 0
            }
        )
        task_id = created["task"]["id"]

        for _ in range(50):
            time.sleep(0.05)

            _, task = request(port, "GET", f"/tasks/{task_id}")

            if task["state"] == "failed":
                break

        assert task["state"] == "failed"
        assert task["error"] == "falló la tarea"
        assert task["retries"] == 1

    finally:
        server.shutdown()
        main._REGISTERED_TASKS.pop("test_bad_job", None)


def test_background_task_retries_then_succeeds(tmp_path):
    attempts = {"count": 0}

    def flaky_job():
        attempts["count"] += 1

        if attempts["count"] < 3:
            raise RuntimeError("fallo temporal")

        return {"ok": True}

    main.register_background_task("test_flaky_job", flaky_job)

    server, port = start_server(tmp_path)

    try:
        _, created = request(
            port,
            "POST",
            "/tasks",
            {
                "title": "tarea con reintentos",
                "callable": "test_flaky_job",
                "max_retries": 5
            }
        )
        task_id = created["task"]["id"]

        for _ in range(50):
            time.sleep(0.05)

            _, task = request(port, "GET", f"/tasks/{task_id}")

            if task["state"] == "completed":
                break

        assert attempts["count"] == 3
        assert task["state"] == "completed"
        assert task["retries"] == 2
        assert task["result"] == {"ok": True}

    finally:
        server.shutdown()
        main._REGISTERED_TASKS.pop("test_flaky_job", None)


def test_ask_responds_while_background_task_runs(tmp_path):
    # Prueba de concurrencia: mientras una tarea larga corre en segundo
    # plano, I.L.U. sigue atendiendo /ask con normalidad.
    finished = {"value": None}

    def long_job():
        time.sleep(1.5)
        finished["value"] = "terminado"
        return {"ok": True}

    main.register_background_task("test_long_job", long_job)

    server, port = start_server(tmp_path)

    try:
        _, created = request(
            port,
            "POST",
            "/tasks",
            {
                "title": "tarea larga",
                "callable": "test_long_job"
            }
        )
        task_id = created["task"]["id"]

        # La tarea sigue corriendo; /ask "hola" debe responder ya.
        status, answer = request(
            port,
            "POST",
            "/ask",
            {"message": "hola"}
        )

        assert status == 200
        assert answer["success"] is True
        assert answer["intent"] == "greeting"

        for _ in range(50):
            time.sleep(0.05)

            _, task = request(port, "GET", f"/tasks/{task_id}")

            if task["state"] == "completed":
                break

        assert finished["value"] == "terminado"
        assert task["state"] == "completed"
        assert task["result"] == {"ok": True}

    finally:
        server.shutdown()
        main._REGISTERED_TASKS.pop("test_long_job", None)


def test_list_tasks_filters_by_state(tmp_path):
    server, port = start_server(tmp_path)

    try:
        _, created = request(
            port,
            "POST",
            "/tasks",
            {"title": "una"}
        )
        first = created["task"]["id"]

        request(
            port,
            "PUT",
            f"/tasks/{first}/state",
            {"state": "running"}
        )

        _, listing = request(port, "GET", "/tasks?state=running")

        assert listing["count"] == 1
        assert listing["tasks"][0]["id"] == first

        _, listing = request(port, "GET", "/tasks?state=failed")
        assert listing["count"] == 0

    finally:
        server.shutdown()
