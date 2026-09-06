"""
Deep Research de I.L.U. (Fase D).

Investigación profunda autónoma: descompone una pregunta compleja en
sub-preguntas, busca y recupera fuentes, sintetiza un informe Markdown
con sección de Fuentes, y persiste en memoria semántica.

Reglas de seguridad:
- Todas las herramientas (web_search, web_fetch) pasan por la compuerta
  gateada via core._execute_tool_call (mode="deep_research").
- La skill NUNCA se concede permisos: sin grant → la compuerta abre
  AuthorizationRequest y el paso se detiene (fail-closed).
- Ejecución en segundo plano (_run_in_background) para no bloquear /ask.
- Audita cada paso: deep_research_step, deep_research_done, deep_research_failed.
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from tools.call import ToolCall


@dataclass
class ResearchTask:
    """Tarea de investigación en curso."""
    id: str
    question: str
    status: str = "pending"  # pending, running, done, failed
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    steps: list = field(default_factory=list)
    report: Optional[str] = None
    sources: list = field(default_factory=list)
    error: Optional[str] = None


class DeepResearch:
    """
    Motor de investigación profunda.

    Flujo:
    1. Descomponer pregunta en sub-preguntas (provider.generate con prompt estructurado).
    2. Por cada sub-pregunta: web_search → top 2-3 resultados → web_fetch en 1-2 páginas.
    3. Sintetizar informe Markdown con sección Fuentes.
    4. Persistir en memoria (semantic, source=deep_research).
    5. Notificar y auditar.
    """

    def __init__(
        self,
        core,                    # ILUCore para _execute_tool_call, provider, memory, audit, notify
        task_store_path: Optional[str] = None,
    ):
        self.core = core
        if task_store_path is None:
            task_store_path = os.environ.get(
                "ILU_DEEP_RESEARCH_PATH", "memory/deep_research.jsonl"
            )
        self.task_store_path = task_store_path
        self._lock = threading.RLock()
        self.tasks: dict[str, ResearchTask] = {}
        self._load()

    # ----------------------------------------------------------------------
    # Persistencia
    # ----------------------------------------------------------------------

    def _load(self):
        try:
            os.makedirs(os.path.dirname(self.task_store_path) or ".", exist_ok=True)
            with open(self.task_store_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        task = ResearchTask(**data)
                        self.tasks[task.id] = task
                    except (json.JSONDecodeError, TypeError):
                        continue
        except OSError:
            self.tasks = {}

    def _save(self):
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.task_store_path) or ".", exist_ok=True)
                with open(self.task_store_path, "w", encoding="utf-8") as fh:
                    for task in self.tasks.values():
                        fh.write(json.dumps(self._task_to_dict(task), ensure_ascii=False) + "\n")
                return True
            except OSError:
                return False

    def _task_to_dict(self, task: ResearchTask) -> dict:
        return {
            "id": task.id,
            "question": task.question,
            "status": task.status,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "steps": task.steps,
            "report": task.report,
            "sources": task.sources,
            "error": task.error,
        }

    # ----------------------------------------------------------------------
    # API pública
    # ----------------------------------------------------------------------

    def run(self, question: str) -> dict:
        """
        Lanza una investigación profunda en SEGUNDO PLANO.

        Devuelve inmediatamente con el task_id; la investigación corre en
        un hilo daemon. El chat recibe evento "Empecé la investigación…"
        y al terminar llega el informe vía notificación.
        """
        question = (question or "").strip()
        if not question:
            return {"success": False, "error": "question_required"}

        task_id = uuid.uuid4().hex[:12]
        task = ResearchTask(
            id=task_id,
            question=question,
            status="pending",
        )
        self.tasks[task_id] = task
        self._save()

        # Lanzar en hilo daemon (no bloquea)
        thread = threading.Thread(
            target=self._run_background,
            args=(task_id,),
            daemon=True,
            name=f"ILU-DeepResearch-{task_id[:6]}",
        )
        thread.start()

        # Notificación inicial
        if hasattr(self.core, "notify"):
            self.core.notify(
                f"Empecé la investigación profunda: {question[:80]}",
                level="info",
            )

        self.core.audit.record(
            "ilu", "deep_research_started",
            task_id=task_id, question=question,
        )

        return {
            "success": True,
            "task_id": task_id,
            "status": "pending",
            "message": "Investigación en curso. Recibirás notificación al terminar.",
        }

    def get_status(self, task_id: str) -> Optional[dict]:
        task = self.tasks.get(task_id)
        if task is None:
            return None
        return self._task_to_dict(task)

    def list_tasks(self, status: Optional[str] = None, limit: int = 50) -> list:
        tasks = list(self.tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return [self._task_to_dict(t) for t in tasks[:limit]]

    # ----------------------------------------------------------------------
    # Ejecución en segundo plano
    # ----------------------------------------------------------------------

    def _run_background(self, task_id: str):
        task = self.tasks.get(task_id)
        if task is None:
            return

        task.status = "running"
        task.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._save()

        try:
            # 1. Descomponer pregunta
            sub_questions = self._decompose(task.question)
            task.steps.append({
                "step": "decompose",
                "sub_questions": sub_questions,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            self._save()

            # 2. Investigar cada sub-pregunta
            all_sources = []
            findings = []

            for i, sub_q in enumerate(sub_questions):
                task.steps.append({
                    "step": "research_sub",
                    "sub_question": sub_q,
                    "index": i + 1,
                    "total": len(sub_questions),
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                })
                self._save()

                sub_result = self._research_sub_question(sub_q)
                findings.append({
                    "sub_question": sub_q,
                    "result": sub_result,
                })
                all_sources.extend(sub_result.get("sources", []))

                task.steps.append({
                    "step": "sub_done",
                    "sub_question": sub_q,
                    "sources_found": len(sub_result.get("sources", [])),
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                })
                self._save()

            # 3. Sintetizar informe final
            task.steps.append({
                "step": "synthesize",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            self._save()

            report = self._synthesize_report(task.question, findings, all_sources)

            # 4. Guardar en memoria semántica
            if report:
                self.core.memory.remember(
                    report,
                    memory_type="semantic",
                    importance=8,
                    source="deep_research",
                    tags=["deep_research", "investigacion"],
                    metadata={
                        "task_id": task_id,
                        "question": task.question,
                        "sources_count": len(all_sources),
                    },
                )

            task.status = "done"
            task.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            task.report = report
            task.sources = all_sources
            self._save()

            # Notificación final
            if hasattr(self.core, "notify"):
                self.core.notify(
                    f"Investigación completada: {task.question[:80]}\n{len(all_sources)} fuentes consultadas.",
                    level="info",
                )

            self.core.audit.record(
                "ilu", "deep_research_done",
                task_id=task_id, question=task.question,
                sources_count=len(all_sources),
            )

        except Exception as e:
            task.status = "failed"
            task.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            task.error = str(e)
            self._save()

            if hasattr(self.core, "notify"):
                self.core.notify(
                    f"Investigación falló: {task.question[:80]} — {str(e)[:100]}",
                    level="error",
                )

            self.core.audit.record(
                "ilu", "deep_research_failed",
                task_id=task_id, question=task.question, error=str(e),
            )

    # ----------------------------------------------------------------------
    # Pasos internos
    # ----------------------------------------------------------------------

    def _decompose(self, question: str) -> list[str]:
        """
        Descompone la pregunta en sub-preguntas atómicas.

        Usa el proveedor con un prompt estructurado para obtener una lista
        JSON de sub-preguntas. Fallback determinista si no hay proveedor.
        """
        if not self.core.provider:
            return self._decompose_fallback(question)

        prompt = (
            "Descompón la siguiente pregunta de investigación en 3-5 "
            "sub-preguntas atómicas y específicas que se puedan responder "
            "buscando en la web. Devuelve SOLO un array JSON de strings, "
            "sin texto extra, sin markdown.\n\n"
            f"Pregunta: {question}\n\n"
            "Ejemplo de salida:\n"
            '["Sub-pregunta 1", "Sub-pregunta 2", "Sub-pregunta 3"]'
        )

        try:
            result = self.core.provider.generate(prompt, [], [])
            if isinstance(result, dict) and result.get("type") == "text":
                content = result.get("content", "").strip()
                # Intentar extraer JSON
                import re
                match = re.search(r'\[.*\]', content, re.DOTALL)
                if match:
                    sub_qs = json.loads(match.group(0))
                    if isinstance(sub_qs, list) and all(isinstance(q, str) for q in sub_qs):
                        return sub_qs[:5]
        except Exception:
            pass

        return self._decompose_fallback(question)

    def _decompose_fallback(self, question: str) -> list[str]:
        """Descomposición heurística simple por conectores."""
        import re
        # Dividir por conectores lógicos
        parts = re.split(r'\b(y|e|también|además|;|,)\b', question, flags=re.IGNORECASE)
        sub_qs = [p.strip() for p in parts if p.strip() and p.strip().lower() not in ("y", "e", "también", "además")]
        if len(sub_qs) < 2:
            return [question]
        return sub_qs[:5]

    def _research_sub_question(self, sub_question: str) -> dict:
        """
        Investiga una sub-pregunta: web_search → web_fetch (1-2 páginas).

        Todas las tools pasan por core._execute_tool_call (gateado).
        """
        sources = []
        content_parts = []

        # web_search
        search_call = ToolCall(
            tool="web_search",
            arguments={"query": sub_question, "max_results": 3},
            reason=f"deep_research: {sub_question}",
        )
        search_result = self.core._execute_tool_call(search_call, mode="deep_research")

        if not search_result.get("success"):
            return {
                "sub_question": sub_question,
                "content": f"Búsqueda falló: {search_result.get('error')}",
                "sources": [],
            }

        results = search_result.get("results", [])
        if not results:
            return {
                "sub_question": sub_question,
                "content": "Sin resultados de búsqueda.",
                "sources": [],
            }

        # web_fetch en los 2 primeros resultados con URL
        fetched = 0
        for result in results:
            if fetched >= 2:
                break
            url = result.get("url")
            if not url:
                continue

            fetch_call = ToolCall(
                tool="web_fetch",
                arguments={"url": url, "max_bytes": 32000},
                reason=f"deep_research fetch: {sub_question}",
            )
            fetch_result = self.core._execute_tool_call(fetch_call, mode="deep_research")

            if fetch_result.get("success"):
                content = fetch_result.get("content", "")
                if content:
                    content_parts.append(f"Fuente: {url}\n{content[:3000]}")
                    sources.append({
                        "url": url,
                        "snippet": result.get("snippet", "")[:200],
                        "sub_question": sub_question,
                    })
                    fetched += 1
            else:
                # Si falla por gate (ask), registrar y continuar
                if fetch_result.get("error") == "authorization_required":
                    content_parts.append(f"[Acceso denegado a {url} — requiere autorización]")
                sources.append({
                    "url": url,
                    "snippet": result.get("snippet", "")[:200],
                    "sub_question": sub_question,
                    "error": fetch_result.get("error"),
                })

        return {
            "sub_question": sub_question,
            "content": "\n\n---\n\n".join(content_parts) if content_parts else "Sin contenido recuperado.",
            "sources": sources,
        }

    def _synthesize_report(self, question: str, findings: list, all_sources: list) -> str:
        """Sintetiza el informe final en Markdown con sección Fuentes."""
        if not self.core.provider:
            return self._synthesize_fallback(question, findings, all_sources)

        # Preparar material para síntesis
        material_parts = []
        for f in findings:
            material_parts.append(
                f"Sub-pregunta: {f['sub_question']}\n{f['result'].get('content', '')}"
            )

        material = "\n\n".join(material_parts)

        prompt = (
            "Eres I.L.U., asistente de investigación. Redacta un informe "
            "profundo en español, en formato Markdown, que responda a la "
            "pregunta original usando SOLO el material proporcionado.\n\n"
            "Estructura:\n"
            "1. Resumen ejecutivo (3-5 líneas)\n"
            "2. Desarrollo por temas/sub-preguntas\n"
            "3. Conclusiones\n"
            "4. Fuentes (lista numerada con URLs)\n\n"
            "Reglas:\n"
            "- No inventes información; si el material no cubre algo, dilo.\n"
            "- Cita las fuentes entre corchetes [1], [2]... referenciando "
            "la lista de Fuentes al final.\n"
            "- Tono profesional, claro y estructurado.\n\n"
            f"Pregunta original: {question}\n\n"
            f"Material recolectado:\n{material}\n\n"
            f"Fuentes disponibles ({len(all_sources)}):\n"
            + "\n".join(f"[{i+1}] {s['url']} — {s.get('snippet','')[:100]}" for i, s in enumerate(all_sources))
        )

        try:
            result = self.core.provider.generate(prompt, [], [])
            if isinstance(result, dict) and result.get("type") == "text":
                content = result.get("content", "").strip()
                # Asegurar sección Fuentes al final
                if "## Fuentes" not in content and "### Fuentes" not in content:
                    content += "\n\n## Fuentes\n"
                    for i, s in enumerate(all_sources, 1):
                        content += f"{i}. {s['url']}\n"
                return content
        except Exception:
            pass

        return self._synthesize_fallback(question, findings, all_sources)

    def _synthesize_fallback(self, question: str, findings: list, all_sources: list) -> str:
        """Síntesis determinista simple sin LLM."""
        lines = [
            f"# Investigación: {question}",
            "",
            "## Resumen ejecutivo",
            f"Se investigaron {len(findings)} sub-preguntas consultando "
            f"{len(all_sources)} fuentes web.",
            "",
            "## Desarrollo",
        ]
        for f in findings:
            lines.append(f"### {f['sub_question']}")
            lines.append(f['result'].get('content', 'Sin contenido.')[:1000])
            lines.append("")

        lines.append("## Conclusiones")
        lines.append("La investigación reunió información de fuentes abiertas. "
                     "Ver detalles en cada sección anterior.")
        lines.append("")
        lines.append("## Fuentes")
        for i, s in enumerate(all_sources, 1):
            lines.append(f"{i}. {s['url']} — {s.get('snippet','')[:150]}")
        return "\n".join(lines)


def create_deep_research(core, task_store_path: Optional[str] = None) -> DeepResearch:
    """Factory para crear DeepResearch inyectando el core."""
    return DeepResearch(core=core, task_store_path=task_store_path)