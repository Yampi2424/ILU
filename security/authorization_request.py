"""
Solicitudes de autorización de I.L.U. (Bloque 8).

Cuando I.L.U. necesita un permiso que no posee, detiene esa parte de la
tarea y dice "Necesito autorización para realizar X". La respuesta humana
(sí / no / sí solo esta acción / durante una hora / para el proyecto...) se
convierte en un grant explícito y auditable.

La tarea que espera una autorización queda PAUSADA y registra exactamente
qué autorización necesita; otras tareas independientes continúan.
"""

import json
import os
import secrets
import time
from datetime import datetime, timezone


def _now():
    """
    Marca de tiempo con precisión de microsegundos (UTC).

    Resolución de segundo (strftime) hacía que dos solicitudes abiertas en
    el mismo segundo compartieran created_at y la ordenación "más reciente
    primero" de list() fuera NO determinista. La precisión de microsegundos
    hace que el orden de apertura sea siempre resolubrable.
    """
    return datetime.now(timezone.utc).isoformat()


def _new_request_id():
    return "req_" + secrets.token_hex(4)


# Alias público, evitando colisión con el argumento `request_id`.
request_id = _new_request_id


class AuthorizationRequired(Exception):
    """
    Se lanza cuando una operación (p. ej. una tool dentro de una tarea en
    segundo plano) requiere una autorización que I.L.U. no posee. El
    orquestador la atrapa, pausa la tarea y abre una solicitud.
    """

    def __init__(self, capability, reason="", task_id=None, scope=None):
        super().__init__(capability)
        self.capability = capability
        self.reason = reason
        self.task_id = task_id
        self.scope = scope or {}


class AuthorizationRequest:
    VALID_STATUS = ("open", "granted", "denied", "expired")

    def __init__(
        self,
        capability,
        reason="",
        principal="owner",
        task_id=None,
        scope=None,
        request_id=None
    ):
        self.key = request_id or _new_request_id()
        self.capability = capability
        self.reason = reason
        self.principal = principal
        self.task_id = task_id
        self.scope = scope or {}
        self.status = "open"
        self.created_at = _now()
        self.resolved_at = None
        self.resolved_by = None
        self.grant_id = None

    def resolve(self, status, actor, grant_id=None, note=""):
        if self.status != "open":
            return False

        if status not in ("granted", "denied", "expired"):
            return False

        self.status = status
        self.resolved_at = _now()
        self.resolved_by = actor
        self.grant_id = grant_id
        self.note = note
        return True

    def to_dict(self):
        return {
            "request_id": self.key,
            "capability": self.capability,
            "reason": self.reason,
            "principal": self.principal,
            "task_id": self.task_id,
            "scope": self.scope,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "resolved_by": self.resolved_by,
            "grant_id": self.grant_id,
            "note": getattr(self, "note", ""),
        }

    @classmethod
    def from_dict(cls, data):
        request = cls(
            capability=data.get("capability", ""),
            reason=data.get("reason", ""),
            principal=data.get("principal", "owner"),
            task_id=data.get("task_id"),
            scope=data.get("scope", {}),
            request_id=data.get("request_id"),
        )
        request.status = data.get("status", "open")
        request.created_at = data.get("created_at", request.created_at)
        request.resolved_at = data.get("resolved_at")
        request.resolved_by = data.get("resolved_by")
        request.grant_id = data.get("grant_id")
        request.note = data.get("note", "")
        return request


class AuthorizationRequestStore:
    """
    Registro persistente (JSONL, gitignored) de solicitudes de autorización.
    Es el puente entre "I.L.U. necesita permiso" y el grant emitido.
    """

    def __init__(self, path=None):
        self.path = path or os.environ.get(
            "ILU_AUTHREQ_PATH",
            "security/requests.jsonl"
        )
        self.requests = {}
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()

                    if not line:
                        continue

                    try:
                        request = AuthorizationRequest.from_dict(
                            json.loads(line)
                        )
                        self.requests[request.key] = request
                    except (json.JSONDecodeError, ValueError):
                        continue
        except OSError:
            pass

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                for key in sorted(self.requests.keys()):
                    handle.write(
                        json.dumps(
                            self.requests[key].to_dict(),
                            ensure_ascii=False
                        ) + "\n"
                    )
            return True
        except OSError:
            return False

    def open(
        self,
        capability,
        reason="",
        principal="owner",
        task_id=None,
        scope=None
    ):
        request = AuthorizationRequest(
            capability=capability,
            reason=reason,
            principal=principal,
            task_id=task_id,
            scope=scope or {},
        )
        self.requests[request.key] = request
        self._save()
        return request

    def get(self, request_id):
        return self.requests.get(request_id)

    def resolve(self, request_id, status, actor, grant_id=None, note=""):
        request = self.get(request_id)

        if request is None:
            return None

        # La resolución es de UNA sola vez: si la solicitud ya no está
        # abierta (granted/denied/expired), no se vuelve a resolver y se
        # devuelve None para señalarlo.
        if not request.resolve(status, actor, grant_id=grant_id, note=note):
            return None

        self._save()
        return request

    def pending(self):
        return [
            request.to_dict()
            for request in self.requests.values()
            if request.status == "open"
        ]

    def list(self, limit=200):
        items = list(self.requests.values())
        items.sort(key=lambda r: r.created_at, reverse=True)
        return [r.to_dict() for r in items[:limit]]