import os


class AIProvider:
    """
    Interfaz base para los proveedores de inteligencia de I.L.U.
    """

    def __init__(self):
        self.name = "base"
        self.version = "0.1.0"

    def generate(self, message, context=None):
        raise NotImplementedError(
            "El proveedor debe implementar generate()"
        )


class LocalProvider(AIProvider):
    """
    Proveedor local liviano.

    No requiere un modelo pesado.
    Sirve como proveedor de respaldo mientras
    se incorpora el modelo de IA definitivo.
    """

    def __init__(self):
        super().__init__()
        self.name = "local"
        self.version = "0.1.0"

    def generate(self, message, context=None):
        context = context or []

        if context:
            return (
                "Procesamiento local realizado. "
                "I.L.U. dispone de contexto relacionado."
            )

        return (
            "Procesamiento local realizado. "
            "I.L.U. está preparada para utilizar un modelo de IA."
        )


class CloudProvider(AIProvider):
    """
    Punto de entrada para futuros modelos en la nube.

    Actualmente funciona como respaldo seguro y no realiza
    llamadas externas.
    """

    def __init__(self):
        super().__init__()
        self.name = "cloud"
        self.version = "0.1.0"

    def generate(self, message, context=None):
        return (
            "El proveedor cloud está configurado como interfaz "
            "y preparado para incorporar un modelo."
        )


def create_provider():
    """
    Selecciona el proveedor mediante ILU_AI_PROVIDER.

    Valores:
        local
        cloud

    Por defecto:
        local
    """

    provider_name = os.environ.get(
        "ILU_AI_PROVIDER",
        "local"
    ).lower()

    if provider_name == "cloud":
        return CloudProvider()

    return LocalProvider()
