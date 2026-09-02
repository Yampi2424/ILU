"""
Bloque 10 — Integración del historial multi-turn en ILUCore.

El historial de la sesión se inyecta como contexto al modelo en la
segunda consulta, de modo que I.L.U. recuerde lo dicho antes. Es
contexto de lectura: no cambia el gateo de herramientas ni la autoridad.
"""

from app.core import ILUCore
from memory.store import MemoryStore


class FakeProvider:
    """Proveedor simulado que captura el contexto que recibe."""

    def __init__(self, model_result):
        self.name = "fake"
        self.version = "0.0.1"
        self.model_result = model_result
        self.last_context = None

    def generate(self, message, context=None, tools=None):
        self.last_context = context or []

        return self.model_result


def make_core(monkeypatch, tmp_path, model_result):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_POOLED", raising=False)
    monkeypatch.delenv("ILU_AI_PROVIDER", raising=False)

    core = ILUCore()

    core.provider = FakeProvider(model_result)
    core.memory = MemoryStore(path=str(tmp_path / "data.json"))

    return core


def test_second_turn_injects_previous_exchange(monkeypatch, tmp_path):
    model_result = {
        "type": "text",
        "content": "respuesta de I.L.U."
    }

    core = make_core(
        monkeypatch,
        tmp_path,
        model_result
    )

    # Primer turno de la sesión.
    core.process(
        "me llamo Ana",
        session_id="sesion-1"
    )

    first_context = core.provider.last_context

    # El primer turno NO lleva historial previo.
    assert not any(
        "Conversación reciente" in str(item)
        for item in first_context
    )

    # Segundo turno de la MISMA sesión.
    core.process(
        "¿cómo me llamo?",
        session_id="sesion-1"
    )

    second_context = core.provider.last_context

    transcript = " ".join(
        str(item) for item in second_context
    )

    # El historial de la sesión se inyecta como contexto.
    assert "Conversación reciente" in transcript
    assert "me llamo Ana" in transcript
    assert "respuesta de I.L.U." in transcript


def test_different_sessions_do_not_mix(monkeypatch, tmp_path):
    model_result = {
        "type": "text",
        "content": "ok"
    }

    core = make_core(
        monkeypatch,
        tmp_path,
        model_result
    )

    # Mensajes neutros que llegan al modelo (no disparan recall/greeting).
    core.process("primer mensaje de la sesión A", session_id="A")
    core.process("consulta propia de B", session_id="B")
    core.process("segunda consulta de B", session_id="B")

    transcript = " ".join(
        str(item) for item in core.provider.last_context
    )

    # La sesión B ve su propio historial, no el de A.
    assert "consulta propia de B" in transcript
    assert "primer mensaje de la sesión A" not in transcript


def test_default_session_when_none(monkeypatch, tmp_path):
    """Sin session_id se usa la sesión 'default' (comportamiento heredado)."""
    model_result = {
        "type": "text",
        "content": "ok"
    }

    core = make_core(
        monkeypatch,
        tmp_path,
        model_result
    )

    core.process("primer mensaje por defecto")
    core.process("segundo mensaje por defecto")

    transcript = " ".join(
        str(item) for item in core.provider.last_context
    )

    assert "Conversación reciente" in transcript
    assert "primer mensaje por defecto" in transcript


def test_tool_calling_still_works_with_history(monkeypatch, tmp_path):
    """El historial no rompe el tool-calling nativo del Bloque 9."""
    model_result = {
        "type": "tool_call",
        "tool": "system_time",
        "arguments": {},
        "reason": "el usuario pidió la hora"
    }

    core = make_core(
        monkeypatch,
        tmp_path,
        model_result
    )

    result = core.process(
        "¿hora?",
        session_id="s1"
    )

    assert result["success"] is True
    assert result["intent"] == "tool_use"
