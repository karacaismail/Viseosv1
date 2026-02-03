"""
Telegram Notification Service.

This module provides an async client for sending notifications via Telegram Bot API.
It supports multiple chat IDs, HTML formatting, alert severity levels, and retry logic.

Based on 019-ALERT-SYSTEM.md specification for multi-channel notifications.

Usage:
    from src.integrations.telegram import TelegramNotifier, get_telegram_notifier

    # Using context manager (recommended)
    async with TelegramNotifier() as notifier:
        await notifier.send_alert(
            severity=AlertSeverity.CRITICAL,
            category=AlertCategory.BOOKING,
            title="Booking Success Rate Critical",
            message="Success rate dropped below 50%",
        )

    # Or with factory function
    notifier = get_telegram_notifier()
    await notifier.send_message(chat_id, "Hello, World!")
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import lru_cache
from typing import Any

import httpx
import structlog

from src.api.config import get_settings
from src.core.exceptions import ViseOSError

logger = structlog.get_logger()


# =============================================================================
# Exceptions
# =============================================================================


class TelegramError(ViseOSError):
    """
    Base exception for Telegram API errors.

    Raised when Telegram API operations fail.

    Attributes:
        chat_id: The chat ID that caused the error.
        error_code: Telegram API error code if available.
    """

    def __init__(
        self,
        message: str = "Telegram API error occurred",
        *,
        chat_id: str | None = None,
        error_code: int | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.chat_id = chat_id
        self.error_code = error_code
        details = details or {}
        if chat_id:
            details["chat_id"] = chat_id
        if error_code:
            details["error_code"] = error_code
        super().__init__(message, code=code or "TELEGRAM_ERROR", details=details)


class TelegramRateLimitError(TelegramError):
    """Raised when Telegram API rate limit is exceeded."""

    def __init__(
        self,
        message: str = "Telegram API rate limit exceeded",
        *,
        retry_after: int = 30,
        chat_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.retry_after = retry_after
        details = details or {}
        details["retry_after"] = retry_after
        super().__init__(
            message,
            chat_id=chat_id,
            error_code=429,
            code="TELEGRAM_RATE_LIMIT",
            details=details,
        )


class TelegramAuthError(TelegramError):
    """Raised when Telegram bot token is invalid."""

    def __init__(
        self,
        message: str = "Invalid Telegram bot token",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            error_code=401,
            code="TELEGRAM_AUTH_ERROR",
            details=details,
        )


class TelegramChatNotFoundError(TelegramError):
    """Raised when the target chat is not found or bot has no access."""

    def __init__(
        self,
        message: str = "Telegram chat not found or access denied",
        *,
        chat_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            chat_id=chat_id,
            error_code=400,
            code="TELEGRAM_CHAT_NOT_FOUND",
            details=details,
        )


# =============================================================================
# Enums and Constants
# =============================================================================


class AlertSeverity(str, Enum):
    """Alert severity levels as per 019-ALERT-SYSTEM.md."""

    CRITICAL = "critical"  # Immediate action required
    HIGH = "high"  # Action within 15 minutes
    MEDIUM = "medium"  # Action within 1 hour
    LOW = "low"  # Action within 24 hours
    INFO = "info"  # No action required


class AlertCategory(str, Enum):
    """Alert categories as per 019-ALERT-SYSTEM.md."""

    BOOKING = "booking"
    SYSTEM = "system"
    SECURITY = "security"
    PAYMENT = "payment"
    EXTERNAL = "external"
    CAPACITY = "capacity"


# Severity to emoji mapping
SEVERITY_EMOJI = {
    AlertSeverity.CRITICAL: "\U0001F6A8",  # 🚨
    AlertSeverity.HIGH: "\u26A0\uFE0F",  # ⚠️
    AlertSeverity.MEDIUM: "\U0001F536",  # 🔶
    AlertSeverity.LOW: "\U0001F535",  # 🔵
    AlertSeverity.INFO: "\u2139\uFE0F",  # ℹ️
}

# Category to emoji mapping
CATEGORY_EMOJI = {
    AlertCategory.BOOKING: "\U0001F4C5",  # 📅
    AlertCategory.SYSTEM: "\u2699\uFE0F",  # ⚙️
    AlertCategory.SECURITY: "\U0001F512",  # 🔒
    AlertCategory.PAYMENT: "\U0001F4B3",  # 💳
    AlertCategory.EXTERNAL: "\U0001F310",  # 🌐
    AlertCategory.CAPACITY: "\U0001F4CA",  # 📊
}

# Telegram API base URL
TELEGRAM_API_URL = "https://api.telegram.org"

# Parse modes
PARSE_MODE_HTML = "HTML"
PARSE_MODE_MARKDOWN = "MarkdownV2"


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class AlertMessage:
    """
    Represents an alert message to be sent via Telegram.

    Attributes:
        severity: Alert severity level.
        category: Alert category.
        title: Alert title.
        message: Alert message body.
        details: Optional additional details.
        runbook_url: Optional link to runbook.
        timestamp: Alert timestamp.
    """

    severity: AlertSeverity
    category: AlertCategory
    title: str
    message: str
    details: dict[str, Any] | None = None
    runbook_url: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def format_html(self) -> str:
        """Format alert as HTML for Telegram."""
        severity_emoji = SEVERITY_EMOJI.get(self.severity, "")
        category_emoji = CATEGORY_EMOJI.get(self.category, "")

        # Build message parts
        parts = [
            f"{severity_emoji} <b>{self.severity.value.upper()} ALERT</b> {severity_emoji}",
            "",
            f"{category_emoji} <b>Category:</b> {self.category.value.title()}",
            f"<b>Alert:</b> {self._escape_html(self.title)}",
            "",
            self._escape_html(self.message),
        ]

        # Add details if present
        if self.details:
            parts.append("")
            parts.append("<b>Details:</b>")
            for key, value in self.details.items():
                parts.append(f"  • {key}: {self._escape_html(str(value))}")

        # Add runbook link if present
        if self.runbook_url:
            parts.append("")
            parts.append(f'\U0001F4D6 <a href="{self.runbook_url}">Runbook</a>')

        # Add timestamp
        parts.append("")
        parts.append(f"<i>Time: {self.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}</i>")

        return "\n".join(parts)

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape HTML special characters."""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )


