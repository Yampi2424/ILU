"""
Bloque 8 — Sub-agente hereda permisos, NUNCA eleva autoridad.

El sub-agente consulta los grants del padre (vía SecurityGate con
grant_store/policy/emergency inyectados) y hereda el nivel de autonomía
del padre. No tiene acceso a Authority: no puede autoconcederse permisos,
cambiar la autonomía, registrar dispositivos ni activar emergencias.
"""

import inspect

from app.audit import AuditLog
from app.security import SecurityGate
from app.subagent import SubAgent
from security.grant import Grant
from security.grant_store import GrantStore
from security.policy import Policy
from tools import create_tool_manager


def make_subagent(tmp_path, autonomy="assisted", grants=None):
    """Construye un SubAgent aislado con stores en tmp_path."""
    gate = SecurityGate(autonomy_level=autonomy)

    grant_store = GrantStore(path=str(tmp_path / "grants.jsonl"))
    for g in grants or []:
        grant_store.add(g)

    policy = Policy()

    sub = SubAgent(
        provider=None,
        tools=create_tool_manager(),
        security=gate,
        audit=AuditLog(path=str(tmp_path / "audit.jsonl")),
        grant_store=grant_store,
        policy=policy,
    )

    return sub


def test_subagent_inherits_grants(tmp_path):
    grant = Grant(
        capability="write_file",
        grantor="owner",
        expires_at="2099-01-01T00:00:00Z",
    )
    sub = make_subagent(tmp_path, autonomy="autonomous", grants=[grant])

    # Con el grant heredado del padre, la compuerta auto-aprueba en
    # modo autónomo.
    decision = sub.security.decide(
        "write_file", "ask", mode="model",
        capability="write_file", actor="ilu", context={},
        grant_store=sub.grant_store, policy=sub.policy,
    )

    assert decision["decision"] == "allow"


def test_subagent_without_grant_stops_at_gate(tmp_path):
    sub = make_subagent(tmp_path, autonomy="autonomous")

    # Sin grant activo: la tool se detiene en la compuerta, incluso en
    # modo autónomo.
    decision = sub.security.decide(
        "write_file", "ask", mode="model",
        capability="write_file", actor="ilu", context={},
        grant_store=sub.grant_store, policy=sub.policy,
    )

    assert decision["decision"] == "ask"


def test_subagent_never_has_authority_reference():
    # El constructor NO acepta Authority; no hay atributo authority.
    sig = inspect.signature(SubAgent.__init__)
    params = list(sig.parameters.keys())

    assert "authority" not in params
    # Solo estos son los deps de seguridad inyectados:
    assert "grant_store" in params
    assert "policy" in params
    assert "emergency" in params


def test_subagent_cannot_grant_to_self(tmp_path):
    sub = make_subagent(tmp_path)

    # No hay método grant en SubAgent; no hay authority inyectado.
    assert not hasattr(sub, "authority")
    assert not hasattr(sub, "grant")
    assert not hasattr(sub, "revoke")


def test_subagent_cannot_set_autonomy(tmp_path):
    sub = make_subagent(tmp_path)

    assert not hasattr(sub, "set_autonomy")
    assert not hasattr(sub, "activate_emergency")


def test_subagent_cannot_register_device(tmp_path):
    sub = make_subagent(tmp_path)

    assert not hasattr(sub, "register_device")
    assert not hasattr(sub, "revoke_device")


def test_subagent_manual_mode_still_asks_even_with_grant(tmp_path):
    grant = Grant(
        capability="write_file",
        grantor="owner",
        expires_at="2099-01-01T00:00:00Z",
    )
    sub = make_subagent(tmp_path, autonomy="manual", grants=[grant])

    decision = sub.security.decide(
        "write_file", "ask", mode="model",
        capability="write_file", actor="ilu", context={},
        grant_store=sub.grant_store, policy=sub.policy,
    )

    # En manual, el grant NO auto-aprueba: la autonomía manda.
    assert decision["decision"] == "ask"


def test_subagent_consumes_single_use_grant(tmp_path):
    grant = Grant(
        capability="write_file",
        grantor="owner",
        max_uses=1,
    )
    sub = make_subagent(tmp_path, autonomy="autonomous", grants=[grant])

    decision1 = sub.security.decide(
        "write_file", "ask", mode="model",
        capability="write_file", actor="ilu", context={},
        grant_store=sub.grant_store, policy=sub.policy,
    )
    decision2 = sub.security.decide(
        "write_file", "ask", mode="model",
        capability="write_file", actor="ilu", context={},
        grant_store=sub.grant_store, policy=sub.policy,
    )

    assert decision1["decision"] == "allow"
    assert decision2["decision"] == "ask"


def test_subagent_shares_same_grant_store_as_parent(tmp_path):
    """El grant consumido por el sub-agente ya no está disponible para el padre."""
    grant_store = GrantStore(path=str(tmp_path / "grants.jsonl"))
    grant_store.add(Grant(
        capability="write_file",
        grantor="owner",
        max_uses=1,
    ))

    gate_parent = SecurityGate(autonomy_level="autonomous")
    gate_sub = SecurityGate(autonomy_level="autonomous")

    # Padre consume
    decision_parent = gate_parent.decide(
        "write_file", "ask", mode="model",
        capability="write_file", actor="ilu", context={},
        grant_store=grant_store, policy=Policy(),
    )

    # Sub-agente consulta el MISMO store -> ya consumido
    decision_sub = gate_sub.decide(
        "write_file", "ask", mode="model",
        capability="write_file", actor="ilu", context={},
        grant_store=grant_store, policy=Policy(),
    )

    assert decision_parent["decision"] == "allow"
    assert decision_sub["decision"] == "ask"