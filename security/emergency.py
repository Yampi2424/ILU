"""
Protocolos de emergencia de I.L.U. (Bloque 8).

Categoría SEPARADA de la autonomía normal. Un protocolo previamente
autorizado puede permitir acciones automáticas sin confirmación en el
momento, porque una emergencia puede no permitir esperar respuesta humana.

I.L.U. NUNCA inventa protocolos: existen únicamente los definidos en la
política (`security/policy.json`) y su ACTIVACIÓN la ordena una autoridad
raíz. Sin protocolo activo aplicable, I.L.U. solicita autorización humana
(salvo acciones de seguridad mínimas explícitas en `fundamental_safety`).
"""

import json
import os
import secrets


class EmergencyRegistry:
    def __init__(self, policy=None, path=None):
        self.policy = policy
        self.path = path or os.environ.get(
            "ILU_EMERGENCY_PATH",
            "security/emergency.json"
        )
        # {protocol_id: {"activated_at", "activated_by", "narrative"}}
        self.active = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            data = {}

        self.active = data.get("active", {}) if isinstance(data, dict) else {}

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(
                    {"active": self.active},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Activación / desactivación (solo autoridad raíz)
    # ------------------------------------------------------------------

    def activate(self, protocol_id, actor, narrative=""):
        """
        Activa un protocolo si existe en la política. El caller (Authority)
        debe validar que `actor` es autoridad raíz.
        """
        if self.policy is None:
            return None

        protocol = self.policy.emergency_protocol(protocol_id)

        if protocol is None:
            return None

        import time
        activation = {
            "activated_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime()
            ),
            "activated_by": actor,
            "narrative": narrative,
        }

        self.active[protocol_id] = activation
        self._save()

        return activation

    def deactivate(self, protocol_id, actor):
        if protocol_id not in self.active:
            return None

        activation = self.active.pop(protocol_id)
        self._save()
        return activation

    # ------------------------------------------------------------------
    # Consulta (usada por SecurityGate)
    # ------------------------------------------------------------------

    def active_protocol(self, protocol_id):
        protocol = (
            self.policy.emergency_protocol(protocol_id)
            if self.policy is not None
            else None
        )

        if protocol_id not in self.active or protocol is None:
            return None

        return protocol

    def covers(self, capability):
        """¿Hay algún protocolo activo que autorice la capacidad?"""
        for protocol_id in self.active:
            protocol = self.active_protocol(protocol_id)

            if protocol is None:
                continue

            caps = protocol.get("capabilities", [])

            if capability in caps:
                return protocol

        return None

    def list_active(self):
        return {
            pid: {"capabilities": (
                self.policy.emergency_protocol(pid).get("capabilities", [])
                if self.policy and self.policy.emergency_protocol(pid)
                else []
            )}
            for pid in self.active
        }

    def new_protocol_id(self):
        return "emg_" + secrets.token_hex(4)