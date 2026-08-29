import os


class ILUSettings:
    """
    Configuración central de I.L.U.
    """

    def __init__(self):
        self.name = "I.L.U."

        self.version = os.environ.get(
            "ILU_VERSION",
            "0.8.0"
        )

        self.provider = os.environ.get(
            "ILU_AI_PROVIDER",
            "local"
        ).lower()

        self.environment = os.environ.get(
            "ILU_ENV",
            "production"
        ).lower()

        self.memory_mode = os.environ.get(
            "ILU_MEMORY_MODE",
            "auto"
        ).lower()

        self.database_url = os.environ.get(
            "DATABASE_URL"
        )

    @property
    def is_cloud(self):
        return self.environment == "production"

    @property
    def has_database(self):
        return bool(self.database_url)

    def summary(self):
        return {
            "name": self.name,
            "version": self.version,
            "provider": self.provider,
            "environment": self.environment,
            "memory_mode": self.memory_mode,
            "database": (
                "configured"
                if self.has_database
                else "local"
            )
        }
