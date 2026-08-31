class ILUReasoning:
    """
    Motor de razonamiento de I.L.U.

    Analiza la solicitud, determina su tipo y genera
    un plan básico de acción antes de responder.
    """

    def __init__(self):
        self.name = "I.L.U. Reasoning"
        self.version = "0.2.0"

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

        elif any(
            word in lowered
            for word in (
                "ejecuta",
                "haz",
                "realiza",
                "comprueba",
                "consulta",
                "busca"
            )
        ):
            reasoning_type = "action"

        elif context:
            reasoning_type = "contextual"

        else:
            reasoning_type = "general"

        plan = self._build_plan(
            reasoning_type,
            context
        )

        complexity = self._estimate_complexity(
            message,
            context
        )

        return {
            "success": True,
            "type": reasoning_type,
            "message": message,
            "context": context,
            "reasoning_ready": True,
            "complexity": complexity,
            "plan": plan,
            "engine": self.name,
            "version": self.version
        }

    def _build_plan(self, reasoning_type, context):
        if reasoning_type == "analysis":
            return [
                "comprender_solicitud",
                "analizar_informacion",
                "generar_respuesta"
            ]

        if reasoning_type == "decision":
            return [
                "comprender_solicitud",
                "revisar_contexto",
                "evaluar_opciones",
                "generar_recomendacion"
            ]

        if reasoning_type == "action":
            return [
                "comprender_solicitud",
                "identificar_accion",
                "verificar_herramienta",
                "ejecutar_accion",
                "generar_respuesta"
            ]

        if reasoning_type == "contextual":
            return [
                "comprender_solicitud",
                "recuperar_contexto",
                "integrar_memoria",
                "generar_respuesta"
            ]

        return [
            "comprender_solicitud",
            "generar_respuesta"
        ]

    def _estimate_complexity(self, message, context):
        words = len(message.split())

        if words > 40 or len(context) >= 4:
            return "high"

        if words > 15 or context:
            return "medium"

        return "simple"

    def respond(self, analysis):
        if not analysis.get("success"):
            return analysis

        reasoning_type = analysis["type"]
        context = analysis.get("context") or []

        if reasoning_type == "analysis":
            response = (
                "I.L.U. ha identificado una solicitud de análisis."
            )

        elif reasoning_type == "decision":
            response = (
                "I.L.U. ha identificado una solicitud de decisión "
                "y evaluará el contexto disponible."
            )

        elif reasoning_type == "action":
            response = (
                "I.L.U. ha identificado una solicitud de acción "
                "y verificará las herramientas disponibles."
            )

        elif reasoning_type == "contextual":
            response = (
                "I.L.U. está utilizando memoria y contexto "
                "para procesar esta conversación."
            )

        else:
            response = (
                "I.L.U. ha procesado la solicitud."
            )

        return {
            "success": True,
            "response": response,
            "reasoning_type": reasoning_type,
            "context_used": len(context),
            "complexity": analysis.get(
                "complexity",
                "simple"
            ),
            "plan": analysis.get(
                "plan",
                []
            ),
            "engine": self.name,
            "version": self.version
        }
