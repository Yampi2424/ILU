"""
Almacén persistente y auditable de autorizaciones (grants) de I.L.U.

Almacenamiento: JSONL en `security/grants.jsonl` (gitignored); se puede
cambiar con `ILU_GRANTS_PATH`. Local-first: la autoridad funciona offline.

El GrantStore es una fuente de verdad de los permisos: igual que AuditLog
y TaskManager, es best-effort ante fallos de disco pero nunca "recuerda"
un permiso que no esté persistido. Los grants expirados/revocados se
marcan (no se borran) para mantener la cadena de auditoría.
"""

import json
import os
from datetime import datetime, timezone

from security.grant import Grant


def _utc_now():
    return datetime.now(timezone.utc)


class GrantStore:
    def __init__(self, path=None):
        self.path = path or os.environ.get(
            "ILU_GRANTS_PATH",
            "security/grants.jsonl"
        )
        self.grants = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _load(self):
        self.grants = {}

        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()

                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                        grant = Grant.from_dict(data)
                        self.grants[grant.key] = grant
                    except (json.JSONDecodeError, ValueError):
                        continue

        except OSError:
            pass

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                for key in sorted(self.grants.keys()):
                    handle.write(
                        json.dumps(
                            self.grants[key].to_dict(),
                            ensure_ascii=False
                        ) + "\n"
                    )
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Escritura
    # ------------------------------------------------------------------

    def add(self, grant):
        self.grants[grant.key] = grant
        self._save()
        return grant

    def revoke(self, grant_id, actor, reason=""):
        grant = self.grants.get(grant_id)

        if grant is None:
            return None

        grant.revoke(actor, reason)
        self._save()
        return grant

    def mark_expired(self, grant_id):
        grant = self.grants.get(grant_id)

        if grant is None:
            return None

        if grant.status == "active":
            grant.mark_expired()
            self._save()

        return grant

    # ------------------------------------------------------------------
    # Consulta (usado por SecurityGate en cada decide())
    # ------------------------------------------------------------------

    def _find_active(self, capability, actor=None, context=None, consume=False):
        """
        Localiza el primer grant activo que cubre la capacidad en el
        contexto dado.

        La expiración y la revocación se aplican aquí, en el momento de la
        decisión: un permiso vencido/revocado se invalida de inmediato
        para la siguiente ejecución protegida.

        consume=False (usado por has_valid_for) SOLO comprueba: no consume
        grants de uso único. Así, preguntar "¿hay permiso?" jamás quema un
        permiso de un solo uso.
        """
        for grant in self.grants.values():
            if not grant.matches(capability, actor=actor, context=context):
                continue

            # Consumo por uso único (scope single_action / max_uses)
            # SOLO cuando la llamada es de consumo real (find_active).
            if grant.max_uses is not None:
                if consume:
                    grant.mark_used()
                    if grant.status == "used":
                        self._save()
                return grant

            return grant

        return None

    def find_active(self, capability, actor=None, context=None):
        """
        Devuelve el primer grant activo que cubre la capacidad, consumiendo
        los grants de uso único (single_action / max_uses).
        """
        return self._find_active(
            capability,
            actor=actor,
            context=context,
            consume=True,
        )

    def sweep_expired(self):
        """Marca los grants activos expirados; devuelve cuántos marcó."""
        count = 0

        for grant in self.grants.values():
            if (
                grant.status == "active"
                and (grant.expired or grant._is_expired())
            ):
                grant.mark_expired()
                count += 1

        if count:
            self._save()

        return count

    def get(self, grant_id):
        return self.grants.get(grant_id)

    def list(self, capability=None, status=None, grantor=None, limit=200):
        items = list(self.grants.values())

        if capability is not None:
            items = [g for g in items if g.capability == capability]

        if status is not None:
            items = [g for g in items if g.status == status]

        if grantor is not None:
            items = [g for g in items if g.grantor == grantor]

        items.sort(key=lambda g: g.created_at, reverse=True)

        return items[:limit]

    def has_valid_for(self, capability, actor=None, context=None):
        """
        ¿Existe un grant activo que cubra la capacidad? NO consume grants
        de uso único: es una comprobación pura (a diferencia de
        find_active, que consume single_action / max_uses).
        """
        return (
            self._find_active(
                capability,
                actor=actor,
                context=context,
                consume=False,
            )
            is not None
        )