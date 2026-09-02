"""
Bloque 9 — Fallback cloud→local.

`FallbackProvider` envuelve un proveedor PRIMARIO (p. ej. OmniRoute) y, si
este devuelve un error de comunicación/configuración, delega en un proveedor
de RESPALDO (p. ej. Ollama local). Si el primario responde bien, se usa su
resultado tal cual.
"""

from unittest import mock

from app.providers import (
    FallbackProvider,
    LocalProvider,
    OmniRouteProvider,
)


class StubProvider:
    """Proveedor programable para aislar el comportamiento del fallback."""

    def __init__(self, result, name="stub", version="0.0.0"):
        self.result = result
        self.name = name
        self.version = version

    def generate(self, message, context=None, tools=None):
        return self.result


def test_primary_error_triggers_fallback():
    primary = StubProvider(
        {"type": "error", "content": "caído", "detail": "red"},
        name="omniroute",
    )
    fallback = StubProvider(
        {"type": "text", "content": "respuesta local"},
        name="ollama",
    )

    provider = FallbackProvider(primary=primary, fallback=fallback)

    result = provider.generate("hola")

    assert result["type"] == "text"
    assert result["content"] == "respuesta local"
    assert result["fallback"] is True
    assert result["provider_used"] == "ollama"
    assert result["provider_used_version"] == "0.0.0"


def test_primary_success_no_fallback():
    primary = StubProvider(
        {"type": "text", "content": "ok cloud"},
        name="omniroute",
    )
    fallback = StubProvider(
        {"type": "text", "content": "no debería usarse"},
        name="ollama",
    )

    provider = FallbackProvider(primary=primary, fallback=fallback)

    result = provider.generate("hola")

    assert result["type"] == "text"
    assert result["content"] == "ok cloud"
    assert "fallback" not in result
    assert result["provider_used"] == "omniroute"


def test_tool_call_passthrough_from_primary():
    primary = StubProvider(
        {
            "type": "tool_call",
            "tool": "system_time",
            "arguments": {},
            "reason": "hora",
        },
        name="omniroute",
    )
    fallback = StubProvider(
        {"type": "text", "content": "no debería usarse"},
        name="ollama",
    )

    provider = FallbackProvider(primary=primary, fallback=fallback)

    result = provider.generate("hora")

    # Un tool_call del primario NO dispara fallback y pasa tal cual.
    assert result["type"] == "tool_call"
    assert result["tool"] == "system_time"
    assert "fallback" not in result


def test_name_and_version_reflect_primary():
    primary = StubProvider(
        {"type": "text", "content": "x"},
        name="omniroute",
        version="0.8.0",
    )
    provider = FallbackProvider(primary=primary, fallback=StubProvider({}))

    assert provider.name == "omniroute"
    assert provider.version == "0.8.0"


def test_real_omniroute_error_falls_back_to_local(monkeypatch):
    """Integración: OmniRoute cae (HTTP 500) -> responde Ollama local."""
    monkeypatch.setenv("ILU_OMNIROUTE_API_KEY", "clave")
    monkeypatch.setenv("ILU_OMNIROUTE_URL", "http://omniroute.test/v1")
    monkeypatch.setenv("ILU_OMNIROUTE_MODEL", "modelo-test")
    monkeypatch.setenv("ILU_LOCAL_MODEL", "local-test")
    monkeypatch.setenv("ILU_OLLAMA_URL", "http://ollama.test:11434")
    monkeypatch.setenv("ILU_OLLAMA_TIMEOUT", "30")

    provider = FallbackProvider(
        primary=OmniRouteProvider(),
        fallback=LocalProvider(),
    )

    class FakeResponse:
        def __init__(self, payload, status=200):
            self._payload = payload
            self._status = status

        def raise_for_status(self):
            from requests import exceptions as rex
            if self._status >= 400:
                raise rex.HTTPError(f"HTTP {self._status}")

        def json(self):
            return self._payload

    # El primario devuelve 500; el fallback responde texto.
    def fake_post(url, **kwargs):
        if "omniroute" in url:
            return FakeResponse({}, status=500)
        return FakeResponse(
            {"message": {"role": "assistant", "content": "local ok"}}
        )

    with mock.patch(
        "app.providers.requests.post",
        side_effect=fake_post,
    ):
        result = provider.generate("hola")

    assert result["type"] == "text"
    assert result["content"] == "local ok"
    assert result["fallback"] is True
    assert result["provider_used"] == "ollama"
