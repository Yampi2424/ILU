"""
Bloque 14 — OwnerSecret: la clave de autorización del owner (PIN).

La concesión de permisos por voz/texto exige que la persona demuestre la
clave del owner. Este guardián la lee de la variable de entorno
ILU_OWNER_SECRET o del archivo local security/owner.pin, y la compara
con secrets.compare_digest. Fail-closed: sin clave configurada, nada de
"todo permitido": no hay con quién comparar.
"""

import os

from security.owner_secret import OwnerSecret


def _write_pin(tmp_path, content="240890"):
    path = tmp_path / "owner.pin"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_lee_del_archivo(tmp_path, monkeypatch):
    monkeypatch.delenv("ILU_OWNER_SECRET", raising=False)

    secret = OwnerSecret(path=_write_pin(tmp_path))

    assert secret.configured is True
    assert secret.source() == f"file:{tmp_path / 'owner.pin'}"


def test_lee_de_la_variable_de_entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("ILU_OWNER_SECRET", "240890")
    secret = OwnerSecret(path=str(tmp_path / "no_existe.pin"))

    assert secret.configured is True
    assert secret.source() == "env:ILU_OWNER_SECRET"


def test_env_tiene_precedencia_sobre_el_archivo(tmp_path, monkeypatch):
    monkeypatch.setenv("ILU_OWNER_SECRET", "240890")
    # El archivo existe pero con OTRA clave: gana el entorno.
    secret = OwnerSecret(path=_write_pin(tmp_path, content="999999"))

    assert secret.matches("240890") is True
    assert secret.matches("999999") is False


def test_matches_verdadero_y_falso(tmp_path, monkeypatch):
    monkeypatch.delenv("ILU_OWNER_SECRET", raising=False)
    secret = OwnerSecret(path=_write_pin(tmp_path))

    assert secret.matches("240890") is True
    assert secret.matches("111111") is False
    assert secret.matches("") is False
    assert secret.matches(None) is False


def test_matches_ignora_espacios_alrededor(tmp_path, monkeypatch):
    monkeypatch.delenv("ILU_OWNER_SECRET", raising=False)
    secret = OwnerSecret(path=_write_pin(tmp_path, content=" 240890 "))

    assert secret.configured is True
    assert secret.matches("240890") is True


def test_fail_closed_sin_configurar(tmp_path, monkeypatch):
    # Ni env ni archivo: NO hay clave -> configured=False y nada coincide.
    monkeypatch.delenv("ILU_OWNER_SECRET", raising=False)

    secret = OwnerSecret(path=str(tmp_path / "no_existe.pin"))

    assert secret.configured is False
    assert secret.source() == "unconfigured"
    assert secret.matches("240890") is False
    assert secret.matches("") is False

    # El archivo vacío también cuenta como "no configurada".
    empty = tmp_path / "vacio.pin"
    empty.write_text("   \n", encoding="utf-8")

    secret = OwnerSecret(path=str(empty))
    assert secret.configured is False