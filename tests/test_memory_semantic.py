"""
Búsqueda semántica/vectorial de memoria (recall por significado).

El recall por palabra clave se queda corto: "¿qué toma por la mañana?"
no comparte términos con "al usuario le gusta tomar café con leche". La
capa semántica rankea por similitud de significado (coseno) y cae de
forma limpia a TF-IDF local cuando no hay embeddings de Ollama.
"""

import pytest

from memory.backends import JsonBackend, MemoryRecord
from memory.router import MemoryRouter
from memory import semantic as S


@pytest.fixture
def router(tmp_path):
    backend = JsonBackend(path=str(tmp_path / "mem.json"))
    return MemoryRouter(backend=backend)


CORPUS = [
    "al usuario le gusta tomar café con leche cada mañana",
    "el proyecto I.L.U. usa el lenguaje python",
    "la mascota del usuario se llama Luna y es una gata",
    "la reunión del equipo es los lunes a las diez",
]


def _records():
    return [
        MemoryRecord(key=f"k{i}", content=text, memory_type="general",
                     importance=5)
        for i, text in enumerate(CORPUS)
    ]


# ----------------------------------------------------------------------
# Motor TF-IDF
# ----------------------------------------------------------------------

def test_tfidf_ranks_related_over_unrelated():
    records = _records()
    # Consulta sobre la bebida matutina: no comparte palabras con python.
    ranked = S.rank_records("¿qué toma la persona por la mañana?", records)
    assert ranked[0].key == "k0"

    ranked = S.rank_records("¿en qué lenguaje está escrito el proyecto?",
                            records)
    assert ranked[0].key == "k1"

    ranked = S.rank_records("¿cómo se llama la mascota?", records)
    assert ranked[0].key == "k2"


def test_stopwords_do_not_create_false_similarity():
    # Dos textos que solo comparten "la" no son similares (stopword).
    a = {"la": 1.0, "perro": 2.0}
    b = {"la": 1.0, "gato": 2.0}
    assert S.cosine(a, b) < 0.5
    # Sin la stopword, coincidencia plena.
    assert S.cosine({"café": 1.0}, {"café": 1.0}) == 1.0


def test_rank_records_empty_query_returns_none():
    assert S.rank_records("", _records()) == []
    assert S.rank_records("   ", _records()) == []


def test_cosine_supports_list_vectors():
    # Forma de los embeddings de Ollama (listas).
    assert S.cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert S.cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert S.cosine([], [1.0]) == 0.0


def test_tokenize_drops_stopwords():
    tokens = S.tokenize("la gata bebe leche con café")
    assert "la" not in tokens and "con" not in tokens
    assert "gata" in tokens and "café" in tokens


# ----------------------------------------------------------------------
# Integración en MemoryRouter
# ----------------------------------------------------------------------

def test_semantic_search_recalls_by_meaning(router, monkeypatch):
    # Fijar el motor TF-IDF: el test es determinista sea cual sea Ollama.
    monkeypatch.setattr(router, "_get_embedder", lambda: S.TfidfEmbedder())

    for i, text in enumerate(CORPUS):
        router.remember(text, key=f"k{i}", memory_type="general",
                        importance=5)

    # La consulta no comparte palabras con "k0", pero el significado sí.
    found = router.semantic_search("¿qué toma la persona por la mañana?",
                                   limit=3)
    assert found, "debe encontrar recuerdos"
    assert found[0]["key"] == "k0"
    # Forma de store dict (compatible con search).
    assert "content" in found[0] and "type" in found[0]


def test_semantic_search_scoped_by_type(router, monkeypatch):
    monkeypatch.setattr(router, "_get_embedder", lambda: S.TfidfEmbedder())

    router.remember("gusta el café", key="pref", memory_type="personal",
                    importance=6)
    router.remember("python para proyectos", key="tech",
                    memory_type="episodic", importance=3)

    found = router.semantic_search("bebida favorita", memory_type="personal")
    assert all(item["type"] == "personal" for item in found)
    assert any(item["key"] == "pref" for item in found)


def test_semantic_search_handles_empty_memory(router):
    assert router.semantic_search("cualquier cosa") == []


def test_semantic_search_fallback_when_ollama_down(router, monkeypatch):
    """Si el embedder de Ollama no está disponible, cae a TF-IDF sin lanzar."""
    router.remember("me gusta el café", key="c1", memory_type="general")

    class Down:
        def available(self):
            return False

        def embed(self, text):
            raise RuntimeError("unavailable")

    monkeypatch.setattr(router, "_get_embedder", lambda: Down())

    found = router.semantic_search("bebida caliente por la mañana")
    assert any(item["key"] == "c1" for item in found)


def test_ollama_embedder_unavailable_reported(monkeypatch):
    """Un servidor que no soporta embeddings se reporta como no disponible."""
    import urllib.request

    def fail(*args, **kwargs):
        raise urllib.error.URLError("no server")

    monkeypatch.setattr(urllib.request, "urlopen", fail)

    embedder = S.OllamaEmbedder(base_url="http://127.0.0.1:1")
    assert embedder.available() is False