@dataclass
class SendResult:
    """Result of a send operation."""

    success: bool
    message_id: int | None = None
    chat_id: str | None = None
    error: str | None = None
    retry_after: int | None = None


# =============================================================================
# Client Class
# =============================================================================


class TelegramNotifier:
    """
    Async client for Telegram Bot API notifications.

    Provides methods for sending messages, alerts, and notifications with
    support for HTML formatting, retry logic, and rate limit handling.

    Attributes:
        bot_token: Telegram bot token.
        default_chat_id: Default chat ID for notifications.
        timeout: Request timeout in seconds.
        max_retries: Maximum number of retries for failed requests.

    Example:
        async with TelegramNotifier() as notifier:
            # Send simple message
            await notifier.send_message(chat_id, "Hello, World!")

            # Send formatted alert
            await notifier.send_alert(
                severity=AlertSeverity.CRITICAL,
                category=AlertCategory.BOOKING,
                title="Booking Success Rate Critical",
                message="Success rate dropped below 50%",
            )
    """

    def __init__(
        self,
        bot_token: str | None = None,
        default_chat_id: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        """
        Initialize the Telegram notifier.

        Args:
            bot_token: Telegram bot token. If not provided, uses
                TELEGRAM_BOT_TOKEN from settings.
            default_chat_id: Default chat ID for notifications. If not provided,
                uses TELEGRAM_ALERT_CHAT_ID from settings.
            timeout: Request timeout in seconds (default: 30).
            max_retries: Maximum retries for failed requests (default: 3).
        """
        settings = get_settings()
        token = bot_token or settings.TELEGRAM_BOT_TOKEN.get_secret_value()
        self._bot_token = token
        self.default_chat_id = default_chat_id or settings.TELEGRAM_ALERT_CHAT_ID
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: httpx.AsyncClient | None = None
        self._logger = logger.bind(service="telegram")

        # Validate bot token format
        if self._bot_token and ":" not in self._bot_token:
            self._logger.warning(
                "telegram_invalid_token_format",
                hint="Token should be in format: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
            )

    @property
    def _base_url(self) -> str:
        """Get the Telegram API base URL for this bot."""
        return f"{TELEGRAM_API_URL}/bot{self._bot_token}"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
            )
        return self._client

    async def __aenter__(self) -> "TelegramNotifier":
        """Async context manager entry."""
        await self._get_client()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit - close the client."""
        await self.close()

    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Make a request to the Telegram Bot API.

        Args:
            method: Telegram API method name (e.g., "sendMessage").
            data: Request parameters.

        Returns:
            API response data.

        Raises:
            TelegramError: For API errors.
            TelegramRateLimitError: When rate limited.
            TelegramAuthError: For authentication failures.
        """
        client = await self._get_client()
        url = f"{self._base_url}/{method}"
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                self._logger.debug(
                    "telegram_request",
                    method=method,
                    attempt=attempt + 1,
                )

                response = await client.post(url, json=data)
                result = response.json()

                # Check for Telegram API error
                if not result.get("ok"):
                    error_code = result.get("error_code", 0)
                    description = result.get("description", "Unknown error")

                    # Handle specific error codes
                    if error_code == 429:
                        retry_after = result.get("parameters", {}).get(
                            "retry_after", 30
                        )
                        self._logger.warning(
                            "telegram_rate_limited",
                            retry_after=retry_after,
                        )
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(retry_after)
                            continue
                        raise TelegramRateLimitError(
                            retry_after=retry_after,
                            chat_id=data.get("chat_id") if data else None,
                        )

                    if error_code == 401:
                        raise TelegramAuthError(message=description)

                    if error_code in (400, 403) and "chat not found" in description.lower():
                        raise TelegramChatNotFoundError(
                            message=description,
                            chat_id=data.get("chat_id") if data else None,
                        )

                    raise TelegramError(
                        message=description,
                        chat_id=data.get("chat_id") if data else None,
                        error_code=error_code,
                    )

                return result.get("result", result)

            except httpx.TimeoutException as e:
                last_error = e
                self._logger.warning(
                    "telegram_timeout",
                    method=method,
                    attempt=attempt + 1,
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2**attempt)  # Exponential backoff
                    continue

            except httpx.HTTPError as e:
                last_error = e
                self._logger.error(
                    "telegram_http_error",
                    method=method,
                    error=str(e),
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2**attempt)
                    continue

        # All retries exhausted
        raise TelegramError(
            message=f"Request failed after {self.max_retries} attempts",
            details={"last_error": str(last_error)} if last_error else None,
        )

    # -------------------------------------------------------------------------
    # Send Operations
    # -------------------------------------------------------------------------

    async def send_message(
        self,
        chat_id: str | None = None,
        text: str = "",
        *,
        parse_mode: str | None = None,
        disable_web_page_preview: bool = False,
        disable_notification: bool = False,
        reply_to_message_id: int | None = None,
    ) -> SendResult:
        """
        Send a text message to a chat.

        Args:
            chat_id: Target chat ID. Uses default_chat_id if not provided.
            text: Message text (max 4096 characters).
            parse_mode: Parse mode ("HTML" or "MarkdownV2").
            disable_web_page_preview: Disable link previews.
            disable_notification: Send silently.
            reply_to_message_id: Message ID to reply to.

        Returns:
            SendResult with success status and message ID.

        Example:
            result = await notifier.send_message(
                chat_id="@my_channel",
                text="<b>Hello</b>, World!",
                parse_mode="HTML",
            )
        """
        target_chat = chat_id or self.default_chat_id

        if not target_chat:
            return SendResult(
                success=False,
                error="No chat_id provided and no default_chat_id configured",
            )

        if not text:
            return SendResult(
                success=False,
                chat_id=target_chat,
                error="Message text cannot be empty",
            )

        # Truncate message if too long
        if len(text) > 4096:
            text = text[:4093] + "..."

        data: dict[str, Any] = {
            "chat_id": target_chat,
            "text": text,
        }

        if parse_mode:
            data["parse_mode"] = parse_mode
        if disable_web_page_preview:
            data["disable_web_page_preview"] = True
        if disable_notification:
            data["disable_notification"] = True
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id

        try:
            result = await self._request("sendMessage", data)

            self._logger.info(
                "telegram_message_sent",
                chat_id=target_chat,
                message_id=result.get("message_id"),
            )

            return SendResult(
                success=True,
                message_id=result.get("message_id"),
                chat_id=target_chat,
            )

        except TelegramRateLimitError as e:
            return SendResult(
                success=False,
                chat_id=target_chat,
                error=str(e),
                retry_after=e.retry_after,
            )
        except TelegramError as e:
            return SendResult(
                success=False,
                chat_id=target_chat,
                error=str(e),
            )

    async def send_alert(
        self,
        severity: AlertSeverity,
        category: AlertCategory,
        title: str,
        message: str,
        *,
        chat_id: str | None = None,
        details: dict[str, Any] | None = None,
        runbook_url: str | None = None,
        disable_notification: bool = False,
    ) -> SendResult:
        """
        Send a formatted alert message.

        Args:
            severity: Alert severity level.
            category: Alert category.
            title: Alert title.
            message: Alert message body.
            chat_id: Target chat ID. Uses default_chat_id if not provided.
            details: Additional details to include.
            runbook_url: Link to runbook documentation.
            disable_notification: Send silently (for low-priority alerts).

        Returns:
            SendResult with success status and message ID.

        Example:
            result = await notifier.send_alert(
                severity=AlertSeverity.CRITICAL,
                category=AlertCategory.BOOKING,
                title="Booking Success Rate Critical",
                message="Success rate dropped below 50% for 5 minutes",
                details={"current_rate": "45%", "threshold": "50%"},
                runbook_url="https://docs.vise.os/runbooks/booking-success-rate",
            )
        """
        alert = AlertMessage(
            severity=severity,
            category=category,
            title=title,
            message=message,
            details=details,
            runbook_url=runbook_url,
        )

        # Send with silent notification for low-priority alerts
        silent = disable_notification or severity in [AlertSeverity.LOW, AlertSeverity.INFO]

        self._logger.info(
            "telegram_sending_alert",
            severity=severity.value,
            category=category.value,
            title=title,
        )

        return await self.send_message(
            chat_id=chat_id,
            text=alert.format_html(),
            parse_mode=PARSE_MODE_HTML,
            disable_notification=silent,
        )

    async def send_booking_success(
        self,
        booking_id: str,
        agency_name: str,
        applicant_name: str,
        appointment_date: str,
        appointment_time: str,
        confirmation_number: str,
        *,
        chat_id: str | None = None,
    ) -> SendResult:
        """
        Send a booking success notification.

        Args:
            booking_id: The booking request ID.
            agency_name: Name of the agency.
            applicant_name: Name of the applicant (masked).
            appointment_date: Appointment date.
            appointment_time: Appointment time.
            confirmation_number: Confirmation number.
            chat_id: Target chat ID. Uses default_chat_id if not provided.

        Returns:
            SendResult with success status and message ID.
        """
        message = (
            "\u2705 <b>BOOKING SUCCESSFUL</b> \u2705\n"
            "\n"
            f"<b>Booking ID:</b> {booking_id[:8]}...\n"
            f"<b>Agency:</b> {applicant_name}\n"
            f"<b>Applicant:</b> {applicant_name}\n"
            "\n"
            f"\U0001F4C5 <b>Date:</b> {appointment_date}\n"
            f"\u23F0 <b>Time:</b> {appointment_time}\n"
            f"\U0001F4CB <b>Confirmation:</b> {confirmation_number}\n"
            "\n"
            f"<i>Agency: {agency_name}</i>"
        )

        return await self.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=PARSE_MODE_HTML,
        )

    async def send_booking_failure(
        self,
        booking_id: str,
        agency_name: str,
        error_message: str,
        attempts: int,
        *,
        chat_id: str | None = None,
    ) -> SendResult:
        """
        Send a booking failure notification.

        Args:
            booking_id: The booking request ID.
            agency_name: Name of the agency.
            error_message: Error description.
            attempts: Number of attempts made.
            chat_id: Target chat ID. Uses default_chat_id if not provided.

        Returns:
            SendResult with success status and message ID.
        """
        message = (
            "\u274C <b>BOOKING FAILED</b> \u274C\n"
            "\n"
            f"<b>Booking ID:</b> {booking_id[:8]}...\n"
            f"<b>Agency:</b> {agency_name}\n"
            f"<b>Attempts:</b> {attempts}\n"
            "\n"
            f"<b>Error:</b>\n{AlertMessage._escape_html(error_message)}"
        )

        return await self.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=PARSE_MODE_HTML,
        )

    # -------------------------------------------------------------------------
    # Utility Methods
    # -------------------------------------------------------------------------

    async def get_me(self) -> dict[str, Any]:
        """
        Get information about the bot.

        Returns:
            Bot information including username and ID.
        """
        return await self._request("getMe")

    async def health_check(self) -> bool:
        """
        Check if Telegram API is accessible and bot token is valid.

        Returns:
            True if healthy, False otherwise.
        """
        try:
            result = await self.get_me()
            return bool(result.get("id"))
        except TelegramError as e:
            self._logger.warning("telegram_health_check_failed", error=str(e))
            return False
        except Exception as e:
            self._logger.warning("telegram_health_check_error", error=str(e))
            return False

    async def get_chat(self, chat_id: str) -> dict[str, Any]:
        """
        Get information about a chat.

        Args:
            chat_id: Target chat ID.

        Returns:
            Chat information.

        Raises:
            TelegramChatNotFoundError: If chat is not found.
        """
        return await self._request("getChat", {"chat_id": chat_id})


