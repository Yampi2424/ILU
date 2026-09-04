"""
I.L.U. — Motor de Proactividad (Bloque C: JARVIS Evolution).

I.L.U. debe ANTICIPARSE a las necesidades de su OWNER/familia sin
violar nunca los permisos. Este motor mantiene reglas proactivas
(recordatorios, check-ins, follow-ups, sugerencias) y las evalúa contra
el tiempo y el estado.

Regla de oro de seguridad: la proactividad NUNCA ejecuta por sí sola.
- En modo MANUAL: solo produce una SUGERENCIA que el owner aprueba.
- En modo ASISTIDO/AUTÓNOMO: puede actuar sobre una CAPACIDAD SOLO si
  hay un grant activo para ella; si no, abre una sugerencia/recordatorio.

El motor es la fuente de verdad de las reglas proactivas. Quién las
dispara (un hilo del servidor, el arranque de una sesión) es
responsabilidad del orquestador.
"""

import json
import os
import threading
import time
import uuid


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ProactivityEngine:
    """
    Reglas proactivas persistentes y evaluación segura.

    Tipos de regla:
      - reminder:   recordatorio puntual/por cadencia de algo importante.
      - check_in:   preguntar al owner por el estado de algo.
      - follow_up:  dar seguimiento a una tarea u objetivo en curso.
      - suggestion: proponer una acción que requeriría un grant.
    """

    KINDS = ("reminder", "check_in", "follow_up", "suggestion")

    def __init__(self, path=None):
        if path is None:
            path = os.environ.get(
                "ILU_PROACTIVITY_PATH",
                "memory/proactivity.jsonl"
            )

        self.path = path
        self.rules = {}
        self._lock = threading.RLock()

        self._load()

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _load(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rule = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(rule, dict) and rule.get("id"):
                        self.rules[rule["id"]] = rule
        except OSError:
            self.rules = {}

    def _save(self):
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                with open(self.path, "w", encoding="utf-8") as handle:
                    for rule in self.rules.values():
                        handle.write(
                            json.dumps(rule, ensure_ascii=False) + "\n"
                        )
                return True
            except OSError:
                return False

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, kind, text, cadence_minutes=None, due_at=None,
            capability=None):
        with self._lock:
            kind = (kind or "").strip()

            if kind not in self.KINDS:
                raise ValueError("invalid_proactivity_kind")

            text = (text or "").strip()

            if not text:
                raise ValueError("proactivity_text_required")

            rule_id = uuid.uuid4().hex[:12]

            rule = {
                "id": rule_id,
                "kind": kind,
                "text": text,
                "cadence_minutes": cadence_minutes,
                "due_at": due_at,
                "capability": capability,
                "enabled": True,
                "last_fired_at": None,
                "created_at": _now(),
            }

            self.rules[rule_id] = rule
            self._save()
            return rule

    def list(self, enabled=None, kind=None, limit=100):
        rules = list(self.rules.values())

        if enabled is not None:
            rules = [r for r in rules if r.get("enabled") is enabled]

        if kind is not None:
            rules = [r for r in rules if r.get("kind") == kind]

        rules.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return rules[:limit]

    def get(self, rule_id):
        return self.rules.get(rule_id)

    def set_enabled(self, rule_id, enabled):
        with self._lock:
            rule = self.rules.get(rule_id)

            if rule is None:
                return None

            rule["enabled"] = bool(enabled)
            rule["updated_at"] = _now()
            self._save()
            return rule

    def remove(self, rule_id):
        with self._lock:
            if rule_id not in self.rules:
                return False
            del self.rules[rule_id]
            self._save()
            return True

    # ------------------------------------------------------------------
    # Evaluación
    # ------------------------------------------------------------------

    def _due(self, rule, now):
        # Regla deshabilitada: no due.
        if not rule.get("enabled", True):
            return False

        # Una vez disparada, no vuelve a sonar hasta que pase la cadencia.
        last = rule.get("last_fired_at")
        cadence = rule.get("cadence_minutes")

        if last and cadence:
            try:
                last_ts = time.mktime(
                    time.strptime(last, "%Y-%m-%dT%H:%M:%SZ")
                )
            except ValueError:
                last_ts = 0

            if (now - last_ts) < cadence * 60:
                return False

        # Si tiene una fecha puntual y aún no llegó, no es due.
        due_at = rule.get("due_at")

        if due_at:
            try:
                due_ts = time.mktime(
                    time.strptime(due_at, "%Y-%m-%dT%H:%M:%SZ")
                )
            except ValueError:
                due_ts = 0

            if now < due_ts:
                return False

        return True

    def due_now(self, limit=10):
        """Reglas vencidas en este instante (sin marcarlas como disparadas)."""
        now = time.time()
        return [
            rule for rule in self.rules.values()
            if self._due(rule, now)
        ][:limit]

    def fire(self, rule_id, autonomy="manual", has_grant=None):
        """
        Evalúa y devuelve la acción proactiva de una regla vencida.

        NUNCA ejecuta por sí sola. Devuelve un dict:
          {"action": "suggest"|"act"|"skip", "text": ..., "rule": {...}}

        - Si la regla necesita una capacidad y no hay grant (o estamos en
          manual), la acción es SOLO "suggest" (I.L.U. propone, no actúa).
        - En assisted/autonomous CON grant, la acción es "act".
        """
        with self._lock:
            rule = self.rules.get(rule_id)

            if rule is None:
                return None

            if not self._due(rule, time.time()):
                return {"action": "skip", "rule": rule}

            capability = rule.get("capability")
            grant = bool(has_grant) if has_grant is not None else False

            if capability and autonomy == "manual":
                action = "suggest"
            elif capability and not grant:
                action = "suggest"
            else:
                action = "act"

            rule["last_fired_at"] = _now()
            rule["updated_at"] = _now()
            self._save()

            return {
                "action": action,
                "text": rule["text"],
                "rule": rule,
            }

    def stats(self):
        counts = {}

        for rule in self.rules.values():
            kind = rule.get("kind", "reminder")
            counts[kind] = counts.get(kind, 0) + 1

        return {
            "total": len(self.rules),
            "counts": counts,
        }
