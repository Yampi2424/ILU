"""
Bloque 8 — Capa de identidad humana (Principal / PrincipalRegistry).

Verifica: tipos válidos, autoridad raíz (owner/family_root), bootstrap
del OWNER desde ILU_OWNER_ID, persistencia y rechazo de tipos inválidos.
"""

import pytest

from security.principal import (
    Principal,
    PrincipalRegistry,
    VALID_TYPES,
    ROOT_TYPES,
)


def test_valid_types_include_owner_and_ilu():
    assert "owner" in VALID_TYPES
    assert "ilu" in VALID_TYPES
    assert "guest" in VALID_TYPES


def test_owner_is_root():
    principal = Principal("yampi", "owner")
    assert principal.is_root is True


def test_family_root_is_root():
    principal = Principal("root_familia", "family_root")
    assert principal.is_root is True


def test_family_member_is_not_root():
    principal = Principal("m", "family_member")
    assert principal.is_root is False


def test_ilu_is_never_root():
    principal = Principal("ilu", "ilu")
    assert principal.is_root is False
    assert "ilu" not in ROOT_TYPES


def test_invalid_principal_type_rejected():
    with pytest.raises(ValueError):
        Principal("x", "superadmin")


def test_bootstrap_creates_owner(tmp_path):
    registry = PrincipalRegistry(
        path=str(tmp_path / "principals.json"),
        owner_id="yampi",
    )

    owner = registry.owner()

    assert owner is not None
    assert owner.principal_id == "yampi"
    assert owner.is_root is True


def test_bootstrap_exactly_once(tmp_path):
    path = str(tmp_path / "principals.json")

    registry1 = PrincipalRegistry(path=path, owner_id="yampi")
    registry2 = PrincipalRegistry(path=path, owner_id="yampi")

    # El segundo registro no vuelve a crear owners: 1 solo principal.
    assert len(registry2.list()) == 1


def test_is_root_only_for_registered_roots(tmp_path):
    registry = PrincipalRegistry(
        path=str(tmp_path / "principals.json"),
        owner_id="yampi",
    )

    assert registry.is_root("yampi") is True
    assert registry.is_root("desconocido") is False
    assert registry.is_root(None) is False
    assert registry.is_root("") is False


def test_register_and_persist(tmp_path):
    path = str(tmp_path / "principals.json")

    registry = PrincipalRegistry(path=path, owner_id="yampi")
    registry.register(Principal("ilu", "ilu"))
    registry.register(Principal("huésped", "guest"))

    reloaded = PrincipalRegistry(path=path, owner_id="yampi")

    assert reloaded.get("ilu").principal_type == "ilu"
    assert reloaded.get("huésped").principal_type == "guest"
    assert reloaded.get("yampi").is_root is True


def test_owner_id_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ILU_OWNER_ID", "familia")

    registry = PrincipalRegistry(
        path=str(tmp_path / "principals.json"),
    )

    assert registry.owner_id == "familia"
    assert registry.owner().principal_id == "familia"


def test_to_dict_does_not_expose_secrets(tmp_path):
    principal = Principal("yampi", "owner")
    principal.public_key = "clave-publica-ejemplo"

    data = principal.to_dict()

    assert data["principal_id"] == "yampi"
    assert "secret" not in json_dumps(data)


def json_dumps(data):
    import json
    return json.dumps(data)


def test_roundtrip_from_dict_keeps_identity():
    original = Principal(
        "yampi",
        "owner",
        display_name="Owner",
        verification_strength="high",
    )

    restored = Principal.from_dict(original.to_dict())

    assert restored.principal_id == original.principal_id
    assert restored.is_root is True
    assert restored.verification_strength == "high"