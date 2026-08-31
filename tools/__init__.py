from tools.manager import ToolManager
from tools.system import get_system_time
from tools.call import ToolCall


def create_tool_manager():
    manager = ToolManager()

    manager.register(
        name="system_time",
        description="Consultar la fecha y hora del sistema.",
        handler=get_system_time,
        permission="safe"
    )

    return manager


__all__ = [
    "ToolManager",
    "ToolCall",
    "create_tool_manager"
]
