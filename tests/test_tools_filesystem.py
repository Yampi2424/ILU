"""Herramientas read_file / write_file con confinamiento de workspace."""

import pytest

from tools.filesystem import (
    read_file,
    write_file,
    resolve_within_workspace,
)


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("ILU_WORKSPACE", str(tmp_path))
    return tmp_path


def test_read_file_returns_content(workspace):
    target = workspace / "notas.txt"
    target.write_text("hola ilu", encoding="utf-8")

    result = read_file("notas.txt")

    assert result["success"] is True
    assert result["content"] == "hola ilu"
    assert target.name in result["path"]


def test_read_file_requires_path(workspace):
    assert read_file(None) == {
        "success": False,
        "error": "path_required",
    }


def test_read_file_missing(workspace):
    result = read_file("no-existe.txt")

    assert result["success"] is False
    assert result["error"] == "file_not_found"


def test_read_file_missing_is_missing_path(workspace):
    # shims: el error debe identificarse, no lanzarse ni confundirse.
    result = read_file("nada.txt")
    assert result["error"] == "file_not_found"


def test_traversal_rejected(workspace):
    result = read_file("../../etc/hostname")

    assert result["success"] is False
    assert result["error"] == "path_outside_workspace"


def test_absolute_outside_rejected(workspace):
    result = read_file("/etc/hostname")

    assert result["success"] is False
    assert result["error"] == "path_outside_workspace"


def test_resolve_within_workspace_allows_relative(workspace):
    target = resolve_within_workspace("sub/dir.txt")

    assert target == (workspace / "sub/dir.txt").resolve()


def test_resolve_within_workspace_rejects_escape(workspace):
    with pytest.raises(ValueError):
        resolve_within_workspace("../../escapar")


def test_write_file_creates_file(workspace):
    result = write_file("out.txt", content="generado por ilu")

    assert result["success"] is True
    assert (workspace / "out.txt").read_text() == "generado por ilu"


def test_write_file_requires_path(workspace):
    assert write_file(None, content="x") == {
        "success": False,
        "error": "path_required",
    }


def test_write_file_outside_workspace_rejected(workspace):
    result = write_file("../fuera.txt", content="x")

    assert result["success"] is False
    assert result["error"] == "path_outside_workspace"