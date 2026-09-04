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

        # ---- Bloque 10: historial de conversación multi-turn ----
        self.conversations_path = os.environ.get(
            "ILU_CONVERSATIONS_PATH",
            "memory/conversations.jsonl"
        )

        self.history_turns = int(
            os.environ.get(
                "ILU_HISTORY_TURNS",
                "6"
            )
        )

        # ---- JARVIS Evolution: planificación y proactividad ----
        self.goals_path = os.environ.get(
            "ILU_GOALS_PATH",
            "memory/goals.jsonl"
        )

        self.proactivity_path = os.environ.get(
            "ILU_PROACTIVITY_PATH",
            "memory/proactivity.jsonl"
        )

        # ---- Bloque 13: ejecución real gateada (run_command / apps / media) ----
        # Ruta de la lista blanca de comandos/aplicaciones del mundo y los
        # confinamientos de ejecución (timeout y tamaño máximo de salida).
        self.run_commands_path = os.environ.get(
            "ILU_RUN_COMMANDS_PATH",
            "security/run_commands.json"
        )

        self.world_timeout = int(
            os.environ.get(
                "ILU_WORLD_TIMEOUT",
                "15"
            )
        )

        self.world_max_output = int(
            os.environ.get(
                "ILU_WORLD_MAX_OUTPUT",
                "8192"
            )
        )

        # ---- Bloque 8: sistema de autoridad / permisos ----
        # Unica variable que I.L.U. necesita conocer del mundo humano:
        # quién es el OWNER (autoridad raíz) en este dispositivo.
        self.owner_id = os.environ.get(
            "ILU_OWNER_ID",
            "owner"
        )

        # Rutas de los almacenes de seguridad (locales, gitignored).
        self.grants_path = os.environ.get(
            "ILU_GRANTS_PATH",
            "security/grants.jsonl"
        )

        self.policy_path = os.environ.get(
            "ILU_POLICY_PATH",
            "security/policy.json"
        )

        self.principals_path = os.environ.get(
            "ILU_PRINCIPALS_PATH",
            "security/principals.json"
        )

        self.emergency_path = os.environ.get(
            "ILU_EMERGENCY_PATH",
            "security/emergency.json"
        )

        self.authreq_path = os.environ.get(
            "ILU_AUTHREQ_PATH",
            "security/requests.jsonl"
        )

        self.devices_path = os.environ.get(
            "ILU_DEVICES_PATH",
            "security/devices.json"
        )

        self.device_key_path = os.environ.get(
            "ILU_DEVICE_KEY",
            "security/device.key"
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
            ),
            "owner": self.owner_id,
            "security": "enabled",
        }
