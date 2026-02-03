"""
3D Secure Authentication Handler.

This module handles 3D Secure (3DS) authentication for payment processing.
Supports both 3DS 1.0 and 3DS 2.0/2.1/2.2 protocols, with automatic version
detection and appropriate challenge handling.

Features:
- 3DS version detection (1.0, 2.0, 2.1, 2.2)
- Frictionless flow support for 3DS 2.0+
- Challenge flow handling (iframe, redirect, popup)
- Bank OTP input support
- Timeout and status polling

Usage:
    from src.payment.three_ds import ThreeDSHandler, ThreeDSChallenge

    handler = ThreeDSHandler(browser_session)
    result = await handler.handle_3ds(challenge)

    if result == ThreeDSStatus.AUTHENTICATED:
        # Payment authenticated
        pass
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from src.core.exceptions import ThreeDSAuthenticationError

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page


class ThreeDSVersion(Enum):
    """
    3D Secure protocol versions.

    3DS 1.0 uses redirect/iframe approach.
    3DS 2.0+ supports frictionless authentication.
    """

    V1 = "1.0"
    V2 = "2.0"
    V2_1 = "2.1"
    V2_2 = "2.2"


class ThreeDSStatus(Enum):
    """
    3D Secure authentication status.

    Status progression:
    PENDING -> CHALLENGE_REQUIRED -> AUTHENTICATED
    PENDING -> FRICTIONLESS -> AUTHENTICATED
    * -> FAILED | TIMEOUT
    """

    PENDING = "pending"
    CHALLENGE_REQUIRED = "challenge_required"
    FRICTIONLESS = "frictionless"
    AUTHENTICATED = "authenticated"
    FAILED = "failed"
    TIMEOUT = "timeout"


class ThreeDSMethod(Enum):
    """
    3DS challenge delivery method.

    IFRAME: Challenge displayed in embedded iframe
    REDIRECT: Full page redirect to ACS
    POPUP: Challenge in popup window
    """

    IFRAME = "iframe"
    REDIRECT = "redirect"
    POPUP = "popup"


@dataclass
class ThreeDSChallenge:
    """
    3D Secure challenge information.

    Contains all data needed to process a 3DS challenge including
    the ACS URL, authentication requests, and delivery method.

    Attributes:
        version: 3DS protocol version.
        status: Current authentication status.
        acs_url: Access Control Server URL for challenge.
        pareq: Payment Authentication Request for 3DS 1.0.
        creq: Challenge Request for 3DS 2.0+.
        transaction_id: Unique transaction identifier.
        md: Merchant Data (opaque data to pass through).
        method: Challenge delivery method (iframe, redirect, popup).
        term_url: URL for 3DS 1.0 callback.
        notification_url: URL for 3DS 2.0+ callback.
        created_at: When the challenge was created.

    Example:
        challenge = ThreeDSChallenge(
            version=ThreeDSVersion.V2,
            status=ThreeDSStatus.CHALLENGE_REQUIRED,
            acs_url="https://bank.example.com/3ds",
            creq="eyJ0aHJlZURTU2VydmVyVHJhbnNJRCI6...",
            transaction_id="txn_123",
        )
    """

    version: ThreeDSVersion
    status: ThreeDSStatus

    # 3DS URLs
    acs_url: str | None = None
    term_url: str | None = None
    notification_url: str | None = None

    # Authentication requests
    pareq: str | None = None  # 3DS 1.0
    creq: str | None = None  # 3DS 2.0+

    # Transaction identifiers
    transaction_id: str = ""
    md: str | None = None

    # Delivery
    method: ThreeDSMethod = ThreeDSMethod.IFRAME

    # Metadata
    created_at: datetime = field(default_factory=datetime.utcnow)

    def is_v1(self) -> bool:
        """Check if this is a 3DS 1.0 challenge."""
        return self.version == ThreeDSVersion.V1

    def is_v2(self) -> bool:
        """Check if this is a 3DS 2.0+ challenge."""
        return self.version in (
            ThreeDSVersion.V2,
            ThreeDSVersion.V2_1,
            ThreeDSVersion.V2_2,
        )

    def requires_challenge(self) -> bool:
        """Check if this challenge requires user interaction."""
        return self.status == ThreeDSStatus.CHALLENGE_REQUIRED

    def to_dict(self) -> dict[str, Any]:
        """Convert challenge to dictionary for logging."""
        return {
            "version": self.version.value,
            "status": self.status.value,
            "method": self.method.value,
            "transaction_id": self.transaction_id,
            "has_acs_url": self.acs_url is not None,
            "has_pareq": self.pareq is not None,
            "has_creq": self.creq is not None,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class ThreeDSResult:
    """
    Result of 3DS authentication.

    Attributes:
        status: Final authentication status.
        transaction_id: Transaction identifier.
        authentication_value: CAVV/AAV for 3DS 2.0+.
        eci: Electronic Commerce Indicator.
        ds_transaction_id: Directory Server transaction ID.
        protocol_version: Protocol version used.
        error_code: Error code if authentication failed.
        error_message: Error message if authentication failed.
        completed_at: When authentication completed.

    Example:
        result = ThreeDSResult(
            status=ThreeDSStatus.AUTHENTICATED,
            transaction_id="txn_123",
            authentication_value="kBKdWQgXACdXGxYBABEBAAAAAAAAAA=",
            eci="05",
        )
    """

    status: ThreeDSStatus
    transaction_id: str = ""
    authentication_value: str | None = None
    eci: str | None = None
    ds_transaction_id: str | None = None
    protocol_version: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    completed_at: datetime = field(default_factory=datetime.utcnow)

    def is_authenticated(self) -> bool:
        """Check if authentication was successful."""
        return self.status == ThreeDSStatus.AUTHENTICATED

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary for logging."""
        return {
            "status": self.status.value,
            "transaction_id": self.transaction_id,
            "has_authentication_value": self.authentication_value is not None,
            "eci": self.eci,
            "protocol_version": self.protocol_version,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "completed_at": self.completed_at.isoformat(),
        }


