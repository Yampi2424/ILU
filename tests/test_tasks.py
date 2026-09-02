import pytest

from tasks import TaskManager


def make_manager(tmp_path):
    return TaskManager(path=str(tmp_path / "tasks.json"))


def test_manager_starts_empty(tmp_path):
    manager = make_manager(tmp_path)

    assert manager.list_tasks() == []
    assert manager.get("nope") is None


def test_create_task_requires_title(tmp_path):
    manager = make_manager(tmp_path)

    with pytest.raises(ValueError):
        manager.create("")


def test_create_and_get(tmp_path):
    manager = make_manager(tmp_path)

    task = manager.create(
        title="investigar",
        description="buscar información",
        priority=3
    )

    assert task["title"] == "investigar"
    assert task["state"] == "created"
    assert task["progress"] == 0
    assert task["priority"] == 3

    fetched = manager.get(task["id"])
    assert fetched == task


def test_set_state_marks_timestamps(tmp_path):
    manager = make_manager(tmp_path)

    task = manager.create("tarea")

    running = manager.set_state(task["id"], "running")
    assert running["state"] == "running"
    assert running["started_at"] is not None

    done = manager.set_state(task["id"], "completed")
    assert done["state"] == "completed"
    assert done["completed_at"] is not None


def test_set_state_rejects_invalid(tmp_path):
    manager = make_manager(tmp_path)

    task = manager.create("tarea")

    with pytest.raises(ValueError):
        manager.set_state(task["id"], "no-existe")


def test_set_state_unknown_task_returns_none(tmp_path):
    manager = make_manager(tmp_path)

    assert manager.set_state("nope", "running") is None


def test_progress_clamped(tmp_path):
    manager = make_manager(tmp_path)

    task = manager.create("tarea")

    manager.set_progress(task["id"], 150)
    assert manager.get(task["id"])["progress"] == 100

    manager.set_progress(task["id"], -5)
    assert manager.get(task["id"])["progress"] == 0

    manager.set_progress(task["id"], 40)
    assert manager.get(task["id"])["progress"] == 40


def test_progress_rejects_non_int(tmp_path):
    manager = make_manager(tmp_path)

    task = manager.create("tarea")

    with pytest.raises(ValueError):
        manager.set_progress(task["id"], "abc")


def test_set_result_completes_task(tmp_path):
    manager = make_manager(tmp_path)

    task = manager.create("tarea")

    done = manager.set_result(task["id"], {"ok": True})

    assert done["state"] == "completed"
    assert done["progress"] == 100
    assert done["result"] == {"ok": True}


def test_set_error_fails_task(tmp_path):
    manager = make_manager(tmp_path)

    task = manager.create("tarea")

    failed = manager.set_error(task["id"], "se cayó la red")

    assert failed["state"] == "failed"
    assert failed["error"] == "se cayó la red"


def test_persists_across_restart(tmp_path):
    manager = make_manager(tmp_path)

    task = manager.create("persistente")
    manager.set_state(task["id"], "running")

    reloaded = make_manager(tmp_path)

    assert reloaded.get(task["id"])["state"] == "running"


def test_create_default_max_retries(tmp_path):
    manager = make_manager(tmp_path)

    task = manager.create("tarea")

    assert task["max_retries"] == 3
    assert task["retries"] == 0


def test_create_custom_max_retries(tmp_path):
    manager = make_manager(tmp_path)

    task = manager.create("tarea", max_retries=1)

    assert task["max_retries"] == 1


def test_record_retry_increments(tmp_path):
    manager = make_manager(tmp_path)

    task = manager.create("tarea", max_retries=2)

    assert manager.record_retry(task["id"]) == 1
    assert manager.record_retry(task["id"]) == 2
    assert manager.get(task["id"])["retries"] == 2

    assert manager.record_retry("nope") is None


def test_stats_counts_by_state(tmp_path):
    manager = make_manager(tmp_path)

    manager.create("una")
    manager.create("dos")

    task = manager.create("tres")
    manager.set_state(task["id"], "running")

    stats = manager.stats()

    assert stats["total"] == 3
    assert stats["counts"]["created"] == 2
    assert stats["counts"]["running"] == 1


def test_list_tasks_filters_by_state(tmp_path):
    manager = make_manager(tmp_path)

    manager.create("una")
    task = manager.create("dos")
    manager.set_state(task["id"], "completed")

    completed = manager.list_tasks(state="completed")

    assert len(completed) == 1
    assert completed[0]["title"] == "dos"
