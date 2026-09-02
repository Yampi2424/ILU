"""
Historial de conversación multi-turn de I.L.U. (Bloque 10).

Guarda los turnos de cada sesión (`session_id`) para que I.L.U. mantenga
contexto entre mensajes de un mismo usuario. Se persiste en JSONL local
(`memory/conversations.jsonl`, gitignored) o en una tabla Postgres cuando
hay `DATABASE_URL`, reutilizando el patrón de `MemoryStore` (file/Postgres).

Es contexto de LECTURA: el historial jamás introduce herramientas ni eleva
permisos; todo sigue pasando por la compuerta de seguridad (Bloque 8).
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ConversationStore:
    """
    Almacén de turnos de conversación por sesión.

    Métodos:
        append(session_id, role, content, tool_calls=None)
        recent(session_id, limit=6) -> list[turn]
        reset(session_id)
        list_sessions() -> {session_id: count}
        transcript(turns) -> str legible para inyectar como contexto
    """

    def __init__(self, path="memory/conversations.jsonl"):
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
                    CREATE TABLE IF NOT EXISTS ilu_conversations (
                        id BIGSERIAL PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        tool_calls TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)

    # ------------------------------------------------------------------
    # Backend local (JSONL)
    # ------------------------------------------------------------------

    def _load_local(self):
        if not self.path.exists():
            return []

        turns = []

        with self.path.open(
            "r",
            encoding="utf-8"
        ) as file:
            for line in file:
                line = line.strip()

                if not line:
                    continue

                try:
                    turns.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        return turns

    def _write_local(self, turns):
        with self.path.open(
            "w",
            encoding="utf-8"
        ) as file:
            for turn in turns:
                file.write(
                    json.dumps(
                        turn,
                        ensure_ascii=False
                    ) + "\n"
                )

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    def append(
        self,
        session_id,
        role,
        content,
        tool_calls=None
    ):
        if not session_id:
            return

        if role not in ("user", "assistant"):
            return

        content = content or ""

        turn = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "tool_calls": tool_calls,
            "created_at": _now()
        }

        if self.database_url:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO ilu_conversations
                            (session_id, role, content, tool_calls)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (
                            session_id,
                            role,
                            content,
                            (
                                json.dumps(tool_calls)
                                if tool_calls
                                else None
                            )
                        )
                    )
            return

        turns = self._load_local()
        turns.append(turn)
        self._write_local(turns)

    def recent(self, session_id, limit=6):
        if self.database_url:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT role, content, tool_calls
                        FROM ilu_conversations
                        WHERE session_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (session_id, limit)
                    )

                    rows = cursor.fetchall()

                    turns = [
                        {
                            "role": row[0],
                            "content": row[1],
                            "tool_calls": (
                                json.loads(row[2])
                                if row[2]
                                else None
                            )
                        }
                        for row in rows
                    ]

                    # Se pidieron los últimos; se devuelven en orden.
                    turns.reverse()

                    return turns

        turns = [
            {
                "role": turn.get("role"),
                "content": turn.get("content", ""),
                "tool_calls": turn.get("tool_calls")
            }
            for turn in self._load_local()
            if turn.get("session_id") == session_id
        ]

        return turns[-limit:]

    def reset(self, session_id):
        if self.database_url:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        DELETE FROM ilu_conversations
                        WHERE session_id = %s
                        """,
                        (session_id,)
                    )
            return

        turns = self._load_local()

        self._write_local(
            [
                turn
                for turn in turns
                if turn.get("session_id") != session_id
            ]
        )

    def list_sessions(self):
        if self.database_url:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT session_id, COUNT(*)
                        FROM ilu_conversations
                        GROUP BY session_id
                        """
                    )

                    return {
                        row[0]: row[1]
                        for row in cursor.fetchall()
                    }

        counts = {}

        for turn in self._load_local():
            session_id = turn.get("session_id")

            if session_id:
                counts[session_id] = (
                    counts.get(session_id, 0) + 1
                )

        return counts

    @staticmethod
    def transcript(turns):
        """Convierte turnos a un texto legible para inyectar como contexto."""
        if not turns:
            return ""

        lines = ["Conversación reciente:"]

        for turn in turns:
            role = (
                "Usuario"
                if turn.get("role") == "user"
                else "I.L.U."
            )

            content = turn.get("content") or ""

            lines.append(f"{role}: {content}")

        return "\n".join(lines)
