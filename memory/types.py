"""
Taxonomía de tipos de memoria de I.L.U.

Esta capa es declarativa: no ejecuta nada, solo describe qué guarda cada
tipo y con qué intención, para que las capas superiores (router, futuro
aprendizaje, planificación, subagentes) puedan comportarse distinto según
el tipo de memoria.

Los tipos canónicos cubren la arquitectura final de la memoria:

- conversation : diálogos y sesiones (retiene poco, caduca antes)
- episodic     : experiencias y acontecimientos vividos
- semantic     : conocimiento aprendido y hechos
- personal     : datos personales/familiares
- family       : autoridad y contexto familiar
- working      : memoria de trabajo / sesión actual (volátil)
- procedural   : cómo hacer cosas y usar herramientas
- knowledge    : conocimiento general de I.L.U.
- experience   : lecciones y resultados pasados
- skill        : capacidades y dominio de I.L.U.
- task         : tareas y sus resultados
- error        : errores y correcciones

Cada tipo declara además un eje de ciclo de vida (`lifecycle`), usado por
la capa de retención:
- volatile  : se borra al reiniciar (memoria de trabajo)
- temporal  : se comprime/agrega al envejecer (conversación, episódica)
- permanent : no se borra por retención (solo por corrección humana)

Se conservan además los tipos legados que ya usa el pipeline actual
(general, preference, project, fact) para no romper datos existentes.
"""

# Tipos canónicos y sus políticas.
MEMORY_TYPES = {
    "conversation": {
        "purpose": "diálogos y sesiones",
        "retention": "temporal",
        "lifecycle": "temporal",
        "importance_default": 3,
    },
    "episodic": {
        "purpose": "experiencias y acontecimientos",
        "retention": "temporal",
        "lifecycle": "temporal",
        "importance_default": 5,
    },
    "semantic": {
        "purpose": "conocimiento aprendido y hechos",
        "retention": "permanente",
        "lifecycle": "permanent",
        "importance_default": 6,
    },
    "personal": {
        "purpose": "datos personales y familiares",
        "retention": "permanente",
        "lifecycle": "permanent",
        "importance_default": 8,
    },
    "family": {
        "purpose": "autoridad y contexto familiar",
        "retention": "permanente",
        "lifecycle": "permanent",
        "importance_default": 9,
    },
    "working": {
        "purpose": "memoria de trabajo y sesión actual",
        "retention": "volátil",
        "lifecycle": "volatile",
        "importance_default": 3,
    },
    "procedural": {
        "purpose": "cómo hacer cosas y usar herramientas",
        "retention": "permanente",
        "lifecycle": "permanent",
        "importance_default": 7,
    },
    "knowledge": {
        "purpose": "conocimiento general de I.L.U.",
        "retention": "permanente",
        "lifecycle": "permanent",
        "importance_default": 6,
    },
    "experience": {
        "purpose": "lecciones y resultados pasados",
        "retention": "permanente",
        "lifecycle": "permanent",
        "importance_default": 7,
    },
    "skill": {
        "purpose": "capacidades y dominio de I.L.U.",
        "retention": "permanente",
        "lifecycle": "permanent",
        "importance_default": 7,
    },
    "task": {
        "purpose": "tareas y sus resultados",
        "retention": "permanente",
        "lifecycle": "permanent",
        "importance_default": 5,
    },
    "error": {
        "purpose": "errores y correcciones",
        "retention": "permanente",
        "lifecycle": "permanent",
        "importance_default": 6,
    },
}

# Tipos que ya usa el pipeline actual (core) y que se conservan por
# compatibilidad con la memoria existente.
LEGACY_TYPES = {"general", "preference", "project", "fact"}

DEFAULT_TYPE = "general"

VALID_TYPES = set(MEMORY_TYPES) | LEGACY_TYPES


def normalize_type(memory_type):
    """
    Devuelve un tipo conocido o el tipo por defecto.

    Cualquier cadena se acepta al guardar (un almacén externo puede usar
    tipos propios), pero esta función garantiza un valor canónico para el
    filtrado y las políticas internas.
    """
    if memory_type and memory_type in VALID_TYPES:
        return memory_type

    return DEFAULT_TYPE


def importance_default(memory_type):
    """Importancia por defecto para un tipo (5 si no está catalogado)."""
    info = MEMORY_TYPES.get(memory_type)

    if info:
        return info["importance_default"]

    return 5


def lifecycle_of(memory_type):
    """
    Eje de ciclo de vida de un tipo: "volatile" | "temporal" | "permanent".

    Los tipos no catalogados se tratan como permanentes (no se borran por
    retención), lo que es el comportamiento conservador por defecto.
    """
    info = MEMORY_TYPES.get(memory_type)

    if info:
        return info.get("lifecycle", "permanent")

    return "permanent"
