"""
Bloque 8 — Policy: reglas separadas del código (security/policy.json).

Verifica las acciones prohibidas, la sensibilidad por capacidad, los
defaults de grants, los protocolos previamente autorizados y las reglas
de auto-revocación.
"""

from security.policy import Policy, DEFAULT_POLICY


def test_default_policy_has_prohibited_actions():
    assert "shell" in DEFAULT_POLICY["prohibited"]
    assert "grant_self" in DEFAULT_POLICY["prohibited"]
    assert "elevate_autonomy" in DEFAULT_POLICY["prohibited"]
    assert "modify_policy" in DEFAULT_POLICY["prohibited"]


def test_is_prohibited():
    policy = Policy()

    assert policy.is_prohibited("shell") is True
    assert policy.is_prohibited("kill_process") is True
    assert policy.is_prohibited("grant_self") is True
    assert policy.is_prohibited("write_file") is False


def test_sensitivity_mapping():
    policy = Policy()

    assert policy.sensitivity("write_file") == "high"
    assert policy.sensitivity("read_file") == "low"
    # Desconocida -> normal (fail-closed razonable pero no exagerado).
    assert policy.sensitivity("capacidad_rara") == "normal"


def test_default_duration():
    policy = Policy()
    assert policy.default_duration() == "1h"


def test_emergency_protocol_unknown():
    policy = Policy()
    assert policy.emergency_protocol("emg_abc") is None


def test_auto_revoke_rules():
    policy = Policy()

    rules = policy.auto_revoke_for("write_file")

    assert len(rules) >= 1
    assert "risk_flag" in [rule.get("trigger") for rule in rules]

    assert policy.auto_revoke_for("system_time") == []


def test_fundamental_safety():
    policy = Policy()

    assert "abort_immediate_hazard" in policy.data.get(
        "fundamental_safety", []
    )


def test_loads_from_real_policy_file():
    # El path por defecto apunta a security/policy.json (trackeado).
    policy = Policy()

    assert policy.data.get("version") == 1
    assert isinstance(policy.data.get("prohibited"), list)
    assert isinstance(policy.data.get("emergency_protocols"), list)


def test_corrupt_policy_falls_back_to_default(tmp_path):
    # Archivo corrupto -> fallback a la política por defecto (failsafe).
    path = tmp_path / "policy.json"
    path.write_text("esto no es json")

    policy = Policy(path=str(path))

    assert policy.is_prohibited("shell") is True


def test_custom_policy_file_updates_defaults(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(
        '{"emergency_protocols": [{"id": "emg_x", '
        '"capabilities": ["notify"]}]}'
    )

    policy = Policy(path=str(path))

    protocol = policy.emergency_protocol("emg_x")
    assert protocol is not None
    assert "notify" in protocol.get("capabilities", [])
    # El resto de reglas por defecto sigue activo.
    assert policy.is_prohibited("shell") is True