"""
Tests de la evolución JARVIS de I.L.U. (Bloques A-F).

Cubre los nuevos módulos:
  - GoalPlanner (planificación y objetivos)
  - LearningEngine (aprendizaje y personalización)
  - IdentityRecognizer (reconocimiento de identidad)
  - ProactivityEngine (proactividad regida por autonomía)
  - PerceptionHub (percepción/sensores, reales y stubs)
  - IntegrationManager (integración con dispositivos, gateada)
  - Comandos por lenguaje natural en ILUCore (via /ask)
  - Rutas HTTP nuevas

Regla transversal que se verifica en todo el bloque:
  NINGÚN módulo nuevo otorga permisos por sí mismo; la autoridad sigue
  viviendo en SecurityGate/Authority y las integraciones exigen grant.
"""

import json
import os
import subprocess
import threading
import time
import urllib.request
import urllib.error

import pytest

from app.planning import GoalPlanner
from app.learning import LearningEngine
from app.identity_recognition import IdentityRecognizer
from app.proactivity import ProactivityEngine
from app.perception import (
    PerceptionHub,
    SystemStateDriver,
    FilesystemDriver,
    CameraDriver,
)
from app.integrations import IntegrationManager


# ------------------------------------------------------------------
# GoalPlanner
# ------------------------------------------------------------------

class TestGoalPlanner:

    def test_create_goal_with_default_plan(self, tmp_path):
        planner = GoalPlanner(path=str(tmp_path / "goals.jsonl"))
        goal = planner.create("organizar la mudanza")

        assert goal["status"] == "active"
        assert len(goal["steps"]) >= 3
        assert planner.get(goal["id"]) is goal

    def test_create_goal_with_explicit_steps(self, tmp_path):
        planner = GoalPlanner(path=str(tmp_path / "goals.jsonl"))
        goal = planner.create(
            "publicar informe",
            steps=["redactar", "revisar", "enviar"],
        )

        titles = [step["title"] for step in goal["steps"]]
        assert titles == ["redactar", "revisar", "enviar"]

    def test_objective_required(self, tmp_path):
        planner = GoalPlanner(path=str(tmp_path / "goals.jsonl"))
        with pytest.raises(ValueError):
            planner.create("   ")

    def test_progress_and_status(self, tmp_path):
        planner = GoalPlanner(path=str(tmp_path / "goals.jsonl"))
        goal = planner.create("t", steps=["a", "b"])

        step_a, step_b = goal["steps"]

        planner.set_step_status(goal["id"], step_a["id"], "completed")
        assert planner.progress(goal["id"])["percent"] == 50

        planner.set_step_status(goal["id"], step_b["id"], "completed")
        # Al completar el último paso, el objetivo se completa.
        assert planner.get(goal["id"])["status"] == "completed"
        assert planner.progress(goal["id"])["percent"] == 100

    def test_materialize_step_creates_task(self, tmp_path):
        from tasks.manager import TaskManager

        tasks = TaskManager(path=str(tmp_path / "tasks.json"))
        planner = GoalPlanner(
            path=str(tmp_path / "goals.jsonl"),
            task_manager=tasks,
        )
        goal = planner.create("automatizar informes", steps=["diseñar"])

        step = goal["steps"][0]
        task = planner.materialize_step(goal["id"], step["id"])

        assert task is not None
        assert tasks.get(task["id"]) is not None
        assert planner.get(goal["id"])["steps"][0]["task_id"] == task["id"]

    def test_persistence_across_reload(self, tmp_path):
        path = str(tmp_path / "goals.jsonl")
        planner = GoalPlanner(path=path)
        goal = planner.create("aprender español")

        planner2 = GoalPlanner(path=path)
        assert planner2.get(goal["id"]) is not None
        assert planner2.stats()["total"] == 1

    def test_remove(self, tmp_path):
        planner = GoalPlanner(path=str(tmp_path / "goals.jsonl"))
        goal = planner.create("t")
        assert planner.remove(goal["id"]) is True
        assert planner.get(goal["id"]) is None

    def test_advance_from_task_marks_step_completed(self, tmp_path):
        from tasks.manager import TaskManager

        tasks = TaskManager(path=str(tmp_path / "tasks.json"))
        planner = GoalPlanner(
            path=str(tmp_path / "goals.jsonl"),
            task_manager=tasks,
        )
        goal = planner.create("publicar informe", steps=["a", "b"])

        step_a = goal["steps"][0]
        task = planner.materialize_step(goal["id"], step_a["id"])

        affected = planner.advance_from_task(task["id"])

        assert affected[0]["step_status"] == "completed"
        assert planner.progress(goal["id"])["percent"] == 50

    def test_advance_from_task_completes_goal_when_last(self, tmp_path):
        from tasks.manager import TaskManager

        tasks = TaskManager(path=str(tmp_path / "tasks.json"))
        planner = GoalPlanner(
            path=str(tmp_path / "goals.jsonl"),
            task_manager=tasks,
        )
        goal = planner.create("publicar informe", steps=["a", "b"])

        for step in goal["steps"]:
            task = planner.materialize_step(goal["id"], step["id"])
            planner.advance_from_task(task["id"])

        assert planner.get(goal["id"])["status"] == "completed"
        assert planner.progress(goal["id"])["percent"] == 100

    def test_advance_from_task_ignores_unlinked(self, tmp_path):
        from tasks.manager import TaskManager

        tasks = TaskManager(path=str(tmp_path / "tasks.json"))
        planner = GoalPlanner(
            path=str(tmp_path / "goals.jsonl"),
            task_manager=tasks,
        )
        planner.create("publicar informe", steps=["a"])
        unrelated = tasks.create(title="tarea suelta")

        assert planner.advance_from_task(unrelated["id"]) == []


