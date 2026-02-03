"""
Unit Tests for VISE OS Exception Hierarchy.

This module provides comprehensive tests for the exception hierarchy,
verifying proper inheritance, attributes, serialization, and error codes.

Test Categories:
- Base Exception: ViseOSError
- Site Adapter Exceptions: SiteAdapterError and subclasses
- Proxy Exceptions: ProxyError and subclasses
- CAPTCHA Exceptions: CaptchaError and subclasses
- Account Pool Exceptions: AccountPoolError and subclasses
- Browser Exceptions: BrowserError and subclasses
- Payment Exceptions: PaymentError and subclasses
- Infrastructure Exceptions: InfrastructureError and subclasses
- Network Exceptions: NetworkError and subclasses
- Verification Exceptions: VerificationError and subclasses
- Booking Exceptions: BookingError and subclasses
- AI Engine Exceptions: AIEngineError and subclasses

Usage:
    pytest tests/unit/test_exceptions.py -v
    pytest tests/unit/test_exceptions.py -k "test_vise_os_error" -v
"""

from __future__ import annotations

import pytest

from src.core.exceptions import (
    # Base
    ViseOSError,
    # Site Adapters
    AccountBannedError,
    LoginFailedError,
    NoSlotsFoundError,
    SelectorNotFoundError,
    SiteAdapterError,
    SiteStructureChangedError,
    SlotNotAvailableError,
    # Proxy
    ProxyBlockedError,
    ProxyConnectionError,
    ProxyError,
    ProxyPoolExhaustedError,
    # CAPTCHA
    CaptchaError,
    CaptchaInvalidSolutionError,
    CaptchaProviderError,
    CaptchaSolveTimeoutError,
    # Account Pool
    AccountCooldownError,
    AccountNotFoundError,
    AccountPoolError,
    AccountPoolExhaustedError,
    # Browser
    BrowserCrashError,
    BrowserError,
    BrowserTimeoutError,
    NavigationError,
    SessionError,
    # Payment
    PaymentDeclinedError,
    PaymentError,
    PaymentTimeoutError,
    ThreeDSAuthenticationError,
    # Infrastructure
    DirectusAPIError,
    DirectusRateLimitError,
    InfrastructureError,
    RedisConnectionError,
    # Network
    ConnectionResetError,
    NetworkError,
    NetworkTimeoutError,
    # Verification
    OTPExtractionError,
    VerificationError,
    VerificationTimeoutError,
    # Booking
    BookingError,
    InsufficientCreditsError,
    InvalidStateTransitionError,
    # AI Engine
    AIEngineError,
    AIProviderError,
    SelectorHealingError,
)


# =============================================================================
# ViseOSError (Base Exception) Tests
# =============================================================================


class TestViseOSError:
    """Tests for ViseOSError base exception."""

    def test_default_message(self) -> None:
        """Verify default error message."""
        error = ViseOSError()

        assert error.message == "An error occurred in VISE OS"
        assert error.code is None
        assert error.details == {}

    def test_custom_message(self) -> None:
        """Verify custom error message."""
        error = ViseOSError("Custom error message")

        assert error.message == "Custom error message"
        assert str(error) == "Custom error message"

    def test_with_code(self) -> None:
        """Verify error with code."""
        error = ViseOSError("Test error", code="TEST_ERROR")

        assert error.code == "TEST_ERROR"
        assert str(error) == "[TEST_ERROR] Test error"

    def test_with_details(self) -> None:
        """Verify error with details."""
        details = {"key": "value", "count": 42}
        error = ViseOSError("Test error", details=details)

        assert error.details == details

    def test_to_dict(self) -> None:
        """Verify error serialization to dictionary."""
        error = ViseOSError(
            "Test error",
            code="TEST_CODE",
            details={"key": "value"},
        )

        result = error.to_dict()

        assert result["error"] == "ViseOSError"
        assert result["message"] == "Test error"
        assert result["code"] == "TEST_CODE"
        assert result["details"] == {"key": "value"}

    def test_is_exception(self) -> None:
        """Verify ViseOSError is an Exception."""
        error = ViseOSError()

        assert isinstance(error, Exception)

    def test_can_be_raised(self) -> None:
        """Verify ViseOSError can be raised and caught."""
        with pytest.raises(ViseOSError) as exc_info:
            raise ViseOSError("Test error")

        assert str(exc_info.value) == "Test error"


# =============================================================================
# SiteAdapterError Tests
# =============================================================================


