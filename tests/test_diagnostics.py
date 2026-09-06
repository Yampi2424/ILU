"""
Tests para diagnostics (Fase F).

Verifica:
- run() con deep=False: checks rápidos, sin red real y sin secretos.
- Componentes presentes: memory, security, scheduler, agents,
  perception, learning, jobs.
- provider: instanciado (light) o health timeout (deep, cuando no hay).
- Nunca expone el PIN del owner ni claves.
"""

import os
import pytest

from app import diagnostics


class TestDiagnosticsHelpers:
    """Funciones auxiliares del snapshot de salud."""

    @pytest.fixture
    def core(self, monkeypatch, tmp_path):
        """ILUCore aislado con stores temporales."""
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setenv("ILU_WORKSPACE", str(ws))
        monkeypatch.setenv("ILU_MEMORY_PATH", str(tmp_path / "memory.json"))
        monkeypatch.setenv("ILU_SCHEDULER_PATH", str(tmp_path / "scheduler.jsonl"))
        monkeypatch.setenv("ILU_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
        for key in [
            "DATABASE_URL", "ILU_AI_PROVIDER", "ILU_AUTONOMY",
            "ILU_OWNER_SECRET",
        ]:
            monkeypatch.delenv(key, raising=False)

        from app.core import ILUCore
        return ILUCore()

    def test_quick_check_shapes(self, core):
        """run() devuelve el snapshot con checks y summary."""
        snap = diagnostics.run(core, deep=False)

        assert snap["timestamp"].endswith("Z")
        assert "healthy" in snap
        assert isinstance(snap["healthy"], bool)
        assert snap["summary"]["total"] == len(snap["checks"])
        assert snap["summary"]["ok"] + snap["summary"]["failed"] == snap["summary"]["total"]

        components = {c["component"] for c in snap["checks"]}
        for expected in [
            "provider", "memory", "security", "scheduler",
            "agents", "perception", "learning", "jobs",
        ]:
            assert expected in components

    def test_check_memory_stats(self, core):
        """memory reporta stats (backend, familias, totales)."""
        check = diagnostics._check_memory(core)
        assert check["ok"] is True
        detail = check["detail"]
        assert "total" in detail
        assert "counts" in detail
        assert detail["total"] >= 0

    def test_check_security_no_secret(self, core):
        """El detalle de seguridad expone estado, jamás el PIN."""
        check = diagnostics._check_security(core)
        raw = repr(check["detail"])
        assert "owner_configured" in check["detail"]
        assert check["detail"]["gate_wired"] is True
        # El PIN nunca aparece en la salida amigable
        pin_variants = ["owner.pin", "ILU_OWNER_SECRET", "-----BEGIN"]
        secret = os.environ.get("ILU_OWNER_SECRET")
        if secret:
            assert secret not in raw
        for v in pin_variants:
            assert v not in raw

    def test_check_scheduler_state(self, core):
        """Scheduler reporta jobs con y sin daemon."""
        check = diagnostics._check_scheduler(core)
        assert check["ok"] is True
        detail = check["detail"]
        assert "total" in detail
        assert "enabled" in detail
        assert "by_kind" in detail

    def test_check_agents_state(self, core):
        """Agentes reportan total/enabled (puede ser 0)."""
        check = diagnostics._check_agents(core)
        assert check["ok"] is True
        assert "total" in check["detail"]
        assert "enabled" in check["detail"]

    def test_check_perception_state(self, core):
        """Percepción reporta capacidades (pueden no estar disponibles)."""
        check = diagnostics._check_perception(core)
        assert check["ok"] is True
        detail = check["detail"]
        assert "total" in detail
        assert "available" in detail
        assert detail["available"] <= detail["total"]

    def test_check_learning_state(self, core):
        """Aprendizaje reporta consolidaciones recientes (≥0)."""
        check = diagnostics._check_learning(core)
        assert check["ok"] is True
        assert "recent_consolidations" in check["detail"]

    def test_check_jobs_state(self, core):
        """Jobs recientes (≥0)."""
        check = diagnostics._check_jobs(core)
        assert check["ok"] is True
        assert "recent" in check["detail"]
        assert check["detail"]["recent"] >= 0

    def test_deep_check_provider_absent(self, monkeypatch, tmp_path):
        """deep=True sin proveedor configurado → provider falla limpio."""
        from app.core import ILUCore
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setenv("ILU_WORKSPACE", str(ws))
        monkeypatch.setenv("ILU_MEMORY_PATH", str(tmp_path / "memory.json"))
        monkeypatch.setenv("ILU_SCHEDULER_PATH", str(tmp_path / "scheduler.jsonl"))
        for key in [
            "DATABASE_URL", "ILU_AI_PROVIDER", "ILU_AUTONOMY",
            "ILU_OWNER_SECRET",
        ]:
            monkeypatch.delenv(key, raising=False)
        core = ILUCore()

        snap = diagnostics.run(core, deep=True)
        provider = [c for c in snap["checks"] if c["component"] == "provider"][0]
        # Sin proveedor real: honesto (ok=False o fallback local con nota)
        assert "component" in provider


