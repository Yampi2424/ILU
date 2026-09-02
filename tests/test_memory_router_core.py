from app.core import ILUCore
from app.audit import AuditLog
from tasks import TaskManager
from memory.router import MemoryRouter
from memory.backends import JsonBackend


class FakeProvider:
    def __init__(self, model_result):
        self.name = "fake"
        self.version = "0.0.1"
        self.model_result = model_result

    def generate(self, message, context=None, tools=None):
        if callable(self.model_result):
            return self.model_result(message, context, tools)

        return self.model_result


def make_core(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_POOLED", raising=False)
    monkeypatch.delenv("ILU_AI_PROVIDER", raising=False)
    monkeypatch.delenv("ILU_AUTONOMY", raising=False)
    monkeypatch.delenv("ILU_MEMORY_BACKEND", raising=False)

    core = ILUCore()

    core.provider = FakeProvider(
        {"type": "text", "content": "no se debe usar"}
    )
    core.audit = AuditLog(path=str(tmp_path / "audit.jsonl"))
    core.tasks = TaskManager(path=str(tmp_path / "tasks.json"))
    core.memory = MemoryRouter(
        backend=JsonBackend(path=str(tmp_path / "memory.json"))
    )

    return core


def test_explicit_save_still_works(monkeypatch, tmp_path):
    # Compatibilidad: "recuerda que X" sigue siendo un guardado
    # explícito, no se interpreta como comando de administración.
    core = make_core(monkeypatch, tmp_path)

    result = core.process("recuerda que me gusta el café")

    assert result["success"] is True
    assert result["intent"] == "memory_save"
    assert result["memory_type"] == "preference"


def test_explicit_search_still_works(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    core.memory.remember(
        "me gusta el café por las mañanas",
        memory_type="conversation",
    )

    result = core.process("qué recuerdas sobre café")

    assert result["success"] is True
    assert result["intent"] == "memory_read"
    assert "café" in result["response"]


def test_forget_via_natural_language(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    core.memory.remember(
        "receta secreta de la abuela",
        memory_type="personal",
    )

    result = core.process("olvida la receta de la abuela")

    assert result["success"] is True
    assert result["intent"] == "memory_forget"
    assert "Olvidado" in result["response"]

    assert core.memory.query("receta") == []


def test_correct_via_natural_language(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    core.memory.remember(
        "el cumpleaños de ana es en mayo",
        memory_type="personal",
    )

    result = core.process(
        "corrige que cumpleaños de ana "
        "por el cumpleaños de ana es en julio"
    )

    assert result["success"] is True
    assert result["intent"] == "memory_update"
    assert "julio" in result["response"]

    matches = core.memory.query("cumpleaños de ana")
    assert "julio" in matches[0].content
    assert "mayo" not in matches[0].content


def test_query_skills_via_natural_language(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    core.memory.remember("python avanzado", memory_type="skill")
    core.memory.remember("bash y scripting", memory_type="skill")

    result = core.process("qué habilidades tienes")

    assert result["success"] is True
    assert result["intent"] == "memory_read"
    assert "python avanzado" in result["response"]
    assert "bash y scripting" in result["response"]


def test_query_skills_empty(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    result = core.process("qué sabes hacer")

    assert result["success"] is True
    assert result["intent"] == "memory_read"
    assert "no he registrado habilidades" in result["response"]


def test_memory_stats_via_natural_language(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    core.memory.remember("una", memory_type="conversation")
    core.memory.remember("dos", memory_type="conversation")
    core.memory.remember("un skill", memory_type="skill")

    result = core.process("cuántos recuerdos tienes")

    assert result["success"] is True
    assert result["intent"] == "memory_read"
    assert result["memory_count"] == 3


def test_normal_talk_is_not_a_memory_command(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    result = core.process("puedes ayudarme con el proyecto")

    assert result["intent"] not in (
        "memory_forget",
        "memory_update",
        "memory_read",
        "memory_save",
    )


def test_save_uses_conversation_type_in_pipeline(monkeypatch, tmp_path):
    # El pipeline sigue guardando la conversación como antes, pero ahora
    # a través del router (tipo "conversation").
    core = make_core(monkeypatch, tmp_path)

    core.process("hola")

    assert len(core.memory.list_by_type("conversation")) >= 1


def test_search_finds_short_tokens_regression(monkeypatch, tmp_path):
    # Regresión: consultar por un término corto ("té") debe encontrarlo,
    # sin depender de la longitud de la palabra.
    core = make_core(monkeypatch, tmp_path)

    core.memory.remember("me gusta el té", memory_type="personal")

    result = core.process("qué recuerdas sobre té")

    assert result["intent"] == "memory_read"
    assert "té" in result["response"]


def test_search_does_not_flood_with_unrelated_records(monkeypatch, tmp_path):
    # Regresión: tras corregir "café" por "té", buscar "café" NO debe
    # devolver el recuerdo sobre "té". La relevancia exige coincidencia.
    core = make_core(monkeypatch, tmp_path)

    core.memory.remember("me gusta el té", memory_type="personal")

    result = core.process("qué recuerdas sobre café")

    assert result["intent"] == "memory_read"
    assert "No encontré" in result["response"]