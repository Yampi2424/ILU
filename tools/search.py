"""
Herramienta de búsqueda web de I.L.U.

Sin clave y sin dependencias nuevas: usa la API de DuckDuckGo
Instant Answers (https://api.duckduckgo.com) vía urllib. Devuelve la
"respuesta directa" cuando existe; si no hay red o la API no responde,
falla de forma explícita (I.L.U. no oculta el fallo). Es de solo lectura
(permiso "safe") y funciona sin Internet → simplemente no puede buscar.

`_fetch_json` es la inyección que los tests sustituyen para no tocar red.
"""

import json
import urllib.parse
import urllib.request


def _fetch_json(url, timeout=8):
    """GET a una URL que devuelve JSON; None si falla (red/HTTP/parseo)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(
                response.read().decode("utf-8", errors="replace")
            )
    except Exception:
        return None


def web_search(query=None, max_results=5):
    """
    Búsqueda web ligera (DuckDuckGo Instant Answers).

    Devuelve un dict con "success" True/False y "results" (lista de
    {"snippet", "url"}). La apertura de una búsqueda sin red devuelve
    success False con error "web_search_unavailable".
    """
    query = (query or "").strip()

    if not query:
        return {"success": False, "error": "query_required"}

    url = (
        "https://api.duckduckgo.com/"
        "?q=" + urllib.parse.quote(query)
        + "&format=json&no_html=1&skip_disambig=1"
    )

    data = _fetch_json(url)

    if data is None:
        return {
            "success": False,
            "error": "web_search_unavailable",
            "query": query,
        }

    abstract = (data.get("AbstractText") or "").strip()

    if not abstract:
        return {"success": True, "query": query, "results": []}

    results = [{
        "snippet": abstract[:400],
        "url": data.get("AbstractURL") or "",
    }]

    return {
        "success": True,
        "query": query,
        "results": results[:max_results],
    }