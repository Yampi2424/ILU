"""
Bloque 15-17 — Servido de archivos estáticos de la interfaz web.

Verifica que el servidor HTTP de I.L.U. sirve los archivos de la interfaz
(I.L.U. Presencia) desde app/web/ con los Content-Type correctos.
Todas las rutas API existentes siguen intactas.
"""

import json
import os
import threading
import time
import urllib.request
import urllib.error

import pytest


PORT = 18765  # Puerto único para no colisionar con otros tests


@pytest.fixture(scope="module")
def web_server(tmp_path_factory):
    """
    Lanza el servidor HTTP en un hilo y devuelve el puerto.

    Aísla memoria y conversaciones en tmp_path para que los tests
    no contaminen el estado real.
    """
    os.environ["PORT"] = str(PORT)
    os.environ["ILU_WORKSPACE"] = str(tmp_path_factory.mktemp("workspace"))
    os.environ["ILU_CONVERSATIONS_PATH"] = str(
        tmp_path_factory.mktemp("conv") / "conversations.jsonl"
    )

    # Desactivar base de datos para no depender de Postgres.
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("DATABASE_URL_POOLED", None)

    from app.__main__ import ILUHandler, core, settings, task_manager

    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", PORT), ILUHandler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    time.sleep(0.5)

    yield server

    server.shutdown()


def _get(path, port=PORT):
    """Helper: GET request, devuelve (status, headers, body)."""
    url = f"http://127.0.0.1:{port}{path}"
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req)
        return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception:
        return 0, {}, b""


def _post_json(path, data, port=PORT):
    """Helper: POST JSON, devuelve (status, body_dict)."""
    url = f"http://127.0.0.1:{port}{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"}
    )
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception:
        return 0, {}


# ------------------------------------------------------------------
# HTML
# ------------------------------------------------------------------

class TestStaticHTML:

    def test_root_serves_html(self, web_server):
        status, headers, body = _get("/")
        assert status == 200
        assert "text/html" in headers.get("Content-Type", "")
        assert b"I.L.U." in body

    def test_root_html_contains_title(self, web_server):
        _, _, body = _get("/")
        assert b"<title>" in body

    def test_root_html_contains_core(self, web_server):
        _, _, body = _get("/")
        assert b"ilu-core" in body or b"iluCore" in body

    def test_root_html_links_css(self, web_server):
        _, _, body = _get("/")
        assert b"/css/ilu.css" in body

    def test_root_html_links_js(self, web_server):
        _, _, body = _get("/")
        assert b"/js/api.js" in body
        assert b"/js/app.js" in body


# ------------------------------------------------------------------
# CSS
# ------------------------------------------------------------------

class TestStaticCSS:

    def test_ilu_css_served(self, web_server):
        status, headers, body = _get("/css/ilu.css")
        assert status == 200
        assert "text/css" in headers.get("Content-Type", "")
        assert b"--ilu-core" in body

    def test_states_css_served(self, web_server):
        status, headers, body = _get("/css/states.css")
        assert status == 200
        assert "text/css" in headers.get("Content-Type", "")
        assert b"ilu-msg-in" in body


# ------------------------------------------------------------------
# JavaScript
# ------------------------------------------------------------------

class TestStaticJS:

    def test_api_js_served(self, web_server):
        status, headers, body = _get("/js/api.js")
        assert status == 200
        ct = headers.get("Content-Type", "")
        assert "javascript" in ct or "text/" in ct
        assert b"ILUApi" in body

    def test_ilu_core_js_served(self, web_server):
        status, headers, body = _get("/js/ilu-core.js")
        assert status == 200
        assert b"ILUCore" in body

    def test_ui_js_served(self, web_server):
        status, headers, body = _get("/js/ui.js")
        assert status == 200
        assert b"ILUUI" in body

    def test_app_js_served(self, web_server):
        status, headers, body = _get("/js/app.js")
        assert status == 200
        assert b"ILUApi" in body

    def test_voice_js_served(self, web_server):
        status, headers, body = _get("/js/voice.js")
        assert status == 200
        ct = headers.get("Content-Type", "")
        assert "javascript" in ct or "text/" in ct
        assert b"ILUVoice" in body
        assert b"SpeechRecognizer" in body
        assert b"SpeechSynthesizer" in body


# ------------------------------------------------------------------
# Rutas API existentes siguen intactas
# ------------------------------------------------------------------

class TestAPIRoutesIntact:

    def test_healthz(self, web_server):
        status, body = _get_json("/healthz")
        assert status == 200
        assert body["status"] == "ok"

    def test_about(self, web_server):
        status, body = _get_json("/about")
        assert status == 200
        assert body["name"] == "I.L.U."

    def test_ask(self, web_server):
        status, body = _post_json("/ask", {"message": "hola"})
        assert status == 200
        assert body["success"] is True
        assert "response" in body

    def test_tasks(self, web_server):
        status, body = _get_json("/tasks")
        assert status == 200
        assert "tasks" in body

    def test_security(self, web_server):
        status, body = _get_json("/security")
        assert status == 200
        assert "autonomy" in body

    def test_grants(self, web_server):
        status, body = _get_json("/grants")
        assert status == 200
        assert "grants" in body

    def test_policy(self, web_server):
        status, body = _get_json("/policy")
        assert status == 200
        assert "policy" in body

    def test_authorization_requests(self, web_server):
        status, body = _get_json("/authorization-requests")
        assert status == 200
        assert "requests" in body


# ------------------------------------------------------------------
# Seguridad: path traversal
# ------------------------------------------------------------------

class TestPathTraversal:

    def test_traversal_rejected(self, web_server):
        status, _, _ = _get("/css/../../etc/passwd")
        assert status == 404

    def test_nonexistent_static(self, web_server):
        status, _, _ = _get("/css/noexiste.css")
        assert status == 404

    def test_nonexistent_js(self, web_server):
        status, _, _ = _get("/js/noexiste.js")
        assert status == 404


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_json(path, port=PORT):
    """GET request that returns JSON. Returns (status, body_dict)."""
    url = f"http://127.0.0.1:{port}{path}"
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception:
        return 0, {}
