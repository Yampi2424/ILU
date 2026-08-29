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
    Optimizado para equipos con recursos limitados.
    """

    def __init__(self):
        super().__init__()

        self.name = "ollama"
        self.version = "0.4.0"

        self.model = os.environ.get(
            "ILU_LOCAL_MODEL",
            "qwen2.5:0.5b-instruct"
        )

        self.host = os.environ.get(
            "OLLAMA_HOST",
            "http://127.0.0.1:11434"
        )

        self.client = None

        if ollama is not None:
            self.client = ollama.Client(
                host=self.host,
                timeout=300
            )

    def generate(self, message, context=None):
        context = context or []

        if self.client is None:
            return (
                "I.L.U. no puede utilizar el modelo local "
                "porque el cliente Ollama no está instalado."
            )

        context_text = ""

        for item in context:
            if isinstance(item, dict):
                content = item.get("content")

                if content:
                    context_text += f"- {content}\n"

        prompt = (
            "Eres I.L.U., Inteligencia Local Unificada.\n"
            "Eres el cerebro local de una arquitectura de "
            "inteligencia ligera.\n"
            "Responde siempre en español.\n"
            "Sé clara, directa y útil.\n\n"
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
            result = self.client.chat(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                options={
                    "num_ctx": 4096,
                    "temperature": 0.7
                }
            )

            return result["message"]["content"].strip()

        except Exception as error:
            return (
                "I.L.U. no pudo obtener respuesta del modelo local. "
                f"Detalle: {error}"
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
