import os
import json
import time
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import ILUCore
from config.settings import ILUSettings
from config.identity import ILU_IDENTITY
from security.authorization_request import AuthorizationRequired


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

        except AuthorizationRequired as auth_error:
            # La tarea necesita un permiso que no se posee: se PAUSA y se
            # abre una solicitud de autorización. Otras tareas
            # independientes siguen avanzando.
            request = core.auth_requests.open(
                capability=auth_error.capability,
                reason=(
                    auth_error.reason
                    or "La tarea necesita autorización para ejecutarse"
                ),
                principal=core.settings.owner_id,
                task_id=task_id,
                scope=auth_error.scope or {},
            )

            task_manager.wait_for_authorization(
                task_id,
                request.key
            )

            core.audit.record(
                actor="ilu",
                action="task_paused_authorization",
                task_id=task_id,
                request_id=request.key,
                capability=auth_error.capability,
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

        elif self._path() == "/grants":
            # Permisos emitidos (consulta; concesión vía POST).
            grants = core.grant_store.list(
                capability=query.get("capability"),
                status=query.get("status"),
            )

            self.send_json(200, {
                "grants": [
                    grant.to_dict() for grant in grants
                ],
                "count": len(grants),
                "policy_version": core.policy.data.get("version"),
            })

        elif self._path() == "/policy":
            # Reglas humanamente auditable (separadas del código).
            self.send_json(200, {
                "policy": core.policy.data,
                "path": core.policy.path,
            })

        elif self._path() == "/authorization-requests":
            requests = core.auth_requests.list(limit=200)

            self.send_json(200, {
                "requests": requests,
                "count": len(requests),
            })

        elif self._path() == "/security":
            # Estado de seguridad: autonomía, owner, grants activos,
            # dispositivos autorizados y protocolos de emergencia.
            self.send_json(200, {
                "autonomy": core.security.autonomy_level,
                "owner": core.settings.owner_id,
                "principals": len(core.principals.list()),
                "grants_active": len(
                    core.grant_store.list(status="active")
                ),
                "authorization_requests_open": len(
                    [r for r in core.auth_requests.pending()]
                ),
                "devices": core.devices.list(),
                "emergency_active": core.emergency.list_active(),
            })

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

        if self._path() == "/grants":
            # Concesión de permiso (solo un principal raíz).
            self._grant()
            return

        if (
            len(segments) == 2
            and segments[0] == "authorization-requests"
        ):
            # Resolver (conceder/denegar) una solicitud de autorización.
            self._resolve_authorization_request(segments[1])
            return

        if self._path() == "/autonomy":
            # Cambiar nivel de autonomía (solo un principal raíz).
            self._change_autonomy()
            return

        self.send_json(404, {
            "error": "not_found"
        })

    def _grant(self):
        """Concede un permiso. Authority valida que el actor sea raíz."""
        try:
            data = self._read_json()

            actor = data.get("actor")
            capability = data.get("capability")

            if not actor or not capability:
                self.send_json(400, {
                    "success": False,
                    "error": "capability_and_actor_required"
                })
                return

            grant = core.authority.grant(
                capability=capability,
                actor=actor,
                reason=data.get("reason", ""),
                level=data.get("level", "execution"),
                scope_type=data.get("scope_type", "single_action"),
                task_id=data.get("task_id"),
                project=data.get("project"),
                context=data.get("context"),
                device_id=data.get("device_id"),
                max_uses=data.get("max_uses"),
                duration=data.get("duration"),
            )

            self.send_json(200, {
                "success": True,
                "grant": grant.to_dict()
            })

        except PermissionError as error:
            self.send_json(403, {
                "success": False,
                "error": str(error)
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

    def _resolve_authorization_request(self, request_id):
        """Concede o deniega una solicitud abierta (solo un raíz)."""
        try:
            data = self._read_json()

            actor = data.get("actor")
            decision = data.get("decision")

            if not actor or decision not in ("granted", "denied"):
                self.send_json(400, {
                    "success": False,
                    "error": "actor_and_decision_required"
                })
                return

            result = core.authority.resolve_request(
                request_id,
                decision,
                actor,
                scope=data.get("scope"),
                duration=data.get("duration"),
                reason=data.get("reason", ""),
            )

            if not result["success"]:
                self.send_json(404, {
                    "success": False,
                    "error": result["error"]
                })
                return

            grant = result.get("grant")

            self.send_json(200, {
                "success": True,
                "request_id": request_id,
                "decision": decision,
                "grant": grant.to_dict() if grant else None
            })

        except PermissionError as error:
            self.send_json(403, {
                "success": False,
                "error": str(error)
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

    def _change_autonomy(self):
        """Cambia el nivel de autonomía (solo un principal raíz)."""
        try:
            data = self._read_json()

            actor = data.get("actor")
            level = data.get("level")

            if (
                not actor
                or level not in core.security.AUTONOMY_LEVELS
            ):
                self.send_json(400, {
                    "success": False,
                    "error": "actor_and_valid_level_required"
                })
                return

            change = core.authority.set_autonomy(
                level,
                actor=actor
            )

            self.send_json(200, {
                "success": True,
                "autonomy": change
            })

        except PermissionError as error:
            self.send_json(403, {
                "success": False,
                "error": str(error)
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
