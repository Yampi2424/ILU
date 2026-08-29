from memory.store import MemoryStore


class ILUCore:
    """
    Núcleo lógico de I.L.U.

    Procesa mensajes, mantiene memoria persistente
    y recupera contexto relevante de conversaciones.
    """

    def __init__(self):
        self.name = "I.L.U."
        self.version = "0.4.0"
        self.memory = MemoryStore()

    def _save_memory(self, content, memory_type="conversation", importance=5):
        content = content.strip()

        if not content:
            return None

        memories = self.memory.load_all()

        index = 1
        while f"memory_{index}" in memories:
            index += 1

        key = f"memory_{index}"

        self.memory.save(
            key,
            content,
            memory_type=memory_type,
            importance=importance
        )

        return content

    def _save_explicit_memory(self, message):
        prefixes = (
            "recuerda que ",
            "recuerda ",
            "memoriza que ",
            "memoriza ",
        )

        lowered = message.lower()

        for prefix in prefixes:
            if lowered.startswith(prefix):
                content = message[len(prefix):].strip()

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
            "que recuerdas" not in lowered
            and "qué recuerdas" not in lowered
            and "busca en tu memoria" not in lowered
            and "busca en la memoria" not in lowered
        ):
            return None

        words = [
            word.strip("¿?¡!,.:;")
            for word in lowered.split()
            if len(word.strip("¿?¡!,.:;")) >= 4
        ]

        results = []

        for word in words:
            for item in self.memory.search(word, limit=5):
                if item not in results:
                    results.append(item)

        return results[:5]

    def _get_context(self, message):
        """
        Busca recuerdos relacionados con el mensaje actual.

        No utiliza el contexto para comandos explícitos de memoria,
        porque esos ya tienen su propio flujo.
        """

        words = [
            word.strip("¿?¡!,.:;")
            for word in message.lower().split()
            if len(word.strip("¿?¡!,.:;")) >= 5
        ]

        if not words:
            return []

        results = []

        for word in words[:6]:
            for item in self.memory.search(word, limit=3):
                if item not in results:
                    results.append(item)

        return results[:5]

    def _format_context(self, context):
        if not context:
            return ""

        memories = [
            item["content"]
            for item in context
            if item.get("content")
        ]

        if not memories:
            return ""

        return " | ".join(memories)

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
                    for item in memory_results
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

        context = self._get_context(message)
        context_text = self._format_context(context)

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
            if context_text:
                response = (
                    "He recibido tu mensaje. "
                    f"Contexto relacionado: {context_text}"
                )
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
            "context": context_text,
            "core": self.name,
            "version": self.version
        }
