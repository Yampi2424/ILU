"""
I.L.U. — Reconocimiento de Identidad (Bloque D: JARVIS Evolution).

I.L.U. debe reconocer a su OWNER primero y, progresivamente, a la
familia y a los usuarios autorizados. Este módulo mapea a un interlocutor
(hasta ahora, por su nombre/alias explícito o por declaración de
identidad en el mensaje) a un principal del PrincipalRegistry.

NO es biometría ni una prueba de identidad fuerte: es la primera capa de
reconocimiento ("quién dice ser"). La prueba fuerte queda en el campo
`verification` de cada principal (voz, cámara, biometría, credenciales)
y en el SpoofingGuard. Para acciones sensibles, I.L.U. siempre exige esa
verificación: el reconocimiento por nombre jamás sustituye a la autoridad
ni eleva permisos por sí solo.

La configuración de aliases es explícita y humana: no se infieren
identidades a partir de nombres comunes (evita suplantaciones por
adivinar un nombre).
"""


# Por defecto, I.L.U. conoce solo el nombre/alias del OWNER. La familia y
# los usuarios autorizados se añaden a este registro (o en el registro de
# principales) con sus alias. Sin configuración, el reconocimiento solo
# puede identificar al owner y a "ilu".
_DEFAULT_ALIASES = {
    "owner": ("jefe", "patrón", "patrona", "mi amor", "amo", "ama"),
}


class IdentityRecognizer:
    """
    Reconoce quién habla a partir del texto y del contexto del principal.

    API:
      - recognize(text)        -> dict (recognized, principal_id, kind,
                                   confidence, method) o reconocimiento
                                   "unknown" con confianza 0.
      - declare(text)          -> extrae una declaración de identidad
                                   ("soy X") si la hay.
      - add_alias / aliases()  -> gestión de alias por principal.
    """

    def __init__(self, principals=None, aliases=None):
        self.principals = principals  # PrincipalRegistry opcional
        self.aliases = dict(_DEFAULT_ALIASES)

        if aliases:
            for principal_id, names in aliases.items():
                self.aliases[principal_id] = tuple(
                    self.aliases.get(principal_id, ()) + tuple(names)
                )

    # ------------------------------------------------------------------
    # Alias
    # ------------------------------------------------------------------

    def add_alias(self, principal_id, name):
        name = (name or "").strip().lower()

        if not name:
            return False

        if principal_id not in self.aliases:
            self.aliases[principal_id] = ()

        if name not in self.aliases[principal_id]:
            self.aliases[principal_id] = self.aliases[principal_id] + (name,)

        return True

    def remove_alias(self, principal_id, name):
        name = (name or "").strip().lower()

        if principal_id not in self.aliases:
            return False

        names = [n for n in self.aliases[principal_id] if n != name]
        self.aliases[principal_id] = tuple(names)
        return True

    def aliases_for(self, principal_id):
        return list(self.aliases.get(principal_id, ()))

    def known_principals(self):
        """Principal ids que I.L.U. puede reconocer (por alias o registro)."""
        known = set(self.aliases.keys())

        if self.principals is not None:
            for principal in self.principals.list():
                known.add(principal.principal_id)

        return sorted(known)

    # ------------------------------------------------------------------
    # Declaración de identidad
    # ------------------------------------------------------------------

    @staticmethod
    def _declared_identity(text):
        """Extrae "soy X" / "me llamo X" si está presente."""
        lowered = text.lower().strip()

        for marker in ("soy ", "me llamo ", "mi nombre es ", "yo soy "):
            if lowered.startswith(marker):
                name = text[len(marker):].strip(" .,;:!¡?¿")
                return name

        # "Soy X y ..." a mitad de frase.
        if lowered.startswith("soy "):
            rest = text[4:].strip()
            name = rest.split()[0].strip(".,;:!¡?¿") if rest else ""
            return name

        return None

    # ------------------------------------------------------------------
    # Reconocimiento
    # ------------------------------------------------------------------

    def recognize(self, text):
        """
        Reconoce al interlocutor. Devuelve un dict siempre:

          {"recognized": bool, "principal_id": str|None,
           "kind": "owner"|"family_member"|"authorized_user"|"unknown",
           "confidence": 0..1, "method": "alias"|"declared"|"none"}

        Un reconocimiento por alias explícito o declaración tiene
        confianza media (0.6); jamás es prueba fuerte. I.L.U. usa esto
        para personalizar, NO para conceder permisos sin más verificación.
        """
        text = (text or "").strip()

        if not text:
            return self._unknown()

        lowered = text.lower()

        # 1) Coincidencia por alias explícito (configurada por el humano).
        for principal_id, names in self.aliases.items():
            for name in names:
                if name and name in lowered:
                    return {
                        "recognized": True,
                        "principal_id": principal_id,
                        "kind": self._kind(principal_id),
                        "confidence": 0.6,
                        "method": "alias",
                    }

        # 2) Declaración de identidad ("soy X").
        declared = self._declared_identity(lowered)

        if declared:
            match = self._match_declared(declared)

            if match is not None:
                principal_id, kind = match
                return {
                    "recognized": True,
                    "principal_id": principal_id,
                    "kind": kind,
                    "confidence": 0.6,
                    "method": "declared",
                }

        # 3) Sin señal de identidad.
        return self._unknown()

    def _match_declared(self, declared_name):
        """Empareja una declaración con un principal registrado."""
        declared = declared_name.strip().lower()

        # Owner por nombre del registro.
        if self.principals is not None:
            owner = self.principals.owner()

            if owner is not None:
                owner_names = {
                    (owner.display_name or "").lower(),
                    (owner.principal_id or "").lower(),
                }
                owner_names.update(
                    self.aliases.get(owner.principal_id, ())
                )

                if declared in owner_names:
                    return (owner.principal_id, "owner")

            # Otros principales registrados.
            for principal in self.principals.list():
                names = {
                    (principal.display_name or "").lower(),
                    (principal.principal_id or "").lower(),
                }
                names.update(
                    self.aliases.get(principal.principal_id, ())
                )

                if declared in names:
                    return (
                        principal.principal_id,
                        self._kind(principal.principal_id),
                    )

        # Aliases sueltos de otros principales no registrados.
        for principal_id, names in self.aliases.items():
            if declared in names:
                return (principal_id, self._kind(principal_id))

        return None

    def _kind(self, principal_id):
        if self.principals is not None:
            principal = self.principals.get(principal_id)

            if principal is not None:
                if principal.is_root:
                    return "owner"
                if principal.principal_type == "family_member":
                    return "family_member"
                return "authorized_user"

        if principal_id == "ilu":
            return "ilu"

        if principal_id in ("owner",):
            return "owner"

        return "authorized_user"

    @staticmethod
    def _unknown():
        return {
            "recognized": False,
            "principal_id": None,
            "kind": "unknown",
            "confidence": 0.0,
            "method": "none",
        }
