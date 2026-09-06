"""
Consolidación de memoria de I.L.U. (Fase E).

Comprime el ruido creciente de memorias `temporal` (ingestas, material de
trabajo) agrupándolas por tema y resumiendo cada grupo en un recuerdo
`semantic` (source `consolidation`). Las originales NO se borran: se
marcan `consolidated=true` en su metadata para no volver a agruparse.

Reglas:
- NO destructivo: nunca se elimina un recuerdo original.
- Una sola pasada por tema: si un grupo ya fue consolidado en esta pasada
  no se reconsolida (evita ruido).
- El resumen lo hace el proveedor cuando está disponible; si no, se
  conserva el material como recuerdo `semantic` concatenado (honesto).
- Se llama al arranque y vía scheduler (job `consolidation`).
"""

import json
import threading
import time
from datetime import datetime, timezone

from memory.semantic import tokenize


# Umbrales por defecto de la pasada.
_MIN_AGE_DAYS = 3        # temporal de hace más de N días
_MIN_IMPORTANCE = 5      # importancia alta se conserva sin tocar
_MAX_GROUP = 8           # recuerdos por grupo temático
_AGE = 60 * 60 * 24      # seconds en un día


def _now():
    return time.time()


def _age_days(updated_at):
    """
    Edad en días de una marca ISO (0 si no parsea).

    El backend guarda marcas UTC (`...Z`, con/sin fracción); se parsean
    SIEMPRE como UTC para no desviar la edad por el offset local
    (`time.mktime` es local-time; aquí se usa `fromisoformat` + UTC).
    """
    if not updated_at:
        return 0
    try:
        ts = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except ValueError:
        return 0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - ts).total_seconds() / _AGE
    return max(0.0, days)


def _tokens(text):
    """Tokens léxicos normalizados (stop-list mínima)."""
    stop = {
        "el", "la", "los", "las", "de", "del", "en", "y", "a", "al",
        "que", "un", "una", "es", "se", "con", "para", "por", "su",
        "lo", "como", "más", "mas", "este", "esta",
    }
    return [
        t for t in tokenize(text or "")
        if len(t) > 2 and t not in stop
    ]


def group_by_topic(records):
    """
    Agrupa recuerdos por traslape léxico de a pares.
    Devuelve lista de grupos (cada grupo: lista de records).
    """
    if not records:
        return []

    tokens = [
        set(_tokens(r.get("content", "")))
        for r in records
    ]

    n = len(records)
    remaining = set(range(n))
    groups = []

    while remaining:
        pivot = min(remaining)
        group = [pivot]
        remaining.discard(pivot)

        pivot_tokens = tokens[pivot]

        # Se amplía mientras haya un record que comparta tokens, sin
        # exceder _MAX_GROUP por grupo (el tope se respeta al añadir, no
        # solo al romper: evitamos sobrepasar el grupo en una pasada).
        while True:
            extended = False
            for i in sorted(remaining):
                if len(group) >= _MAX_GROUP:
                    break
                shared = len(pivot_tokens & tokens[i])
                if shared >= 2:
                    group.append(i)
                    remaining.discard(i)
                    pivot_tokens |= tokens[i]
                    extended = True
            if not extended or len(group) >= _MAX_GROUP:
                break

        groups.append(
            [records[i] for i in sorted(group)]
        )

    return groups


def _candidate_records(router, min_age_days=None, min_importance=None):
    """Memorias temporal viejas, de importancia baja y no consolidadas."""
    min_age_days = min_age_days if min_age_days is not None else _MIN_AGE_DAYS
    min_importance = (
        min_importance if min_importance is not None else _MIN_IMPORTANCE
    )

    records = router.list_by_type("episodic", limit=500)

    candidates = []
    for r in (records or []):
        # El backend devuelve MemoryRecord (objeto); se normaliza a dict
        # para que group_by_topic/consolidate usen un contrato estable.
        # `to_dict()` NO incluye la clave → se restaura desde el objeto.
        if not isinstance(r, dict):
            key_of = getattr(r, "key", None)
            to_dict = getattr(r, "to_dict", None)
            r = to_dict() if callable(to_dict) else None
            if r is None:
                continue
            r["key"] = key_of
        if r.get("importance", 0) >= min_importance:
            continue
        meta = r.get("metadata") or {}
        if meta.get("consolidated"):
            continue
        if _age_days(r.get("updated_at") or "") < min_age_days:
            continue
        candidates.append(r)

    return candidates


def consolidate(router, synthesize=None, min_age_days=None, min_importance=None):
    """
    Consolidación no destructiva de la memoria temporal.

    - `router`: MemoryRouter del núcleo.
    - `synthesize`: callable opcional de síntesis (provider).
    Devuelve un dict reporte (grupos, keys creadas, sin borrar nada).
    """
    candidates = _candidate_records(
        router, min_age_days, min_importance
    )

    if not candidates:
        return {
            "success": True,
            "groups": 0,
            "consolidated": 0,
            "note": "nada_para_consolidar",
            "created_keys": [],
            "temporal_before": 0,
        }

    groups = group_by_topic(candidates)

    created_keys = []
    consolidated_count = 0

    for group in groups:
        if len(group) < 2:
            consolidated_count += 1
            continue

        contents = "\n\n".join(
            "· " + str(r.get("content", "")).strip()
            for r in group
        )
        keys = [r.get("key") for r in group]

        summary = contents

        if callable(synthesize):
            try:
                prompt = (
                    "Consolida en un párrafo breve de español el tema "
                    "común de estas notas sin inventar datos:\n\n%s"
                    % contents[:6000]
                )
                out = synthesize(prompt)
                if isinstance(out, dict) and out.get("content"):
                    summary = out["content"]
                elif isinstance(out, str):
                    summary = out
            except Exception:
                summary = contents

        meta = {
            "source": "consolidation",
            "groups": keys,
            "original_keys": keys,
        }

        record = router.remember(
            summary,
            memory_type="semantic",
            importance=4,
            source="consolidation",
            tags=["consolidated"],
            metadata=meta,
        )

        if record is not None:
            created_keys.append(record.key)

        # Marcar las originales como consolidadas (NO se borran).
        for r in group:
            key = r.get("key")
            existing = router.get(key)
            if existing is None:
                continue
            try:
                existing.metadata = dict(existing.metadata or {})
                existing.metadata["consolidated"] = True
                router.backend.save(existing)
                consolidated_count += 1
            except Exception:
                continue

    return {
        "success": True,
        "groups": len(groups),
        "consolidated": consolidated_count,
        "created_keys": created_keys,
        "temporal_before": len(candidates),
    }