from config.settings import ILUSettings


ENV_KEYS = (
    "ILU_VERSION",
    "ILU_AI_PROVIDER",
    "ILU_ENV",
    "ILU_MEMORY_MODE",
    "DATABASE_URL",
    "DATABASE_URL_POOLED",
    "ILU_OMNIROUTE_URL",
    "ILU_OMNIROUTE_API_KEY",
    "ILU_OMNIROUTE_MODEL",
    "ILU_AUTONOMY",
)


def clear_ilu_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_defaults(monkeypatch):
    clear_ilu_env(monkeypatch)

    settings = ILUSettings()

    assert settings.name == "I.L.U."
    assert settings.version == "0.8.0"
    assert settings.provider == "local"
    assert settings.environment == "production"
    assert settings.memory_mode == "auto"
    assert settings.database_url is None
    assert settings.omniroute_url == "http://localhost:20128/v1"
    assert settings.omniroute_api_key == ""
    assert settings.omniroute_model == "openai/gpt-oss-120b"
    assert settings.autonomy_level == "assisted"


def test_autonomy_env(monkeypatch):
    monkeypatch.setenv("ILU_AUTONOMY", "autonomous")
    settings = ILUSettings()
    assert settings.autonomy_level == "autonomous"


def test_omniroute_envs(monkeypatch):
    monkeypatch.setenv(
        "ILU_OMNIROUTE_URL",
        "http://example.test:20128/v1"
    )
    monkeypatch.setenv("ILU_OMNIROUTE_API_KEY", "clave-secreta")
    monkeypatch.setenv(
        "ILU_OMNIROUTE_MODEL",
        "nvidia/openai/gpt-oss-120b"
    )

    settings = ILUSettings()

    assert settings.omniroute_url == "http://example.test:20128/v1"
    assert settings.omniroute_api_key == "clave-secreta"
    assert settings.omniroute_model == "nvidia/openai/gpt-oss-120b"


def test_provider_env(monkeypatch):
    monkeypatch.setenv("ILU_AI_PROVIDER", "omniroute")
    settings = ILUSettings()
    assert settings.provider == "omniroute"


def test_omniroute_url_trailing_slash(monkeypatch):
    monkeypatch.setenv(
        "ILU_OMNIROUTE_URL",
        "http://example.test:20128/v1/"
    )

    settings = ILUSettings()

    assert settings.omniroute_url == "http://example.test:20128/v1"