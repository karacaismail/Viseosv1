"""
VISE OS Exception Hierarchy.

This module provides a comprehensive exception hierarchy for the VISE OS platform.
All exceptions inherit from ViseOSError as the base class, with specialized
exceptions for each failure mode in the system.

Exception Categories:
- Base: ViseOSError (root of all exceptions)
- Site Adapters: Site-specific booking failures
- Proxy: Proxy rotation and blocking
- CAPTCHA: CAPTCHA solving failures
- Account Pool: Bot account management
- Browser: Browser automation failures
- Payment: Payment processing errors
- Infrastructure: Redis, Directus, network failures

Usage:
    from src.core.exceptions import (
        ViseOSError,
        SiteAdapterError,
        AccountBannedError,
    )

    try:
        await adapter.login(account)
    except AccountBannedError as e:
        logger.error("account_banned", account_id=e.account_id, site=e.site)
        await account_pool.mark_banned(e.account_id)
    except SiteAdapterError as e:
        logger.error("adapter_error", site=e.site, message=str(e))
"""

from typing import Any


# =============================================================================
# Base Exception
# =============================================================================


class ViseOSError(Exception):
    """
    Base exception for all VISE OS errors.

    All custom exceptions in the platform inherit from this class.
    This allows catching all platform-specific errors with a single handler.

    Attributes:
        message: Human-readable error description.
        code: Optional error code for categorization.
        details: Optional dictionary with additional error context.

    Example:
        try:
            await process_booking(booking_id)
        except ViseOSError as e:
            logger.error("booking_failed", error=str(e), code=e.code)
    """

    def __init__(
        self,
        message: str = "An error occurred in VISE OS",
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(message)

    def __str__(self) -> str:
        if self.code:
            return f"[{self.code}] {self.message}"
        return self.message

    def to_dict(self) -> dict[str, Any]:
        """Convert exception to dictionary for logging and API responses."""
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "code": self.code,
            "details": self.details,
        }


# =============================================================================
# Site Adapter Exceptions
# =============================================================================


