"""
Tests para consolidación de memoria (Fase E).

Verifica:
- Resumen con proveedor (cuando disponible) y fallback honesto.
- NO destructivo: originales se marcan consolidated=true, no se borran.
- Dedup: una pasada por tema, no reconsolida.
- Grupos temáticos (traslape léxico).
"""

import os
import time
import pytest

from app.consolidation import (
    consolidate,
    group_by_topic,
    _candidate_records,
    _age_days,
    _tokens,
)


class TestConsolidationHelpers:
    """Funciones auxiliares de consolidación."""

    def test_age_days_parsing(self):
        """Cálculo de edad en días desde ISO."""
        now = time.time()
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        # Hoy → ~0 días (entre formateo y parseo pasa <1s)
        assert _age_days(now_iso) <= 0.001

        # Hace 5 días
        five_days_ago = now - 5 * 24 * 3600
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(five_days_ago))
        assert _age_days(iso) >= 4
        assert _age_days(iso) <= 5.01

        # Formato con microsegundos (strftime no soporta %f; se construye
        # con datetime para un ISO válido con fracción).
        from datetime import datetime, timezone
        five_dt = datetime.fromtimestamp(five_days_ago, timezone.utc)
        iso_us = five_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        assert _age_days(iso_us) >= 4

        # Inválido → 0
        assert _age_days("no-fecha") == 0
        assert _age_days("") == 0

    def test_tokens_normalization(self):
        """Tokens léxicos sin stop-words ni muy cortos."""
        text = "El gato come pescado y duerme en la casa."
        toks = _tokens(text)
        assert "gato" in toks
        assert "come" in toks
        assert "pescado" in toks
        assert "duerme" in toks
        assert "casa" in toks
        assert "el" not in toks
        assert "la" not in toks
        assert "y" not in toks
        assert "en" not in toks

    def test_group_by_topic_basic(self):
        """Agrupamiento por traslape léxico (pares)."""
        records = [
            {"content": "el gato come pescado y duerme", "key": "k1"},
            {"content": "el gato duerme mucho en el patio", "key": "k2"},
            {"content": "el perro ladra fuerte en el parque", "key": "k3"},
            {"content": "el perro corre fuerte por la calle", "key": "k4"},
        ]

        groups = group_by_topic(records)

        # gato+duerme → un grupo; perro+fuerte → otro.
        assert len(groups) == 2
        all_keys = [r["key"] for g in groups for r in g]
        assert set(all_keys) == {"k1", "k2", "k3", "k4"}
        g1_keys = {r["key"] for r in groups[0]}
        g2_keys = {r["key"] for r in groups[1]}
        assert g1_keys == {"k1", "k2"} or g1_keys == {"k3", "k4"}
        assert g2_keys == {"k3", "k4"} or g2_keys == {"k1", "k2"}

    def test_group_by_topic_empty(self):
        """Lista vacía → grupos vacíos."""
        assert group_by_topic([]) == []

    def test_group_by_topic_max_size(self):
        """Máximo 8 por grupo (_MAX_GROUP)."""
        records = [
            {"content": f"tema comun {i}", "key": f"k{i}"}
            for i in range(15)
        ]
        groups = group_by_topic(records)
        for g in groups:
            assert len(g) <= 8


