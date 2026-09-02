"""
Bloque 10 — Historial de conversación multi-turn (ConversationStore).

Guarda turnos por session_id en JSONL local (o Postgres si hay
DATABASE_URL). El historial es contexto de LECTURA: solo se usa para
dar contexto al modelo, nunca para elevar permisos ni introducir tools.
"""

from memory.conversations import ConversationStore


def _store(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_POOLED", raising=False)

    return ConversationStore(
        path=str(tmp_path / "conversations.jsonl")
    )


def test_append_and_recent_round_trip(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)

    store.append("s1", "user", "hola")
    store.append("s1", "assistant", "Hola, ¿en qué te ayudo?")

    turns = store.recent("s1")

    assert len(turns) == 2
    assert turns[0] == {
        "role": "user",
        "content": "hola",
        "tool_calls": None,
    }
    assert turns[1]["role"] == "assistant"
    assert turns[1]["content"] == "Hola, ¿en qué te ayudo?"


def test_sessions_are_isolated(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)

    store.append("a", "user", "para A")
    store.append("b", "user", "para B")

    assert len(store.recent("a")) == 1
    assert len(store.recent("b")) == 1
    assert store.recent("a")[0]["content"] == "para A"
    assert store.recent("b")[0]["content"] == "para B"


def test_recent_respects_limit(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)

    for i in range(5):
        store.append("s1", "user", f"turno {i}")

    turns = store.recent("s1", limit=3)

    assert len(turns) == 3
    # Los últimos (más recientes) en orden cronológico.
    assert turns[-1]["content"] == "turno 4"


def test_reset_clears_only_that_session(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)

    store.append("s1", "user", "a")
    store.append("s2", "user", "b")

    store.reset("s1")

    assert store.recent("s1") == []
    assert len(store.recent("s2")) == 1


def test_list_sessions_counts(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)

    store.append("s1", "user", "a")
    store.append("s1", "user", "b")
    store.append("s2", "user", "c")

    counts = store.list_sessions()

    assert counts["s1"] == 2
    assert counts["s2"] == 1


def test_append_ignores_invalid_roles(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)

    store.append("s1", "system", "no debería guardarse")
    store.append("", "user", "sin sesión")

    assert store.recent("s1") == []
    assert store.list_sessions() == {}


def test_transcript_formats_turns():
    turns = [
        {"role": "user", "content": "¿qué hora es?"},
        {"role": "assistant", "content": "Son las 12:00"},
    ]

    text = ConversationStore.transcript(turns)

    assert "Conversación reciente:" in text
    assert "Usuario: ¿qué hora es?" in text
    assert "I.L.U.: Son las 12:00" in text


def test_transcript_empty():
    assert ConversationStore.transcript([]) == ""
    assert ConversationStore.transcript(None) == ""


def test_persists_across_instances(monkeypatch, tmp_path):
    path = str(tmp_path / "conversations.jsonl")

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_POOLED", raising=False)

    first = ConversationStore(path=path)
    first.append("s1", "user", "hola")

    # Una segunda instancia sobre el mismo archivo ve los turnos.
    second = ConversationStore(path=path)
    turns = second.recent("s1")

    assert len(turns) == 1
    assert turns[0]["content"] == "hola"
