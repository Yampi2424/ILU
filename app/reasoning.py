class ILUReasoning:
    """
    Motor de razonamiento inicial de I.L.U.

    Esta capa está separada del Core para que posteriormente
    pueda conectarse a un modelo de IA local o en la nube.
    """

    def __init__(self):
        self.name = "I.L.U. Reasoning"
        self.version = "0.1.0"

    def analyze(self, message, context=None):
        if not isinstance(message, str):
            return {
                "success": False,
                "error": "message_must_be_text"
            }

        message = message.strip()

        if not message:
            return {
                "success": False,
                "error": "empty_message"
            }

        context = context or []

        lowered = message.lower()

        if any(
            word in lowered
            for word in (
                "por qué",
                "porque",
                "como",
                "cómo",
                "explica",
                "analiza",
                "razona"
            )
        ):
            reasoning_type = "analysis"

        elif any(
            word in lowered
            for word in (
                "qué hago",
                "que hago",
                "debería",
                "deberia",
                "recomienda",
                "recomiéndame",
                "recomiendame"
            )
        ):
            reasoning_type = "decision"

        elif context:
            reasoning_type = "contextual"

        else:
            reasoning_type = "general"

        return {
            "success": True,
            "type": reasoning_type,
            "message": message,
            "context": context,
            "reasoning_ready": True,
            "engine": self.name,
            "version": self.version
        }

    def respond(self, analysis):
        if not analysis.get("success"):
            return analysis

        reasoning_type = analysis["type"]
        context = analysis.get("context") or []

        if reasoning_type == "analysis":
            response = (
                "I.L.U. ha identificado una solicitud de análisis. "
                "El motor de razonamiento está preparado para "
                "procesarla."
            )

        elif reasoning_type == "decision":
            response = (
                "I.L.U. ha identificado una solicitud de decisión. "
                "El contexto disponible será utilizado para evaluarla."
            )

        elif reasoning_type == "contextual":
            response = (
                "I.L.U. está utilizando el contexto disponible "
                "para procesar esta conversación."
            )

        else:
            response = (
                "I.L.U. ha procesado la solicitud "
                "y el motor de razonamiento está operativo."
            )

        return {
            "success": True,
            "response": response,
            "reasoning_type": reasoning_type,
            "context_used": len(context),
            "engine": self.name,
            "version": self.version
        }
