"""
Identidad de dispositivos de I.L.U. (Bloque 8).

I.L.U. puede existir en varios dispositivos, pero todos deben estar
AUTORIZADOS por el owner. Un dispositivo nuevo NO se une a I.L.U. por sí
solo: debe demostrar criptográficamente que pertenece a la misma
identidad antes de acceder a memoria, permisos, identidad o información
sensible.

Mecanismo (stdlib): reto-respuesta HMAC-SHA256 con secreto por
dispositivo. El transporte real, la clave asimétrica (ed25519) y el
SyncEngine siguen siendo PLANIFICADO; aquí queda la autoridad local:
los dispositivos autorizados conservan sus grants y operan offline.

La pérdida de Internet NO destruye la autoridad local: todos los stores
(grants, policy, principals, emergencia) son locales.
"""

import hashlib
import hmac
import json
import os
import secrets
import time


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class DeviceRegistry:
    def __init__(self, path=None):
        self.path = path or os.environ.get(
            "ILU_DEVICES_PATH",
            "security/devices.json"
        )
        self.devices = {}
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

        self.devices = data.get("devices", {}) if isinstance(data, dict) else {}

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(
                    {"devices": self.devices},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Registro (solo autoridad raíz, validado por Authority)
    # ------------------------------------------------------------------

    def register(self, device_id, display_name="", owner_id="owner"):
        if device_id in self.devices:
            return None

        secret = secrets.token_hex(32)

        self.devices[device_id] = {
            "device_id": device_id,
            "display_name": display_name or device_id,
            "owner": owner_id,
            "registered_at": _now(),
            "status": "active",
            "secret": secret,   # jamás se expone fuera de los checks
        }

        self._save()

        return {
            "device_id": device_id,
            "display_name": display_name or device_id,
            "owner": owner_id,
            "registered_at": self.devices[device_id]["registered_at"],
            "status": "active",
        }

    def revoke(self, device_id, actor, reason=""):
        if device_id not in self.devices:
            return None

        self.devices[device_id]["status"] = "revoked"
        self.devices[device_id]["revoked_at"] = _now()
        self.devices[device_id]["revoked_by"] = actor
        self.devices[device_id]["revoke_reason"] = reason
        self._save()

        return self.devices[device_id]

    # ------------------------------------------------------------------
    # Verificación criptográfica (challenge-response HMAC)
    # ------------------------------------------------------------------

    def challenge(self):
        """Nonce fresco para el reto."""
        return secrets.token_hex(24)

    def sign(self, device_id, challenge):
        """
        Firma HMAC-SHA256 del reto con el secreto del dispositivo.
        Devuelve hex, o None si el dispositivo no está autorizado/activo.
        """
        record = self.devices.get(device_id)

        if record is None or record.get("status") != "active":
            return None

        digest = hmac.new(
            record["secret"].encode("utf-8"),
            challenge.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        return digest

    def verify(self, device_id, challenge, signature):
        """Verifica la firma del dispositivo. True solo si coincide."""
        record = self.devices.get(device_id)

        if record is None or record.get("status") != "active":
            return False

        expected = hmac.new(
            record["secret"].encode("utf-8"),
            challenge.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(expected, signature or "")

    def is_authorized(self, device_id):
        record = self.devices.get(device_id)
        return record is not None and record.get("status") == "active"

    def list(self):
        # Nunca se expone el secreto.
        return [
            {
                k: v for k, v in record.items()
                if k != "secret"
            }
            for record in self.devices.values()
        ]