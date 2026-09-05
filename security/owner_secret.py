"""
I.L.U. — Clave de autorización del owner (Bloque 14).

Cuando I.L.U. recibe una orden conversacional de concesión ("autoriza X"),
necesita estar segura de que quien la dice es el dueño. La clave del owner
(`ILU_OWNER_SECRET` en el entorno, o el archivo local `security/owner.pin`)
es la prueba de identidad pedida en esa conversación (voz o texto).

Reglas de oro:

  - La clave NUNCA se inyecta en el system prompt ni se le muestra al
    modelo: solo se valida acá, en código determinista.
  - Fail-closed: si no hay clave configurada, las concesiones quedan
    bloqueadas con un motivo explícito. "Sin clave" jamás significa
    "todo permitido".
  - La comparación usa `secrets.compare_digest` (resistente a timing).

Fuentes (en orden de precedencia):
  1. Variable de entorno `ILU_OWNER_SECRET` (el valor ES la clave).
  2. Archivo `security/owner.pin` (gitignored, como `device.key`).
"""

import os
import secrets


class OwnerSecret:
    """
    Guardián de la clave de autorización del owner.

    La lectura es perezosa (en cada consulta), de modo que configurar la
    variable de entorno o el archivo después de construir el core tiene
    efecto inmediato y los tests pueden inyectar la clave en su fixture.
    """

    def __init__(self, path="security/owner.pin", env_name="ILU_OWNER_SECRET"):
        self.path = path
        self.env_name = env_name

    # ------------------------------------------------------------------
    # Carga perezosa
    # ------------------------------------------------------------------

    def _load(self):
        """Devuelve la clave (str) o None si no está configurada."""
        pin = os.environ.get(self.env_name)

        if pin:
            return pin.strip()

        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                pin = handle.read().strip()
        except OSError:
            return None

        return pin or None

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    @property
    def configured(self):
        """¿Hay una clave configurada (env o archivo)?"""
        return self._load() is not None

    def matches(self, candidate):
        """
        ¿El candidato es la clave del owner? Comparación de tiempo
        constante; cualquier entrada no string o clave ausente -> False.
        """
        secret = self._load()

        if secret is None or not isinstance(candidate, str):
            return False

        return secrets.compare_digest(candidate, secret)

    def source(self):
        """Dónde se configuró (para mensajes de diagnóstico)."""
        if os.environ.get(self.env_name):
            return f"env:{self.env_name}"

        if os.path.exists(self.path):
            return f"file:{self.path}"

        return "unconfigured"