"""
Bloque 7: sub-agentes y sub-assistant anidado.

El sub-agente comparte proveedor, toolset, SecurityGate y AuditLog del
padre; nunca eleva permisos y respeta el nivel de autonomía heredado.
"""

from app.core import ILUCore
from app.audit import AuditLog
from memory.store import MemoryStore
from tools import create_tool_manager
from app.subagent import SubAgent


class FakeProvider:
    """Devuelve una secuencia de resultados del modelo."""

    def __init__(self, results):
        self.name = "fake"
        self.version = "0.0.1"
        self.results = list(results)
        self.calls = 0
        self.last_tools = None

    def generate(self, message, context=None, tools=None):
        self.calls += 1
        self.last_tools = tools

        if not self.results:
            return {"type": "text", "content": "fin"}

        return self.results.pop(0)


class RepeatToolProvider:
    """Siempre propone la misma herramienta (para probar max_rounds)."""

    def __init__(self, tool, arguments=None):
        self.name = "fake"
        self.version = "0.0.1"
        self.tool = tool
        self.arguments = arguments or {}
        self.calls = 0

    def generate(self, message, context=None, tools=None):
        self.calls += 1
        return {
            "type": "tool_call",
            "tool": self.tool,
            "arguments": self.arguments,
            "reason": "para probar max_rounds",
        }


def make_core(monkeypatch, tmp_path, provider):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_POOLED", raising=False)
    monkeypatch.delenv("ILU_AI_PROVIDER", raising=False)
    monkeypatch.delenv("ILU_AUTONOMY", raising=False)

    core = ILUCore()

    core.provider = provider
    core.memory = MemoryStore(path=str(tmp_path / "data.json"))
    core.audit = AuditLog(path=str(tmp_path / "audit.jsonl"))

    return core


# ----------------------------------------------------------------------
# Detección flexible de delegación
# ----------------------------------------------------------------------

def test_subagent_task_detection_variants():
    core = ILUCore()

    triggers = (
        "delega a un subagente que investigue el clima",
        "delega a un sub agente que investigue el clima",
        "encargá esto a un sub-assistant",
        "manejá esto y resumí los resultados",
        "investiga en paralelo el estado del servidor",
        "delega la revisión de logs",
    )

    for message in triggers:
        assert core._subagent_task(message) is not None, message


def test_subagent_task_does_not_over_match():
    core = ILUCore()

    no_triggers = (
        "hola",
        "qué hora es",
        "delega",  # verbo suelto, sin tarea
        "busca en internet ilu",
        "encargado de la puerta",  # "encarga" no debe disparar
        "pide ayuda al sistema",  # petición normal
    )

    for message in no_triggers:
        assert core._subagent_task(message) is None, message


# ----------------------------------------------------------------------
# Comportamiento del SubAgent
# ----------------------------------------------------------------------

def test_subagent_runs_safe_tool_and_audits(monkeypatch, tmp_path):
    provider = FakeProvider([
        {
            "type": "tool_call",
            "tool": "system_time",
            "arguments": {},
            "reason": "el sub quiere saber la hora",
        },
        {"type": "text", "content": "son las 10 de la mañana"},
    ])

    core = make_core(monkeypatch, tmp_path, provider)
    core.security.autonomy_level = "assisted"

    result = core.process(
        "delega a un subagente que me diga la hora"
    )

    assert result["success"] is True
    assert result["intent"] == "subagent"
    assert result["subagent"]["rounds"] == 2
    assert result["subagent"]["tools_used"] == ["system_time"]
    assert result["response"] == "son las 10 de la mañana"

    audit = core.audit.recent()
    assert any(
        entry.get("action") == "tool_result"
        and entry.get("tool") == "system_time"
        and entry.get("success") is True
        for entry in audit
    )
    assert any(
        entry.get("action") == "subagent"
        and entry.get("success") is True
        for entry in audit
    )


def test_subagent_ask_tool_stops_at_gate(monkeypatch, tmp_path):
    # El sub-agente propone write_file (ask): se detiene en la compuerta,
    # el handler JAMÁS corre y no se crea ningún archivo.
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("ILU_WORKSPACE", str(workspace))

    provider = FakeProvider([
        {
            "type": "tool_call",
            "tool": "write_file",
            "arguments": {"path": "x.txt", "content": "x"},
            "reason": "el sub quiere escribir",
        },
    ])

    core = make_core(monkeypatch, tmp_path, provider)

    result = core.process(
        "delega a un subagente que guarde un archivo"
    )

    assert result["success"] is False
    assert result["intent"] == "subagent"
    assert result["subagent"]["tool"] == "write_file"
    assert not (workspace / "x.txt").exists()

    audit = core.audit.recent()
    assert any(
        entry.get("action") == "tool_attempt"
        and entry.get("tool") == "write_file"
        and entry.get("decision") == "ask"
        for entry in audit
    )


def test_subagent_respects_max_rounds(monkeypatch, tmp_path):
    # El modelo propone herramientas sin fin: el bucle se corta en el tope.
    provider = RepeatToolProvider("system_time")

    core = make_core(monkeypatch, tmp_path, provider)
    core.security.autonomy_level = "assisted"

    result = core.process(
        "delega a un subagente que resuelva esto"
    )

    assert result["success"] is True
    assert result["subagent"]["truncated"] is True
    assert result["subagent"]["rounds"] == 3
    assert len(result["subagent"]["tools_used"]) == 3
    assert provider.calls == 3


def test_subagent_inherits_manual_autonomy(monkeypatch, tmp_path):
    # En modo manual, una tool "safe" propuesta por el (sub)modelo se
    # detiene en "ask": el sub-agente hereda el nivel del padre y nunca lo
    # eleva.
    provider = FakeProvider([
        {
            "type": "tool_call",
            "tool": "system_time",
            "arguments": {},
            "reason": "el sub quiere la hora",
        },
    ])

    core = make_core(monkeypatch, tmp_path, provider)
    core.security.autonomy_level = "manual"

    result = core.process(
        "delega a un subagente que resuelva esto"
    )

    assert result["success"] is False
    assert result["subagent"]["tool"] == "system_time"
    assert result["subagent"]["error"] in (
        "manual_mode_proposal",
        "authorization_required",
    )


def test_subagent_plain_text_response(monkeypatch, tmp_path):
    provider = FakeProvider([
        {"type": "text", "content": "tarea resuelta sin herramientas"},
    ])

    core = make_core(monkeypatch, tmp_path, provider)

    result = core.process(
        "delega a un subagente que salude"
    )

    assert result["success"] is True
    assert result["intent"] == "subagent"
    assert result["subagent"]["rounds"] == 1
    assert result["subagent"]["tools_used"] == []
    assert result["response"] == "tarea resuelta sin herramientas"


def test_subagent_reuses_same_instances(monkeypatch, tmp_path):
    # El SubAgent comparte el ToolManager, SecurityGate y AuditLog del padre.
    core = make_core(
        monkeypatch,
        tmp_path,
        FakeProvider([{"type": "text", "content": "ok"}]),
    )

    sub = SubAgent(
        provider=core.provider,
        tools=core.tools,
        security=core.security,
        audit=core.audit,
        memory=core.memory,
    )

    assert sub.tools is core.tools
    assert sub.security is core.security
    assert sub.audit is core.audit
    assert sub.memory is core.memory