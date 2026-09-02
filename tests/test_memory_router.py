from memory.router import MemoryRouter
from memory.backends import JsonBackend, InMemoryBackend


def make_router(tmp_path):
    return MemoryRouter(
        backend=JsonBackend(path=str(tmp_path / "memory.json"))
    )


def test_remember_and_query(tmp_path):
    router = make_router(tmp_path)

    record = router.remember(
        "a la familia le gusta el té",
        memory_type="personal",
        importance=9,
    )

    assert record.key
    assert record.memory_type == "personal"
    assert record.importance == 9

    results = router.query("té")

    assert any(r.key == record.key for r in results)


def test_remember_generates_globally_unique_keys(tmp_path):
    # Claves cortas únicas (UUID), no secuenciales: condición para que
    # varias ubicaciones de I.L.U. puedan sincronizar sin colisiones.
    router = make_router(tmp_path)

    a = router.remember("uno")
    b = router.remember("dos")

    assert a.key != b.key
    assert a.key.startswith("mem_")
    assert b.key.startswith("mem_")
    assert len(a.key) == len("mem_") + 8


def test_remember_uses_type_default_importance(tmp_path):
    router = make_router(tmp_path)

    record = router.remember(
        "preferencia personal",
        memory_type="personal",
    )

    assert record.importance == 8


def test_remember_empty_content(tmp_path):
    router = make_router(tmp_path)

    assert router.remember("   ") is None


def test_list_by_type(tmp_path):
    router = make_router(tmp_path)

    router.remember("python", memory_type="skill")
    router.remember("bash", memory_type="skill")
    router.remember("una conversación", memory_type="conversation")

    skills = router.list_by_type("skill")

    assert len(skills) == 2


def test_update_corrects_content(tmp_path):
    router = make_router(tmp_path)

    record = router.remember("el color favorito es azul", memory_type="personal")

    updated = router.update(record.key, content="el color favorito es verde")

    assert updated.content == "el color favorito es verde"
    assert router.get(record.key).content == "el color favorito es verde"


def test_update_unknown_key_returns_none(tmp_path):
    router = make_router(tmp_path)

    assert router.update("no-existe", content="x") is None


def test_link_and_related(tmp_path):
    router = make_router(tmp_path)

    skill = router.remember("docker", memory_type="skill")
    error = router.remember("falló el puerto 8000", memory_type="error")

    assert router.link(skill.key, error.key, relation="aprendido de") is True

    related = router.related(skill.key)

    assert len(related) == 1
    assert related[0]["record"].key == error.key
    assert related[0]["relation"] == "aprendido de"


def test_link_requires_both_existing(tmp_path):
    router = make_router(tmp_path)

    a = router.remember("uno")

    assert router.link(a.key, "no-existe") is False


def test_unlink(tmp_path):
    router = make_router(tmp_path)

    a = router.remember("uno")
    b = router.remember("dos")

    router.link(a.key, b.key)
    assert len(router.related(a.key)) == 1

    router.unlink(a.key, b.key)
    assert router.related(a.key) == []


def test_forget_deletes_closest(tmp_path):
    router = make_router(tmp_path)

    router.remember("receta secreta de la abuela", memory_type="personal")

    deleted = router.forget("receta")

    assert deleted is not None
    assert "receta" in deleted.content

    assert router.query("receta") == []


def test_forget_no_match_returns_none(tmp_path):
    router = make_router(tmp_path)

    assert router.forget("no existe nada parecido") is None


def test_correct_replaces_content(tmp_path):
    router = make_router(tmp_path)

    router.remember("el cumpleaños de ana es en mayo", memory_type="personal")

    updated = router.correct(
        "cumpleaños de ana",
        "el cumpleaños de ana es en julio",
    )

    assert updated is not None
    assert "julio" in updated.content
    assert "mayo" not in updated.content


def test_stats_counts_by_type(tmp_path):
    router = make_router(tmp_path)

    router.remember("uno", memory_type="conversation")
    router.remember("dos", memory_type="conversation")
    router.remember("skill uno", memory_type="skill")

    stats = router.stats()

    assert stats["total"] == 3
    assert stats["counts"]["conversation"] == 2
    assert stats["counts"]["skill"] == 1


def test_memorystore_compatible_save_search_load_all(tmp_path):
    # El router sustituye a MemoryStore en el pipeline sin romper
    # la interfaz que usa core.
    router = make_router(tmp_path)

    router.save("clave", "contenido conservado", memory_type="personal", importance=9)

    assert router.load_all()["clave"]["content"] == "contenido conservado"
    assert router.load_all()["clave"]["type"] == "personal"

    results = router.search("contenido")

    assert results[0]["content"] == "contenido conservado"
    assert results[0]["type"] == "personal"


def test_works_with_in_memory_backend_too(tmp_path):
    # Mismo comportamiento sobre un almacén distinto: I.L.U. no está
    # atada a JSON.
    router = MemoryRouter(backend=InMemoryBackend())

    record = router.remember("experiencia", memory_type="experience")
    router.remember("habilidad", memory_type="skill")

    assert router.get(record.key).memory_type == "experience"
    assert len(router.list_by_type("skill")) == 1
    assert router.stats()["total"] == 2