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
# puede invocarlas, demostrando UNA de dos credenciales:
#   - el token de dispositivo (security/device.key, gitignored, generado
#     en el primer arranque), la credencial de la MÁQUINA; o
#   - el secreto del owner (security/owner.pin / ILU_OWNER_SECRET), el
#     MISMO PIN que la concesión por voz/texto, la credencial de la
#     PERSONA.
#
# Las dos credenciales se guardan en claro local (gitignored) y no se
# exponen jamás en logs, auditorías ni respuestas HTTP. /ask y los
# archivos estáticos permanecen abiertos para el uso normal.


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


def _memory_types(core):
    """
    Catálogo de tipos de memoria con su propósito, ciclo de vida y conteo
    actual. Alimenta el browser de memoria de la UI (Fase E).
    """
    from memory.types import MEMORY_TYPES, LEGACY_TYPES, lifecycle_of

    stats = core.memory.stats()

    types = []

    for name in sorted(MEMORY_TYPES):
        info = MEMORY_TYPES[name]
        types.append({
            "name": name,
            "purpose": info["purpose"],
            "lifecycle": info.get("lifecycle", "permanent"),
            "count": stats["counts"].get(name, 0),
        })

    for name in sorted(LEGACY_TYPES):
        types.append({
            "name": name,
            "purpose": lifecycle_of(name),
            "lifecycle": lifecycle_of(name),
            "count": stats["counts"].get(name, 0),
        })

    return types


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
        ¿El request demuestra una credencial de administración?

        Dos credenciales independientes; cualquiera de las dos es
        suficiente:

          1) Token de dispositivo (security/device.key), por la cabecera
             'Authorization: Bearer <token>', 'X-ILU-Token: <token>' o
             la query '?token=<token>'. Es la credencial de la MÁQUINA.
          2) El secreto del owner (security/owner.pin o la variable
             ILU_OWNER_SECRET) — el MISMO PIN que la concesión por
             voz/texto — por la cabecera 'X-ILU-Pin: <secreto>'. Es la
             credencial de la PERSONA.

        Toda comparación es de tiempo constante (secrets.compare_digest)
        y el valor de la clave jamás se loguea, se audita ni se devuelve.
        Fail-closed: si el PIN no está configurado, el camino del PIN NO
        autoriza (solo queda el token de dispositivo). Un PIN incorrecto
        deja rastro en el audit como 'owner_secret_failed'.
        """
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            provided_token = header[7:].strip()
        else:
            provided_token = self.headers.get("X-ILU-Token", "")

        if not provided_token:
            provided_token = _query_params(self.path).get("token", "")

        if provided_token and DEVICE_TOKEN:
            if secrets.compare_digest(provided_token, DEVICE_TOKEN):
                return True

        # Camino del owner: el mismo secreto que usa la voz/texto,
        # validado acá en código determinista (el modelo jamás lo ve).
        provided_pin = self.headers.get("X-ILU-Pin", "")

        if provided_pin and core.owner_secret.configured:
            if core.owner_secret.matches(provided_pin):
                return True

            # Se deja rastro del intento fallido; sin exponer el valor.
            core.audit.record(
                action="owner_secret_failed",
                reason="wrong_pin",
                method="http_x_ilu_pin",
                decision="deny",
            )
            return False

        return False

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
            # Serve index.html from web directory
            if self._send_file(os.path.join(WEB_DIR, "index.html")):
                return

        # Serve static files from web/ directory
        # Supports /css/*, /js/*, /assets/* and optional /static/* prefix
        static_path = self._path()
        file_to_serve = static_path.lstrip("/")
        # Allow both /static/css/... and /css/... forms
        if static_path.startswith("/static"):
            file_to_serve = static_path.replace("/static", "").lstrip("/")
        # Skip if it's an API endpoint or reserved path
        if (
            static_path
            and not static_path.startswith("/api")
            and not static_path.startswith("/healthz")
            and not static_path.startswith("/tts")
            and not static_path.startswith("/about")
            and not static_path.startswith("/tasks")
            and not static_path.startswith("/conversations")
            and not static_path.startswith("/authorization-requests")
            and not static_path.startswith("/grants")
            and not static_path.startswith("/security")
            and not static_path.startswith("/goals")
            and not static_path.startswith("/profile")
            and not static_path.startswith("/proactivity")
            and not static_path.startswith("/perception")
            and not static_path.startswith("/integrations")
            and not static_path.startswith("/state")
            and not static_path.startswith("/notifications")
            and not static_path.startswith("/skills")
            and not static_path.startswith("/scheduler")
            and not static_path.startswith("/agents")
            and not static_path.startswith("/research")
            and not static_path.startswith("/memory")
            and not static_path.startswith("/benchmark")
            and not static_path.startswith("/diagnostics")
        ):
            # file_to_serve already contains the correct relative path
            if self._send_file(file_to_serve):
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

        elif len(segments) == 1 and segments[0] == "skills":
            # Fase B — Catálogo de skills de I.L.U.
            self.send_json(200, {
                "skills": core.skills.catalog(),
                "count": len(core.skills.catalog()),
            })

        elif (
            len(segments) == 2
            and segments[0] == "skills"
        ):
            skill = core.skills.get(segments[1])

            if skill is None:
                self.send_json(404, {
                    "error": "skill_not_found",
                })
            else:
                self.send_json(200, skill.to_dict())

        # ---- Fase C: Scheduler / Jobs ----
        elif len(segments) == 1 and segments[0] == "scheduler":
            self.send_json(200, {
                "jobs": [j.to_dict() for j in core.scheduler_store.list()],
                "count": len(core.scheduler_store.list()),
            })

        elif (
            len(segments) == 2
            and segments[0] == "scheduler"
        ):
            job = core.scheduler_store.get(segments[1])

            if job is None:
                self.send_json(404, {
                    "error": "job_not_found",
                })
            else:
                self.send_json(200, job.to_dict())

        # ---- Fase C: Agentes ----
        elif len(segments) == 1 and segments[0] == "agents":
            self.send_json(200, {
                "agents": [a.to_dict() for a in core.agents.agent_store.list()],
                "count": len(core.agents.agent_store.list()),
            })

        elif (
            len(segments) == 2
            and segments[0] == "agents"
        ):
            agent = core.agents.agent_store.get(segments[1])

            if agent is None:
                self.send_json(404, {
                    "error": "agent_not_found",
                })
            else:
                self.send_json(200, agent.to_dict())

        # ---- Fase D: Deep Research ----
        elif len(segments) == 1 and segments[0] == "research":
            status_filter = query.get("status")
            tasks = core.deep_research.list_tasks(
                status=status_filter or None
            )
            self.send_json(200, {
                "tasks": tasks,
                "count": len(tasks),
            })

        elif (
            len(segments) == 2
            and segments[0] == "research"
        ):
            task = core.deep_research.get_status(segments[1])

            if task is None:
                self.send_json(404, {
                    "error": "research_task_not_found",
                })
            else:
                self.send_json(200, task)

        # ---- Fase E: Memoria (browser) ----
        elif (
            len(segments) == 2
            and segments[0] == "memory"
            and segments[1] == "search"
        ):
            term = query.get("q", "")
            limit = _int_or(query.get("limit"), 20)

            if not term:
                self.send_json(400, {
                    "error": "q_required",
                })
                return

            results = core.memory.search(term, limit=limit)

            self.send_json(200, {
                "results": results,
                "count": len(results),
                "q": term,
            })

        elif (
            len(segments) == 2
            and segments[0] == "memory"
            and segments[1] == "recents"
        ):
            memory_type = query.get("type")
            limit = _int_or(query.get("limit"), 50)

            if memory_type:
                records = core.memory.list_by_type(
                    memory_type, limit=limit
                )
            else:
                records = core.memory.backend.list(limit=limit)

            items = [
                {
                    "key": r.key,
                    "type": r.memory_type,
                    "content": r.content,
                    "importance": r.importance,
                    "source": r.source,
                    "tags": list(r.tags),
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                }
                for r in records
            ]

            self.send_json(200, {
                "results": items,
                "count": len(items),
                "type": memory_type,
            })

        elif (
            len(segments) == 1
            and segments[0] == "memory"
        ):
            self.send_json(200, {
                "stats": core.memory.stats(),
                "types": _memory_types(core),
            })

        # ---- Fase F: Diagnostics + Benchmark ----
        elif (
            len(segments) == 1
            and segments[0] == "diagnostics"
        ):
            from app.diagnostics import quick_check
            self.send_json(200, quick_check(core))

        elif (
            len(segments) == 2
            and segments[0] == "diagnostics"
            and segments[1] == "check"
        ):
            from app.diagnostics import deep_check
            self.send_json(200, deep_check(core))

        elif (
            len(segments) == 1
            and segments[0] == "benchmark"
        ):
            from app.benchmark import get_benchmark_history, get_benchmark_latest
            self.send_json(200, {
                "history": get_benchmark_history(limit=20),
                "latest": get_benchmark_latest(),
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
        query = _query_params(self.path)

        # /ask y /ask/stream (o ?stream=1) comparten el mismo dispatch.
        path = self._path()
        wants_stream = (
            path == "/ask/stream"
            or query.get("stream") in ("1", "true", "yes")
        )

        if path == "/ask" or wants_stream:
            data = self._read_json()
            self._handle_ask(data=data, stream=wants_stream)
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

        if (
            len(segments) == 3
            and segments[0] == "skills"
            and segments[2] == "run"
        ):
            # Fase B — Ejecutar una skill (solo admin). La ejecución es
            # GATEADA: SkillManager corre cada paso de herramienta vía
            # core._execute_tool_call(mode="skill"), así que operaciones
            # que lo requieran abren AuthorizationRequest en vez de
            # autoconcederse.
            self._run_skill(segments[1])
            return

        # ---- Fase C: Scheduler / Jobs ----
        if (
            len(segments) == 1
            and segments[0] == "scheduler"
        ):
            self._create_job()
            return

        if (
            len(segments) == 2
            and segments[0] == "scheduler"
        ):
            self._update_job(segments[1])
            return

        if (
            len(segments) == 3
            and segments[0] == "scheduler"
            and segments[2] == "run"
        ):
            self._run_job(segments[1])
            return

        # ---- Fase C: Agentes ----
        if (
            len(segments) == 1
            and segments[0] == "agents"
        ):
            self._create_agent()
            return

        if (
            len(segments) == 2
            and segments[0] == "agents"
        ):
            self._update_agent(segments[1])
            return

        if (
            len(segments) == 3
            and segments[0] == "agents"
            and segments[2] == "run"
        ):
            self._run_agent(segments[1])
            return

        # ---- Fase D: Deep Research ----
        if (
            len(segments) == 2
            and segments[0] == "research"
            and segments[1] == "run"
        ):
            self._run_research()
            return

        # ---- Fase E: Memoria (ingest / consolidación) ----
        if (
            len(segments) == 2
            and segments[0] == "memory"
            and segments[1] == "ingest"
        ):
            self._run_memory_ingest()
            return

        if (
            len(segments) == 2
            and segments[0] == "memory"
            and segments[1] == "consolidate"
        ):
            self._run_memory_consolidate()
            return

        # ---- Fase F: Diagnostics + Benchmark ----
        if (
            len(segments) == 2
            and segments[0] == "benchmark"
            and segments[1] == "run"
        ):
            if not self._require_admin_auth():
                return
            data = self._read_json()
            mode = data.get("mode", "offline")
            categories = data.get("categories")
            from app.benchmark import run_benchmark
            result = run_benchmark(core, mode=mode, categories=categories)
            self.send_json(200, result)
            return

        self.send_json(404, {
            "error": "not_found"
        })

    def _require_admin_auth(self):
        """Envía 401 si el request no demuestra una credencial
        administrativa (token de dispositivo o clave del owner)."""
        if not self._authorized():
            self.send_json(401, {
                "success": False,
                "error": "unauthorized",
                "message": (
                    "Se requiere autenticación de administrador "
                    "(token de dispositivo o clave del owner)."
                )
            })
            return False
        return True

    def _grant(self):
        """Concede un permiso. Authority valida que el actor sea raíz."""
        if not self._require_admin_auth():
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
        if not self._require_admin_auth():
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
                remember=bool(data.get("remember")),
                indefinite=bool(data.get("indefinite")),
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
                "remembered": result.get("remembered", False),
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
        if not self._require_admin_auth():
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

    def _run_skill(self, name):
        """Ejecuta una skill del catálogo (Fase B).

        La ejecución es GATEADA: cada paso de herramienta corre vía
        core._execute_tool_call(mode="skill"), que pasa por SecurityGate,
        audita y, de ser necesario, abre una AuthorizationRequest. La
        skill nunca se autoconcede permisos.
        """
        if not self._require_admin_auth():
            return

        if not core.skills.has(name):
            self.send_json(404, {
                "success": False,
                "error": "skill_not_found",
                "name": name,
            })
            return

        try:
            data = self._read_json()
            variables = data.get("variables") or {}
        except json.JSONDecodeError:
            variables = {}

        result = core.skills.run(name, variables=variables, actor="ilu")

        status = 200 if result.get("success") else 400

        self.send_json(status, result)

    # ---- Fase C: Scheduler handlers ----

    def _create_job(self):
        """Crea un job programado (solo admin)."""
        if not self._require_admin_auth():
            return

        try:
            data = self._read_json()
            name = data.get("name")
            kind = data.get("kind")
            schedule = data.get("schedule")
            params = data.get("params") or {}
            enabled = data.get("enabled", True)

            if not name or not kind or not schedule:
                self.send_json(400, {
                    "success": False,
                    "error": "name_kind_schedule_required"
                })
                return

            job = core.scheduler_store.add(name, kind, schedule, params, enabled)

            self.send_json(201, job.to_dict())

        except ValueError as e:
            self.send_json(400, {
                "success": False,
                "error": str(e)
            })
        except json.JSONDecodeError:
            self.send_json(400, {
                "success": False,
                "error": "invalid_json"
            })

    def _update_job(self, job_id):
        """Actualiza/pausa/elimina un job (solo admin)."""
        if not self._require_admin_auth():
            return

        try:
            data = self._read_json()
            action = data.get("action")  # "enable", "disable", "delete"

            if action == "delete":
                ok = core.scheduler_store.remove(job_id)
                self.send_json(200, {"success": ok})
                return

            enabled = None
            if action == "enable":
                enabled = True
            elif action == "disable":
                enabled = False
            else:
                self.send_json(400, {
                    "success": False,
                    "error": "invalid_action"
                })
                return

            job = core.scheduler_store.set_enabled(job_id, enabled)
            if job is None:
                self.send_json(404, {
                    "success": False,
                    "error": "job_not_found"
                })
                return

            self.send_json(200, job.to_dict())

        except json.JSONDecodeError:
            self.send_json(400, {
                "success": False,
                "error": "invalid_json"
            })

    def _run_job(self, job_id):
        """Ejecuta un job manualmente (solo admin)."""
        if not self._require_admin_auth():
            return

        job = core.scheduler_store.get(job_id)
        if job is None:
            self.send_json(404, {
                "success": False,
                "error": "job_not_found"
            })
            return

        try:
            core.scheduler._run_job(job, time.time())
            core.scheduler_store.update_last_run(job_id)
            self.send_json(200, {"success": True, "job_id": job_id})
        except Exception as e:
            self.send_json(500, {
                "success": False,
                "error": str(e)
            })

    # ---- Fase C: Agent handlers ----

    def _create_agent(self):
        """Crea un agente (solo admin)."""
        if not self._require_admin_auth():
            return

        try:
            data = self._read_json()
            name = data.get("name")
            role = data.get("role")
            objective = data.get("objective")
            schedule = data.get("schedule", "")
            enabled = data.get("enabled", True)

            if not name or not role or not objective:
                self.send_json(400, {
                    "success": False,
                    "error": "name_role_objective_required"
                })
                return

            agent = core.agents.create(name, role, objective, schedule, enabled)

            self.send_json(201, agent.to_dict())

        except json.JSONDecodeError:
            self.send_json(400, {
                "success": False,
                "error": "invalid_json"
            })

    def _update_agent(self, agent_id):
        """Actualiza/pausa/elimina un agente (solo admin)."""
        if not self._require_admin_auth():
            return

        try:
            data = self._read_json()
            action = data.get("action")  # "enable", "disable", "delete", "update"

            if action == "delete":
                ok = core.agents.agent_store.remove(agent_id)
                self.send_json(200, {"success": ok})
                return

            if action in ("enable", "disable"):
                enabled = action == "enable"
                agent = core.agents.agent_store.set_enabled(agent_id, enabled)
                if agent is None:
                    self.send_json(404, {
                        "success": False,
                        "error": "agent_not_found"
                    })
                    return
                self.send_json(200, agent.to_dict())
                return

            if action == "update":
                # Actualizar campos permitidos
                agent = core.agents.update(agent_id, **{
                    k: v for k, v in data.items()
                    if k in ("name", "role", "objective", "schedule")
                })
                if agent is None:
                    self.send_json(404, {
                        "success": False,
                        "error": "agent_not_found"
                    })
                    return
                self.send_json(200, agent.to_dict())
                return

            self.send_json(400, {
                "success": False,
                "error": "invalid_action"
            })

        except json.JSONDecodeError:
            self.send_json(400, {
                "success": False,
                "error": "invalid_json"
            })

    def _run_agent(self, agent_id):
        """Ejecuta un agente manualmente (solo admin)."""
        if not self._require_admin_auth():
            return

        result = core.agents.run(agent_id)

        status = 200 if result.get("success") else 400

        self.send_json(status, result)

    def _run_research(self):
        """Lanza una investigación profunda (solo admin)."""
        if not self._require_admin_auth():
            return

        try:
            data = self._read_json()
            question = data.get("question", "").strip()

            if not question:
                self.send_json(400, {
                    "success": False,
                    "error": "question_required",
                })
                return

            result = core.deep_research.run(question)

            status = 200 if result.get("success") else 400
            self.send_json(status, result)

        except json.JSONDecodeError:
            self.send_json(400, {
                "success": False,
                "error": "invalid_json",
            })

    def _run_memory_ingest(self):
        """Incorpora un archivo del workspace a la memoria (Fase E).

        La ingestión pasa por la MISMA compuerta (core._execute_tool_call
        con mode="memory"): `memory_ingest` es permission "safe" y
        workspace-scoped, así que se permite sin grant, se audita y nunca
        toca Authority ni GrantStore.
        """
        if not self._require_admin_auth():
            return

        try:
            data = self._read_json()
            source = data.get("source", "").strip()
            tag = data.get("tag")

            if not source:
                self.send_json(400, {
                    "success": False,
                    "error": "source_required",
                })
                return

            from tools.call import ToolCall

            call = ToolCall(
                tool="memory_ingest",
                arguments={"source": source, "tag": tag or ""},
                reason="ingesta via endpoint admin",
            )

            result = core._execute_tool_call(call, mode="memory")

            status = 200 if result.get("success") else 400
            self.send_json(status, result)

        except json.JSONDecodeError:
            self.send_json(400, {
                "success": False,
                "error": "invalid_json",
            })

    def _run_memory_consolidate(self):
        """Consolida la memoria temporal (Fase E).

        Es NO destructivo (las originales se marcan consolidated, no se
        borran) y usa el proveedor para resumir cuando está disponible.
        """
        if not self._require_admin_auth():
            return

        from app.consolidation import consolidate

        provider = getattr(core, "provider", None)
        synthesize = None
        if provider is not None and callable(getattr(provider, "generate", None)):
            def synthesize(prompt):
                out = provider.generate(prompt)
                if isinstance(out, dict):
                    return out.get("content") or ""
                return out or ""

        result = consolidate(
            core.memory,
            synthesize=synthesize,
        )

        core.audit.record(
            "ilu", "memory_consolidate",
            success=result.get("success", False),
            groups=result.get("groups", 0),
            consolidated=result.get("consolidated", 0),
            created=len(result.get("created_keys", [])),
        )

        status = 200 if result.get("success") else 400
        self.send_json(status, result)

    def _handle_ask(self, data=None, stream=False):
        try:
            # Si el body ya fue parseado en do_POST (stream inclusive),
            # reutilizarlo: leer el cuerpo dos veces devolvería vacío.
            if data is None:
                data = self._read_json()

            if stream:
                self._handle_ask_stream(data)
                return

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

    def _handle_ask_stream(self, data):
        """
        SSE endpoint para streaming de la respuesta de I.L.U.

        Consume el generador core.process_stream y emite líneas
        "data: {json}\n\n" con flush por evento. Cierra la conexión
        tras el evento `final`.
        """
        message = data.get("message", "")
        session_id = data.get("session_id")

        # Headers SSE
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            for event in core.process_stream(message, session_id=session_id):
                # Serializa el evento a JSON en una línea
                payload = json.dumps(event, ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()

                # Evento final → cerrar stream
                if event.get("event") == "final":
                    break

        except BrokenPipeError:
            # Cliente desconectado; salir limpio
            return
        except Exception as error:  # noqa: BLE001 - respuesta controlada
            error_payload = json.dumps({
                "event": "error",
                "success": False,
                "response": str(error),
                "error": "internal_error",
            }, ensure_ascii=False)
            self.wfile.write(f"data: {error_payload}\n\n".encode("utf-8"))
            self.wfile.flush()

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
            if not self._require_admin_auth():
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

    # ---- Fase C: Scheduler daemon ----
    scheduler_thread = threading.Thread(
        target=lambda: core.scheduler.start(),
        daemon=True,
        name="ilu-scheduler",
    )
    scheduler_thread.start()

    # ---- Fase E: Consolidación diaria (job del scheduler) ----
    # Asegurar que existe un job de consolidación diario (cron 03:00).
    # Se crea solo si no existe; luego el scheduler lo recoge.
    try:
        existing = core.scheduler_store.list(kind="consolidation")
        if not existing:
            core.scheduler_store.add(
                name="consolidación-diaria",
                kind="consolidation",
                schedule="03:00",
                enabled=True,
                params={},
            )
    except Exception:
        # Fail-silent: si falla, no bloquea el arranque.
        pass

    # ---- Fase E: Consolidación al arrancar (no bloqueante) ----
    # Corre en un hilo aparte para no retener el arranque del servidor.
    def _startup_consolidation():
        try:
            from app.consolidation import consolidate

            provider = getattr(core, "provider", None)
            synthesize = None
            if provider is not None and callable(getattr(provider, "generate", None)):
                def synthesize(prompt):
                    out = provider.generate(prompt)
                    if isinstance(out, dict):
                        return out.get("content") or ""
                    return out or ""

            result = consolidate(core.memory, synthesize=synthesize)
            core.audit.record(
                "ilu", "startup_consolidation",
                success=result.get("success", False),
                groups=result.get("groups", 0),
                consolidated=result.get("consolidated", 0),
                created=len(result.get("created_keys", [])),
            )
            if hasattr(core, "notify") and result.get("groups", 0):
                core.notify(
                    "Consolidé mi memoria al arrancar: %d grupo(s) → %d "
                    "recuerdo(s) semántico(s)."
                    % (result.get("groups", 0), result.get("consolidated", 0)),
                    level="info",
                )
        except Exception as e:
            core.audit.record(
                "ilu", "startup_consolidation_error", error=str(e)
            )

    threading.Thread(
        target=_startup_consolidation,
        daemon=True,
        name="ilu-startup-consolidation",
    ).start()

    print(
        f"I.L.U. iniciado en el puerto {port} "
        f"(multi-hilo, {len(_REGISTERED_TASKS)} tareas registradas)"
    )

    server.serve_forever()
