"""
Detección de suplantación de identidad (Bloque 8).

Cuando una operación sensible exige verificación de identidad (p. ej. un
grant de alto nivel o una ejecución con verificación obligatoria), un fallo
de verificación NO es solo un "no": puede ser un intento de suplantación.

SpoofingGuard lleva un contador de fallos por identidad/capacidad en una
ventana de tiempo. Al superar el umbral, marca la identidad como bajo
sospecha y exige verificación reforzada (o rechaza), auditando el incidente.

Es una capa de defensa; la decisión final de permiso la toma SecurityGate.
"""

import time


class SpoofingGuard:
    """
    Monitorea fallos de verificación de identidad para detectar intentos
    de suplantación en capacidades sensibles.

    Registro en memoria (no persistente); basta para señalar anomalías.
    El umbral y la ventana son configurables para tests y despliegues.
    """

    def __init__(self, audit=None, threshold=3, window_seconds=300):
        from app.audit import AuditLog
        self.audit = audit or AuditLog()
        self.threshold = threshold
        self.window_seconds = window_seconds
        # {identity: [timestamps_de_fallos]}
        self._failures = {}

    def record_failure(self, identity, capability, context=None):
        """
        Registra un fallo de verificación de identidad para una capacidad.
        Devuelve True si, con este fallo, se supera el umbral (sospecha).
        """
        now = time.time()
        self._failures.setdefault(identity, []).append(now)

        # Podar fallos fuera de la ventana.
        self._failures[identity] = [
            ts for ts in self._failures[identity]
            if now - ts <= self.window_seconds
        ]

        count = len(self._failures[identity])

        if count >= self.threshold:
            self.audit.record(
                actor=identity,
                action="spoofing_suspected",
                capability=capability,
                failures=count,
                context=context or {},
            )
            return True

        return False

    def is_suspected(self, identity):
        """¿La identidad está bajo sospecha en esta ventana?"""
        now = time.time()
        self._failures.setdefault(identity, [])
        self._failures[identity] = [
            ts for ts in self._failures[identity]
            if now - ts <= self.window_seconds
        ]
        return len(self._failures[identity]) >= self.threshold

    def clear(self, identity=None):
        """Limpia los fallos registrados (p. ej. tras una verificación
        exitosa del propietario legítimo)."""
        if identity is None:
            self._failures.clear()
        else:
            self._failures.pop(identity, None)

    def failures(self, identity):
        now = time.time()
        self._failures.setdefault(identity, [])
        return len([
            ts for ts in self._failures[identity]
            if now - ts <= self.window_seconds
        ])