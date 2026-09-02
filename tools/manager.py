class ToolManager:
    """
    Administrador central de herramientas de I.L.U.

    Las herramientas se registran explícitamente.
    Esto permite controlar qué puede y qué no puede ejecutar I.L.U.
    """

    def __init__(self):
        self.name = "I.L.U. Tool Manager"
        self.version = "0.1.0"
        self.tools = {}

    def register(self, name, description, handler, permission="safe"):
        if not name:
            raise ValueError("tool_name_required")

        if not callable(handler):
            raise ValueError("tool_handler_must_be_callable")

        self.tools[name] = {
            "name": name,
            "description": description,
            "handler": handler,
            "permission": permission
        }

    def list_tools(self):
        return [
            {
                "name": tool["name"],
                "description": tool["description"],
                "permission": tool["permission"]
            }
            for tool in self.tools.values()
        ]

    def has_tool(self, name):
        return name in self.tools

    def get_permission(self, name):
        tool = self.tools.get(name)

        if tool is None:
            return None

        return tool["permission"]

    def execute(self, name, **kwargs):
        tool = self.tools.get(name)

        if tool is None:
            return {
                "success": False,
                "error": "tool_not_found",
                "tool": name
            }

        if tool["permission"] == "blocked":
            return {
                "success": False,
                "error": "tool_blocked",
                "tool": name
            }

        try:
            result = tool["handler"](**kwargs)

            # Un handler que devuelve un dict con success False reporta un
            # fallo FUNCIONAL (no una excepción): se propaga para no
            # ocultar el fallo (p. ej. búsqueda web sin red o ruta fuera
            # del workspace).
            if (
                isinstance(result, dict)
                and result.get("success") is False
            ):
                return {
                    "success": False,
                    "tool": name,
                    "error": result.get("error", "tool_failed"),
                    "result": result
                }

            return {
                "success": True,
                "tool": name,
                "result": result
            }

        except Exception as error:
            return {
                "success": False,
                "error": "tool_execution_failed",
                "tool": name,
                "detail": str(error)
            }
