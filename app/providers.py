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
        self.version = "0.2.0"

    def generate(self, message, context=None):
        raise NotImplementedError(
            "El proveedor debe implementar generate()"
        )


class LocalProvider(AIProvider):
    """
    Proveedor de IA local mediante Ollama.

    Si Ollama o el modelo no están disponibles,
    I.L.U. utiliza una respuesta de respaldo.
    """

    def __init__(self):
        super().__init__()

        self.name = "ollama"
        self.version = "0.2.0"

        self.model = os.environ.get(
            "ILU_LOCAL_MODEL",
            "llama3.2:1b-instruct-q3_K_M"
        )

    def generate(self, message, context=None):
        context = context or []

        if ollama is None:
            return (
                "I.L.U. está preparada para utilizar "
                "un modelo local, pero el cliente Ollama "
                "todavía no está instalado."
            )

        context_text = ""

        for item in context:
            content = item.get("content")

            if content:
                context_text += f"- {content}\n"

        prompt = (
            "Eres I.L.U., Inteligencia Local Unificada.\n"
            "Responde en español de forma clara y directa.\n\n"
        )

        if context_text:
            prompt += (
                "Contexto recuperado de la memoria:\n"
                f"{context_text}\n"
            )

        prompt += (
            "Mensaje del usuario:\n"
            f"{message}\n\n"
            "Respuesta:"
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

            return result["message"]["content"]

        except Exception:
            return (
                "I.L.U. recibió la solicitud, pero el modelo "
                f"local '{self.model}' no está disponible todavía."
            )


class CloudProvider(AIProvider):
    """
    Interfaz para futuros modelos cloud.
    """

    def __init__(self):
        super().__init__()

        self.name = "cloud"
        self.version = "0.2.0"

    def generate(self, message, context=None):
        return (
            "El proveedor cloud está preparado para "
            "incorporar un modelo de inteligencia."
        )


def create_provider():
    provider_name = os.environ.get(
        "ILU_AI_PROVIDER",
        "local"
    ).lower()

    if provider_name == "cloud":
        return CloudProvider()

    return LocalProvider()
