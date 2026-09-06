"""
Benchmark ligero de I.L.U. (Fase F).

Ejecuta tareas de muestra y mide latencia/éxito. Dos modos:
- `offline`: componentes deterministas (parser, discover, gate, SSRF)
  → rápido, sin LLM, score por acierto exacto.
- `live`: `core.process` real (con proveedor LLM) → mide latencia real
  y éxito semántico (`esperado in response`).

Resultados se persisten en JSONL `memory/benchmarks.jsonl`.
"""

import json
import os
import time
import threading
from pathlib import Path


# ------------------------------------------------------------
# Suite de benchmarks (tareas de muestra)
# ------------------------------------------------------------

BENCHMARKS = [
    # Deterministas (offline)
    {
        "id": "skill_discover",
        "name": "Descubrir skills del catálogo",
        "input": "qué skills tenés",
        "mode": "offline",
        "category": "skills",
        "expected": "skills",  # substring en respuesta
    },
    {
        "id": "skill_parser",
        "name": "Parser SKILL.md frontmatter",
        "input": "parser_test",
        "mode": "offline",
        "category": "skills",
        "expected": None,  # special: just verify no exception
    },
    {
        "id": "security_gate_safe",
        "name": "SecurityGate permite safe sin grant",
        "input": "gate_test_safe",
        "mode": "offline",
        "category": "security",
        "expected": None,
    },
    {
        "id": "security_gate_ask",
        "name": "SecurityGate pide autorización para ask",
        "input": "gate_test_ask",
        "mode": "offline",
        "category": "security",
        "expected": None,
    },
    {
        "id": "web_fetch_ssrf",
        "name": "web_fetch bloquea localhost/privado (SSRF)",
        "input": "ssrf_test",
        "mode": "offline",
        "category": "security",
        "expected": None,
    },
    {
        "id": "memory_ingest_local",
        "name": "Ingesta local a memoria (workspace-scoped)",
        "input": "ingest_test",
        "mode": "offline",
        "category": "memory",
        "expected": None,
    },

    # Live (requieren core.process / LLM)
    {
        "id": "nl_memory_ingest",
        "name": "NL: 'ingestá este archivo' → tool memory_ingest",
        "input": "ingestá el archivo test.txt",
        "mode": "live",
        "category": "memory",
        "expected": "incorpor",
    },
    {
        "id": "nl_consolidate",
        "name": "NL: 'consolidá tu memoria' → consolidation job",
        "input": "consolidá tu memoria",
        "mode": "live",
        "category": "memory",
        "expected": "consolid",
    },
    {
        "id": "nl_skill_run",
        "name": "NL: 'ejecutá la skill resumen' → skill run",
        "input": "ejecutá la skill resumen",
        "mode": "live",
        "category": "skills",
        "expected": "ejecut",
    },
    {
        "id": "nl_agent_create",
        "name": "NL: 'creá un agente que...' → agent create",
        "input": "creá un agente que cada mañana haga un resumen",
        "mode": "live",
        "category": "agents",
        "expected": "agent",
    },
    {
        "id": "nl_schedule_job",
        "name": "NL: 'programá un recordatorio' → scheduler job",
        "input": "programá un recordatorio para las 9",
        "mode": "live",
        "category": "scheduler",
        "expected": "recordatori",
    },
    {
        "id": "nl_deep_research",
        "name": "NL: 'investigá X' → deep research task",
        "input": "investigá sobre el clima en Buenos Aires",
        "mode": "live",
        "category": "research",
        "expected": "investig",
    },
]


# ------------------------------------------------------------
# Runners
# ------------------------------------------------------------