class SiteAdapterError(ViseOSError):
    """
    Base exception for site adapter errors.

    Raised when a site-specific operation fails during the booking flow.
    This includes login failures, navigation errors, and form submission issues.

    Attributes:
        site: The site code where the error occurred (e.g., "vfs", "idata").
        step: The step in the booking flow where the error occurred.
    """

    def __init__(
        self,
        message: str = "Site adapter error",
        *,
        site: str | None = None,
        step: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.site = site
        self.step = step
        details = details or {}
        if site:
            details["site"] = site
        if step:
            details["step"] = step
        super().__init__(message, code=code or "ADAPTER_ERROR", details=details)


class AccountBannedError(SiteAdapterError):
    """
    Raised when a bot account is banned by the target site.

    This error triggers automatic account rotation and marks the account
    as banned in the account pool. The booking flow should acquire a new
    account and retry.

    Attributes:
        account_id: The ID of the banned account.
        ban_reason: Optional reason for the ban if available.
    """

    def __init__(
        self,
        message: str = "Account has been banned by the site",
        *,
        account_id: str | None = None,
        ban_reason: str | None = None,
        site: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.account_id = account_id
        self.ban_reason = ban_reason
        details = details or {}
        if account_id:
            details["account_id"] = account_id
        if ban_reason:
            details["ban_reason"] = ban_reason
        super().__init__(
            message, site=site, code="ACCOUNT_BANNED", details=details
        )


class SlotNotAvailableError(SiteAdapterError):
    """
    Raised when the requested appointment slot is no longer available.

    This error occurs when another user books the slot before the booking
    can be completed. The booking flow should transition back to slot search
    and find an alternative.

    Attributes:
        slot_id: The ID of the unavailable slot.
        slot_datetime: The datetime of the requested slot.
    """

    def __init__(
        self,
        message: str = "Requested slot is no longer available",
        *,
        slot_id: str | None = None,
        slot_datetime: str | None = None,
        site: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.slot_id = slot_id
        self.slot_datetime = slot_datetime
        details = details or {}
        if slot_id:
            details["slot_id"] = slot_id
        if slot_datetime:
            details["slot_datetime"] = slot_datetime
        super().__init__(
            message, site=site, code="SLOT_NOT_AVAILABLE", details=details
        )


class SiteStructureChangedError(SiteAdapterError):
    """
    Raised when the target site's HTML structure has changed.

    This error indicates that CSS selectors are no longer valid and
    the AI selector healer should attempt to recover. If recovery fails,
    an alert should be raised for manual intervention.

    Attributes:
        selector: The CSS selector that failed.
        expected_element: Description of the expected element.
    """

    def __init__(
        self,
        message: str = "Site structure has changed, selectors may be invalid",
        *,
        selector: str | None = None,
        expected_element: str | None = None,
        site: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.selector = selector
        self.expected_element = expected_element
        details = details or {}
        if selector:
            details["selector"] = selector
        if expected_element:
            details["expected_element"] = expected_element
        super().__init__(
            message, site=site, code="SITE_STRUCTURE_CHANGED", details=details
        )


class SelectorNotFoundError(SiteAdapterError):
    """
    Raised when a required CSS selector cannot be found on the page.

    This may indicate a site structure change or a navigation error
    where the expected page was not loaded.

    Attributes:
        selector: The CSS selector that was not found.
        page_url: The URL of the page where the selector was expected.
    """

    def __init__(
        self,
        message: str = "Required selector not found on page",
        *,
        selector: str | None = None,
        page_url: str | None = None,
        site: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.selector = selector
        self.page_url = page_url
        details = details or {}
        if selector:
            details["selector"] = selector
        if page_url:
            details["page_url"] = page_url
        super().__init__(
            message, site=site, code="SELECTOR_NOT_FOUND", details=details
        )


class LoginFailedError(SiteAdapterError):
    """
    Raised when login to a target site fails.

    This may indicate invalid credentials, CAPTCHA requirements,
    or account restrictions.

    Attributes:
        account_id: The ID of the account that failed to log in.
        reason: The reason for the login failure if available.
    """

    def __init__(
        self,
        message: str = "Failed to log in to site",
        *,
        account_id: str | None = None,
        reason: str | None = None,
        site: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.account_id = account_id
        self.reason = reason
        details = details or {}
        if account_id:
            details["account_id"] = account_id
        if reason:
            details["reason"] = reason
        super().__init__(
            message, site=site, code="LOGIN_FAILED", details=details
        )


class NoSlotsFoundError(SiteAdapterError):
    """
    Raised when no appointment slots match the search criteria.

    This is expected behavior when no slots are available. The booking
    flow may retry the search or wait for new slots to become available.

    Attributes:
        search_criteria: Summary of the search parameters used.
    """

    def __init__(
        self,
        message: str = "No available slots found matching criteria",
        *,
        search_criteria: dict[str, Any] | None = None,
        site: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.search_criteria = search_criteria
        details = details or {}
        if search_criteria:
            details["search_criteria"] = search_criteria
        super().__init__(
            message, site=site, code="NO_SLOTS_FOUND", details=details
        )


# =============================================================================
# Proxy Exceptions
# =============================================================================


class ProxyError(ViseOSError):
    """
    Base exception for proxy-related errors.

    Raised when proxy operations fail, including rotation failures,
    connection issues, or proxy blocking.

    Attributes:
        proxy_address: The proxy address that caused the error.
        provider: The proxy provider (e.g., "brightdata", "oxylabs").
    """

    def __init__(
        self,
        message: str = "Proxy error occurred",
        *,
        proxy_address: str | None = None,
        provider: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.proxy_address = proxy_address
        self.provider = provider
        details = details or {}
        if proxy_address:
            details["proxy_address"] = proxy_address
        if provider:
            details["provider"] = provider
        super().__init__(message, code=code or "PROXY_ERROR", details=details)


class ProxyBlockedError(ProxyError):
    """
    Raised when a proxy is blocked by the target site.

    The proxy should be marked for cooldown and rotated to a new one.

    Attributes:
        blocked_by: The site that blocked the proxy.
        cooldown_seconds: Suggested cooldown time before retry.
    """

    def __init__(
        self,
        message: str = "Proxy has been blocked by the target site",
        *,
        proxy_address: str | None = None,
        provider: str | None = None,
        blocked_by: str | None = None,
        cooldown_seconds: int = 3600,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.blocked_by = blocked_by
        self.cooldown_seconds = cooldown_seconds
        details = details or {}
        if blocked_by:
            details["blocked_by"] = blocked_by
        details["cooldown_seconds"] = cooldown_seconds
        super().__init__(
            message,
            proxy_address=proxy_address,
            provider=provider,
            code="PROXY_BLOCKED",
            details=details,
        )


class ProxyPoolExhaustedError(ProxyError):
    """
    Raised when no healthy proxies are available in the pool.

    This is a critical error indicating that all proxies are either
    blocked or in cooldown. Operations should be paused until proxies
    become available.
    """

    def __init__(
        self,
        message: str = "No healthy proxies available in pool",
        *,
        provider: str | None = None,
        country: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.country = country
        details = details or {}
        if country:
            details["country"] = country
        super().__init__(
            message,
            provider=provider,
            code="PROXY_POOL_EXHAUSTED",
            details=details,
        )


class ProxyConnectionError(ProxyError):
    """
    Raised when a connection through the proxy fails.

    This may indicate proxy downtime or network issues.
    """

    def __init__(
        self,
        message: str = "Failed to connect through proxy",
        *,
        proxy_address: str | None = None,
        provider: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            proxy_address=proxy_address,
            provider=provider,
            code="PROXY_CONNECTION_FAILED",
            details=details,
        )


# =============================================================================
# CAPTCHA Exceptions
# =============================================================================


class CaptchaError(ViseOSError):
    """
    Base exception for CAPTCHA-related errors.

    Raised when CAPTCHA solving operations fail.

    Attributes:
        captcha_type: The type of CAPTCHA (e.g., "recaptcha_v2", "turnstile").
        provider: The CAPTCHA solving provider used.
    """

    def __init__(
        self,
        message: str = "CAPTCHA error occurred",
        *,
        captcha_type: str | None = None,
        provider: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.captcha_type = captcha_type
        self.provider = provider
        details = details or {}
        if captcha_type:
            details["captcha_type"] = captcha_type
        if provider:
            details["provider"] = provider
        super().__init__(message, code=code or "CAPTCHA_ERROR", details=details)


class CaptchaSolveTimeoutError(CaptchaError):
    """
    Raised when CAPTCHA solving times out.

    The system should retry with an alternate provider or fail
    after maximum retry attempts.

    Attributes:
        timeout_seconds: The timeout duration that was exceeded.
        attempts: Number of attempts made.
    """

    def __init__(
        self,
        message: str = "CAPTCHA solving timed out",
        *,
        captcha_type: str | None = None,
        provider: str | None = None,
        timeout_seconds: int | None = None,
        attempts: int = 1,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        details = details or {}
        if timeout_seconds:
            details["timeout_seconds"] = timeout_seconds
        details["attempts"] = attempts
        super().__init__(
            message,
            captcha_type=captcha_type,
            provider=provider,
            code="CAPTCHA_TIMEOUT",
            details=details,
        )


class CaptchaProviderError(CaptchaError):
    """
    Raised when a CAPTCHA provider returns an error.

    This may indicate API issues, insufficient balance, or invalid requests.

    Attributes:
        provider_error: The error message from the provider.
    """

    def __init__(
        self,
        message: str = "CAPTCHA provider returned an error",
        *,
        captcha_type: str | None = None,
        provider: str | None = None,
        provider_error: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.provider_error = provider_error
        details = details or {}
        if provider_error:
            details["provider_error"] = provider_error
        super().__init__(
            message,
            captcha_type=captcha_type,
            provider=provider,
            code="CAPTCHA_PROVIDER_ERROR",
            details=details,
        )


class CaptchaInvalidSolutionError(CaptchaError):
    """
    Raised when a CAPTCHA solution is rejected by the target site.

    The system should request a new solution and retry.
    """

    def __init__(
        self,
        message: str = "CAPTCHA solution was rejected by the site",
        *,
        captcha_type: str | None = None,
        provider: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            captcha_type=captcha_type,
            provider=provider,
            code="CAPTCHA_INVALID_SOLUTION",
            details=details,
        )


# =============================================================================
# Account Pool Exceptions
# =============================================================================


class AccountPoolError(ViseOSError):
    """
    Base exception for account pool management errors.

    Raised when operations on the bot account pool fail.

    Attributes:
        site: The site code for which account was requested.
    """

    def __init__(
        self,
        message: str = "Account pool error occurred",
        *,
        site: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.site = site
        details = details or {}
        if site:
            details["site"] = site
        super().__init__(message, code=code or "ACCOUNT_POOL_ERROR", details=details)


class AccountPoolExhaustedError(AccountPoolError):
    """
    Raised when no healthy accounts are available for a site.

    This is a critical error indicating all accounts are banned,
    in cooldown, or at usage limits.
    """

    def __init__(
        self,
        message: str = "No healthy accounts available for site",
        *,
        site: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message, site=site, code="ACCOUNT_POOL_EXHAUSTED", details=details
        )


class AccountNotFoundError(AccountPoolError):
    """
    Raised when a requested account cannot be found.

    Attributes:
        account_id: The ID of the account that was not found.
    """

    def __init__(
        self,
        message: str = "Account not found in pool",
        *,
        account_id: str | None = None,
        site: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.account_id = account_id
        details = details or {}
        if account_id:
            details["account_id"] = account_id
        super().__init__(
            message, site=site, code="ACCOUNT_NOT_FOUND", details=details
        )


class AccountCooldownError(AccountPoolError):
    """
    Raised when an account is in cooldown and cannot be used.

    Attributes:
        account_id: The ID of the account in cooldown.
        cooldown_until: When the cooldown period ends.
    """

    def __init__(
        self,
        message: str = "Account is in cooldown period",
        *,
        account_id: str | None = None,
        cooldown_until: str | None = None,
        site: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.account_id = account_id
        self.cooldown_until = cooldown_until
        details = details or {}
        if account_id:
            details["account_id"] = account_id
        if cooldown_until:
            details["cooldown_until"] = cooldown_until
        super().__init__(
            message, site=site, code="ACCOUNT_COOLDOWN", details=details
        )


# =============================================================================
# Browser Exceptions
# =============================================================================


class BrowserError(ViseOSError):
    """
    Base exception for browser automation errors.

    Raised when Playwright/Camoufox browser operations fail.

    Attributes:
        session_id: The browser session ID if available.
    """

    def __init__(
        self,
        message: str = "Browser automation error occurred",
        *,
        session_id: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.session_id = session_id
        details = details or {}
        if session_id:
            details["session_id"] = session_id
        super().__init__(message, code=code or "BROWSER_ERROR", details=details)


class BrowserCrashError(BrowserError):
    """
    Raised when the browser crashes unexpectedly.

    The session state should be saved and the browser restarted
    with a new context to resume from the last checkpoint.
    """

    def __init__(
        self,
        message: str = "Browser crashed unexpectedly",
        *,
        session_id: str | None = None,
        crash_reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.crash_reason = crash_reason
        details = details or {}
        if crash_reason:
            details["crash_reason"] = crash_reason
        super().__init__(
            message, session_id=session_id, code="BROWSER_CRASH", details=details
        )


class BrowserTimeoutError(BrowserError):
    """
    Raised when a browser operation times out.

    This may indicate slow page loads, network issues, or unresponsive pages.

    Attributes:
        operation: The operation that timed out.
        timeout_ms: The timeout duration in milliseconds.
    """

    def __init__(
        self,
        message: str = "Browser operation timed out",
        *,
        session_id: str | None = None,
        operation: str | None = None,
        timeout_ms: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.operation = operation
        self.timeout_ms = timeout_ms
        details = details or {}
        if operation:
            details["operation"] = operation
        if timeout_ms:
            details["timeout_ms"] = timeout_ms
        super().__init__(
            message, session_id=session_id, code="BROWSER_TIMEOUT", details=details
        )


class SessionError(BrowserError):
    """
    Raised when browser session management fails.

    This includes session creation, restoration, and storage errors.
    """

    def __init__(
        self,
        message: str = "Browser session error",
        *,
        session_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message, session_id=session_id, code="SESSION_ERROR", details=details
        )


class NavigationError(BrowserError):
    """
    Raised when navigation to a page fails.

    Attributes:
        url: The URL that failed to load.
        status_code: The HTTP status code if available.
    """

    def __init__(
        self,
        message: str = "Navigation to page failed",
        *,
        session_id: str | None = None,
        url: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.url = url
        self.status_code = status_code
        details = details or {}
        if url:
            details["url"] = url
        if status_code:
            details["status_code"] = status_code
        super().__init__(
            message, session_id=session_id, code="NAVIGATION_ERROR", details=details
        )


# =============================================================================
# Payment Exceptions
# =============================================================================


class PaymentError(ViseOSError):
    """
    Base exception for payment processing errors.

    Raised when payment operations fail.

    Attributes:
        booking_id: The booking associated with the payment.
        gateway: The payment gateway used.
    """

    def __init__(
        self,
        message: str = "Payment error occurred",
        *,
        booking_id: str | None = None,
        gateway: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.booking_id = booking_id
        self.gateway = gateway
        details = details or {}
        if booking_id:
            details["booking_id"] = booking_id
        if gateway:
            details["gateway"] = gateway
        super().__init__(message, code=code or "PAYMENT_ERROR", details=details)


class ThreeDSAuthenticationError(PaymentError):
    """
    Raised when 3D Secure authentication fails.

    The system should retry authentication or notify the user
    if the failure persists.

    Attributes:
        acs_url: The Access Control Server URL if available.
        reason: The reason for authentication failure.
    """

    def __init__(
        self,
        message: str = "3D Secure authentication failed",
        *,
        booking_id: str | None = None,
        gateway: str | None = None,
        acs_url: str | None = None,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.acs_url = acs_url
        self.reason = reason
        details = details or {}
        if acs_url:
            details["acs_url"] = acs_url
        if reason:
            details["reason"] = reason
        super().__init__(
            message,
            booking_id=booking_id,
            gateway=gateway,
            code="THREE_DS_FAILED",
            details=details,
        )


class PaymentDeclinedError(PaymentError):
    """
    Raised when a payment is declined by the payment gateway.

    Attributes:
        decline_code: The decline code from the gateway.
        decline_reason: Human-readable decline reason.
    """

    def __init__(
        self,
        message: str = "Payment was declined",
        *,
        booking_id: str | None = None,
        gateway: str | None = None,
        decline_code: str | None = None,
        decline_reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.decline_code = decline_code
        self.decline_reason = decline_reason
        details = details or {}
        if decline_code:
            details["decline_code"] = decline_code
        if decline_reason:
            details["decline_reason"] = decline_reason
        super().__init__(
            message,
            booking_id=booking_id,
            gateway=gateway,
            code="PAYMENT_DECLINED",
            details=details,
        )


class PaymentTimeoutError(PaymentError):
    """
    Raised when a payment operation times out.

    The payment status may be indeterminate and should be verified
    with the gateway before retrying.
    """

    def __init__(
        self,
        message: str = "Payment operation timed out",
        *,
        booking_id: str | None = None,
        gateway: str | None = None,
        timeout_seconds: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        details = details or {}
        if timeout_seconds:
            details["timeout_seconds"] = timeout_seconds
        super().__init__(
            message,
            booking_id=booking_id,
            gateway=gateway,
            code="PAYMENT_TIMEOUT",
            details=details,
        )


# =============================================================================
# Infrastructure Exceptions
# =============================================================================


class InfrastructureError(ViseOSError):
    """
    Base exception for infrastructure errors.

    Raised when connections to infrastructure services fail.
    """

    def __init__(
        self,
        message: str = "Infrastructure error occurred",
        *,
        service: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.service = service
        details = details or {}
        if service:
            details["service"] = service
        super().__init__(message, code=code or "INFRASTRUCTURE_ERROR", details=details)


class RedisConnectionError(InfrastructureError):
    """
    Raised when connection to Redis fails.

    Operations should gracefully degrade or queue locally
    until the connection is restored.

    Attributes:
        redis_url: The Redis URL that failed to connect.
    """

    def __init__(
        self,
        message: str = "Failed to connect to Redis",
        *,
        redis_url: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.redis_url = redis_url
        details = details or {}
        if redis_url:
            # Mask password in URL for logging
            details["redis_url"] = self._mask_url(redis_url)
        super().__init__(
            message, service="redis", code="REDIS_CONNECTION_ERROR", details=details
        )

    @staticmethod
    def _mask_url(url: str) -> str:
        """Mask password in Redis URL for safe logging."""
        if "@" in url:
            # URL format: redis://user:password@host:port/db
            parts = url.split("@")
            masked = parts[0].rsplit(":", 1)[0] + ":***@" + parts[1]
            return masked
        return url


class DirectusAPIError(InfrastructureError):
    """
    Raised when Directus API operations fail.

    Attributes:
        endpoint: The Directus API endpoint that failed.
        status_code: The HTTP status code if available.
    """

    def __init__(
        self,
        message: str = "Directus API error",
        *,
        endpoint: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.status_code = status_code
        details = details or {}
        if endpoint:
            details["endpoint"] = endpoint
        if status_code:
            details["status_code"] = status_code
        super().__init__(
            message, service="directus", code="DIRECTUS_API_ERROR", details=details
        )


class DirectusRateLimitError(DirectusAPIError):
    """
    Raised when Directus API rate limit is exceeded.

    Operations should implement exponential backoff and batch
    operations where possible.

    Attributes:
        retry_after: Seconds to wait before retrying.
    """

    def __init__(
        self,
        message: str = "Directus API rate limit exceeded",
        *,
        endpoint: str | None = None,
        retry_after: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.retry_after = retry_after
        details = details or {}
        if retry_after:
            details["retry_after"] = retry_after
        # Override status_code for rate limit
        super().__init__(
            message, endpoint=endpoint, status_code=429, details=details
        )
        self.code = "DIRECTUS_RATE_LIMIT"


# =============================================================================
# Network Exceptions
# =============================================================================


class NetworkError(ViseOSError):
    """
    Base exception for network-related errors.

    Raised when network operations fail, including DNS resolution,
    connection establishment, and data transfer.
    """

    def __init__(
        self,
        message: str = "Network error occurred",
        *,
        url: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.url = url
        details = details or {}
        if url:
            details["url"] = url
        super().__init__(message, code=code or "NETWORK_ERROR", details=details)


class NetworkTimeoutError(NetworkError):
    """
    Raised when a network operation times out.

    Retry with exponential backoff is recommended.

    Attributes:
        timeout_seconds: The timeout duration that was exceeded.
    """

    def __init__(
        self,
        message: str = "Network operation timed out",
        *,
        url: str | None = None,
        timeout_seconds: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        details = details or {}
        if timeout_seconds:
            details["timeout_seconds"] = timeout_seconds
        super().__init__(
            message, url=url, code="NETWORK_TIMEOUT", details=details
        )


class ConnectionResetError(NetworkError):
    """
    Raised when a network connection is reset.

    This may indicate server-side issues or network instability.
    """

    def __init__(
        self,
        message: str = "Network connection was reset",
        *,
        url: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message, url=url, code="CONNECTION_RESET", details=details
        )


# =============================================================================
# Verification Exceptions
# =============================================================================


class VerificationError(ViseOSError):
    """
    Base exception for verification errors.

    Raised when email or SMS verification operations fail.

    Attributes:
        verification_type: Type of verification (email, sms).
    """

    def __init__(
        self,
        message: str = "Verification error occurred",
        *,
        verification_type: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.verification_type = verification_type
        details = details or {}
        if verification_type:
            details["verification_type"] = verification_type
        super().__init__(message, code=code or "VERIFICATION_ERROR", details=details)


class OTPExtractionError(VerificationError):
    """
    Raised when OTP extraction from email or SMS fails.

    Attributes:
        source: The source of the OTP (email subject, sender, etc.).
    """

    def __init__(
        self,
        message: str = "Failed to extract OTP from message",
        *,
        verification_type: str | None = None,
        source: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.source = source
        details = details or {}
        if source:
            details["source"] = source
        super().__init__(
            message,
            verification_type=verification_type,
            code="OTP_EXTRACTION_ERROR",
            details=details,
        )


class VerificationTimeoutError(VerificationError):
    """
    Raised when waiting for verification times out.

    The verification email or SMS was not received within the expected time.
    """

    def __init__(
        self,
        message: str = "Verification timed out waiting for code",
        *,
        verification_type: str | None = None,
        timeout_seconds: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        details = details or {}
        if timeout_seconds:
            details["timeout_seconds"] = timeout_seconds
        super().__init__(
            message,
            verification_type=verification_type,
            code="VERIFICATION_TIMEOUT",
            details=details,
        )


# =============================================================================
# Booking Exceptions
# =============================================================================


class BookingError(ViseOSError):
    """
    Base exception for booking-related errors.

    Raised when booking operations fail.

    Attributes:
        booking_id: The ID of the affected booking.
    """

    def __init__(
        self,
        message: str = "Booking error occurred",
        *,
        booking_id: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.booking_id = booking_id
        details = details or {}
        if booking_id:
            details["booking_id"] = booking_id
        super().__init__(message, code=code or "BOOKING_ERROR", details=details)


class InvalidStateTransitionError(BookingError):
    """
    Raised when an invalid state transition is attempted.

    Attributes:
        current_state: The current state of the booking.
        target_state: The invalid target state.
        allowed_states: List of valid target states.
    """

    def __init__(
        self,
        message: str = "Invalid booking state transition",
        *,
        booking_id: str | None = None,
        current_state: str | None = None,
        target_state: str | None = None,
        allowed_states: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.current_state = current_state
        self.target_state = target_state
        self.allowed_states = allowed_states
        details = details or {}
        if current_state:
            details["current_state"] = current_state
        if target_state:
            details["target_state"] = target_state
        if allowed_states:
            details["allowed_states"] = allowed_states
        super().__init__(
            message,
            booking_id=booking_id,
            code="INVALID_STATE_TRANSITION",
            details=details,
        )


class InsufficientCreditsError(BookingError):
    """
    Raised when an agency has insufficient credits for a booking.

    Attributes:
        agency_id: The ID of the agency.
        required_credits: Credits required for the operation.
        available_credits: Credits available in the account.
    """

    def __init__(
        self,
        message: str = "Insufficient credits for booking",
        *,
        booking_id: str | None = None,
        agency_id: str | None = None,
        required_credits: float | None = None,
        available_credits: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.agency_id = agency_id
        self.required_credits = required_credits
        self.available_credits = available_credits
        details = details or {}
        if agency_id:
            details["agency_id"] = agency_id
        if required_credits is not None:
            details["required_credits"] = required_credits
        if available_credits is not None:
            details["available_credits"] = available_credits
        super().__init__(
            message,
            booking_id=booking_id,
            code="INSUFFICIENT_CREDITS",
            details=details,
        )


# =============================================================================
# AI Engine Exceptions
# =============================================================================


class AIEngineError(ViseOSError):
    """
    Base exception for AI engine errors.

    Raised when AI decision engine operations fail.

    Attributes:
        provider: The AI provider (anthropic, openai).
    """

    def __init__(
        self,
        message: str = "AI engine error occurred",
        *,
        provider: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.provider = provider
        details = details or {}
        if provider:
            details["provider"] = provider
        super().__init__(message, code=code or "AI_ENGINE_ERROR", details=details)


class AIProviderError(AIEngineError):
    """
    Raised when an AI provider returns an error.

    Attributes:
        provider_error: The error message from the provider.
        status_code: HTTP status code if available.
    """

    def __init__(
        self,
        message: str = "AI provider returned an error",
        *,
        provider: str | None = None,
        provider_error: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.provider_error = provider_error
        self.status_code = status_code
        details = details or {}
        if provider_error:
            details["provider_error"] = provider_error
        if status_code:
            details["status_code"] = status_code
        super().__init__(
            message, provider=provider, code="AI_PROVIDER_ERROR", details=details
        )


class SelectorHealingError(AIEngineError):
    """
    Raised when AI selector healing fails.

    Attributes:
        selector: The selector that failed to heal.
        attempts: Number of healing attempts made.
    """

    def __init__(
        self,
        message: str = "Failed to heal selector with AI",
        *,
        provider: str | None = None,
        selector: str | None = None,
        attempts: int = 1,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.selector = selector
        self.attempts = attempts
        details = details or {}
        if selector:
            details["selector"] = selector
        details["attempts"] = attempts
        super().__init__(
            message, provider=provider, code="SELECTOR_HEALING_ERROR", details=details
        )


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Base
    "ViseOSError",
    # Site Adapters
    "SiteAdapterError",
    "AccountBannedError",
    "SlotNotAvailableError",
    "SiteStructureChangedError",
    "SelectorNotFoundError",
    "LoginFailedError",
    "NoSlotsFoundError",
    # Proxy
    "ProxyError",
    "ProxyBlockedError",
    "ProxyPoolExhaustedError",
    "ProxyConnectionError",
    # CAPTCHA
    "CaptchaError",
    "CaptchaSolveTimeoutError",
    "CaptchaProviderError",
    "CaptchaInvalidSolutionError",
    # Account Pool
    "AccountPoolError",
    "AccountPoolExhaustedError",
    "AccountNotFoundError",
    "AccountCooldownError",
    # Browser
    "BrowserError",
    "BrowserCrashError",
    "BrowserTimeoutError",
    "SessionError",
    "NavigationError",
    # Payment
    "PaymentError",
    "ThreeDSAuthenticationError",
    "PaymentDeclinedError",
    "PaymentTimeoutError",
    # Infrastructure
    "InfrastructureError",
    "RedisConnectionError",
    "DirectusAPIError",
    "DirectusRateLimitError",
    # Network
    "NetworkError",
    "NetworkTimeoutError",
    "ConnectionResetError",
    # Verification
    "VerificationError",
    "OTPExtractionError",
    "VerificationTimeoutError",
    # Booking
    "BookingError",
    "InvalidStateTransitionError",
    "InsufficientCreditsError",
    # AI Engine
    "AIEngineError",
    "AIProviderError",
    "SelectorHealingError",
]