# ------------------------------------------------------------------
# LearningEngine
# ------------------------------------------------------------------

class TestLearningEngine:

    def test_learns_preference(self, tmp_path):
        from memory.router import MemoryRouter
        from memory.backends import JsonBackend

        backend = JsonBackend(str(tmp_path / "mem.json"))
        engine = LearningEngine(MemoryRouter(backend=backend))

        learned = engine.learn("Prefiero que me respondas en español")

        assert len(learned) == 1
        assert learned[0]["memory_type"] == "preference"
        assert engine.profile()["count"] == 1

    def test_learns_personal(self, tmp_path):
        from memory.router import MemoryRouter
        from memory.backends import JsonBackend

        engine = LearningEngine(
            MemoryRouter(backend=JsonBackend(str(tmp_path / "mem.json")))
        )
        learned = engine.learn("Me llamo Yampi y soy el dueño")

        assert learned and learned[0]["memory_type"] == "personal"

    def test_noise_is_not_learned(self, tmp_path):
        from memory.router import MemoryRouter
        from memory.backends import JsonBackend

        engine = LearningEngine(
            MemoryRouter(backend=JsonBackend(str(tmp_path / "mem.json")))
        )
        learned = engine.learn("qué hora es")

        assert learned == []

    def test_avoids_duplicates(self, tmp_path):
        from memory.router import MemoryRouter
        from memory.backends import JsonBackend

        engine = LearningEngine(
            MemoryRouter(backend=JsonBackend(str(tmp_path / "mem.json")))
        )
        engine.learn("Me gusta el café")
        learned = engine.learn("Me gusta el café")

        assert learned == []
        assert engine.profile()["count"] == 1

    def test_summary_empty(self, tmp_path):
        from memory.router import MemoryRouter
        from memory.backends import JsonBackend

        engine = LearningEngine(
            MemoryRouter(backend=JsonBackend(str(tmp_path / "mem.json")))
        )
        assert "todavía" in engine.summary().lower()


