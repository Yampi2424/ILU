class ILUReasoning:
    """
    Motor de razonamiento y planificación ligera de I.L.U.

    Esta capa analiza la intención de una solicitud,
    identifica si necesita contexto y construye
    una estructura de pasos que posteriormente
    podrá utilizar el sistema de herramientas.
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

        reasoning_type = self._detect_type(
            lowered,
            context
        )

        needs_memory = bool(context)

        complexity = self._estimate_complexity(
            lowered
        )

        plan = self._build_plan(
            reasoning_type,
            needs_memory,
            complexity
        )

        return {
            "success": True,
            "type": reasoning_type,
            "message": message,
            "context": context,
            "context_used": len(context),
            "needs_memory": needs_memory,
            "complexity": complexity,
            "plan": plan,
            "reasoning_ready": True,
            "engine": self.name,
            "version": self.version
        }

    def _detect_type(self, message, context):
        analysis_words = (
            "por qué",
            "porque",
            "cómo",
            "como",
            "explica",
            "analiza",
            "razona",
            "compara",
            "evalúa",
            "evalua",
            "calcula"
        )

        decision_words = (
            "qué hago",
            "que hago",
            "debería",
            "deberia",
            "recomienda",
            "recomiéndame",
            "recomiendame",
            "conviene",
            "cuál es mejor",
            "cual es mejor",
            "elige"
        )

        action_words = (
            "haz",
            "hacer",
            "crea",
            "crear",
            "ejecuta",
            "ejecutar",
            "abre",
            "abrir",
            "cambia",
            "cambiar",
            "instala",
            "instalar",
            "descarga",
            "descargar"
        )

        question_words = (
            "qué",
            "que",
            "quién",
            "quien",
            "dónde",
            "donde",
            "cuándo",
            "cuando"
        )

        if any(
            word in message
            for word in action_words
        ):
            return "action"

        if any(
            word in message
            for word in decision_words
        ):
            return "decision"

        if any(
            word in message
            for word in analysis_words
        ):
            return "analysis"

        if any(
            word in message
            for word in question_words
        ):
            return "question"

        if context:
            return "contextual"

        return "general"

    def _estimate_complexity(self, message):
        words = message.split()

        score = 0

        if len(words) > 8:
            score += 1

        if len(words) > 20:
            score += 1

        if any(
            word in message
            for word in (
                "y ",
                "además",
                "también",
                "después",
                "luego",
                "primero",
                "finalmente"
            )
        ):
            score += 1

        if any(
            word in message
            for word in (
                "analiza",
                "compara",
                "evalúa",
                "evalua",
                "planifica",
                "organiza"
            )
        ):
            score += 1

        if score == 0:
            return "simple"

        if score <= 2:
            return "moderate"

        return "complex"

    def _build_plan(
        self,
        reasoning_type,
        needs_memory,
        complexity
    ):
        steps = []

        if needs_memory:
            steps.append(
                "recuperar_contexto"
            )

        if reasoning_type in (
            "analysis",
            "decision",
            "action"
        ):
            steps.append(
                "analizar_solicitud"
            )

        if reasoning_type == "decision":
            steps.append(
                "evaluar_opciones"
            )

        if reasoning_type == "action":
            steps.append(
                "preparar_accion"
            )

        if complexity != "simple":
            steps.append(
                "organizar_respuesta"
            )

        steps.append(
            "generar_respuesta"
        )

        return steps

    def respond(self, analysis):
        if not analysis.get("success"):
            return analysis

        reasoning_type = analysis.get(
            "type",
            "general"
        )

        context_used = analysis.get(
            "context_used",
            0
        )

        plan = analysis.get(
            "plan",
            []
        )

        if reasoning_type == "analysis":
            response = (
                "Solicitud de análisis identificada."
            )

        elif reasoning_type == "decision":
            response = (
                "Solicitud de decisión identificada."
            )

        elif reasoning_type == "action":
            response = (
                "Solicitud de acción identificada."
            )

        elif reasoning_type == "question":
            response = (
                "Pregunta identificada."
            )

        elif reasoning_type == "contextual":
            response = (
                "Solicitud contextual identificada."
            )

        else:
            response = (
                "Solicitud general identificada."
            )

        return {
            "success": True,
            "response": response,
            "reasoning_type": reasoning_type,
            "context_used": context_used,
            "complexity": analysis.get(
                "complexity",
                "simple"
            ),
            "plan": plan,
            "engine": self.name,
            "version": self.version
        }
