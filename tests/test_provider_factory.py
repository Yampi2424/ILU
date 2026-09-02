from app.providers import (
    CloudProvider,
    LocalProvider,
    OmniRouteProvider,
    create_provider,
)


def test_factory_default_local(monkeypatch):
    monkeypatch.delenv("ILU_AI_PROVIDER", raising=False)
    assert isinstance(create_provider(), LocalProvider)


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