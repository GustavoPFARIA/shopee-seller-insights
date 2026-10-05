"""Application settings loaded from environment variables (never hard-coded)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
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

    # Shopee Open Platform (optional; the integration is off unless both are set).
    shopee_partner_id: int | None = None
    shopee_partner_key: SecretStr | None = None
    # Test environment by default; production is https://partner.shopeemobile.com
    shopee_api_host: str = "https://partner.test-stable.shopeemobile.com"
    shopee_redirect_url: str = "http://localhost:8080/api/shopee/callback"
    # Push (webhook): the URL registered on the Shopee console, used in the signature,
    # and its key if the console shows one different from the partner key.
    shopee_push_url: str = "http://localhost:8080/api/shopee/push"
    shopee_push_key: SecretStr | None = None
    shopee_backfill_days: int = Field(default=90, ge=1, le=365)
    shopee_sync_interval_minutes: int = Field(default=30, ge=5, le=24 * 60)
    # Fernet key (urlsafe base64, 32 bytes) used to encrypt Shopee tokens at rest.
    token_encryption_key: SecretStr | None = None

    # E-mail (optional): invitations and the weekly digest. Off unless SMTP_HOST is set.
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str = "Shopee Seller Insights <no-reply@localhost>"
    # "starttls" (port 587), "ssl" (port 465) or "none" (local test servers only).
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    # Public URL of the web app, used to build links in e-mails.
    app_base_url: str = "http://localhost:8080"

    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-haiku-4-5"

    @field_validator(
        "shopee_partner_id",
        "shopee_partner_key",
        "shopee_push_key",
        "smtp_host",
        "smtp_username",
        "smtp_password",
        "token_encryption_key",
        "anthropic_api_key",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        """docker compose passes unset optional variables as empty strings."""
        return None if isinstance(value, str) and value.strip() == "" else value

    @model_validator(mode="after")
    def _no_dev_secrets_in_production(self) -> "Settings":
        if self.app_env == "production":
            for name in ("jwt_secret", "pii_hash_secret"):
                value: SecretStr = getattr(self, name)
                if "dev-only" in value.get_secret_value():
                    raise ValueError(f"{name.upper()} uses a dev-only default in production")
            key = self.token_encryption_key
            if key is not None and "dev-only" in key.get_secret_value():
                raise ValueError("TOKEN_ENCRYPTION_KEY uses a dev-only default in production")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
            if self.smtp_host and self.smtp_security == "none":
                raise ValueError("SMTP_SECURITY=none is not allowed in production")
        return self

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host)

    @property
    def shopee_enabled(self) -> bool:
        return (
            self.shopee_partner_id is not None
            and self.shopee_partner_key is not None
            and self.token_encryption_key is not None
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
