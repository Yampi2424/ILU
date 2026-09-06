"""
Fase B — Skills: catálogo, parser y ejecución gateada.

Verifica que las skills de I.L.U. encadenan herramientas SIEMPRE a
través de la compuerta (`core._execute_tool_call(mode="skill")`):

- una tool permitida corre;
- una tool que exige autorización se DETIENE (handler nunca se ejecuta),
  se audita y abre una AuthorizationRequest;
- la skill NUNCA se autoconcede permisos (tras un run no hay grants
  nuevos);
- los ciclos de sub-skills y el límite de profundidad abortan
  (fail-closed);
- la detección por lenguaje natural listaa y ejecuta skills;
- la tool `memory_ingest` (Fase E) es seguro de workspace y el guard
  SSRF de `web_fetch` bloquea la red interna.

Sin red, sin LLM real: el proveedor se sustituye por fake y el catálogo
se aísla en tmp_path.
"""

import os

import pytest

from app.core import ILUCore
from app.skills import (
    SkillManager,
    parse_skill_markdown,
    create_skill_manager,
)
from tools.search import web_fetch, _is_blocked_address


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

class FakeProvider:
    """Proveedor simulado para la síntesis (sin red)."""

    def __init__(self, answer="Síntesis de prueba."):
        self.name = "fake"
        self.version = "0.0.1"
        self.answer = answer
        self.last_prompt = None

    def generate(self, message, context=None, tools=None):
        self.last_prompt = message
        return {"type": "text", "content": self.answer}


