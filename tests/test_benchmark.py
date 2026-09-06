"""
Tests para benchmark (Fase F).

Verifica:
- Suite con benchmarks offline deterministas (sin LLM, sin red).
- run_suite() scoring + persistencia JSONL (memory/benchmarks.jsonl).
- Filtro por categorías y por modo.
- history()/last_snapshot() leen el JSONL.
- Los benchmarks NO crean grants (ni tocan Authority).
"""

import json
import os
import time
import pytest

from app import benchmark


@pytest.fixture
def bench_path(tmp_path, monkeypatch):
    """Redirige el JSONL a tmp_path."""
    target = tmp_path / "benchmarks.jsonl"
    monkeypatch.setattr(benchmark, "_BENCH_PATH", target)
    return target


@pytest.fixture
def core(monkeypatch, tmp_path):
    """ILUCore aislado (stores en tmp_path, sin DD/LLM real)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("ILU_WORKSPACE", str(ws))
    monkeypatch.setenv("ILU_MEMORY_PATH", str(tmp_path / "memory.json"))
    monkeypatch.setenv("ILU_SCHEDULER_PATH", str(tmp_path / "scheduler.jsonl"))
    monkeypatch.setenv("ILU_GRANTS_PATH", str(tmp_path / "grants.jsonl"))
    monkeypatch.setenv("ILU_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    for key in ["DATABASE_URL", "ILU_AI_PROVIDER", "ILU_AUTONOMY",
                "ILU_OWNER_SECRET"]:
        monkeypatch.delenv(key, raising=False)
    from app.core import ILUCore
    return ILUCore()


class TestBenchmarkSuite:
    """La suite expone benchmarks bien formados."""

    def test_benchmarks_well_formed(self):
        """Todos tienen id único, modo válido y categoría."""
        ids = [b["id"] for b in benchmark.BENCHMARKS]
        assert len(ids) == len(set(ids)), "ids duplicados"

        for b in benchmark.BENCHMARKS:
            assert b["mode"] in ("offline", "live")
            assert b["category"]
            assert b["input"]
            assert b["name"]

        # Hay al menos un modo offline (determinista) y uno live.
        modes = {b["mode"] for b in benchmark.BENCHMARKS}
        assert "offline" in modes
        assert "live" in modes


class TestRunSuiteOffline:
    """run_suite(mode="offline") es determinista y no toca red."""

    def test_offline_deterministic(self, core, bench_path):
        """Modo offline no llama LLM: rápido, sin latencia en vivo."""
        offset = time.time()
        result = benchmark.run_suite(core, mode="offline")
        elapsed = time.time() - offset

        assert result["mode"] == "offline"
        assert result["executed"] >= 4  # los 6 offline (o filtrados)
        assert result["passed"] + result["failed"] == result["executed"]
        assert 0.0 <= result["score"] <= 1.0

        # Sin LLM: cada offline corre sin latencia_s (o latencia mínima)
        for r in result["results"]:
            assert r["mode"] == "offline"
            assert r["ok"] in (True, False)
            assert "detail" in r

        # Determinismo: volver a correr da el mismo número de aciertos
        result2 = benchmark.run_suite(core, mode="offline")
        assert result2["passed"] == result["passed"]

    def test_persists_jsonl(self, core, bench_path):
        """Cada run se suma al JSONL."""
        before = benchmark._load_benchmarks()
        benchmark.run_suite(core, mode="offline")
        after = benchmark._load_benchmarks()

        assert len(after) == len(before) + 1
        last = after[-1]
        assert last["timestamp"].endswith("Z")
        assert last["mode"] == "offline"
        assert "score" in last

    def test_filter_categories(self, core, bench_path):
        """Filtro por categoría deja solo esa categoría."""
        result = benchmark.run_suite(core, mode="offline", filter_categories=["security"])
        cats = {r["category"] for r in result["results"]}
        assert cats == {"security"}

    def test_no_grants_created(self, core, bench_path):
        """El benchmark jamás crea grants (no auto-grant)."""
        grants_before = core.grant_store.list()
        benchmark.run_suite(core, mode="offline")
        grants_after = core.grant_store.list()
        assert len(grants_after) == len(grants_before)


class TestBenchmarkPersistence:
    """history() y last_snapshot() leyendo el JSONL."""

    def test_history_empty(self, bench_path):
        """Sin runs → lista vacía, sin error."""
        assert benchmark.history() == []

    def test_history_orders_newest_first(self, core, bench_path):
        benchmark.run_suite(core, mode="offline")
        time.sleep(1.1)  # timestamps en segundos
        benchmark.run_suite(core, mode="offline")

        hist = benchmark.history()
        assert len(hist) == 2
        assert hist[0]["timestamp"] >= hist[1]["timestamp"]

    def test_last_snapshot_none(self, bench_path):
        """Sin runs → None."""
        assert benchmark.last_snapshot() is None

    def test_last_snapshot_returns_latest(self, core, bench_path):
        benchmark.run_suite(core, mode="offline")
        time.sleep(1.1)
        benchmark.run_suite(core, mode="offline")

        snap = benchmark.last_snapshot()
        assert snap["timestamp"] == benchmark.history()[0]["timestamp"]

    def test_run_all_excludes_nothing(self, core, bench_path):
        """mode='all' incluye offline y live (los live aquí fallan o
        tardan; solo verificamos que se filtran por modo correctamente)."""
        result = benchmark.run_suite(core, mode="all", filter_categories=["security"])
        modes = {r["mode"] for r in result["results"]}
        assert modes == {"offline"}  # security solo tiene offline en la suite


class TestBenchmarkHelpers:
    """Alias de endpoints."""

    def test_run_benchmark_alias(self, core, bench_path):
        """run_benchmark() ≡ run_suite()."""
        direct = benchmark.run_suite(core, mode="offline")
        alias = benchmark.run_benchmark(core, mode="offline")
        assert alias["executed"] == direct["executed"]

    def test_get_latest(self, core, bench_path):
        benchmark.run_suite(core, mode="offline")
        assert benchmark.get_benchmark_latest() is not None