"""
Capa de almacenes de memoria de I.L.U.

Aquí vive el desacoplamiento respecto al almacenamiento concreto. El resto
del sistema trabaja contra la interfaz `MemoryBackend` y el value object
`MemoryRecord`; qué proveedor de almacén se usa detrás es una decisión de
configuración (`ILU_MEMORY_BACKEND`), no una decisión de diseño.

Almacenes disponibles:
- JsonBackend       : JSON local (por defecto, funciona sin Internet).
- PostgresBackend   : PostgreSQL/Neon (cuando haya DATABASE_URL).
- InMemoryBackend   : solo en memoria (pruebas, sin persistencia).

El comportamiento de búsqueda y puntuación es idéntico entre almacenes:
la puntuación se calcula en Python sobre registros ya normalizados, de
modo que I.L.U. se comporta igual sin importar dónde guarde.

Esta es además la costura donde, más adelante, se conectará la
sincronización entre distintas ubicaciones de I.L.U.
"""

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from memory.types import normalize_type, importance_default


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _recency_score(updated_at):
    if not updated_at:
        return 0.0

    try:
        if isinstance(updated_at, str):
            updated = datetime.fromisoformat(
                updated_at.replace("Z", "+00:00")
            )
        else:
            updated = updated_at

        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)

        age_days = max(
            0,
            (datetime.now(timezone.utc) - updated).total_seconds() / 86400
        )

        return 1.0 / (1.0 + age_days)

    except Exception:
        return 0.0


def _score(record, query):
    """
    Puntuación de relevancia compartida por todos los almacenes.

    Regla clave: sin evidencia léxica la puntuación es 0.0. La memoria
    no fabrica candidatos con importancia + recencia; esos factores solo
    ordenan entre registros que SÍ coinciden con la consulta. Así una
    consulta no devuelve toda la memoria indiscriminadamente.
    """
    text = str(query).strip().lower()

    if not text:
        return 0.0

    content = str(record.content).lower()
    key = str(record.key).lower()
    tags = " ".join(record.tags).lower()

    query_words = {
        word.strip("¿?¡!,.:;()[]{}")
        for word in text.split()
        if len(word.strip("¿?¡!,.:;()[]{}")) >= 3
    }

    content_words = {
        word.strip("¿?¡!,.:;()[]{}")
        for word in content.split()
        if len(word.strip("¿?¡!,.:;()[]{}")) >= 3
    }

    exact_content = text in content
    exact_key = text in key
    exact_tags = text in tags
    overlap = query_words & content_words

    if not (exact_content or exact_key or exact_tags or overlap):
        return 0.0

    score = 0.0

    if exact_content:
        score += 10.0

    if exact_key:
        score += 8.0

    if exact_tags:
        score += 5.0

    score += len(overlap) * 3.0
    score += float(record.importance) * 0.8
    score += _recency_score(record.updated_at) * 2.0

    return score


