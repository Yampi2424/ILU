"""
Tests para Deep Research (Fase D).

Verifica:
- Descomposición en sub-preguntas.
- Uso de web_search/web_fetch a través del gate.
- Informe con fuentes.
- Timeline de pasos.
- Sin grants creados.
"""

import json
import os
import tempfile
import pytest

from app.deep_research import DeepResearch
from tools.call import ToolCall


@pytest.fixture
def tmp_workspace(monkeypatch, tmp_path):
    """Workspace temporal aislado."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setenv("ILU_WORKSPACE", str(ws))
    monkeypatch.setenv("ILU_DEEP_RESEARCH_PATH", str(tmp_path / "deep_research.jsonl"))
    monkeypatch.setenv("ILU_SCHEDULER_PATH", str(tmp_path / "scheduler.jsonl"))
    monkeypatch.setenv("ILU_AGENTS_PATH", str(tmp_path / "agents.jsonl"))
    monkeypatch.setenv("ILU_PROACTIVITY_PATH", str(tmp_path / "proactivity.jsonl"))
    return ws


@pytest.fixture
def core(monkeypatch, tmp_workspace):
    """ILUCore aislado con stores temporales."""
    for key in [
        "DATABASE_URL", "DATABASE_URL_POOLED", "ILU_AI_PROVIDER",
        "ILU_AUTONOMY", "ILU_OWNER_SECRET"
    ]:
        monkeypatch.delenv(key, raising=False)

    from app.core import ILUCore
    core = ILUCore()

    # Mock provider para tests que lo necesiten
    class MockProvider:
        name = "mock"
        version = "0.0.1"
        def generate(self, prompt, context=None, tools=None):
            return {"type": "text", "content": "mock response"}
    core.provider = MockProvider()
    return core


class TestDeepResearch:
    """DeepResearch funcionalidad completa."""

    def test_run_returns_task_id_immediately(self, core, tmp_workspace):
        """run() devuelve task_id y lanza en background."""
        dr = DeepResearch(core=core)
        result = dr.run("¿Cómo funciona la fotosíntesis?")

        assert result["success"] is True
        assert "task_id" in result
        assert result["status"] == "pending"

    def test_task_persists_and_can_be_retrieved(self, core, tmp_workspace):
        """La tarea persiste y se puede consultar estado."""
        dr = DeepResearch(core=core)
        result = dr.run("Test question")

        task_id = result["task_id"]
        status = dr.get_status(task_id)

        assert status is not None
        assert status["id"] == task_id
        assert status["question"] == "Test question"
        assert status["status"] in ("pending", "running", "done", "failed")

    def test_decompose_fallback_works(self, core, tmp_workspace):
        """Descomposición fallback funciona sin proveedor."""
        dr = DeepResearch(core=core)

        # Pregunta con conectores
        subs = dr._decompose_fallback("¿Qué es X y cómo funciona Y?")
        assert len(subs) >= 2

        # Pregunta simple
        subs2 = dr._decompose_fallback("¿Qué es la fotosíntesis?")
        assert len(subs2) == 1
        assert subs2[0] == "¿Qué es la fotosíntesis?"

    def test_research_sub_question_uses_gate(self, core, tmp_workspace, monkeypatch):
        """_research_sub_question pasa por _execute_tool_call (gate)."""
        dr = DeepResearch(core=core)

        captured = {"tools": [], "modes": []}

        def mock_execute(call, mode="direct"):
            captured["tools"].append(call.tool)
            captured["modes"].append(mode)
            if call.tool == "web_search":
                return {"success": True, "results": [
                    {"snippet": "Resultado 1", "url": "https://example.com/1"},
                    {"snippet": "Resultado 2", "url": "https://example.com/2"},
                ]}
            if call.tool == "web_fetch":
                return {"success": True, "content": "Contenido de la página", "url": call.arguments.get("url")}
            return {"success": False, "error": "unknown_tool"}

        core._execute_tool_call = mock_execute

        result = dr._research_sub_question("Test sub question")

        assert "web_search" in captured["tools"]
        assert "web_fetch" in captured["tools"]
        assert all(m == "deep_research" for m in captured["modes"])
        assert result["sources"]  # debe haber fuentes
        assert len(result["sources"]) > 0

    def test_synthesize_report_includes_sources(self, core, tmp_workspace):
        """Informe final incluye sección Fuentes."""
        dr = DeepResearch(core=core)

        findings = [{
            "sub_question": "Sub 1",
            "result": {"content": "Contenido sub 1", "sources": [
                {"url": "https://example.com/1", "snippet": "Snippet 1"}
            ]}
        }]
        all_sources = [{
            "url": "https://example.com/1",
            "snippet": "Snippet 1",
            "sub_question": "Sub 1",
        }]

        report = dr._synthesize_fallback("Pregunta principal", findings, all_sources)

        assert "Pregunta principal" in report
        assert "Sub 1" in report
        assert "Fuentes" in report
        assert "https://example.com/1" in report

    def test_run_never_creates_grants(self, core, tmp_workspace):
        """DeepResearch NUNCA crea grants."""
        grants_before = len(core.grant_store.list())

        dr = DeepResearch(core=core)

        # Mock provider para descomponer
        def mock_generate(prompt, context, tools):
            return {"type": "text", "content": '["Sub 1", "Sub 2"]'}

        core.provider.generate = mock_generate

        # Mock tools
        def mock_execute(call, mode="direct"):
            if call.tool == "web_search":
                return {"success": True, "results": [
                    {"snippet": "r", "url": "https://example.com/1"}
                ]}
            if call.tool == "web_fetch":
                return {"success": True, "content": "content", "url": call.arguments.get("url")}
            return {"success": False, "error": "unknown"}

        core._execute_tool_call = mock_execute

        # Ejecutar
        result = dr.run("Test question")

        # Esperar a que termine (es rápido en tests sin red real)
        import time
        for _ in range(50):
            status = dr.get_status(result["task_id"])
            if status["status"] in ("done", "failed"):
                break
            time.sleep(0.02)

        grants_after = len(core.grant_store.list())
        assert grants_after == grants_before

    def test_timeline_steps_recorded(self, core, tmp_workspace):
        """Los pasos se registran en task.steps (timeline para UI)."""
        dr = DeepResearch(core=core)

        def mock_generate(prompt, context, tools):
            return {"type": "text", "content": '["Sub 1", "Sub 2"]'}

        core.provider.generate = mock_generate

        def mock_execute(call, mode="direct"):
            if call.tool == "web_search":
                return {"success": True, "results": [{"snippet": "r", "url": "https://example.com/1"}]}
            if call.tool == "web_fetch":
                return {"success": True, "content": "content", "url": call.arguments.get("url")}
            return {"success": False, "error": "unknown"}

        core._execute_tool_call = mock_execute

        result = dr.run("Test question")

        import time
        for _ in range(50):
            status = dr.get_status(result["task_id"])
            if status["status"] in ("done", "failed"):
                break
            time.sleep(0.02)

        status = dr.get_status(result["task_id"])
        assert status["status"] == "done"
        assert len(status["steps"]) >= 3  # decompose, research_sub, sub_done, synthesize

        step_names = [s["step"] for s in status["steps"]]
        assert "decompose" in step_names
        assert "research_sub" in step_names
        assert "sub_done" in step_names
        assert "synthesize" in step_names


class TestDeepResearchNL:
    """Detección de intención de investigación profunda (NL)."""

    def test_detects_deep_research_intent(self, core, tmp_workspace):
        """Core detecta 'investigá profundamente' / 'investigación'."""
        # El comando NL se maneja en core._research_command
        # Verificamos que existe el método
        assert hasattr(core, "_research_command")

    def test_research_command_creates_task(self, core, tmp_workspace):
        """_research_command crea tarea y devuelve info."""
        # Mock provider
        def mock_generate(prompt, context, tools):
            return {"type": "text", "content": '["Sub 1"]'}

        core.provider.generate = mock_generate

        def mock_execute(call, mode="direct"):
            if call.tool == "web_search":
                return {"success": True, "results": [{"snippet": "r", "url": "https://example.com/1"}]}
            if call.tool == "web_fetch":
                return {"success": True, "content": "content", "url": call.arguments.get("url")}
            return {"success": False, "error": "unknown"}

        core._execute_tool_call = mock_execute

        result = core._research_command("investigá profundamente sobre cambio climático")

        assert result is not None
        assert result.get("success") is True
        assert result.get("intent") == "research_started"
        # task_id va anidado en research (vía _javis_reply)
        assert "task_id" in result.get("research", {})