import os
import json
import time
import mimetypes
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import ILUCore
from config.settings import ILUSettings
from config.identity import ILU_IDENTITY
from security.authorization_request import AuthorizationRequired
from .tts import TTSService, TTSUnavailable


core = ILUCore()
settings = ILUSettings()

# Servicio de síntesis de voz de I.L.U. (voz de la respuesta).
tts = TTSService()

# Directorio de archivos estáticos de la interfaz web (I.L.U. Presencia).
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")


# ----------------------------------------------------------------------
# Token de dispositivo para rutas administrativas
# ----------------------------------------------------------------------
#
# Las rutas que conceden permisos, cambian la autonomía o resuelven
# solicitudes de autorización son acciones de AUTORIDAD: solo el owner
# (y quienes poseen el token de este dispositivo) pueden invocarlas.
#
# El token se guarda en security/device.key (gitignored) y se genera en
# el primer arranque. Protege la interfaz HTTP cuando I.L.U. escucha en
# 0.0.0.0: un actor de la red no puede escalar a root sin el token.
# /ask y los archivos estáticos permanecen abiertos para el uso normal.


def _load_or_create_token(path):
    """Carga el token de dispositivo o lo crea si no existe."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            token = handle.read().strip()
        if token:
            return token
    except OSError:
        pass

    token = secrets.token_hex(32)

    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
    except OSError:
        pass

    return token


# Token de autoridad de este dispositivo (cargado una sola vez).
DEVICE_TOKEN = _load_or_create_token(settings.device_key_path)

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


def _call_with_timeout(fn, args, kwargs, timeout):
    """
    Ejecuta `fn` en un hilo daemon con un límite de tiempo (F-3).

    Devuelve:
      {"timeout": True}        si supera el tiempo límite
      {"result": r}            si termina a tiempo (r puede ser None)
    Lanza la excepción de `fn` si esta ocurre antes del timeout.

    No se puede matar un hilo en Python; el hilo huérfano se deja
    como daemon (no bloquea el cierre del proceso) y la tarea se
    declara fallida por timeout.
    """
    box = {}

    def runner():
        try:
            box["result"] = fn(*(args or ()), **(kwargs or {}))
        except Exception as exc:  # noqa: BLE001 - se propaga al caller
            box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        return {"timeout": True}

    if "error" in box:
        raise box["error"]

    return {"result": box.get("result")}


def _run_task(task_id, callable_fn, args=None, kwargs=None, timeout=None):
    """
    Ejecutor de tareas en segundo plano con reintentos.

    Toma el estado a 'running', ejecuta el callable, y registra el
    resultado o el error. Si falla y aún quedan reintentos
    (max_retries de la tarea), lo intenta de nuevo; al agotarlos,
    marca la tarea como fallida.

    timeout (segundos): límite por intento (F-3). Por defecto usa
    ILU_TASK_TIMEOUT (300). Evita que una tarea en segundo plano se
    quede colgada para siempre.
    """
    if timeout is None:
        try:
            timeout = float(os.environ.get("ILU_TASK_TIMEOUT", "300"))
        except ValueError:
            timeout = 300.0

    task = task_manager.get(task_id)
    max_retries = (
        int(task.get("max_retries", 0) or 0)
        if task else 0
    )

    task_manager.set_state(task_id, "running")

    while True:
        try:
            outcome = _call_with_timeout(
                callable_fn,
                args,
                kwargs,
                timeout,
            )

            if outcome.get("timeout"):
                raise TimeoutError(
                    f"La tarea superó el límite de {timeout}s."
                )

            result = outcome.get("result")

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


def _int_or(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_notifications(limit=20):
    """
    Lee las notificaciones locales de I.L.U. (archivo JSONL escrito por la
    tool `notify` y por el hilo de proactividad en vivo). Devuelve las
    `limit` más recientes, de más nueva a más antigua.
    """
    raw = os.environ.get(
        "ILU_NOTIFICATIONS_PATH",
        "memory/notifications.jsonl"
    )

    path = os.path.expanduser(raw)

    if not os.path.exists(path):
        return []

    entries = []

    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()

                if not line:
                    continue

                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if isinstance(entry, dict):
                    entries.append(entry)
    except OSError:
        return []

    entries.sort(key=lambda e: e.get("ts", ""), reverse=True)

    return entries[:limit]


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

    def _authorized(self):
        """
        True si el request demuestra posesión del token de dispositivo.

        Se acepta el token por:
          - cabecera 'Authorization: Bearer <token>'
          - cabecera 'X-ILU-Token: <token>'
          - query '?token=<token>'

        La comparación es en tiempo constante (secrets.compare_digest)
        para no filtrar el token por temporización.
        """
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            provided = header[7:].strip()
        else:
            provided = self.headers.get("X-ILU-Token", "")

        if not provided:
            provided = _query_params(self.path).get("token", "")

        if not provided or not DEVICE_TOKEN:
            return False

        return secrets.compare_digest(provided, DEVICE_TOKEN)

    def _send_file(self, relative_path):
        """
        Sirve un archivo estático desde app/web/.

        Devuelve True si el archivo existía y fue enviado;
        False si no se encontró (el caller decide la respuesta).
        """
        safe = os.path.normpath(relative_path)
        if safe.startswith(".."):
            return False

        file_path = os.path.join(WEB_DIR, safe)

        if not os.path.isfile(file_path):
            return False

        content_type, _ = mimetypes.guess_type(file_path)
        if content_type is None:
            content_type = "application/octet-stream"

        with open(file_path, "rb") as f:
            body = f.read()

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)
        return True

    # ------------------------------------------------------------------
    # GET
    # ------------------------------------------------------------------

    def do_GET(self):
        segments = self._segments()
        query = _query_params(self.path)

        # --- Archivos estáticos de la interfaz (I.L.U. Presencia) ---
        path = self._path()

        if path == "/":
            if self._send_file("index.html"):
                return

        if (
            len(segments) >= 2
            and segments[0] in ("css", "js", "assets")
            and self._send_file(path.lstrip("/"))
        ):
            return

        if path == "/":
            self.send_json(200, {
                "name": "I.L.U.",
                "status": "online",
                "version": settings.version
            })

        elif self._path() == "/healthz":
            self.send_json(200, {
                "status": "ok"
            })

        elif self._path() == "/tts":
            # Voz de I.L.U.: sintetiza el texto de la respuesta a audio.
            # Si el motor no está disponible (sin red / sin paquete),
            # devuelve 503 y el frontend cae al TTS del navegador.
            self._handle_tts()
            return

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
                "tasks": task_manager.stats(),
                "goals": core.planner.stats(),
                "learning": core.learning.profile().get("count", 0),
                "proactivity": core.proactivity.stats(),
                "perception": core.perception.list_capabilities(),
                "integrations": core.integrations.list_capabilities()
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

        elif (
            len(segments) == 2
            and segments[0] == "conversations"
        ):
            # Bloque 10: auditar/debug el historial de una sesión.
            session_id = segments[1]

            turns = core.conversations.recent(
                session_id,
                limit=int(
                    query.get("limit", "100")
                )
            )

            self.send_json(200, {
                "session_id": session_id,
                "count": len(turns),
                "turns": turns
            })

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

        elif self._path() == "/goals":
            # JARVIS Evolution: objetivos y planes de I.L.U.
            status = query.get("status")

            goals = core.planner.list(status=status)

            self.send_json(200, {
                "goals": goals,
                "count": len(goals),
                "stats": core.planner.stats(),
            })

        elif (
            len(segments) == 2
            and segments[0] == "goals"
        ):
            goal = core.planner.get(segments[1])

            if goal is None:
                self.send_json(404, {"error": "goal_not_found"})
            else:
                self.send_json(200, {
                    "goal": goal,
                    "progress": core.planner.progress(goal["id"]),
                })

        elif self._path() == "/profile":
            # JARVIS Evolution: perfil de aprendizaje/personalización.
            self.send_json(200, core.learning.profile())

        elif self._path() == "/proactivity":
            # JARVIS Evolution: reglas proactivas de I.L.U.
            kind = query.get("kind")
            enabled = query.get("enabled")

            if enabled is not None:
                enabled = enabled.lower() in ("1", "true", "yes")

            rules = core.proactivity.list(kind=kind, enabled=enabled)

            self.send_json(200, {
                "rules": rules,
                "count": len(rules),
                "stats": core.proactivity.stats(),
                "due_now": len(core.proactivity.due_now()),
            })

        elif self._path() == "/perception":
            # JARVIS Evolution: sensores y percepción del entorno.
            self.send_json(200, {
                "capabilities": core.perception.list_capabilities(),
                "perception": core.perception.perceive_all(),
            })

        elif self._path() == "/integrations":
            # JARVIS Evolution: catálogo de integraciones con dispositivos.
            self.send_json(200, {
                "capabilities": core.integrations.list_capabilities(),
            })

        elif self._path() == "/state":
            # Conciencia unificada: estado de I.L.U. como una sola
            # inteligencia (identidad, aprendizaje, objetivos, percepción,
            # proactividad) para que la UI renderice su presencia.
            self.send_json(200, core._build_awareness(""))

        elif self._path() == "/notifications":
            # Notificaciones locales de I.L.U. (tool notify + proactividad
            # en vivo). La UI las lee para mostrar avisos del sistema.
            limit = _int_or(query.get("limit"), 20)

            self.send_json(200, {
                "notifications": _read_notifications(limit),
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

    def _require_device_token(self):
        """Envía 401 si el request no demuestra el token de dispositivo."""
        if not self._authorized():
            self.send_json(401, {
                "success": False,
                "error": "unauthorized",
                "message": "Se requiere el token de dispositivo para esta acción."
            })
            return False
        return True

    def _grant(self):
        """Concede un permiso. Authority valida que el actor sea raíz."""
        if not self._require_device_token():
            return

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
        if not self._require_device_token():
            return

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
        if not self._require_device_token():
            return

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

            # Bloque 10: sesión de conversación (contexto multi-turn).
            session_id = data.get("session_id")

            result = core.process(
                message,
                session_id=session_id
            )

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

    def _handle_tts(self):
        """Sintetiza el texto de la respuesta de I.L.U. a audio (MP3)."""
        text = _query_params(self.path).get("text", "").strip()

        if not text:
            self.send_json(400, {
                "success": False,
                "error": "text_required"
            })
            return

        try:
            audio = tts.synthesize(text)

            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(audio)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(audio)

        except TTSUnavailable as error:
            # 503: el frontend interpreta esto y cae al TTS nativo.
            self.send_json(503, {
                "success": False,
                "error": "tts_unavailable",
                "detail": str(error)
            })

        except Exception as error:  # noqa: BLE001 - respuesta de error controlada
            self.send_json(500, {
                "success": False,
                "error": "tts_error",
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
    # DELETE
    # ------------------------------------------------------------------

    def do_DELETE(self):
        segments = self._segments()

        if (
            len(segments) == 2
            and segments[0] == "conversations"
        ):
            # Bloque 10: resetear el historial de una sesión.
            # Borrar datos es una acción destructiva: requiere el token.
            if not self._require_device_token():
                return

            session_id = segments[1]

            core.conversations.reset(session_id)

            self.send_json(200, {
                "success": True,
                "session_id": session_id,
                "message": (
                    f"Historial de la sesión '{session_id}' borrado."
                )
            })
            return

        self.send_json(404, {
            "error": "not_found"
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

                # Verificación → planificación conectadas: si la tarea se
                # completó y era un paso materializado de un objetivo,
                # se avanza el plan (el objetivo se auto-completa si era
                # el último paso). I.L.U. no otorga permisos: solo cierra
                # el bucle ejecutar→verificar→aprender.
                if (
                    state == "completed"
                    and updated is not None
                    and hasattr(core, "planner")
                ):
                    try:
                        core.planner.advance_from_task(task_id)
                    except Exception:
                        # Avanzar el plan es best-effort: un fallo aquí no
                        # debe romper la respuesta de la tarea.
                        pass

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


# ----------------------------------------------------------------------
# Proactividad EN VIVO (orquestación C)
#
# I.L.U. no espera a que le hablen para ofrecer su ayuda: un hilo
# del servidor revisa las reglas proactivas vencidas y las dispara.
# La regla de oro se mantiene: la proactividad NUNCA ejecuta por sí
# sola. Depende de la autonomía y de los grants activos; si no hay
# autoridad para actuar, solo publica una SUGERENCIA/aviso local.
# ----------------------------------------------------------------------


def _fire_proactive_rule(rule):
    """Dispara una regla vencida con seguridad: actúa o solo sugiere."""
    from tools import notify as notify_tool

    autonomy = settings.autonomy_level
    capability = rule.get("capability")

    has_grant = False

    if capability:
        try:
            # ¿I.L.U. (actor="ilu") tiene un grant activo que cubra la
            # capacidad? has_valid_for es SOLO comprobación: no consume
            # permisos de uso único.
            has_grant = core.grant_store.has_valid_for(
                capability,
                actor="ilu",
            )
        except Exception:
            has_grant = False

    try:
        fired = core.proactivity.fire(
            rule["id"],
            autonomy=autonomy,
            has_grant=has_grant,
        )
    except Exception:
        return

    if fired is None or fired.get("action") == "skip":
        return

    action = fired.get("action")
    text = fired.get("text", "")

    if action == "act" and capability:
        # Con grant y autonomía suficiente, I.L.U. encola la ejecución
        # de la integración gateada (que vuelve a exigir autorización).
        try:
            result = core.integrations.execute(capability)
            message = f"[proactivo·ejecutado] {text}"
            if not result.get("success"):
                message += f" ({result.get('error', 'error')})"
        except Exception:
            message = f"[proactivo] {text}"
    else:
        # Sin autoridad, SOLO se sugiere/avisa (nunca se actúa).
        message = f"[proactivo·sugerencia] {text}"

    notify_tool.notify(
        message=message,
        level="info",
    )


def _proactivity_loop(interval=30):
    """Revisa periódicamente las reglas proactivas vencidas."""
    while True:
        try:
            for rule in core.proactivity.due_now(limit=10):
                _fire_proactive_rule(rule)
        except Exception:
            # Un fallo en el ciclo no debe tumbar el hilo ni el servidor.
            pass

        time.sleep(interval)


if __name__ == "__main__":
    port = int(
        os.environ.get("PORT", "8000")
    )

    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        ILUHandler
    )

    # Hilo de proactividad en vivo (daemon: no bloquea el cierre).
    proactivity_thread = threading.Thread(
        target=_proactivity_loop,
        daemon=True,
        name="ilu-proactivity",
    )
    proactivity_thread.start()

    print(
        f"I.L.U. iniciado en el puerto {port} "
        f"(multi-hilo, {len(_REGISTERED_TASKS)} tareas registradas)"
    )

    server.serve_forever()
