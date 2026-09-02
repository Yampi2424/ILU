"""
Bloque 8 — Authority: la ÚNICA capa que emite/revoca permisos.

Verifica: gating por autoridad raíz, rechazo de acciones prohibidas,
no-permanencia por defecto, single_action consume en 1 uso, revocación
inmediata, autonomía gobernada, auto-revocación por riesgo (regla
predefinida) y resolución de solicitudes.
"""

import pytest

from app.audit import AuditLog
from app.security import SecurityGate
from security.authority import Authority, parse_duration
from security.grant_store import GrantStore
from security.principal import PrincipalRegistry
from security.policy import Policy
from security.emergency import EmergencyRegistry
from security.device import DeviceRegistry
from security.authorization_request import AuthorizationRequestStore


def make_authority(tmp_path, owner_id="owner"):
    policy = Policy()
    gate = SecurityGate()

    authority = Authority(
        grant_store=GrantStore(path=str(tmp_path / "grants.jsonl")),
        principals=PrincipalRegistry(
            path=str(tmp_path / "principals.json"),
            owner_id=owner_id,
        ),
        audit=AuditLog(path=str(tmp_path / "audit.jsonl")),
        emergency=EmergencyRegistry(
            policy=policy,
            path=str(tmp_path / "emergency.json"),
        ),
        devices=DeviceRegistry(path=str(tmp_path / "devices.json")),
        policy=policy,
        gate=gate,
        requests=AuthorizationRequestStore(
            path=str(tmp_path / "requests.jsonl")
        ),
    )

    return authority


def test_owner_can_grant(tmp_path):
    authority = make_authority(tmp_path)

    grant = authority.grant("write_file", actor="owner")

    assert grant.status == "active"
    assert grant.level == "execution"
    assert grant.grantor == "owner"
    assert grant.grantee == "ilu"
    # Nadie decide: la duración por defecto de policy da un vencimiento.
    assert grant.expires_at is not None


def test_non_root_cannot_grant(tmp_path):
    authority = make_authority(tmp_path)

    with pytest.raises(PermissionError) as info:
        authority.grant("write_file", actor="desconocido")

    assert "root" in str(info.value).lower() or "raiz" in str(info.value)


def test_missing_actor_rejected(tmp_path):
    authority = make_authority(tmp_path)

    with pytest.raises(PermissionError):
        authority.grant("write_file", actor=None)


def test_prohibited_capability_never_granted(tmp_path):
    authority = make_authority(tmp_path)

    for prohibited in ("shell", "grant_self", "elevate_autonomy"):
        with pytest.raises(ValueError) as info:
            authority.grant(prohibited, actor="owner")

        assert str(info.value) == "capability_prohibited"


def test_single_action_scope_consumes_in_one_use(tmp_path):
    authority = make_authority(tmp_path)

    grant = authority.grant(
        "write_file",
        actor="owner",
        scope_type="single_action",
    )

    # El alcance "single_action" vale por UN solo uso.
    assert grant.max_uses == 1

    first = authority.grant_store.find_active("write_file")
    assert first is not None and first.key == grant.key

    assert authority.grant_store.find_active("write_file") is None


def test_revoke_is_immediate_and_root_only(tmp_path):
    authority = make_authority(tmp_path)

    grant = authority.grant("write_file", actor="owner")

    with pytest.raises(PermissionError):
        authority.revoke(grant.key, actor="desconocido")

    revoked = authority.revoke(grant.key, actor="owner", reason="basta")

    assert revoked.status == "revoked"
    assert authority.grant_store.find_active("write_file") is None


def test_auto_revoke_risk_revokes_active(tmp_path):
    authority = make_authority(tmp_path)

    authority.grant("write_file", actor="owner")
    authority.grant("write_file", actor="owner")

    revoked = authority.auto_revoke_risk(
        "write_file",
        "regla predefinida: riesgo grave",
    )

    assert len(revoked) == 2
    assert authority.grant_store.find_active("write_file") is None


def test_set_autonomy_root_only(tmp_path):
    authority = make_authority(tmp_path)

    with pytest.raises(PermissionError):
        authority.set_autonomy("autonomous", actor="desconocido")

    change = authority.set_autonomy("autonomous", actor="owner")

    assert change == {"from": "assisted", "to": "autonomous"}
    assert authority.gate.autonomy_level == "autonomous"


def test_set_autonomy_invalid_level(tmp_path):
    authority = make_authority(tmp_path)

    with pytest.raises(ValueError):
        authority.set_autonomy("dios", actor="owner")


def test_custom_owner_id_is_the_only_root(tmp_path):
    authority = make_authority(tmp_path, owner_id="familia")

    authority.grant("write_file", actor="familia")

    with pytest.raises(PermissionError):
        authority.grant("write_file", actor="owner")


def test_resolve_request_granted(tmp_path):
    authority = make_authority(tmp_path)

    request = authority._requests().open(
        capability="write_file",
        reason="necesito escribir el informe",
        principal="owner",
        task_id="tarea_1",
    )

    result = authority.resolve_request(
        request.key,
        "granted",
        actor="owner",
        scope={"type": "single_action"},
    )

    assert result["success"] is True
    assert result["grant"] is not None

    stored = authority._requests().get(request.key)
    assert stored.status == "granted"
    assert stored.grant_id == result["grant"].key


def test_resolve_request_denied(tmp_path):
    authority = make_authority(tmp_path)

    request = authority._requests().open(
        capability="write_file",
        reason="pedido",
    )

    result = authority.resolve_request(
        request.key,
        "denied",
        actor="owner",
    )

    assert result["success"] is True
    assert result["grant"] is None

    stored = authority._requests().get(request.key)
    assert stored.status == "denied"


def test_resolve_request_non_root_rejected(tmp_path):
    authority = make_authority(tmp_path)

    request = authority._requests().open(
        capability="write_file",
        reason="pedido",
    )

    with pytest.raises(PermissionError):
        authority.resolve_request(request.key, "granted", actor="otro")


def test_resolve_twice_not_allowed(tmp_path):
    authority = make_authority(tmp_path)

    request = authority._requests().open(capability="write_file")

    authority.resolve_request(request.key, "granted", actor="owner")

    second = authority.resolve_request(
        request.key,
        "granted",
        actor="owner",
    )

    assert second["success"] is False


def test_register_and_revoke_device(tmp_path):
    authority = make_authority(tmp_path)

    record = authority.register_device(
        "phone_yampi",
        actor="owner",
        display_name="Celular",
    )

    assert record["status"] == "active"
    assert "secret" not in record

    revoked = authority.revoke_device(
        "phone_yampi",
        actor="owner",
        reason="robo",
    )

    assert revoked["status"] == "revoked"


def test_activate_unknown_emergency_fails(tmp_path):
    authority = make_authority(tmp_path)

    # policy por defecto no define protocolos: no se puede activar.
    assert authority.activate_emergency("emg_nonexistent", actor="owner") is None


def test_parse_duration():
    assert parse_duration(None) is None

    hour = parse_duration("1h")
    assert hour is not None and hour.minute == hour.minute  # simplemente válido

    assert parse_duration("xyz") is None
    assert parse_duration("10 días") is None