class TestSiteAdapterError:
    """Tests for SiteAdapterError and subclasses."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = SiteAdapterError()

        assert error.message == "Site adapter error"
        assert error.code == "ADAPTER_ERROR"
        assert error.site is None
        assert error.step is None

    def test_with_site_and_step(self) -> None:
        """Verify error with site and step."""
        error = SiteAdapterError(
            "Login failed",
            site="vfs",
            step="login",
        )

        assert error.site == "vfs"
        assert error.step == "login"
        assert error.details["site"] == "vfs"
        assert error.details["step"] == "login"

    def test_inherits_from_vise_os_error(self) -> None:
        """Verify inheritance from ViseOSError."""
        error = SiteAdapterError()

        assert isinstance(error, ViseOSError)


class TestAccountBannedError:
    """Tests for AccountBannedError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = AccountBannedError()

        assert "banned" in error.message.lower()
        assert error.code == "ACCOUNT_BANNED"
        assert error.account_id is None
        assert error.ban_reason is None

    def test_with_account_id_and_reason(self) -> None:
        """Verify error with account ID and reason."""
        error = AccountBannedError(
            account_id="acc-123",
            ban_reason="suspicious_activity",
            site="vfs",
        )

        assert error.account_id == "acc-123"
        assert error.ban_reason == "suspicious_activity"
        assert error.site == "vfs"
        assert error.details["account_id"] == "acc-123"
        assert error.details["ban_reason"] == "suspicious_activity"

    def test_inherits_from_site_adapter_error(self) -> None:
        """Verify inheritance from SiteAdapterError."""
        error = AccountBannedError()

        assert isinstance(error, SiteAdapterError)
        assert isinstance(error, ViseOSError)


class TestSlotNotAvailableError:
    """Tests for SlotNotAvailableError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = SlotNotAvailableError()

        assert "not available" in error.message.lower()
        assert error.code == "SLOT_NOT_AVAILABLE"

    def test_with_slot_details(self) -> None:
        """Verify error with slot details."""
        error = SlotNotAvailableError(
            slot_id="slot-123",
            slot_datetime="2024-06-15 10:30",
            site="vfs",
        )

        assert error.slot_id == "slot-123"
        assert error.slot_datetime == "2024-06-15 10:30"


class TestSiteStructureChangedError:
    """Tests for SiteStructureChangedError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = SiteStructureChangedError()

        assert "structure" in error.message.lower()
        assert error.code == "SITE_STRUCTURE_CHANGED"

    def test_with_selector_details(self) -> None:
        """Verify error with selector details."""
        error = SiteStructureChangedError(
            selector="#login-button",
            expected_element="Login button",
            site="vfs",
        )

        assert error.selector == "#login-button"
        assert error.expected_element == "Login button"


class TestLoginFailedError:
    """Tests for LoginFailedError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = LoginFailedError()

        assert "login" in error.message.lower()
        assert error.code == "LOGIN_FAILED"

    def test_with_reason(self) -> None:
        """Verify error with reason."""
        error = LoginFailedError(
            account_id="acc-123",
            reason="Invalid credentials",
            site="vfs",
        )

        assert error.account_id == "acc-123"
        assert error.reason == "Invalid credentials"


class TestNoSlotsFoundError:
    """Tests for NoSlotsFoundError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = NoSlotsFoundError()

        assert "no" in error.message.lower() and "slots" in error.message.lower()
        assert error.code == "NO_SLOTS_FOUND"

    def test_with_search_criteria(self) -> None:
        """Verify error with search criteria."""
        criteria = {"country": "DE", "date": "2024-06"}
        error = NoSlotsFoundError(
            search_criteria=criteria,
            site="vfs",
        )

        assert error.search_criteria == criteria


# =============================================================================
# ProxyError Tests
# =============================================================================


class TestProxyError:
    """Tests for ProxyError and subclasses."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = ProxyError()

        assert error.code == "PROXY_ERROR"
        assert error.proxy_address is None
        assert error.provider is None

    def test_with_proxy_details(self) -> None:
        """Verify error with proxy details."""
        error = ProxyError(
            "Proxy connection failed",
            proxy_address="http://proxy:8080",
            provider="brightdata",
        )

        assert error.proxy_address == "http://proxy:8080"
        assert error.provider == "brightdata"

    def test_inherits_from_vise_os_error(self) -> None:
        """Verify inheritance from ViseOSError."""
        error = ProxyError()

        assert isinstance(error, ViseOSError)


class TestProxyBlockedError:
    """Tests for ProxyBlockedError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = ProxyBlockedError()

        assert error.code == "PROXY_BLOCKED"
        assert error.blocked_by is None
        assert error.cooldown_seconds == 3600

    def test_with_blocked_details(self) -> None:
        """Verify error with blocked details."""
        error = ProxyBlockedError(
            blocked_by="vfs",
            cooldown_seconds=7200,
            proxy_address="http://proxy:8080",
        )

        assert error.blocked_by == "vfs"
        assert error.cooldown_seconds == 7200


