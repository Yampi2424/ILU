"""
Bloque 13 — CommandPolicy: la lista blanca de ejecución real.

Verifica que el dial N.º 2 (QUÉ se puede ejecutar/abrir/controlar después
del grant) es fail-closed: solo comandos de la allowlist, sin metachars,
y con un archivo ausente/corrupto la política queda vacía (nada ejecuta).
"""

import json

from security.command_policy import (
    CommandPolicy,
    PLAYERCTL_ACTIONS,
    MEDIA_ACTIONS,
)


def _write_policy(tmp_path, **overrides):
    path = tmp_path / "run_commands.json"
    data = {
        "version": 1,
        "allowlist": ["ls", "pwd", "whoami", "date", "uname", "hostname"],
        "apps": ["firefox", "brave", "code", "vlc"],
        "media": ["playerctl"],
        "deny_substrings": [";", "&&", "||", "|", ">", "<", "$", "`", ".."],
        "default_timeout": 15,
        "max_output_bytes": 8192,
    }
    data.update(overrides)
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_validate_command_allowlist_ok(tmp_path):
    policy = CommandPolicy(_write_policy(tmp_path))

    ok, value = policy.validate_command('ls -la "mi dir"')

    assert ok is True
    assert value == ["ls", "-la", "mi dir"]


def test_validate_command_not_allowlisted(tmp_path):
    policy = CommandPolicy(_write_policy(tmp_path))

    ok, error = policy.validate_command("rm -rf /")

    assert ok is False
    assert error == "command_not_allowlisted"


def test_validate_command_metachars_rejected(tmp_path):
    policy = CommandPolicy(_write_policy(tmp_path))

    # Pipes / redirección / sustitución de comando en CUALQUIER token.
    for cmdline in (
        "ls | grep x",
        "ls > out.txt",
        "whoami && echo hola",
        "date; echo pwned",
        "echo $(whoami)",
        "echo `whoami`",
        "ls -la ../etc",
    ):
        ok, error = policy.validate_command(cmdline)
        assert ok is False, cmdline
        # Rechazo fail-closed: el error concreto varía según dónde caiga
        # el token contaminado (primer token vs. token posterior), pero
        # NUNCA puede ser un ejecutable.
        assert error in (
            "command_not_allowlisted",
            "command_token_rejected",
        ), cmdline


def test_validate_command_shlex_malformed(tmp_path):
    policy = CommandPolicy(_write_policy(tmp_path))

    ok, error = policy.validate_command('ls "sin cerrar')

    assert ok is False
    assert error == "command_malformed"


def test_validate_command_when_policy_missing_is_fail_closed(tmp_path):
    policy = CommandPolicy(str(tmp_path / "no_existe.json"))

    assert policy.available() is False
    assert policy.allowlist == []
    assert policy.apps == []

    ok, error = policy.validate_command("ls")

    assert ok is False
    assert error == "command_policy_unavailable"


def test_validate_command_non_string_fails(tmp_path):
    policy = CommandPolicy(_write_policy(tmp_path))

    ok, error = policy.validate_command(None)

    assert ok is False
    assert error == "command_required"


def test_app_allowed_ok_and_denied(tmp_path):
    policy = CommandPolicy(_write_policy(tmp_path))

    assert policy.app_allowed("firefox") is True
    assert policy.app_allowed("sudo") is False
    assert policy.app_allowed("") is False


def test_media_args_traduccion_y_accion_desconocida(tmp_path):
    policy = CommandPolicy(_write_policy(tmp_path))

    args, backend = policy.media_args("pause")
    assert args == ["pause"]
    assert backend == "playerctl"

    args, backend = policy.media_args("volume-up")
    assert args == ["volume", "+0.05"]
    assert backend == "playerctl"

    args, backend = policy.media_args("hackear-mp3")
    assert args is None
    assert backend is None


def test_media_actions_acotadas():
    # El catálogo de media es CERRADO: se maprea a comandos playerctl
    # exactos, jamás a argumentos libres.
    assert MEDIA_ACTIONS == (
        "play", "pause", "play-pause", "next", "previous",
        "volume-up", "volume-down", "mute", "unmute",
    )
    assert all(isinstance(v, list) for v in PLAYERCTL_ACTIONS.values())


def test_confinamientos_por_defecto(tmp_path):
    policy = CommandPolicy(_write_policy(tmp_path))

    assert policy.default_timeout() == 15
    assert policy.max_output_bytes() == 8192


def test_env_override_de_confinamientos(tmp_path, monkeypatch):
    policy = CommandPolicy(_write_policy(tmp_path))

    monkeypatch.setenv("ILU_WORLD_TIMEOUT", "7")
    monkeypatch.setenv("ILU_WORLD_MAX_OUTPUT", "1024")

    # Re-carga para que el env tenga efecto (Una sola fuente efectiva).
    policy = CommandPolicy(_write_policy(tmp_path))

    assert policy.default_timeout() == 7
    assert policy.max_output_bytes() == 1024


def test_allowlist_por_defecto_es_solo_lectura():
    # El default de la política SIN archivo-fuente es un set de SOLO
    # LECTURA/inspección: jamás rm/sudo/shutdown/dd...
    policy = CommandPolicy("/path/irrelevante/que/no_existe.json")

    destructivos = {"rm", "sudo", "shutdown", "dd", "mkfs", "chmod"}
    # Los defaults se usan solo cuando el archivo existe pero no define
    # allowlist; sin archivo -> fail-closed vacío. Este test verifica el
    # default commiteado de run_commands.json real.
    import os
    real_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "security", "run_commands.json",
    )
    loaded = CommandPolicy(real_path)

    assert not (set(loaded.allowlist) & destructivos)