import pytest
from pydantic import ValidationError

from app.core.config import DatabaseSettings, Settings, StartupSettings, get_settings


def test_defaults_preserve_local_startup():
    settings = get_settings()
    assert settings.mongo_uri.get_secret_value() == "mongodb://localhost:27017"
    assert settings.mongodb_db_name == "spacos"
    assert settings.api_port == 8000
    assert settings.jwt_secret is None
    assert not settings.auth_enabled
    assert not settings.sptrans_enabled


def test_dotenv_is_loaded_and_environment_takes_priority(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MONGO_URI=mongodb://dotenv.test:27017\n"
        "MONGODB_DB_NAME=arquivo\nCORS_ORIGINS=https://frontend.example.com\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MONGODB_DB_NAME", "terminal")
    settings = Settings(_env_file=env_file)
    assert settings.mongo_uri.get_secret_value() == "mongodb://dotenv.test:27017"
    assert settings.mongodb_db_name == "terminal"
    assert settings.cors_origins == ["https://frontend.example.com"]


@pytest.mark.parametrize("alias", ["MONGO_URI", "MONGODB_URI"])
def test_mongo_aliases(alias, monkeypatch):
    monkeypatch.setenv(alias, "mongodb://legacy.test:27018")
    assert DatabaseSettings().mongo_uri.get_secret_value() == (
        "mongodb://legacy.test:27018"
    )


def test_canonical_alias_wins_in_same_source(monkeypatch):
    monkeypatch.setenv("MONGO_URI", "mongodb://canonical.test:27017")
    monkeypatch.setenv("MONGODB_URI", "mongodb://legacy.test:27017")
    assert DatabaseSettings().mongo_uri.get_secret_value() == (
        "mongodb://canonical.test:27017"
    )


@pytest.mark.parametrize(
    ("env_alias", "file_alias"),
    [("MONGODB_URI", "MONGO_URI"), ("MONGO_URI", "MONGODB_URI")],
)
def test_environment_alias_beats_dotenv_alias(
    tmp_path, monkeypatch, env_alias, file_alias
):
    env_file = tmp_path / ".env"
    env_file.write_text(f"{file_alias}=mongodb://file.test:27017\n", encoding="utf-8")
    monkeypatch.setenv(env_alias, "mongodb://environment.test:27017")
    settings = DatabaseSettings(_env_file=env_file)
    assert settings.mongo_uri.get_secret_value() == "mongodb://environment.test:27017"


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"mongo_uri": "https://wrong.example.com"}, "mongodb://"),
        ({"mongodb_db_name": " "}, "must not be blank"),
        ({"api_port": 0}, "greater than or equal"),
        ({"external_api_timeout_seconds": 0}, "greater than"),
        ({"external_api_timeout_seconds": float("inf")}, "finite"),
        ({"sptrans_base_url": "not-a-url"}, "URL"),
        ({"auth_enabled": True}, "requires JWT_SECRET"),
        ({"jwt_secret": "short-secret"}, "at least 32"),
        ({"mapas_culturais_enabled": True}, "MAPAS_CULTURAIS_ENABLED"),
        ({"geosampa_enabled": True}, "GEOSAMPA_ENABLED"),
        ({"sptrans_enabled": True}, "SPTRANS_ENABLED"),
        (
            {"sptrans_enabled": True, "sptrans_base_url": "https://example.com"},
            "requires SPTRANS_TOKEN",
        ),
        (
            {
                "sptrans_enabled": True,
                "sptrans_base_url": "https://example.com",
                "sptrans_token": " ",
            },
            "requires SPTRANS_TOKEN",
        ),
    ],
)
def test_invalid_settings_fail_early(values, message):
    with pytest.raises(ValidationError, match=message):
        Settings(**values)


def test_enabled_features_and_secret_redaction():
    secret = "random-test-secret-with-more-than-32-characters"
    token = "private-test-api-token"
    settings = Settings(
        auth_enabled=True,
        jwt_secret=secret,
        sptrans_enabled=True,
        sptrans_base_url="https://example.com",
        sptrans_token=token,
        mapas_culturais_enabled=True,
        mapas_culturais_base_url="https://maps.example.com",
        geosampa_enabled=True,
        geosampa_base_url="https://geo.example.com",
    )
    assert settings.jwt_secret.get_secret_value() == secret
    assert settings.sptrans_token.get_secret_value() == token
    assert secret not in repr(settings)
    assert token not in settings.model_dump_json()


def test_database_commands_ignore_unrelated_feature_settings(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv("SPTRANS_ENABLED", "true")
    assert DatabaseSettings().mongodb_db_name == "spacos"
    assert StartupSettings().api_port == 8000


def test_empty_optional_fields_and_unknown_dotenv_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "JWT_SECRET=\nSPTRANS_TOKEN=\nMONGODB_TEST_URI=\n"
        "MAPAS_CULTURAIS_BASE_URL=\nGEOSAMPA_BASE_URL=\nSPTRANS_BASE_URL=\n"
        "UNRELATED_TOOL_VALUE=example\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=env_file)
    assert settings.jwt_secret is None
    assert settings.mongodb_test_uri is None
    assert settings.mapas_culturais_base_url is None


def test_invalid_secret_is_not_in_validation_error():
    with pytest.raises(ValidationError) as error:
        Settings(jwt_secret="private-value")
    assert "private-value" not in str(error.value)


def test_env_path_is_independent_of_working_directory(tmp_path, monkeypatch):
    from app.core.config import ENV_FILE

    monkeypatch.chdir(tmp_path)
    assert ENV_FILE.is_absolute()
    assert ENV_FILE.parent.name == "server"
