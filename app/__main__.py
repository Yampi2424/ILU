import os
import json
import time
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import ILUCore
from config.settings import ILUSettings
from config.identity import ILU_IDENTITY


core = ILUCore()
settings = ILUSettings()

# I.L.U. usa un único TaskManager compartido entre el core y el HTTP:
# el registro en memoria y en disco es el mismo para ambos.
task_manager = core.tasks


def _run_in_background(fn):
    """
    Lanza una función en un hilo en segundo plano y devuelve
    enseguida. Permite a I.L.U. mantener tareas largas ejecutándose
    mientras continúa conversando.
    """
    thread = threading.Thread(
        target=fn,
        daemon=True
    )
    thread.start()
    return thread


def _run_task(task_id, callable_fn, args=None, kwargs=None):
    """
    Ejecutor de tareas en segundo plano con reintentos.

    Toma el estado a 'running', ejecuta el callable, y registra el
    resultado o el error. Si falla y aún quedan reintentos
    (max_retries de la tarea), lo intenta de nuevo; al agotarlos,
    marca la tarea como fallida.
    """
    task = task_manager.get(task_id)
    max_retries = (
        int(task.get("max_retries", 0) or 0)
        if task else 0
    )

    task_manager.set_state(task_id, "running")

    while True:
        try:
            result = callable_fn(
                *(args or ()),
                **(kwargs or {})
            )

            task_manager.set_result(task_id, result)

            core.audit.record(
                actor="ilu",
                action="task_result",
                task_id=task_id,
                success=True
            )

            return

        except Exception as error:
            retries = task_manager.record_retry(task_id) or 0

            if retries >= max_retries:
                task_manager.set_error(task_id, str(error))

                core.audit.record(
                    actor="ilu",
                    action="task_result",
                    task_id=task_id,
                    success=False
                )

                return

            # Reintento: pequeño respiro antes del siguiente intento.
            time.sleep(0.1)


def _query_params(path):
    parsed = urllib.parse.urlsplit(path)

    return {
        key: values[0]
        for key, values in urllib.parse.parse_qs(
            parsed.query
        ).items()
    }


