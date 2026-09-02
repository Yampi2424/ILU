from app.audit import AuditLog


def test_record_and_recent(tmp_path):
    audit = AuditLog(path=str(tmp_path / "audit.jsonl"))

    audit.record(
        actor="ilu",
        action="tool_attempt",
        tool="system_time",
        decision="allow"
    )

    audit.record(
        actor="ilu",
        action="tool_result",
        tool="system_time",
        success=True
    )

    entries = audit.recent()

    assert len(entries) == 2
    assert entries[0]["action"] == "tool_attempt"
    assert entries[0]["decision"] == "allow"
    assert entries[1]["success"] is True
    assert isinstance(entries[1]["timestamp"], str)


def test_record_sanitizes_sensitive_fields(tmp_path):
    audit = AuditLog(path=str(tmp_path / "audit.jsonl"))

    audit.record(
        actor="ilu",
        action="test",
        api_key="supersecreta",
        token="abc",
        password="x",
        tool="system_time"
    )

    entries = audit.recent()

    assert entries[0]["api_key"] == "***"
    assert entries[0]["token"] == "***"
    assert entries[0]["password"] == "***"
    assert entries[0]["tool"] == "system_time"


def test_recent_empty_when_no_file(tmp_path):
    audit = AuditLog(path=str(tmp_path / "no-existe.jsonl"))

    assert audit.recent() == []


def test_recent_bounded(tmp_path):
    audit = AuditLog(path=str(tmp_path / "audit.jsonl"))

    for index in range(5):
        audit.record(action=f"evento-{index}")

    entries = audit.recent(limit=2)

    assert len(entries) == 2
    assert entries[0]["action"] == "evento-3"
    assert entries[1]["action"] == "evento-4"


def test_record_never_raises_on_os_error(tmp_path):
    # Abrir un directorio como archivo levanta OSError real;
    # la auditoría best-effort no debe propagarlo.
    audit = AuditLog(path=str(tmp_path))

    result = audit.record(action="falla")

    assert result is False