"""
Bloque 11 — JSON-schema por herramienta.

`ToolManager` guarda un esquema opcional por tool y `openai_functions`
lo emite como `parameters` reales en el array `tools` nativo. Una tool
sin esquema sigue emitiendo `properties` vacío (retrocompatible).
"""

import app.toolshape as toolshape
from tools.manager import ToolManager


def _schema_tool():
    return {
        "name": "web_search",
        "description": "Busca en la web",
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"}
            },
            "required": ["query"]
        }
    }


def test_openai_functions_emits_schema():
    native = toolshape.openai_functions([_schema_tool()])

    assert len(native) == 1

    function = native[0]["function"]

    assert function["name"] == "web_search"
    assert function["parameters"] == {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer"}
        },
        "required": ["query"]
    }


def test_openai_functions_no_schema_emits_empty():
    tool = {"name": "system_time", "description": "hora"}

    native = toolshape.openai_functions([tool])

    assert native[0]["function"]["parameters"] == {
        "type": "object",
        "properties": {}
    }


def test_openai_functions_invalid_schema_ignored():
    # Un "schema" que no es dict se descarta -> properties vacío.
    tool = {
        "name": "raro",
        "schema": "no-es-objeto"
    }

    native = toolshape.openai_functions([tool])

    assert native[0]["function"]["parameters"] == {
        "type": "object",
        "properties": {}
    }


def test_manager_stores_and_returns_schema():
    manager = ToolManager()

    schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"]
    }

    manager.register(
        name="read_file",
        description="lee",
        handler=lambda: "ok",
        schema=schema
    )

    assert manager.get_schema("read_file") == schema
    assert manager.get_schema("no-existe") is None


def test_manager_no_schema_is_none():
    manager = ToolManager()

    manager.register(
        name="plain",
        description="sin esquema",
        handler=lambda: "ok"
    )

    assert manager.get_schema("plain") is None


def test_list_tools_public_is_retrocompatible():
    """list_tools() no expone schema (preserva el contrato del B1-B10)."""
    manager = ToolManager()

    manager.register(
        name="t1",
        description="uno",
        handler=lambda: "ok",
        schema={"type": "object", "properties": {}}
    )

    public = manager.list_tools()

    assert public == [
        {
            "name": "t1",
            "description": "uno",
            "permission": "safe"
        }
    ]


def test_list_tools_full_includes_schema():
    manager = ToolManager()

    schema = {"type": "object", "properties": {}}

    manager.register(
        name="t1",
        description="uno",
        handler=lambda: "ok",
        schema=schema
    )

    full = manager.list_tools_full()

    assert full[0]["schema"] == schema


def test_create_tool_manager_has_schemas():
    """Las tools del panel declaran esquema (B11)."""
    from tools import create_tool_manager

    manager = create_tool_manager()

    # web_search requiere query.
    assert manager.get_schema("web_search")["required"] == ["query"]

    # read_file requiere path.
    assert manager.get_schema("read_file")["required"] == ["path"]

    # system_time no requiere nada (esquema sin "required").
    assert manager.get_schema("system_time").get("required", []) == []
