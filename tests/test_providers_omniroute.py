from unittest import mock

from requests import exceptions as requests_exceptions

from app.providers import OmniRouteProvider


class FakeResponse:
    """Respuesta HTTP simulada: no se toca la red en los tests."""

    def __init__(self, payload, status=200):
        self._payload = payload
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise requests_exceptions.HTTPError(
                f"HTTP {self._status}"
            )

    def json(self):
        return self._payload


def make_provider(monkeypatch, api_key="clave-secreta"):
    monkeypatch.setenv("ILU_OMNIROUTE_MODEL", "modelo-test")
    monkeypatch.setenv("ILU_OMNIROUTE_URL", "http://omniroute.test/v1")

    if api_key is None:
        monkeypatch.delenv("ILU_OMNIROUTE_API_KEY", raising=False)
        monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ILU_OMNIROUTE_API_KEY", api_key)

    return OmniRouteProvider()


def test_missing_key_never_calls_network(monkeypatch, tmp_path):
    # Se aísla el cwd: sin la variable Y sin security/omniroute.key a la
    # vista no hay clave (hermético aunque el repo local tenga la suya).
    monkeypatch.chdir(tmp_path)
    provider = make_provider(monkeypatch, api_key=None)

    with mock.patch("app.providers.requests.post") as post:
        result = provider.generate("hola")

    assert result["type"] == "error"
    assert result["detail"] == "missing_api_key"
    assert "ILU_OMNIROUTE_API_KEY" in result["content"]

    # Sin clave jamás se hace una petición a la red.
    post.assert_not_called()


def test_api_key_reads_from_local_file(monkeypatch, tmp_path):
    # Sin variables de entorno, la clave se lee del archivo gitignored
    # security/omniroute.key (mismo patrón que device.key / owner.pin).
    monkeypatch.delenv("ILU_OMNIROUTE_API_KEY", raising=False)
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    key_dir = tmp_path / "security"
    key_dir.mkdir()
    (key_dir / "omniroute.key").write_text(
        "clave-del-archivo\n",
        encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILU_OMNIROUTE_MODEL", "modelo-test")
    monkeypatch.setenv("ILU_OMNIROUTE_URL", "http://omniroute.test/v1")

    provider = OmniRouteProvider()

    assert provider.api_key == "clave-del-archivo"


def test_api_key_env_wins_over_file(monkeypatch, tmp_path):
    # La variable de entorno tiene precedencia sobre el archivo local.
    monkeypatch.setenv(
        "ILU_OMNIROUTE_API_KEY",
        "clave-por-entorno"
    )
    key_dir = tmp_path / "security"
    key_dir.mkdir()
    (key_dir / "omniroute.key").write_text(
        "clave-del-archivo",
        encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ILU_OMNIROUTE_MODEL", "modelo-test")
    monkeypatch.setenv("ILU_OMNIROUTE_URL", "http://omniroute.test/v1")

    provider = OmniRouteProvider()

    assert provider.api_key == "clave-por-entorno"


def test_omniroute_text_and_auth(monkeypatch):
    provider = make_provider(monkeypatch)

    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Respuesta desde OmniRoute"
                }
            }
        ]
    }

    with mock.patch(
        "app.providers.requests.post",
        return_value=FakeResponse(payload)
    ) as post:
        result = provider.generate("hola")

    assert result["type"] == "text"
    assert result["content"] == "Respuesta desde OmniRoute"

    post.assert_called_once()

    url = post.call_args.args[0]
    assert url == "http://omniroute.test/v1/chat/completions"

    sent = post.call_args.kwargs["json"]
    assert sent["model"] == "modelo-test"
    assert sent["stream"] is False
    assert sent["messages"][0]["role"] == "system"
    # La clave viaja solo en headers, nunca en el cuerpo.
    assert "api_key" not in sent

    headers = post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer clave-secreta"


def test_omniroute_tool_call_allowed(monkeypatch):
    provider = make_provider(monkeypatch)

    content = (
        '{"tool": "system_time", "arguments": {}, "reason": "hora"}'
    )

    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content
                }
            }
        ]
    }

    with mock.patch(
        "app.providers.requests.post",
        return_value=FakeResponse(payload)
    ):
        result = provider.generate(
            "hora",
            tools=[{"name": "system_time"}]
        )

    assert result["type"] == "tool_call"
    assert result["tool"] == "system_time"


def test_omniroute_key_fallback_to_legacy_env(monkeypatch):
    monkeypatch.setenv("ILU_OMNIROUTE_MODEL", "modelo-test")
    monkeypatch.setenv("ILU_OMNIROUTE_URL", "http://omniroute.test/v1")
    monkeypatch.delenv("ILU_OMNIROUTE_API_KEY", raising=False)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "clave-respaldo")

    provider = OmniRouteProvider()
    assert provider.api_key == "clave-respaldo"

    payload = {
        "choices": [
            {"message": {"role": "assistant", "content": "ok"}}
        ]
    }

    with mock.patch(
        "app.providers.requests.post",
        return_value=FakeResponse(payload)
    ) as post:
        result = provider.generate("hola")

    assert result["type"] == "text"
    headers = post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer clave-respaldo"


def test_omniroute_http_error_does_not_leak_key(monkeypatch):
    provider = make_provider(monkeypatch, api_key="supersecreta")

    response = FakeResponse({}, status=401)

    with mock.patch(
        "app.providers.requests.post",
        return_value=response
    ):
        result = provider.generate("hola")

    assert result["type"] == "error"
    assert "supersecreta" not in result.get("detail", "")
    assert "supersecreta" not in result.get("content", "")

def test_omniroute_native_tool_call_string_arguments(monkeypatch):
    """OpenAI-compat entrega tool_calls con arguments como STRING JSON."""
    provider = make_provider(monkeypatch)

    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "system_time",
                                "arguments": '{"tz": "UTC"}',
                            },
                        }
                    ],
                }
            }
        ]
    }

    with mock.patch(
        "app.providers.requests.post",
        return_value=FakeResponse(payload)
    ):
        result = provider.generate(
            "hora",
            tools=[{"name": "system_time"}]
        )

    assert result["type"] == "tool_call"
    assert result["tool"] == "system_time"
    assert result["arguments"] == {"tz": "UTC"}


def test_omniroute_native_tool_call_denied(monkeypatch):
    """Un tool_call nativo no permitido jamás se ejecuta (fail-closed)."""
    provider = make_provider(monkeypatch)

    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "shell",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            }
        ]
    }

    with mock.patch(
        "app.providers.requests.post",
        return_value=FakeResponse(payload)
    ):
        result = provider.generate("ejecuta")

    assert result["type"] == "text"
    assert "shell" in result["content"]


# ------------------------------------------------------------------
# F-2 — timeout configurable (ILU_OMNIROUTE_TIMEOUT)
# ------------------------------------------------------------------

def test_omniroute_timeout_configurable(monkeypatch):
    monkeypatch.setenv("ILU_OMNIROUTE_TIMEOUT", "45")

    provider = OmniRouteProvider()

    assert provider.timeout == 45
