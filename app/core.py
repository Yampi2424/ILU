class ILUCore:
    """
    Núcleo lógico inicial de I.L.U.

    Esta versión es deliberadamente liviana.
    Los modelos pesados se incorporarán posteriormente.
    """

    def __init__(self):
        self.name = "I.L.U."
        self.version = "0.1.0"

    def process(self, message):
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

        lowered = message.lower()

        if lowered in ("hola", "hello", "buenas", "buenos dias", "buenas tardes"):
            response = "Hola. I.L.U. está disponible."

            intent = "greeting"

        elif "estado" in lowered or "status" in lowered:
            response = "I.L.U. está operativa."

            intent = "status"

        elif "quien eres" in lowered or "qué eres" in lowered or "que eres" in lowered:
            response = "Soy I.L.U., una arquitectura de inteligencia preparada para trabajar localmente y en la nube."

            intent = "identity"

        else:
            response = "He recibido tu mensaje. El núcleo de procesamiento está funcionando."

            intent = "general"

        return {
            "success": True,
            "input": message,
            "intent": intent,
            "response": response,
            "core": self.name,
            "version": self.version
        }
