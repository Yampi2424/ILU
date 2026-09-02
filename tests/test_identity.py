from config.identity import ILU_IDENTITY, ilu_system_prompt


def test_identity_keys():
    assert ILU_IDENTITY["name"] == "I.L.U."
    assert ILU_IDENTITY["full_name"] == "Inteligencia Local Unificada"
    assert ILU_IDENTITY["owner"] == "familia"
    assert isinstance(ILU_IDENTITY["capabilities"], list)
    assert ILU_IDENTITY["capabilities"]
    assert isinstance(ILU_IDENTITY["limits"], list)
    assert ILU_IDENTITY["limits"]


def test_system_prompt_base():
    prompt = ilu_system_prompt()

    assert "Eres I.L.U." in prompt
    assert "Inteligencia Local Unificada" in prompt
    assert "No inventes capacidades, herramientas ni acciones" in prompt


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