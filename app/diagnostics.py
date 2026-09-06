"""
Diagnósticos de salud de I.L.U. (Fase F).

Verifica componentes críticos sin exponer secretos ni llamar a red real
para health checks (salvo petición explícita). Devuelve un snapshot
estructurado para UI y automatización.
"""

import os
import time
import threading

from tools.call import ToolCall


# ------------------------------------------------------------
# Health checks internos (rápidos, no tocan red real)
# ------------------------------------------------------------

def _check_provider_health(core, timeout_s=3):
    """
    Chequea disponibilidad del proveedor LLM.

    No expone claves; intenta `create_runtime_provider` y un
    `generate` trivial con timeout. Si no hay proveedor configurado
    (fallback local), devuelve healthy con nota.
    """
    try:
        from app.providers import create_runtime_provider
        provider = create_runtime_provider()
    except Exception as e:
        return {
            "ok": False,
            "component": "provider",
            "detail": f"no_provider_configured: {e}",
        }

    # health ligero: generate trivial con timeout
    def _generate():
        try:
            out = provider.generate("ping", context=None, tools=None)
            return out is not None
        except Exception:
            return False

    result = {"ok": False, "error": None}
    def worker():
        try:
            result["ok"] = _generate()
        except Exception as e:
            result["ok"] = False
            result["error"] = str(e)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=timeout_s)

    if t.is_alive():
        return {
            "ok": False,
            "component": "provider",
            "detail": "health_timeout",
        }

    detail = provider.__class__.__name__
    if hasattr(provider, "name") and provider.name:
        detail += f" ({provider.name})"
    if result["error"]:
        detail += f": {result['error']}"

    return {"ok": result["ok"], "component": "provider", "detail": detail}


def _check_memory(core):
    """Estado de la memoria (stats, backend, tipos)."""
    stats = core.memory.stats()
    return {
        "ok": True,
        "component": "memory",
        "detail": stats,
    }


def _check_security(core):
    """Estado de seguridad: owner existe, gate wired, grants, spoofing."""
    owner_id = getattr(core.settings, "owner_id", None)
    has_owner = bool(owner_id)
    gate_wired = hasattr(core, "security") and core.security is not None
    grants_active = 0
    if hasattr(core, "grant_store") and core.grant_store is not None:
        try:
            grants_active = len(core.grant_store.list())
        except Exception:
            grants_active = -1
    spoofing_armed = (
        hasattr(core, "spoofing")
        and core.spoofing is not None
    )
    return {
        "ok": has_owner and gate_wired,
        "component": "security",
        "detail": {
            "owner_configured": has_owner,
            "gate_wired": gate_wired,
            "grants_active": grants_active,
            "spoofing_armed": spoofing_armed,
        },
    }


def _check_scheduler(core):
    """Estado del scheduler: store, jobs, daemon."""
    store = getattr(core, "scheduler_store", None)
    if store is None:
        return {"ok": False, "component": "scheduler", "detail": "no_store"}
    try:
        jobs = store.list()
        enabled = sum(1 for j in jobs if j.enabled)
        kinds = {}
        for j in jobs:
            kinds[j.kind] = kinds.get(j.kind, 0) + 1
        return {
            "ok": True,
            "component": "scheduler",
            "detail": {"total": len(jobs), "enabled": enabled, "by_kind": kinds},
        }
    except Exception as e:
        return {"ok": False, "component": "scheduler", "detail": str(e)}


def _check_agents(core):
    """Estado de agentes programados."""
    am = getattr(core, "agents", None)
    if am is None:
        return {"ok": False, "component": "agents", "detail": "no_agent_manager"}
    # El catálogo vive en el AgentStore; `cores.agents` es el orchestrator.
    store = getattr(am, "agent_store", None)
    if store is None and hasattr(am, "list"):
        store = am  # fallback: si el manager expone list() directo
    if store is None:
        return {"ok": False, "component": "agents", "detail": "no_agent_store"}
    try:
        all_agents = store.list()
        enabled = sum(1 for a in all_agents if a.enabled)
        return {
            "ok": True,
            "component": "agents",
            "detail": {"total": len(all_agents), "enabled": enabled},
        }
    except Exception as e:
        return {"ok": False, "component": "agents", "detail": str(e)}


def _check_perception(core):
    """Estado de sensores (capacidades disponibles)."""
    perc = getattr(core, "perception", None)
    if perc is None:
        return {"ok": False, "component": "perception", "detail": "no_perception_engine"}
    try:
        caps = perc.list_capabilities()
        available = sum(1 for c in caps if c.get("available"))
        return {
            "ok": True,
            "component": "perception",
            "detail": {"total": len(caps), "available": available},
        }
    except Exception as e:
        return {"ok": False, "component": "perception", "detail": str(e)}


def _check_learning(core):
    """Estado de aprendizaje/consolidación."""
    # Sin engine dedicado; reportar consolidación reciente en auditoría
    recent = core.audit.recent(limit=20)
    consolidations = [a for a in recent if a.get("action") == "consolidation"]
    return {
        "ok": True,
        "component": "learning",
        "detail": {"recent_consolidations": len(consolidations)},
    }


def _check_jobs(core):
    """Jobs recientes del scheduler (auditoría)."""
    recent = core.audit.recent(limit=50)
    scheduler_jobs = [a for a in recent if a.get("action", "").startswith("scheduler_job")]
    return {
        "ok": True,
        "component": "jobs",
        "detail": {"recent": len(scheduler_jobs)},
    }


# ------------------------------------------------------------
# API pública
# ------------------------------------------------------------

def run(core, deep=False):
    """
    Ejecuta el paquete de health checks.

    - `deep=False` (por defecto): checks rápidos, sin red real.
    - `deep=True`: intenta `generate` real del proveedor (puede tardar).

    Devuelve dict:
    {
        "timestamp": "...Z",
        "healthy": bool,
        "checks": [...],
        "summary": {...},
    }
    """
    checks = [
        _check_memory(core),
        _check_security(core),
        _check_scheduler(core),
        _check_agents(core),
        _check_perception(core),
        _check_learning(core),
        _check_jobs(core),
    ]

    if deep:
        checks.insert(0, _check_provider_health(core))
    else:
        # Check ligero: solo si el proveedor está instanciado
        prov = getattr(core, "provider", None)
        if prov is not None:
            checks.insert(0, {"ok": True, "component": "provider", "detail": "instanciado (deep para test real)"})

    all_ok = all(c.get("ok") for c in checks)
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "healthy": all_ok,
        "checks": checks,
        "summary": {
            "total": len(checks),
            "ok": sum(1 for c in checks if c.get("ok")),
            "failed": sum(1 for c in checks if not c.get("ok")),
        },
    }


# ------------------------------------------------------------
# Helpers para endpoint (si se quiere exportar)
# ------------------------------------------------------------

def quick_check(core):
    """Alias corto: run(core, deep=False)."""
    return run(core, deep=False)


def deep_check(core):
    """Alias: run(core, deep=True)."""
    return run(core, deep=True)