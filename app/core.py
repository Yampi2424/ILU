from memory.store import MemoryStore


class ILUCore:
    """
    Núcleo lógico inicial de I.L.U.

    Núcleo liviano y preparado para utilizar
    una capa de memoria externa.
    """

    def __init__(self):
        self.name = "I.L.U."
        self.version = "0.2.0"
        self.memory = MemoryStore()

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

        if lowered in (
            "hola",
            "hello",
            "buenas",
            "buenos dias",
            "buenas tardes"
        ):
            response = "Hola. I.L.U. está disponible."
            intent = "greeting"

        elif "estado" in lowered or "status" in lowered:
            response = "I.L.U. está operativa."
            intent = "status"

        elif (
            "quien eres" in lowered
            or "qué eres" in lowered
            or "que eres" in lowered
        ):
            response = (
                "Soy I.L.U., una arquitectura de inteligencia "
                "preparada para trabajar localmente y en la nube."
            )
            intent = "identity"

        elif lowered.startswith("recuerda "):
            value = message[9:].strip()

            if not value:
                response = "No recibí nada para recordar."
                intent = "memory_error"
            else:
                self.memory.save("last_memory", value)
                response = f"Recordado: {value}"
                intent = "memory_save"

        elif (
            "que recuerdas" in lowered
            or "qué recuerdas" in lowered
            or "memoria" in lowered
        ):
            remembered = self.memory.get("last_memory")

            if remembered:
                response = f"Recuerdo: {remembered}"
            else:
                response = "Todavía no tengo recuerdos almacenados."

            intent = "memory_read"

        else:
            response = (
                "He recibido tu mensaje. "
                "El núcleo de procesamiento está funcionando."
            )
            intent = "general"

        return {
            "success": True,
            "input": message,
            "intent": intent,
            "response": response,
            "core": self.name,
            "version": self.version
        }