def make_core(monkeypatch, tmp_path, skills_dir=None):
    """ILUCore aislado (stores en tmp, proveedor fake, skills de tmp)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_POOLED", raising=False)
    monkeypatch.delenv("ILU_AI_PROVIDER", raising=False)
    monkeypatch.delenv("ILU_AUTONOMY", raising=False)

    core = ILUCore()
    core.provider = FakeProvider()

    if skills_dir is not None:
        core.skills.directory = str(skills_dir)
        core.skills.discover()

    return core


def _write_skill(directory, name, body):
    """Escribe un skill .md en el directorio de prueba."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name + ".md")
    path.write_text(body, encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------

class TestParser:

    def test_parses_tool_steps(self):
        text = (
            "---\n"
            "name: ejemplo\n"
            "description: Hace algo con la hora.\n"
            "variables:\n"
            "  - quien\n"
            "steps:\n"
            "  - tool: system_time\n"
            "    args:\n"
            "      quien: \"{quien}\"\n"
            "    note: \"Consultando la hora.\"\n"
            "  - synthesize: true\n"
            "    note: \"Un resumen.\"\n"
            "---\n"
            "# Skill: ejemplo\n"
            "Documentación libre.\n"
        )

        skill = parse_skill_markdown(text)

        assert skill.name == "ejemplo"
        assert skill.variables == ["quien"]
        assert len(skill.steps) == 2

        first = skill.steps[0]
        assert first.kind == "tool"
        assert first.tool == "system_time"
        assert first.args == {"quien": "{quien}"}

        second = skill.steps[1]
        assert second.kind == "synthesize"
        assert second.synthesize is True

    def test_parses_sub_skill_step(self):
        text = (
            "---\n"
            "name: padre\n"
            "description: Reusa hija.\n"
            "steps:\n"
            "  - skill: hija\n"
            "    note: \"Reuso.\"\n"
            "---\n"
        )

        skill = parse_skill_markdown(text)

        assert skill.steps[0].kind == "skill"
        assert skill.steps[0].skill == "hija"

    def test_missing_frontmatter_rejected(self):
        with pytest.raises(ValueError):
            parse_skill_markdown("# sin frontmatter\n")

    def test_missing_name_rejected(self):
        text = "---\ndescription: sin nombre\nsteps: []\n---\n"
        with pytest.raises(ValueError):
            parse_skill_markdown(text)

    def test_step_without_action_rejected(self):
        text = (
            "---\n"
            "name: malo\n"
            "description: paso sin acción.\n"
            "steps:\n"
            "  - note: nada\n"
            "---\n"
        )
        with pytest.raises(ValueError):
            parse_skill_markdown(text)


# ----------------------------------------------------------------------
# Descubrimiento / catálogo
# ----------------------------------------------------------------------

class TestCatalog:

    def test_discover_loads_skills(self, tmp_path):
        _write_skill(
            tmp_path, "a",
            "---\nname: a\ndescription: A.\nsteps:\n"
            "  - tool: system_time\n---\n"
        )
        _write_skill(
            tmp_path, "b",
            "---\nname: b\ndescription: B.\nsteps:\n"
            "  - tool: system_time\n---\n"
        )

        manager = SkillManager(directory=str(tmp_path))

        assert sorted(s["name"] for s in manager.catalog()) == ["a", "b"]
        assert manager.has("a") is True
        assert manager.get("b").description == "B."

    def test_invalid_skill_omitted(self, tmp_path):
        # Skill inválida (sin frontmatter) NO se ofrece: fail-closed.
        _write_skill(tmp_path, "valida", (
            "---\nname: valida\ndescription: OK.\nsteps:\n"
            "  - tool: system_time\n---\n"
        ))
        _write_skill(tmp_path, "rota", "# Sin frontmatter\n")

        manager = SkillManager(directory=str(tmp_path))

        assert manager.has("valida") is True
        assert manager.has("rota") is False


# ----------------------------------------------------------------------
# Ejecución gateada
# ----------------------------------------------------------------------

class TestRunGated:

    def test_permitted_tool_runs_through_core(self, monkeypatch, tmp_path):
        # Skill que solo usa una tool "safe": el gate la deja pasar.
        _write_skill(tmp_path, "hora", (
            "---\n"
            "name: hora\n"
            "description: Dice la hora.\n"
            "steps:\n"
            "  - tool: system_time\n"
            "    note: \"Consultando la hora del sistema.\"\n"
            "---\n"
        ))

        core = make_core(monkeypatch, tmp_path, skills_dir=tmp_path)

        result = core.skills.run("hora", actor="ilu")

        assert result["success"] is True
        assert result["audited"] is True
        assert "datetime" in str(result["summary"])

        # El gate audito el intento permitido en modo "skill".
        audit = core.audit.recent()
        assert any(
            entry.get("action") == "tool_attempt"
            and entry.get("decision") == "allow"
            and entry.get("tool") == "system_time"
            and entry.get("mode") == "skill"
            for entry in audit
        )

    def test_unpermitted_tool_denied_handler_not_executed(
        self, monkeypatch, tmp_path
    ):
        # Skill cuyo paso usa write_file (permission "ask", sin grant).
        # La compuerta NO ejecuta el handler: abre una AuthorizationRequest
        # y el skill aborta (fail-closed).
        _write_skill(tmp_path, "escribe", (
            "---\n"
            "name: escribe\n"
            "description: Escribe un archivo de prueba.\n"
            "steps:\n"
            "  - tool: write_file\n"
            "    args:\n"
            "      path: \"prueba.txt\"\n"
            "      content: \"hola\"\n"
            "    note: \"Escribiendo…\"\n"
            "---\n"
        ))

        core = make_core(monkeypatch, tmp_path, skills_dir=tmp_path)

        result = core.skills.run("escribe", actor="ilu")

        assert result["success"] is False
        assert result["error"] == "tool_step_failed"
        assert result["tool"] == "write_file"
        assert result["detail"].get("authorization") == "ask"

        # La compuerta audito el intento denegado.
        audit = core.audit.recent()
        assert any(
            entry.get("action") == "tool_attempt"
            and entry.get("decision") == "ask"
            and entry.get("tool") == "write_file"
            and entry.get("mode") == "skill"
            for entry in audit
        )

        # Hay una AuthorizationRequest abierta para que el owner decida.
        pending = core.auth_requests.pending()
        assert any(
            req.get("capability") == "write_file"
            for req in pending
        )

        # El workspace quedó intacto: no se escribió nada (el handler
        # de write_file jamás fue ejecutado por la compuerta).
        from tools.filesystem import workspace_root
        ws = workspace_root()
        if ws and os.path.isdir(ws):
            assert "prueba.txt" not in os.listdir(ws)

    def test_run_never_creates_grants(self, monkeypatch, tmp_path):
        # Incluso cuando un paso es DENEGADO y pide autorización, la
        # skill jamás se autoconcede un grant.
        _write_skill(tmp_path, "escribe", (
            "---\n"
            "name: escribe\n"
            "description: Escribe un archivo de prueba.\n"
            "steps:\n"
            "  - tool: write_file\n"
            "    args:\n"
            "      path: \"prueba.txt\"\n"
            "      content: \"hola\"\n"
            "---\n"
        ))

        core = make_core(monkeypatch, tmp_path, skills_dir=tmp_path)

        grants_before = len(core.grant_store.list())

        core.skills.run("escribe", actor="ilu")

        grants_after = core.grant_store.list()

        assert len(grants_after) == grants_before == 0

    def test_variables_materialized(self, monkeypatch, tmp_path):
        # La plantilla {path} se resuelve desde las variables de llamada.
        _write_skill(tmp_path, "resume", (
            "---\n"
            "name: resume\n"
            "description: Lee un archivo.\n"
            "variables:\n"
            "  - path\n"
            "steps:\n"
            "  - tool: read_file\n"
            "    args:\n"
            "      path: \"{path}\"\n"
            "    note: \"Leyendo…\"\n"
            "---\n"
        ))

        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "notas.txt").write_text("contenido de prueba", encoding="utf-8")
        os.environ["ILU_WORKSPACE"] = str(ws)

        core = make_core(monkeypatch, tmp_path, skills_dir=tmp_path)

        result = core.skills.run(
            "resume", variables={"path": "notas.txt"}, actor="ilu"
        )

        assert result["success"] is True
        assert "contenido de prueba" in str(result["summary"])