# =============================================================================
# Factory Functions
# =============================================================================


@lru_cache
def get_telegram_notifier() -> TelegramNotifier:
    """
    Get a cached Telegram notifier instance.

    The notifier is cached and reused across requests.
    Use this for dependency injection in FastAPI.

    Returns:
        Configured TelegramNotifier instance.

    Example:
        from fastapi import Depends

        @router.post("/alerts")
        async def send_alert(
            notifier: TelegramNotifier = Depends(get_telegram_notifier)
        ):
            await notifier.send_alert(...)
    """
    return TelegramNotifier()


def clear_telegram_notifier_cache() -> None:
    """
    Clear the cached Telegram notifier.

    Useful for testing or when connection settings change.
    """
    get_telegram_notifier.cache_clear()


@asynccontextmanager
async def telegram_notifier(
    bot_token: str | None = None,
    default_chat_id: str | None = None,
):
    """
    Async context manager for a Telegram notifier.

    Creates a new notifier instance that is automatically closed on exit.
    Use this when you need a notifier with custom settings.

    Args:
        bot_token: Optional custom bot token.
        default_chat_id: Optional default chat ID.

    Yields:
        Configured TelegramNotifier instance.

    Example:
        async with telegram_notifier() as notifier:
            await notifier.send_message(chat_id, "Hello!")
    """
    notifier = TelegramNotifier(
        bot_token=bot_token,
        default_chat_id=default_chat_id,
    )
    try:
        await notifier._get_client()
        yield notifier
    finally:
        await notifier.close()


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Client
    "TelegramNotifier",
    "get_telegram_notifier",
    "clear_telegram_notifier_cache",
    "telegram_notifier",
    # Data classes
    "AlertMessage",
    "SendResult",
    # Enums
    "AlertSeverity",
    "AlertCategory",
    # Exceptions
    "TelegramError",
    "TelegramRateLimitError",
    "TelegramAuthError",
    "TelegramChatNotFoundError",
    # Constants
    "SEVERITY_EMOJI",
    "CATEGORY_EMOJI",
    "PARSE_MODE_HTML",
    "PARSE_MODE_MARKDOWN",
]
