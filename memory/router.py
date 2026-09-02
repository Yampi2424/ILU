"""
MemoryRouter: orquestador de la memoria multi-tipo de I.L.U.

Es la puerta de entrada del resto del sistema a la memoria. Opera contra
una `MemoryBackend` (JSON, Postgres, en memoria...), de modo que I.L.U.
puede almacenar, recuperar, relacionar, actualizar y consultar distintos
tipos de memoria sin quedar atada a un almacenamiento concreto.

Además del API enriquecido (remember / query / list_by_type / update /
link / forget / correct / stats), expone los métodos `save`, `search` y
`load_all` con la misma firma que el antiguo `MemoryStore`, para poder
sustituirlo en el pipeline sin romper compatibilidad.

Este bloque (Bloque 5) añade sobre esa base:
- versionado real de recuerdos (revisions) sin destruir el historial;
- tipos `episodic`, `semantic`, `working`, `procedural`;
- eje de ciclo de vida (volatile / temporal / permanent);
- `source` y `device_id` en cada recuerdo;
- `recall_intent` / `recall_context` / `recall_recent` como puerta de
  consulta dirigida;
- costura de sincronización (`changes_since` / `apply_changes`): la
  interfaz queda lista, pero el SyncEngine real NO está implementado
  (PLANIFICADO).
"""

import os
import secrets
from datetime import datetime, timezone

from memory.backends import MemoryRecord, create_backend, _now
from memory.types import normalize_type, importance_default, lifecycle_of
from memory import graph


def _age_days(updated_at):
    """Días transcurridos desde `updated_at` (inf si no se puede calcular)."""
    if not updated_at:
        return float("inf")

    try:
        updated = datetime.fromisoformat(
            updated_at.replace("Z", "+00:00")
        )
    except Exception:
        return float("inf")

    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)

    return (datetime.now(timezone.utc) - updated).total_seconds() / 86400


