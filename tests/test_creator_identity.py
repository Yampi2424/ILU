"""
Bloque 14 — Identidad del creador.

Verifica que I.L.U. conoce a su creador desde el primer arranque: la
memoria durable guarda el nombre real (tipo family, importancia máxima)
y el principal 'owner' lo lleva en display_name y real_name.
"""

from config.identity import ILU_IDENTITY
from security.principal import Principal
from app.core import ILUCore


def test_ilucore_bootstraps_memoria_del_creador(monkeypatch, tmp_path):
    monkeypatch.delenv("ILU_OWNER_SECRET", raising=False)

    core = ILUCore()

    memories = core.memory.load_all()

    creador = memories.get("creador")
    assert creador is not None
    assert creador["type"] == "family"
    assert creador["content"] == "Jean Pierre Ronaldo Soto Acevedo"
    assert creador["importance"] == 10


def test_ilucore_bootstrap_es_idempotente(monkeypatch, tmp_path):
    monkeypatch.delenv("ILU_OWNER_SECRET", raising=False)

    core = ILUCore()
    core._bootstrap_creator_identity()
    core._bootstrap_creator_identity()

    memories = core.memory.load_all()
    entries = [
        entry for key, entry in memories.items()
        if key == "creador"
    ]
    assert len(entries) == 1


def test_principal_roundtrip_real_name():
    original = Principal(
        "owner",
        "owner",
        display_name="Jean Pierre Ronaldo Soto Acevedo",
        real_name="Jean Pierre Ronaldo Soto Acevedo",
    )

    restored = Principal.from_dict(original.to_dict())

    assert restored.display_name == "Jean Pierre Ronaldo Soto Acevedo"
    assert restored.real_name == "Jean Pierre Ronaldo Soto Acevedo"


def test_principal_real_name_default_none():
    # Retrocompatibilidad: sin real_name, el roundtrip no rompe.
    original = Principal("owner", "owner", display_name="Owner")

    restored = Principal.from_dict(original.to_dict())

    assert restored.real_name is None
    assert restored.display_name == "Owner"


def test_identity_creator_matches_memory(monkeypatch, tmp_path):
    monkeypatch.delenv("ILU_OWNER_SECRET", raising=False)

    core = ILUCore()

    assert ILU_IDENTITY["creator"] == core.memory.load_all()[
        "creador"
    ]["content"]