@dataclass
class MemoryRecord:
    """
    Un recuerdo de I.L.U., independiente del almacén.

    - tags    : etiquetas libres para agrupar y filtrar.
    - links   : relaciones con otros recuerdos, cada una como
                {"key": <clave destino>, "relation": <tipo de relación>}.
    - metadata: dict extensible (fuente, confianza, dispositivo, ...).
    - version / revisions : versionado real. Cada cambio de contenido
                conserva el valor anterior en `revisions` (con su versión,
                marca de tiempo y motivo) y `version` se incrementa, de
                modo que corregir información NO destruye el historial.
    - source   : procedencia del recuerdo ("user", "conversation", "tool",
                "learning", "sync", ...).
    - device_id: qué ubicación de I.L.U. lo creó/modificó (para la futura
                sincronización entre ubicaciones).
    - tombstone: borrado lógico, para que una baja se propague al sincronizar.
    """
    key: str
    content: str
    memory_type: str = "general"
    importance: int = 5
    tags: list = field(default_factory=list)
    links: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    version: int = 1
    revisions: list = field(default_factory=list)
    source: str = ""
    device_id: str = ""
    tombstone: bool = False
    created_at: str = None
    updated_at: str = None

    def to_dict(self):
        return {
            "memory_type": self.memory_type,
            "content": self.content,
            "importance": self.importance,
            "tags": list(self.tags),
            "links": list(self.links),
            "metadata": dict(self.metadata),
            "version": self.version,
            "revisions": list(self.revisions),
            "source": self.source,
            "device_id": self.device_id,
            "tombstone": self.tombstone,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class MemoryBackend(ABC):
    """Interfaz común de almacén de memoria. I.L.U. no depende de JSON."""

    @abstractmethod
    def save(self, record):
        """Guarda (crea o actualiza) un record."""

    @abstractmethod
    def get(self, key):
        """Devuelve un MemoryRecord o None."""

    @abstractmethod
    def delete(self, key):
        """Borra por clave; devuelve True si existía."""

    @abstractmethod
    def search(self, query, types=None, limit=10):
        """Búsqueda por relevancia; devuelve lista de MemoryRecord."""

    @abstractmethod
    def list(self, types=None, limit=100):
        """Lista registros (opcionalmente filtrados por tipo)."""

    @abstractmethod
    def all(self):
        """Devuelve {key: MemoryRecord}."""


class InMemoryBackend(MemoryBackend):
    """Almacén solo en memoria. Útil para pruebas y modos sin disco."""

    def __init__(self):
        self._data = {}

    def save(self, record):
        if record.created_at is None:
            record.created_at = _now()

        record.updated_at = _now()
        self._data[record.key] = record

    def get(self, key):
        return self._data.get(key)

    def delete(self, key):
        return self._data.pop(key, None) is not None

    def search(self, query, types=None, limit=10):
        records = self._filter(list(self._data.values()), types)
        scored = [
            (score, record)
            for record in records
            if (score := _score(record, query)) > 0
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [record for _, record in scored[:limit]]

    def list(self, types=None, limit=100):
        records = self._filter(list(self._data.values()), types)
        records.sort(key=lambda r: r.updated_at or "", reverse=True)
        return records[:limit]

    def all(self):
        return dict(self._data)

    @staticmethod
    def _filter(records, types):
        if not types:
            return records

        allowed = set(types)
        return [r for r in records if r.memory_type in allowed]


class JsonBackend(MemoryBackend):
    """
    Almacén JSON local.

    Por defecto apunta a `memory/data.json` para conservar la memoria que
    ya existe; lee tanto el formato legado del antiguo MemoryStore
    ({type, content, importance, ...}) como el formato ampliado con
    tags/links/metadata.
    """

    def __init__(self, path="memory/data.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Cache en memoria: el archivo se lee una vez y se muta en memoria,
        # evitando releer/reescribir el JSON entero en cada operación.
        self._data = None

    def _load(self):
        if self._data is None:
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as file:
                    self._data = json.load(file)
            else:
                self._data = {}

        return self._data

    def _write(self):
        # Escritura atómica: se escribe a un temporal y luego se renombra,
        # para no dejar un archivo corrupto si el proceso se corta.
        tmp = self.path.with_name(self.path.name + ".tmp")

        with tmp.open("w", encoding="utf-8") as file:
            json.dump(self._data, file, ensure_ascii=False, indent=2)

        tmp.replace(self.path)

    @staticmethod
    def _from_raw(key, raw):
        if not isinstance(raw, dict):
            raw = {"content": raw}

        memory_type = normalize_type(
            raw.get("memory_type") or raw.get("type")
        )

        return MemoryRecord(
            key=key,
            content=str(raw.get("content", "")),
            memory_type=memory_type,
            importance=int(
                raw.get(
                    "importance",
                    importance_default(memory_type)
                ) or 0
            ),
            tags=list(raw.get("tags", []) or []),
            links=list(raw.get("links", []) or []),
            metadata=dict(raw.get("metadata", {}) or {}),
            version=int(raw.get("version", 1) or 1),
            revisions=list(raw.get("revisions", []) or []),
            source=raw.get("source", "") or "",
            device_id=raw.get("device_id", "") or "",
            tombstone=bool(raw.get("tombstone", False)),
            created_at=raw.get("created_at"),
            updated_at=raw.get("updated_at"),
        )

    def save(self, record):
        if record.created_at is None:
            record.created_at = _now()

        record.updated_at = _now()

        data = self._load()
        data[record.key] = record.to_dict()
        self._write()

    def get(self, key):
        data = self._load()

        if key not in data:
            return None

        return self._from_raw(key, data[key])

    def delete(self, key):
        data = self._load()

        if key not in data:
            return False

        del data[key]
        self._write()
        return True

    def search(self, query, types=None, limit=10):
        records = self.list(types=types, limit=100000)
        scored = [
            (score, record)
            for record in records
            if (score := _score(record, query)) > 0
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [record for _, record in scored[:limit]]

    def list(self, types=None, limit=100):
        records = list(self.all().values())

        if types:
            allowed = set(types)
            records = [r for r in records if r.memory_type in allowed]

        records.sort(key=lambda r: r.updated_at or "", reverse=True)
        return records[:limit]

    def all(self):
        return {
            key: self._from_raw(key, raw)
            for key, raw in self._load().items()
        }


class PostgresBackend(MemoryBackend):
    """
    Almacén PostgreSQL / Neon.

    Usa una tabla propia (`ilu_memory_v2`) para no interferir con la tabla
    legada del antiguo MemoryStore. Los links se guardan como JSONB y las
    etiquetas como array de texto; el resto del sistema no distingue.
    """

    TABLE = "ilu_memory_v2"

    # Orden fijo de columnas leídas (coincide con _from_row). Se seleccionan
    # explícitamente (no `*`) para que el mapeo no dependa del orden físico.
    COLS = [
        "memory_key", "memory_type", "content", "importance",
        "tags", "links", "metadata", "version", "revisions",
        "source", "device_id", "tombstone", "created_at", "updated_at",
    ]

    def __init__(self, database_url):
        self.database_url = database_url
        self._init_table()

    def _connect(self):
        return psycopg.connect(self.database_url)

    def _init_table(self):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.TABLE} (
                        id BIGSERIAL PRIMARY KEY,
                        memory_key TEXT NOT NULL UNIQUE,
                        memory_type TEXT NOT NULL DEFAULT 'general',
                        content TEXT NOT NULL,
                        importance INTEGER NOT NULL DEFAULT 5,
                        tags TEXT[] NOT NULL DEFAULT '{{}}',
                        links JSONB NOT NULL DEFAULT '[]',
                        metadata JSONB NOT NULL DEFAULT '{{}}',
                        version INTEGER NOT NULL DEFAULT 1,
                        revisions JSONB NOT NULL DEFAULT '[]',
                        source TEXT NOT NULL DEFAULT '',
                        device_id TEXT NOT NULL DEFAULT '',
                        tombstone BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )

                # Migración idempotente para tablas creadas antes del Bloque 5.
                for column, ddl in (
                    ("version", "INTEGER NOT NULL DEFAULT 1"),
                    ("revisions", "JSONB NOT NULL DEFAULT '[]'"),
                    ("source", "TEXT NOT NULL DEFAULT ''"),
                    ("device_id", "TEXT NOT NULL DEFAULT ''"),
                    ("tombstone", "BOOLEAN NOT NULL DEFAULT FALSE"),
                ):
                    cursor.execute(
                        f"ALTER TABLE {self.TABLE} "
                        f"ADD COLUMN IF NOT EXISTS {column} {ddl}"
                    )

    @classmethod
    def _from_row(cls, row):
        data = dict(zip(cls.COLS, row))

        return MemoryRecord(
            key=data["memory_key"],
            memory_type=normalize_type(data["memory_type"]),
            content=data["content"],
            importance=data["importance"],
            tags=list(data["tags"] or []),
            links=list(data["links"] or []),
            metadata=dict(data["metadata"] or {}),
            version=int(data["version"] or 1),
            revisions=list(data["revisions"] or []),
            source=data["source"] or "",
            device_id=data["device_id"] or "",
            tombstone=bool(data["tombstone"]),
            created_at=(
                data["created_at"].isoformat()
                if data["created_at"] else None
            ),
            updated_at=(
                data["updated_at"].isoformat()
                if data["updated_at"] else None
            ),
        )

    def save(self, record):
        if record.created_at is None:
            record.created_at = _now()

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self.TABLE}
                        (memory_key, memory_type, content, importance,
                         tags, links, metadata, version, revisions,
                         source, device_id, tombstone, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, NOW())
                    ON CONFLICT (memory_key) DO UPDATE SET
                        memory_type = EXCLUDED.memory_type,
                        content = EXCLUDED.content,
                        importance = EXCLUDED.importance,
                        tags = EXCLUDED.tags,
                        links = EXCLUDED.links,
                        metadata = EXCLUDED.metadata,
                        version = EXCLUDED.version,
                        revisions = EXCLUDED.revisions,
                        source = EXCLUDED.source,
                        device_id = EXCLUDED.device_id,
                        tombstone = EXCLUDED.tombstone,
                        updated_at = NOW()
                    """,
                    (
                        record.key,
                        normalize_type(record.memory_type),
                        record.content,
                        int(record.importance),
                        record.tags,
                        json.dumps(record.links),
                        json.dumps(record.metadata),
                        int(record.version),
                        json.dumps(record.revisions),
                        record.source,
                        record.device_id,
                        bool(record.tombstone),
                    ),
                )

    def get(self, key):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT {", ".join(self.COLS)} FROM {self.TABLE}
                    WHERE memory_key = %s
                    """,
                    (key,),
                )
                row = cursor.fetchone()

        return self._from_row(row) if row else None

    def delete(self, key):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"DELETE FROM {self.TABLE} WHERE memory_key = %s",
                    (key,),
                )
                return cursor.rowcount > 0

    def search(self, query, types=None, limit=10):
        records = self.list(types=types, limit=100000)
        scored = [
            (score, record)
            for record in records
            if (score := _score(record, query)) > 0
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [record for _, record in scored[:limit]]

    def list(self, types=None, limit=100):
        query_sql = f"SELECT {', '.join(self.COLS)} FROM {self.TABLE}"
        params = []

        if types:
            placeholders = ", ".join(["%s"] * len(types))
            query_sql += f" WHERE memory_type IN ({placeholders})"
            params.extend(types)

        query_sql += " ORDER BY updated_at DESC LIMIT %s"
        params.append(limit)

        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query_sql, params)
                rows = cursor.fetchall()

        return [self._from_row(row) for row in rows]

    def all(self):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {', '.join(self.COLS)} FROM {self.TABLE}"
                )
                rows = cursor.fetchall()

        return {self._from_row(row).key: self._from_row(row) for row in rows}


def create_backend():
    """
    Factory de almacén según `ILU_MEMORY_BACKEND`.

    - "json" (default): local, funciona sin Internet.
    - "postgres": usa DATABASE_URL (o DATABASE_URL_POOLED). Si no hay URL,
      falla de forma explícita (no ocultamos el fallo).
    """
    backend = os.environ.get(
        "ILU_MEMORY_BACKEND",
        "json"
    ).lower()

    if backend == "postgres":
        url = (
            os.environ.get("DATABASE_URL_POOLED")
            or os.environ.get("DATABASE_URL")
        )

        if not url:
            raise RuntimeError(
                "ILU_MEMORY_BACKEND=postgres pero no hay DATABASE_URL "
                "ni DATABASE_URL_POOLED configurada."
            )

        return PostgresBackend(url)

    path = os.environ.get(
        "ILU_MEMORY_PATH",
        "memory/data.json"
    )

    return JsonBackend(path)