class TestProxyPoolExhaustedError:
    """Tests for ProxyPoolExhaustedError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = ProxyPoolExhaustedError()

        assert error.code == "PROXY_POOL_EXHAUSTED"

    def test_with_country(self) -> None:
        """Verify error with country."""
        error = ProxyPoolExhaustedError(
            country="TR",
            provider="brightdata",
        )

        assert error.country == "TR"


# =============================================================================
# CaptchaError Tests
# =============================================================================


class TestCaptchaError:
    """Tests for CaptchaError and subclasses."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = CaptchaError()

        assert error.code == "CAPTCHA_ERROR"
        assert error.captcha_type is None
        assert error.provider is None

    def test_with_captcha_details(self) -> None:
        """Verify error with captcha details."""
        error = CaptchaError(
            "CAPTCHA solve failed",
            captcha_type="recaptcha_v2",
            provider="2captcha",
        )

        assert error.captcha_type == "recaptcha_v2"
        assert error.provider == "2captcha"


class TestCaptchaSolveTimeoutError:
    """Tests for CaptchaSolveTimeoutError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = CaptchaSolveTimeoutError()

        assert error.code == "CAPTCHA_TIMEOUT"
        assert error.attempts == 1

    def test_with_timeout_details(self) -> None:
        """Verify error with timeout details."""
        error = CaptchaSolveTimeoutError(
            timeout_seconds=120,
            attempts=3,
            captcha_type="turnstile",
        )

        assert error.timeout_seconds == 120
        assert error.attempts == 3


# =============================================================================
# AccountPoolError Tests
# =============================================================================


class TestAccountPoolError:
    """Tests for AccountPoolError and subclasses."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = AccountPoolError()

        assert error.code == "ACCOUNT_POOL_ERROR"
        assert error.site is None

    def test_with_site(self) -> None:
        """Verify error with site."""
        error = AccountPoolError("Pool empty", site="vfs")

        assert error.site == "vfs"


class TestAccountPoolExhaustedError:
    """Tests for AccountPoolExhaustedError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = AccountPoolExhaustedError()

        assert error.code == "ACCOUNT_POOL_EXHAUSTED"


class TestAccountCooldownError:
    """Tests for AccountCooldownError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = AccountCooldownError()

        assert error.code == "ACCOUNT_COOLDOWN"

    def test_with_cooldown_details(self) -> None:
        """Verify error with cooldown details."""
        error = AccountCooldownError(
            account_id="acc-123",
            cooldown_until="2024-06-01T12:00:00",
            site="vfs",
        )

        assert error.account_id == "acc-123"
        assert error.cooldown_until == "2024-06-01T12:00:00"


# =============================================================================
# BrowserError Tests
# =============================================================================


class TestBrowserError:
    """Tests for BrowserError and subclasses."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = BrowserError()

        assert error.code == "BROWSER_ERROR"
        assert error.session_id is None

    def test_with_session_id(self) -> None:
        """Verify error with session ID."""
        error = BrowserError("Browser crashed", session_id="sess-123")

        assert error.session_id == "sess-123"


class TestBrowserCrashError:
    """Tests for BrowserCrashError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = BrowserCrashError()

        assert error.code == "BROWSER_CRASH"

    def test_with_crash_reason(self) -> None:
        """Verify error with crash reason."""
        error = BrowserCrashError(
            crash_reason="Out of memory",
            session_id="sess-123",
        )

        assert error.crash_reason == "Out of memory"


