"""
Scheduler + Agentes programados persistentes (Fase C).

Pieza central que conecta el vacío en proactivity.py: "quién las dispara
es responsabilidad del orquestador" → hoy nadie dispara.

- JobStore persistente JSONL (memory/scheduler.jsonl).
- Job: {id, name, kind, schedule, enabled, last_run, next_due, params}.
  Schedule: interval (segundos) o cron-lite ("*/5 * * * *", "HH:MM" diaria).
- Scheduler daemon thread (arranca en __main__ junto al server):
  tick cada ~15s → llama core.on_tick():
  - Dispara core.proactivity.fire() (reglas reminder/check_in/follow_up/
    suggestion vencidas) → flujo proactividad existente (grant-aware).
  - Dispara jobs vencidos:
    * kind=agent → AgentManager.run()
    * kind=reminder → notify
    * kind=monitor → ejecuta check gateado (tool) y compara con última
      corrida, notifica en cambio
    * kind=digest → skill resumen-del-dia
  - Jobs que requieran tool() pasan por _execute_tool_call con actor +
    capability: sin grant → notificación/AuthorizationRequest, nunca
    auto-otorga; con grant durable → corre sin re-pedir.
  - Resultados → notificación + audit (scheduler_job, job_id, success).
"""

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.proactivity import ProactivityEngine


# ----------------------------------------------------------------------
# Cron-lite parser (muy simple: solo minutos/horas/días, sin meses/años)
# ----------------------------------------------------------------------

_CRON_FIELD_RE = re.compile(r"^\s*(\*|\d+)(?:/(\d+))?\s*$")


def _parse_cron_field(value: str, min_v: int, max_v: int) -> list[int]:
    """
    Parsea un campo cron simple: "*", "5", "*/5".
    Devuelve lista de valores permitidos en [min_v, max_v].
    """
    value = value.strip()
    if value == "*":
        return list(range(min_v, max_v + 1))
    m = _CRON_FIELD_RE.match(value)
    if not m:
        return []
    base_str = m.group(1)
    step = int(m.group(2)) if m.group(2) else 1
    # Si base es "*" con step (ej. "*/5"), significa todos los valores cada step
    if base_str == "*":
        return list(range(min_v, max_v + 1, step))
    base = int(base_str)
    if base < min_v or base > max_v:
        return []
    return list(range(base, max_v + 1, step))


def _matches_cron_lite(cron_expr: str, now_ts: float) -> bool:
    """
    Evalúa si `now_ts` coincide con `cron_expr` simple:
    "M H * * *" o "HH:MM" (diario a esa hora).
    """
    now = time.gmtime(now_ts)
    minute, hour = now.tm_min, now.tm_hour

    parts = cron_expr.strip().split()
    if len(parts) == 1 and ":" in parts[0]:
        # Formato "HH:MM" → diario a esa hora/minuto
        try:
            h, m = map(int, parts[0].split(":"))
            return minute == m and hour == h
        except ValueError:
            return False

    if len(parts) != 5:
        return False

    # minute hour day month dow
    allowed_m = _parse_cron_field(parts[0], 0, 59)
    allowed_h = _parse_cron_field(parts[1], 0, 23)
    # day, month, dow: * = cualquier (simplificado: ignoramos día/mes/dow)
    if minute not in allowed_m or hour not in allowed_h:
        return False
    return True


# ----------------------------------------------------------------------
# Job
# ----------------------------------------------------------------------

_JOB_KINDS = ("agent", "reminder", "monitor", "digest", "consolidation")


