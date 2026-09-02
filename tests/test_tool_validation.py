"""
Bloque 11 — Validación de argumentos contra el JSON-schema.

`validate_arguments` rechaza argumentos inválidos (fail-closed) ANTES de
que la herramienta se ejecute. Una tool sin esquema siempre pasa
(retrocompatibilidad). La validación ocurre en `_execute_tool_call`, de
modo que un `tool_call` con malos argumentos jamás llega al handler.
"""

import app.toolshape as toolshape

from app.core import ILUCore
from memory.store import MemoryStore


SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "max_results": {"type": "integer"}
    },
    "required": ["query"]
}


def test_valid_arguments_pass():
    ok, err = toolshape.validate_arguments(
        SCHEMA,
        {"query": "noticias", "max_results": 3}
    )

    assert ok is True
    assert err == ""


def test_missing_required_fails():
    ok, err = toolshape.validate_arguments(
        SCHEMA,
        {"max_results": 3}
    )

    assert ok is False
    assert "query" in err


def test_wrong_type_fails():
    ok, err = toolshape.validate_arguments(
        SCHEMA,
        {"query": "noticias", "max_results": "tres"}
    )

    assert ok is False
    assert "max_results" in err


def test_boolean_is_not_integer():
    ok, err = toolshape.validate_arguments(
        {"properties": {"n": {"type": "integer"}}},
        {"n": True}
    )

    assert ok is False


def test_no_schema_always_passes():
    ok, err = toolshape.validate_arguments(None, {"cualquier": "cosa"})
    assert ok is True

    ok, err = toolshape.validate_arguments({}, {})
    assert ok is True

    ok, err = toolshape.validate_arguments("no-objeto", {})
    assert ok is True


def test_non_object_arguments_fails():
    ok, err = toolshape.validate_arguments(SCHEMA, "no-es-objeto")
    assert ok is False


def test_undeclared_property_tolerated():
    # Una propiedad no declarada en el esquema no se rechaza por ser
    # estricto; la tool decide al ejecutar.
    ok, _ = toolshape.validate_arguments(
        SCHEMA,
        {"query": "x", "otra_cosa": 123}
    )

    assert ok is True


def test_unknown_type_not_validated():
    # Un tipo no soportado no se valida (se tolera).
    ok, _ = toolshape.validate_arguments(
        {"properties": {"x": {"type": "weird"}}},
        {"x": "cualquier"}
    )

    assert ok is True


# ------------------------------------------------------------------
# Integración en ILUCore
# ------------------------------------------------------------------

class FakeProvider:
    def __init__(self, model_result):
        self.name = "fake"
        self.version = "0.0.1"
        self.model_result = model_result
        self.executed = []

    def generate(self, message, context=None, tools=None):
        return self.model_result


def make_core(monkeypatch, tmp_path, model_result):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_POOLED", raising=False)
    monkeypatch.delenv("ILU_AI_PROVIDER", raising=False)

    core = ILUCore()
    core.provider = FakeProvider(model_result)
    core.memory = MemoryStore(path=str(tmp_path / "data.json"))
    return core


def test_model_invalid_arguments_not_executed(monkeypatch, tmp_path):
    """Un tool_call con argumentos inválidos NO se ejecuta (fail-closed)."""
    model_result = {
        "type": "tool_call",
        "tool": "web_search",
        "arguments": {"max_results": 3},  # falta query (required)
        "reason": "buscar"
    }

    core = make_core(monkeypatch, tmp_path, model_result)

    result = core.process("busca algo")

    # Se rechaza de forma honesta (fail-closed): no se ejecuta nada.
    assert result["success"] is False
    assert result["intent"] == "tool_error"
    assert result["tool"] == "web_search"
    assert result["tool_result"]["validation"] == "failed"
    assert "query" in result["tool_result"]["error"]


def test_model_valid_arguments_execute(monkeypatch, tmp_path):
    """Un tool_call con argumentos válidos se ejecuta normalmente."""
    model_result = {
        "type": "tool_call",
        "tool": "web_search",
        "arguments": {"query": "noticias"},
        "reason": "buscar"
    }

    core = make_core(monkeypatch, tmp_path, model_result)

    result = core.process("busca noticias")

    assert result["success"] is True
    assert result["tool"] == "web_search"


def test_tool_without_schema_still_executes(monkeypatch, tmp_path):
    """Una tool sin esquema (o sin args) no se bloquea por validación."""
    model_result = {
        "type": "tool_call",
        "tool": "system_time",
        "arguments": {},
        "reason": "hora"
    }

    core = make_core(monkeypatch, tmp_path, model_result)

    result = core.process("¿qué hora es?")

    assert result["success"] is True
    assert result["tool"] == "system_time"
