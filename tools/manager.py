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

    def register(
        self,
        name,
        description,
        handler,
        permission="safe",
        schema=None
    ):
        """
        Registra una herramienta.

        `schema` es opcional: un JSON-schema de los parámetros (Bloque 11)
        que el modelo rellena. Si se omite, la herramienta sigue
        funcionando igual (retrocompatible), con `properties` vacío.
        """
        if not name:
            raise ValueError("tool_name_required")

        if not callable(handler):
            raise ValueError("tool_handler_must_be_callable")

        self.tools[name] = {
            "name": name,
            "description": description,
            "handler": handler,
            "permission": permission,
            "schema": schema or None
        }

    def get_schema(self, name):
        """Devuelve el JSON-schema de la herramienta, o None si no tiene."""
        tool = self.tools.get(name)

        if tool is None:
            return None

        return tool.get("schema")

    def list_tools(self):
        """Lista pública (retrocompatible): name/description/permission."""
        return [
            {
                "name": tool["name"],
                "description": tool["description"],
                "permission": tool["permission"]
            }
            for tool in self.tools.values()
        ]

    def list_tools_full(self):
        """
        Lista completa (Bloque 11): incluye el JSON-schema de cada tool.

        El array `tools` que se envía al proveedor usa esta forma, de
        modo que `openai_functions()` puede emitir los `parameters`
        reales. No altera `list_tools()`, preservando la
        retrocompatibilidad de la lista pública.
        """
        return [
            {
                "name": tool["name"],
                "description": tool["description"],
                "permission": tool["permission"],
                "schema": tool.get("schema")
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
