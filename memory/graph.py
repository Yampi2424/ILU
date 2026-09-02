"""
Relaciones entre recuerdos de I.L.U.

Los recuerdos no viven aislados: un conocimiento puede relacionarse con
una experiencia, una tarea con su resultado, una habilidad con los
errores que la corrigieron. Aquí se modelan esas relaciones.

La relación se guarda como `links` dentro de cada `MemoryRecord`
(una lista de {"key", "relation"}), de modo que funciona sobre cualquier
`MemoryBackend` sin necesidad de una base de datos de grafos dedicada.
Para la mayoría de los usos de I.L.U. esto es suficiente; si más adelante
hace falta un grafo real, la interfaz de esta capa se conserva.

Un mismo vínculo se registra en ambos extremos (simetría), de forma que
se pueda navegar desde cualquiera de los dos recuerdos.
"""


def _twin(record_a, record_b, relation):
    """Crea el enlace desde a hacia b y su gemelo desde b hacia a."""
    outgoing = {
        "key": record_b.key,
        "relation": relation,
    }

    incoming = {
        "key": record_a.key,
        "relation": relation,
    }

    return outgoing, incoming


def link_records(backend, key_a, key_b, relation=None):
    """
    Relaciona dos recuerdos (idempotente).

    Devuelve True si ambos existían y quedaron vinculados.
    """
    record_a = backend.get(key_a)
    record_b = backend.get(key_b)

    if record_a is None or record_b is None:
        return False

    a_link, b_link = _twin(record_a, record_b, relation)

    if a_link not in record_a.links:
        record_a.links.append(a_link)
        backend.save(record_a)

    if b_link not in record_b.links:
        record_b.links.append(b_link)
        backend.save(record_b)

    return True


def unlink_records(backend, key_a, key_b):
    """Quita el vínculo entre dos recuerdos. Devuelve True si ambos existían."""
    record_a = backend.get(key_a)
    record_b = backend.get(key_b)

    if record_a is None or record_b is None:
        return False

    record_a.links = [
        link for link in record_a.links
        if link.get("key") != key_b
    ]

    record_b.links = [
        link for link in record_b.links
        if link.get("key") != key_a
    ]

    backend.save(record_a)
    backend.save(record_b)

    return True


def related(backend, key):
    """
    Devuelve los recuerdos relacionados con `key`.

    Cada entrada es {"record": MemoryRecord, "relation": str|None}.
    """
    record = backend.get(key)

    if record is None:
        return []

    results = []

    for link in record.links:
        peer = backend.get(link.get("key"))

        if peer is not None:
            results.append({
                "record": peer,
                "relation": link.get("relation"),
            })

    return results
