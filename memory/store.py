import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg


class MemoryStore:
    """
    Memoria persistente de I.L.U.

    Producción:
        Neon PostgreSQL

    Desarrollo:
        JSON local si no existe DATABASE_URL.
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
            "created_at": datetime.now(
                timezone.utc
            ).isoformat(),
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

    def _recency_score(self, updated_at):
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
                updated = updated.replace(
                    tzinfo=timezone.utc
                )

            now = datetime.now(timezone.utc)
            age_days = max(
                0,
                (now - updated).total_seconds() / 86400
            )

            return 1.0 / (1.0 + age_days)

        except Exception:
            return 0.0

    def _score_memory(self, item, text):
        content = str(
            item.get("content", "")
        ).lower()

        key = str(
            item.get("key", "")
        ).lower()

        query = str(text).lower().strip()

        if not query:
            return 0.0

        query_words = {
            word.strip("¿?¡!,.:;()[]{}")
            for word in query.split()
            if len(word.strip("¿?¡!,.:;()[]{}")) >= 3
        }

        content_words = {
            word.strip("¿?¡!,.:;()[]{}")
            for word in content.split()
            if len(word.strip("¿?¡!,.:;()[]{}")) >= 3
        }

        key_words = {
            word.strip("¿?¡!,.:;()[]{}")
            for word in key.split()
            if len(word.strip("¿?¡!,.:;()[]{}")) >= 3
        }

        overlap = query_words & content_words
        key_overlap = query_words & key_words

        exact_content = query in content
        exact_key = query in key

        relevance = 0.0

        if exact_content:
            relevance += 10.0

        if exact_key:
            relevance += 8.0

        relevance += len(overlap) * 3.0
        relevance += len(key_overlap) * 4.0

        importance = float(
            item.get("importance", 5)
        )

        recency = self._recency_score(
            item.get("updated_at")
        )

        return (
            relevance
            + (importance * 0.8)
            + (recency * 2.0)
        )

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
                            OR EXISTS (
                                SELECT 1
                                FROM regexp_split_to_table(
                                    LOWER(content),
                                    '\\s+'
                                ) AS word
                                WHERE word ILIKE %s
                            )
                        ORDER BY importance DESC, updated_at DESC
                        LIMIT 50
                        """,
                        (
                            f"%{text}%",
                            f"%{text}%",
                            f"%{text}%"
                        )
                    )

                    rows = cursor.fetchall()

                    results = [
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

        else:
            data = self.load_all()
            results = []

            text_lower = text.lower()

            for key, value in data.items():
                if isinstance(value, dict):
                    content = str(
                        value.get("content", "")
                    )
                    memory_type = value.get(
                        "type",
                        "general"
                    )
                    importance = value.get(
                        "importance",
                        5
                    )
                    created_at = value.get(
                        "created_at"
                    )
                    updated_at = value.get(
                        "updated_at"
                    )
                else:
                    content = str(value)
                    memory_type = "general"
                    importance = 5
                    created_at = None
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
                        "created_at": created_at,
                        "updated_at": updated_at
                    })

        scored = []

        for item in results:
            score = self._score_memory(
                item,
                text
            )

            if score > 0:
                item = dict(item)
                item["score"] = round(
                    score,
                    3
                )
                scored.append(item)

        scored.sort(
            key=lambda item: (
                item.get("score", 0),
                item.get("importance", 5)
            ),
            reverse=True
        )

        return scored[:limit]

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

        with self.path.open(
            "r",
            encoding="utf-8"
        ) as file:
            return json.load(file)
