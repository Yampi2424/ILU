"""
Bloque 8 — Comandos de autoridad por lenguaje natural.

I.L.U. actúa como interfaz de voz/texto hacia Authority, que exige un
principal raíz (el OWNER) para conceder/revocar permisos y cambiar la
autonomía. El nivel concedido es SIEMPRE "execution": nunca se
autoconcede ni se delega autoridad por una frase.
"""

import pytest

from app.core import ILUCore


@pytest.fixture
def core(monkeypatch):
    """ILUCore aislado; _save_memory es no-op para no tocar memoria real."""
    instance = ILUCore()
    instance._save_memory = lambda *args, **kwargs: None
    return instance


def test_grant_by_owner(core):
    result = core._authority_command("autoriza write_file")

    assert result["success"] is True
    assert result["intent"] == "permission_granted"
    assert result["grant"]["capability"] == "write_file"
    # Nunca se delega autoridad por lenguaje natural.
    assert result["grant"]["level"] == "execution"

    stored = core.grant_store.get(result["grant"]["grant_id"])
    assert stored is not None
    assert stored.status == "active"
    assert stored.grantor == core.settings.owner_id


def test_grant_scope_single_use_by_default(core):
    result = core._authority_command("autoriza notify")

    assert result["intent"] == "permission_granted"
    stored = core.grant_store.get(result["grant"]["grant_id"])

    # Alcance por defecto: una sola acción, UN solo uso.
    assert stored.scope_type == "single_action"
    assert stored.max_uses == 1


def test_prohibited_capability_rejected(core):
    result = core._authority_command("autoriza shell")

    assert result["intent"] == "permission_error"
    assert result["error"] == "capability_prohibited"

    # Nada se persistió.
    assert core.grant_store.list(limit=50) == []


def test_note_y_without_target_ignored(core):
    # Sin capacidad objetivo, la frase no es un comando de autoridad.
    assert core._authority_command("autoriza") is None


def test_revoke_by_owner(core):
    core._authority_command("autoriza write_file")
    result = core._authority_command("revoca write_file")

    assert result["intent"] == "permission_revoked"
    assert result["grant"]["status"] == "revoked"

    stored = core.grant_store.get(result["grant"]["grant_id"])
    assert stored.status == "revoked"


def test_revoke_nonexistent(core):
    result = core._authority_command("revoca no_existe")

    assert result["intent"] == "permission_revoked"
    assert result["grant"] is None


def test_status_empty(core):
    result = core._authority_command("estado de permisos")

    assert result["intent"] == "permission_status"
    assert result["grants"] == []


def test_status_lists_grants(core):
    core._authority_command("autoriza write_file")
    core._authority_command("autoriza notify")

    result = core._authority_command("qué permisos hay")

    assert result["intent"] == "permission_status"
    caps = {g["capability"] for g in result["grants"]}
    assert caps == {"write_file", "notify"}


def test_change_autonomy_to_autonomous(core):
    result = core._authority_command("cambia la autonomía a autónomo")

    assert result["intent"] == "autonomy_change"
    assert result["autonomy"]["to"] == "autonomous"
    assert core.security.autonomy_level == "autonomous"


def test_change_autonomy_spanish_variants(core):
    core.security.autonomy_level = "manual"

    result = core._authority_command("cambia la autonomia a asistida")
    assert result["intent"] == "autonomy_change"
    assert result["autonomy"]["to"] == "assisted"

    result = core._authority_command("autonomia a autónoma")
    assert result["intent"] == "autonomy_change"
    assert result["autonomy"]["to"] == "autonomous"


def test_change_autonomy_invalid_level(core):
    result = core._authority_command("cambia la autonomía a total")

    assert result["intent"] == "permission_error"
    assert result["error"] == "invalid_autonomy_level"
    # Sin cambio.
    assert core.security.autonomy_level == "assisted"


def test_non_root_cannot_grant(core):
    # El principal raíz es el owner ("owner" por defecto de ILU_OWNER_ID).
    # Si el core habla en nombre de un id que NO es raíz, Authority
    # rechaza la orden con PermissionError.
    core.settings.owner_id = "cuenta_no_raiz"

    result = core._authority_command("autoriza write_file")

    assert result["intent"] == "permission_error"
    assert result["error"] == "no_autoridad_raiz"


def test_non_root_cannot_change_autonomy(core):
    core.settings.owner_id = "cuenta_no_raiz"

    result = core._authority_command("cambia la autonomía a autónomo")

    assert result["intent"] == "permission_error"
    assert result["error"] == "no_autoridad_raiz"


def test_plain_message_not_authority(core):
    assert core._authority_command("hola, ¿cómo estás?") is None
    assert core._authority_command("escribe un poema") is None