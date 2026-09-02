"""Herramienta notify: notificación local en log JSONL."""

from tools.notify import notify


def test_notify_requires_message(monkeypatch, tmp_path):
    monkeypatch.setattr("tools.notify._notifications_file",
                        lambda: tmp_path / "notificaciones.jsonl")
    assert notify("   ") == {
        "success": False,
        "error": "message_required",
    }


def test_notify_writes_entry(monkeypatch, tmp_path):
    path = tmp_path / "notificaciones.jsonl"
    monkeypatch.setattr("tools.notify._notifications_file",
                        lambda: path)

    result = notify("la tarea terminó")

    assert result["success"] is True
    assert result["message"] == "la tarea terminó"
    assert path.exists()

    import json
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1

    entry = json.loads(lines[0])
    assert entry["message"] == "la tarea terminó"
    assert entry["level"] == "info"
    assert entry["ts"]


def test_notify_appends(monkeypatch, tmp_path):
    path = tmp_path / "notificaciones.jsonl"
    monkeypatch.setattr("tools.notify._notifications_file",
                        lambda: path)

    notify("uno")
    notify("dos")

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2