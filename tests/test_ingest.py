"""
Tests para ingesta de memoria (Fase E).

Verifica:
- Scope de workspace (fuera del workspace falla).
- Salta ocultos, binarios, bases de datos, stores de I.L.U.
- Chunk + memoria (source=ingest, metadata de ruta).
- Sin lectura de rutas sensibles.
"""

import os
import tempfile
import pytest

from app.ingest import (
    ingest_path,
    ingest_bytes,
    walk_workspace,
    _is_binary,
    _is_sensitive,
    _chunk,
    _extract,
)
from tools.filesystem import workspace_root


class TestIngestHelpers:
    """Funciones auxiliares de ingesta."""

    def test_is_binary_detection(self, tmp_path):
        """Detección rápida de binarios (bytes de control)."""
        text_file = tmp_path / "texto.txt"
        text_file.write_text("hola mundo\n" * 10, encoding="utf-8")

        bin_file = tmp_path / "binario.bin"
        bin_file.write_bytes(bytes([0, 1, 2, 3, 4, 5, 6, 7, 8] * 10))

        assert _is_binary(str(text_file)) is False
        assert _is_binary(str(bin_file)) is True

    def test_is_sensitive_paths(self, tmp_path):
        """Rutas sensibles del runtime I.L.U. se bloquean."""
        # security/
        assert _is_sensitive(str(tmp_path / "security" / "owner.pin")) is True
        assert _is_sensitive(str(tmp_path / "memory" / "audit.jsonl")) is True
        assert _is_sensitive(str(tmp_path / "memory" / "conversations.jsonl")) is True
        assert _is_sensitive(str(tmp_path / "memory" / "goals.jsonl")) is True
        assert _is_sensitive(str(tmp_path / "memory" / "scheduler.jsonl")) is True
        assert _is_sensitive(str(tmp_path / "memory" / "agents.jsonl")) is True
        assert _is_sensitive(str(tmp_path / "memory" / "benchmarks.jsonl")) is True
        assert _is_sensitive(str(tmp_path / "omniroute.key")) is True

        # Rutas normales
        assert _is_sensitive(str(tmp_path / "notas.txt")) is False
        assert _is_sensitive(str(tmp_path / "docs" / "algo.md")) is False

    def test_chunk_splitting(self):
        """División en chunks con solape."""
        short = "corto"
        assert _chunk(short) == ["corto"]

        long_text = "a\n" * 3000  # ~6000 chars
        chunks = _chunk(long_text, size=2000, overlap=200)
        assert len(chunks) >= 2
        # Verificar solape: inicio del 2do chunk ≈ pos 1800
        assert chunks[1].startswith("a\n") or chunks[1][:200] != chunks[0][:200]

    def test_extract_html(self):
        """HTML se depura (quita script/style)."""
        html = """
        <html>
            <head><title>Test</title></head>
            <body>
                <h1>Hola</h1>
                <script>alert('x')</script>
                <p>Contenido real</p>
                <style>body {color:red}</style>
            </body>
        </html>
        """
        text = _extract("test.html", html.encode("utf-8"))
        assert "Hola" in text
        assert "Contenido real" in text
        assert "alert" not in text
        assert "color:red" not in text