# ----------------------------------------------------------------------
# Ciclos y profundidad
# ----------------------------------------------------------------------

class TestCycleAndDepth:

    def test_self_reference_aborts(self, tmp_path):
        _write_skill(tmp_path, "loop", (
            "---\nname: loop\ndescription: Se llama a sí misma.\nsteps:\n"
            "  - skill: loop\n---\n"
        ))

        manager = SkillManager(directory=str(tmp_path))

        result = manager.run("loop", actor="ilu")

        assert result["success"] is False
        assert result["error"] == "skill_self_reference"

    def test_cycle_between_skills_aborts(self, tmp_path):
        _write_skill(tmp_path, "a", (
            "---\nname: a\ndescription: Llama a b.\nsteps:\n"
            "  - skill: b\n---\n"
        ))
        _write_skill(tmp_path, "b", (
            "---\nname: b\ndescription: Llama a a.\nsteps:\n"
            "  - skill: a\n---\n"
        ))

        manager = SkillManager(directory=str(tmp_path))

        result = manager.run("a", actor="ilu")

        assert result["success"] is False
        assert result.get("error") in ("skill_cycle", "skill_self_reference")

    def test_depth_limit_aborts(self, tmp_path):
        # Cadena de sub-skills que excede el límite de profundidad.
        for i in range(6):
            nxt = "b%d" % (i + 1) if i < 5 else "system_time"
            _write_skill(tmp_path, "b%d" % i, (
                "---\nname: b%d\ndescription: nivel %d.\nsteps:\n"
                "  - skill: %s\n---\n" % (i, i, nxt)
            ))

        manager = SkillManager(directory=str(tmp_path))

        result = manager.run("b0", actor="ilu")

        assert result["success"] is False
        assert result["error"] == "skill_depth_exceeded"

    def test_missing_sub_skill_aborts(self, tmp_path):
        _write_skill(tmp_path, "padre", (
            "---\nname: padre\ndescription: hija inexistente.\nsteps:\n"
            "  - skill: fantasma\n---\n"
        ))

        manager = SkillManager(directory=str(tmp_path))

        result = manager.run("padre", actor="ilu")

        assert result["success"] is False
        assert result["error"] == "skill_not_found"


# ----------------------------------------------------------------------
# Detección por lenguaje natural (core._skill_command)
# ----------------------------------------------------------------------

class TestNLCommand:

    def test_list_skills(self, monkeypatch, tmp_path):
        _write_skill(tmp_path, "hora", (
            "---\nname: hora\ndescription: Dice la hora.\nsteps:\n"
            "  - tool: system_time\n---\n"
        ))

        core = make_core(monkeypatch, tmp_path, skills_dir=tmp_path)

        result = core.process("qué skills tenés")

        assert result["success"] is True
        assert result["intent"] == "skill_list"
        assert any(s["name"] == "hora" for s in result["skills"])

    def test_run_skill_via_nl(self, monkeypatch, tmp_path):
        _write_skill(tmp_path, "hora", (
            "---\nname: hora\ndescription: Dice la hora.\nsteps:\n"
            "  - tool: system_time\n    note: \"Consultando la hora.\"\n---\n"
        ))

        core = make_core(monkeypatch, tmp_path, skills_dir=tmp_path)

        result = core.process("ejecutá la skill hora")

        assert result["success"] is True
        assert result["intent"] == "skill_run"
        assert result["skill_result"]["name"] == "hora"
        assert result["skill_result"]["success"] is True

    def test_unknown_skill_via_nl(self, monkeypatch, tmp_path):
        core = make_core(monkeypatch, tmp_path)

        result = core.process("ejecutá la skill inexistente")

        assert result["success"] is True
        assert result["intent"] == "skill_run"
        assert result["skill_result"]["error"] == "skill_not_found"


# ----------------------------------------------------------------------
# memory_ingest (Fase E) — workspace-scoped y fail-closed
# ----------------------------------------------------------------------

