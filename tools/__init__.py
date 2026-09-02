from tools.manager import ToolManager
from tools.system import get_system_time
from tools.call import ToolCall
from tools.search import web_search
from tools.filesystem import read_file, write_file
# Importamos con alias para no sombrear el submódulo tools.notify (el
# nombre de la función coincide con el del módulo; si no se hace así,
# tools.notify deja de apuntar al módulo y los tests/monkeypatch fallan).
from tools.notify import notify as _notify


def create_tool_manager():
    """
    Panel de herramientas ejecutables de I.L.U.

    Permisos:
    - safe  : solo lectura / inocua — se auto-ejecuta en modo asistido.
    - ask   : requiere autorización humana — se detiene en la compuerta.
    - blocked : prohibida (ninguna por defecto).

    Ninguna herramienta se ejecuta sin pasar antes por la compuerta de
    autorización (SecurityGate); una respuesta del LLM no equivale a
    permiso de ejecución.
    """
    manager = ToolManager()

    manager.register(
        name="system_time",
        description="Consultar la fecha y hora del sistema.",
        handler=get_system_time,
        permission="safe"
    )

    manager.register(
        name="web_search",
        description=(
            "Búsqueda web ligera sin clave "
            "(DuckDuckGo Instant Answers)."
        ),
        handler=web_search,
        permission="safe"
    )

    manager.register(
        name="read_file",
        description=(
            "Leer un archivo de texto dentro del workspace de I.L.U. "
            "(ILU_WORKSPACE)."
        ),
        handler=read_file,
        permission="safe"
    )

    manager.register(
        name="notify",
        description="Dejar una notificación local dirigida al usuario.",
        handler=_notify,
        permission="safe"
    )

    manager.register(
        name="write_file",
        description=(
            "Crear o reescribir un archivo dentro del workspace. "
            "Requiere autorización humana."
        ),
        handler=write_file,
        permission="ask"
    )

    return manager


__all__ = [
    "ToolManager",
    "ToolCall",
    "create_tool_manager"
]