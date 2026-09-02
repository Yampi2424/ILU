"""
Consulta dirigida (recall): recall_intent / recall_context / recall_recent
como puerta única para que core, razonamiento y futuros subagentes pregunten
a la memoria por intención, sin heurísticas dispersas.
"""

from memory.router import MemoryRouter
from memory.backends import JsonBackend, InMemoryBackend


def make_router(tmp_path):
    return MemoryRouter(
        backend=JsonBackend(path=str(tmp_path / "m.json"))
    )


def test_recall_intent_returns_relevant_memories(tmp_path):
    router = make_router(tmp_path)

    router.remember("el servidor escucha en el puerto 8080", memory_type="semantic")
    router.remember("a la familia le gusta el té", memory_type="personal")
    router.remember("falló el puerto 8000 una vez", memory_type="error")

    results = router.recall_intent("¿en qué puerto escucha el servidor?")

    assert any("8080" in r.content for r in results)


def test_recall_context_filters_to_conversational_types(tmp_path):
    router = make_router(tmp_path)

    router.remember("el usuario se llama Ana", memory_type="personal")
    router.remember("una charla sobre el té", memory_type="conversation")
    router.remember("python avanzado", memory_type="skill")

    results = router.recall_context("cuéntame sobre Ana")

    # Solo los tipos de contexto conversacional/personal.
    assert all(r.memory_type in ("conversation", "working", "personal")
               for r in results)


def test_recall_recent_returns_latest_of_a_type(tmp_path):
    router = make_router(tmp_path)

    router.remember("episodio uno", memory_type="episodic")
    router.remember("episodio dos", memory_type="episodic")
    router.remember("una tarea", memory_type="task")

    episodes = router.recall_recent(memory_type="episodic", n=10)

    assert len(episodes) == 2
    assert all(r.memory_type == "episodic" for r in episodes)


def test_recall_works_on_in_memory_backend(tmp_path):
    router = MemoryRouter(backend=InMemoryBackend())

    router.remember("aprendí a usar docker", memory_type="procedural")

    results = router.recall_intent("cómo uso docker")

    assert any("docker" in r.content for r in results)