class MemoryRouter:
    def __init__(self, backend=None):
        self.backend = backend or create_backend()
        # device_id identifica la ubicación de I.L.U. (para la futura sync).
        self.device_id = os.environ.get("ILU_DEVICE_ID", "local")
        # La memoria de trabajo (volatile) no sobrevive a un reinicio.
        self.prune_volatile()

    # ------------------------------------------------------------------
    # API enriquecida
    # ------------------------------------------------------------------

    def remember(
        self,
        content,
        memory_type="general",
        key=None,
        importance=None,
        tags=None,
        links=None,
        metadata=None,
        source="user",
        device_id=None
    ):
        """
        Guarda un recuerdo en el tipo indicado y devuelve el MemoryRecord.

        Si no se da `key`, se genera una clave corta única global (prepara
        la sincronización entre ubicaciones). Si no se da `importance`, se
        usa el default del tipo.
        """
        content = str(content).strip()

        if not content:
            return None

        memory_type = normalize_type(memory_type)

        if importance is None:
            importance = importance_default(memory_type)

        if key is None:
            key = self._generate_key()

        record = MemoryRecord(
            key=key,
            content=content,
            memory_type=memory_type,
            importance=int(importance),
            tags=list(tags or []),
            links=list(links or []),
            metadata=dict(metadata or {}),
            source=source or "user",
            device_id=device_id or self.device_id,
        )

        self.backend.save(record)
        return record

    def query(self, query, types=None, limit=10):
        """Busca por relevancia; devuelve lista de MemoryRecord."""
        return self.backend.search(query, types=types, limit=limit)

    def list_by_type(self, memory_type, limit=100):
        """Lista recuerdos de un tipo concreto."""
        return self.backend.list(types=[memory_type], limit=limit)

    def get(self, key):
        """Devuelve un MemoryRecord por clave (o None)."""
        return self.backend.get(key)

    def update(
        self,
        key,
        content=None,
        importance=None,
        memory_type=None,
        tags=None,
        source=None,
        why=None
    ):
        """
        Actualiza/corrige un recuerdo SIN destruir el historial.

        Si cambia el contenido, el valor anterior se conserva en
        `revisions` (con su versión, marca de tiempo y motivo) y `version`
        se incrementa. Devuelve el record actualizado, o None si la clave
        no existe.
        """
        record = self.backend.get(key)

        if record is None:
            return None

        if content is not None and content != record.content:
            record.revisions.append({
                "version": record.version,
                "content": record.content,
                "ts": _now(),
                "why": why,
            })
            record.version += 1
            record.content = str(content)

        if importance is not None:
            record.importance = int(importance)

        if memory_type is not None:
            record.memory_type = normalize_type(memory_type)

        if tags is not None:
            record.tags = list(tags)

        if source is not None:
            record.source = source

        self.backend.save(record)
        return record

    def link(self, key_a, key_b, relation=None):
        """Relaciona dos recuerdos. Devuelve True si ambos existían."""
        return graph.link_records(self.backend, key_a, key_b, relation)

    def unlink(self, key_a, key_b):
        """Quita el vínculo entre dos recuerdos."""
        return graph.unlink_records(self.backend, key_a, key_b)

    def related(self, key):
        """Recuerdos relacionados con `key` (con su relación)."""
        return graph.related(self.backend, key)

    def forget(self, target):
        """
        Borra el recuerdo más cercano al texto.

        Devuelve el record borrado, o None si no hubo coincidencia.
        """
        matches = self.backend.search(target, limit=1)

        if not matches:
            return None

        record = matches[0]
        self.backend.delete(record.key)
        return record

    def correct(self, old, new, why=None):
        """
        Corrige información antigua versionándola (no la destruye).

        Reemplaza el contenido del recuerdo más cercano a `old` por `new`,
        conservando el valor anterior en `revisions`. Devuelve el record
        actualizado, o None.
        """
        matches = self.backend.search(old, limit=1)

        if not matches:
            return None

        return self.update(matches[0].key, content=new, why=why)

    def stats(self, types=None):
        """Conteos por tipo (y total) sobre los recuerdos guardados."""
        records = self.backend.list(types=types, limit=100000)

        counts = {}

        for record in records:
            counts[record.memory_type] = (
                counts.get(record.memory_type, 0) + 1
            )

        return {
            "total": len(records),
            "counts": counts,
        }

    # ------------------------------------------------------------------
    # Ciclo de vida (lifecycle)
    # ------------------------------------------------------------------

    def lifecycle(self, memory_type):
        """Eje de ciclo de vida de un tipo (volatile/temporal/permanent)."""
        return lifecycle_of(memory_type)

    def prune_volatile(self):
        """Limpia la memoria de trabajo (volatile); se llama al arrancar."""
        for key, record in self.backend.all().items():
            if lifecycle_of(record.memory_type) == "volatile":
                self.backend.delete(key)

    def prune(self, days=30):
        """
        Retención básica: borra memorias temporales (conversation/episodic)
        con más de `days` días y toda la memoria volatile. Devuelve cuántos
        recuerdos quitó.
        """
        removed = 0

        for key, record in self.backend.all().items():
            if lifecycle_of(record.memory_type) == "volatile":
                self.backend.delete(key)
                removed += 1
                continue

            if (
                lifecycle_of(record.memory_type) == "temporal"
                and _age_days(record.updated_at) > days
            ):
                self.backend.delete(key)
                removed += 1

        return removed

    # ------------------------------------------------------------------
    # Consulta dirigida (recall)
    # ------------------------------------------------------------------

    def recall_intent(self, message, top_k=10):
        """
        Recuerdos que importan para responder a un mensaje: qué sabe I.L.U.
        que guarda relación con lo que se le pide. El ranking semántico es
        PLANIFICADO; hoy es relevancia léxica sobre todos los tipos.
        """
        return self.query(message, limit=top_k)

    def recall_context(self, message, top_k=5):
        """Contexto de conversación/contexto personal relevante al mensaje."""
        return self.query(
            message,
            types=["conversation", "working", "personal"],
            limit=top_k,
        )

    def recall_recent(self, memory_type="episodic", n=10):
        """Recuerdos recientes de un tipo (para aprendizaje/planificación)."""
        return self.backend.list(types=[memory_type], limit=n)

    # ------------------------------------------------------------------
    # Costura de sincronización (PLANIFICADO el SyncEngine real)
    # ------------------------------------------------------------------

    def changes_since(self, cursor=None, limit=100):
        """
        Recuerdos modificados desde `cursor` (marca de tiempo), incluidos
        los borrados lógicos (`tombstone`). Interfaz para que una ubicación
        difunda sus cambios a otras. El transporte/merge real es PLANIFICADO.
        """
        records = self.backend.list(limit=100000)

        if cursor:
            records = [
                record for record in records
                if (record.updated_at or "") >= cursor
            ]

        records.sort(key=lambda record: record.updated_at or "")
        return records[:limit]

    def apply_changes(self, batch):
        """
        Aplica cambios remotos fusionando por clave y versión: solo entra
        una versión más nueva que la local. Interfaz del futuro SyncEngine
        (el merge por reloj vectorial y la resolución de conflictos son
        PLANIFICADO).
        """
        applied = 0

        for incoming in batch:
            local = self.backend.get(incoming.get("key"))

            if local is None or incoming.get("version", 1) > local.version:
                self.remember(
                    content=incoming.get("content", ""),
                    memory_type=incoming.get("memory_type", "general"),
                    key=incoming.get("key"),
                    importance=incoming.get("importance"),
                    tags=incoming.get("tags"),
                    links=incoming.get("links"),
                    metadata=incoming.get("metadata"),
                    source=incoming.get("source", "sync"),
                    device_id=incoming.get("device_id"),
                )
                applied += 1

        return applied

    # ------------------------------------------------------------------
    # Compatibilidad con MemoryStore (para el pipeline actual)
    # ------------------------------------------------------------------

    def save(self, key, value, memory_type="general", importance=5,
             source=None, device_id=None):
        """Firma compatible con el antiguo MemoryStore.save."""
        self.remember(
            value,
            memory_type=memory_type,
            key=key,
            importance=importance,
            source=source,
            device_id=device_id,
        )

    def search(self, text, limit=10, memory_type=None):
        """
        Firma compatible con MemoryStore.search: devuelve lista de dicts
        con "type", "key", "content", "importance", "created_at",
        "updated_at".
        """
        types = [memory_type] if memory_type else None

        records = self.backend.search(
            text,
            types=types,
            limit=limit
        )

        return [self._to_store_dict(record) for record in records]

    def load_all(self):
        """Firma compatible: {key: {type, content, ...}}."""
        return {
            key: self._to_store_dict(record)
            for key, record in self.backend.all().items()
        }

    @staticmethod
    def _to_store_dict(record):
        return {
            "type": record.memory_type,
            "key": record.key,
            "content": record.content,
            "importance": record.importance,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    def _generate_key(self):
        """Clave corta única global (prepara la sync entre ubicaciones)."""
        return "mem_" + secrets.token_hex(4)
