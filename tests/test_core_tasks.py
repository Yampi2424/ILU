from app.core import ILUCore
from app.audit import AuditLog
from memory.store import MemoryStore
from tasks import TaskManager


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

    core = ILUCore()

    core.provider = FakeProvider(
        {"type": "text", "content": "no se debe usar"}
    )
    core.memory = MemoryStore(path=str(tmp_path / "data.json"))
    core.audit = AuditLog(path=str(tmp_path / "audit.jsonl"))
    core.tasks = TaskManager(path=str(tmp_path / "tasks.json"))

    return core


def test_create_task_in_natural_language(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    result = core.process("crea una tarea: revisar los informes")

    assert result["success"] is True
    assert result["intent"] == "task_create"
    assert result["response"].startswith("Tarea 'revisar los informes'")
    assert result["task_id"]

    task = core.tasks.get(result["task_id"])
    assert task["title"] == "revisar los informes"
    assert task["state"] == "created"


def test_create_task_requires_title(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    result = core.process("crea una tarea")

    assert result["intent"] == "task_create_pending"
    assert "qué tarea" in result["response"].lower()


def test_list_tasks_in_natural_language(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    core.tasks.create("investigar omni")
    core.tasks.create("revisar logs")

    result = core.process("qué tareas tienes")

    assert result["intent"] == "task_list"
    assert len(result["tasks"]) == 2
    assert "investigar omni" in result["response"]


def test_list_tasks_empty(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    result = core.process("mis tareas")

    assert result["intent"] == "task_list"
    assert result["tasks"] == []
    assert "No hay tareas" in result["response"]


def test_task_status_reports_latest(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    task = core.tasks.create("investigar")
    core.tasks.set_state(task["id"], "running")
    core.tasks.set_progress(task["id"], 60)

    result = core.process("cómo va la tarea")

    assert result["intent"] == "task_status"
    assert result["task_id"] == task["id"]
    assert result["state"] == "running"
    assert result["progress"] == 60
    assert "60%" in result["response"]


def test_task_status_when_empty(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    result = core.process("estado de la tarea")

    assert result["intent"] == "task_status"
    assert "No hay tareas" in result["response"]


def test_task_command_not_triggered_by_normal_talk(monkeypatch, tmp_path):
    # Una conversación normal no debe interpretarse como comando.
    core = make_core(monkeypatch, tmp_path)

    result = core.process("¿puedes ayudarme con el proyecto?")

    assert result["intent"] not in (
        "task_create",
        "task_list",
        "task_status",
        "task_create_pending"
    )


def test_create_task_is_audited(monkeypatch, tmp_path):
    core = make_core(monkeypatch, tmp_path)

    core.process("crea una tarea: revisar informes")

    audit = core.audit.recent()

    assert any(
        entry.get("action") == "task_create"
        and "revisar informes" in entry.get("title", "")
        for entry in audit
    )
