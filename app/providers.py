import json
import os

import requests

from app import toolshape
from config.identity import ilu_system_prompt


class AIProvider:
    """
    Interfaz base para los proveedores de inteligencia de I.L.U.

    Los proveedores son motores que I.L.U. utiliza.
    La identidad de I.L.U. no depende del proveedor activo.
    """

    def __init__(self):
        self.name = "base"
        self.version = "0.7.0"

    def generate(self, message, context=None, tools=None):
        raise NotImplementedError(
            "El proveedor debe implementar generate()"
        )

    def _messages(self, message, context):
        """
        Mensajes de sistema y usuario compartidos por todos
        los proveedores.
        """

        return [
            {
                "role": "system",
                "content": ilu_system_prompt(context)
            },
            {
                "role": "user",
                "content": message
            }
        ]

    def _available_tools(self, tools):
        """
        Conjunto de nombres de herramientas permitidas.
        """

        return {
            tool.get("name")
            for tool in (tools or [])
            if isinstance(tool, dict)
            and isinstance(tool.get("name"), str)
        }

    def _extract_tool_call(self, content):
        """
        Reconoce una respuesta JSON de herramienta.

        Formato esperado:
            {"tool": str, "arguments": dict, "reason": str}
        """

        if not content:
            return None

        try:
            data = json.loads(content.strip())

        except (json.JSONDecodeError, TypeError):
            return None

        if not isinstance(data, dict):
            return None

        tool = data.get("tool")
        arguments = data.get("arguments")

        if not isinstance(tool, str):
            return None

        if not isinstance(arguments, dict):
            return None

        return {
            "tool": tool,
            "arguments": arguments,
            "reason": data.get("reason", "")
        }

    def _decide_result(self, message_data, available_tools):
        """
        Traduce la respuesta de un proveedor a la forma canónica de I.L.U.

        Orden de preferencia:
          1) tool-call NATIVO (Ollama u OpenAI-compat, vía toolshape):
             la más fiable, porque el proveedor la declara estructurada.
          2) formato heredado JSON-en-content (modelos sin tool calling).

        En ambos casos se aplica el mismo gateo por `available_tools`:
        una tool no permitida jamás se ejecuta (fail-closed), se responde
        como texto.
        """
        content = message_data.get("content", "")

        if content is None:
            # Sin contenido de texto (p. ej. un tool_call nativo denegado):
            # se trata como vacío, nunca como la cadena "None".
            content = ""
        elif not isinstance(content, str):
            content = str(content)

        content = content.strip()

        # 1) Tool calling nativo del proveedor.
        for call in toolshape.parse_tool_calls(message_data):
            if call["tool"] in available_tools:
                return {
                    "type": "tool_call",
                    "tool": call["tool"],
                    "arguments": call["arguments"],
                    "reason": call.get("reason", ""),
                }

            # No está permitida: fail-closed, se reporta como texto.
            if content:
                return {"type": "text", "content": content}

            return {
                "type": "text",
                "content": (
                    f"(El modelo solicitó la herramienta "
                    f"'{call['tool']}' pero no está disponible.)"
                ),
            }

        # 2) Formato heredado: JSON embebido en el content.
        tool_call = self._extract_tool_call(content)

        if (
            tool_call
            and tool_call["tool"] in available_tools
        ):
            return {
                "type": "tool_call",
                "tool": tool_call["tool"],
                "arguments": tool_call["arguments"],
                "reason": tool_call["reason"]
            }

        return {"type": "text", "content": content}