@dataclass
class Job:
    id: str
    name: str
    kind: str
    schedule: str           # "interval:N" (segundos) o cron-lite
    enabled: bool = True
    last_run: Optional[str] = None
    next_due: Optional[float] = None
    params: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "schedule": self.schedule,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "next_due": self.next_due,
            "params": self.params,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        return cls(
            id=data["id"],
            name=data["name"],
            kind=data["kind"],
            schedule=data["schedule"],
            enabled=data.get("enabled", True),
            last_run=data.get("last_run"),
            next_due=data.get("next_due"),
            params=data.get("params", {}),
            created_at=data.get("created_at", ""),
        )

    def compute_next_due(self, now_ts: Optional[float] = None) -> float:
        """Calcula el próximo timestamp en que este job debe correr."""
        now = now_ts or time.time()

        if self.schedule.startswith("interval:"):
            try:
                interval = int(self.schedule.split(":", 1)[1])
            except (ValueError, IndexError):
                interval = 300
            base = self.last_run
            if base:
                try:
                    base_ts = time.mktime(time.strptime(base, "%Y-%m-%dT%H:%M:%SZ"))
                except ValueError:
                    base_ts = now
            else:
                base_ts = now
            # siguiente múltiplo del intervalo después de base
            next_ts = base_ts + interval
            while next_ts <= now:
                next_ts += interval
            return next_ts

        # cron-lite
        # Buscar el siguiente minuto que coincida (máx 24h adelante).
        # Empezamos en offset=1 para asegurar que sea estrictamente > now.
        for offset in range(1, 24 * 60 + 1):
            cand = now + offset * 60
            if _matches_cron_lite(self.schedule, cand):
                return cand
        return now + 86400  # fallback: mañana


# ----------------------------------------------------------------------
# JobStore (persistente JSONL)
# ----------------------------------------------------------------------


