"""
Tests para la capa de voz (ILUVoice).

Verifica:
- Archivos estáticos servidos (voice.js, estilos, HTML)
- Sintaxis JS válida
- Patrones de arquitectura: mismo /ask, estados válidos, anti-autoescucha
- No existe un segundo pipeline de conversación
- Integridad del pipeline de seguridad
- No hay regresiones en funcionalidad existente
"""

import json
import os
import subprocess
import threading
import time
import urllib.request
import urllib.error

import pytest

# Puerto único para tests de voz (evitar colisión con test_web_serving)
_PORT = 18766


# ------------------------------------------------------------------
# Fixture: servidor web aislado
# ------------------------------------------------------------------

@pytest.fixture(scope="module")
def voice_server(tmp_path_factory):
    os.environ["PORT"] = str(_PORT)
    os.environ["ILU_WORKSPACE"] = str(tmp_path_factory.mktemp("workspace"))
    os.environ["ILU_CONVERSATIONS_PATH"] = str(
        tmp_path_factory.mktemp("conv") / "conversations.jsonl"
    )
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("DATABASE_URL_POOLED", None)

    from app.__main__ import ILUHandler, core, settings, task_manager
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", _PORT), ILUHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.5)
    yield server
    server.shutdown()


def _get(path):
    url = f"http://127.0.0.1:{_PORT}{path}"
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req)
        return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception:
        return 0, {}, b""


def _post_json(path, data):
    url = f"http://127.0.0.1:{_PORT}{path}"
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
# Archivos estáticos
# ------------------------------------------------------------------

class TestVoiceStaticFiles:

    def test_voice_js_served(self, voice_server):
        status, headers, body = _get("/js/voice.js")
        assert status == 200
        ct = headers.get("Content-Type", "")
        assert "javascript" in ct or "text/" in ct
        assert b"ILUVoice" in body
        assert b"SpeechRecognizer" in body or b"SpeechSynthesizer" in body
        assert b"createWebSpeechRecognizer" in body
        assert b"createWebSpeechSynthesizer" in body

    def test_voice_css_styles(self, voice_server):
        status, headers, body = _get("/css/ilu.css")
        assert status == 200
        assert b".chat-mic" in body
        assert b".voice-bar" in body
        assert b".voice-status" in body
        assert b".voice-transcript" in body
        assert b".chat-mic.active" in body
        assert b".chat-mic.speaking" in body
        assert b"ilu-mic-pulse" in body

    def test_mic_button_in_html(self, voice_server):
        status, _, body = _get("/")
        assert status == 200
        assert b'id="micButton"' in body
        assert b'id="voiceBar"' in body
        assert b'id="voiceStatus"' in body
        assert b'id="voiceTranscript"' in body
        assert b'aria-label="Activar voz"' in body
        assert b'/js/voice.js' in body

    def test_voice_js_loads_before_app_js(self, voice_server):
        """voice.js debe cargarse antes de app.js."""
        status, _, body = _get("/")
        assert status == 200
        html = body.decode("utf-8")
        voice_idx = html.find('/js/voice.js')
        app_idx = html.find('/js/app.js')
        assert voice_idx > 0 and app_idx > 0
        assert voice_idx < app_idx


# ------------------------------------------------------------------
# Sintaxis JS
# ------------------------------------------------------------------

