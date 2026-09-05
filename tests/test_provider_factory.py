from app.providers import (
    CloudProvider,
    LocalProvider,
    OmniRouteProvider,
    create_provider,
)


def test_factory_default_omniroute(monkeypatch):
    # El proveedor por defecto de I.L.U. es OmniRoute (con fallback local
    # en create_runtime_provider); sin variable, se elige OmniRoute.
    monkeypatch.delenv("ILU_AI_PROVIDER", raising=False)
    assert isinstance(create_provider(), OmniRouteProvider)


def test_factory_default_runtime_omniroute(monkeypatch):
    # create_runtime_provider envuelve OmniRoute con fallback local.
    from app.providers import FallbackProvider, create_runtime_provider
    monkeypatch.delenv("ILU_AI_PROVIDER", raising=False)
    provider = create_runtime_provider()
    assert isinstance(provider, FallbackProvider)
    assert provider.primary.name == "omniroute"
    assert provider.fallback.name == "ollama"


def test_factory_local(monkeypatch):
    monkeypatch.setenv("ILU_AI_PROVIDER", "local")
    assert isinstance(create_provider(), LocalProvider)


def test_factory_omniroute(monkeypatch):
    monkeypatch.setenv("ILU_AI_PROVIDER", "omniroute")
    assert isinstance(create_provider(), OmniRouteProvider)


def test_factory_omniroute_case_insensitive(monkeypatch):
    monkeypatch.setenv("ILU_AI_PROVIDER", "OmniRoute")
    assert isinstance(create_provider(), OmniRouteProvider)


def test_factory_cloud_stub(monkeypatch):
    monkeypatch.setenv("ILU_AI_PROVIDER", "cloud")
    assert isinstance(create_provider(), CloudProvider)


def test_factory_unknown_falls_back_local(monkeypatch):
    monkeypatch.setenv("ILU_AI_PROVIDER", "desconocido")
    assert isinstance(create_provider(), LocalProvider)