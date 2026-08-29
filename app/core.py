from memory.store import MemoryStore


class ILUCore:
    """
    Núcleo lógico de I.L.U.

    Procesa mensajes y mantiene memoria persistente
    de conversación.
    """

    def __init__(self):
        self.name = "I.L.U."
        self.version = "0.3.0"
        self.memory = MemoryStore()

    def _save_memory(self, message, memory_type="conversation", importance=5):
        text = message.strip()

        if not text:
            return None

        count = len(self.memory.load_all())
        key = f"memory_{count + 1}"

        self.memory.save(
            key,
            text,
            memory_type=memory_type,
            importance=importance
        )

        return text

    def _save_explicit_memory(self, message):
        text = message.strip()

        prefixes = [
            "recuerda que ",
            "recuerda ",
            "memoriza que ",
            "memoriza ",
        ]

        for prefix in prefixes:
            if text.lower().startswith(prefix):
                content = text[len(prefix):].strip()

                if content:
                    self._save_memory(
                        content,
                        memory_type="fact",
                        importance=10
                    )

                    return content

        return None

    def _search_memory(self, message):
        lowered = message.lower()

        if (
            "que recuerdas" in lowered
            or "qué recuerdas" in lowered
            or "busca en tu memoria" in lowered
            or "busca en la memoria" in lowered
        ):
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

        return None

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

        explicit_memory = self._save_explicit_memory(message)

        if explicit_memory:
            return {
                "success": True,
                "input": message,
                "intent": "memory_save",
                "response": f"Recordado: {explicit_memory}",
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

        else:
            response = (
                "He recibido tu mensaje. "
                "El núcleo de procesamiento está funcionando."
            )
            intent = "general"

        self._save_memory(
            message,
            memory_type="conversation",
            importance=3
        )

        return {
            "success": True,
            "input": message,
            "intent": intent,
            "response": response,
            "core": self.name,
            "version": self.version
        }
