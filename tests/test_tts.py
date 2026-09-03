"""
Bloque Voz — Síntesis de voz de I.L.U. (TTS)

Se verifica la capa de TTS sin depender de la red:
  - Texto vacío -> TTSUnavailable (sin llamar al motor).
  - El endpoint HTTP /tts devuelve 400 ante texto vacío.
  - El endpoint devuelve audio/mpeg con bytes de audio cuando el motor
    responde (motor mockeado; la red real no se toca en tests).
  - El endpoint devuelve 503 cuando el motor no está disponible
    (sin red / sin paquete), para que el frontend caiga al TTS nativo.

La síntesis real (edge-tts, red) se prueba manualmente; aquí se mockea
el servicio para un test determinista y sin red.
"""

import json
import threading
import time
import urllib.request
import urllib.error

import pytest

from app.tts import TTSService, TTSUnavailable

import app.__main__ as main


PORT = 18769  # Puerto único: no colisiona con otros test_web_*


@pytest.fixture(scope="module")
def tts_server():
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", PORT), main.ILUHandler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    time.sleep(0.5)

    yield server

    server.shutdown()


def _get(path):
    with urllib.request.urlopen(
        f"http://127.0.0.1:{PORT}{path}"
    ) as resp:
        return resp.status, resp.headers, resp.read()


def _get_status(path):
    try:
        status, _, _ = _get(path)
        return status
    except urllib.error.HTTPError as error:
        return error.code


# ----------------------------------------------------------------------
# Servicio TTS
# ----------------------------------------------------------------------

def test_empty_text_raises():
    service = TTSService()

    with pytest.raises(TTSUnavailable):
        service.synthesize("")


def test_empty_text_raises_even_with_voice():
    service = TTSService()

    with pytest.raises(TTSUnavailable):
        service.synthesize("   ")


def test_provider_name_and_default_voice():
    service = TTSService()

    assert service.name == "edge-tts"
    # Voz por defecto en español rioplatense (Argentina), femenina.
    assert service.voice.startswith("es-")


# ----------------------------------------------------------------------
# Endpoint HTTP /tts
# ----------------------------------------------------------------------

def test_tts_endpoint_empty_text_returns_400(tts_server):
    assert _get_status("/tts?text=") == 400


def test_tts_endpoint_missing_text_returns_400(tts_server):
    assert _get_status("/tts") == 400


def test_tts_endpoint_returns_audio_mpeg_on_success(tts_server, monkeypatch):
    class FakeService:
        def synthesize(self, text, voice=None):
            return b"\xff\xf3\x64fake-mp3-audio"

    monkeypatch.setattr(main, "tts", FakeService())

    status, headers, body = _get("/tts?text=hola")

    assert status == 200
    assert headers["Content-Type"] == "audio/mpeg"
    assert body == b"\xff\xf3\x64fake-mp3-audio"


def test_tts_endpoint_returns_503_when_unavailable(tts_server, monkeypatch):
    class UnavailableService:
        def synthesize(self, text, voice=None):
            raise TTSUnavailable("sin red")

    monkeypatch.setattr(main, "tts", UnavailableService())

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{PORT}/tts?text=hola"
        ) as resp:
            pytest.fail("Se esperaba HTTP 503")
    except urllib.error.HTTPError as error:
        assert error.code == 503
        payload = json.loads(error.read().decode("utf-8"))
        assert payload["error"] == "tts_unavailable"
