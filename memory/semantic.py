"""
I.L.U. — Búsqueda semántica/vectorial de memoria.

Recall por SIGNIFICADO, no solo por coincidencia de palabras. Dos motores
intercambiables, elegidos en runtime:

  - OllamaEmbedder: embeddings REALES vía Ollama (/api/embeddings). Se
    activa SOLO si el servidor los soporta (arrancado con --embeddings);
    de lo contrario `available()` es False y no se intenta de nuevo.
  - TfidfEmbedder: vectores TF-IDF + coseno en stdlib puro. Siempre
    disponible, local y sin dependencias.

Ambos producen un vector por texto y la similitud es coseno. La memoria
jamás queda bloqueada por la ausencia de un motor: si Ollama no ofrece
embeddings, se cae limpio a TF-IDF.
"""

import json
import math
import os
import re
import urllib.request

_TOKEN_RE = re.compile(r"[a-záéíóúüñ0-9]{2,}")

# Palabras funcionales que no aportan significado y ensucian el coseno.
_STOPWORDS = {
    "la", "las", "el", "los", "de", "del", "un", "una", "unos", "unas",
    "y", "e", "o", "u", "a", "al", "en", "es", "se", "su", "sus", "para",
    "por", "con", "sin", "lo", "le", "que", "qué", "como", "cuál", "cuando",
    "mi", "mis", "tu", "te", "me", "no", "si", "sí", "ya", "muy", "pero",
}


def tokenize(text):
    """
    Tokenización simple en minúsculas, con acentos preservados y sin
    palabras funcionales (stopwords) que no aportan significado.
    """
    return [
        token
        for token in _TOKEN_RE.findall(str(text).lower())
        if token not in _STOPWORDS
    ]


class TfidfEmbedder:
    """Vectores TF-IDF en stdlib puro; siempre disponible y determinista."""

    def __init__(self):
        self._df = {}
        self._n = 0

    def fit(self, texts):
        """Ingiere el corpus para calcular frecuencias de documento."""
        self._df = {}
        self._n = 0
        for text in texts:
            seen = set(tokenize(text))
            for term in seen:
                self._df[term] = self._df.get(term, 0) + 1
            self._n += 1
        return self

    def embed(self, text):
        """Devuelve {term: tfidf} para `text` en el vocabulario del corpus."""
        tokens = tokenize(text)
        if not tokens:
            return {}
        tf = {}
        for term in tokens:
            tf[term] = tf.get(term, 0) + 1
        n = max(self._n, 1)
        vector = {}
        for term, count in tf.items():
            idf = math.log(n / (self._df.get(term, 0) + 1)) + 1.0
            vector[term] = count * idf
        return vector

    def available(self):
        return True


class OllamaEmbedder:
    """
    Embeddings reales de Ollama. `available()` hace UNA sonda y la cachea
    a nivel de PROCESO (no por instancia): muchos ILUCore/MemoryRouter
    comparten el mismo servidor Ollama, y sondear cada vez (unos 3s si el
    servidor está arriba sin --embeddings) penalizaría enormemente. Si el
    servidor no soporta embeddings, no se vuelve a intentar en el proceso.
    """

    # Cache de disponibilidad compartida entre todas las instancias.
    _probe_cache = None  # None=sin sondear, True/False=resultado

    def __init__(self, base_url=None, model=None, timeout=3.0):
        self.base_url = (
            base_url
            or os.environ.get("ILU_OLLAMA_URL")
            or "http://127.0.0.1:11434"
        ).rstrip("/")
        self.model = (
            model
            or os.environ.get("ILU_EMBED_MODEL")
            or "llama3.2:1b-instruct-q3_K_M"
        )
        self.timeout = timeout

    def _request(self, text):
        payload = json.dumps({
            "model": self.model,
            "prompt": str(text),
        }).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        embedding = body.get("embedding")
        if not embedding:
            raise RuntimeError(str(body.get("error", "no_embedding")))
        return embedding

    def available(self):
        if OllamaEmbedder._probe_cache is None:
            try:
                self._request("probe")
                OllamaEmbedder._probe_cache = True
            except Exception:
                OllamaEmbedder._probe_cache = False
        return OllamaEmbedder._probe_cache

    def embed(self, text):
        if not self.available():
            raise RuntimeError("ollama_embeddings_unavailable")
        return self._request(text)


def cosine(a, b):
    """
    Similitud coseno entre dos vectores, que pueden ser dict {term: peso}
    (TF-IDF) o listas de floats (embeddings de Ollama). Devuelve 0.0 si
    alguno está vacío o los tipos no coinciden.
    """
    if not a or not b:
        return 0.0

    if isinstance(a, dict) and isinstance(b, dict):
        keys = set(a) | set(b)
        dot = 0.0
        for key in keys:
            dot += a.get(key, 0.0) * b.get(key, 0.0)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)

    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        length = min(len(a), len(b))
        if length == 0:
            return 0.0
        dot = 0.0
        na = nb = 0.0
        for i in range(length):
            dot += a[i] * b[i]
            na += a[i] * a[i]
            nb += b[i] * b[i]
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (math.sqrt(na) * math.sqrt(nb))

    return 0.0


def rank_records(query, records, embedder=None, limit=10):
    """
    Ordena MemoryRecords por similitud semántica con `query`.

    - Si `embedder` es Ollama y está disponible, usa sus embeddings reales.
    - Si no, construye un TF-IDF sobre el corpus de `records` + query y
      rankea por coseno.

    Devuelve los `limit` registros mejor puntuados (nunca lanza).
    """
    if not records:
        return []

    # Sin tokens significativos en la consulta no hay evidencia de qué
    # recordar: no se fabrican candidatos (misma regla que el recall léxico).
    if not tokenize(query):
        return []

    tfidf = TfidfEmbedder().fit(
        [record.content for record in records] + [query]
    )

    used_ollama = False
    if embedder is not None and embedder.available():
        try:
            query_vec = embedder.embed(query)
            scored = [
                (cosine(query_vec, embedder.embed(record.content)), record)
                for record in records
            ]
            used_ollama = True
        except Exception:
            used_ollama = False

    if not used_ollama:
        query_vec = tfidf.embed(query)
        scored = [
            (cosine(query_vec, tfidf.embed(record.content)), record)
            for record in records
        ]

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [record for _, record in scored[:limit]]
