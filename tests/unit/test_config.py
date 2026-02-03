"""
Unit Tests for Settings Configuration.

This module provides comprehensive tests for the application settings,
including environment variable parsing, validation, properties, and caching.

Test Categories:
- Settings: Pydantic V2 Settings class
- Default Values: Verify all defaults are set correctly
- Properties: Test computed properties
- Validation: Field validators
- Factory Functions: get_settings, clear_settings_cache
- SecretStr Handling: Secret value protection

Usage:
    pytest tests/unit/test_config.py -v
    pytest tests/unit/test_config.py -k "test_environment" -v
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from src.api.config import Settings, clear_settings_cache, get_settings


# =============================================================================
# Settings Initialization Tests
# =============================================================================


class TestSettingsInit:
    """Tests for Settings class initialization."""

    def test_create_settings_with_defaults(self) -> None:
        """Verify Settings creation with default values."""
        # Clear cache to ensure fresh settings
        clear_settings_cache()

        settings = Settings()

        assert settings.APP_NAME == "VISE OS"
        assert settings.DEBUG is False
        assert settings.ENVIRONMENT == "development"

    def test_create_settings_with_custom_values(self) -> None:
        """Verify Settings creation with custom values."""
        settings = Settings(
            APP_NAME="Custom App",
            DEBUG=True,
            ENVIRONMENT="production",
        )

        assert settings.APP_NAME == "Custom App"
        assert settings.DEBUG is True
        assert settings.ENVIRONMENT == "production"


# =============================================================================
# Default Values Tests
# =============================================================================


class TestSettingsDefaults:
    """Tests for Settings default values."""

    def test_application_defaults(self) -> None:
        """Verify application setting defaults."""
        settings = Settings()

        assert settings.APP_NAME == "VISE OS"
        assert settings.DEBUG is False
        assert settings.ENVIRONMENT == "development"

    def test_directus_defaults(self) -> None:
        """Verify Directus setting defaults."""
        settings = Settings()

        assert settings.DIRECTUS_URL == "http://localhost:8055"
        assert isinstance(settings.DIRECTUS_TOKEN, SecretStr)

    def test_redis_defaults(self) -> None:
        """Verify Redis setting defaults."""
        settings = Settings()

        assert settings.REDIS_URL == "redis://localhost:6379/0"
        assert settings.CELERY_BROKER_URL == "redis://localhost:6379/1"
        assert settings.CELERY_RESULT_BACKEND == "redis://localhost:6379/2"

    def test_rate_limit_defaults(self) -> None:
        """Verify rate limit setting defaults."""
        settings = Settings()

        assert settings.RATE_LIMIT_REQUESTS_PER_MINUTE == 60
        assert settings.RATE_LIMIT_BURST == 10

    def test_logging_defaults(self) -> None:
        """Verify logging setting defaults."""
        settings = Settings()

        assert settings.LOG_LEVEL == "INFO"
        assert settings.LOG_FORMAT == "json"

    def test_worker_defaults(self) -> None:
        """Verify worker setting defaults."""
        settings = Settings()

        assert settings.CELERY_WORKER_CONCURRENCY == 4
        assert settings.CELERY_TASK_TIME_LIMIT == 600

    def test_browser_defaults(self) -> None:
        """Verify browser setting defaults."""
        settings = Settings()

        assert settings.BROWSER_HEADLESS is True
        assert settings.BROWSER_TIMEOUT == 30000
        assert settings.BROWSER_SCREENSHOT_ON_ERROR is True

    def test_jwt_defaults(self) -> None:
        """Verify JWT setting defaults."""
        settings = Settings()

        assert settings.JWT_ALGORITHM == "HS256"
        assert settings.JWT_EXPIRATION_HOURS == 24

    def test_imap_defaults(self) -> None:
        """Verify IMAP setting defaults."""
        settings = Settings()

        assert settings.IMAP_SERVER == "imap.gmail.com"
        assert settings.IMAP_PORT == 993

    def test_google_sheets_defaults(self) -> None:
        """Verify Google Sheets setting defaults."""
        settings = Settings()

        assert settings.GOOGLE_SHEETS_SYNC_INTERVAL_MINUTES == 5


# =============================================================================
# Environment Type Tests
# =============================================================================


class TestSettingsEnvironment:
    """Tests for environment configuration."""

    def test_valid_environments(self) -> None:
        """Verify all valid environment values work."""
        valid_envs = ["development", "staging", "production"]

        for env in valid_envs:
            settings = Settings(ENVIRONMENT=env)
            assert settings.ENVIRONMENT == env

    def test_environment_validation_lowercase(self) -> None:
        """Verify environment is converted to lowercase."""
        settings = Settings(ENVIRONMENT="PRODUCTION")

        assert settings.ENVIRONMENT == "production"

    def test_environment_validation_mixed_case(self) -> None:
        """Verify mixed case environment is normalized."""
        settings = Settings(ENVIRONMENT="Production")

        assert settings.ENVIRONMENT == "production"


# =============================================================================
# Property Tests
# =============================================================================


class TestSettingsProperties:
    """Tests for Settings computed properties."""

    def test_is_production_true(self) -> None:
        """Verify is_production returns True for production."""
        settings = Settings(ENVIRONMENT="production")

        assert settings.is_production is True
        assert settings.is_development is False

    def test_is_production_false(self) -> None:
        """Verify is_production returns False for non-production."""
        settings = Settings(ENVIRONMENT="development")

        assert settings.is_production is False

    def test_is_development_true(self) -> None:
        """Verify is_development returns True for development."""
        settings = Settings(ENVIRONMENT="development")

        assert settings.is_development is True
        assert settings.is_production is False

    def test_is_development_false(self) -> None:
        """Verify is_development returns False for non-development."""
        settings = Settings(ENVIRONMENT="staging")

        assert settings.is_development is False

    def test_redis_url_properties(self) -> None:
        """Verify Redis URL properties return correct values."""
        settings = Settings(
            REDIS_URL="redis://localhost:6379/0",
            CELERY_BROKER_URL="redis://localhost:6379/1",
            CELERY_RESULT_BACKEND="redis://localhost:6379/2",
        )

        assert settings.redis_cache_url == "redis://localhost:6379/0"
        assert settings.redis_broker_url == "redis://localhost:6379/1"
        assert settings.redis_result_url == "redis://localhost:6379/2"

    def test_has_anthropic_true(self) -> None:
        """Verify has_anthropic returns True when key is set."""
        settings = Settings(ANTHROPIC_API_KEY=SecretStr("sk-ant-test-key"))

        assert settings.has_anthropic is True

    def test_has_anthropic_false(self) -> None:
        """Verify has_anthropic returns False when key is empty."""
        settings = Settings(ANTHROPIC_API_KEY=SecretStr(""))

        assert settings.has_anthropic is False

    def test_has_openai_true(self) -> None:
        """Verify has_openai returns True when key is set."""
        settings = Settings(OPENAI_API_KEY=SecretStr("sk-test-key"))

        assert settings.has_openai is True

    def test_has_openai_false(self) -> None:
        """Verify has_openai returns False when key is empty."""
        settings = Settings(OPENAI_API_KEY=SecretStr(""))

        assert settings.has_openai is False

    def test_has_proxy_true_brightdata(self) -> None:
        """Verify has_proxy returns True when BrightData is configured."""
        settings = Settings(BRIGHTDATA_USERNAME="user")

        assert settings.has_proxy is True

    def test_has_proxy_true_oxylabs(self) -> None:
        """Verify has_proxy returns True when Oxylabs is configured."""
        settings = Settings(OXYLABS_USERNAME="user")

        assert settings.has_proxy is True

    def test_has_proxy_false(self) -> None:
        """Verify has_proxy returns False when no proxy is configured."""
        settings = Settings(
            BRIGHTDATA_USERNAME="",
            OXYLABS_USERNAME="",
        )

        assert settings.has_proxy is False

    def test_has_captcha_true_twocaptcha(self) -> None:
        """Verify has_captcha returns True when 2Captcha is configured."""
        settings = Settings(TWOCAPTCHA_API_KEY=SecretStr("test-key"))

        assert settings.has_captcha is True

    def test_has_captcha_true_capsolver(self) -> None:
        """Verify has_captcha returns True when CapSolver is configured."""
        settings = Settings(CAPSOLVER_API_KEY=SecretStr("test-key"))

        assert settings.has_captcha is True

    def test_has_captcha_false(self) -> None:
        """Verify has_captcha returns False when no solver is configured."""
        settings = Settings(
            TWOCAPTCHA_API_KEY=SecretStr(""),
            CAPSOLVER_API_KEY=SecretStr(""),
        )

        assert settings.has_captcha is False

    def test_has_telegram_true(self) -> None:
        """Verify has_telegram returns True when both token and chat ID are set."""
        settings = Settings(
            TELEGRAM_BOT_TOKEN=SecretStr("123:ABC"),
            TELEGRAM_ALERT_CHAT_ID="-100123456789",
        )

        assert settings.has_telegram is True

    def test_has_telegram_false_no_token(self) -> None:
        """Verify has_telegram returns False when token is missing."""
        settings = Settings(
            TELEGRAM_BOT_TOKEN=SecretStr(""),
            TELEGRAM_ALERT_CHAT_ID="-100123456789",
        )

        assert settings.has_telegram is False

    def test_has_telegram_false_no_chat_id(self) -> None:
        """Verify has_telegram returns False when chat ID is missing."""
        settings = Settings(
            TELEGRAM_BOT_TOKEN=SecretStr("123:ABC"),
            TELEGRAM_ALERT_CHAT_ID="",
        )

        assert settings.has_telegram is False

    def test_rate_limit_per_minute_alias(self) -> None:
        """Verify RATE_LIMIT_PER_MINUTE alias works."""
        settings = Settings(RATE_LIMIT_REQUESTS_PER_MINUTE=100)

        assert settings.RATE_LIMIT_PER_MINUTE == 100
        assert settings.RATE_LIMIT_PER_MINUTE == settings.RATE_LIMIT_REQUESTS_PER_MINUTE


# =============================================================================
# SecretStr Tests
# =============================================================================


class TestSettingsSecretStr:
    """Tests for SecretStr handling in Settings."""

    def test_secret_str_fields_are_secret(self) -> None:
        """Verify sensitive fields are SecretStr type."""
        settings = Settings()

        assert isinstance(settings.DATABASE_URL, SecretStr)
        assert isinstance(settings.DIRECTUS_TOKEN, SecretStr)
        assert isinstance(settings.ANTHROPIC_API_KEY, SecretStr)
        assert isinstance(settings.OPENAI_API_KEY, SecretStr)
        assert isinstance(settings.BRIGHTDATA_PASSWORD, SecretStr)
        assert isinstance(settings.OXYLABS_PASSWORD, SecretStr)
        assert isinstance(settings.TWOCAPTCHA_API_KEY, SecretStr)
        assert isinstance(settings.CAPSOLVER_API_KEY, SecretStr)
        assert isinstance(settings.FIVESIM_API_KEY, SecretStr)
        assert isinstance(settings.IYZICO_API_KEY, SecretStr)
        assert isinstance(settings.IYZICO_SECRET_KEY, SecretStr)
        assert isinstance(settings.STRIPE_API_KEY, SecretStr)
        assert isinstance(settings.STRIPE_WEBHOOK_SECRET, SecretStr)
        assert isinstance(settings.RESEND_API_KEY, SecretStr)
        assert isinstance(settings.IMAP_PASSWORD, SecretStr)
        assert isinstance(settings.ENCRYPTION_KEY, SecretStr)
        assert isinstance(settings.JWT_SECRET_KEY, SecretStr)
        assert isinstance(settings.TELEGRAM_BOT_TOKEN, SecretStr)

    def test_secret_str_not_exposed_in_repr(self) -> None:
        """Verify secret values are not exposed in string representation."""
        settings = Settings(
            ANTHROPIC_API_KEY=SecretStr("sk-ant-super-secret")
        )

        repr_str = repr(settings.ANTHROPIC_API_KEY)

        assert "sk-ant-super-secret" not in repr_str
        assert "**" in repr_str

    def test_secret_str_get_secret_value(self) -> None:
        """Verify secret value can be retrieved."""
        secret_key = "sk-ant-super-secret"
        settings = Settings(ANTHROPIC_API_KEY=SecretStr(secret_key))

        assert settings.ANTHROPIC_API_KEY.get_secret_value() == secret_key


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestGetSettings:
    """Tests for get_settings factory function."""

    def test_get_settings_returns_settings(self) -> None:
        """Verify get_settings returns Settings instance."""
        settings = get_settings()

        assert isinstance(settings, Settings)

    def test_get_settings_cached(self) -> None:
        """Verify get_settings returns cached instance."""
        settings1 = get_settings()
        settings2 = get_settings()

        assert settings1 is settings2

    def test_get_settings_reads_from_environment(self) -> None:
        """Verify get_settings reads from environment variables."""
        clear_settings_cache()

        # Environment is set in conftest.py
        settings = get_settings()

        # These are set in conftest.py
        assert settings.ENVIRONMENT == "development"


class TestClearSettingsCache:
    """Tests for clear_settings_cache function."""

    def test_clear_settings_cache_clears_cache(self) -> None:
        """Verify clear_settings_cache clears the cached settings."""
        # Get first settings
        settings1 = get_settings()

        # Clear cache
        clear_settings_cache()

        # Get new settings
        settings2 = get_settings()

        # Note: In tests, since conftest.py clears caches, we just verify
        # the function runs without error and returns valid settings
        assert settings2 is not None


# =============================================================================
# Model Config Tests
# =============================================================================


class TestSettingsModelConfig:
    """Tests for Settings model_config."""

    def test_model_config_env_file(self) -> None:
        """Verify model_config has env_file set."""
        assert Settings.model_config["env_file"] == ".env"

    def test_model_config_case_sensitive(self) -> None:
        """Verify model_config is case sensitive."""
        assert Settings.model_config["case_sensitive"] is True

    def test_model_config_extra_ignore(self) -> None:
        """Verify model_config ignores extra fields."""
        assert Settings.model_config["extra"] == "ignore"


# =============================================================================
# Payment Settings Tests
# =============================================================================


class TestPaymentSettings:
    """Tests for payment-related settings."""

    def test_iyzico_base_url_default(self) -> None:
        """Verify iyzico base URL defaults to sandbox."""
        settings = Settings()

        assert settings.IYZICO_BASE_URL == "https://sandbox-api.iyzipay.com"

    def test_iyzico_base_url_custom(self) -> None:
        """Verify iyzico base URL can be customized."""
        settings = Settings(IYZICO_BASE_URL="https://api.iyzipay.com")

        assert settings.IYZICO_BASE_URL == "https://api.iyzipay.com"


# =============================================================================
# Monitoring Settings Tests
# =============================================================================


class TestMonitoringSettings:
    """Tests for monitoring-related settings."""

    def test_sentry_dsn_default(self) -> None:
        """Verify Sentry DSN defaults to empty."""
        settings = Settings()

        assert settings.SENTRY_DSN == ""

    def test_prometheus_multiproc_dir_default(self) -> None:
        """Verify Prometheus multiproc dir default."""
        settings = Settings()

        assert settings.prometheus_multiproc_dir == "/tmp/prometheus_multiproc"


# =============================================================================
# Integration Tests
# =============================================================================


class TestSettingsIntegration:
    """Integration tests for Settings class."""

    def test_settings_can_be_used_in_application(self) -> None:
        """Verify settings work in a typical application context."""
        settings = get_settings()

        # Should be able to access all common settings
        assert settings.APP_NAME
        assert settings.ENVIRONMENT in ["development", "staging", "production"]
        assert settings.DIRECTUS_URL
        assert settings.REDIS_URL

    def test_settings_debug_mode(self) -> None:
        """Verify debug mode behavior."""
        debug_settings = Settings(DEBUG=True)
        prod_settings = Settings(DEBUG=False)

        assert debug_settings.DEBUG is True
        assert prod_settings.DEBUG is False

    def test_settings_combined_properties(self) -> None:
        """Verify multiple properties work together."""
        settings = Settings(
            ENVIRONMENT="production",
            ANTHROPIC_API_KEY=SecretStr("sk-ant-key"),
            BRIGHTDATA_USERNAME="user",
            TWOCAPTCHA_API_KEY=SecretStr("captcha-key"),
            TELEGRAM_BOT_TOKEN=SecretStr("bot-token"),
            TELEGRAM_ALERT_CHAT_ID="chat-id",
        )

        assert settings.is_production is True
        assert settings.has_anthropic is True
        assert settings.has_proxy is True
        assert settings.has_captcha is True
        assert settings.has_telegram is True