class LocalProvider(AIProvider):
    """
    Proveedor local mediante la API HTTP de Ollama.

    Se utiliza HTTP directamente en lugar de la librería
    ollama para reducir sobrecarga y mantener un control
    directo sobre la petición al servidor local.
    """

    def __init__(self):
        super().__init__()

        self.name = "ollama"
        self.version = "0.7.0"

        self.model = os.environ.get(
            "ILU_LOCAL_MODEL",
            "qwen2.5:0.5b-instruct"
        )

        self.base_url = os.environ.get(
            "ILU_OLLAMA_URL",
            "http://127.0.0.1:11434"
        ).rstrip("/")

        self.timeout = int(
            os.environ.get(
                "ILU_OLLAMA_TIMEOUT",
                "600"
            )
        )

    def generate(self, message, context=None, tools=None):
        available_tools = self._available_tools(
            tools
        )

        payload = {
            "model": self.model,
            "messages": self._messages(
                message,
                context
            ),
            "stream": False,
            "think": False,
            "options": {
                "num_predict": 128
            },
            "keep_alive": "5m"
        }

        # Herramientas en formato nativo "functions" (el que Ollama entiende)
        # -> el modelo puede responder con tool_calls estructurados.
        native_tools = toolshape.openai_functions(tools)

        if native_tools:
            payload["tools"] = native_tools

        url = f"{self.base_url}/api/chat"

        try:
            response = requests.post(
                url,
                json=payload,
                timeout=self.timeout
            )

            response.raise_for_status()

            result = response.json()

            message_data = result.get(
                "message",
                {}
            )

            return self._decide_result(
                message_data,
                available_tools
            )

        except requests.exceptions.Timeout:
            return {
                "type": "error",
                "content": (
                    "I.L.U. agotó el tiempo de espera "
                    "del modelo local."
                ),
                "detail": (
                    f"Timeout después de "
                    f"{self.timeout} segundos."
                )
            }

        except requests.exceptions.RequestException as error:
            return {
                "type": "error",
                "content": (
                    "I.L.U. no pudo comunicarse "
                    "con Ollama."
                ),
                "detail": str(error)
            }

        except Exception as error:
            return {
                "type": "error",
                "content": (
                    "I.L.U. no pudo procesar "
                    "la respuesta del modelo local."
                ),
                "detail": str(error)
            }


class OmniRouteProvider(AIProvider):
    """
    Proveedor mediante la API compatible con OpenAI de OmniRoute.

    OmniRoute expone un gateway local compatible con OpenAI
    (POST /v1/chat/completions) y se autentica con un
    Bearer token.

    La API key proviene de las variables de entorno:
        ILU_OMNIROUTE_API_KEY
    y jamás se registra en logs, mensajes de error ni código.
    """

    def __init__(self):
        super().__init__()

        self.name = "omniroute"
        self.version = "0.8.0"

        self.model = os.environ.get(
            "ILU_OMNIROUTE_MODEL",
            "openai/gpt-oss-120b"
        )

        self.base_url = os.environ.get(
            "ILU_OMNIROUTE_URL",
            "http://localhost:20128/v1"
        ).rstrip("/")

        # La clave viaja exclusivamente por variables de entorno.
        # Se admite ILU_OMNIROUTE_API_KEY (especificada) con
        # OMNIROUTE_API_KEY como respaldo para entornos actuales.
        self.api_key = (
            os.environ.get("ILU_OMNIROUTE_API_KEY")
            or os.environ.get("OMNIROUTE_API_KEY")
            or ""
        )

        self.timeout = 600

    def _auth_headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}"
        }

    def generate(self, message, context=None, tools=None):
        if not self.api_key:
            return {
                "type": "error",
                "content": (
                    "I.L.U. no puede usar OmniRoute: la variable "
                    "de entorno ILU_OMNIROUTE_API_KEY no está "
                    "configurada."
                ),
                "detail": "missing_api_key"
            }

        available_tools = self._available_tools(
            tools
        )

        payload = {
            "model": self.model,
            "messages": self._messages(
                message,
                context
            ),
            "stream": False
        }

        # Herramientas nativas en formato OpenAI function -> el modelo puede
        # responder con tool_calls estructurados (arguments como string JSON).
        native_tools = toolshape.openai_functions(tools)

        if native_tools:
            payload["tools"] = native_tools

        url = f"{self.base_url}/chat/completions"

        try:
            response = requests.post(
                url,
                json=payload,
                headers=self._auth_headers(),
                timeout=self.timeout
            )

            response.raise_for_status()

            result = response.json()

            choices = result.get("choices") or []

            message_data = (
                choices[0].get("message", {})
                if choices
                else {}
            )

            return self._decide_result(
                message_data,
                available_tools
            )

        except requests.exceptions.Timeout:
            return {
                "type": "error",
                "content": (
                    "I.L.U. agotó el tiempo de espera "
                    "del proveedor OmniRoute."
                ),
                "detail": (
                    f"Timeout después de "
                    f"{self.timeout} segundos."
                )
            }

        except requests.exceptions.RequestException as error:
            return {
                "type": "error",
                "content": (
                    "I.L.U. no pudo comunicarse "
                    "con OmniRoute."
                ),
                "detail": str(error)
            }

        except Exception as error:
            return {
                "type": "error",
                "content": (
                    "I.L.U. no pudo procesar "
                    "la respuesta de OmniRoute."
                ),
                "detail": str(error)
            }


