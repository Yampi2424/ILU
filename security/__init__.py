"""
Capa de autoridad, permisos y autonomía gobernada de I.L.U. (Bloque 8).

Separación de capas (una única puerta de ejecución: SecurityGate):

    Human Identity (Principal/Device)
        -> Authority (concede/revoca grants, cambia autonomía, registra dispositivos)
        -> Policy (reglas separadas del código)
        -> Grants (autorizaciones explícitas, persistentes, auditables)
        -> SecurityGate (único punto de enforcement)
        -> Task / Tool / Subagent
        -> AuditLog

Principio: la INTELIGENCIA propone; la AUTORIDAD decide; la EJECUCIÓN
solo ocurre cuando la autoridad correspondiente lo permite. Nada puede
autoconcederse permisos: ni I.L.U., ni una herramienta, ni un sub-agente,
ni un proceso secundario.
"""

from security.grant import Grant, grant_id
from security.grant_store import GrantStore
from security.principal import Principal, PrincipalRegistry
from security.policy import Policy
from security.emergency import EmergencyRegistry
from security.authority import Authority
from security.authorization_request import (
    AuthorizationRequest,
    AuthorizationRequestStore,
    AuthorizationRequired,
)
from security.device import DeviceRegistry
from security.spoofing import SpoofingGuard

__all__ = [
    "Grant",
    "grant_id",
    "GrantStore",
    "Principal",
    "PrincipalRegistry",
    "Policy",
    "EmergencyRegistry",
    "Authority",
    "AuthorizationRequest",
    "AuthorizationRequestStore",
    "AuthorizationRequired",
    "DeviceRegistry",
    "SpoofingGuard",
]