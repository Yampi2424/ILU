"""
Herramientas de lectura/escritura de archivos de I.L.U.

Confinamiento de workspace: los handlers resuelven la ruta y exigen que el
archivo quede DENTRO del directorio de trabajo de I.L.U. (`ILU_WORKSPACE`,
por defecto la carpeta desde la que corre la app). Esto es defensa en
profundidad: aunque `write_file` ya es permiso "ask" (no se ejecuta sin
autorización humana), ningún handler debe poder escapar del workspace.

Se rechazan rutas fuera del workspace (incluido el traversal `../`) y
archivos demasiado grandes (límite de lectura).
"""

import os
from pathlib import Path

MAX_READ_BYTES = 200_000


def workspace_root():
    """Raíz del workspace de I.L.U.: ILU_WORKSPACE o el cwd."""
    raw = os.environ.get("ILU_WORKSPACE", "").strip()

    if raw:
        return Path(raw).resolve()

    return Path.cwd().resolve()


def resolve_within_workspace(path):
    """
    Resuelve `path` y garantiza que quede dentro del workspace.

    - `path` relativo se interpreta contra la raíz del workspace.
    - `path` absoluto se permite solo si cae dentro del workspace.
    - Un intento de salir del workspace levanta ValueError.
    """
    base = workspace_root()
    candidate = (base / path).resolve()
    base_resolved = base.resolve()

    if not (
        candidate == base_resolved
        or candidate.is_relative_to(base_resolved)
    ):
        raise ValueError("path_outside_workspace")

    return candidate


def read_file(path=None):
    """
    Lee un archivo de texto dentro del workspace.

    Devuelve {"success", "path", "content"} o un error estructurado
    (path_required / path_outside_workspace / file_not_found /
     not_a_file / file_too_large).
    """
    if not path:
        return {"success": False, "error": "path_required"}

    try:
        target = resolve_within_workspace(path)
    except ValueError:
        return {
            "success": False,
            "error": "path_outside_workspace",
            "path": str(path),
        }

    if not target.exists():
        return {
            "success": False,
            "error": "file_not_found",
            "path": str(target),
        }

    if not target.is_file():
        return {
            "success": False,
            "error": "not_a_file",
            "path": str(target),
        }

    if target.stat().st_size > MAX_READ_BYTES:
        return {
            "success": False,
            "error": "file_too_large",
            "path": str(target),
        }

    content = target.read_text(encoding="utf-8", errors="replace")

    return {
        "success": True,
        "path": str(target),
        "content": content,
    }


def write_file(path=None, content="", append=False):
    """
    Crea o reescribe un archivo dentro del workspace.

    Permiso "ask": no se ejecuta sin autorización humana — la compuerta de
    seguridad ya no lo deja pasar en modo asistido; el confinamiento de
    workspace es una segunda barrera.
    """
    if not path:
        return {"success": False, "error": "path_required"}

    try:
        target = resolve_within_workspace(path)
    except ValueError:
        return {
            "success": False,
            "error": "path_outside_workspace",
            "path": str(path),
        }

    target.parent.mkdir(parents=True, exist_ok=True)

    with target.open("a" if append else "w", encoding="utf-8") as file:
        file.write(str(content))

    return {
        "success": True,
        "path": str(target),
        "chars": len(str(content)),
    }