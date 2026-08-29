import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg


class MemoryStore:
    """
    Memoria persistente de I.L.U.

    En producción utiliza Neon PostgreSQL.
    En desarrollo, si no existe DATABASE_URL,
    utiliza almacenamiento JSON local.
    """

    def __init__(self, path="memory/data.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self.database_url = (
            os.environ.get("DATABASE_URL_POOLED")
            or os.environ.get("DATABASE_URL")
        )

        if self.database_url:
            self._init_database()

    def _connect(self):
        return psycopg.connect(self.database_url)

    def _init_database(self):
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS ilu_memory (
                        id BIGSERIAL PRIMARY KEY,
                        memory_type TEXT NOT NULL DEFAULT 'general',
                        memory_key TEXT NOT NULL,
                        content TEXT NOT NULL,
                        importance INTEGER NOT NULL DEFAULT 5,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE(memory_type, memory_key)
                    )
                """)

    def save(
        self,
        key,
        value,
        memory_type="general",
        importance=5
    ):
        if self.database_url:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO ilu_memory
                            (memory_type, memory_key, content, importance)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (memory_type, memory_key)
                        DO UPDATE SET
                            content = EXCLUDED.content,
                            importance = EXCLUDED.importance,
                            updated_at = NOW()
                        """,
                        (
                            memory_type,
                            key,
                            str(value),
                            importance
                        )
                    )
            return

        data = self.load_all()

        data[key] = {
            "type": memory_type,
            "content": value,
            "importance": importance,
            "updated_at": datetime.now(
                timezone.utc
            ).isoformat()
        }

        with self.path.open("w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )

    def get(self, key, default=None, memory_type="general"):
        if self.database_url:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT content
                        FROM ilu_memory
                        WHERE memory_type = %s
                          AND memory_key = %s
                        """,
                        (memory_type, key)
                    )

                    row = cursor.fetchone()

                    if row is None:
                        return default

                    return row[0]

        data = self.load_all()
        item = data.get(key)

        if item is None:
            return default

        if isinstance(item, dict):
            return item.get("content", default)

        return item

    def search(self, text, limit=10):
        text = str(text).strip()

        if not text:
            return []

        if self.database_url:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT
                            memory_type,
                            memory_key,
                            content,
                            importance,
                            created_at,
                            updated_at
                        FROM ilu_memory
                        WHERE
                            memory_key ILIKE %s
                            OR content ILIKE %s
                        ORDER BY importance DESC, updated_at DESC
                        LIMIT %s
                        """,
                        (
                            f"%{text}%",
                            f"%{text}%",
                            limit
                        )
                    )

                    rows = cursor.fetchall()

                    return [
                        {
                            "type": row[0],
                            "key": row[1],
                            "content": row[2],
                            "importance": row[3],
                            "created_at": row[4].isoformat(),
                            "updated_at": row[5].isoformat()
                        }
                        for row in rows
                    ]

        data = self.load_all()
        results = []

        text_lower = text.lower()

        for key, value in data.items():
            if isinstance(value, dict):
                content = str(value.get("content", ""))
                memory_type = value.get("type", "general")
                importance = value.get("importance", 5)
                updated_at = value.get("updated_at")
            else:
                content = str(value)
                memory_type = "general"
                importance = 5
                updated_at = None

            if (
                text_lower in key.lower()
                or text_lower in content.lower()
            ):
                results.append({
                    "type": memory_type,
                    "key": key,
                    "content": content,
                    "importance": importance,
                    "updated_at": updated_at
                })

        results.sort(
            key=lambda item: item.get("importance", 5),
            reverse=True
        )

        return results[:limit]

    def load_all(self):
        if self.database_url:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT
                            memory_key,
                            memory_type,
                            content,
                            importance,
                            created_at,
                            updated_at
                        FROM ilu_memory
                        ORDER BY updated_at DESC
                        """
                    )

                    rows = cursor.fetchall()

                    return {
                        row[0]: {
                            "type": row[1],
                            "content": row[2],
                            "importance": row[3],
                            "created_at": row[4].isoformat(),
                            "updated_at": row[5].isoformat()
                        }
                        for row in rows
                    }

        if not self.path.exists():
            return {}

        with self.path.open("r", encoding="utf-8") as file:
            return json.load(file)
