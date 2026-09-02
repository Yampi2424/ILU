"""
Autoridad de I.L.U. (Bloque 8).

La AUTHORITY es la única capa que puede:
  - conceder grants (permisos explícitos y auditables),
  - revocar permisos (inmediatamente),
  - cambiar el nivel de autonomía (hacia arriba: SOLO autoridad raíz),
  - registrar/revocar dispositivos,
  - activar/desactivar protocolos de emergencia.

NUNCA es alcanzable por el modelo, las herramientas, los subagentes o por
I.L.U. misma: el core y la capa HTTP llaman a Authority solo en nombre de
un principal humano raíz verificado. Todo intento de concederse permisos a
sí misma queda fuera por construcción (Authority no se inyecta a la
inteligencia).

Auto-revocación por riesgo: permitida SOLO cuando una regla de policy
predefinida la dispara (nunca un permiso que se autoconcede).
"""

import re
import time
from datetime import datetime, timedelta, timezone

from security.grant import Grant
from security.grant_store import GrantStore
from security.principal import PrincipalRegistry
from security.emergency import EmergencyRegistry
from security.device import DeviceRegistry


def _utc_now():
    return datetime.now(timezone.utc)


def parse_duration(value):
    """
    Convierte "1h", "90m", "2d", "30s" o un ISO timestamp a datetime UTC.
    Devuelve None si no se puede interpretar.
    """
    if value is None:
        return None

    try:
        if isinstance(value, datetime):
            return value

        text = str(value).strip()

        if text[0].isdigit() and ("h" in text or "m" in text or "d" in text or "s" in text):
            match = re.match(r"^(\d+)\s*(h|m|d|s)$", text)

            if not match:
                return None

            amount = int(match.group(1))
            unit = match.group(2)

            if unit == "h":
                delta = timedelta(hours=amount)
            elif unit == "m":
                delta = timedelta(minutes=amount)
            elif unit == "d":
                delta = timedelta(days=amount)
            else:
                delta = timedelta(seconds=amount)

            return _utc_now() + delta

        # ISO timestamp
        clean = text.replace("Z", "+00:00")
        return datetime.fromisoformat(clean)

    except (ValueError, TypeError, AttributeError):
        return None


