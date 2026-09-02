"""
Historial auditado de acciones importantes de I.L.U.

Almacenamiento: archivo JSONL (una acción por línea).
Ruta por defecto: memory/audit.jsonl (gitignored).
Se puede cambiar con la variable de entorno ILU_AUDIT_PATH.

Reglas:
    - Nunca se registran argumentos ni valores sensibles.
    - Los campos cuyo nombre contenga indicadores de secreto
      (api_key, secret, token, password, key...) se enmascaran.
    - La escritura es best-effort: un fallo de auditoría no debe
      bloquear la operación principal de I.L.U.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path


SENSITIVE_KEYS = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "token",
    "key",
)


class AuditLog:

    def __init__(self, path=None):
        self.path = Path(
            path
            or os.environ.get("ILU_AUDIT_PATH")
            or "memory/audit.jsonl"
        )
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

    def record(self, actor="ilu", action="unknown", **fields):
        entry = {
            "timestamp": datetime.now(
                timezone.utc
            ).isoformat(),
            "actor": actor,
            "action": action,
        }
        entry.update(fields)
        self._sanitize(entry)

        try:
            with self.path.open(
                "a",
                encoding="utf-8"
            ) as file:
                file.write(
                    json.dumps(
                        entry,
                        ensure_ascii=False
                    ) + "\n"
                )
            return True

        except OSError:
            return False

    def _sanitize(self, entry):
        for key in list(entry):
            lowered = key.lower()

            if any(
                tag in lowered
                for tag in SENSITIVE_KEYS
            ):
                entry[key] = "***"

    def recent(self, limit=20):
        if not self.path.exists():
            return []

        try:
            lines = self.path.read_text(
                encoding="utf-8"
            ).splitlines()

        except OSError:
            return []

        entries = []

        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        return entries