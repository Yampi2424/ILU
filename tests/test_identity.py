from config.identity import ILU_IDENTITY, ilu_system_prompt


def test_identity_keys():
    assert ILU_IDENTITY["name"] == "I.L.U."
    assert ILU_IDENTITY["full_name"] == "Inteligencia Local Unificada"
    assert ILU_IDENTITY["owner"] == "Jean Pierre Ronaldo Soto Acevedo"
    assert ILU_IDENTITY["creator"] == "Jean Pierre Ronaldo Soto Acevedo"
    assert isinstance(ILU_IDENTITY["capabilities"], list)
    assert ILU_IDENTITY["capabilities"]
    assert isinstance(ILU_IDENTITY["limits"], list)
    assert ILU_IDENTITY["limits"]


def test_system_prompt_base():
    prompt = ilu_system_prompt()

    assert "Eres I.L.U." in prompt
    assert "Inteligencia Local Unificada" in prompt
    assert "No inventes capacidades, herramientas ni acciones" in prompt


def test_system_prompt_nombra_al_creador():
    prompt = ilu_system_prompt()

    assert "Jean Pierre Ronaldo Soto Acevedo" in prompt
    # La clave de autorización NUNCA se le revela al modelo.
    assert "240890" not in prompt


def test_limits_no_revela_clave():
    limits = " ".join(ILU_IDENTITY["limits"]).lower()

    assert "clave" in limits
    assert "revela" in limits
    assert "autorización" in limits


def test_system_prompt_with_context():
    prompt = ilu_system_prompt([
        {"content": "Al usuario le gusta el café"}
    ])

    assert "Al usuario le gusta el café" in prompt


def test_system_prompt_filters_non_memories():
    prompt = ilu_system_prompt([
        "texto suelto",
        {"content": ""},
        42,
        {"content": "esto sí vale"}
    ])

    assert "texto suelto" not in prompt
    assert " esto sí vale" in prompt