class TestCandidateRecords:
    """Filtro de candidatos a consolidar."""

    @pytest.fixture
    def router(self, monkeypatch, tmp_path):
        from app.core import ILUCore
        monkeypatch.setenv("ILU_MEMORY_PATH", str(tmp_path / "memory.json"))
        monkeypatch.setenv(
            "ILU_SCHEDULER_PATH", str(tmp_path / "scheduler.jsonl")
        )
        for key in [
            "DATABASE_URL", "ILU_AI_PROVIDER", "ILU_AUTONOMY",
            "ILU_OWNER_SECRET",
        ]:
            monkeypatch.delenv(key, raising=False)
        core = ILUCore()
        return core.memory

    def make_temporal(self, router, content, days_ago=10, importance=3, consolidated=False):
        """Helper: crea memoria temporal con metadata y edad forzada.

        `save()` de JsonBackend sella updated_at=ahora, así que para
        "envejecer" un recuerdo se escribe su raw directamente en el
        cache del backend (no-destructivo, mismo formato que to_dict).
        """
        ts = time.time() - days_ago * 24 * 3600
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
        key = router.remember(
            content,
            memory_type="episodic",
            importance=importance,
            source="test",
            metadata={"consolidated": consolidated},
        )
        # Forzar updated_at en el backend (write directo al raw)
        backend = router.backend
        data = backend._load()
        raw = backend.get(key.key).to_dict()
        raw["key"] = key.key
        raw["updated_at"] = iso
        data[key.key] = raw
        backend._write()
        return router.backend.get(key.key)

    def test_candidates_excludes_high_importance(self, router):
        """Importancia >= 5 se excluye."""
        self.make_temporal(router, "importante", importance=5)
        self.make_temporal(router, "poco importante", importance=3)

        candidates = _candidate_records(router, min_importance=5)

        # Solo el de importancia 3 (min_importance=5 means >=5 excluded)
        assert len(candidates) == 1
        assert "poco importante" in candidates[0]["content"]

    def test_candidates_excludes_consolidated(self, router):
        """Ya consolidados se excluyen."""
        self.make_temporal(router, "a", consolidated=True)
        self.make_temporal(router, "b", consolidated=False)

        candidates = _candidate_records(router)
        assert len(candidates) == 1
        assert candidates[0]["content"] == "b"

    def test_candidates_excludes_recent(self, router):
        """Menos de min_age_days se excluye."""
        self.make_temporal(router, "viejo", days_ago=10)
        self.make_temporal(router, "nuevo", days_ago=1)

        candidates = _candidate_records(router, min_age_days=3)
        assert len(candidates) == 1
        assert candidates[0]["content"] == "viejo"


class TestConsolidationIntegration:
    """consolidate() flujo completo con MemoryRouter real."""

    @pytest.fixture
    def router(self, monkeypatch, tmp_path):
        from app.core import ILUCore
        monkeypatch.setenv("ILU_MEMORY_PATH", str(tmp_path / "memory.json"))
        monkeypatch.setenv(
            "ILU_SCHEDULER_PATH", str(tmp_path / "scheduler.jsonl")
        )
        for key in [
            "DATABASE_URL", "ILU_AI_PROVIDER", "ILU_AUTONOMY",
            "ILU_OWNER_SECRET",
        ]:
            monkeypatch.delenv(key, raising=False)
        core = ILUCore()
        return core.memory

    def make_temporal(self, router, content, days_ago=10, importance=3):
        """Helper: crea temporal antigua (edad forzada en el raw)."""
        ts = time.time() - days_ago * 24 * 3600
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
        key = router.remember(
            content,
            memory_type="episodic",
            importance=importance,
            source="ingest",
        )
        backend = router.backend
        data = backend._load()
        raw = backend.get(key.key).to_dict()
        raw["key"] = key.key
        raw["updated_at"] = iso
        data[key.key] = raw
        backend._write()
        return router.backend.get(key.key)

    def test_consolidate_nothing_to_do(self, router):
        """Sin candidatos → reporte vacío."""
        # Solo temporales recientes
        self.make_temporal(router, "reciente", days_ago=1)

        result = consolidate(router)

        assert result["success"] is True
        assert result["groups"] == 0
        assert result["consolidated"] == 0
        assert result["note"] == "nada_para_consolidar"

    def test_consolidate_creates_semantic(self, router):
        """Grupos temáticos → recuerdos semantic creados."""
        self.make_temporal(router, "el gato come pescado", days_ago=10)
        self.make_temporal(router, "el gato come croquetas", days_ago=10)
        self.make_temporal(router, "el perro ladra fuerte", days_ago=10)
        self.make_temporal(router, "el perro ladra mucho", days_ago=10)

        result = consolidate(router, synthesize=None)

        assert result["success"] is True
        assert result["groups"] >= 1
        assert len(result["created_keys"]) >= 1

        # Verificar tipo semantic y metadata
        for ck in result["created_keys"]:
            rec = router.get(ck)
            assert rec is not None
            assert rec.memory_type == "semantic"
            assert rec.source == "consolidation"
            assert "groups" in rec.metadata
            assert "original_keys" in rec.metadata

    def test_consolidate_not_destructive(self, router):
        """Originales NO se borran; se marcan consolidated=True."""
        k1 = self.make_temporal(router, "el gato come pescado", days_ago=10)
        k2 = self.make_temporal(router, "el gato come croquetas", days_ago=10)

        original_keys = [k1.key, k2.key]
        consolidate(router, synthesize=None)

        # Originales siguen existiendo
        for k in original_keys:
            rec = router.get(k)
            assert rec is not None
            assert rec.metadata.get("consolidated") is True

    def test_consolidate_dedup_single_pass(self, router):
        """Una pasada por tema: segunda consolidación no duplica."""
        self.make_temporal(router, "el gato come pescado", days_ago=10)
        self.make_temporal(router, "el gato come croquetas", days_ago=10)

        # Primera consolidación
        r1 = consolidate(router, synthesize=None)
        created1 = len(r1["created_keys"])

        # Segunda consolidación (mismos datos, ya marcados)
        r2 = consolidate(router, synthesize=None)

        # No debe crear nuevos (originales ya consolidated=True)
        assert len(r2["created_keys"]) == 0

    def test_consolidate_with_synthesize(self, router):
        """Proveedor usado para resumir (cuando callable synthesize)."""
        self.make_temporal(router, "el gato come pescado y duerme", days_ago=10)
        self.make_temporal(router, "el gato come croquetas a diario", days_ago=10)

        # Mock synthesize que devuelve resumen fijo
        def mock_synthesize(prompt):
            return "Resumen: el gato come y juega."

        result = consolidate(router, synthesize=mock_synthesize)

        assert result["success"] is True
        assert len(result["created_keys"]) == 1
        rec = router.get(result["created_keys"][0])
        assert "Resumen: el gato come y juega" in rec.content

    def test_consolidate_synthesize_fails_fallback(self, router):
        """Si synthesize falla, usa contenido concatenado (honesto)."""
        self.make_temporal(router, "nota 1: el gato come pescado", days_ago=10)
        self.make_temporal(router, "nota 2: el gato come croquetas", days_ago=10)

        def failing_synthesize(prompt):
            raise RuntimeError("provider down")

        result = consolidate(router, synthesize=failing_synthesize)

        assert result["success"] is True
        rec = router.get(result["created_keys"][0])
        # Fallback: concatenación con viñetas
        assert "· nota 1" in rec.content
        assert "· nota 2" in rec.content


