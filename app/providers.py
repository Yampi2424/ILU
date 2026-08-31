import json
import os

try:
    import ollama
except ImportError:
    ollama = None


class AIProvider:
    """
    Interfaz base para los proveedores de inteligencia de I.L.U.
    """

    def __init__(self):
        self.name = "base"
        self.version = "0.5.0"

    def generate(self, message, context=None, tools=None):
        raise NotImplementedError(
            "El proveedor debe implementar generate()"
        )


class LocalProvider(AIProvider):
    """
    Proveedor local mediante Ollama.

    Puede generar respuestas normales y recibir
    información estructurada sobre las herramientas
    disponibles.
    """

    def __init__(self):
        super().__init__()

        self.name = "ollama"
        self.version = "0.5.0"

        self.model = os.environ.get(
            "ILU_LOCAL_MODEL",
            "qwen2.5:0.5b-instruct"
        )

    def _build_prompt(self, message, context, tools):
        context = context or []
        tools = tools or []

        prompt = (
            "Eres I.L.U., Inteligencia Local Unificada.\n"
            "Responde en español de forma clara, directa y útil.\n"
            "No inventes herramientas ni acciones.\n\n"
        )

        if context:
            prompt += "Memoria relevante:\n"

            for item in context:
                content = item.get("content")

                if content:
                    prompt += f"- {content}\n"

            prompt += "\n"

        if tools:
            prompt += "Herramientas disponibles:\n"

            for tool in tools:
                prompt += (
                    f"- {tool.get('name')}: "
                    f"{tool.get('description')}\n"
                )

            prompt += (
                "\nSi necesitas una herramienta, responde "
                "EXACTAMENTE con JSON usando este formato:\n"
                '{"tool":"nombre","arguments":{},"reason":"motivo"}\n\n'
                "Si no necesitas una herramienta, responde "
                "normalmente en español.\n\n"
            )

        prompt += (
            "Mensaje del usuario:\n"
            f"{message}\n\n"
            "Respuesta:"
        )

        return prompt

    def _extract_tool_call(self, content):
        if not content:
            return None

        text = content.strip()

        try:
            data = json.loads(text)

            if (
                isinstance(data, dict)
                and isinstance(data.get("tool"), str)
                and isinstance(data.get("arguments"), dict)
            ):
                return {
                    "tool": data["tool"],
                    "arguments": data["arguments"],
                    "reason": data.get("reason", "")
                }

        except json.JSONDecodeError:
            pass

        return None

    def generate(self, message, context=None, tools=None):
        if ollama is None:
            return {
                "type": "text",
                "content": (
                    "I.L.U. está preparada para utilizar "
                    "un modelo local, pero Ollama no está instalado."
                )
            }

        prompt = self._build_prompt(
            message,
            context,
            tools
        )

        try:
            result = ollama.chat(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )

            content = result["message"]["content"].strip()

            tool_call = self._extract_tool_call(
                content
            )

            if tool_call:
                return {
                    "type": "tool_call",
                    "tool": tool_call["tool"],
                    "arguments": tool_call["arguments"],
                    "reason": tool_call["reason"]
                }

            return {
                "type": "text",
                "content": content
            }

        except Exception as error:
            return {
                "type": "error",
                "content": (
                    "I.L.U. no pudo obtener respuesta "
                    "del modelo local."
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
        self.version = "0.5.0"

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

    if provider_name == "cloud":
        return CloudProvider()

    return LocalProvider()