class TestBrowserTimeoutError:
    """Tests for BrowserTimeoutError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = BrowserTimeoutError()

        assert error.code == "BROWSER_TIMEOUT"

    def test_with_timeout_details(self) -> None:
        """Verify error with timeout details."""
        error = BrowserTimeoutError(
            operation="wait_for_selector",
            timeout_ms=30000,
            session_id="sess-123",
        )

        assert error.operation == "wait_for_selector"
        assert error.timeout_ms == 30000


class TestNavigationError:
    """Tests for NavigationError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = NavigationError()

        assert error.code == "NAVIGATION_ERROR"

    def test_with_url_and_status(self) -> None:
        """Verify error with URL and status code."""
        error = NavigationError(
            url="https://example.com/page",
            status_code=404,
            session_id="sess-123",
        )

        assert error.url == "https://example.com/page"
        assert error.status_code == 404


# =============================================================================
# PaymentError Tests
# =============================================================================


class TestPaymentError:
    """Tests for PaymentError and subclasses."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = PaymentError()

        assert error.code == "PAYMENT_ERROR"
        assert error.booking_id is None
        assert error.gateway is None

    def test_with_payment_details(self) -> None:
        """Verify error with payment details."""
        error = PaymentError(
            "Payment failed",
            booking_id="booking-123",
            gateway="iyzico",
        )

        assert error.booking_id == "booking-123"
        assert error.gateway == "iyzico"


class TestThreeDSAuthenticationError:
    """Tests for ThreeDSAuthenticationError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = ThreeDSAuthenticationError()

        assert error.code == "THREE_DS_FAILED"

    def test_with_3ds_details(self) -> None:
        """Verify error with 3DS details."""
        error = ThreeDSAuthenticationError(
            acs_url="https://bank.com/3ds",
            reason="User cancelled",
            booking_id="booking-123",
        )

        assert error.acs_url == "https://bank.com/3ds"
        assert error.reason == "User cancelled"


class TestPaymentDeclinedError:
    """Tests for PaymentDeclinedError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = PaymentDeclinedError()

        assert error.code == "PAYMENT_DECLINED"

    def test_with_decline_details(self) -> None:
        """Verify error with decline details."""
        error = PaymentDeclinedError(
            decline_code="INSUFFICIENT_FUNDS",
            decline_reason="Not enough balance",
            gateway="iyzico",
        )

        assert error.decline_code == "INSUFFICIENT_FUNDS"
        assert error.decline_reason == "Not enough balance"


# =============================================================================
# InfrastructureError Tests
# =============================================================================


class TestInfrastructureError:
    """Tests for InfrastructureError and subclasses."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = InfrastructureError()

        assert error.code == "INFRASTRUCTURE_ERROR"
        assert error.service is None

    def test_with_service(self) -> None:
        """Verify error with service."""
        error = InfrastructureError("Service down", service="redis")

        assert error.service == "redis"


class TestRedisConnectionError:
    """Tests for RedisConnectionError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = RedisConnectionError()

        assert error.code == "REDIS_CONNECTION_ERROR"
        assert error.service == "redis"

    def test_masks_password_in_url(self) -> None:
        """Verify password is masked in URL."""
        error = RedisConnectionError(
            redis_url="redis://user:secret_pass@localhost:6379/0"
        )

        assert "secret_pass" not in error.details["redis_url"]
        assert "***" in error.details["redis_url"]


class TestDirectusAPIError:
    """Tests for DirectusAPIError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = DirectusAPIError()

        assert error.code == "DIRECTUS_API_ERROR"
        assert error.service == "directus"

    def test_with_endpoint_and_status(self) -> None:
        """Verify error with endpoint and status code."""
        error = DirectusAPIError(
            endpoint="/items/agencies",
            status_code=500,
        )

        assert error.endpoint == "/items/agencies"
        assert error.status_code == 500


class TestDirectusRateLimitError:
    """Tests for DirectusRateLimitError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = DirectusRateLimitError()

        assert error.code == "DIRECTUS_RATE_LIMIT"
        assert error.status_code == 429

    def test_with_retry_after(self) -> None:
        """Verify error with retry_after."""
        error = DirectusRateLimitError(
            retry_after=60,
            endpoint="/items/bookings",
        )

        assert error.retry_after == 60


# =============================================================================
# NetworkError Tests
# =============================================================================


class TestNetworkError:
    """Tests for NetworkError and subclasses."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = NetworkError()

        assert error.code == "NETWORK_ERROR"
        assert error.url is None

    def test_with_url(self) -> None:
        """Verify error with URL."""
        error = NetworkError("Connection failed", url="https://example.com")

        assert error.url == "https://example.com"


