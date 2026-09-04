"""
Bloque 13 — Despacho por lenguaje natural de la ejecución real.

Verifica que "ejecutá X", "abrí Y" y "pausá la música" se resuelven de
forma determinista contra las tools del mundo, SIEMPRE gateadas: sin
grant -> authorization=ask + solicitud abierta; con grant ->
ejecución real (o rechazo honesto de la lista blanca). Y que "abre el
archivo…" sigue siendo read_file (sin colisión).
"""

from app.core import ILUCore
from app.audit import AuditLog
from memory.store import MemoryStore


class FakeProvider:
    def __init__(self, model_result):
        self.name = "fake"
        self.version = "0.0.1"
        self.model_result = model_result

    def generate(self, message, context=None, tools=None):
        return self.model_result


def make_core(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_POOLED", raising=False)
    monkeypatch.delenv("ILU_AI_PROVIDER", raising=False)
    monkeypatch.delenv("ILU_AUTONOMY", raising=False)

    core = ILUCore()
    core.provider = FakeProvider(
        {"type": "text", "content": "respuesta"}
    )
    core.memory = MemoryStore(path=str(tmp_path / "data.json"))
    core.audit = AuditLog(path=str(tmp_path / "audit.jsonl"))
    return core


def test_ejecuta_sin_grant_pide_autorizacion_y_abre_solicitud(
    monkeypatch, tmp_path
):
    core = make_core(monkeypatch, tmp_path)

    result = core.process("ejecutá whoami")

    assert result["success"] is False
    assert result["intent"] == "tool_error"
    assert result["tool"] == "run_command"
    assert result["authorization"] == "ask"
    assert result["authorization_request_id"]
    assert "necesita autorización" in result["response"].lower()


def test_ejecuta_con_grant_corre_real(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)
    core.authority.grant(
        "run_command",
        actor="owner",
        grantee="ilu",
        scope_type="duration",
        duration="1h",
    )

    result = core.process("ejecutá whoami")

    assert result["success"] is True
    assert result["intent"] == "tool_use"
    assert result["tool"] == "run_command"
    assert result["response"].startswith("Ejecuté:")


def test_ejecuta_rm_fuera_de_allowlist_rechazo_honesto(
    monkeypatch, tmp_path
):
    core = make_core(monkeypatch, tmp_path)
    core.authority.grant(
        "run_command",
        actor="owner",
        grantee="ilu",
        scope_type="duration",
        duration="1h",
    )

    result = core.process("ejecutá rm -rf /")

    assert result["success"] is False
    assert result["intent"] == "tool_error"
    assert result["tool"] == "run_command"
    assert result["response"] == (
        "Ese comando no está en la lista blanca de I.L.U."
    )


def test_ejecuta_con_metachars_rechazo_honesto(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)
    core.authority.grant(
        "run_command",
        actor="owner",
        grantee="ilu",
        scope_type="duration",
        duration="1h",
    )

    result = core.process("ejecutá ls | grep x")

    assert result["success"] is False
    assert result["intent"] == "tool_error"
    assert result["tool"] == "run_command"
    assert result["response"] == (
        "Ese comando usa operadores de shell vetados "
        "(pipes, redirección o sustitución)."
    )


def test_abre_el_archivo_sigue_siendo_read_file(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    result = core.process("abre el archivo inexistente.txt")

    # La colisión más temida: "abre el archivo X" NUNCA debe caer en
    # open_app. Ahora crea un archivo real y verifica read_file.

    target = tmp_path / "workspace" / "nota.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("contenido", encoding="utf-8")

    result = core.process(f"abre el archivo {target}")

    assert result["tool"] == "read_file"
    assert result["tool"] != "open_app"
    assert "contenido" in result["response"]


def test_abri_firefox_con_grant_es_open_app(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)
    core.authority.grant(
        "open_app",
        actor="owner",
        grantee="ilu",
        scope_type="duration",
        duration="1h",
    )

    result = core.process("abrí firefox")

    assert result["tool"] == "open_app"
    assert result["intent"] in ("tool_use", "tool_error")
    assert result.get("authorization") != "ask"


def test_abri_app_desconocida_no_despacha_open_app(monkeypatch, tmp_path):
    # "abrí apprarez" (no en allowlist): el despacho directo NO la fuerza;
    # cae al modelo (provider fake → intent "chat").
    core = make_core(monkeypatch, tmp_path)

    result = core.process("abrí una app inexistente")

    assert result.get("tool") != "open_app"


def test_pausa_la_musica_sin_grant_pide_autorizacion(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    result = core.process("pausá la música")

    assert result["success"] is False
    assert result["tool"] == "media_control"
    assert result["authorization"] == "ask"
    assert result["authorization_request_id"]


def test_subi_el_volumen_despacha_media_control(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    result = core.process("subí el volumen")

    assert result["tool"] == "media_control"
    assert result["tool_call"]["arguments"]["action"] == "volume-up"


def test_ejecuta_sin_comando_cae_al_modelo(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    # "ejecutá" suelto (sin comando) no forma un direct tool call.
    result = core.process("ejecutá")

    assert result.get("tool") is None
    assert result["success"] is True