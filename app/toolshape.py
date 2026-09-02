"""
Traducción de tool-calls entre proveedores (Bloque 9).

I.L.U. habla UNA forma canónica de herramienta:
    [{"name": ..., "description": ..., "permission": ...}]   (lista interna)
    {"tool": ..., "arguments": {...}, "reason": ...}          (tool_call canónico)

y esta capa la traduce al wire-format nativo de cada proveedor:

  - Ollama nativo y OpenAI-compat (OmniRoute) comparten el MISMO esquema
    "functions":
        tools = [{"type": "function", "function": {name, description,
                                                       parameters}}]
        assistant message.tool_calls = [{function: {name, arguments}}]

  - La única diferencia real está en el tipo de `function.arguments`:
      * Ollama nativo  -> JSON ya parseado (dict).
      * OpenAI-compat  -> STRING JSON (a veces "null" o ausente).

Esta capa normaliza ambas variantes a la forma canónica, de modo que los
proveedores no necesitan saber con cuál de las dos lidiaron.
"""

import json


def openai_functions(tools):
    """
    Convierte la lista interna de tools de I.L.U. al array "tools" en
    formato OpenAI function (el que usan tanto Ollama como OmniRoute).
    Devuelve [] si no hay herramientas nombradas; se puede omitir del
    payload cuando esté vacío.
    """
    out = []

    for tool in tools or []:
        if not isinstance(tool, dict):
            continue

        name = tool.get("name")

        if not isinstance(name, str) or not name:
            continue

        function = {
            "name": name,
            "parameters": {"type": "object", "properties": {}}
        }

        description = tool.get("description")

        if isinstance(description, str) and description:
            function["description"] = description

        out.append({"type": "function", "function": function})

    return out


def _as_dict(raw):
    """Normaliza un `arguments` de tool call (dict | str | None) a dict."""
    if raw is None:
        return {}

    if isinstance(raw, dict):
        return raw

    if isinstance(raw, str):
        text = raw.strip()

        if not text:
            return {}

        # OpenAI-compat entrega arguments como STRING JSON; a veces el
        # string es "null" o un valor no-objeto (lo toleramos).
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return {}

        return data if isinstance(data, dict) else {}

    return {}


def parse_tool_calls(message):
    """
    Extrae los tool_calls NATIVOS de un mensaje de assistant.

    Entrada: el dict `message` de la respuesta (Ollama u OpenAI-compat).
    Salida:  [] de dicts canónicos {"tool", "arguments", "reason", "id"}.
    Devuelve [] cuando no hay tool_calls nativos (p. ej. respuesta de
    texto o del formato heredado JSON-en-content).
    """
    if not isinstance(message, dict):
        return []

    calls = message.get("tool_calls")

    if not calls:
        return []

    result = []

    for tc in calls:
        if not isinstance(tc, dict):
            continue

        function = tc.get("function")

        if not isinstance(function, dict):
            continue

        name = function.get("name")

        if not isinstance(name, str) or not name:
            continue

        # "index" es específico de Ollama; "id" de OpenAI-compat. Ambos
        # son opcionales: se conservan si vienen, sin necesidad de unirse.
        result.append({
            "tool": name,
            "arguments": _as_dict(function.get("arguments")),
            "reason": "",
            "id": tc.get("id") if isinstance(tc.get("id"), str) else "",
        })

    return result