class TestNetworkTimeoutError:
    """Tests for NetworkTimeoutError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = NetworkTimeoutError()

        assert error.code == "NETWORK_TIMEOUT"

    def test_with_timeout(self) -> None:
        """Verify error with timeout."""
        error = NetworkTimeoutError(
            timeout_seconds=30,
            url="https://example.com",
        )

        assert error.timeout_seconds == 30


# =============================================================================
# VerificationError Tests
# =============================================================================


class TestVerificationError:
    """Tests for VerificationError and subclasses."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = VerificationError()

        assert error.code == "VERIFICATION_ERROR"
        assert error.verification_type is None

    def test_with_type(self) -> None:
        """Verify error with verification type."""
        error = VerificationError("OTP failed", verification_type="sms")

        assert error.verification_type == "sms"


class TestOTPExtractionError:
    """Tests for OTPExtractionError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = OTPExtractionError()

        assert error.code == "OTP_EXTRACTION_ERROR"

    def test_with_source(self) -> None:
        """Verify error with source."""
        error = OTPExtractionError(
            source="email from vfs@service.com",
            verification_type="email",
        )

        assert error.source == "email from vfs@service.com"


# =============================================================================
# BookingError Tests
# =============================================================================


class TestBookingError:
    """Tests for BookingError and subclasses."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = BookingError()

        assert error.code == "BOOKING_ERROR"
        assert error.booking_id is None

    def test_with_booking_id(self) -> None:
        """Verify error with booking ID."""
        error = BookingError("Booking failed", booking_id="booking-123")

        assert error.booking_id == "booking-123"


class TestInvalidStateTransitionError:
    """Tests for InvalidStateTransitionError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = InvalidStateTransitionError()

        assert error.code == "INVALID_STATE_TRANSITION"

    def test_with_state_details(self) -> None:
        """Verify error with state details."""
        error = InvalidStateTransitionError(
            current_state="pending",
            target_state="completed",
            allowed_states=["queued", "cancelled"],
            booking_id="booking-123",
        )

        assert error.current_state == "pending"
        assert error.target_state == "completed"
        assert error.allowed_states == ["queued", "cancelled"]


class TestInsufficientCreditsError:
    """Tests for InsufficientCreditsError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = InsufficientCreditsError()

        assert error.code == "INSUFFICIENT_CREDITS"

    def test_with_credit_details(self) -> None:
        """Verify error with credit details."""
        error = InsufficientCreditsError(
            agency_id="agency-123",
            required_credits=10.0,
            available_credits=5.0,
            booking_id="booking-123",
        )

        assert error.agency_id == "agency-123"
        assert error.required_credits == 10.0
        assert error.available_credits == 5.0


# =============================================================================
# AIEngineError Tests
# =============================================================================


class TestAIEngineError:
    """Tests for AIEngineError and subclasses."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = AIEngineError()

        assert error.code == "AI_ENGINE_ERROR"
        assert error.provider is None

    def test_with_provider(self) -> None:
        """Verify error with provider."""
        error = AIEngineError("AI failed", provider="anthropic")

        assert error.provider == "anthropic"


class TestAIProviderError:
    """Tests for AIProviderError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = AIProviderError()

        assert error.code == "AI_PROVIDER_ERROR"

    def test_with_provider_details(self) -> None:
        """Verify error with provider details."""
        error = AIProviderError(
            provider="anthropic",
            provider_error="Rate limit exceeded",
            status_code=429,
        )

        assert error.provider_error == "Rate limit exceeded"
        assert error.status_code == 429


class TestSelectorHealingError:
    """Tests for SelectorHealingError."""

    def test_default_values(self) -> None:
        """Verify default values."""
        error = SelectorHealingError()

        assert error.code == "SELECTOR_HEALING_ERROR"
        assert error.attempts == 1

    def test_with_healing_details(self) -> None:
        """Verify error with healing details."""
        error = SelectorHealingError(
            selector="#old-selector",
            attempts=3,
            provider="anthropic",
        )

        assert error.selector == "#old-selector"
        assert error.attempts == 3


# =============================================================================
# Inheritance Tests
# =============================================================================


