"""Application settings loaded from environment variables (never hard-coded)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = Field(
        default="postgresql+psycopg://app_user:app_password@localhost:5432/shopee_insights"
    )
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = Field(default=15, ge=1, le=24 * 60)
    refresh_token_days: int = Field(default=7, ge=1, le=90)
    # Secure cookies require HTTPS; only disable for a local http:// demo.
    cookie_secure: bool = True
    # Secret used to pseudonymize buyer identifiers (HMAC) on import.
    pii_hash_secret: SecretStr = Field(min_length=32)

    # IANA zone used to group sales by calendar day (resolved by PostgreSQL).
    report_timezone: str = Field(
        default="America/Sao_Paulo", pattern=r"^[A-Za-z_]+(/[A-Za-z_+-]+)*$"
    )

    cors_origins: list[str] = Field(default=["http://localhost:8080", "http://localhost:5173"])

    max_upload_mb: int = Field(default=5, ge=1, le=50)
    max_upload_rows: int = Field(default=50_000, ge=1)

    login_rate_limit: int = Field(default=5, ge=1)
    login_rate_window_seconds: int = Field(default=60, ge=1)
    upload_rate_limit: int = Field(default=10, ge=1)
    upload_rate_window_seconds: int = Field(default=3600, ge=1)

    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-haiku-4-5"

    @model_validator(mode="after")
    def _no_dev_secrets_in_production(self) -> "Settings":
        if self.app_env == "production":
            for name in ("jwt_secret", "pii_hash_secret"):
                value: SecretStr = getattr(self, name)
                if "dev-only" in value.get_secret_value():
                    raise ValueError(f"{name.upper()} uses a dev-only default in production")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
