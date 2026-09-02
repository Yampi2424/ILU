from memory.types import (
    MEMORY_TYPES,
    normalize_type,
    importance_default,
    lifecycle_of,
    VALID_TYPES,
    DEFAULT_TYPE,
)


def test_canonical_types_cover_final_architecture():
    # Los tipos canónicos cubren las categorías de la arquitectura final.
    assert "conversation" in MEMORY_TYPES
    assert "episodic" in MEMORY_TYPES
    assert "semantic" in MEMORY_TYPES
    assert "personal" in MEMORY_TYPES
    assert "family" in MEMORY_TYPES
    assert "working" in MEMORY_TYPES
    assert "procedural" in MEMORY_TYPES
    assert "knowledge" in MEMORY_TYPES
    assert "experience" in MEMORY_TYPES
    assert "skill" in MEMORY_TYPES
    assert "task" in MEMORY_TYPES
    assert "error" in MEMORY_TYPES


def test_each_type_has_policy():
    for name, policy in MEMORY_TYPES.items():
        assert policy["purpose"]
        assert policy["retention"] in ("permanente", "temporal", "volátil")
        assert policy["lifecycle"] in ("volatile", "temporal", "permanent")
        assert 1 <= policy["importance_default"] <= 10


def test_conversation_is_temporal_others_permanent():
    assert MEMORY_TYPES["conversation"]["retention"] == "temporal"

    for name in ("personal", "knowledge", "skill"):
        assert MEMORY_TYPES[name]["retention"] == "permanente"


def test_working_is_volatile():
    # La memoria de trabajo no sobrevive a un reinicio.
    assert MEMORY_TYPES["working"]["retention"] == "volátil"
    assert MEMORY_TYPES["working"]["lifecycle"] == "volatile"


def test_lifecycle_maps_per_type():
    # El eje de ciclo de vida alimenta la capa de retención.
    assert lifecycle_of("working") == "volatile"
    assert lifecycle_of("conversation") == "temporal"
    assert lifecycle_of("episodic") == "temporal"
    assert lifecycle_of("semantic") == "permanent"
    assert lifecycle_of("procedural") == "permanent"


def test_lifecycle_unknown_defaults_to_permanent():
    # Conservador: un tipo no catalogado no se borra por retención.
    assert lifecycle_of("no-existe") == "permanent"


def test_legacy_types_preserved():
    assert "general" in VALID_TYPES
    assert "preference" in VALID_TYPES
    assert "project" in VALID_TYPES
    assert "fact" in VALID_TYPES


def test_normalize_known_type_passthrough():
    assert normalize_type("skill") == "skill"
    assert normalize_type("knowledge") == "knowledge"
    assert normalize_type("preference") == "preference"


def test_normalize_unknown_type_falls_back_to_default():
    assert normalize_type("no-existe") == DEFAULT_TYPE
    assert normalize_type(None) == DEFAULT_TYPE
    assert normalize_type("") == DEFAULT_TYPE


def test_importance_default_known_type():
    assert importance_default("personal") == 8
    assert importance_default("task") == 5


def test_importance_default_unknown_returns_5():
    assert importance_default("no-existe") == 5