class TestExceptionInheritance:
    """Tests for exception hierarchy inheritance."""

    def test_all_exceptions_inherit_from_vise_os_error(self) -> None:
        """Verify all custom exceptions inherit from ViseOSError."""
        exceptions = [
            SiteAdapterError(),
            AccountBannedError(),
            ProxyError(),
            ProxyBlockedError(),
            CaptchaError(),
            CaptchaSolveTimeoutError(),
            AccountPoolError(),
            AccountPoolExhaustedError(),
            BrowserError(),
            BrowserCrashError(),
            PaymentError(),
            ThreeDSAuthenticationError(),
            InfrastructureError(),
            RedisConnectionError(),
            NetworkError(),
            NetworkTimeoutError(),
            VerificationError(),
            OTPExtractionError(),
            BookingError(),
            InvalidStateTransitionError(),
            AIEngineError(),
            AIProviderError(),
        ]

        for exception in exceptions:
            assert isinstance(
                exception, ViseOSError
            ), f"{type(exception).__name__} should inherit from ViseOSError"

    def test_all_exceptions_can_be_caught_as_vise_os_error(self) -> None:
        """Verify all exceptions can be caught as ViseOSError."""
        exceptions_to_test = [
            lambda: AccountBannedError("test"),
            lambda: ProxyBlockedError("test"),
            lambda: CaptchaSolveTimeoutError("test"),
            lambda: BrowserCrashError("test"),
            lambda: PaymentDeclinedError("test"),
            lambda: RedisConnectionError("test"),
            lambda: InvalidStateTransitionError("test"),
        ]

        for exc_factory in exceptions_to_test:
            with pytest.raises(ViseOSError):
                raise exc_factory()

    def test_site_adapter_subclasses(self) -> None:
        """Verify SiteAdapterError subclasses."""
        subclasses = [
            AccountBannedError(),
            SlotNotAvailableError(),
            SiteStructureChangedError(),
            SelectorNotFoundError(),
            LoginFailedError(),
            NoSlotsFoundError(),
        ]

        for exc in subclasses:
            assert isinstance(
                exc, SiteAdapterError
            ), f"{type(exc).__name__} should inherit from SiteAdapterError"

    def test_proxy_subclasses(self) -> None:
        """Verify ProxyError subclasses."""
        subclasses = [
            ProxyBlockedError(),
            ProxyPoolExhaustedError(),
            ProxyConnectionError(),
        ]

        for exc in subclasses:
            assert isinstance(
                exc, ProxyError
            ), f"{type(exc).__name__} should inherit from ProxyError"

    def test_captcha_subclasses(self) -> None:
        """Verify CaptchaError subclasses."""
        subclasses = [
            CaptchaSolveTimeoutError(),
            CaptchaProviderError(),
            CaptchaInvalidSolutionError(),
        ]

        for exc in subclasses:
            assert isinstance(
                exc, CaptchaError
            ), f"{type(exc).__name__} should inherit from CaptchaError"

    def test_browser_subclasses(self) -> None:
        """Verify BrowserError subclasses."""
        subclasses = [
            BrowserCrashError(),
            BrowserTimeoutError(),
            SessionError(),
            NavigationError(),
        ]

        for exc in subclasses:
            assert isinstance(
                exc, BrowserError
            ), f"{type(exc).__name__} should inherit from BrowserError"

    def test_payment_subclasses(self) -> None:
        """Verify PaymentError subclasses."""
        subclasses = [
            ThreeDSAuthenticationError(),
            PaymentDeclinedError(),
            PaymentTimeoutError(),
        ]

        for exc in subclasses:
            assert isinstance(
                exc, PaymentError
            ), f"{type(exc).__name__} should inherit from PaymentError"


# =============================================================================
# Serialization Tests
# =============================================================================


class TestExceptionSerialization:
    """Tests for exception serialization."""

    def test_to_dict_includes_all_fields(self) -> None:
        """Verify to_dict includes all required fields."""
        error = AccountBannedError(
            "Account was banned",
            account_id="acc-123",
            site="vfs",
            ban_reason="suspicious",
        )

        result = error.to_dict()

        assert "error" in result
        assert "message" in result
        assert "code" in result
        assert "details" in result
        assert result["details"]["account_id"] == "acc-123"
        assert result["details"]["site"] == "vfs"
        assert result["details"]["ban_reason"] == "suspicious"

    def test_to_dict_can_be_serialized_to_json(self) -> None:
        """Verify to_dict output can be serialized to JSON."""
        import json

        error = PaymentDeclinedError(
            decline_code="CARD_EXPIRED",
            decline_reason="Card has expired",
        )

        result = error.to_dict()

        # Should not raise
        json_str = json.dumps(result)
        assert json_str is not None

        # Should be deserializable
        parsed = json.loads(json_str)
        assert parsed["code"] == "PAYMENT_DECLINED"
