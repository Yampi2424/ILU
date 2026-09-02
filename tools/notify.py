"""
Herramienta de notificación local de I.L.U.

Inocua, local y sin Internet: escribe una entrada en un log JSONL
(`memory/notifications.jsonl`, `ILU_NOTIFICATIONS_PATH` para cambiarlo).
Es la vía honesta para que I.L.U. "avise" de algo al usuario sin depender
de un servicio externo; un cliente/UI puede leer ese archivo. Permiso
"safe" (no toca nada y deja rastro auditable).
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _notifications_file():
    raw = os.environ.get(
        "ILU_NOTIFICATIONS_PATH",
        "memory/notifications.jsonl"
    )

    return Path(raw)


def notify(message=None, level="info"):
    """
    Registra una notificación local dirigida al usuario.

    Devuelve {"success", "message", "stored"} o un error estructurado
    (message_required).
    """
    message = (message or "").strip()

    if not message:
        return {"success": False, "error": "message_required"}

    path = _notifications_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "message": message,
    }

    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return {
        "success": True,
        "message": message,
        "stored": str(path),
    }