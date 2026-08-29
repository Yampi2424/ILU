from memory.store import MemoryStore


class ILUCore:
    """
    Núcleo lógico de I.L.U.

    Coordina el procesamiento básico y la memoria persistente.
    """

    def __init__(self):
        self.name = "I.L.U."
        self.version = "0.2.0"
        self.memory = MemoryStore()

    def _save_memory(self, message):
        text = message.strip()

        prefixes = [
            "recuerda que ",
            "recuerda ",
            "memoriza que ",
            "memoriza ",
        ]

        content = None

        for prefix in prefixes:
            if text.lower().startswith(prefix):
                content = text[len(prefix):].strip()
                break

        if not content:
            return None

        key = f"memory_{len(self.memory.load_all()) + 1}"

        self.memory.save(
            key,
            content,
            memory_type="conversation",
            importance=5
        )

        return content

    def _search_memory(self, message):
        lowered = message.lower()

        triggers = [
            "que recuerdas",
            "qué recuerdas",
            "recuerdas ",
            "recuerdas?",
            "busca en tu memoria",
            "busca en la memoria",
        ]

        if not any(trigger in lowered for trigger in triggers):
            return None

        words = [
            word.strip("¿?¡!,.:;")
            for word in lowered.split()
            if len(word.strip("¿?¡!,.:;")) >= 4
        ]

        results = []

        for word in words:
            found = self.memory.search(word, limit=5)

            for item in found:
                if item not in results:
                    results.append(item)

        return results

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

        saved = self._save_memory(message)

        if saved:
            return {
                "success": True,
                "input": message,
                "intent": "memory_save",
                "response": f"Recordado: {saved}",
                "core": self.name,
                "version": self.version
            }

        memory_results = self._search_memory(message)

        if memory_results is not None:
            if not memory_results:
                response = "No encontré recuerdos relacionados."

            else:
                memories = [
                    item["content"]
                    for item in memory_results[:5]
                ]

                response = "Recuerdo: " + " | ".join(memories)

            return {
                "success": True,
                "input": message,
                "intent": "memory_read",
                "response": response,
                "core": self.name,
                "version": self.version
            }

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
