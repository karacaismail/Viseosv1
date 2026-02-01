"""
Settings configuration for VISE OS API.

This module provides centralized configuration management using Pydantic V2 settings.
All settings are loaded from environment variables with fallback to .env file.

Usage:
    from src.api.config import get_settings

    settings = get_settings()
    directus_url = settings.DIRECTUS_URL
    api_key = settings.ANTHROPIC_API_KEY.get_secret_value()
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    Settings are organized by category:
    - Application: Core app settings
    - Database: PostgreSQL via Directus
    - Directus: CMS configuration
    - Redis: Cache and message broker
    - AI: LLM providers
    - Proxy: Residential proxy services
    - CAPTCHA: Solving services
    - SMS: Phone verification
    - Payment: Payment gateways
    - Email: Transactional email and IMAP
    - Security: Encryption and JWT
    - Monitoring: Alerts and metrics
    - Rate Limiting: Request throttling
    - Logging: Log configuration
    - Worker: Celery configuration
    - Browser: Automation settings
    """

    # -------------------------------------------------------------------------
    # Application Settings
    # -------------------------------------------------------------------------
    APP_NAME: str = "VISE OS"
    DEBUG: bool = False
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"

    # -------------------------------------------------------------------------
    # Database (PostgreSQL via Directus)
    # -------------------------------------------------------------------------
    DATABASE_URL: SecretStr = Field(
        default=SecretStr("postgresql://user:pass@localhost:5432/vise_os"),
        description="PostgreSQL connection string",
    )

    # -------------------------------------------------------------------------
    # Directus CMS
    # -------------------------------------------------------------------------
    DIRECTUS_URL: str = "http://localhost:8055"
    DIRECTUS_TOKEN: SecretStr = Field(
        default=SecretStr(""),
        description="Directus admin API token",
    )

    # -------------------------------------------------------------------------
    # Redis Configuration
    # Uses separate DBs: 0=cache, 1=broker, 2=results
    # -------------------------------------------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # -------------------------------------------------------------------------
    # AI Providers
    # -------------------------------------------------------------------------
    ANTHROPIC_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="Anthropic Claude API key (primary AI provider)",
    )
    OPENAI_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="OpenAI GPT-4 API key (optional fallback)",
    )

    # -------------------------------------------------------------------------
    # Proxy Services
    # -------------------------------------------------------------------------
    BRIGHTDATA_USERNAME: str = ""
    BRIGHTDATA_PASSWORD: SecretStr = Field(
        default=SecretStr(""),
        description="BrightData proxy password",
    )
    OXYLABS_USERNAME: str = ""
    OXYLABS_PASSWORD: SecretStr = Field(
        default=SecretStr(""),
        description="Oxylabs proxy password",
    )

    # -------------------------------------------------------------------------
    # CAPTCHA Services
    # -------------------------------------------------------------------------
    TWOCAPTCHA_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="2Captcha API key (primary solver)",
    )
    CAPSOLVER_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="CapSolver API key (backup solver)",
    )

    # -------------------------------------------------------------------------
    # SMS/Phone Verification
    # -------------------------------------------------------------------------
    FIVESIM_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="5sim.net API key for phone verification",
    )

    # -------------------------------------------------------------------------
    # Payment Processing
    # -------------------------------------------------------------------------
    IYZICO_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="iyzico payment gateway API key",
    )
    IYZICO_SECRET_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="iyzico payment gateway secret key",
    )
    IYZICO_BASE_URL: str = "https://sandbox-api.iyzipay.com"

    STRIPE_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="Stripe API key (optional fallback)",
    )
    STRIPE_WEBHOOK_SECRET: SecretStr = Field(
        default=SecretStr(""),
        description="Stripe webhook signing secret",
    )

    # -------------------------------------------------------------------------
    # Email Services
    # -------------------------------------------------------------------------
    RESEND_API_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="Resend transactional email API key",
    )
    IMAP_SERVER: str = "imap.gmail.com"
    IMAP_PORT: int = 993
    IMAP_USERNAME: str = ""
    IMAP_PASSWORD: SecretStr = Field(
        default=SecretStr(""),
        description="IMAP password for email verification",
    )

    # -------------------------------------------------------------------------
    # Security
    # -------------------------------------------------------------------------
    ENCRYPTION_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="Fernet key for PII field-level encryption",
    )
    JWT_SECRET_KEY: SecretStr = Field(
        default=SecretStr(""),
        description="JWT signing secret key (min 32 chars)",
    )
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24

    # -------------------------------------------------------------------------
    # Monitoring & Alerting
    # -------------------------------------------------------------------------
    TELEGRAM_BOT_TOKEN: SecretStr = Field(
        default=SecretStr(""),
        description="Telegram bot token for notifications",
    )
    TELEGRAM_ALERT_CHAT_ID: str = ""
    SENTRY_DSN: str = ""
    prometheus_multiproc_dir: str = "/tmp/prometheus_multiproc"

    # -------------------------------------------------------------------------
    # Rate Limiting
    # -------------------------------------------------------------------------
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 60
    RATE_LIMIT_BURST: int = 10

    # -------------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------------
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "json"

    # -------------------------------------------------------------------------
    # Worker Configuration
    # -------------------------------------------------------------------------
    CELERY_WORKER_CONCURRENCY: int = 4
    CELERY_TASK_TIME_LIMIT: int = 600

    # -------------------------------------------------------------------------
    # Browser Automation
    # -------------------------------------------------------------------------
    BROWSER_HEADLESS: bool = True
    BROWSER_TIMEOUT: int = 30000
    BROWSER_SCREENSHOT_ON_ERROR: bool = True

    # Pydantic V2 configuration - NOT class Config (V1 pattern)
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",  # Ignore extra environment variables
    )

    @field_validator("ENVIRONMENT", mode="before")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        """Ensure environment is lowercase for consistency."""
        return v.lower() if isinstance(v, str) else v

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.ENVIRONMENT == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.ENVIRONMENT == "development"

    @property
    def redis_cache_url(self) -> str:
        """Get Redis URL for cache (DB 0)."""
        return self.REDIS_URL

    @property
    def redis_broker_url(self) -> str:
        """Get Redis URL for Celery broker (DB 1)."""
        return self.CELERY_BROKER_URL

    @property
    def redis_result_url(self) -> str:
        """Get Redis URL for Celery results (DB 2)."""
        return self.CELERY_RESULT_BACKEND


@lru_cache
def get_settings() -> Settings:
    """
    Get cached application settings.

    Uses lru_cache to avoid re-reading .env file on every call.
    This is critical for performance as settings are accessed frequently.

    Returns:
        Settings: Cached application settings instance.

    Example:
        settings = get_settings()
        if settings.DEBUG:
            print("Running in debug mode")
    """
    return Settings()


def clear_settings_cache() -> None:
    """
    Clear the settings cache.

    Useful for testing or when environment changes require a refresh.

    Example:
        clear_settings_cache()
        settings = get_settings()  # Will re-read from environment
    """
    get_settings.cache_clear()