class TestVoiceJSSyntax:

    @pytest.mark.parametrize("jsfile", [
        "voice.js",
        "app.js",
        "ui.js",
        "api.js",
        "ilu-core.js",
        "ilu-plasma.js",
    ])
    def test_js_syntax_valid(self, jsfile):
        path = os.path.join(
            os.path.dirname(__file__), "..", "app", "web", "js", jsfile
        )
        result = subprocess.run(
            ["node", "--check", path],
            capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, f"Syntax error in {jsfile}: {result.stderr}"


# ------------------------------------------------------------------
# Arquitectura y patrones
# ------------------------------------------------------------------

class TestVoiceArchitecture:

    def test_voice_uses_same_ask_endpoint(self):
        """La voz entra por el mismo ILUApi.ask que el texto."""
        path = os.path.join(os.path.dirname(__file__), "..", "app", "web", "js", "app.js")
        with open(path) as f:
            content = f.read()
        assert "ILUApi.ask" in content
        assert "_dispatchMessage" in content
        assert "_sendVoiceText" in content

    def test_voice_states_mapped_to_ilu_core(self):
        """Los estados de voz usan valores ILUCore válidos."""
        path = os.path.join(os.path.dirname(__file__), "..", "app", "web", "js", "voice.js")
        with open(path) as f:
            content = f.read()
        valid = ["listening", "thinking", "responding",
                 "authorization", "error", "idle"]
        for state in valid:
            assert ("'" + state + "'" in content
                    or '"' + state + '"' in content), \
                f"Voice module missing state: {state}"

    def test_no_parallel_conversation_system(self):
        """voice.js NO llama a ILUApi directamente; es orquestador puro."""
        path = os.path.join(os.path.dirname(__file__), "..", "app", "web", "js", "voice.js")
        with open(path) as f:
            content = f.read()
        assert "ILUCore" in content
        assert "ILUApi" not in content
        assert "fetch(" not in content
        assert "XMLHttpRequest" not in content

    def test_voice_not_identity_bypass_in_code(self):
        """voice.js NO contiene lógica de autorización/seuridad en código."""
        path = os.path.join(os.path.dirname(__file__), "..", "app", "web", "js", "voice.js")
        with open(path) as f:
            lines = f.readlines()
        for line in lines:
            stripped = line.strip()
            # Saltar comentarios (líneas que empiezan por // o dentro de bloque /* */)
            if stripped.startswith('//') or stripped.startswith('*') or stripped.startswith('/*'):
                continue
            # No debe contener lógica de seguridad en código ejecutable
            for term in ['SecurityGate', 'Authority', 'grantPermission',
                         'resolveAuthRequest']:
                assert term not in stripped, \
                    f"Security term '{term}' found in voice.js code: {stripped}"

    def test_anti_self_listen_guards(self):
        """_beginListening verifica _speaking y _busy antes de activar."""
        path = os.path.join(os.path.dirname(__file__), "..", "app", "web", "js", "voice.js")
        with open(path) as f:
            content = f.read()
        assert "_speaking" in content
        assert "_busy" in content
        assert "_active" in content
        assert "_beginListening" in content

    def test_guard_timer_anti_echo(self):
        """Existe ventana de guarda antes de re-armar micrófono."""
        path = os.path.join(os.path.dirname(__file__), "..", "app", "web", "js", "voice.js")
        with open(path) as f:
            content = f.read()
        assert "_guardTimer" in content
        assert "setTimeout" in content
        assert "500" in content

    def test_providers_interfaces(self):
        """Interfaces intercambiables: create + isAvailable + start/speak."""
        path = os.path.join(os.path.dirname(__file__), "..", "app", "web", "js", "voice.js")
        with open(path) as f:
            content = f.read()
        assert "createWebSpeechRecognizer" in content
        assert "createWebSpeechSynthesizer" in content
        assert "isAvailable" in content
        assert "start" in content
        assert "speak" in content
        assert "cancel" in content

    def test_continuous_conversation_mode(self):
        """Modo continuo configurable con _continuous."""
        path = os.path.join(os.path.dirname(__file__), "..", "app", "web", "js", "voice.js")
        with open(path) as f:
            content = f.read()
        assert "_continuous" in content
        assert "configure" in content
        assert "onTranscript" in content

    def test_speak_response_in_app(self):
        """app.js habla respuestas, errores y autorizaciones cuando voz activa."""
        path = os.path.join(os.path.dirname(__file__), "..", "app", "web", "js", "app.js")
        with open(path) as f:
            content = f.read()
        assert "_applyVisualAndSpeak" in content
        assert "speakResponse" in content
        assert "speakError" in content
        assert "speakAuthorization" in content
        assert "authorization" in content

    def test_same_session_id_shared(self):
        """La voz comparte session_id con el chat de texto."""
        path = os.path.join(os.path.dirname(__file__), "..", "app", "web", "js", "app.js")
        with open(path) as f:
            content = f.read()
        # _sendVoiceText llama a _dispatchMessage que usa _sessionId
        assert "_sendVoiceText" in content
        assert "_dispatchMessage" in content
        assert "_sessionId" in content


# ------------------------------------------------------------------
# Sin regresiones
# ------------------------------------------------------------------

class TestNoRegressions:

    def test_chat_ask_still_works(self, voice_server):
        status, body = _post_json("/ask", {"message": "hola"})
        assert status == 200
        assert body["success"] is True
        assert "response" in body

    def test_healthz_still_works(self, voice_server):
        status, _, _ = _get("/healthz")
        assert status == 200

    def test_all_static_files_served(self, voice_server):
        for path in ['/js/api.js', '/js/ilu-core.js', '/js/ilu-plasma.js',
                     '/js/ui.js', '/js/app.js', '/js/voice.js',
                     '/css/ilu.css', '/css/states.css']:
            status, _, _ = _get(path)
            assert status == 200, f"Failed to serve {path}"
