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

        self.omniroute_url = os.environ.get(
            "ILU_OMNIROUTE_URL",
            "http://localhost:20128/v1"
        ).rstrip("/")

        self.omniroute_api_key = os.environ.get(
            "ILU_OMNIROUTE_API_KEY",
            ""
        )

        self.omniroute_model = os.environ.get(
            "ILU_OMNIROUTE_MODEL",
            "openai/gpt-oss-120b"
        )

        self.autonomy_level = os.environ.get(
            "ILU_AUTONOMY",
            "assisted"
        ).lower()

        self.tasks_path = os.environ.get(
            "ILU_TASKS_PATH",
            "memory/tasks.json"
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
            "autonomy": self.autonomy_level,
            "database": (
                "configured"
                if self.has_database
                else "local"
            )
        }