class TestConsolidationScheduler:
    """Integración: job consolidation via scheduler."""

    @pytest.fixture
    def core(self, monkeypatch, tmp_path):
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
        core = ILUCore()

        class MockProvider:
            name = "mock"
            version = "0.0.1"
            def generate(self, prompt, context=None, tools=None):
                return {"type": "text", "content": "Resumen del proveedor."}
        core.provider = MockProvider()

        # Poblar temporales (edad forzada en el raw, save sellaría updated_at)
        backend = core.memory.backend
        for i in range(4):
            ts = time.time() - 10 * 24 * 3600
            iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
            key = core.memory.remember(
                f"tema grupo {i}",
                memory_type="episodic",
                importance=3,
                source="ingest",
            )
            data = backend._load()
            raw = backend.get(key.key).to_dict()
            raw["key"] = key.key
            raw["updated_at"] = iso
            data[key.key] = raw
            backend._write()

        return core

    def test_scheduler_runs_consolidation_job(self, core):
        """Scheduler ejecuta job consolidation y audita."""
        # Añadir job consolidation con next_due en el pasado (para que
        # due_jobs lo devuelva inmediato; schedule interval:60 pondría
        # next_due=now+60).
        import time
        from app.scheduler import Job
        job = Job(
            id="test-consolidation",
            name="test-consolidation",
            kind="consolidation",
            schedule="interval:60",
            enabled=True,
            params={},
            next_due=time.time() - 10,  # ya vencido
            last_run=None,
        )
        core.scheduler_store.jobs[job.id] = job
        core.scheduler_store._save()

        # Ejecutar tick manualmente (bypass wait)
        from app.scheduler import Scheduler
        sched = Scheduler(core=core, job_store=core.scheduler_store)
        sched._tick()

        # Verificar auditoría
        audits = core.audit.recent(limit=10)
        consolidation_audits = [
            a for a in audits
            if a.get("action") == "consolidation"
        ]
        assert len(consolidation_audits) >= 1
        assert consolidation_audits[0].get("success") is True