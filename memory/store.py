import json
import os
from pathlib import Path

import psycopg


class MemoryStore:
    """
    Sistema de memoria de I.L.U.

    En producción utiliza Neon PostgreSQL mediante
    DATABASE_URL_POOLED o DATABASE_URL.

    Si no existe una conexión a PostgreSQL,
    utiliza almacenamiento JSON local para desarrollo.
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
                        key TEXT PRIMARY KEY,
                        value JSONB NOT NULL
                    )
                """)

    def save(self, key, value):
        if self.database_url:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO ilu_memory (key, value)
                        VALUES (%s, %s::jsonb)
                        ON CONFLICT (key)
                        DO UPDATE SET value = EXCLUDED.value
                        """,
                        (key, json.dumps(value, ensure_ascii=False))
                    )
            return

        data = self.load_all()
        data[key] = value

        with self.path.open("w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )

    def get(self, key, default=None):
        if self.database_url:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT value FROM ilu_memory WHERE key = %s",
                        (key,)
                    )

                    row = cursor.fetchone()

                    if row is None:
                        return default

                    return row[0]

        data = self.load_all()
        return data.get(key, default)

    def load_all(self):
        if self.database_url:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT key, value FROM ilu_memory ORDER BY key"
                    )

                    rows = cursor.fetchall()

                    return {
                        key: value
                        for key, value in rows
                    }

        if not self.path.exists():
            return {}

        with self.path.open("r", encoding="utf-8") as file:
            return json.load(file)
