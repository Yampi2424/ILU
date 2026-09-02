"""
Capa de identidad humana (Principal) de I.L.U.

I.L.U. debe poder demostrar "esta orden viene realmente de una persona
autorizada". En este bloque se implementa la identidad del OWNER como
autoridad máxima; los niveles familia/usuarios/invitados quedan MODELADOS
pero NO activos (futuros bloques).

La arquitectura deja el enganche `verification` para métodos de
autenticación más fuertes (voz, cámara, reconocimiento facial, biometría,
credenciales) que se incorporarán después. Una contraseña sola no debe
considerarse suficiente para acciones muy sensibles cuando exista un
mecanismo más fuerte: cada pedido lleva `required_verification`.
"""

import json
import os
import secrets


VALID_TYPES = (
    "owner",            # autoridad raíz máxima
    "family_root",      # raíz de la familia (modelado, no activo)
    "family_member",    # miembro de la familia (modelado, no activo)
    "authorized_user",  # usuario autorizado (modelado, no activo)
    "guest",            # invitado (modelado, no activo)
    "device",           # dispositivo autorizado
    "ilu",              # la propia I.L.U. (nunca autoridad raíz)
)

# Tipos que constituyen autoridad raíz (pueden conceder autoridad).
ROOT_TYPES = ("owner", "family_root")


class Principal:
    def __init__(
        self,
        principal_id,
        principal_type="owner",
        display_name="",
        verification_method="credential",
        verification_strength="high",
        device_id=None,
        public_key=None
    ):
        if principal_type not in VALID_TYPES:
            raise ValueError("invalid_principal_type")

        self.principal_id = principal_id
        self.principal_type = principal_type
        self.display_name = display_name or principal_id
        # Método de verificación actual; futuro: voz/cámara/biometría.
        self.verification_method = verification_method
        self.verification_strength = verification_strength
        self.device_id = device_id
        self.public_key = public_key
        self.registered_at = None

    @property
    def is_root(self):
        return self.principal_type in ROOT_TYPES

    def to_dict(self):
        return {
            "principal_id": self.principal_id,
            "type": self.principal_type,
            "display_name": self.display_name,
            "verification": {
                "method": self.verification_method,
                "strength": self.verification_strength,
            },
            "device_id": self.device_id,
            "public_key": self.public_key,
            "registered_at": self.registered_at,
        }

    @classmethod
    def from_dict(cls, data):
        principal = cls(
            principal_id=data.get("principal_id", ""),
            principal_type=data.get("type", "owner"),
            display_name=data.get("display_name", ""),
            verification_method=(
                data.get("verification", {}).get("method", "credential")
            ),
            verification_strength=(
                data.get("verification", {}).get("strength", "high")
            ),
            device_id=data.get("device_id"),
            public_key=data.get("public_key"),
        )
        principal.registered_at = data.get("registered_at")
        return principal


class PrincipalRegistry:
    """
    Registro persistente de principales autorizados.

    Arranca en cero y crea el OWNER en el primer arranque desde
    `ILU_OWNER_ID` (la persona que configura el dispositivo es el owner).
    Nadie tiene autoridad superior al owner.
    """

    def __init__(self, path=None, owner_id=None):
        self.path = path or os.environ.get(
            "ILU_PRINCIPALS_PATH",
            "security/principals.json"
        )
        self.owner_id = owner_id or os.environ.get(
            "ILU_OWNER_ID",
            "owner"
        )
        self.principals = {}
        self._load()
        self.bootstrap()

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _load(self):
        self.principals = {}

        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = {}

        for key, value in (data.items() if isinstance(data, dict) else {}):
            try:
                principal = Principal.from_dict(value)
                self.principals[principal.principal_id] = principal
            except ValueError:
                continue

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        pid: principal.to_dict()
                        for pid, principal in self.principals.items()
                    },
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Bootstrapping del OWNER
    # ------------------------------------------------------------------

    def bootstrap(self):
        """Crea el owner en el primer arranque (bootstrap de confianza)."""
        if not self.principals:
            import time
            owner = Principal(
                principal_id=self.owner_id,
                principal_type="owner",
                display_name="Owner de I.L.U.",
            )
            owner.registered_at = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime()
            )
            self.principals[owner.principal_id] = owner
            self._save()

        return self.principals.get(self.owner_id)

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------

    def get(self, principal_id):
        return self.principals.get(principal_id)

    def owner(self):
        return self.principals.get(self.owner_id)

    def is_root(self, principal_id):
        principal = self.principals.get(principal_id)
        return principal is not None and principal.is_root

    def list(self):
        return [p.to_dict() for p in self.principals.values()]

    def register(self, principal):
        """Registra un principal NO raíz (o raíz) con control del caller."""
        from datetime import datetime, timezone
        principal.registered_at = datetime.now(
            timezone.utc
        ).isoformat()
        self.principals[principal.principal_id] = principal
        self._save()
        return principal