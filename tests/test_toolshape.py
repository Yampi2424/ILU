"""
Bloque 9 — Traducción de tool-shapes entre proveedores.

I.L.U. habla una forma canónica y `app/toolshape.py` la traduce al
wire-format de Ollama nativo y OpenAI-compat (OmniRoute). La única
diferencia real entre proveedores está en el tipo de `function.arguments`
(dict en Ollama; string JSON en OpenAI-compat), que esta capa normaliza.
"""

import app.toolshape as toolshape


def test_openai_functions_serializes_ilu_tools():
    tools = [
        {"name": "system_time", "description": "da la hora",
         "permission": "safe"},
        {"name": "write_file", "description": "escribe archivo",
         "permission": "ask"},
    ]

    native = toolshape.openai_functions(tools)

    assert len(native) == 2
    assert native[0] == {
        "type": "function",
        "function": {
            "name": "system_time",
            "description": "da la hora",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_openai_functions_skips_unnamed():
    assert toolshape.openai_functions([{}, {"name": ""}, None]) == []
    assert toolshape.openai_functions([]) == []
    assert toolshape.openai_functions(None) == []


def test_parse_ollama_native_arguments_object():
    # Ollama nativo entrega function.arguments como OBJETO parseado.
    message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "function": {
                    "name": "system_time",
                    "arguments": {"tz": "UTC"},
                },
                "index": 0,
            }
        ],
    }

    calls = toolshape.parse_tool_calls(message)

    assert len(calls) == 1
    assert calls[0]["tool"] == "system_time"
    assert calls[0]["arguments"] == {"tz": "UTC"}


def test_parse_openai_arguments_string():
    # OpenAI-compat entrega function.arguments como STRING JSON.
    message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_abc",
                "type": "function",
                "function": {
                    "name": "web_search",
                    "arguments": '{"query": "noticias", "limit": 3}',
                },
            }
        ],
    }

    calls = toolshape.parse_tool_calls(message)

    assert len(calls) == 1
    assert calls[0]["tool"] == "web_search"
    assert calls[0]["arguments"] == {"query": "noticias", "limit": 3}
    assert calls[0]["id"] == "call_abc"


def test_parse_openai_null_or_garbage_arguments():
    # "null", string vacío o JSON inválido -> argumentos vacíos (tolerado).
    message = {
        "tool_calls": [
            {"function": {"name": "t1", "arguments": "null"}},
            {"function": {"name": "t2", "arguments": ""}},
            {"function": {"name": "t3", "arguments": "no-es-json"}},
            {"function": {"name": "t4"}},
        ]
    }

    calls = toolshape.parse_tool_calls(message)

    assert all(call["arguments"] == {} for call in calls)


def test_parse_no_tool_calls_returns_empty():
    assert toolshape.parse_tool_calls({}) == []
    assert toolshape.parse_tool_calls({"content": "hola"}) == []
    assert toolshape.parse_tool_calls(None) == []
    assert toolshape.parse_tool_calls("texto") == []


def test_parse_skips_malformed_entries():
    message = {
        "tool_calls": [
            {},                       # sin function
            {"function": {}},          # sin name
            {"function": {"name": "ok", "arguments": {"x": 1}}},  # válido
            "basura",                  # no dict
        ]
    }

    calls = toolshape.parse_tool_calls(message)

    assert len(calls) == 1
    assert calls[0]["tool"] == "ok"


def test_round_trip_ilu_tools_to_native_and_back():
    """Los nombres que I.L.U. lista sobreviven a la serialización nativa."""
    tools = [{"name": "notify", "description": "notifica",
              "permission": "safe"}]

    native = toolshape.openai_functions(tools)
    names = [f["function"]["name"] for f in native]

    assert names == ["notify"]