class Authority:
    """Único punto que emite y administra autorizaciones efectivas."""

    def __init__(
        self,
        grant_store=None,
        principals=None,
        audit=None,
        emergency=None,
        devices=None,
        policy=None,
        gate=None,
        requests=None
    ):
        from app.audit import AuditLog
        from security.policy import Policy

        self.grant_store = grant_store or GrantStore()
        self.principals = principals or PrincipalRegistry()
        self.audit = audit or AuditLog()
        self.emergency = emergency or EmergencyRegistry(policy=policy)
        self.devices = devices or DeviceRegistry()
        self.policy = policy or Policy()
        self.gate = gate  # SecurityGate, para gobernar el nivel de autonomía
        # La MISMA instancia del store de solicitudes que usa el
        # orquestador (si no se inyecta, se crea una propia).
        self._request_store = requests

    # ------------------------------------------------------------------
    # Guardas
    # ------------------------------------------------------------------

    def _require_root(self, actor):
        if not actor:
            raise PermissionError("autoridad_raiz_requerida")

        if not self.principals.is_root(actor):
            raise PermissionError("no_autoridad_raiz")

    def _audit(self, action, **fields):
        # El actor del audit es "authority" salvo que el evento lo
        # identifique (p. ej. el humano que ordenó un cambio).
        fields.setdefault("actor", "authority")
        self.audit.record(action=action, **fields)

    # ------------------------------------------------------------------
    # Grants
    # ------------------------------------------------------------------

    def grant(
        self,
        capability,
        actor,
        grantee="ilu",
        level="execution",
        reason="",
        origin="user_request",
        scope_type="single_action",
        project=None,
        context=None,
        device_id=None,
        task_id=None,
        max_uses=None,
        duration=None,
        expires_at=None,
        indefinite=False,
        required_verification=None,
        request_id=None
    ):
        """
        Concede un permiso explícito. SOLO una autoridad raíz puede
        conceder (y SOLO la raíz puede conceder nivel "authority").
        """
        self._require_root(actor)

        if level in ("authority", "policy_modification", "emergency"):
            # Solo la raíz máxima puede delegar autoridad; ningún nivel
            # "execution" puede auto-elevarse.
            if not self.principals.is_root(actor):
                raise PermissionError("no_autoridad_raiz")

        if level == "execution" and self.policy.is_prohibited(capability):
            raise ValueError("capability_prohibited")

        # Un alcance "single_action" significa UN solo uso:  si no se
        # especificó un tope de usos, se fija 1 para que el grant se
        # consuma en la primera ejecución protegida.
        if scope_type == "single_action" and max_uses is None:
            max_uses = 1

        # Verificación exigida por sensibilidad (default de policy).
        if required_verification is None:
            required_verification = self.policy.sensitivity(capability)

        # Ningún grant es permanente por defecto.
        if indefinite and expires_at is None:
            # El owner elige explícitamente un permiso sin vencimiento.
            pass
        elif expires_at is None:
            expires_at = parse_duration(
                duration or self.policy.default_duration()
            )

            if expires_at is not None:
                expires_at = expires_at.isoformat()

        grant = Grant(
            capability=capability,
            grantor=actor,
            grantee=grantee,
            level=level,
            reason=reason,
            origin=origin,
            scope_type=scope_type,
            task_id=task_id,
            project=project,
            context=context,
            device_id=device_id,
            max_uses=max_uses,
            expires_at=expires_at,
            indefinite=bool(indefinite),
            required_verification=required_verification,
        )

        self.grant_store.add(grant)

        self._audit(
            "grant",
            grant_id=grant.key,
            grantor=actor,
            grantee=grantee,
            capability=capability,
            level=level,
            scope_type=scope_type,
            expires_at=expires_at,
            request_id=request_id,
        )

        if request_id:
            self._resolve_request(
                request_id,
                "granted",
                actor,
                grant_id=grant.key,
            )

        return grant

    def revoke(self, grant_id, actor, reason=""):
        """Revocación inmediata, auditable. Una herramienta jamás puede
        revocarse a sí misma: requiere autoridad raíz."""
        self._require_root(actor)

        grant = self.grant_store.revoke(grant_id, actor=actor, reason=reason)

        if grant is None:
            return None

        self._audit(
            "revoke",
            grant_id=grant.key,
            revoked_by=actor,
            capability=grant.capability,
            reason=reason,
        )

        return grant

    def auto_revoke_risk(self, capability, reason):
        """
        Revocación AUTOMÁTICA por riesgo, disparada SOLO por una regla de
        policy predefinida (nunca autoconcedida). I.L.U. puede aplicarla:
        detecta un riesgo grave y las reglas predefinidas la autorizan.
        """
        revoked = []

        for grant in list(self.grant_store.grants.values()):
            if (
                grant.capability == capability
                and grant.status == "active"
            ):
                grant.revoke(actor="ilu_autorevoke", reason=reason)
                revoked.append(grant.key)

        if revoked:
            self.grant_store._save()
            self._audit(
                "auto_revoke",
                capability=capability,
                reason=reason,
                grant_ids=revoked,
            )

        return revoked

    # ------------------------------------------------------------------
    # Solicitudes de autorización
    # ------------------------------------------------------------------

    def resolve_request(
        self,
        request_id,
        decision,
        actor,
        scope=None,
        duration=None,
        reason=""
    ):
        """
        Resuelve una solicitud abierta ("Necesito autorización para X").
        decision: "granted" | "denied". Si se concede, emite el grant
        correspondiente (con el scope que el humano eligió).
        """
        self._require_root(actor)

        request = self._requests().get(request_id)

        if request is None:
            return {"success": False, "error": "request_not_found"}

        if request.status != "open":
            return {"success": False, "error": "request_not_open"}

        if decision == "granted":
            scope = scope or request.scope or {}

            grant = self.grant(
                capability=request.capability,
                actor=actor,
                reason=reason or request.reason,
                origin="authorization_request",
                scope_type=scope.get("type", "single_action"),
                project=scope.get("project"),
                context=scope.get("context"),
                device_id=scope.get("device_id"),
                task_id=request.task_id,
                max_uses=scope.get("max_uses"),
                duration=duration,
                request_id=request_id,
            )

            return {"success": True, "grant": grant}

        self._resolve_request(request_id, "denied", actor)

        return {"success": True, "grant": None}

    def _resolve_request(self, request_id, status, actor, grant_id=None):
        request = self._requests().get(request_id)

        if request is None:
            return None

        self._requests().resolve(
            request_id,
            status,
            actor,
            grant_id=grant_id,
        )

        self._audit(
            "authorization_request",
            request_id=request_id,
            capability=request.capability,
            status=status,
            resolved_by=actor,
            grant_id=grant_id,
        )

        return request

    def _requests(self):
        if self._request_store is None:
            from security.authorization_request import (
                AuthorizationRequestStore,
            )
            self._request_store = AuthorizationRequestStore()

        return self._request_store

    # ------------------------------------------------------------------
    # Autonomía (gobernada; I.L.U. solo recomienda)
    # ------------------------------------------------------------------

    def set_autonomy(self, level, actor):
        """Cambia el nivel de autonomía. Subirlo exige autoridad raíz."""
        self._require_root(actor)

        if self.gate is None:
            return None

        if level not in self.gate.AUTONOMY_LEVELS:
            raise ValueError("invalid_autonomy_level")

        previous = self.gate.autonomy_level
        self.gate.autonomy_level = level

        self._audit(
            "autonomy_change",
            actor=actor,
            from_level=previous,
            to_level=level,
        )

        return {"from": previous, "to": level}

    # ------------------------------------------------------------------
    # Dispositivos (solo raíz)
    # ------------------------------------------------------------------

    def register_device(self, device_id, actor, display_name=None):
        self._require_root(actor)

        record = self.devices.register(
            device_id,
            display_name=display_name,
            owner_id=actor,
        )

        if record is None:
            return None

        self._audit(
            "device_register",
            device_id=device_id,
            by=actor,
        )

        return record

    def revoke_device(self, device_id, actor, reason=""):
        self._require_root(actor)

        record = self.devices.revoke(device_id, actor, reason=reason)

        if record is None:
            return None

        self._audit(
            "device_revoke",
            device_id=device_id,
            by=actor,
            reason=reason,
        )

        return record

    # ------------------------------------------------------------------
    # Emergencia (solo raíz activa protocolos definidos)
    # ------------------------------------------------------------------

    def activate_emergency(self, protocol_id, actor, narrative=""):
        self._require_root(actor)

        activation = self.emergency.activate(
            protocol_id,
            actor,
            narrative=narrative,
        )

        if activation is None:
            return None

        self._audit(
            "emergency_activate",
            protocol_id=protocol_id,
            by=actor,
        )

        return activation

    def deactivate_emergency(self, protocol_id, actor):
        self._require_root(actor)

        activation = self.emergency.deactivate(protocol_id, actor)

        if activation is None:
            return None

        self._audit(
            "emergency_deactivate",
            protocol_id=protocol_id,
            by=actor,
        )

        return activation