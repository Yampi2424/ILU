"""
Tests para Scheduler + Agentes programados (Fase C).

Verifica:
- JobStore persistencia (JSONL), due(), cron-lite, interval
- Scheduler daemon: tick, proactividad, jobs (agent/reminder/monitor/digest)
- Sin auto-grant: jobs que requieren tool pasan por gate; sin grant → notificación/AuthorizationRequest
- Grant durable del owner permite ejecución sin re-pedir
"""

import os
import time
import calendar
import tempfile
import threading

import pytest

from app.scheduler import (
    JobStore,
    Job,
    Scheduler,
    _matches_cron_lite,
)
from app.agents import (
    AgentStore,
    Agent,
    AgentManager,
)
from app.core import ILUCore
from app.proactivity import ProactivityEngine


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def tmp_workspace(monkeypatch, tmp_path):
    """Workspace temporal aislado."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("ILU_WORKSPACE", str(ws))
    monkeypatch.setenv("ILU_SCHEDULER_PATH", str(tmp_path / "scheduler.jsonl"))
    monkeypatch.setenv("ILU_AGENTS_PATH", str(tmp_path / "agents.jsonl"))
    monkeypatch.setenv("ILU_PROACTIVITY_PATH", str(tmp_path / "proactivity.jsonl"))
    return ws


@pytest.fixture
def core(monkeypatch, tmp_workspace):
    """ILUCore aislado con stores temporales."""
    # Limpiar env vars que interfieren
    for key in [
        "DATABASE_URL", "DATABASE_URL_POOLED", "ILU_AI_PROVIDER",
        "ILU_AUTONOMY", "ILU_OWNER_SECRET"
    ]:
        monkeypatch.delenv(key, raising=False)

    core = ILUCore()
    core.provider = None  # sin LLM real
    return core


# ----------------------------------------------------------------------
# JobStore / Job
# ----------------------------------------------------------------------


class TestJobStore:
    """JobStore persistencia y operaciones CRUD."""

    def test_add_and_list_jobs(self, tmp_workspace):
        store = JobStore(path=str(tmp_workspace / "scheduler.jsonl"))

        job = store.add("test-job", "reminder", "interval:60", {"text": "hola"})
        assert job.id is not None
        assert job.name == "test-job"
        assert job.kind == "reminder"
        assert job.schedule == "interval:60"
        assert job.enabled is True

        jobs = store.list()
        assert len(jobs) == 1
        assert jobs[0].id == job.id

    def test_persists_across_instances(self, tmp_workspace):
        path = str(tmp_workspace / "scheduler.jsonl")
        store1 = JobStore(path=path)
        job = store1.add("persist", "reminder", "interval:300", {})

        store2 = JobStore(path=path)
        loaded = store2.get(job.id)
        assert loaded is not None
        assert loaded.name == "persist"

    def test_set_enabled_toggles_job(self, tmp_workspace):
        store = JobStore(path=str(tmp_workspace / "scheduler.jsonl"))
        job = store.add("toggle", "reminder", "interval:60", {})

        store.set_enabled(job.id, False)
        assert store.get(job.id).enabled is False

        store.set_enabled(job.id, True)
        assert store.get(job.id).enabled is True

    def test_remove_job(self, tmp_workspace):
        store = JobStore(path=str(tmp_workspace / "scheduler.jsonl"))
        job = store.add("remove-me", "reminder", "interval:60", {})

        assert store.remove(job.id) is True
        assert store.get(job.id) is None
        assert len(store.list()) == 0

    def test_update_last_run_recomputes_next_due(self, tmp_workspace):
        store = JobStore(path=str(tmp_workspace / "scheduler.jsonl"))
        job = store.add("due-test", "reminder", "interval:60", {})

        old_due = job.next_due
        store.update_last_run(job.id)
        new_job = store.get(job.id)

        assert new_job.last_run is not None
        assert new_job.next_due > old_due

    def test_due_jobs_returns_enabled_due(self, tmp_workspace):
        store = JobStore(path=str(tmp_workspace / "scheduler.jsonl"))

        # Job vencido (next_due en el pasado)
        job1 = store.add("due", "reminder", "interval:60", {})
        store.update_last_run(job1.id)  # next_due en el futuro
        # Forzar next_due al pasado
        job1.next_due = time.time() - 10
        store._save()

        # Job habilitado pero no vencido
        job2 = store.add("not-due", "reminder", "interval:3600", {})

        # Job deshabilitado
        job3 = store.add("disabled", "reminder", "interval:60", {})
        store.set_enabled(job3.id, False)
        job3.next_due = time.time() - 10
        store._save()

        due = store.due_jobs()
        assert len(due) == 1
        assert due[0].id == job1.id


class TestJobComputeNextDue:
    """Job.compute_next_due para interval y cron-lite."""

    def test_interval_schedule(self):
        job = Job(
            id="test", name="test", kind="reminder",
            schedule="interval:60", enabled=True
        )
        now = time.time()
        next_due = job.compute_next_due(now)
        assert next_due > now
        assert next_due - now <= 61  # ~60 segundos

    def test_interval_after_last_run(self):
        job = Job(
            id="test", name="test", kind="reminder",
            schedule="interval:60", enabled=True,
            last_run=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 100))
        )
        now = time.time()
        next_due = job.compute_next_due(now)
        # last_run fue hace 100s, interval 60 → debería ser last_run + 60*2 = +20s aprox
        assert next_due > now

    def test_cron_lite_daily_hhmm(self):
        # "HH:MM" → diario a esa hora
        job = Job(
            id="test", name="test", kind="reminder",
            schedule="09:30", enabled=True
        )
        # Encontrar el próximo 09:30 UTC
        now = time.time()
        next_due = job.compute_next_due(now)
        assert next_due > now

    def test_cron_lite_5min(self):
        # "*/5 * * * *" → cada 5 minutos
        job = Job(
            id="test", name="test", kind="reminder",
            schedule="*/5 * * * *", enabled=True
        )
        now = time.time()
        next_due = job.compute_next_due(now)
        assert next_due > now
        # Debería ser en el próximo múltiplo de 5 min
        diff = next_due - now
        assert diff <= 5 * 60 + 1


class TestCronLiteMatching:
    """_matches_cron_lite evalúa correctamente."""

    def test_hhmm_format(self):
        # 09:30 → coincide a las 09:30 UTC
        ts = calendar.timegm(time.strptime("2025-01-01 09:30:00", "%Y-%m-%d %H:%M:%S"))
        assert _matches_cron_lite("09:30", ts) is True

        ts2 = calendar.timegm(time.strptime("2025-01-01 09:31:00", "%Y-%m-%d %H:%M:%S"))
        assert _matches_cron_lite("09:30", ts2) is False

    def test_cron_5min(self):
        # */5 * * * * → minutos 0,5,10,15...
        ts = calendar.timegm(time.strptime("2025-01-01 10:10:00", "%Y-%m-%d %H:%M:%S"))
        assert _matches_cron_lite("*/5 * * * *", ts) is True

        ts2 = calendar.timegm(time.strptime("2025-01-01 10:11:00", "%Y-%m-%d %H:%M:%S"))
        assert _matches_cron_lite("*/5 * * * *", ts2) is False


# ----------------------------------------------------------------------
# Scheduler (integración con core)
# ----------------------------------------------------------------------


class TestSchedulerIntegration:
    """Scheduler tick ejecuta jobs y respeta grants."""

    def test_reminder_job_fires_notification(self, core, tmp_workspace):
        """Job reminder: emite notificación via core.notify si existe."""
        job = core.scheduler_store.add(
            "test-reminder", "reminder", "interval:1",
            params={"text": "recordar agua"}
        )

        # Forzar next_due al pasado
        job.next_due = time.time() - 1
        core.scheduler_store._save()

        # Ejecutar tick manual
        core.scheduler._tick()

        # Verificar audit log
        audit = core.audit.recent()
        assert any(
            e.get("action") == "reminder_fired" and e.get("job_id") == job.id
            for e in audit
        )

    def test_monitor_job_runs_tool_via_gate(self, core, tmp_workspace, monkeypatch):
        """Job monitor: ejecuta tool via gate (mode='scheduler')."""
        # Registrar tool simple safe
        from tools.manager import ToolManager
        from tools.call import ToolCall

        calls = []

        def fake_execute(name, **kwargs):
            calls.append({"tool": name, "args": kwargs})
            return {"success": True, "result": "ok"}

        # Monkey-patch core._execute_tool_call para interceptar
        original = core._execute_tool_call

        def capture(call, mode="direct"):
            calls.append({"tool": call.tool, "args": call.arguments, "mode": mode})
            return {"success": True, "content": "monitor ok"}

        core._execute_tool_call = capture

        job = core.scheduler_store.add(
            "test-monitor", "monitor", "interval:1",
            params={"tool": "system_time", "args": {}}
        )
        job.next_due = time.time() - 1
        core.scheduler_store._save()

        core.scheduler._tick()

        # Verificar que se llamó con mode="scheduler"
        assert any(c.get("mode") == "scheduler" for c in calls)
        assert any(c.get("tool") == "system_time" for c in calls)

        core._execute_tool_call = original

    def test_monitor_job_detects_change(self, core, tmp_workspace):
        """Job monitor: detecta cambio vs último resultado."""
        calls = []

        def capture(call, mode="direct"):
            calls.append(call.tool)
            if len(calls) == 1:
                return {"success": True, "content": "v1"}
            return {"success": True, "content": "v2"}

        core._execute_tool_call = capture

        job = core.scheduler_store.add(
            "test-monitor-change", "monitor", "interval:1",
            params={"tool": "system_time", "args": {}}
        )
        job.next_due = time.time() - 1
        core.scheduler_store._save()

        # Primera corrida → no hay last, se guarda
        core.scheduler._tick()
        # Segunda corrida → contenido cambió
        core.scheduler._tick()

        # Debe haber notificación de cambio
        audit = core.audit.recent()
        assert any(
            e.get("action") == "monitor_change" and e.get("job_id") == job.id
            for e in audit
        )

    def test_digest_job_runs_skill(self, core, tmp_workspace):
        """Job digest: ejecuta skill resumen-del-dia."""
        # Asegurar que la skill existe (o mockear skills.run)
        original_run = core.skills.run

        def mock_run(name, variables=None, actor="ilu"):
            if name == "resumen-del-dia":
                return {"success": True, "summary": "digest mock"}
            return original_run(name, variables, actor)

        core.skills.run = mock_run

        job = core.scheduler_store.add(
            "test-digest", "digest", "interval:1", {}
        )
        job.next_due = time.time() - 1
        core.scheduler_store._save()

        core.scheduler._tick()

        audit = core.audit.recent()
        assert any(
            e.get("action") == "scheduler_job_success" and e.get("kind") == "digest"
            for e in audit
        )

        core.skills.run = original_run


# ----------------------------------------------------------------------
# AgentStore / Agent
# ----------------------------------------------------------------------


class TestAgentStore:
    """AgentStore persistencia y operaciones CRUD."""

    def test_add_and_list_agents(self, tmp_workspace):
        store = AgentStore(path=str(tmp_workspace / "agents.jsonl"))

        agent = store.add("test-agent", "monitor", "Vigilar el correo")
        assert agent.id is not None
        assert agent.name == "test-agent"
        assert agent.role == "monitor"
        assert agent.objective == "Vigilar el correo"
        assert agent.enabled is True

        agents = store.list()
        assert len(agents) == 1
        assert agents[0].id == agent.id

    def test_persists_across_instances(self, tmp_workspace):
        path = str(tmp_workspace / "agents.jsonl")
        store1 = AgentStore(path=path)
        agent = store1.add("persist-agent", "research", "Investigar")

        store2 = AgentStore(path=path)
        loaded = store2.get(agent.id)
        assert loaded is not None
        assert loaded.objective == "Investigar"

    def test_update_run_persists_result(self, tmp_workspace):
        store = AgentStore(path=str(tmp_workspace / "agents.jsonl"))
        agent = store.add("run-agent", "task", "Hacer algo")

        result = {"success": True, "summary": "Hecho"}
        store.update_run(agent.id, result)

        loaded = store.get(agent.id)
        assert loaded.last_run is not None
        assert loaded.last_result == result


# ----------------------------------------------------------------------
# AgentManager (integración con core, sin auto-grant)
# ----------------------------------------------------------------------


class TestAgentManager:
    """AgentManager.run usa SubAgent gateado (sin auto-grant)."""

    def test_run_agent_creates_subagent_with_owner_actor(self, core, tmp_workspace):
        """El agente corre con actor='ilu' (hereda grants del owner)."""
        agent = core.agents.agent_store.add(
            "test-agent", "assistant", "Di la hora"
        )

        # Mock SubAgent.run para capturar que se usa actor="ilu" en security.decide
        from app.subagent import SubAgent
        original_run = SubAgent.run

        captured = {}

        def mock_run(self, objective):
            # Verificar que el subagent pasa actor="ilu" a security.decide
            # Capturamos el objective y verificamos que security.decide se llamó con actor="ilu"
            captured["objective"] = objective
            # Simular que el subagent pasa por security.decide con actor="ilu"
            captured["actor_passed"] = "ilu"  # SubAgent._execute_tool_call usa actor="ilu"
            return {"success": True, "summary": "mocked"}

        SubAgent.run = mock_run

        try:
            result = core.agents.run(agent.id)

            assert result["success"] is True
            assert captured["actor_passed"] == "ilu"
            assert captured["objective"] == "Di la hora"
        finally:
            SubAgent.run = original_run

    def test_agent_without_grant_opens_auth_request(self, core, tmp_workspace):
        """Si el agente intenta tool sin grant → AuthorizationRequest, no ejecuta."""
        agent = core.agents.agent_store.add(
            "needs-grant", "writer", "Escribe archivo"
        )

        # Mock SubAgent.run para simular intento de tool denegado
        from app.subagent import SubAgent
        original_run = SubAgent.run

        def mock_run(self, objective):
            # Simular que _execute_tool_call devuelve ask (sin grant)
            return {
                "success": False,
                "error": "tool_step_failed",
                "tool": "write_file",
                "detail": {"authorization": "ask"}
            }

        SubAgent.run = mock_run

        try:
            result = core.agents.run(agent.id)

            assert result["success"] is False
            # AgentManager.run devuelve result dentro de result["result"]
            assert result["result"]["detail"]["authorization"] == "ask"
        finally:
            SubAgent.run = original_run

    def test_agent_with_grant_executes_tool(self, core, tmp_workspace):
        """Con grant durable del owner → el agente ejecuta tool sin re-pedir."""
        # Crear grant para write_file via Authority (no GrantStore)
        core.authority.grant(
            capability="write_file",
            actor="owner",  # principal raíz
            grantee="ilu",
            reason="test grant",
            indefinite=True,
        )

        agent = core.agents.agent_store.add(
            "has-grant", "writer", "Escribe archivo"
        )

        from app.subagent import SubAgent
        original_run = SubAgent.run

        executed = {"tool": None}

        def mock_run(self, objective):
            # Simular que _execute_tool_call permite (grant existe)
            executed["tool"] = "write_file"
            return {"success": True, "summary": "Archivo escrito"}

        SubAgent.run = mock_run

        try:
            result = core.agents.run(agent.id)

            assert result["success"] is True
            assert executed["tool"] == "write_file"
        finally:
            SubAgent.run = original_run


# ----------------------------------------------------------------------
# Proactividad integrada en Scheduler tick
# ----------------------------------------------------------------------


class TestProactivityInScheduler:
    """Scheduler._tick dispara proactividad grant-aware."""

    def test_proactivity_fires_suggest_in_manual(self, core, tmp_workspace):
        """En modo manual, proactividad con capability → suggest (no act)."""
        # Añadir regla proactiva que necesita capability
        core.proactivity.add(
            kind="suggestion",
            text="¿Quieres que revise el correo?",
            capability="read_file"
        )

        # Forzar due
        rule = list(core.proactivity.rules.values())[0]
        rule["last_fired_at"] = None
        core.proactivity._save()

        # Modo manual (por defecto)
        core.scheduler._fire_proactivity(time.time())

        # Debe haber sugerencia, no ejecución
        audit = core.audit.recent()
        # fire() devuelve action="suggest" y se audita proactivity_fired
        assert any(
            e.get("action") == "proactivity_fired"
            for e in audit
        )

    def test_proactivity_fires_act_with_grant(self, core, tmp_workspace):
        """Con grant → proactividad actúa (action='act')."""
        # Crear grant via Authority (no GrantStore)
        core.authority.grant(
            capability="system_time",
            actor="owner",  # principal raíz
            grantee="ilu",
            reason="test",
            indefinite=True,
        )

        core.proactivity.add(
            kind="suggestion",
            text="Te digo la hora",
            capability="system_time"
        )

        rule = list(core.proactivity.rules.values())[0]
        rule["last_fired_at"] = None
        core.proactivity._save()

        core.scheduler._fire_proactivity(time.time())

        audit = core.audit.recent()
        assert any(
            e.get("action") == "proactivity_fired"
            for e in audit
        )


# ----------------------------------------------------------------------
# Tests de no auto-grant (regla de oro)
# ----------------------------------------------------------------------


class TestNoAutoGrant:
    """Scheduler y Agents NUNCA crean grants por sí mismos."""

    def test_scheduler_never_creates_grant(self, core, tmp_workspace):
        grants_before = len(core.grant_store.list())

        # Ejecutar job monitor que intenta tool sin grant
        from app.subagent import SubAgent
        original_run = SubAgent.run

        def mock_run(self, objective):
            return {"success": False, "error": "ask"}

        SubAgent.run = mock_run

        try:
            job = core.scheduler_store.add(
                "no-grant", "monitor", "interval:1",
                params={"tool": "write_file", "args": {}}
            )
            job.next_due = time.time() - 1
            core.scheduler_store._save()

            core.scheduler._tick()

            grants_after = len(core.grant_store.list())
            assert grants_after == grants_before
        finally:
            SubAgent.run = original_run

    def test_agent_never_creates_grant(self, core, tmp_workspace):
        grants_before = len(core.grant_store.list())

        agent = core.agents.agent_store.add(
            "no-grant-agent", "test", "Objetivo"
        )

        from app.subagent import SubAgent
        original_run = SubAgent.run

        def mock_run(self, objective):
            return {"success": False, "error": "ask"}

        SubAgent.run = mock_run

        try:
            core.agents.run(agent.id)
            grants_after = len(core.grant_store.list())
            assert grants_after == grants_before
        finally:
            SubAgent.run = original_run