"""
Modelo de una autorización (Grant) de I.L.U.

Una autorización es una entidad explícita y auditable. Contiene como
mínimo: quién concedió, a quién, qué autoridad concede, qué
capacidad/herramienta/acción permite, cuándo se concedió, cuándo expira,
estado, motivo, alcance, origen y auditoría asociada.

IMPORTANTE: ningún permiso asume permanencia. Todo grant debe tener una
fecha de expiración o un número máximo de usos; la única vía a un permiso
"indefinido" es un `indefinite=True` emitido explícitamente por el owner
(y siempre revocable inmediatamente y sujeto a revisión de policy).
"""

import json
import secrets
import time


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_grant_id():
    """Identificador corto y único de un grant (prepara sync futura)."""
    return "gr_" + secrets.token_hex(4)


# Alias público: la función generadora se referencia internamente como
# _new_grant_id para no colisionar con el argumento `grant_id` de Grant.
grant_id = _new_grant_id


class Grant:
    """
    Autorización explícita y auditable de una capacidad a un destinatario.

    Estados: active -> used | expired | revoked  (revoked no se reactiva).
    """

    VALID_STATUS = ("active", "used", "expired", "revoked")

    VALID_LEVELS = (
        "execution",          # permite ejecutar una herramienta/acción
        "authority",          # permite conceder/revocar permisos y cambiar autonomía
        "policy_modification",# permite modificar reglas
        "device_register",    # permite registrar un dispositivo
        "emergency",          # permite activar/consultar protocolos de emergencia
    )

    VALID_SCOPES = (
        "single_action",
        "task",
        "duration",
        "tool",
        "project",
        "device",
        "context",
    )

    def __init__(
        self,
        capability,
        grantor,
        grantee="ilu",
        level="execution",
        reason="",
        origin="user_request",
        scope_type="single_action",
        task_id=None,
        project=None,
        context=None,
        device_id=None,
        max_uses=None,
        expires_at=None,
        indefinite=False,
        required_verification="low",
        grant_id=None
    ):
        if level not in self.VALID_LEVELS:
            raise ValueError("invalid_grant_level")

        if scope_type not in self.VALID_SCOPES:
            raise ValueError("invalid_grant_scope")

        if not capability or not isinstance(capability, str):
            raise ValueError("capability_required")

        if (not expires_at and max_uses is None and not indefinite):
            raise ValueError("grant_must_not_be_permanent_by_default")

        self.key = grant_id or _new_grant_id()
        self.capability = capability
        self.grantor = grantor
        self.grantee = grantee
        self.level = level
        self.reason = reason
        self.origin = origin
        self.scope_type = scope_type
        self.task_id = task_id
        self.project = project
        self.context = context
        self.device_id = device_id
        self.max_uses = max_uses
        self.uses = 0
        self.expires_at = expires_at
        self.indefinite = bool(indefinite)
        self.required_verification = required_verification
        self.status = "active"
        self.created_at = _now()
        self.revoked_at = None
        self.revoked_by = None
        self.revoke_reason = None

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    @property
    def expired(self):
        return self._is_expired()

    def _is_expired(self):
        # Un grant "indefinido" NO expira (pero sigue siendo revocable).
        if self.indefinite or not self.expires_at:
            return False

        try:
            from datetime import datetime

            expires = self.expires_at

            if expires.endswith("Z"):
                expires = expires[:-1] + "+00:00"

            exp = datetime.fromisoformat(expires)

            now = datetime.now(
                exp.tzinfo or datetime.now().astimezone().tzinfo
            )

            return now >= exp

        except (ValueError, TypeError, AttributeError):
            # Formato inválido: fail-closed, se considera expirado.
            return True

    def is_active(self):
        """Vigente y dentro de alcance de tiempo/uso."""
        if self.status != "active":
            return False

        if self._is_expired():
            return False

        if self.max_uses is not None and self.uses >= self.max_uses:
            return False

        return True

    def mark_used(self):
        """Consume el grant (para scope single_action / max_uses)."""
        self.uses += 1

        if self.max_uses is not None and self.uses >= self.max_uses:
            self.status = "used"

    def revoke(self, actor, reason=""):
        """Revocación inmediata: invalida sin reactivar. Auditable por el caller."""
        self.status = "revoked"
        self.revoked_at = _now()
        self.revoked_by = actor
        self.revoke_reason = reason

    def mark_expired(self):
        self.status = "expired"

    # ------------------------------------------------------------------
    # Alcance
    # ------------------------------------------------------------------

    def matches(self, capability, actor=None, context=None):
        """¿Este grant cubre la capacidad pedida en el contexto dado?"""
        if not self.is_active():
            return False

        if capability != self.capability:
            return False

        if self.scope_type == "task":
            task_id = context.get("task_id") if isinstance(context, dict) else None
            if task_id is None or task_id != self.task_id:
                return False

        if self.scope_type == "project":
            project = context.get("project") if isinstance(context, dict) else None
            if project is None or project != self.project:
                return False

        if self.scope_type == "context":
            ctx = context.get("context") if isinstance(context, dict) else None
            if ctx is None or (self.context and ctx != self.context):
                return False

        if self.scope_type == "device":
            device_id = context.get("device_id") if isinstance(context, dict) else None
            if device_id is None or device_id != self.device_id:
                return False

        return True

    # ------------------------------------------------------------------
    # Serialización
    # ------------------------------------------------------------------

    def to_dict(self):
        return {
            "grant_id": self.key,
            "capability": self.capability,
            "grantor": self.grantor,
            "grantee": self.grantee,
            "level": self.level,
            "reason": self.reason,
            "origin": self.origin,
            "scope_type": self.scope_type,
            "task_id": self.task_id,
            "project": self.project,
            "context": self.context,
            "device_id": self.device_id,
            "max_uses": self.max_uses,
            "uses": self.uses,
            "expires_at": self.expires_at,
            "indefinite": self.indefinite,
            "required_verification": self.required_verification,
            "status": self.status,
            "created_at": self.created_at,
            "revoked_at": self.revoked_at,
            "revoked_by": self.revoked_by,
            "revoke_reason": self.revoke_reason,
        }

    @classmethod
    def from_dict(cls, data):
        grant = cls(
            capability=data.get("capability", ""),
            grantor=data.get("grantor", ""),
            grantee=data.get("grantee", "ilu"),
            level=data.get("level", "execution"),
            reason=data.get("reason", ""),
            origin=data.get("origin", "user_request"),
            scope_type=data.get("scope_type", "single_action"),
            task_id=data.get("task_id"),
            project=data.get("project"),
            context=data.get("context"),
            device_id=data.get("device_id"),
            max_uses=data.get("max_uses"),
            expires_at=data.get("expires_at"),
            indefinite=data.get("indefinite", False),
            required_verification=data.get("required_verification", "low"),
            grant_id=data.get("grant_id"),
        )
        grant.uses = data.get("uses", 0)
        grant.status = data.get("status", "active")
        grant.created_at = data.get("created_at", grant.created_at)
        grant.revoked_at = data.get("revoked_at")
        grant.revoked_by = data.get("revoked_by")
        grant.revoke_reason = data.get("revoke_reason")
        return grant