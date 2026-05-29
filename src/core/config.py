"""Core configuration via Pydantic Settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_SECRET_KEY = "change-me-in-production"
_DEFAULT_ADMIN_TOKEN = "change-me-admin"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    ENVIRONMENT: Literal["development", "staging", "production", "test"] = "development"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    SECRET_KEY: str = _DEFAULT_SECRET_KEY
    ADMIN_SECRET_TOKEN: str = _DEFAULT_ADMIN_TOKEN

    MIDDLEWARE_DRY_RUN: bool = False

    DATABASE_URL: PostgresDsn
    SYNC_DATABASE_URL: PostgresDsn
    DATABASE_REPLICA_URL: PostgresDsn | None = None
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30

    REDIS_URL: RedisDsn = Field(default="redis://localhost:6379/0")
    CELERY_BROKER_URL: RedisDsn = Field(default="redis://localhost:6379/1")
    CELERY_RESULT_BACKEND: RedisDsn = Field(default="redis://localhost:6379/2")

    ODOO_URL: str
    ODOO_DB: str
    ODOO_USER: str
    ODOO_PASSWORD: str
    ODOO_TIMEOUT: int = 30

    SHOPEE_PARTNER_ID: str
    SHOPEE_PARTNER_KEY: str
    SHOPEE_SHOP_ID: str
    SHOPEE_IS_SANDBOX: bool = False

    SLACK_WEBHOOK_URL: str | None = None
    ALERT_EMAIL: str | None = None
    SENTRY_DSN: str | None = None

    DEFAULT_STOCK_BUFFER_PCT: int = 10
    DEFAULT_SHOPEE_ALLOCATION_PCT: int = 100

    PRICE_MASTER: Literal["platform", "odoo"] = "platform"
    PRICE_SYNC_INTERVAL_HOURS: int = 6

    ENABLE_AUTO_CONFIRM_ORDER: bool = True
    ENABLE_STOCK_SYNC: bool = True
    ENABLE_POLLING_FALLBACK: bool = True
    ENABLE_RECONCILIATION: bool = True
    ENABLE_PRICE_SYNC: bool = False
    POLLING_INTERVAL_MINUTES: int = 10

    CREDENTIAL_KEYS: str = ""

    ODOO_CB_FAILURE_THRESHOLD: int = 5
    ODOO_CB_OPEN_TIMEOUT: int = 60

    SHOPEE_BASE_URL_PROD: str = "https://partner.shopeemobile.com/api/v2"
    SHOPEE_BASE_URL_SANDBOX: str = "https://partner.test-stable.shopeemobile.com/api/v2"

    @property
    def shopee_base_url(self) -> str:
        return self.SHOPEE_BASE_URL_SANDBOX if self.SHOPEE_IS_SANDBOX else self.SHOPEE_BASE_URL_PROD

    @property
    def credential_keys_list(self) -> list[str]:
        return [k.strip() for k in self.CREDENTIAL_KEYS.split(",") if k.strip()]

    @model_validator(mode="after")
    def _reject_unsafe_production_defaults(self) -> "Settings":
        # `test` is exempt — test fixtures set their own sentinel-like values.
        if self.ENVIRONMENT == "test":
            return self

        strict = self.ENVIRONMENT in ("production", "staging")
        unsafe: list[str] = []
        if self.SECRET_KEY == _DEFAULT_SECRET_KEY:
            unsafe.append("SECRET_KEY (sentinel default)")
        if self.ADMIN_SECRET_TOKEN == _DEFAULT_ADMIN_TOKEN:
            unsafe.append("ADMIN_SECRET_TOKEN (sentinel default)")

        if strict:
            if len(self.SECRET_KEY) < 32:
                unsafe.append("SECRET_KEY (length < 32)")
            if len(self.ADMIN_SECRET_TOKEN) < 32:
                unsafe.append("ADMIN_SECRET_TOKEN (length < 32)")
            if len(set(self.SECRET_KEY)) < 8:
                unsafe.append("SECRET_KEY (unique chars < 8)")
            if len(set(self.ADMIN_SECRET_TOKEN)) < 8:
                unsafe.append("ADMIN_SECRET_TOKEN (unique chars < 8)")
            if not self.CREDENTIAL_KEYS.strip():
                unsafe.append("CREDENTIAL_KEYS (empty)")

        if unsafe:
            raise ValueError(
                f"Refusing to start in ENVIRONMENT={self.ENVIRONMENT} with unsafe "
                f"values for: {', '.join(unsafe)}. Set strong secrets (>= 32 chars, "
                ">= 8 unique chars, no sentinel defaults) via environment variables "
                "before launching."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
