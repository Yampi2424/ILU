"""
Ingesta de archivos locales a la memoria de I.L.U. (Fase E).

Reemplaza la idea de "connectors/OAuth" de OpenJarvis con una ingestión
LOCAL, real y sin cuentas externas: I.L.U. lee material del workspace
(.md/.txt/.html) y lo incorpora a su memoria semántica por chunks con
`source=ingest` y metadata de ruta.

Seguridad:
- Solo se leen rutas dentro del workspace (`resolve_within_workspace`).
- Se saltan ocultos, binarios, bases de datos y los propios stores de
  I.L.U. (nunca se auto-ingiere su runtime secreto).
- La tool `memory_ingest` que expone esto tiene permission "safe" y va
  a través de la compuerta (lo registra el núcleo; aquí solo la lógica).

El chunking reutiliza el patrón de `memory/backends` (una memoria por
fragmento) vía `MemoryRouter.remember`.
"""

import os

from tools.filesystem import resolve_within_workspace


# Extensiones de texto plano que I.L.U. entiende por defecto.
_TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".rst", ".log"}

# Archivos/rutas del runtime de I.L.U. que jamás se auto-ingieren.
_SENSITIVE_FRAGMENTS = (
    "security/",
    "memory/audit.jsonl",
    "memory/conversations.jsonl",
    "memory/goals.jsonl",
    "memory/scheduler.jsonl",
    "memory/agents.jsonl",
    "memory/benchmarks.jsonl",
    "." + os.path.join("device.key"),
    "omniroute.key",
    "owner.pin",
)

# Tamaño de chunk aproximado (caracteres) para memorias de material.
_CHUNK_SIZE = 2200
_OVERLAP = 200


def _is_binary(path):
    """Detección rápida de binario: bytes de control en el primer bloque."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(1024)
    except OSError:
        return True  # ilegible → tratar como binario (fail-closed)
    if not head:
        return False  # vacío → no binario (no generará chunks)
    control = sum(1 for b in head if b < 9 or (13 < b < 32))
    return control > 10


def _is_sensitive(path):
    """True si la ruta apunta a un store/secreto del sistema de I.L.U."""
    norm = str(path).replace("\\", "/")
    for fragment in _SENSITIVE_FRAGMENTS:
        if fragment in norm:
            return True
    return False


def _chunk(text, size=_CHUNK_SIZE, overlap=_OVERLAP):
    """Divide el texto en fragmentos con solape leve para contexto."""
    text = "\n".join(line.rstrip() for line in text.splitlines())
    if len(text) <= size:
        return [text] if text.strip() else []

    chunks = []
    step = size - overlap
    for i in range(0, len(text), step):
        chunk = text[i:i + size]
        if chunk.strip():
            chunks.append(chunk)
    return chunks


def _extract(path, stream_bytes):
    """Devuelve texto plano según extensión (HTML se depura)."""
    text = stream_bytes.decode("utf-8", errors="replace")
    ext = os.path.splitext(path)[1].lower()

    if ext == ".html" or ext == ".htm":
        from html.parser import HTMLParser

        class _Text(HTMLParser):
            _skip = {"script", "style", "noscript", "template"}

            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.depth = 0
                self.parts = []

            def handle_starttag(self, tag, attrs):
                if tag in self._skip:
                    self.depth += 1

            def handle_endtag(self, tag):
                if tag in self._skip and self.depth:
                    self.depth -= 1

            def handle_data(self, data):
                if not self.depth and data.strip():
                    self.parts.append(data.strip())

        parser = _Text()
        try:
            parser.feed(text)
            text = "\n".join(parser.parts)
        except Exception:
            text = " ".join(text.split())

    return text


def ingest_bytes(
    path,
    content_bytes,
    router,
    source=None,
    tag=None,
):
    """
    Ingiere CONTENIDO ya leído (string/bytes) a la memoria.

    Devuelve {"success", "chunks", "keys", "path"}. `router` es un
    MemoryRouter (el del núcleo). No toca la red ni el filesystem.
    """
    if content_bytes is None:
        return {"success": False, "error": "empty_content"}

    if isinstance(content_bytes, str):
        content_bytes = content_bytes.encode("utf-8", errors="replace")

    if _is_sensitive(str(path)):
        return {
            "success": False,
            "error": "sensitive_path",
            "path": str(path),
        }

    text = _extract(path, content_bytes)
    chunks = _chunk(text)

    if not chunks:
        return {
            "success": True,
            "chunks": 0,
            "path": str(path),
            "keys": [],
            "note": "sin_texto",
        }

    keys = []
    total = len(chunks)

    for i, chunk in enumerate(chunks):
        record = router.remember(
            chunk,
            memory_type="episodic",
            importance=3,
            source=source or "ingest",
            tags=[t for t in (tag, "ingest") if t],
            metadata={
                "path": str(path),
                "chunk": i,
                "chunks_total": total,
            },
        )
        if record is not None:
            keys.append(record.key)

    return {
        "success": True,
        "chunks": len(chunks),
        "path": str(path),
        "keys": keys,
    }


def ingest_path(path, router, source=None, tag=None):
    """
    Lee e ingiere una ruta del workspace (fail-closed fuera de scope).

    Mismo contrato que `ingest_bytes` pero resolviendo primero la ruta en
    el workspace y saltando binarios/bases de datos provenentes.
    """
    try:
        resolved = resolve_within_workspace(path)
    except ValueError:
        return {"success": False, "error": "outside_workspace", "path": path}

    if resolved is None:
        return {"success": False, "error": "outside_workspace", "path": path}

    # Una ruta inexistente NO es un archivo: fail-closed antes de mirar
    # extensiones o leer bytes.
    if not os.path.isfile(resolved):
        return {"success": False, "error": "not_a_file", "path": str(resolved)}

    if _is_sensitive(resolved):
        return {"success": False, "error": "sensitive_path", "path": path}

    ext = os.path.splitext(resolved)[1].lower()

    if ext not in _TEXT_EXTENSIONS and ext not in (".html", ".htm"):
        return {
            "success": False,
            "error": "unsupported_extension",
            "path": str(resolved),
            "extension": ext,
        }

    if _is_binary(resolved):
        return {"success": False, "error": "binary_file", "path": str(resolved)}

    try:
        with open(resolved, "rb") as fh:
            data = fh.read()
    except OSError as error:
        return {
            "success": False,
            "error": "read_failed",
            "path": str(resolved),
            "detail": str(error),
        }

    return ingest_bytes(resolved, data, router, source=source, tag=tag)


def walk_workspace(top=None):
    """
    Lista rutas interesantes del workspace (docs de texto, sin el
    runtime de I.L.U.) para que el bucle de ingesta las procese.
    """
    from tools.filesystem import workspace_root

    base = top or workspace_root()
    found = []

    if base is None or not os.path.isdir(base):
        return found

    for root, dirs, files in os.walk(base):
        # Nunca descender a lugares del runtime o del propio código.
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".") and d not in (
                "__pycache__", "node_modules", ".git",
                "security", "memory",
            )
        ]
        for fname in files:
            if fname.startswith("."):
                continue
            full = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()
            if ext in _TEXT_EXTENSIONS | {".html", ".htm"}:
                if not _is_sensitive(full):
                    found.append(full)

    return found[:200]