class CloudProvider(AIProvider):
    """
    Interfaz para futuros modelos cloud.
    """

    def __init__(self):
        super().__init__()

        self.name = "cloud"
        self.version = "0.7.0"

    def generate(self, message, context=None, tools=None):
        return {
            "type": "text",
            "content": (
                "El proveedor cloud está preparado para "
                "incorporar un modelo de inteligencia."
            )
        }


def create_provider():
    provider_name = os.environ.get(
        "ILU_AI_PROVIDER",
        "local"
    ).lower()

    if provider_name == "omniroute":
        return OmniRouteProvider()

    if provider_name == "cloud":
        return CloudProvider()

    return LocalProvider()


class FallbackProvider(AIProvider):
    """
    Envuelve un proveedor PRIMARIO (p. ej. OmniRoute/cloud) y, si este
    devuelve un error de comunicación/configuración, delega en un
    proveedor de RESPALDO (p. ej. Ollama local).

    Objetivo: I.L.U. deja de depender de un único punto de fallo. Si el
    cloud está caído o sin clave, sigue respondiendo con el motor local.

    No cambia la arquitectura: la inteligencia propone, la compuerta
    decide, y aquí solo se elige QUÉ motor generó la propuesta.
    """

    def __init__(self, primary=None, fallback=None):
        super().__init__()

        self.primary = primary or LocalProvider()
        self.fallback = fallback or LocalProvider()

        self.name = self.primary.name
        self.version = self.primary.version

    def generate(self, message, context=None, tools=None):
        result = self.primary.generate(
            message,
            context=context,
            tools=tools
        )

        # Un error del primario (red caída, timeout, sin clave) dispara
        # el fallback al respaldo local.
        if isinstance(result, dict) and result.get("type") == "error":
            fallback_result = self.fallback.generate(
                message,
                context=context,
                tools=tools
            )

            if isinstance(fallback_result, dict):
                # Visible para el orquestador/API: qué motor respondió.
                fallback_result["fallback"] = True
                fallback_result["provider_used"] = self.fallback.name
                fallback_result["provider_used_version"] = (
                    self.fallback.version
                )

            return fallback_result

        if isinstance(result, dict):
            result.setdefault("provider_used", self.primary.name)
            result.setdefault("provider_used_version", self.primary.version)

        return result


def create_runtime_provider():
    """
    Proveedor que I.L.U. usa en ejecución.

    Igual que `create_provider()`, salvo que con `ILU_AI_PROVIDER=omniroute`
    devuelve un `FallbackProvider(OmniRoute, Local)`: si el cloud falla,
    cae en Ollama local. Para `local`/`cloud`/desconocido devuelve lo mismo
    que `create_provider()` (sin envolver).
    """
    provider_name = os.environ.get(
        "ILU_AI_PROVIDER",
        "local"
    ).lower()

    if provider_name == "omniroute":
        return FallbackProvider(
            primary=OmniRouteProvider(),
            fallback=LocalProvider()
        )

    return create_provider()