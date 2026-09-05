"""
Aislamiento de los almacenes locales de I.L.U. para los tests (Bloque 8).

Sin este fixture, construir ILUCore() escribiría grants, principales,
dispositivos etc. en security/ del repositorio real y cruzaría estado
entre tests. Cada test recibe sus propios paths en tmp_path vía env.

La política (security/policy.json) NO se redirige: es la fuente de reglas
real del repo (solo lectura) y los tests deben validarla tal cual.
"""

import pytest


@pytest.fixture(autouse=True)
def ilu_local_stores(monkeypatch, tmp_path):
    monkeypatch.setenv("ILU_GRANTS_PATH", str(tmp_path / "grants.jsonl"))
    monkeypatch.setenv(
        "ILU_PRINCIPALS_PATH", str(tmp_path / "principals.json")
    )
    monkeypatch.setenv(
        "ILU_EMERGENCY_PATH", str(tmp_path / "emergency.json")
    )
    monkeypatch.setenv("ILU_DEVICES_PATH", str(tmp_path / "devices.json"))
    monkeypatch.setenv(
        "ILU_AUTHREQ_PATH", str(tmp_path / "requests.jsonl")
    )
    monkeypatch.setenv("ILU_TASKS_PATH", str(tmp_path / "tasks.json"))
    monkeypatch.setenv("ILU_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv(
        "ILU_CONVERSATIONS_PATH",
        str(tmp_path / "conversations.jsonl")
    )
    monkeypatch.setenv("ILU_WORKSPACE", str(tmp_path / "workspace"))
    # Bloque 14: la memoria durable de I.L.U. (data.json) también se aísla,
    # para que el bootstrap de identidad del creador no toque el repo real.
    monkeypatch.setenv("ILU_MEMORY_PATH", str(tmp_path / "data.json"))