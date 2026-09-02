"""
Bloque 8 — Protocolos de emergencia.

I.L.U. NUNCA inventa protocolos: solo existen los definidos en policy y
solo una autoridad raíz los activa. Sin protocolo activo aplicable, la
capacidad NO se autoriza en automático.
"""

import time

from security.policy import Policy
from security.emergency import EmergencyRegistry


def make_policy_with_protocol(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(
        '{"emergency_protocols": ['
        '{"id": "emg_alerta", "capabilities": ["write_file", "notify"]}'
        "]}"
    )
    return Policy(path=str(path))


def test_no_protocols_defined_by_default():
    policy = Policy()
    assert policy.emergency_protocols() == []


def test_activate_unknown_protocol_fails(tmp_path):
    policy = make_policy_with_protocol(tmp_path)
    registry = EmergencyRegistry(
        policy=policy,
        path=str(tmp_path / "emergency.json"),
    )

    assert (
        registry.activate("emg_inexistente", "owner", narrative="test")
        is None
    )
    assert registry.list_active() == {}


def test_activate_defined_protocol(tmp_path):
    policy = make_policy_with_protocol(tmp_path)
    registry = EmergencyRegistry(
        policy=policy,
        path=str(tmp_path / "emergency.json"),
    )

    activation = registry.activate(
        "emg_alerta",
        "owner",
        narrative="alerta familiar",
    )

    assert activation["activated_by"] == "owner"
    assert "alerta familiar" in activation["narrative"]
    assert "emg_alerta" in registry.active


def test_covers_returns_protocol_for_capability(tmp_path):
    policy = make_policy_with_protocol(tmp_path)
    registry = EmergencyRegistry(
        policy=policy,
        path=str(tmp_path / "emergency.json"),
    )

    registry.activate("emg_alerta", "owner")

    assert registry.covers("write_file") is not None
    assert registry.covers("notify") is not None
    assert registry.covers("read_file") is None


def test_inactive_protocol_does_not_cover(tmp_path):
    policy = make_policy_with_protocol(tmp_path)
    registry = EmergencyRegistry(
        policy=policy,
        path=str(tmp_path / "emergency.json"),
    )

    # Definido pero NO activo -> no cubre.
    assert registry.covers("write_file") is None


def test_deactivate_removes_coverage(tmp_path):
    policy = make_policy_with_protocol(tmp_path)
    registry = EmergencyRegistry(
        policy=policy,
        path=str(tmp_path / "emergency.json"),
    )

    registry.activate("emg_alerta", "owner")
    assert registry.covers("write_file") is not None

    registry.deactivate("emg_alerta", "owner")
    assert registry.covers("write_file") is None


def test_persistence(tmp_path):
    path = str(tmp_path / "emergency.json")
    policy = make_policy_with_protocol(tmp_path)

    registry = EmergencyRegistry(policy=policy, path=path)
    registry.activate("emg_alerta", "owner")

    reloaded = EmergencyRegistry(policy=policy, path=path)

    assert "emg_alerta" in reloaded.active
    assert reloaded.covers("write_file") is not None


def test_new_protocol_id_unique():
    registry = EmergencyRegistry()
    a = registry.new_protocol_id()
    b = registry.new_protocol_id()

    assert a != b
    assert a.startswith("emg_")