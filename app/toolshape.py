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
            # Bloque 11: si la tool declara un JSON-schema (campo
            # "schema"), se emite tal cual; si no, se mantiene el objeto
            # vacío (retrocompatible con el Bloque 9).
            "parameters": _schema_or_empty(tool)
        }

        description = tool.get("description")

        if isinstance(description, str) and description:
            function["description"] = description

        out.append({"type": "function", "function": function})

    return out


def _schema_or_empty(tool):
    """
    Devuelve el JSON-schema de la tool, o un objeto vacío si no tiene.

    El esquema, si existe, DEBE ser un objeto; cualquier otro valor se
    descarta para no enviar JSON inválido al proveedor.
    """
    schema = tool.get("schema")

    if isinstance(schema, dict) and schema:
        return schema

    return {"type": "object", "properties": {}}


# Tipos JSON-schema que I.L.U. entiende y cómo comprobarlos.
_VALIDATORS = {
    "string": lambda value: isinstance(value, str),
    "boolean": lambda value: isinstance(value, bool),
    "integer": lambda value: (
        isinstance(value, int)
        and not isinstance(value, bool)
    ),
    "number": lambda value: (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
    ),
}


def validate_arguments(schema, arguments):
    """
    Valida `arguments` contra un JSON-schema básico (Bloque 11).

    Soporta: type=object, `required` y `properties` con tipos
    string/boolean/integer/number. Devuelve (ok, error): ok=True si los
    argumentos pasan; si no, ok=False con un error legible para rechazar
    de forma honesta (fail-closed) sin ejecutar.

    Una tool SIN esquema (o con esquema no-objeto) siempre pasa: no hay
    nada que validar (retrocompatibilidad).
    """
    if not isinstance(schema, dict) or not schema:
        return True, ""

    if not isinstance(arguments, dict):
        return (
            False,
            "arguments_must_be_object"
        )

    # 1) Propiedades requeridas.
    required = schema.get("required", [])

    if isinstance(required, list):
        for prop in required:
            if prop not in arguments:
                return (
                    False,
                    f"missing_required_argument:{prop}"
                )

    # 2) Tipos de las propiedades presentes.
    properties = schema.get("properties", {})

    if not isinstance(properties, dict):
        properties = {}

    for name, value in arguments.items():
        prop_schema = properties.get(name)

        if not isinstance(prop_schema, dict):
            # Propiedad no declarada en el esquema: se tolera (no se
            # rechaza por ser estricto; la tool decide al ejecutar).
            continue

        prop_type = prop_schema.get("type")

        validator = _VALIDATORS.get(prop_type)

        if validator is not None and not validator(value):
            return (
                False,
                f"invalid_argument_type:{name}"
            )

    return True, ""


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