# ------------------------------------------------------------------
# IdentityRecognizer
# ------------------------------------------------------------------

class TestIdentityRecognizer:

    def test_recognizes_owner_by_alias(self):
        rec = IdentityRecognizer()
        result = rec.recognize("jefe, ayúdame con esto")

        assert result["recognized"] is True
        assert result["principal_id"] == "owner"
        assert result["method"] == "alias"

    def test_unknown_by_default(self):
        rec = IdentityRecognizer()
        result = rec.recognize("hola")

        assert result["recognized"] is False
        assert result["kind"] == "unknown"

    def test_add_alias_and_recognize(self):
        rec = IdentityRecognizer()
        rec.add_alias("ana", "Ana")
        result = rec.recognize("Ana, necesito tu ayuda")

        assert result["recognized"] is True
        assert result["principal_id"] == "ana"
        assert result["kind"] == "authorized_user"

    def test_recognizes_owner_via_registry(self, tmp_path):
        from security.principal import PrincipalRegistry

        registry = PrincipalRegistry(
            path=str(tmp_path / "principals.json"),
            owner_id="owner",
        )
        rec = IdentityRecognizer(principals=registry)
        result = rec.recognize("soy owner")

        assert result["recognized"] is True
        assert result["kind"] == "owner"

    def test_declared_identity(self):
        rec = IdentityRecognizer()
        rec.add_alias("lucia", "lucia")
        result = rec.recognize("soy lucia")

        assert result["recognized"] is True
        assert result["principal_id"] == "lucia"


# ------------------------------------------------------------------
# ProactivityEngine
# ------------------------------------------------------------------

class TestProactivityEngine:

    def test_add_and_list(self, tmp_path):
        engine = ProactivityEngine(path=str(tmp_path / "pro.jsonl"))
        rule = engine.add("reminder", "revisar el correo", cadence_minutes=30)

        assert engine.get(rule["id"]) is not None
        assert engine.stats()["total"] == 1

    def test_manual_autonomy_only_suggests(self, tmp_path):
        engine = ProactivityEngine(path=str(tmp_path / "pro.jsonl"))
        rule = engine.add(
            "suggestion",
            "proponer reorganizar archivos",
            capability="workspace_write",
        )

        result = engine.fire(rule["id"], autonomy="manual")

        assert result["action"] == "suggest"

    def test_act_with_grant_in_autonomous(self, tmp_path):
        engine = ProactivityEngine(path=str(tmp_path / "pro.jsonl"))
        rule = engine.add(
            "suggestion",
            "actuar sobre archivos",
            capability="workspace_write",
        )

        result = engine.fire(
            rule["id"],
            autonomy="autonomous",
            has_grant=True,
        )

        assert result["action"] == "act"

    def test_suggest_without_grant_even_autonomous(self, tmp_path):
        engine = ProactivityEngine(path=str(tmp_path / "pro.jsonl"))
        rule = engine.add(
            "suggestion",
            "actuar sin permiso",
            capability="run_command",
        )

        result = engine.fire(
            rule["id"],
            autonomy="autonomous",
            has_grant=False,
        )

        assert result["action"] == "suggest"

    def test_disabled_rule_not_due(self, tmp_path):
        engine = ProactivityEngine(path=str(tmp_path / "pro.jsonl"))
        rule = engine.add("reminder", "algo", cadence_minutes=1)
        engine.set_enabled(rule["id"], False)

        assert rule["id"] not in [r["id"] for r in engine.due_now()]


# ------------------------------------------------------------------
# PerceptionHub
# ------------------------------------------------------------------

