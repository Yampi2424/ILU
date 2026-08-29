from memory.store import MemoryStore
from app.reasoning import ILUReasoning


class ILUCore:
    """
    Núcleo central de I.L.U.

    Coordina:
    - procesamiento de mensajes
    - memoria persistente
    - recuperación de contexto
    - motor de razonamiento
    """

    def __init__(self):
        self.name = "I.L.U."
        self.version = "0.6.0"

        self.memory = MemoryStore()
        self.reasoning = ILUReasoning()

    def _next_memory_key(self):
        memories = self.memory.load_all()

        index = 1

        while f"memory_{index}" in memories:
            index += 1

        return f"memory_{index}"

    def _save_memory(
        self,
        content,
        memory_type="conversation",
        importance=5
    ):
        content = content.strip()

        if not content:
            return None

        self.memory.save(
            self._next_memory_key(),
            content,
            memory_type=memory_type,
            importance=importance
        )

        return content

    def _detect_memory_type(self, content):
        lowered = content.lower()

        if any(
            phrase in lowered
            for phrase in (
                "prefiero",
                "prefiere",
                "me gusta",
                "no me gusta",
                "mi preferencia"
            )
        ):
            return "preference", 9

        if any(
            word in lowered
            for word in (
                "proyecto",
                "sistema",
                "aplicación",
                "aplicacion",
                "i.l.u.",
                "ilu"
            )
        ):
            return "project", 9

        if any(
            word in lowered
            for word in (
                "es ",
                "son ",
                "está ",
                "esta ",
                "tiene ",
                "usa ",
                "utiliza "
            )
        ):
            return "fact", 8

        return "knowledge", 6

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

                if not content:
                    return None

                memory_type, importance = self._detect_memory_type(
                    content
                )

                self._save_memory(
                    content,
                    memory_type=memory_type,
                    importance=importance
                )

                return {
                    "content": content,
                    "type": memory_type,
                    "importance": importance
                }

        return None

    def _search_memory(self, message):
        lowered = message.lower()

        if not (
            "que recuerdas" in lowered
            or "qué recuerdas" in lowered
            or "busca en tu memoria" in lowered
            or "busca en la memoria" in lowered
        ):
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

        return results[:10]

    def _get_context(self, message):
        words = [
            word.strip("¿?¡!,.:;")
            for word in message.lower().split()
            if len(word.strip("¿?¡!,.:;")) >= 5
        ]

        if not words:
            return []

        results = []

        for word in words[:6]:
            found = self.memory.search(word, limit=3)

            for item in found:
                if item not in results:
                    results.append(item)

        return results[:5]

    def _format_memories(self, memories):
        if not memories:
            return ""

        values = []

        for item in memories:
            content = item.get("content")

            if content:
                values.append(content)

        return " | ".join(values)

    def _basic_response(self, message):
        lowered = message.lower()

        if lowered in (
            "hola",
            "hello",
            "buenas",
            "buenos dias",
            "buenas tardes"
        ):
            return (
                "Hola. I.L.U. está disponible.",
                "greeting"
            )

        if "estado" in lowered or "status" in lowered:
            return (
                "I.L.U. está operativa.",
                "status"
            )

        if (
            "quien eres" in lowered
            or "qué eres" in lowered
            or "que eres" in lowered
        ):
            return (
                "Soy I.L.U., una arquitectura de inteligencia "
                "preparada para trabajar localmente y en la nube.",
                "identity"
            )

        return (
            "He recibido tu mensaje. "
            "El núcleo de procesamiento está funcionando.",
            "general"
        )

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
                "memory_type": explicit_memory["type"],
                "importance": explicit_memory["importance"],
                "response": (
                    f"Recordado: {explicit_memory['content']}"
                ),
                "core": self.name,
                "version": self.version
            }

        memory_results = self._search_memory(message)

        if memory_results is not None:
            if not memory_results:
                response = "No encontré recuerdos relacionados."
            else:
                response = (
                    "Recuerdo: "
                    + self._format_memories(memory_results)
                )

            return {
                "success": True,
                "input": message,
                "intent": "memory_read",
                "response": response,
                "core": self.name,
                "version": self.version
            }

        context = self._get_context(message)
        context_text = self._format_memories(context)

        basic_response, intent = self._basic_response(message)

        analysis = self.reasoning.analyze(
            message,
            context
        )

        reasoning = self.reasoning.respond(
            analysis
        )

        if intent == "general" and reasoning.get("success"):
            response = reasoning["response"]

            if context_text:
                response += (
                    f" Contexto relacionado: {context_text}"
                )
        else:
            response = basic_response

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
            "reasoning": {
                "type": reasoning.get("reasoning_type"),
                "context_used": reasoning.get("context_used", 0)
            },
            "core": self.name,
            "version": self.version
        }