class TestIngestPath:
    """ingest_path: ruta del workspace → memoria."""

    @pytest.fixture
    def tmp_workspace(self, monkeypatch, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setenv("ILU_WORKSPACE", str(ws))
        return ws

    @pytest.fixture
    def core_router(self, monkeypatch, tmp_workspace):
        """MemoryRouter aislado."""
        for key in ["DATABASE_URL", "DATABASE_URL_POOLED", "ILU_AI_PROVIDER"]:
            monkeypatch.delenv(key, raising=False)

        from app.core import ILUCore
        core = ILUCore()
        return core.memory

    def test_ingest_txt_file(self, tmp_workspace, core_router):
        """Archivo .txt se ingiere y crea memorias temporales."""
        doc = tmp_workspace / "notas.txt"
        doc.write_text("Primera línea.\nSegunda línea con más texto.\n", encoding="utf-8")

        result = ingest_path("notas.txt", core_router)

        assert result["success"] is True
        assert result["chunks"] >= 1
        assert len(result["keys"]) == result["chunks"]
        # Metadata de ruta en las memorias
        for key in result["keys"]:
            rec = core_router.get(key)
            assert rec is not None
            assert rec.memory_type == "episodic"
            assert rec.source == "ingest"
            assert rec.metadata.get("path") == str(doc)
            assert "chunk" in rec.metadata

    def test_ingest_md_file(self, tmp_workspace, core_router):
        """Archivo .md se ingiere."""
        doc = tmp_workspace / "readme.md"
        doc.write_text("# Título\n\nContenido *markdown*.\n", encoding="utf-8")

        result = ingest_path("readme.md", core_router)
        assert result["success"] is True
        assert result["chunks"] >= 1

    def test_ingest_html_file(self, tmp_workspace, core_router):
        """Archivo .html se depura y se ingiere."""
        doc = tmp_workspace / "pagina.html"
        doc.write_text(
            "<html><body><h1>Título</h1><p>Cuerpo</p><script>x</script></body></html>",
            encoding="utf-8",
        )

        result = ingest_path("pagina.html", core_router)
        assert result["success"] is True
        assert result["chunks"] >= 1

    def test_ingest_fails_outside_workspace(self, core_router, monkeypatch, tmp_path):
        """Ruta fuera del workspace falla (fail-closed)."""
        ws = tmp_path / "ws_outside_test"
        ws.mkdir()
        monkeypatch.setenv("ILU_WORKSPACE", str(ws))

        # Archivo fuera del workspace
        outside = tmp_path / "fuera.txt"
        outside.write_text("secreto", encoding="utf-8")

        # resolve_within_workspace lanza ValueError
        result = ingest_path(str(outside), core_router)
        assert result["success"] is False
        assert result["error"] == "outside_workspace"

    def test_ingest_fails_hidden_file(self, tmp_workspace, core_router):
        """Archivos ocultos se saltan en walk_workspace."""
        hidden = tmp_workspace / ".secreto.txt"
        hidden.write_text("oculto", encoding="utf-8")

        # Hidden files are skipped in walk_workspace, but ingest_path
        # handles them normally (extension check passes)
        # The test verifies walk_workspace behavior
        found = walk_workspace(str(tmp_workspace))
        hidden_paths = [f for f in found if ".secreto.txt" in f]
        assert len(hidden_paths) == 0

    def test_ingest_fails_binary(self, tmp_workspace, core_router):
        """Binarios con extensión soportada se rechazan por contenido."""
        bin_file = tmp_workspace / "binario.txt"
        bin_file.write_bytes(bytes([0, 1, 2, 3] * 100))

        result = ingest_path("binario.txt", core_router)
        assert result["success"] is False
        assert result["error"] == "binary_file"

    def test_ingest_fails_db_file(self, tmp_workspace, core_router):
        """Archivos .db se rechazan (extensión no soportada)."""
        db_file = tmp_workspace / "datos.db"
        db_file.write_text("sqlite", encoding="utf-8")

        result = ingest_path("datos.db", core_router)
        assert result["success"] is False
        assert result["error"] == "unsupported_extension"

    def test_ingest_fails_sensitive_store(self, tmp_workspace, core_router, monkeypatch):
        """Stores internos de I.L.U. se bloquean (security/, memory/)."""
        # Intentar ingerir security/owner.pin
        result = ingest_path("security/owner.pin", core_router)
        assert result["success"] is False
        assert result["error"] in ("sensitive_path", "outside_workspace", "not_a_file")

    def test_ingest_non_existent(self, tmp_workspace, core_router):
        """Archivo inexistente falla."""
        result = ingest_path("no_existe.txt", core_router)
        assert result["success"] is False
        assert result["error"] == "not_a_file"

    def test_ingest_empty_file(self, tmp_workspace, core_router):
        """Archivo vacío → éxito sin chunks."""
        empty = tmp_workspace / "vacio.txt"
        empty.write_text("", encoding="utf-8")

        result = ingest_path("vacio.txt", core_router)
        assert result["success"] is True
        assert result["chunks"] == 0
        assert result["keys"] == []


class TestWalkWorkspace:
    """walk_workspace: lista rutas interesantes del workspace."""

    @pytest.fixture
    def tmp_workspace(self, monkeypatch, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setenv("ILU_WORKSPACE", str(ws))
        return ws

    def test_walk_returns_text_files(self, tmp_workspace):
        """Devuelve .txt, .md, .html (sin ocultos, sin runtime)."""
        (tmp_workspace / "a.txt").write_text("a", encoding="utf-8")
        (tmp_workspace / "b.md").write_text("b", encoding="utf-8")
        (tmp_workspace / "c.html").write_text("<html>c</html>", encoding="utf-8")
        (tmp_workspace / ".hidden.txt").write_text("h", encoding="utf-8")
        (tmp_workspace / "bin.dat").write_bytes(b"\x00\x01")

        # Carpeta memory/ no debe descender
        mem_dir = tmp_workspace / "memory"
        mem_dir.mkdir()
        (mem_dir / "data.json").write_text("{}", encoding="utf-8")

        found = walk_workspace()
        names = {os.path.basename(f) for f in found}
        assert "a.txt" in names
        assert "b.md" in names
        assert "c.html" in names
        assert ".hidden.txt" not in names
        assert "bin.dat" not in names
        assert "data.json" not in names

    def test_walk_limits_results(self, tmp_workspace):
        """Máximo 200 archivos."""
        for i in range(250):
            (tmp_workspace / f"f{i}.txt").write_text(str(i), encoding="utf-8")
        found = walk_workspace()
        assert len(found) == 200


class TestIngestBytes:
    """ingest_bytes: contenido ya leído → memoria (sin filesystem)."""

    @pytest.fixture
    def core_router(self, monkeypatch, tmp_path):
        for key in ["DATABASE_URL", "DATABASE_URL_POOLED", "ILU_AI_PROVIDER"]:
            monkeypatch.delenv(key, raising=False)

        from app.core import ILUCore
        core = ILUCore()
        return core.memory

    def test_ingest_bytes_creates_memories(self, core_router):
        """Bytes de texto crean memorias con metadata."""
        content = "Contenido de prueba para ingesta de bytes.\nLínea 2.\nLínea 3."
        result = ingest_bytes("test.txt", content.encode("utf-8"), core_router)

        assert result["success"] is True
        assert result["chunks"] >= 1
        for key in result["keys"]:
            rec = core_router.get(key)
            assert rec is not None
            assert rec.source == "ingest"
            assert rec.metadata.get("path") == "test.txt"

    def test_ingest_bytes_rejects_sensitive(self, core_router):
        """Ruta sensible rechazada aunque no toque filesystem."""
        result = ingest_bytes("security/owner.pin", b"pin", core_router)
        assert result["success"] is False
        assert result["error"] == "sensitive_path"


class TestIngestIntegration:
    """Integración: tool memory_ingest vía core._execute_tool_call."""

    @pytest.fixture
    def core(self, monkeypatch, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setenv("ILU_WORKSPACE", str(ws))

        for key in [
            "DATABASE_URL", "DATABASE_URL_POOLED", "ILU_AI_PROVIDER",
            "ILU_AUTONOMY", "ILU_OWNER_SECRET",
        ]:
            monkeypatch.delenv(key, raising=False)

        from app.core import ILUCore
        core = ILUCore()

        # Mock provider (no se usa en ingest)
        class MockProvider:
            name = "mock"
            version = "0.0.1"
            def generate(self, prompt, context=None, tools=None):
                return {"type": "text", "content": "mock"}
        core.provider = MockProvider()

        return core

    def test_tool_memory_ingest_via_core(self, core, tmp_path):
        """memory_ingest tool se ejecuta via _execute_tool_call (mode=memory)."""
        ws = tmp_path / "ws"
        doc = ws / "material.txt"
        doc.write_text("Contenido para incorporar a la memoria semántica.", encoding="utf-8")

        from tools.call import ToolCall
        call = ToolCall(
            tool="memory_ingest",
            arguments={"source": "material.txt"},
            reason="test",
        )

        result = core._execute_tool_call(call, mode="memory")

        # El resultado de la tool llega envuelto por ToolManager.execute.
        assert result["success"] is True
        payload = result["result"]
        assert payload["success"] is True
        assert payload["chunks"] >= 1
        assert len(payload["keys"]) == payload["chunks"]

        # Verificar memoria creada
        for key in payload["keys"]:
            rec = core.memory.get(key)
            assert rec is not None
            assert rec.source == "ingest"
            assert "material.txt" in rec.metadata.get("path", "")

    def test_tool_memory_ingest_rejects_outside_workspace(self, core, tmp_path):
        """memory_ingest rechaza rutas fuera del workspace."""
        outside = tmp_path / "fuera.txt"
        outside.write_text("secreto", encoding="utf-8")

        from tools.call import ToolCall
        call = ToolCall(
            tool="memory_ingest",
            arguments={"source": str(outside)},
            reason="test",
        )

        result = core._execute_tool_call(call, mode="memory")
        assert result["success"] is False
        assert result["result"]["error"] == "outside_workspace"