from app.security import SecurityGate


def test_safe_direct_allowed():
    gate = SecurityGate(autonomy_level="assisted")

    decision = gate.decide("system_time", "safe", mode="direct")

    assert decision["decision"] == "allow"
    assert decision["reason"] == "allowed_safe_tool"


def test_safe_model_allowed_in_assisted():
    gate = SecurityGate(autonomy_level="assisted")

    decision = gate.decide("system_time", "safe", mode="model")

    assert decision["decision"] == "allow"


def test_safe_model_proposal_in_manual():
    gate = SecurityGate(autonomy_level="manual")

    decision = gate.decide("system_time", "safe", mode="model")

    assert decision["decision"] == "ask"
    assert decision["reason"] == "manual_mode_proposal"


def test_ask_requires_human_in_all_levels():
    for level in ("manual", "assisted", "autonomous"):
        gate = SecurityGate(autonomy_level=level)

        decision = gate.decide("peligrosa", "ask", mode="model")

        assert decision["decision"] == "ask"
        assert decision["reason"] == "authorization_required"


def test_blocked_denied():
    gate = SecurityGate()

    decision = gate.decide("shell", "blocked", mode="direct")

    assert decision["decision"] == "deny"
    assert decision["reason"] == "tool_blocked"


def test_unknown_permission_fails_closed():
    gate = SecurityGate()

    decision = gate.decide("raro", "desconocido", mode="direct")

    assert decision["decision"] == "deny"
    assert decision["reason"] == "unknown_permission"


def test_invalid_autonomy_level_defaults_to_assisted():
    gate = SecurityGate(autonomy_level="super")

    assert gate.autonomy_level == "assisted"


def test_autonomy_level_from_env(monkeypatch):
    monkeypatch.setenv("ILU_AUTONOMY", "autonomous")
    gate = SecurityGate()
    assert gate.autonomy_level == "autonomous"

    monkeypatch.setenv("ILU_AUTONOMY", "manual")
    gate = SecurityGate()
    assert gate.autonomy_level == "manual"


def test_default_autonomy_is_assisted(monkeypatch):
    monkeypatch.delenv("ILU_AUTONOMY", raising=False)
    gate = SecurityGate()
    assert gate.autonomy_level == "assisted"