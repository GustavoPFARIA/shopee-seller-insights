"""Application settings loaded from environment variables (never hard-coded)."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://app_user:app_password@localhost:5432/shopee_insights"
    )
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = Field(default=30, ge=1, le=24 * 60)
    # Secret used to pseudonymize buyer identifiers (HMAC) on import.
    pii_hash_secret: SecretStr = Field(min_length=32)

    cors_origins: list[str] = Field(default=["http://localhost:5173"])

    max_upload_mb: int = Field(default=5, ge=1, le=50)
    max_upload_rows: int = Field(default=50_000, ge=1)

    login_rate_limit: int = Field(default=5, ge=1)
    login_rate_window_seconds: int = Field(default=60, ge=1)
    upload_rate_limit: int = Field(default=10, ge=1)
    upload_rate_window_seconds: int = Field(default=3600, ge=1)

    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-haiku-4-5"


@lru_cache
def get_settings() -> Settings:
    return Settings()
