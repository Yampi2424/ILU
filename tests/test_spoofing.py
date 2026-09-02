"""
Bloque 8 — Detección de suplantación de identidad.

Cuenta fallos de verificación en una ventana y marca sospecha al superar
el umbral, auditando el incidente (nunca deja de ser una capa de señal:
la decisión final la toma SecurityGate).
"""

import time

from app.audit import AuditLog
from security.spoofing import SpoofingGuard


def make_guard(tmp_path, threshold=3, window=300):
    return SpoofingGuard(
        audit=AuditLog(path=str(tmp_path / "audit.jsonl")),
        threshold=threshold,
        window_seconds=window,
    )


def test_single_failure_is_not_suspected(tmp_path):
    guard = make_guard(tmp_path, threshold=3)

    assert guard.record_failure("intruso", "write_file") is False
    assert guard.is_suspected("intruso") is False


def test_threshold_marks_suspected(tmp_path):
    guard = make_guard(tmp_path, threshold=3)

    guard.record_failure("intruso", "write_file")
    guard.record_failure("intruso", "write_file")

    assert guard.record_failure("intruso", "write_file") is True
    assert guard.is_suspected("intruso") is True


def test_failures_are_per_identity(tmp_path):
    guard = make_guard(tmp_path, threshold=3)

    guard.record_failure("intruso_a", "write_file")
    guard.record_failure("intruso_a", "write_file")

    # La identidad B no hereda los fallos de A.
    assert guard.record_failure("intruso_b", "write_file") is False


def test_window_prunes_old_failures(tmp_path):
    guard = make_guard(tmp_path, threshold=3, window=1)

    guard.record_failure("intruso", "write_file")
    guard.record_failure("intruso", "write_file")

    time.sleep(1.1)

    assert guard.failures("intruso") == 0
    assert guard.is_suspected("intruso") is False


def test_clear_resets_legitimate_owner(tmp_path):
    guard = make_guard(tmp_path, threshold=2)

    guard.record_failure("yampi", "write_file")
    guard.record_failure("yampi", "write_file")
    assert guard.is_suspected("yampi") is True

    guard.clear("yampi")

    assert guard.is_suspected("yampi") is False


def test_clear_all(tmp_path):
    guard = make_guard(tmp_path, threshold=2)

    guard.record_failure("a", "write_file")
    guard.record_failure("a", "write_file")
    guard.record_failure("b", "notify")
    guard.record_failure("b", "notify")

    guard.clear()

    assert guard.is_suspected("a") is False
    assert guard.is_suspected("b") is False


def test_audit_records_incidents(tmp_path):
    audit = AuditLog(path=str(tmp_path / "audit.jsonl"))
    guard = SpoofingGuard(audit=audit, threshold=2, window_seconds=300)

    guard.record_failure("intruso", "write_file")
    guard.record_failure("intruso", "write_file")

    with open(str(tmp_path / "audit.jsonl"), "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    flagged = [line for line in lines if "spoofing_suspected" in line]

    assert len(flagged) == 1


def test_failures_count_reported():
    guard = SpoofingGuard(audit=AuditLog(), threshold=3)

    guard.record_failure("intruso", "write_file")
    guard.record_failure("intruso", "write_file")

    assert guard.failures("intruso") == 2