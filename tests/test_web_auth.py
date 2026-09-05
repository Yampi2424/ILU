"""
Bloque D-1 — Autenticación en rutas sensibles.

Las rutas que escalan privilegios (conceder permisos, cambiar autonomía,
resolver solicitudes de autorización) y las que borran datos (reset de
conversaciones) exigen UNA de dos credenciales independientes:

  1) El token de dispositivo (security/device.key) via
     'Authorization: Bearer <token>'.
  2) La clave del owner (security/owner.pin o ILU_OWNER_SECRET) via
     'X-ILU-Pin: <secreto>' — el MISMO PIN de la concesión por
     voz/texto (Bloque 14), validado en código determinista.

Se verifica, para ambas credenciales:
  - Sin credencial        -> 401
  - Credencial inválida   -> 401 (con rastro 'owner_secret_failed' para el PIN)
  - Credencial correcta   -> la acción procede
  - Credencial correcta pero actor no-raíz -> 403 (jerarquía intacta)
  - /ask y archivos estáticos siguen abiertos (uso normal)

El VALOR de la clave del owner jamás se fija como literal en este archivo:
los casos de "clave correcta" usan la clave REAL configurada en el sistema
(security/owner.pin o la variable ILU_OWNER_SECRET), igual que hace el
core en producción. Si el sistema no tiene clave configurada, esos casos se
skipean y queda cubierta la parte fail-closed (401 sin rastro).
"""

import os
import json
import threading
import time
import urllib.request
import urllib.error

import pytest

# --- Clave del owner: se LEE del mecanismo seguro, no se hardcodea ---
def _configured_secret():
    env = os.environ.get("ILU_OWNER_SECRET")
    if env:
        return env.strip()
    try:
        with open("security/owner.pin", encoding="utf-8") as handle:
            return handle.read().strip() or None
    except OSError:
        return None


OWNER_SECRET = _configured_secret()

requires_secret = pytest.mark.skipif(
    OWNER_SECRET is None,
    reason="clave del owner no configurada (security/owner.pin o ILU_OWNER_SECRET)",
)


import app.__main__ as main  # noqa: E402


PORT = 18767  # Puerto único: no colisiona con test_web_serving (18765)


@pytest.fixture(scope="module", autouse=True)
def _clean_env():
    yield
    os.environ.pop("ILU_OWNER_SECRET", None)


@pytest.fixture(scope="module")
def auth_server():
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", PORT), main.ILUHandler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    time.sleep(0.5)

    yield server

    server.shutdown()


def _req(method, path, body=None, token=None, pin=None):
    """Helper HTTP. Devuelve (status, body_dict).

    - token: credencial de DISPOSITIVO -> 'Authorization: Bearer <token>'
    - pin:   credencial del OWNER (clave de voz/texto) -> 'X-ILU-Pin: <pin>'
    """
    url = f"http://127.0.0.1:{PORT}{path}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if pin is not None:
        headers["X-ILU-Pin"] = pin
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


class TestSensitiveRoutesAcceptOwnerPin:
    """El secreto del owner (Bloque 14) también autentica la UI web.

    Es el MISMO secreto que pide la concesión por voz/texto, verificado
    acá en código determinista y leído del mecanismo seguro (sin fijar
    su valor en el código). El valor de la clave jamás se loguea.
    """

    @requires_secret
    def test_grant_with_pin(self, auth_server):
        status, body = _req(
            "POST", "/grants",
            {"actor": "owner", "capability": "read_file"},
            pin=OWNER_SECRET
        )
        assert status == 200
        assert body.get("success") is True

    def test_grant_wrong_pin_unauthorized(self, auth_server):
        status, body = _req(
            "POST", "/grants",
            {"actor": "owner", "capability": "read_file"},
            pin="111111"
        )
        assert status == 401
        assert body.get("error") == "unauthorized"

    @requires_secret
    def test_grant_wrong_pin_audits_failure(self, auth_server):
        _req(
            "POST", "/grants",
            {"actor": "owner", "capability": "read_file"},
            pin="999999"
        )
        # El intento fallido deja rastro SIN exponer el valor de la clave.
        # Se lee desde el MISMO AuditLog que usa el servidor (main.core),
        # porque conftest redirige ILU_AUDIT_PATH por-test a un tmp_path
        # y un AuditLog() nuevo apuntaría al tmp vacío de este test.
        entries = main.core.audit.recent(limit=50)
        failed = [
            e for e in entries
            if e.get("action") == "owner_secret_failed"
        ]
        assert failed
        assert failed[0]["reason"] == "wrong_pin"
        assert failed[0]["method"] == "http_x_ilu_pin"
        assert failed[0]["decision"] == "deny"
        # La clave real no debe reaparecer en ningún campo del rastro.
        for e in failed:
            assert OWNER_SECRET not in json.dumps(e)

    @requires_secret
    def test_grant_pin_non_root_actor_denied(self, auth_server):
        # Jerarquía intacta: el PIN prueba quién se es, no lo hace raíz.
        # Un actor no-raíz con PIN válido queda fuera (403), no autorizado.
        status, body = _req(
            "POST", "/grants",
            {"actor": "invitado", "capability": "read_file"},
            pin=OWNER_SECRET
        )
        assert status == 403
        assert body.get("success") is False

    def test_grant_without_credential(self, auth_server):
        # Sin credencial la ruta exige token de dispositivo o clave.
        status, body = _req(
            "POST", "/grants",
            {"actor": "owner", "capability": "read_file"}
        )
        assert status == 401
        assert body.get("error") == "unauthorized"

    @requires_secret
    def test_change_autonomy_with_pin(self, auth_server):
        status, body = _req(
            "POST", "/autonomy",
            {"actor": "owner", "level": "manual"},
            pin=OWNER_SECRET
        )
        assert status == 200
        assert body.get("success") is True

    @requires_secret
    def test_resolve_auth_request_with_pin(self, auth_server):
        request = main.core.auth_requests.open(
            capability="notify",
            reason="prueba",
            principal=main.core.settings.owner_id,
        )
        status, body = _req(
            "POST",
            f"/authorization-requests/{request.key}",
            {"actor": "owner", "decision": "denied"},
            pin=OWNER_SECRET
        )
        assert status == 200
        assert body.get("success") is True

    @requires_secret
    def test_delete_conversation_with_pin(self, auth_server):
        status, body = _req(
            "DELETE", "/conversations/mi-sesion",
            pin=OWNER_SECRET
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
