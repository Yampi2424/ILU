"""
I.L.U. — Aprendizaje y Personalización (Bloque B: JARVIS Evolution).

El motor de aprendizaje convierte la conversación en conocimiento
persistente y en un perfil personalizable del OWNER / la familia:

  - `learn(turn)`: distila de un turno los recuerdos de alto valor
    (preferencias, datos personales, proyectos, hechos estables) y los
    guarda con el tipo y la importancia correctos en el MemoryRouter.
    No depende de un comando explícito: I.L.U. aprende de forma
    pasiva lo que es relevante y estable.

  - `profile()`: agrega las memorias de tipo preferencia/personal en un
    perfil estructurado que I.L.U. puede usar para personalizar sus
    respuestas y su proactividad.

El aprendizaje NUNCA es un bypass de permisos: solo escribe en la
memoria de I.L.U., que es su propio dominio. No ejecuta herramientas ni
acciones sobre el mundo.

Para evitar el ruido, la destilación es conservadora: solo se promueve
un recuerdo si supera un umbral de relevancia (importancia >= 7) o si es
una preferencia/personal explícita, y se evita duplicar lo ya guardado.
"""

import re


# Marcas que indican información estable y personalizable (no ruido).
_PREFERENCE_MARKS = (
    "prefiero", "prefieres", "me gusta", "no me gusta",
    "me encanta", "detesto", "prefiero que", "me gustaría",
    "quisiera que", "quiero que siempre", "nunca me",
    "siempre me", "mi favorito", "mi favorita",
)

_PERSONAL_MARKS = (
    "soy", "me llamo", "mi nombre", "mi cumpleaños", "nací",
    "trabajo en", "vivo en", "mi familia", "mi pareja",
    "mi hijo", "mi hija", "mis hijos", "mi esposa", "mi esposo",
    "somos", "nosotros", "mi mascota",
)

_PROJECT_MARKS = (
    "proyecto", "estoy trabajando en", "estamos trabajando",
    "el sistema", "la aplicación", "mi repo", "i.l.u.", "ilu",
    "el servidor", "nuestro proyecto",
)

_FACT_MARKS = (
    "es ", "son ", "está ", "tiene ", "usa ", "utiliza ",
    "se llama ", "queda en ", "queda a las ",
)


def _mentions(text, marks):
    lowered = text.lower()
    return any(mark in lowered for mark in marks)


def classify(text):
    """Clasifica un turno en un tipo de memoria y una importancia."""
    lowered = text.lower()

    if _mentions(lowered, _PREFERENCE_MARKS):
        return "preference", 9

    if _mentions(lowered, _PERSONAL_MARKS):
        return "personal", 9

    if _mentions(lowered, _PROJECT_MARKS):
        return "project", 8

    if _mentions(lowered, _FACT_MARKS):
        return "fact", 7

    return None, 0


class LearningEngine:
    """
    Motor de aprendizaje pasivo y perfil de personalización.
    """

    def __init__(self, memory=None):
        # `memory` es un MemoryRouter. Se inyecta para testear; si no,
        # se crea uno por defecto.
        if memory is None:
            from memory.router import MemoryRouter
            memory = MemoryRouter()

        self.memory = memory

    # ------------------------------------------------------------------
    # Destilación
    # ------------------------------------------------------------------

    def _already_known(self, content, memory_type=None, threshold=0.6):
        """
        ¿Un recuerdo esencialmente igual ya está guardado?

        Solo se compara contra recuerdos del MISMO tipo: un turno de
        conversación con la misma frase no debe impedir aprender la
        preferencia/personal que destila (la conversación vive en su
        propio tipo con baja importancia).
        """
        types = [memory_type] if memory_type else None

        for record in self.memory.query(content, types=types, limit=5):
            existing = (record.content or "").lower()
            if existing == content.lower():
                return True

        return False

    def learn(self, turn, source="user"):
        """
        Distila un turno de conversación en recuerdos de alto valor.

        Devuelve una lista de diccionarios con lo aprendido (content,
        memory_type, importance). Si el turno no contiene información
        estable o relevante, devuelve [] sin guardar nada (anti-ruido).
        """
        turn = (turn or "").strip()

        if not turn:
            return []

        # Frases de control explícitas de memoria ya se gestionan en el
        # core; aquí solo tratamos el aprendizaje pasivo.
        memory_type, importance = classify(turn)

        if memory_type is None or importance < 7:
            return []

        if self._already_known(turn, memory_type=memory_type):
            return []

        record = self.memory.remember(
            content=turn,
            memory_type=memory_type,
            importance=importance,
            source=source,
        )

        if record is None:
            return []

        return [{
            "content": record.content,
            "memory_type": record.memory_type,
            "importance": record.importance,
            "key": record.key,
        }]

    # ------------------------------------------------------------------
    # Perfil de personalización
    # ------------------------------------------------------------------

    def profile(self):
        """
        Agrega las memorias de personalización en un perfil estructurado.

        El perfil agrupa por tipo (preferencias, personal, proyecto,
        hechos) y conserva la importancia y la antigüedad para que I.L.U.
        pueda ponderar. Devuelve un dict legible y accionable.
        """
        types = ("preference", "personal", "project", "fact")

        grouped = {}

        for memory_type in types:
            records = self.memory.list_by_type(memory_type, limit=100)

            items = [
                {
                    "content": record.content,
                    "importance": record.importance,
                    "updated_at": record.updated_at,
                }
                for record in records
            ]

            items.sort(key=lambda item: item["importance"], reverse=True)

            grouped[memory_type] = items

        total = sum(len(items) for items in grouped.values())

        return {
            "count": total,
            "groups": grouped,
        }

    # ------------------------------------------------------------------
    # Resumen hablable
    # ------------------------------------------------------------------

    def summary(self):
        """Resumen legible del perfil para respuestas por voz/texto."""
        profile = self.profile()

        if profile["count"] == 0:
            return "Todavía no he aprendido suficiente sobre ti."

        parts = []

        label = {
            "preference": "Preferencias",
            "personal": "Sobre ti",
            "project": "Tus proyectos",
            "fact": "Hechos",
        }

        for memory_type in ("preference", "personal", "project", "fact"):
            items = profile["groups"].get(memory_type, [])

            if not items:
                continue

            lines = [
                f"{item['content']}"
                for item in items[:5]
            ]

            parts.append(f"{label[memory_type]}: {' | '.join(lines)}")

        return "Aprendí: " + " ; ".join(parts)
