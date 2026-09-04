"""
Panel de herramientas (Bloque 6): registro, despacho directo por lenguaje
natural y gating por SecurityGate. El LLM propone; la compuerta decide.
"""

from app.core import ILUCore
from app.audit import AuditLog
from memory.store import MemoryStore
from tools import create_tool_manager
from tools import search as search_module
from tools.notify import _notifications_file


class FakeProvider:
    def __init__(self, model_result):
        self.name = "fake"
        self.version = "0.0.1"
        self.model_result = model_result
        self.last_tools = None

    def generate(self, message, context=None, tools=None):
        self.last_tools = tools

        if callable(self.model_result):
            return self.model_result(message, context, tools)

        return self.model_result


def make_core(monkeypatch, tmp_path, model_result, workspace=None):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_POOLED", raising=False)
    monkeypatch.delenv("ILU_AI_PROVIDER", raising=False)
    monkeypatch.delenv("ILU_AUTONOMY", raising=False)

    if workspace is not None:
        monkeypatch.setenv("ILU_WORKSPACE", str(workspace))

    core = ILUCore()

    core.provider = FakeProvider(model_result)
    core.memory = MemoryStore(path=str(tmp_path / "data.json"))
    core.audit = AuditLog(path=str(tmp_path / "audit.jsonl"))

    return core


def test_panel_registers_five_tools():
    manager = create_tool_manager()

    tools = {tool["name"]: tool["permission"] for tool in manager.list_tools()}

    assert tools == {
        "system_time": "safe",
        "web_search": "safe",
        "read_file": "safe",
        "notify": "safe",
        "write_file": "ask",
    }


def test_direct_web_search_via_natural_language(monkeypatch, tmp_path):
    monkeypatch.setattr(
        search_module,
        "_fetch_json",
        lambda url, timeout=8: {
            "AbstractText": "I.L.U. es el asistente.",
            "AbstractURL": "https://example.org",
        },
    )

    core = make_core(
        monkeypatch,
        tmp_path,
        {"type": "text", "content": "no se debe usar"},
    )

    result = core.process("busca en internet qué es ilu")

    assert result["success"] is True
    assert result["intent"] == "tool_use"
    assert result["tool"] == "web_search"
    assert result["reasoning"]["type"] == "direct_tool"
    assert result["tool_result"]["success"] is True
    assert core.provider.last_tools is None  # no pasó por el modelo


def test_web_search_offline_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(
        search_module,
        "_fetch_json",
        lambda url, timeout=8: None,
    )

    core = make_core(
        monkeypatch,
        tmp_path,
        {"type": "text", "content": "no se debe usar"},
    )

    result = core.process("busca en internet algo")

    # La búsqueda falló de forma explícita: I.L.U. no oculta el fallo.
    assert result["success"] is False
    assert result["intent"] == "tool_error"
    assert result["tool"] == "web_search"
    assert result["tool_result"]["success"] is False
    assert result["tool_result"]["error"] == "web_search_unavailable"


def test_direct_read_file_via_natural_language(monkeypatch, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "notas.txt").write_text("contenido privado", encoding="utf-8")

    core = make_core(
        monkeypatch,
        tmp_path,
        {"type": "text", "content": "no se debe usar"},
        workspace=workspace,
    )

    result = core.process("lee el archivo notas.txt")

    assert result["success"] is True
    assert result["intent"] == "tool_use"
    assert result["tool"] == "read_file"
    assert result["tool_result"]["success"] is True
    assert result["tool_result"]["content"] == "contenido privado"


def test_direct_read_file_refuses_escape(monkeypatch, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()

    core = make_core(
        monkeypatch,
        tmp_path,
        {"type": "text", "content": "no se debe usar"},
        workspace=workspace,
    )

    result = core.process("lee el archivo ../../etc/passwd")

    assert result["success"] is False
    assert result["intent"] == "tool_error"
    assert result["tool"] == "read_file"
    assert result["tool_result"]["success"] is False
    assert result["tool_result"]["error"] == "path_outside_workspace"


def test_direct_notify_via_natural_language(monkeypatch, tmp_path):
    notify_path = tmp_path / "notif.jsonl"
    monkeypatch.setattr("tools.notify._notifications_file",
                        lambda: notify_path)

    core = make_core(
        monkeypatch,
        tmp_path,
        {"type": "text", "content": "no se debe usar"},
    )

    result = core.process("notifícame que la tarea terminó")

    assert result["success"] is True
    assert result["intent"] == "tool_use"
    assert result["tool"] == "notify"
    assert result["tool_result"]["success"] is True
    assert notify_path.exists()


def test_write_file_proposed_by_model_waits_at_gate(monkeypatch, tmp_path):
    # El LLM propone crear un archivo: permiso "ask" → la compuerta se
    # detiene y el handler JAMÁS se ejecuta.
    workspace = tmp_path / "ws"
    workspace.mkdir()

    model_result = {
        "type": "tool_call",
        "tool": "write_file",
        "arguments": {"path": "autogenerado.txt", "content": "contenido"},
        "reason": "el modelo quiere guardar un archivo",
    }

    core = make_core(
        monkeypatch,
        tmp_path,
        model_result,
        workspace=workspace,
    )

    result = core.process("guarda un archivo")

    assert result["success"] is False
    assert result["intent"] == "tool_error"
    assert result["authorization"] == "ask"
    assert result["tool"] == "write_file"
    assert "autorización humana" in result["response"]

    # El handler no corrió: no hay ningún archivo creado.
    assert not (workspace / "autogenerado.txt").exists()


def test_write_file_proposed_manually_authorized(monkeypatch, tmp_path):
    # Incluso pidiéndolo, sin un registro de autorizaciones previas la
    # compuerta exige decisión humana (ask) en todo nivel de autonomía.
    workspace = tmp_path / "ws"
    workspace.mkdir()

    model_result = {
        "type": "tool_call",
        "tool": "write_file",
        "arguments": {"path": "x.txt", "content": "x"},
        "reason": "pedido",
    }

    core = make_core(
        monkeypatch,
        tmp_path,
        model_result,
        workspace=workspace,
    )

    core.security.autonomy_level = "autonomous"

    result = core.process("guarda un archivo")

    assert result["authorization"] == "ask"
    assert not (workspace / "x.txt").exists()

    audit = core.audit.recent()
    assert any(
        entry.get("action") == "tool_attempt"
        and entry.get("tool") == "write_file"
        and entry.get("decision") == "ask"
        for entry in audit
    )


def test_tools_exposed_to_model(monkeypatch, tmp_path):
    core = make_core(
        monkeypatch,
        tmp_path,
        {"type": "text", "content": "respuesta"},
    )

    result = core.process("pregunta")

    assert result["success"] is True
    names = {tool["name"] for tool in result["tools"]}
    assert names == {
        "system_time", "web_search",
        "read_file", "notify", "write_file",
        # Bloque 13: ejecución real gateada (se registran en ILUCore).
        "run_command", "open_app", "media_control",
    }