class ThreeDSHandler:
    """
    3D Secure authentication handler.

    Handles 3DS authentication flows for payment processing.
    Supports both 3DS 1.0 (redirect/iframe) and 3DS 2.0+
    (frictionless/challenge) protocols.

    Attributes:
        browser: Browser session for interacting with 3DS pages.
        challenge_timeout: Timeout for challenge completion in seconds.
        poll_interval: Interval between status checks in seconds.

    Configuration:
        CHALLENGE_TIMEOUT: 180 seconds (3 minutes)
        POLL_INTERVAL: 2 seconds

    Usage:
        handler = ThreeDSHandler(browser_session)

        # Handle a 3DS challenge
        result = await handler.handle_3ds(challenge)

        if result.is_authenticated():
            # Proceed with payment
            pass
        else:
            # Handle failure
            logger.error("3ds_failed", error=result.error_message)
    """

    # Configuration
    CHALLENGE_TIMEOUT: int = 180  # 3 minutes
    POLL_INTERVAL: int = 2  # seconds

    # 3DS iframe selectors
    FRAME_SELECTORS: list[str] = [
        'iframe[name*="3ds"]',
        'iframe[src*="3dsecure"]',
        'iframe[src*="acs"]',
        'iframe[id*="cardinal"]',
        'iframe[id*="3ds"]',
        "#threeDSIframe",
        "#threeDsIframe",
        ".three-ds-iframe",
    ]

    # OTP input selectors
    OTP_SELECTORS: list[str] = [
        'input[name*="otp"]',
        'input[name*="sms"]',
        'input[id*="otp"]',
        'input[type="tel"]',
        'input[maxlength="6"]',
        'input[maxlength="4"]',
        "#otpInput",
        "#smsCode",
        ".otp-input",
    ]

    # Success indicators
    SUCCESS_PATTERNS: list[str] = [
        "payment successful",
        "payment complete",
        "transaction approved",
        "authenticated",
        "authentication successful",
        "3ds success",
    ]

    # Failure indicators
    FAILURE_PATTERNS: list[str] = [
        "authentication failed",
        "payment failed",
        "declined",
        "error",
        "cancelled",
        "3ds failed",
    ]

    def __init__(
        self,
        browser_session: Any,
        *,
        challenge_timeout: int | None = None,
        poll_interval: int | None = None,
    ) -> None:
        """
        Initialize the 3DS handler.

        Args:
            browser_session: Browser session with page access.
            challenge_timeout: Override default challenge timeout.
            poll_interval: Override default poll interval.

        Example:
            handler = ThreeDSHandler(browser_session)
            handler = ThreeDSHandler(browser_session, challenge_timeout=300)
        """
        self.browser = browser_session
        self.challenge_timeout = challenge_timeout or self.CHALLENGE_TIMEOUT
        self.poll_interval = poll_interval or self.POLL_INTERVAL

    @property
    def page(self) -> Page:
        """Get the current browser page."""
        return self.browser.page

    async def handle_3ds(
        self,
        challenge: ThreeDSChallenge,
    ) -> ThreeDSResult:
        """
        Handle a 3DS authentication challenge.

        Routes to appropriate handler based on 3DS version and
        challenge status. For frictionless flows, returns immediately.
        For challenge flows, waits for user interaction.

        Args:
            challenge: The 3DS challenge to handle.

        Returns:
            ThreeDSResult: Result of authentication.

        Raises:
            ThreeDSAuthenticationError: If authentication fails.

        Example:
            result = await handler.handle_3ds(challenge)
            if result.is_authenticated():
                print("Payment authenticated")
        """
        if challenge.status == ThreeDSStatus.FRICTIONLESS:
            return ThreeDSResult(
                status=ThreeDSStatus.AUTHENTICATED,
                transaction_id=challenge.transaction_id,
                protocol_version=challenge.version.value,
            )

        if challenge.is_v1():
            return await self._handle_3ds_v1(challenge)
        else:
            return await self._handle_3ds_v2(challenge)

    async def _handle_3ds_v1(
        self,
        challenge: ThreeDSChallenge,
    ) -> ThreeDSResult:
        """
        Handle 3DS 1.0 authentication.

        3DS 1.0 uses redirect or iframe approach. The user is sent
        to the bank's ACS page to authenticate (usually with password
        or OTP).

        Args:
            challenge: The 3DS 1.0 challenge.

        Returns:
            ThreeDSResult: Result of authentication.
        """
        if challenge.method == ThreeDSMethod.REDIRECT:
            if challenge.acs_url:
                await self.page.goto(challenge.acs_url)
        else:
            frame = await self._wait_for_3ds_frame()
            if not frame:
                return ThreeDSResult(
                    status=ThreeDSStatus.FAILED,
                    transaction_id=challenge.transaction_id,
                    error_code="FRAME_NOT_FOUND",
                    error_message="3DS iframe not found",
                )

        status = await self._wait_for_completion(challenge.transaction_id)

        return ThreeDSResult(
            status=status,
            transaction_id=challenge.transaction_id,
            protocol_version=challenge.version.value,
            error_code="3DS_FAILED" if status == ThreeDSStatus.FAILED else None,
            error_message="Authentication failed" if status == ThreeDSStatus.FAILED else None,
        )

    async def _handle_3ds_v2(
        self,
        challenge: ThreeDSChallenge,
    ) -> ThreeDSResult:
        """
        Handle 3DS 2.0+ authentication.

        3DS 2.0+ supports frictionless authentication using risk-based
        analysis. If challenge is required, the user must complete
        additional verification.

        Args:
            challenge: The 3DS 2.0+ challenge.

        Returns:
            ThreeDSResult: Result of authentication.
        """
        if challenge.status == ThreeDSStatus.FRICTIONLESS:
            return ThreeDSResult(
                status=ThreeDSStatus.AUTHENTICATED,
                transaction_id=challenge.transaction_id,
                protocol_version=challenge.version.value,
            )

        if challenge.creq and challenge.acs_url:
            await self._submit_creq(challenge)

        status = await self._wait_for_completion(challenge.transaction_id)

        return ThreeDSResult(
            status=status,
            transaction_id=challenge.transaction_id,
            protocol_version=challenge.version.value,
            error_code="3DS_FAILED" if status == ThreeDSStatus.FAILED else None,
            error_message="Authentication failed" if status == ThreeDSStatus.FAILED else None,
        )

    async def _wait_for_3ds_frame(self) -> Frame | None:
        """
        Wait for 3DS iframe to appear.

        Searches for common 3DS iframe selectors and returns the
        frame if found.

        Returns:
            Frame if found, None otherwise.
        """
        for selector in self.FRAME_SELECTORS:
            try:
                frame_element = await self.page.wait_for_selector(
                    selector,
                    timeout=10000,
                )
                if frame_element:
                    return await frame_element.content_frame()
            except Exception:
                continue

        return None

    async def _submit_creq(
        self,
        challenge: ThreeDSChallenge,
    ) -> None:
        """
        Submit 3DS 2.0 Challenge Request to ACS.

        Creates a hidden form and submits the CReq to the ACS URL,
        targeting the 3DS iframe.

        Args:
            challenge: The 3DS challenge with CReq data.
        """
        if not challenge.acs_url or not challenge.creq:
            return

        await self.page.evaluate(
            """
            ([acsUrl, creq]) => {
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = acsUrl;
                form.target = 'threeDSFrame';

                const creqInput = document.createElement('input');
                creqInput.type = 'hidden';
                creqInput.name = 'creq';
                creqInput.value = creq;
                form.appendChild(creqInput);

                document.body.appendChild(form);
                form.submit();
                document.body.removeChild(form);
            }
            """,
            [challenge.acs_url, challenge.creq],
        )

    async def _wait_for_completion(
        self,
        transaction_id: str,
    ) -> ThreeDSStatus:
        """
        Wait for 3DS authentication to complete.

        Polls the page for success or failure indicators until
        authentication completes or timeout is reached.

        Args:
            transaction_id: Transaction ID for logging.

        Returns:
            ThreeDSStatus indicating the result.
        """
        start_time = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start_time < self.challenge_timeout:
            url = self.page.url.lower()
            content = await self.page.content()
            content_lower = content.lower()

            for pattern in self.SUCCESS_PATTERNS:
                if pattern in url or pattern in content_lower:
                    return ThreeDSStatus.AUTHENTICATED

            for pattern in self.FAILURE_PATTERNS:
                if pattern in content_lower and "success" not in content_lower:
                    return ThreeDSStatus.FAILED

            if "success" in url or "complete" in url or "return" in url:
                return ThreeDSStatus.AUTHENTICATED

            await asyncio.sleep(self.poll_interval)

        return ThreeDSStatus.TIMEOUT

    async def handle_bank_otp(
        self,
        otp_code: str,
        *,
        page: Page | None = None,
    ) -> bool:
        """
        Enter bank OTP code for 3DS verification.

        Finds the OTP input field and submits the code. This is used
        when the bank requires SMS or app-based OTP for authentication.

        Args:
            otp_code: The OTP code to enter.
            page: Optional page to use (defaults to current page).

        Returns:
            True if OTP was successfully entered and submitted.

        Example:
            success = await handler.handle_bank_otp("123456")
            if success:
                # OTP submitted, wait for result
                pass
        """
        target = page or self.page

        for selector in self.OTP_SELECTORS:
            try:
                otp_input = await target.wait_for_selector(
                    selector,
                    timeout=5000,
                )
                if otp_input:
                    await otp_input.fill(otp_code)

                    submit_selectors = [
                        'button[type="submit"]',
                        'input[type="submit"]',
                        ".otp-submit",
                        "#submitOtp",
                        ".submit-btn",
                    ]

                    for submit_selector in submit_selectors:
                        submit_btn = await target.query_selector(submit_selector)
                        if submit_btn:
                            await submit_btn.click()
                            return True

                    await target.keyboard.press("Enter")
                    return True
            except Exception:
                continue

        return False

    async def detect_3ds_challenge(
        self,
        page: Page | None = None,
    ) -> ThreeDSChallenge | None:
        """
        Detect 3DS challenge on the current page.

        Scans the page for 3DS indicators such as iframes, redirects,
        or specific HTML patterns.

        Args:
            page: Optional page to scan (defaults to current page).

        Returns:
            ThreeDSChallenge if detected, None otherwise.

        Example:
            challenge = await handler.detect_3ds_challenge()
            if challenge:
                result = await handler.handle_3ds(challenge)
        """
        target = page or self.page
        url = target.url.lower()

        for selector in self.FRAME_SELECTORS:
            element = await target.query_selector(selector)
            if element:
                return ThreeDSChallenge(
                    version=ThreeDSVersion.V2,
                    status=ThreeDSStatus.CHALLENGE_REQUIRED,
                    method=ThreeDSMethod.IFRAME,
                    transaction_id="",
                )

        if "3ds" in url or "secure" in url or "acs" in url:
            return ThreeDSChallenge(
                version=ThreeDSVersion.V1,
                status=ThreeDSStatus.CHALLENGE_REQUIRED,
                acs_url=target.url,
                method=ThreeDSMethod.REDIRECT,
                transaction_id="",
            )

        return None


__all__ = [
    "ThreeDSChallenge",
    "ThreeDSHandler",
    "ThreeDSMethod",
    "ThreeDSResult",
    "ThreeDSStatus",
    "ThreeDSVersion",
]
