"""Herramienta web_search: búsqueda web ligera sin clave."""

from tools.search import web_search, _fetch_json
from tools import search as search_module


def test_query_required(monkeypatch):
    assert web_search("   ") == {
        "success": False,
        "error": "query_required",
    }


def test_search_with_abstract(monkeypatch, tmp_path):
    monkeypatch.setattr(
        search_module,
        "_fetch_json",
        lambda url, timeout=8: {
            "AbstractText": "I.L.U. es un asistente inteligente.",
            "AbstractURL": "https://example.org/ilu",
        },
    )

    result = web_search("ilu")

    assert result["success"] is True
    assert result["query"] == "ilu"
    assert result["results"][0]["snippet"].startswith("I.L.U.")


def test_search_returns_empty_when_no_instant_answer(monkeypatch):
    monkeypatch.setattr(
        search_module,
        "_fetch_json",
        lambda url, timeout=8: {
            "AbstractText": "",
            "AbstractURL": "",
        },
    )

    result = web_search("consulta sin respuesta")

    assert result["success"] is True
    assert result["results"] == []


def test_search_fails_loudly_offline(monkeypatch):
    # Sin red: la búsqueda falla de forma explícita, no lo oculta.
    monkeypatch.setattr(
        search_module,
        "_fetch_json",
        lambda url, timeout=8: None,
    )

    result = web_search("algo")

    assert result["success"] is False
    assert result["error"] == "web_search_unavailable"
    assert result["query"] == "algo"


def test_fetch_json_returns_none_on_error(monkeypatch):
    import urllib.request

    def broken(response_url, timeout=None):
        raise OSError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", broken)

    assert _fetch_json("https://ejemplo") is None