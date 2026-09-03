"""
Bloque D-1 — Autenticación del token de dispositivo en rutas sensibles.

Las rutas que escalan privilegios (conceder permisos, cambiar autonomía,
resolver solicitudes de autorización) y las que borran datos (reset de
conversaciones) exigen el token de dispositivo (security/device.key).

Se verifica:
  - Sin token  -> 401
  - Token inválido -> 401
  - Token correcto -> la acción procede
  - /ask y archivos estáticos siguen abiertos (uso normal)
"""

import json
import threading
import time
import urllib.request
import urllib.error

import pytest

import app.__main__ as main


PORT = 18767  # Puerto único: no colisiona con test_web_serving (18765)


@pytest.fixture(scope="module")
def auth_server():
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", PORT), main.ILUHandler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    time.sleep(0.5)

    yield server

    server.shutdown()


def _req(method, path, body=None, token=None):
    """Helper HTTP. Devuelve (status, body_dict)."""
    url = f"http://127.0.0.1:{PORT}{path}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    def _decode(raw):
        try:
            return json.loads(raw)
        except Exception:
            return {"_body": raw.decode("utf-8", "replace")}

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, _decode(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, _decode(e.read())
        except Exception:
            return e.code, {}
    except Exception:
        return 0, {}


class TestSensitiveRoutesRequireToken:

    def test_grant_without_token(self, auth_server):
        status, body = _req(
            "POST", "/grants",
            {"actor": "owner", "capability": "read_file"}
        )
        assert status == 401
        assert body.get("error") == "unauthorized"

    def test_grant_invalid_token(self, auth_server):
        status, _ = _req(
            "POST", "/grants",
            {"actor": "owner", "capability": "read_file"},
            token="token-invalido"
        )
        assert status == 401

    def test_grant_valid_token(self, auth_server):
        status, body = _req(
            "POST", "/grants",
            {"actor": "owner", "capability": "read_file"},
            token=main.DEVICE_TOKEN
        )
        assert status == 200
        assert body.get("success") is True

    def test_change_autonomy_without_token(self, auth_server):
        status, body = _req(
            "POST", "/autonomy",
            {"actor": "owner", "level": "autonomous"}
        )
        assert status == 401
        assert body.get("error") == "unauthorized"

    def test_change_autonomy_valid_token(self, auth_server):
        status, body = _req(
            "POST", "/autonomy",
            {"actor": "owner", "level": "manual"},
            token=main.DEVICE_TOKEN
        )
        assert status == 200
        assert body.get("success") is True

    def test_resolve_auth_request_without_token(self, auth_server):
        # Abre una solicitud real para intentar resolverla sin token.
        from security.authorization_request import AuthorizationRequest
        request = main.core.auth_requests.open(
            capability="notify",
            reason="prueba",
            principal=main.core.settings.owner_id,
        )
        status, body = _req(
            "POST",
            f"/authorization-requests/{request.key}",
            {"actor": "owner", "decision": "granted"}
        )
        assert status == 401

    def test_resolve_auth_request_valid_token(self, auth_server):
        from security.authorization_request import AuthorizationRequest
        request = main.core.auth_requests.open(
            capability="notify",
            reason="prueba",
            principal=main.core.settings.owner_id,
        )
        status, body = _req(
            "POST",
            f"/authorization-requests/{request.key}",
            {"actor": "owner", "decision": "denied"},
            token=main.DEVICE_TOKEN
        )
        assert status == 200
        assert body.get("success") is True

    def test_delete_conversation_without_token(self, auth_server):
        status, body = _req("DELETE", "/conversations/mi-sesion")
        assert status == 401
        assert body.get("error") == "unauthorized"

    def test_delete_conversation_valid_token(self, auth_server):
        status, body = _req(
            "DELETE", "/conversations/mi-sesion",
            token=main.DEVICE_TOKEN
        )
        assert status == 200
        assert body.get("success") is True


class TestOpenRoutesStillWork:

    def test_ask_open(self, auth_server):
        status, body = _req("POST", "/ask", {"message": "hola"})
        assert status == 200
        assert body.get("success") is True

    def test_static_open(self, auth_server):
        status, _ = _req("GET", "/")
        assert status == 200

    def test_healthz_open(self, auth_server):
        status, body = _req("GET", "/healthz")
        assert status == 200
        assert body.get("status") == "ok"

    def test_security_read_open(self, auth_server):
        # La lectura de estado de seguridad sigue siendo consulta pública.
        status, body = _req("GET", "/security")
        assert status == 200
        assert "autonomy" in body
