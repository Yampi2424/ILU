from app.core import ILUCore
from app.audit import AuditLog
from memory.store import MemoryStore


class FakeProvider:
    """
    Proveedor simulado: respuestas controladas, sin red.
    Sustituye a core.provider después de construir ILUCore.
    """

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


def make_core(monkeypatch, tmp_path, model_result, autonomy=None):
    # Sin base de datos, sin proveedor externo y con auditoría en tmp.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_POOLED", raising=False)
    monkeypatch.delenv("ILU_AI_PROVIDER", raising=False)

    if autonomy is None:
        monkeypatch.delenv("ILU_AUTONOMY", raising=False)
    else:
        monkeypatch.setenv("ILU_AUTONOMY", autonomy)

    core = ILUCore()

    core.provider = FakeProvider(model_result)
    core.memory = MemoryStore(path=str(tmp_path / "data.json"))
    core.audit = AuditLog(path=str(tmp_path / "audit.jsonl"))

    return core


def test_model_tool_call_executes_registered_tool(monkeypatch, tmp_path):
    model_result = {
        "type": "tool_call",
        "tool": "system_time",
        "arguments": {},
        "reason": "el usuario pidió la hora"
    }

    core = make_core(monkeypatch, tmp_path, model_result)

    result = core.process("¿me puedes decir la hora?")

    assert result["success"] is True
    assert result["intent"] == "tool_use"
    assert result["tool"] == "system_time"
    assert result["tool_call"]["tool"] == "system_time"
    assert "datetime" in result["tool_result"]
    assert result["reasoning"]["type"] == "model_tool"

    # El modelo recibió el registro de herramientas permitidas.
    names = [tool["name"] for tool in core.provider.last_tools]
    assert "system_time" in names


def test_model_tool_call_unknown_tool_refused(monkeypatch, tmp_path):
    # Una respuesta del modelo NUNCA equivale a permiso de ejecución:
    # aunque proponga `shell`, I.L.U. no tiene esa herramienta y la
    # rechaza. El mensaje NO dispara el despacho NL directo del mundo
    # (Bloque 13) para que el modelo (fake) sea quien proponga shell.
    model_result = {
        "type": "tool_call",
        "tool": "shell",
        "arguments": {"command": "rm -rf /"},
        "reason": "ejecutar comando"
    }

    core = make_core(monkeypatch, tmp_path, model_result)

    result = core.process("quiero que se borre todo el disco")

    assert result["success"] is False
    assert result["intent"] == "tool_error"
    assert result["tool"] == "shell"
    assert result["tool_result"]["error"] == "tool_not_available"


def test_model_tool_call_blocked_tool_refused(monkeypatch, tmp_path):
    model_result = {
        "type": "tool_call",
        "tool": "peligrosa",
        "arguments": {},
        "reason": "prueba de permiso"
    }

    core = make_core(monkeypatch, tmp_path, model_result)

    # Se registra una herramienta pero con permiso bloqueado.
    core.tools.register(
        "peligrosa",
        "Herramienta bloqueada",
        lambda: "no debería correr",
        permission="blocked"
    )

    result = core.process("haz algo")

    assert result["success"] is False
    assert result["intent"] == "tool_error"
    assert result["tool"] == "peligrosa"
    assert result["tool_result"]["error"] == "tool_blocked"


def test_model_text_passthrough(monkeypatch, tmp_path):
    model_result = {
        "type": "text",
        "content": "Aquí está tu respuesta."
    }

    core = make_core(monkeypatch, tmp_path, model_result)

    result = core.process("explícame algo")

    assert result["success"] is True
    assert result["response"] == "Aquí está tu respuesta."
    assert result["tool"] is None
    assert result["tool_call"] is None

    names = [tool["name"] for tool in core.provider.last_tools]
    assert "system_time" in names


def test_model_error_reported(monkeypatch, tmp_path):
    model_result = {
        "type": "error",
        "content": "I.L.U. no pudo contactar con el modelo.",
        "detail": "network_down"
    }

    core = make_core(monkeypatch, tmp_path, model_result)

    result = core.process("pregunta cualquiera")

    assert result["success"] is True
    assert "no pudo" in result["response"]
    assert result["tool"] is None
    assert result["tool_call"] is None


def test_direct_deterministic_tool_still_works(monkeypatch, tmp_path):
    # Compatibilidad: la herramienta determinista por frase
    # sigue respondiendo sin pasar por el modelo.
    core = make_core(
        monkeypatch,
        tmp_path,
        {"type": "text", "content": "no se debe usar"}
    )

    result = core.process("¿qué hora es?")

    assert result["success"] is True
    assert result["intent"] == "tool_use"
    assert result["reasoning"]["type"] == "direct_tool"
    assert "datetime" in result["tool_result"]
    assert core.provider.last_tools is None

    # La auditoría registró el intento permitido.
    audit = core.audit.recent()
    assert any(
        entry.get("action") == "tool_attempt"
        and entry.get("decision") == "allow"
        and entry.get("tool") == "system_time"
        for entry in audit
    )


def test_model_tool_call_ask_waits_for_authorization(monkeypatch, tmp_path):
    executed = {"called": False}

    def handler():
        executed["called"] = True
        return "no debería ejecutarse"

    model_result = {
        "type": "tool_call",
        "tool": "correo",
        "arguments": {},
        "reason": "enviar un mensaje"
    }

    core = make_core(monkeypatch, tmp_path, model_result)

    core.tools.register(
        "correo",
        "Envía un mensaje",
        handler,
        permission="ask"
    )

    result = core.process("envíale un correo a mamá")

    # El handler jamás se ejecuta: la compuerta se detiene.
    assert executed["called"] is False
    assert result["success"] is False
    assert result["intent"] == "tool_error"
    assert result["authorization"] == "ask"
    assert result["tool"] == "correo"
    assert "autorización humana" in result["response"]

    # La auditoría registró el intento con decisión "ask".
    audit = core.audit.recent()
    assert any(
        entry.get("action") == "tool_attempt"
        and entry.get("decision") == "ask"
        and entry.get("tool") == "correo"
        for entry in audit
    )


def test_manual_mode_does_not_execute_model_tool(monkeypatch, tmp_path):
    model_result = {
        "type": "tool_call",
        "tool": "system_time",
        "arguments": {},
        "reason": "la propone el modelo"
    }

    core = make_core(
        monkeypatch,
        tmp_path,
        model_result,
        autonomy="manual"
    )

    result = core.process("¿me puedes decir la hora?")

    # En modo manual, I.L.U. no ejecuta por su cuenta la
    # herramienta que propone el modelo.
    assert result["success"] is False
    assert result["intent"] == "tool_error"
    assert result["authorization"] == "ask"
    assert result["reasoning"]["type"] == "model_tool"
    assert result["tool_result"]["error"] == "manual_mode_proposal"
    assert "modo manual" in result["response"]