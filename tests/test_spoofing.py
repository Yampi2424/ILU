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

# ------------------------------------------------------------------
# D-7 — El guard está conectado a la verificación de identidad del core
# ------------------------------------------------------------------

def test_core_verification_ok_recognizes_ilu_and_owner():
    """
    El core considera verificado a I.L.U. misma y al owner raíz; cualquier
    identidad ajena NO está verificada (queda bajo vigilancia).
    """
    from app.core import ILUCore

    core = ILUCore()

    assert core._verification_ok("ilu") is True
    assert core._verification_ok(core.settings.owner_id) is True
    assert core._verification_ok("intruso-desconocido") is False


def test_unverified_high_sensitivity_escalates_to_deny(tmp_path):
    """
    Verificación funcional de spoofing: un actor NO verificado que insiste
    en una capacidad sensible (alta sensibilidad) acaba siendo DENEGADO por
    suplantación, no solo preguntado.
    """
    from app.security import SecurityGate
    from security.spoofing import SpoofingGuard
    from security.policy import Policy
    from app.audit import AuditLog

    gate = SecurityGate(autonomy_level="autonomous")
    policy = Policy()
    spoofing = SpoofingGuard(
        audit=AuditLog(path=str(tmp_path / "audit.jsonl")),
        threshold=3,
        window_seconds=300,
    )

    # write_file es sensibilidad alta (policy).
    assert policy.sensitivity("write_file") == "high"

    for _ in range(3):
        decision = gate.decide(
            "write_file", "ask", mode="model",
            capability="write_file",
            actor="intruso",
            context={},
            grant_store=None,
            policy=policy,
            spoofing=spoofing,
            verification_ok=False,  # identidad no verificada
        )

    # Tras el umbral: sospecha de suplantación -> deny.
    assert decision["decision"] == "deny"
    assert decision["reason"] == "identity_suspected"
