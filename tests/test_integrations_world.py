"""
Bloque 13 — Integraciones reales gateadas (IntegrationManager).

Verifica que run_command / open_app / media_control se ejecutan SOLO con
grant activo para la capacidad (dial N.º 1) y que el CommandPolicy limita
QUÉ exacto se ejecuta (dial N.º 2). Sin grant -> authorization=ask.
"""

import json

from app.integrations import IntegrationManager
from app.audit import AuditLog
from security.command_policy import CommandPolicy
from security.grant_store import GrantStore
from security.principal import PrincipalRegistry
from security.policy import Policy
from security.emergency import EmergencyRegistry
from security.device import DeviceRegistry
from security.authority import Authority
from security.authorization_request import AuthorizationRequestStore


def _write_world_policy(tmp_path):
    path = tmp_path / "run_commands.json"
    data = {
        "version": 1,
        "allowlist": ["ls", "pwd", "whoami", "date", "uname", "hostname", "echo"],
        "apps": ["firefox", "brave", "code"],
        "media": ["playerctl"],
        "deny_substrings": [";", "&&", "||", "|", ">", "<", "$", "`", ".."],
        "default_timeout": 11,
        "max_output_bytes": 64,
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def _make_integration(tmp_path, grant_store):
    integration = IntegrationManager(
        workspace=str(tmp_path / "workspace"),
        grant_store=grant_store,
    )
    # La política de comandos del TEST (no la del repo): incluye 'echo'
    # y un max_output pequeño para verificar el truncado.
    integration.command_policy = CommandPolicy(
        _write_world_policy(tmp_path)
    )
    return integration


def _make_authority(tmp_path):
    policy = Policy()

    authority = Authority(
        grant_store=GrantStore(path=str(tmp_path / "grants.jsonl")),
        principals=PrincipalRegistry(
            path=str(tmp_path / "principals.json"),
            owner_id="owner",
        ),
        audit=AuditLog(path=str(tmp_path / "audit.jsonl")),
        emergency=EmergencyRegistry(
            policy=policy,
            path=str(tmp_path / "emergency.json"),
        ),
        devices=DeviceRegistry(path=str(tmp_path / "devices.json")),
        policy=policy,
        requests=AuthorizationRequestStore(
            path=str(tmp_path / "requests.jsonl")
        ),
    )

    return authority


def test_run_command_sin_grant_devuelve_ask(tmp_path):
    authority = _make_authority(tmp_path)

    integration = _make_integration(tmp_path, authority.grant_store)

    result = integration.execute("run_command", actor="ilu", command="whoami")

    assert result["success"] is False
    assert result["error"] == "authorization_required"
    assert result["authorization"] == "ask"


def test_run_command_con_grant_ejecuta_y_sale_por_allowlist(tmp_path):
    authority = _make_authority(tmp_path)
    authority.grant(
        "run_command",
        actor="owner",
        grantee="ilu",
        scope_type="duration",
        duration="1h",
    )

    integration = _make_integration(tmp_path, authority.grant_store)

    result = integration.execute(
        "run_command",
        actor="ilu",
        command="echo hola mundo",
    )

    assert result["success"] is True
    assert result["exit_code"] == 0
    assert "hola mundo" in (result["stdout"] or "")


def test_run_command_rechaza_fuera_de_allowlist_con_y_sin_grant(tmp_path):
    authority = _make_authority(tmp_path)
    authority.grant(
        "run_command",
        actor="owner",
        grantee="ilu",
        scope_type="duration",
        duration="1h",
    )

    integration = _make_integration(tmp_path, authority.grant_store)

    result = integration.execute(
        "run_command", actor="ilu", command="rm -rf /",
    )

    assert result["success"] is False
    assert result["error"] == "command_not_allowlisted"


def test_run_command_rechaza_metachars_con_grant(tmp_path):
    authority = _make_authority(tmp_path)
    authority.grant(
        "run_command",
        actor="owner",
        grantee="ilu",
        scope_type="duration",
        duration="1h",
    )

    integration = _make_integration(tmp_path, authority.grant_store)

    result = integration.execute(
        "run_command", actor="ilu", command="ls | grep x",
    )

    assert result["success"] is False
    assert result["error"] == "command_token_rejected"


def test_run_command_trunca_salida(tmp_path):
    authority = _make_authority(tmp_path)
    authority.grant(
        "run_command",
        actor="owner",
        grantee="ilu",
        scope_type="duration",
        duration="1h",
    )

    integration = _make_integration(tmp_path, authority.grant_store)

    result = integration.execute(
        "run_command", actor="ilu",
        command="echo " + ("x" * 200),
    )

    assert result["success"] is True
    assert len(result["stdout"]) <= 64
    assert result["truncated"] is True


def test_pre_authorized_salta_el_check_local(tmp_path):
    authority = _make_authority(tmp_path)

    # Grant de UN solo uso: si execute() lo consumiera dos veces (compuerta
    # + integración) fallaría. pre_authorized=True golpea solo la compuerta.
    authority.grant(
        "run_command",
        actor="owner",
        grantee="ilu",
        scope_type="single_action",
    )

    integration = _make_integration(tmp_path, authority.grant_store)

    result = integration.execute(
        "run_command",
        actor="ilu",
        pre_authorized=True,
        command="whoami",
    )

    assert result["success"] is True

    # El grant sigue ACTIVO: no se consumió dos veces (nadie lo gastó).
    active = authority.grant_store.list(
        capability="run_command", status="active",
    )
    assert len(active) == 1


def test_open_app_con_grant_pero_app_no_instalada_es_honesto(tmp_path):
    authority = _make_authority(tmp_path)
    authority.grant(
        "open_app",
        actor="owner",
        grantee="ilu",
        scope_type="duration",
        duration="1h",
    )

    integration = _make_integration(tmp_path, authority.grant_store)

    # "firefox" está en la allowlist pero es improbable que exista en el
    # entorno de CI: el error es HONESTO (app_not_found), nunca falso.
    result = integration.execute(
        "open_app", actor="ilu", app="firefox",
    )

    if result["success"] is True:
        assert result.get("pid") is not None
    else:
        assert result["error"] in ("app_not_found", "app_not_allowed")


def test_open_app_fuera_de_allowlist_negado(tmp_path):
    authority = _make_authority(tmp_path)
    authority.grant(
        "open_app",
        actor="owner",
        grantee="ilu",
        scope_type="duration",
        duration="1h",
    )

    integration = _make_integration(tmp_path, authority.grant_store)

    result = integration.execute(
        "open_app", actor="ilu", app="tmux",
    )

    assert result["success"] is False
    assert result["error"] == "app_not_allowed"


def test_media_control_sin_backend_es_honesto(tmp_path):
    authority = _make_authority(tmp_path)
    authority.grant(
        "media_control",
        actor="owner",
        grantee="ilu",
        scope_type="duration",
        duration="1h",
    )

    integration = _make_integration(tmp_path, authority.grant_store)

    # Acción válida. playerctl quizá no esté instalado: si el backend no
    # existe, el error es explícito (media_backend_unavailable).
    result = integration.execute(
        "media_control", actor="ilu", action="pause",
    )

    if result["success"] is False:
        assert result["error"] in (
            "media_backend_unavailable",
            "media_control_failed",
        )


def test_media_action_invalida_rechazada(tmp_path):
    authority = _make_authority(tmp_path)
    authority.grant(
        "media_control",
        actor="owner",
        grantee="ilu",
        scope_type="duration",
        duration="1h",
    )

    integration = _make_integration(tmp_path, authority.grant_store)

    result = integration.execute(
        "media_control", actor="ilu", action="borrar-musica",
    )

    assert result["success"] is False
    assert result["error"] == "media_action_invalid"


def test_capacidad_planificada_no_implementada(tmp_path):
    authority = _make_authority(tmp_path)
    authority.grant(
        "device_control",
        actor="owner",
        grantee="ilu",
        scope_type="duration",
        duration="1h",
    )

    integration = _make_integration(tmp_path, authority.grant_store)

    result = integration.execute(
        "device_control", actor="ilu",
    )

    assert result["success"] is False
    assert result["error"] == "not_implemented"


def test_capacidad_fuera_del_catalogo(tmp_path):
    integration = _make_integration(tmp_path, None)

    result = integration.execute(
        "volar", actor="ilu",
    )

    assert result["success"] is False
    assert result["error"] == "capability_not_in_catalog"