"""
Política de seguridad de I.L.U. (Bloque 8).

Las reglas viven separadas del código (`security/policy.json`) para poder
auditarlas. I.L.U. puede PROPONER cambios de reglas, nunca aplicarlos.

Estructura:
- prohibited: acciones que I.L.U. jamás puede ejecutar (ningún modelo,
  herramienta, subagente o proceso secundario las puede modificar).
- fundamental_safety: acciones de seguridad mínimas explícitas que pueden
  estar cubiertas por un protocolo de emergencia.
- sensitivity: verificación exigida por capacidad.
- auto_revoke_rules: condiciones que disparan revocación automática por
  riesgo (reglas del sistema, no autoconcedidas).
- defaults: duración/uso por defecto de un grant.
- emergency_protocols: protocolos previamente autorizados por el owner
  (I.L.U. nunca inventa protocolos de emergencia).
"""

import json
import os

DEFAULT_POLICY = {
    "version": 1,
    "prohibited": [
        "shell",
        "kill_process",
        "delete_workspace",
        "modify_own_code",
        "modify_policy",
        "grant_self",
        "elevate_autonomy"
    ],
    "fundamental_safety": [
        "abort_immediate_hazard"
    ],
    "sensitivity": {
        "system_time": "low",
        "web_search": "low",
        "read_file": "low",
        "notify": "low",
        "write_file": "high",
        "run_command": "high",
        "open_app": "medium",
        "media_control": "low"
    },
    "auto_revoke_rules": [
        {
            "trigger": "risk_flag",
            "capabilities": ["write_file"],
            "cooldown_seconds": 3600,
            "revoke_reason": "riesgo detectado por regla predefinida"
        }
    ],
    "defaults": {
        "grant_duration": "1h",
        "max_uses_default": None
    },
    "emergency_protocols": []
}


class Policy:
    """
    Carga la política desde disco. Si el archivo no existe o está
    corrupto, usa la política por defecto (failsafe, registrable).
    """

    def __init__(self, path=None):
        self.path = path or os.environ.get(
            "ILU_POLICY_PATH",
            "security/policy.json"
        )
        self.data = dict(DEFAULT_POLICY)
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return

        if isinstance(data, dict):
            self.data.update(data)

    # ------------------------------------------------------------------
    # Reglas
    # ------------------------------------------------------------------

    def is_prohibited(self, capability):
        return capability in self.data.get("prohibited", [])

    def sensitivity(self, capability):
        return self.data.get("sensitivity", {}).get(
            capability,
            "normal"
        )

    def default_duration(self):
        return self.data.get("defaults", {}).get(
            "grant_duration",
            "1h"
        )

    def default_max_uses(self):
        return self.data.get("defaults", {}).get(
            "max_uses_default",
            None
        )

    # ------------------------------------------------------------------
    # Emergencia (protocolos previamente autorizados)
    # ------------------------------------------------------------------

    def emergency_protocol(self, protocol_id):
        for protocol in self.data.get("emergency_protocols", []):
            if protocol.get("id") == protocol_id:
                return protocol

        return None

    def emergency_protocols(self):
        return list(self.data.get("emergency_protocols", []))

    # ------------------------------------------------------------------
    # Revocación automática por riesgo
    # ------------------------------------------------------------------

    def auto_revoke_for(self, capability):
        """Reglas de auto-revocación aplicables a una capacidad."""
        rules = []

        for rule in self.data.get("auto_revoke_rules", []):
            caps = rule.get("capabilities", [])

            if capability in caps:
                rules.append(rule)

        return rules