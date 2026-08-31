class ToolCall:
    """
    Representa una solicitud de ejecución de una herramienta.

    I.L.U. primero crea una ToolCall y luego el ToolManager
    decide si puede ejecutarla.
    """

    def __init__(
        self,
        tool,
        arguments=None,
        reason=""
    ):
        self.tool = tool
        self.arguments = arguments or {}
        self.reason = reason

    def to_dict(self):
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            "reason": self.reason
        }
