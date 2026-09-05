"""
Autorizaciones RECORDADAS — "concede una vez y recuérdalo".

La infraestructura de grants ya era persistente; lo que faltaba era el
concepto de RECORDAR una autorización para que I.L.U. pueda volver a usar
una capacidad sin re-preguntar (y, cuando el owner lo pide, de forma
indefinida pero SIEMPRE revocable).

Reglas de seguridad preservadas:
  - "autoriza X" (sin más) sigue siendo de UN solo uso (menor privilegio).
  - "autoriza siempre X" / "recuerda que puedes X" → permiso recordado,
    indefinido pero revocable, jamás auto-concedido.
  - Resolver una solicitud con remember=True produce un grant DURABLE que
    SecurityGate reutiliza en la siguiente ejecución de la misma capacidad.
  - Ningún permiso recordado escapa a la revocación ni a la auditoría.
"""

import pytest

from app.security import SecurityGate
from app.core import ILUCore
from security.grant_store import GrantStore
from security.policy import Policy
from security.authorization_request import AuthorizationRequestStore


def _store(tmp_path, name="g.jsonl"):
    return GrantStore(path=str(tmp_path / name))


# ----------------------------------------------------------------------
# Autoridad: resolución recordada
# ----------------------------------------------------------------------

def test_resolve_remember_produces_durable_grant(tmp_path):
    from security.authority import Authority

    grants = _store(tmp_path)
    requests = AuthorizationRequestStore(
        path=str(tmp_path / "requests.jsonl")
    )
    authority = Authority(
        grant_store=grants,
        requests=requests,
        policy=Policy(),
    )

    req = requests.open(
        capability="write_file",
        reason="Necesita autorización para write_file",
        principal="owner",
        scope={"type": "tool", "tool": "write_file"},
    )

    result = authority.resolve_request(
        req.key, "granted", "owner", remember=True
    )

    assert result["success"] is True
    assert result["remembered"] is True

    grant = result["grant"]
    # Durable: NO es de un solo uso y expira según policy (o indefinido).
    assert grant.scope_type != "single_action"
    assert grant.max_uses is None


def test_resolve_remember_gate_reuses_grant(tmp_path):
    """La misma capacidad en la 2ª ejecución se auto-aprueba sin re-preguntar."""
    from security.authority import Authority

    grants = _store(tmp_path)
    requests = AuthorizationRequestStore(
        path=str(tmp_path / "requests.jsonl")
    )
    authority = Authority(
        grant_store=grants,
        requests=requests,
        policy=Policy(),
    )

    req = requests.open(
        capability="notify",
        reason="Necesita autorización para notify",
        principal="owner",
        scope={"type": "tool", "tool": "notify"},
    )
    authority.resolve_request(req.key, "granted", "owner", remember=True)

    gate = SecurityGate("assisted")
    first = gate.decide(
        "notify", "ask", mode="model", capability="notify",
        actor="ilu", grant_store=grants, policy=Policy(),
    )
    second = gate.decide(
        "notify", "ask", mode="model", capability="notify",
        actor="ilu", grant_store=grants, policy=Policy(),
    )

    # Ambas auto-aprueban por el mismo grant recordado.
    assert first["decision"] == "allow"
    assert second["decision"] == "allow"
    assert first["grant_id"] == second["grant_id"]


def test_resolve_indefinite_produces_non_expiring_grant(tmp_path):
    from security.authority import Authority

    grants = _store(tmp_path)
    requests = AuthorizationRequestStore(
        path=str(tmp_path / "requests.jsonl")
    )
    authority = Authority(
        grant_store=grants,
        requests=requests,
        policy=Policy(),
    )

    req = requests.open(
        capability="write_file",
        reason="Necesita autorización para write_file",
        principal="owner",
    )

    result = authority.resolve_request(
        req.key, "granted", "owner", indefinite=True
    )

    grant = result["grant"]
    assert grant.indefinite is True
    assert grant.expires_at is None
    assert grant.scope_type != "single_action"
    # Aunque sea indefinido, sigue siendo revocable y auditable.
    assert grant.status == "active"


def test_resolve_remembered_grant_still_revocable(tmp_path):
    from security.authority import Authority

    grants = _store(tmp_path)
    requests = AuthorizationRequestStore(
        path=str(tmp_path / "requests.jsonl")
    )
    authority = Authority(
        grant_store=grants,
        requests=requests,
        policy=Policy(),
    )

    req = requests.open(
        capability="notify",
        reason="Necesita autorización para notify",
        principal="owner",
    )
    result = authority.resolve_request(
        req.key, "granted", "owner", indefinite=True
    )

    revoked = authority.revoke(result["grant"].key, "owner")
    assert revoked.status == "revoked"

    # Revocado → el gate ya no lo usa.
    gate = SecurityGate("assisted")
    decision = gate.decide(
        "notify", "ask", mode="model", capability="notify",
        actor="ilu", grant_store=grants, policy=Policy(),
    )
    assert decision["decision"] == "ask"


# ----------------------------------------------------------------------
# Lenguaje natural (core)
# ----------------------------------------------------------------------

@pytest.fixture
def core(monkeypatch):
    """Core aislado con la clave del owner configurada (Bloque 14)."""
    monkeypatch.setenv("ILU_OWNER_SECRET", "240890")
    instance = ILUCore()
    instance._save_memory = lambda *args, **kwargs: None
    return instance


def test_nl_single_use_is_default(core):
    # Sin "siempre"/"recuerda": menor privilegio, UN solo uso.
    result = core._authority_command("autoriza write_file 240890")

    assert result["intent"] == "permission_granted"
    grant = core.grant_store.get(result["grant"]["grant_id"])
    assert grant.scope_type == "single_action"
    assert grant.max_uses == 1
    assert grant.indefinite is False


def test_nl_autoriza_siempre_is_remembered(core):
    result = core._authority_command("autoriza siempre write_file 240890")

    assert result["intent"] == "permission_granted"
    assert result["grant"]["indefinite"] is True

    grant = core.grant_store.get(result["grant"]["grant_id"])
    assert grant.indefinite is True
    assert grant.scope_type != "single_action"
    assert grant.status == "active"


def test_nl_recuerda_que_puedes_is_remembered(core):
    result = core._authority_command("recuerda que puedes notify 240890")

    assert result["intent"] == "permission_granted"
    grant = core.grant_store.get(result["grant"]["grant_id"])
    assert grant.indefinite is True
    assert grant.origin == "nl_owner_command_remembered"


def test_nl_remembered_grant_auto_approves_second_ask(core):
    core._authority_command("autoriza siempre notify 240890")

    gate = SecurityGate("assisted")
    decision = gate.decide(
        "notify", "ask", mode="model", capability="notify",
        actor="ilu", grant_store=core.grant_store, policy=core.policy,
    )
    assert decision["decision"] == "allow"


def test_nl_remembered_still_level_execution(core):
    # Nunca se delega autoridad por lenguaje natural, ni recordando.
    result = core._authority_command("autoriza siempre write_file 240890")
    assert result["grant"]["level"] == "execution"


def test_nl_recuerda_without_permission_target_ignored(core):
    # "recuerda" genérico no es un comando de autoridad.
    assert core._authority_command("recuerda comprar leche") is None
    assert core._authority_command("recuerda") is None


def test_nl_remembered_can_be_revoked(core):
    core._authority_command("autoriza siempre write_file 240890")
    result = core._authority_command("revoca write_file")

    assert result["intent"] == "permission_revoked"
    assert result["grant"]["status"] == "revoked"
