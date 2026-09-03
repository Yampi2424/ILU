"""
Bloque 8 — Almacén de grants (permisos explícitos).

Verifica: la regla central "ningún permiso es permanente por defecto",
expiración/revocación invalidadas en el momento de la decisión, consumo
por uso único, persistencia y filtros.
"""

import pytest

from security.grant import Grant, grant_id
from security.grant_store import GrantStore


def test_grant_default_policy_requires_expiry_or_uses():
    # Sin expiración, sin max_uses y sin indefinite -> rechazado.
    with pytest.raises(ValueError) as info:
        Grant(capability="write_file", grantor="owner")

    assert "grant_must_not_be_permanent" in str(info.value)


def test_grant_with_expiry_is_valid():
    grant = Grant(
        capability="write_file",
        grantor="owner",
        expires_at="2099-01-01T00:00:00Z",
    )

    assert grant.is_active()
    assert grant.expires_at is not None


def test_grant_with_max_uses_is_valid():
    grant = Grant(
        capability="write_file",
        grantor="owner",
        max_uses=1,
    )

    assert grant.is_active()


def test_grant_explicitly_indefinite_is_valid_but_revocable():
    grant = Grant(
        capability="write_file",
        grantor="owner",
        indefinite=True,
    )

    assert grant.is_active()

    grant.revoke("owner", "por que sí")
    assert grant.status == "revoked"
    assert not grant.is_active()


def test_invalid_level_rejected():
    with pytest.raises(ValueError):
        Grant(
            capability="write_file",
            grantor="owner",
            level="god",
            expires_at="2099-01-01T00:00:00Z",
        )


def test_invalid_scope_rejected():
    with pytest.raises(ValueError):
        Grant(
            capability="write_file",
            grantor="owner",
            scope_type="everything",
            expires_at="2099-01-01T00:00:00Z",
        )


def test_empty_capability_rejected():
    with pytest.raises(ValueError):
        Grant(
            capability="",
            grantor="owner",
            expires_at="2099-01-01T00:00:00Z",
        )


def test_find_active_skips_expired(tmp_path):
    store = GrantStore(path=str(tmp_path / "grants.jsonl"))

    store.add(Grant(
        capability="write_file",
        grantor="owner",
        expires_at="2000-01-01T00:00:00Z",
    ))

    assert store.find_active("write_file") is None


def test_find_active_skips_revoked(tmp_path):
    store = GrantStore(path=str(tmp_path / "grants.jsonl"))

    grant = Grant(
        capability="write_file",
        grantor="owner",
        expires_at="2099-01-01T00:00:00Z",
    )
    store.add(grant)
    store.revoke(grant.key, "owner", "no más")

    assert store.find_active("write_file") is None


def test_single_use_consumed(tmp_path):
    store = GrantStore(path=str(tmp_path / "grants.jsonl"))

    grant = store.add(Grant(
        capability="write_file",
        grantor="owner",
        max_uses=1,
    ))

    found = store.find_active("write_file")
    assert found is not None and found.key == grant.key

    # Consumido: ya no queda grant activo.
    assert store.find_active("write_file") is None


def test_sweep_marks_expired(tmp_path):
    store = GrantStore(path=str(tmp_path / "grants.jsonl"))

    store.add(Grant(
        capability="write_file",
        grantor="owner",
        expires_at="2000-01-01T00:00:00Z",
    ))

    assert store.sweep_expired() == 1

    remaining = store.list(status="active")
    assert remaining == []


def test_persistence_roundtrip(tmp_path):
    path = str(tmp_path / "grants.jsonl")

    store = GrantStore(path=path)
    grant = store.add(Grant(
        capability="write_file",
        grantor="owner",
        expires_at="2099-01-01T00:00:00Z",
    ))

    reloaded = GrantStore(path=path)
    restored = reloaded.get(grant.key)

    assert restored is not None
    assert restored.capability == "write_file"
    assert restored.grantor == "owner"
    assert restored.status == "active"


def test_list_filters(tmp_path):
    store = GrantStore(path=str(tmp_path / "grants.jsonl"))

    store.add(Grant(
        capability="write_file",
        grantor="owner",
        expires_at="2099-01-01T00:00:00Z",
    ))

    store.add(Grant(
        capability="write_file",
        grantor="owner",
        expires_at="2099-01-01T00:00:00Z",
        max_uses=1,
    ))
    store.revoke(list(store.grants.values())[1].key, "owner", "x")

    assert len(store.list(capability="write_file")) == 2
    assert len(store.list(status="active")) == 1
    assert len(store.list(status="revoked")) == 1
    assert len(store.list(grantor="owner")) == 2


def test_grant_id_generator():
    a = grant_id()
    b = grant_id()

    assert a != b
    assert a.startswith("gr_")
    assert b.startswith("gr_")

# ------------------------------------------------------------------
# D-2/D-3 — matches() verifica actor; has_valid_for no consume
# ------------------------------------------------------------------

def test_matches_enforces_actor(tmp_path):
    """Un grant para 'ilu' NO debe servir a un actor distinto."""
    store = GrantStore(path=str(tmp_path / "grants.jsonl"))

    grant = store.add(Grant(
        capability="write_file",
        grantor="owner",
        grantee="ilu",
        expires_at="2099-01-01T00:00:00Z",
    ))

    # El destinatario legítimo sí coincide.
    assert grant.matches("write_file", actor="ilu")
    assert store.find_active("write_file", actor="ilu") is not None

    # Un actor ajeno NO debe usar el grant de "ilu".
    assert not grant.matches("write_file", actor="attacker")
    assert store.find_active("write_file", actor="attacker") is None


def test_matches_actor_empty_actor_kept_backward_compat(tmp_path):
    """Sin actor explícito se mantiene el comportamiento previo."""
    store = GrantStore(path=str(tmp_path / "grants.jsonl"))

    grant = store.add(Grant(
        capability="write_file",
        grantor="owner",
        grantee="ilu",
        expires_at="2099-01-01T00:00:00Z",
    ))

    assert grant.matches("write_file")  # actor=None
    assert store.find_active("write_file") is not None


def test_has_valid_for_does_not_consume(tmp_path):
    """
    Preguntar '¿hay permiso?' con has_valid_for NO debe quemar un grant
    de un solo uso (a diferencia de find_active, que sí lo consume).
    """
    store = GrantStore(path=str(tmp_path / "grants.jsonl"))

    store.add(Grant(
        capability="write_file",
        grantor="owner",
        max_uses=1,
    ))

    # Múltiples comprobaciones puras: el grant sigue activo.
    assert store.has_valid_for("write_file") is True
    assert store.has_valid_for("write_file") is True
    assert store.has_valid_for("write_file") is True

    # El grant sigue sin consumir.
    assert store.find_active("write_file") is not None

    # Tras un uso real, sí se agota.
    assert store.find_active("write_file") is None
