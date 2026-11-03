"""Validated environment settings shared by the API, adapters and local scripts."""

from pathlib import Path
from typing import Annotated, Self

from pydantic import (
    AliasChoices,
    Field,
    HttpUrl,
    MongoDsn,
    SecretStr,
    TypeAdapter,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
_mongo_dsn = TypeAdapter(MongoDsn)


class EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
        populate_by_name=True,
    )


class DatabaseSettings(EnvironmentSettings):
    """Database commands do not require authentication or API credentials."""

    mongo_uri: SecretStr = Field(
        default="mongodb://localhost:27017",
        validation_alias=AliasChoices("MONGO_URI", "MONGODB_URI"),
    )
    mongodb_db_name: str = Field(default="spacos", min_length=1)
    mongodb_test_uri: SecretStr | None = None

    @field_validator("mongodb_test_uri", mode="before")
    @classmethod
    def empty_test_uri(cls, value):
        return None if value == "" else value

    @field_validator("mongo_uri", "mongodb_test_uri")
    @classmethod
    def validate_mongo_uri(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            try:
                _mongo_dsn.validate_python(value.get_secret_value())
            except ValueError:
                raise ValueError(
                    "Expected a valid mongodb:// or mongodb+srv:// URI"
                ) from None
        return value

    @field_validator("mongodb_db_name")
    @classmethod
    def validate_database_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("MONGODB_DB_NAME must not be blank")
        return value


class StartupSettings(DatabaseSettings):
    """Settings needed before launching the API, with no external API probes."""

    api_host: str = Field(default="127.0.0.1", pattern=r"^[a-zA-Z0-9_.:\-]+$")
    api_port: int = Field(default=8000, ge=1, le=65535)


class Settings(StartupSettings):
    """Application settings; optional features validate their own prerequisites."""

    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: DEFAULT_CORS_ORIGINS.split(",")
    )
    auth_enabled: bool = False
    jwt_secret: SecretStr | None = None
    external_api_timeout_seconds: float = Field(default=10, gt=0, allow_inf_nan=False)

    mapas_culturais_enabled: bool = False
    mapas_culturais_base_url: HttpUrl | None = None
    geosampa_enabled: bool = False
    geosampa_base_url: HttpUrl | None = None
    sptrans_enabled: bool = False
    sptrans_base_url: HttpUrl | None = None
    sptrans_token: SecretStr | None = None
    open_meteo_enabled: bool = False
    open_meteo_base_url: HttpUrl = "https://api.open-meteo.com"

    @field_validator(
        "jwt_secret",
        "sptrans_token",
        "mapas_culturais_base_url",
        "geosampa_base_url",
        "sptrans_base_url",
        mode="before",
    )
    @classmethod
    def empty_optional_value(cls, value):
        return None if value == "" else value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value):
        if isinstance(value, str):
            value = [origin.strip() for origin in value.split(",") if origin.strip()]
        if "*" in value:
            raise ValueError("CORS_ORIGINS must list explicit origins instead of '*'.")
        return value

    @model_validator(mode="after")
    def validate_enabled_features(self) -> Self:
        if (
            self.jwt_secret is not None
            and len(self.jwt_secret.get_secret_value().strip()) < 32
        ):
            raise ValueError("JWT_SECRET must contain at least 32 characters")
        if self.auth_enabled and self.jwt_secret is None:
            raise ValueError("AUTH_ENABLED requires JWT_SECRET")
        for feature in ("mapas_culturais", "geosampa", "sptrans"):
            if (
                getattr(self, f"{feature}_enabled")
                and getattr(self, f"{feature}_base_url") is None
            ):
                raise ValueError(f"{feature.upper()}_ENABLED requires a base URL")
        if self.sptrans_enabled and (
            self.sptrans_token is None
            or not self.sptrans_token.get_secret_value().strip()
        ):
            raise ValueError("SPTRANS_ENABLED requires SPTRANS_TOKEN")
        return self


def get_settings() -> Settings:
    """Load settings at application setup, not on each HTTP request."""
    return Settings()


def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()


def get_cors_origins() -> list[str]:
    """Keep the existing application entry point for the CORS configuration."""
    return get_settings().cors_origins