class ILUHandler(BaseHTTPRequestHandler):

    def send_json(self, status, data):
        body = json.dumps(
            data,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.end_headers()

        self.wfile.write(body)

    def _path(self):
        return urllib.parse.urlsplit(
            self.path
        ).path

    def _segments(self):
        return [
            segment for segment in self._path().split("/")
            if segment
        ]

    def _read_json(self):
        content_length = int(
            self.headers.get("Content-Length", "0")
        )

        raw_body = self.rfile.read(content_length)

        if not raw_body:
            return {}

        return json.loads(raw_body.decode("utf-8"))

    # ------------------------------------------------------------------
    # GET
    # ------------------------------------------------------------------

    def do_GET(self):
        segments = self._segments()
        query = _query_params(self.path)

        if self._path() == "/":
            self.send_json(200, {
                "name": "I.L.U.",
                "status": "online",
                "version": settings.version
            })

        elif self._path() == "/healthz":
            self.send_json(200, {
                "status": "ok"
            })

        elif self._path() == "/about":
            self.send_json(200, {
                "name": ILU_IDENTITY["name"],
                "description": ILU_IDENTITY["full_name"],
                "version": settings.version,
                "mode": "cloud-ready",
                "role": ILU_IDENTITY["role"],
                "owner": ILU_IDENTITY["owner"],
                "architecture": ILU_IDENTITY["architecture"],
                "capabilities": ILU_IDENTITY["capabilities"],
                "limits": ILU_IDENTITY["limits"],
                "autonomy": settings.autonomy_level,
                "tasks": task_manager.stats()
            })

        elif (
            len(segments) == 1
            and segments[0] == "tasks"
        ):
            state = query.get("state")

            tasks = task_manager.list_tasks(state=state)

            self.send_json(200, {
                "tasks": tasks,
                "count": len(tasks)
            })

        elif (
            len(segments) == 2
            and segments[0] == "tasks"
        ):
            task = task_manager.get(segments[1])

            if task is None:
                self.send_json(404, {
                    "error": "task_not_found"
                })
            else:
                self.send_json(200, task)

        else:
            self.send_json(404, {
                "error": "not_found"
            })

    # ------------------------------------------------------------------
    # POST
    # ------------------------------------------------------------------

    def do_POST(self):
        segments = self._segments()

        if self._path() == "/ask":
            self._handle_ask()
            return

        if (
            len(segments) == 1
            and segments[0] == "tasks"
        ):
            self._create_task()
            return

        self.send_json(404, {
            "error": "not_found"
        })

    def _handle_ask(self):
        try:
            data = self._read_json()
            message = data.get("message", "")

            result = core.process(message)

            status = 200 if result["success"] else 400

            self.send_json(status, result)

        except json.JSONDecodeError:
            self.send_json(400, {
                "success": False,
                "error": "invalid_json"
            })

        except Exception as error:
            self.send_json(500, {
                "success": False,
                "error": "internal_error",
                "detail": str(error)
            })

    def _create_task(self):
        try:
            data = self._read_json()

            title = data.get("title", "")
            description = data.get("description", "")
            priority = data.get("priority", 5)
            max_retries = data.get("max_retries")
            callable_key = data.get("callable")

            task = task_manager.create(
                title=title,
                description=description,
                priority=priority,
                max_retries=max_retries
            )

            core.audit.record(
                actor="ilu",
                action="task_create",
                task_id=task["id"],
                title=title
            )

            payload = {
                "success": True,
                "task": task,
                "message": (
                    f"Tarea '{title}' creada. "
                    f"ID: {task['id']}"
                )
            }

            # Si el cliente pide ejecutar una tarea registrada en
            # segundo plano, la lanzamos sin bloquear la respuesta.
            if callable_key in _REGISTERED_TASKS:
                fn = _REGISTERED_TASKS[callable_key]
                _run_in_background(
                    lambda: _run_task(task["id"], fn)
                )

                payload["message"] += " Ejecución en segundo plano iniciada."

            self.send_json(200, payload)

        except ValueError as error:
            self.send_json(400, {
                "success": False,
                "error": str(error)
            })

        except json.JSONDecodeError:
            self.send_json(400, {
                "success": False,
                "error": "invalid_json"
            })

    # ------------------------------------------------------------------
    # PUT
    # ------------------------------------------------------------------

    def do_PUT(self):
        segments = self._segments()

        if (
            len(segments) == 3
            and segments[0] == "tasks"
        ):
            self._update_task(segments[1], segments[2])
            return

        self.send_json(404, {
            "error": "not_found"
        })

    def _update_task(self, task_id, field):
        try:
            data = self._read_json()

            task = task_manager.get(task_id)

            if task is None:
                self.send_json(404, {
                    "error": "task_not_found"
                })
                return

            if field == "state":
                state = data.get("state")

                if state is None:
                    self.send_json(400, {
                        "success": False,
                        "error": "state_required"
                    })
                    return

                updated = task_manager.set_state(
                    task_id,
                    state
                )

                self.send_json(200, {
                    "success": True,
                    "task": updated
                })

            elif field == "progress":
                progress = data.get("progress")

                if progress is None:
                    self.send_json(400, {
                        "success": False,
                        "error": "progress_required"
                    })
                    return

                updated = task_manager.set_progress(
                    task_id,
                    progress
                )

                self.send_json(200, {
                    "success": True,
                    "task": updated
                })

            elif field == "result":
                result = data.get("result")

                updated = task_manager.set_result(
                    task_id,
                    result
                )

                self.send_json(200, {
                    "success": True,
                    "task": updated
                })

            else:
                self.send_json(404, {
                    "error": "field_not_found"
                })

        except ValueError as error:
            self.send_json(400, {
                "success": False,
                "error": str(error)
            })

        except json.JSONDecodeError:
            self.send_json(400, {
                "success": False,
                "error": "invalid_json"
            })


# Tareas registradas que I.L.U. puede lanzar en segundo plano.
# Un callable con su clave: el cliente la pasa como "callable".
_REGISTERED_TASKS = {}


def register_background_task(key, fn):
    _REGISTERED_TASKS[key] = fn


if __name__ == "__main__":
    port = int(
        os.environ.get("PORT", "8000")
    )

    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        ILUHandler
    )

    print(
        f"I.L.U. iniciado en el puerto {port} "
        f"(multi-hilo, {len(_REGISTERED_TASKS)} tareas registradas)"
    )

    server.serve_forever()