class TestPerceptionHub:

    def test_real_system_state(self):
        hub = PerceptionHub()
        hub.register(SystemStateDriver())

        result = hub.perceive("system_state")
        assert result["available"] is True
        assert "uptime_seconds" in result["data"]

    def test_camera_driver_contract(self):
        # La cámara es hardware-aware: si hay hardware, está disponible y
        # devuelve datos; si no, reporta un motivo honesto. Nunca lanza.
        hub = PerceptionHub()
        hub.register(CameraDriver())

        result = hub.perceive("camera")
        assert result["available"] in (True, False)
        assert "capability" in result
        if result["available"]:
            assert "cameras" in result["data"]
        else:
            assert result["reason"] in ("no_camera_device",)

    def test_no_driver(self):
        hub = PerceptionHub()
        result = hub.perceive("nope")
        assert result["available"] is False
        assert result["reason"] == "no_driver_registered"

    def test_filesystem_driver(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        hub = PerceptionHub()
        hub.register(FilesystemDriver(workspace=str(tmp_path)))

        result = hub.perceive("filesystem")
        assert result["available"] is True
        assert "a.txt" in result["data"]["entries"]

    def test_list_capabilities(self):
        hub = PerceptionHub()
        hub.register(SystemStateDriver())
        hub.register(CameraDriver())

        caps = hub.list_capabilities()
        by_name = {c["capability"]: c["available"] for c in caps}
        # system_state es real y local: siempre disponible.
        assert by_name["system_state"] is True
        # camera es hardware-aware: disponible si hay hardware real.
        assert by_name["camera"] in (True, False)


# ------------------------------------------------------------------
# IntegrationManager
# ------------------------------------------------------------------

class TestIntegrationManager:

    def test_unimplemented_reports_honestly(self, tmp_path):
        # device_control sigue PLANIFICADO: reporta not_implemented.
        mgr = IntegrationManager(workspace=str(tmp_path))
        result = mgr.execute("device_control")

        assert result["success"] is False
        assert result["error"] == "not_implemented"

    def test_run_command_es_implementado_y_gateado(self, tmp_path):
        # Bloque 13: run_command ya es REAL. Sin grant -> authorization=ask,
        # nunca "not_implemented".
        mgr = IntegrationManager(workspace=str(tmp_path))
        result = mgr.execute("run_command", command="whoami")

        assert result["success"] is False
        assert result["error"] == "authorization_required"
        assert result["authorization"] == "ask"

    def test_unauthorized_requires_grant(self, tmp_path):
        mgr = IntegrationManager(workspace=str(tmp_path))
        result = mgr.execute("workspace_write", filename="a.txt", content="x")

        assert result["authorization"] == "ask"
        assert result["error"] == "authorization_required"

    def test_workspace_write_with_grant(self, tmp_path):
        from security.grant_store import GrantStore
        from security.grant import Grant

        grants = GrantStore(path=str(tmp_path / "grants.jsonl"))
        grants.add(Grant(
            capability="workspace_write",
            grantor="owner",
            grantee="ilu",
            reason="test",
            indefinite=True,
        ))
        mgr = IntegrationManager(
            workspace=str(tmp_path),
            grant_store=grants,
        )

        result = mgr.execute("workspace_write", filename="b.txt", content="hola")

        assert result["success"] is True
        assert (tmp_path / "b.txt").read_text() == "hola"

    def test_path_escape_blocked(self, tmp_path):
        from security.grant_store import GrantStore
        from security.grant import Grant

        grants = GrantStore(path=str(tmp_path / "grants.jsonl"))
        grants.add(Grant(
            capability="workspace_write",
            grantor="owner",
            grantee="ilu",
            reason="test",
            indefinite=True,
        ))
        mgr = IntegrationManager(workspace=str(tmp_path), grant_store=grants)

        result = mgr.execute(
            "workspace_write",
            filename="../evil.txt",
            content="x",
        )

        assert result["success"] is False
        assert result["error"] == "path_outside_workspace"

    def test_list_capabilities(self, tmp_path):
        # Bloque 13: run_command/open_app/media_control ya están
        # implementadas; device_control sigue PLANIFICADA.
        mgr = IntegrationManager(workspace=str(tmp_path))
        caps = mgr.list_capabilities()
        by_name = {c["capability"]: c["implemented"] for c in caps}

        assert by_name["workspace_write"] is True
        assert by_name["run_command"] is True
        assert by_name["open_app"] is True
        assert by_name["media_control"] is True
        assert by_name["device_control"] is False


# ------------------------------------------------------------------
# ILUCore por lenguaje natural
# ------------------------------------------------------------------

@pytest.fixture(scope="module")
def core_env(tmp_path_factory):
    os.environ["ILU_WORKSPACE"] = str(tmp_path_factory.mktemp("ws"))
    os.environ["ILU_GOALS_PATH"] = str(
        tmp_path_factory.mktemp("g") / "goals.jsonl"
    )
    os.environ["ILU_PROACTIVITY_PATH"] = str(
        tmp_path_factory.mktemp("p") / "pro.jsonl"
    )
    os.environ["ILU_CONVERSATIONS_PATH"] = str(
        tmp_path_factory.mktemp("c") / "conversations.jsonl"
    )
    os.environ["ILU_TASKS_PATH"] = str(
        tmp_path_factory.mktemp("t") / "tasks.json"
    )

    from app.core import ILUCore
    return ILUCore()


class TestILUCoreJavisCommands:

    def test_planifica(self, core_env):
        result = core_env.process("planifica organizar la mudanza")
        assert result["success"] is True
        assert result["intent"] == "plan_create"
        assert result["goal"]["status"] == "active"

    def test_mis_planes(self, core_env):
        result = core_env.process("mis planes")
        assert result["success"] is True
        assert result["intent"] == "plan_list"

    def test_aprendizaje(self, core_env):
        core_env.process("me gusta el café")
        result = core_env.process("qué has aprendido de mí")
        assert result["success"] is True
        assert result["intent"] == "learning_profile"
        assert result["profile"]["count"] >= 1

    def test_recordatorio(self, core_env):
        result = core_env.process("recuérdame revisar el informe en 30 minutos")
        assert result["success"] is True
        assert result["intent"] == "reminder_create"

    def test_percepcion(self, core_env):
        result = core_env.process("qué sensores tienes")
        assert result["success"] is True
        assert result["intent"] == "perception_status"
        assert len(result["capabilities"]) >= 4

    def test_identidad(self, core_env):
        result = core_env.process("jefe, ¿quién soy?")
        assert result["success"] is True
        assert result["intent"] == "identity_recognition"
        assert result["recognition"]["principal_id"] == "owner"


# ------------------------------------------------------------------
# Conciencia unificada (orquestación JARVIS)
# ------------------------------------------------------------------

class TestAwarenessIntegration:

    def test_awareness_includes_identity(self, core_env):
        awareness = core_env._build_awareness("jefe, hola")

        assert awareness["self"] == core_env.name
        assert awareness["identity"]["user"] == "owner"
        assert awareness["identity"]["user_kind"] == "owner"

    def test_awareness_reflects_learning(self, core_env):
        core_env.learning.learn("me gusta el café")
        awareness = core_env._build_awareness("hola")

        assert "me gusta el café" in awareness["preferences"]

    def test_awareness_reflects_goals(self, core_env):
        core_env.planner.create("organizar la mudanza")
        awareness = core_env._build_awareness("hola")

        assert any("organizar la mudanza" in g["title"]
                   for g in awareness["goals"])

    def test_awareness_includes_real_perception(self, core_env):
        awareness = core_env._build_awareness("hola")

        capabilities = {s["capability"] for s in awareness["perception"]}
        # system_state y filesystem son reales y locales.
        assert "system_state" in capabilities

    def test_awareness_context_labeled_blocks(self, core_env):
        core_env.learning.learn("me gusta el café")
        awareness = core_env._build_awareness("hola")
        blocks = core_env._awareness_context(awareness)

        roles = {b["role"] for b in blocks}
        assert "preferencias del usuario" in roles

    def test_awareness_injected_into_response(self, core_env):
        # La respuesta del modelo transporta la conciencia unificada.
        result = core_env.process("me gusta el café")
        assert "awareness" in result

    def test_tool_error_transporta_awareness(self, core_env):
        # La conciencia viaja con TODA respuesta, incluida la de
        # herramientas fallidas: el contrato no depende de que el modelo
        # decida llamar o no a una herramienta en un turno dado.
        result = core_env._build_tool_response(
            "me gusta el café",
            None,
            {"success": False, "error": "world_opaque"},
        )
        assert result["success"] is False
        assert result["intent"] == "tool_error"
        assert "awareness" in result
        assert "awareness_context" in result
        assert result["awareness"]["self"] == core_env.name

    def test_tool_ok_transporta_awareness(self, core_env):
        result = core_env._build_tool_response(
            "qué hora es",
            None,
            {"success": True, "tool": "datetime",
             "result": {"datetime": "2026-09-04 20:00:00"}},
        )
        assert result["success"] is True
        assert result["intent"] == "tool_use"
        assert "awareness" in result
        assert "awareness_context" in result


# ------------------------------------------------------------------
# Rutas HTTP
# ------------------------------------------------------------------

_PORT = 18777


@pytest.fixture(scope="module")
def evo_server(tmp_path_factory):
    os.environ["PORT"] = str(_PORT)
    os.environ["ILU_WORKSPACE"] = str(tmp_path_factory.mktemp("ws"))
    os.environ["ILU_GOALS_PATH"] = str(
        tmp_path_factory.mktemp("g") / "goals.jsonl"
    )
    os.environ["ILU_PROACTIVITY_PATH"] = str(
        tmp_path_factory.mktemp("p") / "pro.jsonl"
    )
    os.environ["ILU_CONVERSATIONS_PATH"] = str(
        tmp_path_factory.mktemp("c") / "conversations.jsonl"
    )
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("DATABASE_URL_POOLED", None)

    from app.__main__ import ILUHandler
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", _PORT), ILUHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.5)
    yield server
    server.shutdown()


def _get(path):
    url = f"http://127.0.0.1:{_PORT}{path}"
    try:
        resp = urllib.request.urlopen(url)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post_json(path, data):
    url = f"http://127.0.0.1:{_PORT}{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class TestJavisHTTPRoutes:

    def test_goals_route(self, evo_server):
        status, data = _get("/goals")
        assert status == 200
        assert "goals" in data and "stats" in data

    def test_ask_planifica_via_http(self, evo_server):
        status, data = _post_json(
            "/ask", {"message": "planifica limpiar el escritorio"}
        )
        assert status == 200
        assert data["intent"] == "plan_create"

    def test_profile_route(self, evo_server):
        status, data = _get("/profile")
        assert status == 200
        assert "groups" in data

    def test_proactivity_route(self, evo_server):
        status, data = _get("/proactivity")
        assert status == 200
        assert "rules" in data and "stats" in data

    def test_perception_route(self, evo_server):
        status, data = _get("/perception")
        assert status == 200
        assert "capabilities" in data

    def test_integrations_route(self, evo_server):
        status, data = _get("/integrations")
        assert status == 200
        assert "capabilities" in data

    def test_about_includes_evolution(self, evo_server):
        status, data = _get("/about")
        assert status == 200
        assert "goals" in data
        assert "learning" in data
        assert "perception" in data

    def test_healthz_still_works(self, evo_server):
        status, _ = _get("/healthz")
        assert status == 200

    def test_state_route(self, evo_server):
        # Conciencia unificada de I.L.U. (presencia) por HTTP.
        status, data = _get("/state")
        assert status == 200
        assert "identity" in data
        assert "goals" in data
        assert "perception" in data

    def test_notifications_route(self, evo_server):
        status, data = _get("/notifications")
        assert status == 200
        assert "notifications" in data
