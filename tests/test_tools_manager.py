import pytest

from tools.call import ToolCall
from tools.manager import ToolManager
from tools import create_tool_manager


def test_manager_starts_empty():
    manager = ToolManager()

    assert manager.list_tools() == []
    assert not manager.has_tool("system_time")


def test_manager_register_and_execute():
    manager = ToolManager()

    manager.register(
        name="saluda",
        description="Devuelve un saludo",
        handler=lambda: "hola",
        permission="safe"
    )

    assert manager.has_tool("saluda")
    assert manager.list_tools() == [
        {
            "name": "saluda",
            "description": "Devuelve un saludo",
            "permission": "safe"
        }
    ]

    result = manager.execute("saluda")

    assert result == {
        "success": True,
        "tool": "saluda",
        "result": "hola"
    }


def test_manager_get_permission():
    manager = ToolManager()

    manager.register(
        name="segura",
        description="Segura",
        handler=lambda: "ok",
        permission="safe"
    )

    manager.register(
        name="pide",
        description="Requiere autorización",
        handler=lambda: "no",
        permission="ask"
    )

    manager.register(
        name="prohibida",
        description="Prohibida",
        handler=lambda: "no",
        permission="blocked"
    )

    assert manager.get_permission("segura") == "safe"
    assert manager.get_permission("pide") == "ask"
    assert manager.get_permission("prohibida") == "blocked"
    assert manager.get_permission("no-existe") is None


def test_manager_register_rejects_empty_name():
    manager = ToolManager()

    with pytest.raises(ValueError):
        manager.register("", "desc", lambda: None)


def test_manager_register_rejects_non_callable():
    manager = ToolManager()

    with pytest.raises(ValueError):
        manager.register("x", "desc", "no-es-callable")


def test_manager_blocked_tool_not_executed():
    manager = ToolManager()

    manager.register(
        name="peligrosa",
        description="Herramienta bloqueada",
        handler=lambda: "no debería correr",
        permission="blocked"
    )

    result = manager.execute("peligrosa")

    assert result["success"] is False
    assert result["error"] == "tool_blocked"


def test_manager_unknown_tool():
    manager = ToolManager()

    result = manager.execute("no-existe")

    assert result["success"] is False
    assert result["error"] == "tool_not_found"


def test_manager_handler_error_reported():
    manager = ToolManager()

    def handler():
        raise RuntimeError("falló el handler")

    manager.register("falla", "Falla siempre", handler)

    result = manager.execute("falla")

    assert result["success"] is False
    assert result["error"] == "tool_execution_failed"


def test_create_tool_manager_registers_system_time():
    manager = create_tool_manager()

    assert manager.has_tool("system_time")

    result = manager.execute("system_time")

    assert result["success"] is True
    assert "datetime" in result["result"]


def test_tool_call_value_object():
    call = ToolCall(tool="system_time", arguments={}, reason="hora")

    assert call.to_dict() == {
        "tool": "system_time",
        "arguments": {},
        "reason": "hora"
    }

def test_execute_rejects_invalid_arguments_centrally():
    """
    D-5 — La validación de esquema vive en ToolManager.execute: cualquier
    camino (core o subagente) rechaza argumentos inválidos sin ejecutar.
    """
    manager = ToolManager()
    calls = []

    def handler(**kwargs):
        calls.append(kwargs)
        return "ok"

    manager.register(
        name="escribir",
        description="Requiere path y content",
        handler=handler,
        permission="safe",
        schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"}
            },
            "required": ["path", "content"]
        }
    )

    # Falta 'content' -> rechazado, el handler NO se ejecuta.
    result = manager.execute("escribir", path="a.txt")
    assert result["success"] is False
    assert result["validation"] == "failed"
    assert "missing_required_argument" in result["error"]
    assert calls == []

    # Tipo inválido -> rechazado.
    result = manager.execute("escribir", path="a.txt", content=123)
    assert result["success"] is False
    assert result["validation"] == "failed"

    # Argumentos válidos -> el handler sí se ejecuta.
    result = manager.execute("escribir", path="a.txt", content="hola")
    assert result["success"] is True
    assert calls == [{"path": "a.txt", "content": "hola"}]
