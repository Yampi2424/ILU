"""
I.L.U. — Planificación y Objetivos (Bloque A: JARVIS Evolution).

Un objetivo (goal) es una intención de alto nivel que I.L.U. descompone
en un plan estructurado de pasos accionables. Cada paso puede concretarse
en una tarea del TaskManager (ejecución en segundo plano con retries,
timeout y autorización).

Esta capa NO otorga permisos: solo organiza intenciones en pasos. Toda
ejecución sigue pasando por la compuerta de seguridad (SecurityGate).
En modo manual, un plan es una hoja de ruta; en modo asistido/autónomo,
los pasos autorizados pueden delegarse al ejecutor de tareas.

Persistencia: JSONL local (`memory/goals.jsonl`, gitignored), siguiendo
el patrón de TaskManager (RLock para concurrencia HTTP + hilos).
"""

import json
import os
import threading
import time
import uuid


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# Fases por defecto cuando un objetivo llega sin pasos explícitos.
# Son honestas y genéricas: I.L.U. no finge un planificador complejo,
# organiza la intención y deja la profundización al razonamiento.
_DEFAULT_PHASES = (
    ("investigar", "Investigar y reunir la información necesaria"),
    ("preparar", "Preparar los recursos y condiciones requeridos"),
    ("ejecutar", "Ejecutar las acciones definidas para el objetivo"),
    ("verificar", "Verificar resultados y consolidar lo aprendido"),
)


class GoalPlanner:
    """
    Almacén persistente de objetivos y planes de I.L.U.

    Cada objetivo es un documento independiente con:
      - id, título, objetivo (texto), estado
      - pasos (plan) con estado individual y dependencias opcionales
      - vínculo opcional a tareas del TaskManager (task_ids por paso)

    El GoalPlanner es la fuente de verdad del estado de los planes;
    quién ejecuta los pasos (el orquestador, un hilo en segundo plano,
    un subagente) es responsabilidad del llamador.
    """

    GOAL_STATES = ("active", "paused", "completed", "cancelled")
    STEP_STATES = ("pending", "in_progress", "completed", "blocked", "skipped")

    def __init__(self, path=None, task_manager=None):
        if path is None:
            path = os.environ.get(
                "ILU_GOALS_PATH",
                "memory/goals.jsonl"
            )

        self.path = path
        self.goals = {}
        self.task_manager = task_manager
        self._lock = threading.RLock()

        self._load()

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _load(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        goal = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(goal, dict) and goal.get("id"):
                        self.goals[goal["id"]] = goal
        except OSError:
            self.goals = {}

    def _save(self):
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                with open(self.path, "w", encoding="utf-8") as handle:
                    for goal in self.goals.values():
                        handle.write(
                            json.dumps(goal, ensure_ascii=False) + "\n"
                        )
                return True
            except OSError:
                return False

    # ------------------------------------------------------------------
    # Descomposición
    # ------------------------------------------------------------------

    @staticmethod
    def _decompose(objective, steps=None):
        """Convierte una intención en una lista de pasos ordenados.

        Si el usuario ya da pasos explícitos (lista), se usan tal cual.
        Si no, se genera un plan por fases por defecto. Nunca se inventan
        pasos peligrosos: son marcadores organizativos.
        """
        if steps:
            return [
                {
                    "id": uuid.uuid4().hex[:8],
                    "title": str(step).strip(),
                    "status": "pending",
                    "task_id": None,
                }
                for step in steps
                if str(step).strip()
            ]

        return [
            {
                "id": uuid.uuid4().hex[:8],
                "title": f"{phase}: {description}",
                "status": "pending",
                "task_id": None,
            }
            for phase, description in _DEFAULT_PHASES
        ]

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(self, objective, steps=None, title=None):
        with self._lock:
            objective = (objective or "").strip()

            if not objective:
                raise ValueError("objective_required")

            goal_id = uuid.uuid4().hex[:12]
            now = _now()

            goal = {
                "id": goal_id,
                "title": (title or objective)[:80],
                "objective": objective,
                "status": "active",
                "steps": self._decompose(objective, steps),
                "created_at": now,
                "updated_at": now,
                "completed_at": None,
            }

            self.goals[goal_id] = goal
            self._save()

            return goal

    def list(self, status=None, limit=100):
        goals = list(self.goals.values())

        if status is not None:
            goals = [
                goal for goal in goals
                if goal.get("status") == status
            ]

        goals.sort(
            key=lambda goal: goal.get("created_at", ""),
            reverse=True,
        )

        return goals[:limit]

    def get(self, goal_id):
        return self.goals.get(goal_id)

    def stats(self):
        counts = {}

        for goal in self.goals.values():
            status = goal.get("status", "active")
            counts[status] = counts.get(status, 0) + 1

        return {
            "total": len(self.goals),
            "counts": counts,
        }

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    def set_status(self, goal_id, status):
        with self._lock:
            goal = self.goals.get(goal_id)

            if goal is None:
                return None

            if status not in self.GOAL_STATES:
                raise ValueError("invalid_goal_state")

            goal["status"] = status
            goal["updated_at"] = _now()

            if status in ("completed", "cancelled"):
                goal["completed_at"] = _now()

            self._save()
            return goal

    def set_step_status(self, goal_id, step_id, status):
        with self._lock:
            goal = self.goals.get(goal_id)

            if goal is None:
                return None

            if status not in self.STEP_STATES:
                raise ValueError("invalid_step_state")

            step = next(
                (s for s in goal["steps"] if s["id"] == step_id),
                None,
            )

            if step is None:
                return None

            step["status"] = status
            goal["updated_at"] = _now()

            # Un paso completado cierra el objetivo si es el último.
            if (
                status == "completed"
                and all(
                    s["status"] in ("completed", "skipped")
                    for s in goal["steps"]
                )
            ):
                goal["status"] = "completed"
                goal["completed_at"] = _now()

            self._save()
            return goal

    # ------------------------------------------------------------------
    # Progreso
    # ------------------------------------------------------------------

    def progress(self, goal_id):
        goal = self.get(goal_id)

        if goal is None:
            return None

        steps = goal["steps"]

        if not steps:
            return {"completed": 0, "total": 0, "percent": 0}

        completed = sum(
            1 for s in steps
            if s["status"] in ("completed", "skipped")
        )

        return {
            "completed": completed,
            "total": len(steps),
            "percent": round(completed * 100 / len(steps)),
        }

    # ------------------------------------------------------------------
    # Integración con el ejecutor de tareas
    # ------------------------------------------------------------------

    def materialize_step(self, goal_id, step_id):
        """
        Convierte un paso del plan en una tarea del TaskManager.

        Devuelve la tarea creada, o None si no hay paso/gestor. La
        ejecución de esa tarea sigue sujeta a autorización y timeout:
        el plan NO otorga permisos por sí mismo.
        """
        if self.task_manager is None:
            return None

        goal = self.get(goal_id)

        if goal is None:
            return None

        step = next(
            (s for s in goal["steps"] if s["id"] == step_id),
            None,
        )

        if step is None:
            return None

        task = self.task_manager.create(
            title=f"{goal['title']} · {step['title']}",
            description=f"Paso del objetivo '{goal['id']}'",
        )

        with self._lock:
            step["task_id"] = task["id"]
            step["status"] = "in_progress"
            goal["updated_at"] = _now()
            self._save()

        return task

    def remove(self, goal_id):
        with self._lock:
            if goal_id not in self.goals:
                return False

            del self.goals[goal_id]
            self._save()
            return True