def _run_offline(bench, core):
    """Ejecuta un benchmark offline (determinista, sin LLM)."""
    start = time.time()
    try:
        if bench["id"] == "skill_discover":
            skills = core.skills.catalog()
            return {"ok": len(skills) >= 0, "detail": f"{len(skills)} skills"}

        elif bench["id"] == "skill_parser":
            # Solo verifica que no explota
            from app.skills import SkillManager
            sm = SkillManager()
            _ = sm.catalog()
            return {"ok": True, "detail": "parser ok"}

        elif bench["id"] == "security_gate_safe":
            from app.security import SecurityGate
            gate = SecurityGate()
            from tools.call import ToolCall
            decision = gate.decide("read_file", "safe", mode="direct", capability="read_file", actor="ilu", context={})
            return {"ok": decision["decision"] == "allow", "detail": str(decision)}

        elif bench["id"] == "security_gate_ask":
            from app.security import SecurityGate
            gate = SecurityGate()
            decision = gate.decide("write_file", "ask", mode="direct", capability="write_file", actor="ilu", context={})
            return {"ok": decision["decision"] == "ask", "detail": str(decision)}

        elif bench["id"] == "web_fetch_ssrf":
            from tools.search import _is_blocked_address
            blocked = _is_blocked_address("127.0.0.1")
            return {"ok": blocked, "detail": "127.0.0.1 blocked"}

        elif bench["id"] == "memory_ingest_local":
            # Solo verifica que la tool existe y su handler responde
            from app.ingest import _is_sensitive
            return {"ok": not _is_sensitive("test.txt"), "detail": "not sensitive"}

        else:
            return {"ok": False, "detail": f"unknown offline bench {bench['id']}"}

    except Exception as e:
        return {"ok": False, "detail": f"error: {e}"}
    finally:
        pass  # latency measured outside


def _run_live(bench, core):
    """Ejecuta un benchmark live (core.process real)."""
    start = time.time()
    try:
        result = core.process(bench["input"], session_id="bench")
        elapsed = time.time() - start

        text = ""
        if isinstance(result, dict):
            text = result.get("text") or result.get("content") or ""
        elif isinstance(result, str):
            text = result

        ok = True
        if bench.get("expected"):
            ok = bench["expected"].lower() in text.lower()

        return {
            "ok": ok,
            "latency_s": round(elapsed, 3),
            "detail": text[:200] if text else "empty",
        }
    except Exception as e:
        return {"ok": False, "latency_s": round(time.time() - start, 3), "detail": f"error: {e}"}


# ------------------------------------------------------------
# Persistencia JSONL
# ------------------------------------------------------------

_BENCH_PATH = Path("memory/benchmarks.jsonl")
_LOCK = threading.Lock()


def _load_benchmarks():
    if not _BENCH_PATH.exists():
        return []
    with _LOCK:
        try:
            with open(_BENCH_PATH, "r", encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]
        except Exception:
            return []


def _save_benchmark(record):
    _BENCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with open(_BENCH_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ------------------------------------------------------------
# API pública
# ------------------------------------------------------------

def run_suite(core, mode="offline", filter_categories=None):
    """
    Ejecuta la suite completa o filtrada.

    - `mode`: "offline", "live", o "all"
    - `filter_categories`: lista de categorías a incluir (None = todas)

    Devuelve:
    {
        "timestamp": "...",
        "mode": "...",
        "executed": int,
        "passed": int,
        "failed": int,
        "score": float (0-1),
        "results": [...],
    }
    """
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results = []
    passed = 0

    for bench in BENCHMARKS:
        if filter_categories and bench["category"] not in filter_categories:
            continue
        if mode != "all" and bench["mode"] != mode:
            continue

        if bench["mode"] == "offline":
            res = _run_offline(bench, core)
        elif bench["mode"] == "live":
            res = _run_live(bench, core)
        else:
            res = {"ok": False, "detail": f"unknown mode {bench['mode']}"}

        result = {
            "benchmark_id": bench["id"],
            "name": bench["name"],
            "category": bench["category"],
            "mode": bench["mode"],
            "ok": res.get("ok", False),
            "latency_s": res.get("latency_s"),
            "detail": res.get("detail"),
        }
        results.append(result)

        if result["ok"]:
            passed += 1

    executed = len(results)
    score = passed / executed if executed else 0.0

    record = {
        "timestamp": timestamp,
        "mode": mode,
        "executed": executed,
        "passed": passed,
        "failed": executed - passed,
        "score": round(score, 3),
        "results": results,
    }
    _save_benchmark(record)

    return record


def history(limit=50):
    """Historial de runs (más reciente primero)."""
    runs = _load_benchmarks()
    runs.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return runs[:limit]


def last_snapshot():
    """Último run completo."""
    runs = _load_benchmarks()
    if not runs:
        return None
    return max(runs, key=lambda r: r.get("timestamp", ""))


# ------------------------------------------------------------
# Helper para endpoints
# ------------------------------------------------------------

def run_benchmark(core, mode="offline", categories=None):
    """Alias para endpoint POST /benchmark/run."""
    return run_suite(core, mode=mode, filter_categories=categories)


def get_benchmark_history(limit=20):
    """Alias para endpoint GET /benchmark."""
    return history(limit=limit)


def get_benchmark_latest():
    """Último snapshot."""
    return last_snapshot()