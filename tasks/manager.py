import json
import os
import threading
import time
import uuid


class TaskManager:
    """
    Almacén persistente de tareas de I.L.U.

    Cada tarea es un documento de estado independiente. I.L.U. puede
    registrar tareas, consultarlas y actualizar su estado/progreso
    sin perder la información entre reinicios.

    El TaskManager NO ejecuta lógica de negocio: es la fuente de
    verdad del estado de las tareas. Quién las ejecuta (un hilo en
    segundo plano, un subagente, I.L.U. mismo) es responsabilidad
    del orquestador.
    """

    VALID_STATES = (
        "created",
        "queued",
        "running",
        "paused",
        "completed",
        "failed",
        "cancelled"
    )

    def __init__(self, path=None):
        if path is None:
            path = os.environ.get(
                "ILU_TASKS_PATH",
                "memory/tasks.json"
            )

        self.path = path
        self.tasks = {}

        # D-6: reentrant lock para concurrencia. El TaskManager se usa
        # desde hilos del servidor HTTP (ThreadingHTTPServer) y desde
        # tareas en segundo plano (_run_task) a la vez: sin lock, un
        # read-modify-write simultáneo pierde actualizaciones o corrompe
        # el archivo al guardar. RLock permite que los métodos mutantes
        # llamen a save() (que también toma el lock) sin interbloquearse.
        self._lock = threading.RLock()

        self._load()

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = {}

        self.tasks = data.get("tasks", {}) if isinstance(data, dict) else {}

    def save(self):
        with self._lock:
            try:
                with open(self.path, "w", encoding="utf-8") as handle:
                    json.dump(
                        {"tasks": self.tasks},
                        handle,
                        ensure_ascii=False,
                        indent=2
                    )
                return True
            except OSError:
                # Persistencia best-effort: una tarea en memoria no debe
                # romper al sistema si el disco falla.
                return False

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------

    def list_tasks(self, state=None, limit=100):
        tasks = list(self.tasks.values())

        if state is not None:
            tasks = [
                task for task in tasks
                if task["state"] == state
            ]

        tasks.sort(
            key=lambda task: task.get("created_at", ""),
            reverse=True
        )

        return tasks[:limit]

    def stats(self):
        counts = {}

        for task in self.tasks.values():
            state = task.get("state", "created")
            counts[state] = counts.get(state, 0) + 1

        return {
            "total": len(self.tasks),
            "counts": counts
        }

    def get(self, task_id):
        return self.tasks.get(task_id)

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    def create(self, title, description="", priority=5, state="created", max_retries=None):
        with self._lock:
            title = (title or "").strip()

            if not title:
                raise ValueError("task_title_required")

            if max_retries is None:
                try:
                    max_retries = int(
                        os.environ.get(
                            "ILU_TASK_MAX_RETRIES",
                            "3"
                        )
                    )
                except ValueError:
                    max_retries = 3

            task_id = uuid.uuid4().hex[:12]
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            task = {
                "id": task_id,
                "title": title,
                "description": description,
                "priority": priority,
                "state": state,
                "progress": 0,
                "result": None,
                "error": None,
                "retries": 0,
                "max_retries": max_retries,
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "completed_at": None
            }

            self.tasks[task_id] = task
            self.save()

            return task

    def set_state(self, task_id, state):
        with self._lock:
            task = self.tasks.get(task_id)

            if task is None:
                return None

            if state not in self.VALID_STATES:
                raise ValueError("invalid_task_state")

            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            task["state"] = state
            task["updated_at"] = now

            if state == "running" and not task["started_at"]:
                task["started_at"] = now

            if state in ("completed", "failed", "cancelled"):
                task["completed_at"] = now

            self.save()

            return task

    def wait_for_authorization(self, task_id, request_id):
        """
        Pausa la tarea hasta que el owner resuelva una solicitud de
        autorización (Bloque 8). La tarea queda en estado "paused" con
        el campo waiting_authorization apuntando a la solicitud abierta.
        """
        with self._lock:
            task = self.tasks.get(task_id)

            if task is None:
                return None

            task["state"] = "paused"
            task["waiting_authorization"] = request_id
            task["updated_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime()
            )

            self.save()

            return task

    def set_progress(self, task_id, progress):
        with self._lock:
            task = self.tasks.get(task_id)

            if task is None:
                return None

            try:
                progress = int(progress)
            except (TypeError, ValueError):
                raise ValueError("invalid_progress")

            progress = max(0, min(100, progress))

            task["progress"] = progress
            task["updated_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime()
            )

            self.save()

            return task

    def record_retry(self, task_id):
        with self._lock:
            task = self.tasks.get(task_id)

            if task is None:
                return None

            task["retries"] = task.get("retries", 0) + 1
            task["updated_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime()
            )

            self.save()

            return task["retries"]

    def set_result(self, task_id, result):
        with self._lock:
            task = self.tasks.get(task_id)

            if task is None:
                return None

            task["result"] = result
            task["state"] = "completed"
            task["progress"] = 100
            task["completed_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime()
            )
            task["updated_at"] = task["completed_at"]

            self.save()

            return task

    def set_error(self, task_id, error):
        with self._lock:
            task = self.tasks.get(task_id)

            if task is None:
                return None

            task["error"] = error
            task["state"] = "failed"
            task["completed_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime()
            )
            task["updated_at"] = task["completed_at"]

            self.save()

            return task
