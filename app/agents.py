"""
Agentes programados persistentes (Fase C).

- AgentStore persistente (memory/agents.jsonl).
- Agent: {id, name, role, objective, schedule, enabled, last_run, last_result}.
- AgentManager.run(agent): SubAgent(actor="ilu").run(objective).
  Tools del agente pasan por el gate con los grants del owner
  (el agente no tiene identidad propia que auto-grantee).
  Resultado → memoria semántica + notificación + audit (agent_run, agent_id, success).
  Persistencia de last_run/last_result.
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from app.subagent import SubAgent


# ----------------------------------------------------------------------
# Agent
# ----------------------------------------------------------------------


@dataclass
class Agent:
    id: str
    name: str
    role: str
    objective: str
    schedule: str = ""          # cron-lite o interval:N (opcional, para scheduler)
    enabled: bool = True
    last_run: Optional[str] = None
    last_result: Optional[dict] = None
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "objective": self.objective,
            "schedule": self.schedule,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "last_result": self.last_result,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Agent":
        return cls(
            id=data["id"],
            name=data["name"],
            role=data["role"],
            objective=data["objective"],
            schedule=data.get("schedule", ""),
            enabled=data.get("enabled", True),
            last_run=data.get("last_run"),
            last_result=data.get("last_result"),
            created_at=data.get("created_at", ""),
        )


# ----------------------------------------------------------------------
# AgentStore (persistente JSONL)
# ----------------------------------------------------------------------


class AgentStore:
    """Almacén persistente de agentes (JSONL)."""

    def __init__(self, path: Optional[str] = None):
        if path is None:
            path = os.environ.get("ILU_AGENTS_PATH", "memory/agents.jsonl")
        self.path = path
        self.agents: dict[str, Agent] = {}
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
                        agent = Agent.from_dict(json.loads(line))
                        self.agents[agent.id] = agent
                    except (json.JSONDecodeError, KeyError, TypeError):
                        continue
        except OSError:
            self.agents = {}

    def _save(self):
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                with open(self.path, "w", encoding="utf-8") as fh:
                    for agent in self.agents.values():
                        fh.write(json.dumps(agent.to_dict(), ensure_ascii=False) + "\n")
                return True
            except OSError:
                return False

    def add(self, name: str, role: str, objective: str, schedule: str = "",
            enabled: bool = True) -> Agent:
        with self._lock:
            agent_id = uuid.uuid4().hex[:12]
            agent = Agent(
                id=agent_id,
                name=name,
                role=role,
                objective=objective,
                schedule=schedule,
                enabled=enabled,
            )
            self.agents[agent_id] = agent
            self._save()
            return agent

    def list(self, enabled: Optional[bool] = None) -> list[Agent]:
        agents = list(self.agents.values())
        if enabled is not None:
            agents = [a for a in agents if a.enabled is enabled]
        agents.sort(key=lambda a: a.created_at, reverse=True)
        return agents

    def get(self, agent_id: str) -> Optional[Agent]:
        return self.agents.get(agent_id)

    def set_enabled(self, agent_id: str, enabled: bool) -> Optional[Agent]:
        with self._lock:
            agent = self.agents.get(agent_id)
            if agent is None:
                return None
            agent.enabled = bool(enabled)
            self._save()
            return agent

    def remove(self, agent_id: str) -> bool:
        with self._lock:
            if agent_id not in self.agents:
                return False
            del self.agents[agent_id]
            self._save()
            return True

    def update_run(self, agent_id: str, result: dict) -> Optional[Agent]:
        with self._lock:
            agent = self.agents.get(agent_id)
            if agent is None:
                return None
            agent.last_run = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            agent.last_result = result
            self._save()
            return agent


# ----------------------------------------------------------------------
# AgentManager
# ----------------------------------------------------------------------


class AgentManager:
    """
    Ejecuta agentes (instancias de SubAgent) con aislamiento de autoridad.

    El agente NO tiene identidad propia para auto-otorgarse permisos:
    - actor="ilu" (hereda grants del owner via SecurityGate)
    - capability se deriva del tool que intenta usar
    - si no hay grant → AuthorizationRequest / notificación, NO ejecuta
    """

    def __init__(
        self,
        core,                          # ILUCore (para _execute_tool_call, memory, audit, notify)
        agent_store: Optional[AgentStore] = None,
    ):
        self.core = core
        self.agent_store = agent_store or AgentStore()

    def run(self, agent_id: str) -> dict:
        """
        Ejecuta un agente y retorna su resultado.

        Flujo:
        1. Carga agente del store
        2. Crea SubAgent(actor="ilu") con objetivo del agente
        3. SubAgent.run() usa _execute_tool_call del core (gate + audit)
        4. Guarda last_run/last_result en store
        5. Audita agent_run
        6. Notifica si hay resultado
        """
        agent = self.agent_store.get(agent_id)
        if agent is None:
            return {
                "success": False,
                "error": "agent_not_found",
                "agent_id": agent_id,
            }

        if not agent.enabled:
            return {
                "success": False,
                "error": "agent_disabled",
                "agent_id": agent_id,
            }

        self.core.audit.record(
            "ilu", "agent_run_start",
            agent_id=agent.id, agent_name=agent.name, objective=agent.objective,
        )

        try:
            # SubAgent usa el core._execute_tool_call inyectado (mode="agent")
            subagent = SubAgent(
                provider=self.core.provider,
                tools=self.core.tools,
                security=self.core.security,
                audit=self.core.audit,
                memory=self.core.memory,
                grant_store=self.core.grant_store,
                policy=self.core.policy,
                emergency=self.core.emergency,
                spoofing=self.core.spoofing,
            )
            result = subagent.run(agent.objective)

            # Persistir resultado
            self.agent_store.update_run(agent.id, result)

            # Si el subagent falló (p. ej. tool denegado por gate), propagar el fallo
            if not result.get("success"):
                self.agent_store.update_run(agent.id, result)
                self.core.audit.record(
                    "ilu", "agent_run_failed",
                    agent_id=agent.id, agent_name=agent.name, error=result.get("error"),
                )
                if hasattr(self.core, "notify"):
                    self.core.notify(
                        f"Agente '{agent.name}' falló: {result.get('error', 'desconocido')[:100]}",
                        level="error",
                    )
                return {
                    "success": False,
                    "agent_id": agent.id,
                    "agent_name": agent.name,
                    "result": result,
                }

            # Guardar en memoria semántica si fue exitoso
            summary = result.get("summary") or str(result)
            self.core.memory.remember(
                summary,
                memory_type="semantic",
                importance=5,
                source="agent",
                tags=[agent.name, "agent_result"],
                metadata={"agent_id": agent.id, "agent_name": agent.name},
            )

            # Notificar
            if hasattr(self.core, "notify"):
                self.core.notify(
                    f"Agente '{agent.name}' completó: {agent.objective[:80]}",
                    level="info",
                )

            self.core.audit.record(
                "ilu", "agent_run_success",
                agent_id=agent.id, agent_name=agent.name, success=True,
            )

            return {
                "success": True,
                "agent_id": agent.id,
                "agent_name": agent.name,
                "result": result,
            }

        except Exception as e:
            error_result = {
                "success": False,
                "error": "agent_execution_failed",
                "detail": str(e),
            }
            self.agent_store.update_run(agent.id, error_result)

            self.core.audit.record(
                "ilu", "agent_run_failed",
                agent_id=agent.id, agent_name=agent.name, error=str(e),
            )

            if hasattr(self.core, "notify"):
                self.core.notify(
                    f"Agente '{agent.name}' falló: {str(e)[:100]}",
                    level="error",
                )

            return {
                "success": False,
                "agent_id": agent.id,
                "agent_name": agent.name,
                "error": str(e),
            }

    def create(self, name: str, role: str, objective: str, schedule: str = "",
               enabled: bool = True) -> Agent:
        """Crea un nuevo agente y opcionalmente lo programa en scheduler."""
        return self.agent_store.add(name, role, objective, schedule, enabled)

    def update(self, agent_id: str, **fields) -> Optional[Agent]:
        with self.agent_store._lock:
            agent = self.agent_store.get(agent_id)
            if agent is None:
                return None
            for k, v in fields.items():
                if hasattr(agent, k):
                    setattr(agent, k, v)
            self.agent_store._save()
            return agent


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------


def create_agent_manager(core, agent_store: Optional[AgentStore] = None) -> AgentManager:
    """Factory para crear el AgentManager inyectando el core."""
    return AgentManager(core=core, agent_store=agent_store)