class TestMemoryIngestTool:

    def test_ingest_registered_and_safe(self, monkeypatch, tmp_path):
        core = make_core(monkeypatch, tmp_path)

        assert core.tools.has_tool("memory_ingest")
        assert core.tools.get_permission("memory_ingest") == "safe"

    def test_ingest_rejects_outside_workspace(self, monkeypatch, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        os.environ["ILU_WORKSPACE"] = str(ws)

        core = make_core(monkeypatch, tmp_path)

        outside = tmp_path / "fuera.txt"
        outside.write_text("secreto", encoding="utf-8")

        # tool directa (gate permitido por ser safe) pero handler
        # rechaza rutas fuera del workspace: fail-closed.
        result = core.tools.execute("memory_ingest", source=str(outside))

        assert result["success"] is False
        assert result["error"] == "outside_workspace"

    def test_ingest_skips_sensitive_paths(self, monkeypatch, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        os.environ["ILU_WORKSPACE"] = str(ws)

        core = make_core(monkeypatch, tmp_path)

        # Un archivo dentro del workspace pero en security/ se salta.
        sec = ws / "security"
        sec.mkdir()

        result = core.tools.execute("memory_ingest", source="security/owner.pin")

        assert result["success"] is False
        assert result["error"] == "sensitive_path"

    def test_ingest_learns_from_file(self, monkeypatch, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        os.environ["ILU_WORKSPACE"] = str(ws)

        (ws / "apuntes.md").write_text(
            "I.L.U. recuerda este apunte para el futuro.",
            encoding="utf-8",
        )

        core = make_core(monkeypatch, tmp_path)

        # Directo por el gate: memory_ingest es permission "safe" → la
        # compuerta lo deja pasar y el handler escribe en la memoria.
        tool_call = core.tools.execute(
            "memory_ingest",
            source="apuntes.md",
            tag="test",
        )

        # El gate delega en ingest_path (app/ingest.py), que vuelve
        # success con los chunks ingeridos a la memoria semántica.
        assert tool_call["success"] is True
        assert tool_call["result"]["chunks"] >= 1

        # Re-ingerir el mismo archivo es válido (idempotente): I.L.U.
        # puede volver a aprender material aunque ya lo tenga.
        result = core.tools.execute(
            "memory_ingest",
            source="apuntes.md",
            tag="test",
        )
        assert result["success"] is True
        assert result["result"]["chunks"] >= 1

        assert core.tools.has_tool("memory_ingest")
        # Un archivo inexistente dentro del workspace es fail-closed.
        missing = core.tools.execute("memory_ingest", source="no-existe.md")
        assert missing["error"] == "not_a_file"


# ----------------------------------------------------------------------
# Guard SSRF de web_fetch
# ----------------------------------------------------------------------

class TestWebFetchSSRF:

    @pytest.mark.parametrize("ip, expected", [
        ("127.0.0.1", True),
        ("127.0.0.2", True),
        ("10.0.0.1", True),
        ("172.16.0.1", True),
        ("172.31.255.255", True),
        ("192.168.1.1", True),
        ("0.0.0.0", True),
        ("169.254.1.1", True),
        ("224.0.0.1", True),
        ("8.8.8.8", False),
        ("1.1.1.1", False),
        ("::1", True),
        ("::", True),
        ("fd00::1", True),
        ("fe80::1", True),
        ("ff02::1", True),
        ("2001:4860:4860::8888", False),
    ])
    def test_is_blocked_address(self, ip, expected):
        assert _is_blocked_address(ip) is expected

    def test_host_resolution_failure_is_blocked(self, monkeypatch):
        # Un host que no resuelve es fail-closed (no conecta).
        import tools.search as search

        monkeypatch.setattr(
            search.socket, "getaddrinfo",
            lambda *a, **k: (_ for _ in ()).throw(
                search.socket.gaierror("no such host")
            ),
        )

        result = web_fetch("https://no-existe-ejemplo.invalid/x")

        assert result["success"] is False
        assert result["error"] == "host_blocked_ssrf"

    def test_loopback_hostname_fails_closed(self, monkeypatch):
        # localhost resuelve a 127.0.0.1 → bloqueado.
        import tools.search as search

        real_getaddrinfo = search.socket.getaddrinfo

        def fake_getaddrinfo(host, *args, **kwargs):
            if host == "localhost":
                return [
                    (search.socket.AF_INET, 1, 6, "", ("127.0.0.1", 0))
                ]
            return real_getaddrinfo(host, *args, **kwargs)

        monkeypatch.setattr(
            search.socket, "getaddrinfo", fake_getaddrinfo
        )

        result = web_fetch("https://localhost/index.html")

        assert result["success"] is False
        assert result["error"] == "host_blocked_ssrf"

    def test_non_http_scheme_rejected(self):
        result = web_fetch("file:///etc/passwd")
        assert result["success"] is False
        assert result["error"] == "scheme_not_allowed"

    def test_missing_url_rejected(self):
        result = web_fetch("")
        assert result["success"] is False
        assert result["error"] == "url_required"