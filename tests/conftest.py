"""
Pytest Configuration and Fixtures.

This module provides shared fixtures for all VISE OS tests.
Fixtures are organized by category and provide consistent test data
and mock objects across the test suite.

Categories:
- Settings: Configuration fixtures with test values
- Encryption: PIIEncryptor with test key
- State Machine: BookingContext and BookingStateMachine
- Directus: Mock DirectusClient for API operations
- Celery: Mock Celery app and tasks
- HTTP: AsyncClient for API testing
- Services: Mock proxy, captcha, account pool services
- Data: Sample booking, agency, applicant data

Usage:
    # Use a fixture in a test
    def test_encryption(pii_encryptor):
        encrypted = pii_encryptor.encrypt("John Doe")
        assert pii_encryptor.decrypt(encrypted) == "John Doe"

    # Use async fixtures
    async def test_directus_client(mock_directus_client):
        items = await mock_directus_client.get_items("agencies")
        assert len(items) > 0
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from typing import Any, AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient

# Set test environment before importing app modules
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("DIRECTUS_URL", "http://localhost:8055")
os.environ.setdefault("DIRECTUS_TOKEN", "test_token")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/1")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")
os.environ.setdefault("ENCRYPTION_KEY", "test_encryption_key_32_bytes_ok")
os.environ.setdefault("JWT_SECRET_KEY", "test_jwt_secret_key_min_32_chars")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_anthropic_key")
os.environ.setdefault("TWOCAPTCHA_API_KEY", "test_captcha_key")

# =============================================================================
# Event Loop Configuration
# =============================================================================


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# Settings Fixtures
# =============================================================================


@pytest.fixture
def test_settings() -> Any:
    """
    Get test settings with mocked values.

    Returns settings configured for testing with:
    - DEBUG enabled
    - Development environment
    - Mock API keys
    - Local service URLs
    """
    from src.api.config import Settings, clear_settings_cache

    # Clear any cached settings
    clear_settings_cache()

    settings = Settings(
        APP_NAME="VISE OS Test",
        DEBUG=True,
        ENVIRONMENT="development",
        DIRECTUS_URL="http://localhost:8055",
        REDIS_URL="redis://localhost:6379/0",
        CELERY_BROKER_URL="redis://localhost:6379/1",
        CELERY_RESULT_BACKEND="redis://localhost:6379/2",
        RATE_LIMIT_REQUESTS_PER_MINUTE=1000,  # High limit for tests
        BROWSER_HEADLESS=True,
    )

    return settings


@pytest.fixture
def mock_settings(test_settings: Any) -> Generator[Any, None, None]:
    """
    Patch get_settings to return test settings.

    Use this fixture when you need settings to be injected
    via dependency injection in the application.
    """
    with patch("src.api.config.get_settings", return_value=test_settings):
        yield test_settings


# =============================================================================
# Encryption Fixtures
# =============================================================================


@pytest.fixture
def test_encryption_key() -> bytes:
    """
    Get a 32-byte test encryption key.

    This key is only for testing - never use in production.
    """
    return b"test_encryption_key_32_bytes_ok"


@pytest.fixture
def pii_encryptor(test_encryption_key: bytes) -> Any:
    """
    Get a PIIEncryptor instance with test key.

    Use for testing encryption/decryption operations.

    Example:
        def test_encrypt_decrypt(pii_encryptor):
            encrypted = pii_encryptor.encrypt("test")
            assert pii_encryptor.decrypt(encrypted) == "test"
    """
    from src.core.applicant.encryption import PIIEncryptor

    return PIIEncryptor(test_encryption_key)


@pytest.fixture
def mock_encryptor(pii_encryptor: Any) -> Generator[Any, None, None]:
    """
    Patch get_encryptor to return test encryptor.

    Use when the encryptor is accessed via the global factory.
    """
    with patch("src.core.applicant.encryption.get_encryptor", return_value=pii_encryptor):
        yield pii_encryptor


# =============================================================================
# State Machine Fixtures
# =============================================================================


@pytest.fixture
def booking_state_machine() -> Any:
    """
    Get a BookingStateMachine instance.

    Use for testing state transitions and validation.
    """
    from src.core.booking.state_machine import BookingStateMachine

    return BookingStateMachine()


@pytest.fixture
def booking_context() -> Any:
    """
    Get a BookingContext with default values.

    Creates a context in PENDING state for testing.
    """
    from src.core.booking.state_machine import BookingContext, BookingState

    return BookingContext(
        booking_id=str(uuid4()),
        agency_id=str(uuid4()),
        applicant_id=str(uuid4()),
        state=BookingState.PENDING,
        max_attempts=3,
    )


@pytest.fixture
def queued_booking_context(booking_context: Any) -> Any:
    """
    Get a BookingContext in QUEUED state.

    Use for testing transitions from QUEUED state.
    """
    from src.core.booking.state_machine import BookingState

    booking_context.state = BookingState.QUEUED
    return booking_context


@pytest.fixture
def processing_booking_context(booking_context: Any) -> Any:
    """
    Get a BookingContext in PROCESSING state.

    Use for testing slot finding and booking operations.
    """
    from src.core.booking.state_machine import BookingState

    booking_context.state = BookingState.PROCESSING
    booking_context.attempt_count = 1
    booking_context.last_attempt_at = datetime.utcnow()
    return booking_context


@pytest.fixture
def completed_booking_context(booking_context: Any) -> Any:
    """
    Get a BookingContext in COMPLETED state with result data.

    Use for testing post-completion operations.
    """
    from src.core.booking.state_machine import BookingState

    booking_context.state = BookingState.COMPLETED
    booking_context.confirmation_number = "VFS-2024-123456"
    booking_context.appointment_date = "2024-06-15"
    booking_context.appointment_time = "10:30"
    return booking_context


@pytest.fixture
def retry_manager() -> Any:
    """Get a RetryManager instance."""
    from src.core.booking.state_machine import RetryManager

    return RetryManager()


# =============================================================================
# Directus Client Fixtures
# =============================================================================


@pytest.fixture
def mock_directus_client() -> AsyncMock:
    """
    Get a mock DirectusClient.

    Provides async mocks for all client methods:
    - get_items, get_item, create_item, update_item, delete_item
    - create_items, update_items, delete_items
    - count_items, aggregate

    Example:
        async def test_get_items(mock_directus_client):
            mock_directus_client.get_items.return_value = [{"id": "1", "name": "Test"}]
            items = await mock_directus_client.get_items("agencies")
            assert len(items) == 1
    """
    client = AsyncMock()

    # Default return values
    client.get_items = AsyncMock(return_value=[])
    client.get_item = AsyncMock(return_value=None)
    client.create_item = AsyncMock(return_value={"id": str(uuid4())})
    client.update_item = AsyncMock(return_value={"id": str(uuid4())})
    client.delete_item = AsyncMock(return_value=True)
    client.create_items = AsyncMock(return_value=[])
    client.update_items = AsyncMock(return_value=[])
    client.delete_items = AsyncMock(return_value=True)
    client.count_items = AsyncMock(return_value=0)
    client.aggregate = AsyncMock(return_value=[])
    client.health_check = AsyncMock(return_value=True)

    # Context manager support
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    return client


@pytest.fixture
def patch_directus_client(mock_directus_client: AsyncMock) -> Generator[AsyncMock, None, None]:
    """
    Patch get_directus_client to return mock client.

    Use when Directus client is accessed via dependency injection.
    """
    with patch(
        "src.integrations.directus.get_directus_client", return_value=mock_directus_client
    ):
        yield mock_directus_client


# =============================================================================
# Celery Fixtures
# =============================================================================


@pytest.fixture
def mock_celery_app() -> MagicMock:
    """
    Get a mock Celery application.

    Use for testing task registration and queue operations
    without requiring a Redis broker.
    """
    app = MagicMock()
    app.send_task = MagicMock(return_value=MagicMock(id=str(uuid4())))
    app.control.inspect = MagicMock()
    app.control.inspect.return_value.ping = MagicMock(return_value={"worker@test": {"ok": "pong"}})
    return app


@pytest.fixture
def mock_task() -> MagicMock:
    """
    Get a mock Celery task.

    Use for testing task behavior without execution.
    """
    task = MagicMock()
    task.delay = MagicMock(return_value=MagicMock(id=str(uuid4())))
    task.apply_async = MagicMock(return_value=MagicMock(id=str(uuid4())))
    task.s = MagicMock()
    return task


# =============================================================================
# HTTP Client Fixtures (for API testing)
# =============================================================================


@pytest.fixture
async def api_client(mock_settings: Any) -> AsyncGenerator[AsyncClient, None]:
    """
    Get an async HTTP client for API testing.

    Creates a test client connected to the FastAPI application.
    Use for testing API endpoints.

    Example:
        async def test_health_endpoint(api_client):
            response = await api_client.get("/api/health")
            assert response.status_code == 200
    """
    from src.api.main import create_application

    app = create_application()

    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client


@pytest.fixture
def api_headers() -> dict[str, str]:
    """
    Get default headers for API requests.

    Includes a test API key for authentication.
    """
    return {
        "Content-Type": "application/json",
        "X-API-Key": "test_api_key",
        "X-Request-ID": str(uuid4()),
    }


# =============================================================================
# Service Mock Fixtures
# =============================================================================


@pytest.fixture
def mock_proxy_manager() -> AsyncMock:
    """
    Get a mock ProxyManager.

    Provides mocks for proxy acquisition and rotation.
    """
    manager = AsyncMock()
    manager.acquire_proxy = AsyncMock(
        return_value={
            "id": str(uuid4()),
            "address": "http://proxy.example.com:8080",
            "country": "TR",
            "provider": "brightdata",
        }
    )
    manager.release_proxy = AsyncMock(return_value=True)
    manager.report_usage = AsyncMock(return_value=True)
    manager.get_health = AsyncMock(return_value={"healthy": 10, "unhealthy": 2})
    return manager


@pytest.fixture
def mock_captcha_solver() -> AsyncMock:
    """
    Get a mock CaptchaSolver.

    Provides mocks for CAPTCHA detection and solving.
    """
    solver = AsyncMock()
    solver.detect_captcha = AsyncMock(return_value=None)
    solver.solve = AsyncMock(return_value="captcha_token_123")
    solver.inject_token = AsyncMock(return_value=True)
    return solver


@pytest.fixture
def mock_account_pool() -> AsyncMock:
    """
    Get a mock AccountPoolManager.

    Provides mocks for account acquisition and lifecycle.
    """
    pool = AsyncMock()
    pool.acquire_account = AsyncMock(
        return_value={
            "id": str(uuid4()),
            "email": "test@example.com",
            "site": "vfs",
            "status": "active",
            "health_score": 100,
        }
    )
    pool.release_account = AsyncMock(return_value=True)
    pool.mark_banned = AsyncMock(return_value=True)
    pool.get_stats = AsyncMock(return_value={"total": 10, "active": 8, "banned": 2})
    return pool


@pytest.fixture
def mock_session_manager() -> AsyncMock:
    """
    Get a mock SessionManager.

    Provides mocks for browser session management.
    """
    manager = AsyncMock()

    # Create a mock session
    mock_session = AsyncMock()
    mock_session.id = str(uuid4())
    mock_session.is_healthy = True
    mock_session.goto = AsyncMock(return_value=True)
    mock_session.close = AsyncMock(return_value=True)

    manager.acquire = AsyncMock(return_value=mock_session)
    manager.release = AsyncMock(return_value=True)
    manager.cleanup = AsyncMock(return_value=True)
    return manager


# =============================================================================
# Sample Data Fixtures
# =============================================================================


@pytest.fixture
def sample_agency_data() -> dict[str, Any]:
    """
    Get sample agency data for testing.

    Returns a valid agency profile dictionary.
    """
    return {
        "id": str(uuid4()),
        "name": "Test Travel Agency",
        "contact_email": "contact@testagency.com",
        "contact_phone": "+905551234567",
        "status": "active",
        "subscription_tier": "professional",
        "created_at": datetime.utcnow().isoformat(),
        "settings": {
            "notification_email": True,
            "notification_telegram": False,
        },
    }


@pytest.fixture
def sample_applicant_data() -> dict[str, Any]:
    """
    Get sample applicant data for testing.

    Returns a valid applicant profile dictionary.
    Note: PII fields are not encrypted in this fixture.
    """
    return {
        "id": str(uuid4()),
        "agency_id": str(uuid4()),
        "first_name": "John",
        "last_name": "Doe",
        "email": "john.doe@example.com",
        "phone": "+905551234567",
        "passport_number": "U12345678",
        "nationality": "TR",
        "date_of_birth": "1990-01-15",
        "status": "active",
        "created_at": datetime.utcnow().isoformat(),
    }


@pytest.fixture
def sample_booking_data(sample_agency_data: dict, sample_applicant_data: dict) -> dict[str, Any]:
    """
    Get sample booking request data for testing.

    Returns a valid booking request dictionary.
    """
    return {
        "id": str(uuid4()),
        "agency_id": sample_agency_data["id"],
        "applicant_id": sample_applicant_data["id"],
        "target_system": "vfs",
        "target_country": "DE",
        "visa_type": "schengen_tourist",
        "preferred_date_start": (datetime.utcnow() + timedelta(days=14)).strftime("%Y-%m-%d"),
        "preferred_date_end": (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d"),
        "status": "pending",
        "priority": "normal",
        "created_at": datetime.utcnow().isoformat(),
    }


@pytest.fixture
def sample_slot_data() -> dict[str, Any]:
    """
    Get sample appointment slot data for testing.

    Returns a valid slot dictionary.
    """
    return {
        "date": (datetime.utcnow() + timedelta(days=14)).strftime("%Y-%m-%d"),
        "time": "10:30",
        "location": "Istanbul - Levent",
        "location_id": "istanbul-levent",
        "available": True,
        "capacity": 5,
    }


@pytest.fixture
def sample_bot_account() -> dict[str, Any]:
    """
    Get sample bot account data for testing.

    Returns a valid bot account dictionary.
    """
    return {
        "id": str(uuid4()),
        "email": "bot.account@example.com",
        "password_encrypted": "encrypted_password",
        "site": "vfs",
        "status": "active",
        "health_score": 100,
        "last_used": datetime.utcnow().isoformat(),
        "daily_usage_count": 0,
        "created_at": datetime.utcnow().isoformat(),
    }


@pytest.fixture
def sample_proxy() -> dict[str, Any]:
    """
    Get sample proxy data for testing.

    Returns a valid proxy configuration dictionary.
    """
    return {
        "id": str(uuid4()),
        "provider": "brightdata",
        "address": "http://user:pass@proxy.example.com:8080",
        "country": "TR",
        "city": "Istanbul",
        "status": "active",
        "health_score": 95,
        "success_count": 100,
        "failure_count": 5,
        "last_used": datetime.utcnow().isoformat(),
    }


# =============================================================================
# Exception Fixtures
# =============================================================================


@pytest.fixture
def vise_os_error() -> Any:
    """Get a base ViseOSError instance."""
    from src.core.exceptions import ViseOSError

    return ViseOSError("Test error", code="TEST_ERROR", details={"key": "value"})


@pytest.fixture
def site_adapter_error() -> Any:
    """Get a SiteAdapterError instance."""
    from src.core.exceptions import SiteAdapterError

    return SiteAdapterError(
        "Adapter error",
        site="vfs",
        step="login",
        details={"reason": "test"},
    )


@pytest.fixture
def account_banned_error() -> Any:
    """Get an AccountBannedError instance."""
    from src.core.exceptions import AccountBannedError

    return AccountBannedError(
        "Account banned",
        account_id=str(uuid4()),
        site="vfs",
        ban_reason="suspicious_activity",
    )


# =============================================================================
# Redis Fixtures (for integration tests)
# =============================================================================


@pytest.fixture
def mock_redis() -> AsyncMock:
    """
    Get a mock Redis client.

    Provides async mocks for common Redis operations.
    """
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    redis.ping = AsyncMock(return_value=True)
    redis.close = AsyncMock(return_value=None)
    return redis


@pytest.fixture
def patch_redis(mock_redis: AsyncMock) -> Generator[AsyncMock, None, None]:
    """
    Patch Redis client creation.

    Use when Redis is accessed via the application.
    """
    with patch("redis.asyncio.from_url", return_value=mock_redis):
        yield mock_redis


# =============================================================================
# Utility Fixtures
# =============================================================================


@pytest.fixture
def freeze_time() -> Generator[datetime, None, None]:
    """
    Freeze time for deterministic testing.

    Patches datetime.utcnow to return a fixed time.
    """
    frozen_time = datetime(2024, 6, 1, 12, 0, 0)

    with patch("datetime.datetime") as mock_datetime:
        mock_datetime.utcnow.return_value = frozen_time
        mock_datetime.now.return_value = frozen_time
        mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        yield frozen_time


@pytest.fixture
def random_uuid() -> str:
    """Get a random UUID string."""
    return str(uuid4())


@pytest.fixture
def async_mock() -> AsyncMock:
    """Get a fresh AsyncMock instance."""
    return AsyncMock()


# =============================================================================
# Cleanup Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def clear_caches() -> Generator[None, None, None]:
    """
    Clear all LRU caches before and after each test.

    Ensures tests don't affect each other through cached values.
    """
    # Import modules that have cached functions
    from src.api.config import clear_settings_cache
    from src.core.applicant.encryption import clear_encryptor_cache

    # Clear before test
    clear_settings_cache()
    clear_encryptor_cache()

    yield

    # Clear after test
    clear_settings_cache()
    clear_encryptor_cache()


# =============================================================================
# Markers and Skips
# =============================================================================


def pytest_configure(config: Any) -> None:
    """Configure custom pytest markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests requiring external services"
    )
    config.addinivalue_line(
        "markers", "e2e: marks end-to-end tests requiring full stack"
    )
    config.addinivalue_line(
        "markers", "requires_redis: marks tests requiring Redis connection"
    )
    config.addinivalue_line(
        "markers", "requires_directus: marks tests requiring Directus connection"
    )
    config.addinivalue_line(
        "markers", "requires_browser: marks tests requiring browser automation"
    )
