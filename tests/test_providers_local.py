from unittest import mock

from requests import exceptions as requests_exceptions

from app.providers import LocalProvider


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


def make_provider(monkeypatch):
    monkeypatch.setenv("ILU_LOCAL_MODEL", "modelo-test")
    monkeypatch.setenv("ILU_OLLAMA_URL", "http://ollama.test:11434")
    monkeypatch.setenv("ILU_OLLAMA_TIMEOUT", "30")
    return LocalProvider()


def test_local_payload_and_text(monkeypatch):
    provider = make_provider(monkeypatch)

    payload = {
        "message": {
            "role": "assistant",
            "content": "Hola, ¿cómo estás?"
        }
    }

    with mock.patch(
        "app.providers.requests.post",
        return_value=FakeResponse(payload)
    ) as post:
        result = provider.generate("hola")

    assert result["type"] == "text"
    assert result["content"] == "Hola, ¿cómo estás?"

    post.assert_called_once()

    url = post.call_args.args[0]
    assert url == "http://ollama.test:11434/api/chat"

    sent = post.call_args.kwargs["json"]
    assert sent["model"] == "modelo-test"
    assert sent["stream"] is False
    assert sent["messages"][0]["role"] == "system"
    assert sent["messages"][1] == {
        "role": "user",
        "content": "hola"
    }


def test_local_tool_call_allowed(monkeypatch):
    provider = make_provider(monkeypatch)

    content = (
        '{"tool": "system_time", "arguments": {}, '
        '"reason": "el usuario pide la hora"}'
    )

    payload = {
        "message": {
            "role": "assistant",
            "content": content
        }
    }

    with mock.patch(
        "app.providers.requests.post",
        return_value=FakeResponse(payload)
    ):
        result = provider.generate(
            "¿qué hora es?",
            tools=[{"name": "system_time"}]
        )

    assert result["type"] == "tool_call"
    assert result["tool"] == "system_time"
    assert result["arguments"] == {}
    assert result["reason"]


def test_local_tool_call_denied(monkeypatch):
    provider = make_provider(monkeypatch)

    content = (
        '{"tool": "shell", "arguments": {}, '
        '"reason": "ejecutar comando"}'
    )

    payload = {
        "message": {
            "role": "assistant",
            "content": content
        }
    }

    with mock.patch(
        "app.providers.requests.post",
        return_value=FakeResponse(payload)
    ):
        result = provider.generate("ejecuta algo")

    # La herramienta no está permitida => no se ejecuta, se
    # devuelve la respuesta como texto.
    assert result["type"] == "text"
    assert "shell" in result["content"]


def test_local_http_error(monkeypatch):
    provider = make_provider(monkeypatch)

    response = FakeResponse({}, status=500)

    with mock.patch(
        "app.providers.requests.post",
        return_value=response
    ):
        result = provider.generate("hola")

    assert result["type"] == "error"
    assert result["content"]
    assert result["detail"]


def test_local_timeout(monkeypatch):
    provider = make_provider(monkeypatch)

    with mock.patch(
        "app.providers.requests.post",
        side_effect=requests_exceptions.Timeout()
    ):
        result = provider.generate("hola")

    assert result["type"] == "error"
    assert "tiempo" in result["content"]