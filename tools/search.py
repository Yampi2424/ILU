"""
Herramienta de búsqueda web de I.L.U.

Sin clave y sin dependencias nuevas: usa la API de DuckDuckGo
Instant Answers (https://api.duckduckgo.com) vía urllib. Devuelve la
"respuesta directa" cuando existe; si no hay red o la API no responde,
falla de forma explícita (I.L.U. no oculta el fallo). Es de solo lectura
(permiso "safe") y funciona sin Internet → simplemente no puede buscar.

`_fetch_json` es la inyección que los tests sustituyen para no tocar red.
"""

import html
from html.parser import HTMLParser
import json
import socket
import urllib.error
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


# ----------------------------------------------------------------------
# web_fetch — lectura web gateada con guard SSRF (Fase B)
# ----------------------------------------------------------------------
#
# I.L.U. puede leer una página HTTP(S) de Internet, pero JAMÁS de la red
# interna: se bloquean localhost, loopback, rangos privados y link-local
# (IPv4 e IPv6) resolviendo el host antes de conectar. Los redirects se
# rechazan (un redirect podría apuntar de vuelta a la red interna).


class _ForbiddenRedirect(urllib.error.HTTPError):
    """Redirección rechazada por el guard SSRF (fail-closed)."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise _ForbiddenRedirect(
            newurl, code, "redirect_forbidden_by_ssrf", headers, fp
        )


class _TextExtractor(HTMLParser):
    """Extrae el texto visible de una página HTML (stdlib)."""

    _SKIP = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self._parts.append(text)

    def text(self):
        return "\n".join(self._parts)


def _is_blocked_address(ip):
    """True si la IP pertenece a una red sensible (SSRF)."""
    try:
        packed = socket.inet_pton(socket.AF_INET, ip)
    except (socket.error, OSError):
        packed = None

    if packed:
        first = packed[0]
        if first == 127:                 # 127.0.0.0/8 loopback
            return True
        if first == 10:                  # 10.0.0.0/8 privado
            return True
        if first == 0:                   # 0.0.0.0/8 unspecified
            return True
        if first >= 224 and first <= 239:  # 224/4 multicast
            return True
        if first == 169 and packed[1] == 254:   # 169.254/16 link-local
            return True
        if first == 172 and 16 <= packed[1] <= 31:  # 172.16/12
            return True
        if first == 192 and packed[1] == 168:       # 192.168/16
            return True
        return False

    try:
        packed6 = socket.inet_pton(socket.AF_INET6, ip)
    except (socket.error, OSError):
        return True  # no es una IP válida → no conectar

    if ip in ("::", "::1"):
        return True                                     # :: / ::1
    if packed6[0] == 0xFD or packed6[0] == 0xFC:        # fc00::/7 unique-local
        return True
    if packed6[0] == 0xFE and (packed6[1] & 0xC0) == 0x80:  # fe80::/10
        return True
    if packed6[0] == 0xFF:                              # ff00::/8 multicast
        return True
    return False


def _resolve_blocked(host, family=0):
    """Resuelve `host`; True si alguna de sus direcciones está bloqueada."""
    try:
        infos = socket.getaddrinfo(
            host, None, family=family,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        return True  # host no resuelve → fail-closed

    seen = set()
    for info in infos:
        ip = info[4][0]
        if ip not in seen:
            seen.add(ip)
            if _is_blocked_address(ip):
                return True
    return False


def web_fetch(url=None, max_bytes=64000):
    """
    Lee el texto visible de una URL HTTP(S) pública.

    Guard SSRF: el host se resuelve y se rechaza cualquier dirección
    loopback/privada/link-local/multicast/unspecified (IPv4 e IPv6). Los
    redirects se bloquean. `max_bytes` limita la lectura. La salida es
    texto plano (HTML o texto); nunca se ejecuta nada de lo leído.
    """
    url = (url or "").strip()

    if not url:
        return {"success": False, "error": "url_required"}

    parts = urllib.parse.urlsplit(url)

    if parts.scheme not in ("http", "https"):
        return {
            "success": False,
            "error": "scheme_not_allowed",
            "url": url,
        }

    host = parts.hostname

    if not host:
        return {
            "success": False,
            "error": "host_required",
            "url": url,
        }

    # Resolución SSRF (fail-closed).
    if _resolve_blocked(host):
        return {
            "success": False,
            "error": "host_blocked_ssrf",
            "host": host,
        }

    try:
        handler = _NoRedirectHandler()
        opener = urllib.request.build_opener(handler)

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "I.L.U. (investigacion; contacto: owner)",
                "Accept": "text/html,text/plain;q=0.9,application/json",
            },
        )

        try:
            with opener.open(request, timeout=8) as response:
                raw = response.read(int(max_bytes) + 1)
        except _ForbiddenRedirect:
            return {
                "success": False,
                "error": "redirect_forbidden",
                "url": url,
            }
        except urllib.error.HTTPError as http_error:
            return {
                "success": False,
                "error": "http_error",
                "status": getattr(http_error, "code", None),
                "url": url,
            }
        except Exception:
            return {
                "success": False,
                "error": "fetch_failed",
                "url": url,
            }

        if len(raw) > max_bytes:
            raw = raw[:max_bytes]

        content_type = (
            response.headers.get("Content-Type") or ""
        ).lower()

        text = raw.decode("utf-8", errors="replace")

        if "html" in content_type or "<html" in text[:1024].lower():
            extractor = _TextExtractor()
            try:
                extractor.feed(text)
                text = extractor.text()
            except Exception:
                text = " ".join(text.split())

        text = (text or "").strip()

        if not text:
            return {
                "success": True,
                "url": url,
                "content": "",
                "note": "empty_page",
            }

        return {
            "success": True,
            "url": url,
            "content": text[:max_bytes],
        }

    except Exception:
        # Cualquier imprevisto (lectura, configuración, red) se reporta
        # como un fallo controlado; nunca se propaga crudo ni conecta de
        # otra forma (fail-closed).
        return {
            "success": False,
            "error": "fetch_failed",
            "url": url,
        }