class JobStore:
    """Almacén persistente de jobs (JSONL)."""

    def __init__(self, path: Optional[str] = None):
        if path is None:
            path = os.environ.get("ILU_SCHEDULER_PATH", "memory/scheduler.jsonl")
        self.path = path
        self.jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._load()

    def _load(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        job = Job.from_dict(json.loads(line))
                        self.jobs[job.id] = job
                    except (json.JSONDecodeError, KeyError, TypeError):
                        continue
        except OSError:
            self.jobs = {}

    def _save(self):
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                with open(self.path, "w", encoding="utf-8") as fh:
                    for job in self.jobs.values():
                        fh.write(json.dumps(job.to_dict(), ensure_ascii=False) + "\n")
                return True
            except OSError:
                return False

    def add(self, name: str, kind: str, schedule: str, params: Optional[dict] = None,
            enabled: bool = True) -> Job:
        with self._lock:
            if kind not in _JOB_KINDS:
                raise ValueError(f"invalid_job_kind: {kind}")

            job_id = uuid.uuid4().hex[:12]
            job = Job(
                id=job_id,
                name=name,
                kind=kind,
                schedule=schedule,
                enabled=enabled,
                params=params or {},
            )
            job.next_due = job.compute_next_due()
            self.jobs[job_id] = job
            self._save()
            return job

    def list(self, enabled: Optional[bool] = None, kind: Optional[str] = None) -> list[Job]:
        jobs = list(self.jobs.values())
        if enabled is not None:
            jobs = [j for j in jobs if j.enabled is enabled]
        if kind is not None:
            jobs = [j for j in jobs if j.kind == kind]
        jobs.sort(key=lambda j: j.next_due or 0)
        return jobs

    def get(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def set_enabled(self, job_id: str, enabled: bool) -> Optional[Job]:
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            job.enabled = bool(enabled)
            job.next_due = job.compute_next_due() if enabled else None
            self._save()
            return job

    def remove(self, job_id: str) -> bool:
        with self._lock:
            if job_id not in self.jobs:
                return False
            del self.jobs[job_id]
            self._save()
            return True

    def update_last_run(self, job_id: str, timestamp: Optional[str] = None) -> Optional[Job]:
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            job.last_run = timestamp or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            job.next_due = job.compute_next_due()
            self._save()
            return job

    def due_jobs(self, now_ts: Optional[float] = None) -> list[Job]:
        """Jobs habilitados cuyo next_due <= now."""
        now = now_ts or time.time()
        return [j for j in self.jobs.values() if j.enabled and j.next_due and j.next_due <= now]


# ----------------------------------------------------------------------
# Scheduler (daemon thread)
# ----------------------------------------------------------------------


class Scheduler:
    """
    Daemon que cada ~15s evalúa jobs vencidos y los ejecuta.

    Uso:
        sched = Scheduler(core=core)
        sched.start()
        ...
        sched.stop()
    """

    def __init__(
        self,
        core,                         # ILUCore (para _execute_tool_call, proactivity, skills, agents, audit, notify)
        job_store: Optional[JobStore] = None,
        tick_interval: float = 15.0,
    ):
        self.core = core
        self.job_store = job_store or JobStore()
        self.tick_interval = tick_interval
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="ILU-Scheduler")
            self._thread.start()

    def stop(self, timeout: float = 5.0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                # Log pero no matar el hilo
                self.core.audit.record("ilu", "scheduler_tick_error", error=str(e))
            self._stop.wait(self.tick_interval)

    def _tick(self):
        now = time.time()

        # 1. Proactividad: reglas vencidas
        self._fire_proactivity(now)

        # 2. Jobs del scheduler
        due = self.job_store.due_jobs(now)
        for job in due:
            try:
                self._run_job(job, now)
            except Exception as e:
                self.core.audit.record(
                    "ilu", "scheduler_job_error",
                    job_id=job.id, job_name=job.name, error=str(e),
                )

    def _fire_proactivity(self, now_ts: float):
        """Dispara reglas proactivas vencidas (grant-aware via core)."""
        # core.proactivity es ProactivityEngine
        due_rules = self.core.proactivity.due_now(limit=20)
        for rule in due_rules:
            # Autonomía actual del core (manual/assisted/autonomous)
            autonomy = getattr(self.core, "autonomy", "manual")
            # Verificar grant si la regla tiene capability
            has_grant = False
            capability = rule.get("capability")
            if capability and hasattr(self.core, "grant_store"):
                has_grant = self.core.grant_store.has_valid_for(
                    capability, actor="ilu"
                )
            result = self.core.proactivity.fire(
                rule["id"],
                autonomy=autonomy,
                has_grant=has_grant,
            )
            if result:
                # Auditar siempre que se dispara una regla proactiva
                self.core.audit.record(
                    "ilu", "proactivity_fired",
                    rule_id=rule["id"], kind=rule["kind"], text=rule["text"],
                    proactivity_action=result.get("action"),
                )
                if result.get("action") == "act":
                    # La proactividad devolvió "act" → notificar/ejecutar según tipo
                    if hasattr(self.core, "notify"):
                        self.core.notify(result.get("text", ""), level="info")

    def _run_job(self, job: Job, now_ts: float):
        """Ejecuta un job según su kind."""
        kind = job.kind
        params = job.params

        self.core.audit.record(
            "ilu", "scheduler_job_start",
            job_id=job.id, job_name=job.name, kind=kind,
        )

        try:
            if kind == "agent":
                self._run_agent_job(job, params)
            elif kind == "reminder":
                self._run_reminder_job(job, params)
            elif kind == "monitor":
                self._run_monitor_job(job, params)
            elif kind == "digest":
                self._run_digest_job(job, params)
            elif kind == "consolidation":
                self._run_consolidation_job(job, params)
            else:
                raise ValueError(f"unknown_job_kind: {kind}")

            # Marcar last_run y recalcular next_due
            self.job_store.update_last_run(job.id)
            self.core.audit.record(
                "ilu", "scheduler_job_success",
                job_id=job.id, job_name=job.name, kind=kind,
            )
        except Exception as e:
            self.core.audit.record(
                "ilu", "scheduler_job_failed",
                job_id=job.id, job_name=job.name, kind=kind, error=str(e),
            )
            raise

    def _run_agent_job(self, job: Job, params: dict):
        """Ejecuta un agente programado via AgentManager."""
        agent_id = params.get("agent_id")
        if not agent_id:
            raise ValueError("agent_job_requires_agent_id")

        # Usar AgentManager del core
        if hasattr(self.core, "agents") and hasattr(self.core.agents, "run"):
            self.core.agents.run(agent_id)
        else:
            raise RuntimeError("AgentManager not available in core")

    def _run_reminder_job(self, job: Job, params: dict):
        """Emite un recordatorio (notificación)."""
        text = params.get("text", job.name)
        # Notificación via core si existe, sino solo audit
        if hasattr(self.core, "notify"):
            self.core.notify(text, level="info")
        self.core.audit.record("ilu", "reminder_fired", text=text, job_id=job.id)

    def _run_monitor_job(self, job: Job, params: dict):
        """Ejecuta un check gateado (tool) y compara con última corrida."""
        tool_name = params.get("tool")
        tool_args = params.get("args", {})
        if not tool_name:
            raise ValueError("monitor_job_requires_tool")

        # Ejecutar tool a través del gate (mode="scheduler")
        from tools.call import ToolCall
        call = ToolCall(tool=tool_name, arguments=tool_args, reason=f"monitor job {job.name}")
        result = self.core._execute_tool_call(call, mode="scheduler")

        # Comparar con último resultado guardado en job.params
        last = job.params.get("_last_result")
        changed = False
        if last is None:
            changed = True
        else:
            # Comparación simple de contenido
            changed = str(result) != str(last)

        # Guardar resultado actual para próxima comparación
        job.params["_last_result"] = result
        self.job_store._save()

        if changed:
            # Notificar cambio
            if hasattr(self.core, "notify"):
                self.core.notify(
                    f"Monitor '{job.name}' detectó cambio en {tool_name}",
                    level="info",
                )
            self.core.audit.record("ilu", "monitor_change", job_id=job.id, tool=tool_name)

    def _run_digest_job(self, job: Job, params: dict):
        """Ejecuta skill resumen-del-dia."""
        if hasattr(self.core, "skills") and hasattr(self.core.skills, "run"):
            self.core.skills.run("resumen-del-dia", actor="scheduler")
        else:
            raise RuntimeError("SkillManager not available in core")

    def _run_consolidation_job(self, job: Job, params: dict):
        """Consolida la memoria temporal en recuerdos semánticos (Fase E).

        Pasa por la MISMA función de consolidación que el comando NL y el
        endpoint admin (`app.consolidation.consolidate`), que es NO
        destructiva y nunca toca Authority ni GrantStore. El resumen lo
        hace el proveedor cuando está disponible; si no, se conserva el
        material concatenado (honesto, no inventa datos).
        """
        from app.consolidation import consolidate

        memory = getattr(self.core, "memory", None)
        if memory is None:
            raise RuntimeError("MemoryRouter not available in core")

        provider = getattr(self.core, "provider", None)
        synthesize = None
        if provider is not None and callable(getattr(provider, "generate", None)):
            def synthesize(prompt):
                out = provider.generate(prompt)
                if isinstance(out, dict):
                    return out.get("content") or ""
                return out or ""

        result = consolidate(memory, synthesize=synthesize)

        # El resultado del job queda auditable y visible como notificación
        # (grupos temáticos → recuerdos semánticos creados).
        self.core.audit.record(
            "ilu", "consolidation",
            success=result.get("success", False),
            groups=result.get("groups", 0),
            consolidated=result.get("consolidated", 0),
            created=len(result.get("created_keys", [])),
            job_id=job.id,
        )

        if hasattr(self.core, "notify"):
            groups = result.get("groups", 0)
            consolidated = result.get("consolidated", 0)
            if groups:
                self.core.notify(
                    "Consolidé mi memoria: %d grupo(s) → %d recuerdo(s) "
                    "semántico(s)." % (groups, consolidated),
                    level="info",
                )


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------


def create_scheduler(core, job_store: Optional[JobStore] = None) -> Scheduler:
    """Factory para crear el scheduler inyectando el core."""
    return Scheduler(core=core, job_store=job_store)