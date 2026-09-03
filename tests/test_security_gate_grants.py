"""
Bloque 8 — SecurityGate como ÚNICA puerta de ejecución.

Verifica dos cosas a la vez:
1) RETROCOMPATIBILIDAD: sin grant_store/policy/emergency inyectados, el
   comportamiento es idéntico al de los Bloques 1-7.
2) La nueva capa: grants activos auto-aprueban una tool "ask" en autonomía
   asistida/autónoma, las acciones prohibidas se niegan siempre, los
   protocolos de emergencia autorizan, y el spoofing bloquea.
"""

from app.security import SecurityGate
from security.grant_store import GrantStore
from security.policy import Policy
from security.emergency import EmergencyRegistry
from security.spoofing import SpoofingGuard
from security.grant import Grant


def test_retrocompat_ask_requires_human_in_all_levels():
    # Sin grant_store: comportamiento previo que ya fijaban los tests.
    for level in ("manual", "assisted", "autonomous"):
        gate = SecurityGate(autonomy_level=level)

        decision = gate.decide("write_file", "ask", mode="model")

        assert decision["decision"] == "ask"
        assert decision["reason"] == "authorization_required"


def test_retrocompat_safe_allowed():
    gate = SecurityGate(autonomy_level="assisted")

    decision = gate.decide("system_time", "safe", mode="direct")

    assert decision["decision"] == "allow"


def test_retrocompat_blocked_denied():
    gate = SecurityGate(autonomy_level="autonomous")

    decision = gate.decide("peligrosa", "blocked", mode="model")

    assert decision["decision"] == "deny"
    assert decision["reason"] == "tool_blocked"


def test_grant_auto_approves_ask_in_autonomous(tmp_path):
    gate = SecurityGate(autonomy_level="autonomous")
    store = GrantStore(path=str(tmp_path / "grants.jsonl"))
    policy = Policy()

    store.add(Grant(
        capability="write_file",
        grantor="owner",
        expires_at="2099-01-01T00:00:00Z",
    ))

    decision = gate.decide(
        "write_file",
        "ask",
        mode="model",
        capability="write_file",
        actor="ilu",
        context={},
        grant_store=store,
        policy=policy,
    )

    assert decision["decision"] == "allow"
    assert decision["reason"] == "granted"


def test_grant_auto_approves_ask_in_assisted(tmp_path):
    gate = SecurityGate(autonomy_level="assisted")
    store = GrantStore(path=str(tmp_path / "grants.jsonl"))
    policy = Policy()

    store.add(Grant(
        capability="write_file",
        grantor="owner",
        expires_at="2099-01-01T00:00:00Z",
    ))

    decision = gate.decide(
        "write_file",
        "ask",
        mode="model",
        capability="write_file",
        actor="ilu",
        context={},
        grant_store=store,
        policy=policy,
    )

    assert decision["decision"] == "allow"
    assert decision["reason"] == "granted"


def test_manual_never_auto_approves_even_with_grant(tmp_path):
    # En manual la decisión no se delega a grants: siempre pregunta.
    gate = SecurityGate(autonomy_level="manual")
    store = GrantStore(path=str(tmp_path / "grants.jsonl"))
    policy = Policy()

    store.add(Grant(
        capability="write_file",
        grantor="owner",
        expires_at="2099-01-01T00:00:00Z",
    ))

    decision = gate.decide(
        "write_file",
        "ask",
        mode="model",
        capability="write_file",
        actor="ilu",
        context={},
        grant_store=store,
        policy=policy,
    )

    assert decision["decision"] == "ask"


def test_consumed_grant_no_longer_auto_approves(tmp_path):
    gate = SecurityGate(autonomy_level="autonomous")
    store = GrantStore(path=str(tmp_path / "grants.jsonl"))
    policy = Policy()

    store.add(Grant(
        capability="write_file",
        grantor="owner",
        max_uses=1,
    ))

    first = gate.decide(
        "write_file", "ask", mode="model",
        capability="write_file", actor="ilu", context={},
        grant_store=store, policy=policy,
    )
    assert first["decision"] == "allow"

    second = gate.decide(
        "write_file", "ask", mode="model",
        capability="write_file", actor="ilu", context={},
        grant_store=store, policy=policy,
    )
    assert second["decision"] == "ask"


def test_prohibited_denied_even_if_safe_permission(tmp_path):
    gate = SecurityGate(autonomy_level="autonomous")
    policy = Policy()

    decision = gate.decide(
        "shell",
        "safe",  # aunque el registro dijera "safe"
        mode="model",
        capability="shell",
        actor="ilu",
        context={},
        policy=policy,
    )

    assert decision["decision"] == "deny"
    assert decision["reason"] == "prohibited_action"


def test_emergency_protocol_authorizes(tmp_path):
    gate = SecurityGate(autonomy_level="manual")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        '{"emergency_protocols": ['
        '{"id": "emg_a", "capabilities": ["write_file"]}]}'
    )
    policy = Policy(path=str(policy_path))
    emergency = EmergencyRegistry(
        policy=policy,
        path=str(tmp_path / "emergency.json"),
    )
    emergency.activate("emg_a", "owner")

    decision = gate.decide(
        "write_file",
        "ask",
        mode="model",
        capability="write_file",
        actor="ilu",
        context={},
        policy=policy,
        emergency=emergency,
    )

    assert decision["decision"] == "allow"
    assert decision["reason"] == "emergency_protocol"


def test_spoofing_blocks_after_failures(tmp_path):
    gate = SecurityGate(autonomy_level="autonomous")
    policy = Policy()
    store = GrantStore(path=str(tmp_path / "grants.jsonl"))
    spoofing = SpoofingGuard(threshold=2, window_seconds=300)

    # Dos fallos de verificación de identidad en write_file (high).
    gate.decide(
        "write_file", "ask", mode="model",
        capability="write_file", actor="intruso",
        context={}, grant_store=store, policy=policy,
        spoofing=spoofing, verification_ok=False,
    )
    decision = gate.decide(
        "write_file", "ask", mode="model",
        capability="write_file", actor="intruso",
        context={}, grant_store=store, policy=policy,
        spoofing=spoofing, verification_ok=False,
    )

    assert decision["decision"] == "deny"
    assert decision["reason"] == "identity_suspected"


def test_spoofing_does_not_deny_below_threshold(tmp_path):
    gate = SecurityGate(autonomy_level="autonomous")
    policy = Policy()
    store = GrantStore(path=str(tmp_path / "grants.jsonl"))
    store.add(Grant(
        capability="write_file",
        grantor="owner",
        grantee="intruso",   # el grant es del actor que se está evaluando
        expires_at="2099-01-01T00:00:00Z",
    ))
    spoofing = SpoofingGuard(threshold=5, window_seconds=300)

    decision = gate.decide(
        "write_file", "ask", mode="model",
        capability="write_file", actor="intruso",
        context={}, grant_store=store, policy=policy,
        spoofing=spoofing, verification_ok=False,
    )

    # 1 fallo < umbral 5: el spoofing NO dispara; el grant permite.
    # (Un grant SOLO cubre a su grantee: actor y grantee coinciden aquí.)
    assert decision["decision"] == "allow"