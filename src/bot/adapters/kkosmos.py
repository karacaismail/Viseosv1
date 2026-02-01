"""
VISE OS KKOSMOS Adapter.

KKOSMOS booking adapter implementing SMS/Voice verification for
Greece Schengen visa appointments. Phone number pool management,
SMS code extraction, and voice call handling.

Features:
- Mandatory SMS/Voice verification on every booking
- Phone number pool management with provider support
- SMS code extraction from multiple providers (5sim, SMSHub, SMSActivate)
- Voice call handling with fallback to human escalation
- Aggressive rate limiting handling (3 hour ban)
- 15 minute session timeout

Technical Profile:
- Anti-Bot: reCAPTCHA + SMS/Phone Verification
- Rate Limiting: Aggressive, 3 hour ban
- Session Timeout: 15 minutes
- CAPTCHA: reCAPTCHA v2
- TLS Fingerprinting: Minimal
- Risk Level: HIGH - phone calls may occur

Usage:
    from src.bot.adapters.kkosmos import KKOSMOSAdapter
    from src.bot.adapters import AdapterConfig, SiteCode

    config = AdapterConfig(
        site_code=SiteCode.KKOSMOS,
        base_url="https://www.kkosmos.gr",
    )

    adapter = KKOSMOSAdapter(
        config=config,
        stealth_engine=stealth_engine,
        proxy_manager=proxy_manager,
        captcha_solver=captcha_solver,
        phone_pool=phone_pool,
    )

    async with adapter.create_session(account) as session:
        await adapter.login(session, account)
        slots = await adapter.search_slots(session, criteria)
        result = await adapter.book_slot(session, slots[0], applicant)
"""

from __future__ import annotations

import asyncio
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

import structlog

from src.bot.adapters.base import (
    AdapterConfig,
    AdapterSession,
    BaseSiteAdapter,
    BookingResult,
    BookingResultStatus,
    SiteCode,
    Slot,
    SlotSearchCriteria,
    SlotStatus,
)
from src.core.exceptions import (
    AccountBannedError,
    BookingError,
    BrowserTimeoutError,
    CaptchaError,
    CaptchaSolveTimeoutError,
    LoginFailedError,
    NoSlotsFoundError,
    SelectorNotFoundError,
    SiteAdapterError,
    SlotNotAvailableError,
    VerificationError,
)

if TYPE_CHECKING:
    from playwright.async_api import Page

    from src.bot.engine.stealth import StealthEngine
    from src.bot.services.account import BotAccount
    from src.bot.services.captcha import CaptchaSolver
    from src.bot.services.proxy import ProxyManager


logger = structlog.get_logger(__name__)


# =============================================================================
# Constants and Enums
# =============================================================================


class KKOSMOSFlowState(Enum):
    """KKOSMOS booking flow states."""

    INITIAL = "initial"
    NAVIGATING = "navigating"
    LOGIN_PAGE = "login_page"
    AUTHENTICATING = "authenticating"
    AUTHENTICATED = "authenticated"
    SELECTING_SERVICE = "selecting_service"
    CHECKING_SLOTS = "checking_slots"
    SLOT_FOUND = "slot_found"
    FILLING_FORM = "filling_form"
    SOLVING_CAPTCHA = "solving_captcha"
    REQUESTING_VERIFICATION = "requesting_verification"
    WAITING_SMS = "waiting_sms"
    WAITING_VOICE = "waiting_voice"
    ENTERING_CODE = "entering_code"
    CONFIRMING = "confirming"
    COMPLETED = "completed"
    FAILED = "failed"


class PhoneStatus(Enum):
    """Phone number status in pool."""

    AVAILABLE = "available"
    IN_USE = "in_use"
    COOLDOWN = "cooldown"
    BURNED = "burned"
    VOICE_ONLY = "voice_only"


# =============================================================================
# KKOSMOS URL and Endpoint Builder
# =============================================================================


class KKOSMOSURLBuilder:
    """KKOSMOS URL builder."""

    BASE_URL = "https://www.kkosmos.gr"
    APPOINTMENT_BASE = f"{BASE_URL}/appointment"

    ENDPOINTS = {
        "home": "/",
        "login": "/login",
        "register": "/register",
        "appointment": "/book",
        "calendar": "/calendar",
        "verification": "/verify",
        "confirmation": "/confirmation",
    }

    # Service centers
    CENTERS = {
        "istanbul": "IST",
        "ankara": "ANK",
        "izmir": "IZM",
    }

    # Visa types
    VISA_TYPES = {
        "tourist": "C_TOURIST",
        "business": "C_BUSINESS",
        "family": "C_FAMILY",
        "student": "D_STUDENT",
        "transit": "B_TRANSIT",
    }

    @classmethod
    def get_login_url(cls) -> str:
        """Get login URL."""
        return f"{cls.BASE_URL}{cls.ENDPOINTS['login']}"

    @classmethod
    def get_appointment_url(cls) -> str:
        """Get appointment booking URL."""
        return f"{cls.APPOINTMENT_BASE}{cls.ENDPOINTS['appointment']}"

    @classmethod
    def get_calendar_url(cls) -> str:
        """Get calendar URL."""
        return f"{cls.BASE_URL}{cls.ENDPOINTS['calendar']}"

    @classmethod
    def get_verification_url(cls) -> str:
        """Get verification URL."""
        return f"{cls.BASE_URL}{cls.ENDPOINTS['verification']}"

    @classmethod
    def get_confirmation_url(cls) -> str:
        """Get confirmation URL."""
        return f"{cls.BASE_URL}{cls.ENDPOINTS['confirmation']}"

    @classmethod
    def get_center_code(cls, center: str) -> str:
        """Get center code from center name."""
        return cls.CENTERS.get(center.lower(), center.upper())

    @classmethod
    def get_visa_code(cls, visa_type: str) -> str:
        """Get visa code from type name."""
        return cls.VISA_TYPES.get(visa_type.lower(), visa_type.upper())


# =============================================================================
# KKOSMOS Selectors
# =============================================================================


class KKOSMOSSelectors:
    """CSS selectors for KKOSMOS pages."""

    # Login page
    EMAIL_INPUT = 'input[name="email"], input[type="email"], #email'
    PASSWORD_INPUT = 'input[name="password"], input[type="password"], #password'
    LOGIN_BUTTON = 'button[type="submit"], .login-btn, #loginBtn'

    # Service Selection
    VISA_TYPE_SELECT = '#visaType, select[name="visaType"]'
    CENTER_SELECT = '#center, select[name="center"]'

    # Calendar
    CALENDAR = "#calendar, .appointment-calendar"
    AVAILABLE_DATE = ".available, .open"
    SELECTED_DATE = ".selected, .active"
    NEXT_MONTH = ".next-month, .calendar-next"
    PREV_MONTH = ".prev-month, .calendar-prev"

    # Time Selection
    TIME_SLOTS = ".time-slot, .slot"
    TIME_SLOT_AVAILABLE = ".time-slot:not(.unavailable)"

    # Applicant Form
    FIRST_NAME = "#firstName"
    LAST_NAME = "#lastName"
    BIRTH_DATE = "#birthDate"
    PASSPORT_NUMBER = "#passportNumber"
    PASSPORT_EXPIRY = "#passportExpiry"
    NATIONALITY = "#nationality"
    PHONE = "#phone"
    EMAIL_APPLICANT = "#applicantEmail"

    # Verification - CRITICAL for KKOSMOS
    VERIFICATION_PHONE = "#verificationPhone"
    SEND_SMS_BTN = '#sendSMS, button:has-text("SMS")'
    CALL_ME_BTN = '#callMe, button:has-text("Call")'
    VERIFICATION_CODE = '#verificationCode, input[name="code"]'
    VERIFY_BTN = '#verifyBtn, button:has-text("Verify")'

    # CAPTCHA
    RECAPTCHA = ".g-recaptcha, #recaptcha"

    # Confirmation
    TERMS_CHECKBOX = '#terms, input[name="terms"]'
    SUBMIT_BUTTON = "#submitBtn"

    # Result
    SUCCESS_MESSAGE = ".success, .confirmation"
    ERROR_MESSAGE = ".error, .alert-danger"
    CONFIRMATION_NUMBER = "#confirmationNumber"

    # Dashboard indicators (post-login)
    DASHBOARD_INDICATORS = [
        ".user-info",
        ".logout",
        "#visaType",
        ".welcome",
        ".user-menu",
        ".appointment-form",
    ]


# =============================================================================
# Phone Number Data Classes
# =============================================================================


@dataclass
class PhoneNumber:
    """Virtual phone number for SMS/Voice verification."""

    id: str
    provider: str  # "5sim", "smshub", "smsactivate"
    number: str  # Full number with country code
    country: str  # "tr", "de", etc.
    status: PhoneStatus

    usage_count: int = 0
    max_usage: int = 10

    last_used_at: datetime | None = None
    cooldown_until: datetime | None = None
    burned_at: datetime | None = None

    # SMS tracking
    sms_received: int = 0
    sms_failed: int = 0

    # Voice call tracking
    voice_received: int = 0
    voice_failed: int = 0


# =============================================================================
# Phone Pool Manager
# =============================================================================


class PhonePoolManager:
    """Virtual phone number pool manager for SMS/Voice verification."""

    # Cooldown settings
    COOLDOWN_AFTER_USE_MINUTES = 30
    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self) -> None:
        """Initialize phone pool manager."""
        self.phones: dict[str, PhoneNumber] = {}
        self.lock = asyncio.Lock()
        self.providers: dict[str, Any] = {}

    async def initialize(self, provider_configs: dict[str, str]) -> None:
        """
        Initialize SMS providers.

        Args:
            provider_configs: Provider API keys.
        """
        if provider_configs.get("5sim_api_key"):
            self.providers["5sim"] = FiveSimClient(provider_configs["5sim_api_key"])

        if provider_configs.get("smshub_api_key"):
            self.providers["smshub"] = SMSHubClient(provider_configs["smshub_api_key"])

        if provider_configs.get("smsactivate_api_key"):
            self.providers["smsactivate"] = SMSActivateClient(
                provider_configs["smsactivate_api_key"]
            )

        logger.info(
            "phone_pool_initialized",
            providers=list(self.providers.keys()),
        )

    async def acquire_number(
        self,
        country: str = "tr",
        service: str = "kkosmos",
        prefer_voice: bool = False,
    ) -> PhoneNumber | None:
        """
        Acquire a phone number from pool or purchase new.

        Args:
            country: Country code.
            service: Service name for purchase.
            prefer_voice: Prefer numbers that support voice calls.

        Returns:
            PhoneNumber or None if unavailable.
        """
        async with self.lock:
            # Try to find available number in pool
            phone = self._find_available(country, prefer_voice)

            if phone:
                phone.status = PhoneStatus.IN_USE
                phone.last_used_at = datetime.utcnow()
                logger.info(
                    "phone_acquired_from_pool",
                    phone_id=phone.id,
                    number=phone.number[-4:],  # Last 4 digits for privacy
                )
                return phone

            # No available, purchase new
            phone = await self._purchase_new(country, service)

            if phone:
                self.phones[phone.id] = phone
                phone.status = PhoneStatus.IN_USE
                logger.info(
                    "phone_purchased",
                    phone_id=phone.id,
                    provider=phone.provider,
                )
                return phone

            logger.warning("phone_acquisition_failed", country=country)
            return None

    def _find_available(
        self,
        country: str,
        prefer_voice: bool,
    ) -> PhoneNumber | None:
        """
        Find available number in pool.

        Args:
            country: Country code.
            prefer_voice: Prefer voice-capable numbers.

        Returns:
            PhoneNumber or None.
        """
        now = datetime.utcnow()
        candidates: list[tuple[PhoneNumber, int]] = []

        for phone in self.phones.values():
            # Basic checks
            if phone.country != country:
                continue

            if phone.status == PhoneStatus.BURNED:
                continue

            if phone.usage_count >= phone.max_usage:
                continue

            # Cooldown check
            if phone.cooldown_until and phone.cooldown_until > now:
                continue

            # Voice preference scoring
            if prefer_voice and phone.status == PhoneStatus.VOICE_ONLY:
                candidates.append((phone, 10))  # Higher priority
            elif phone.status == PhoneStatus.AVAILABLE:
                candidates.append((phone, 5))

        if not candidates:
            return None

        # Sort by priority (descending)
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]

    async def _purchase_new(
        self,
        country: str,
        service: str,
    ) -> PhoneNumber | None:
        """
        Purchase new phone number from provider.

        Args:
            country: Country code.
            service: Service name.

        Returns:
            PhoneNumber or None.
        """
        for provider_name, client in self.providers.items():
            try:
                result = await client.purchase_number(
                    country=country,
                    service=service,
                )

                if result:
                    return PhoneNumber(
                        id=result["id"],
                        provider=provider_name,
                        number=result["number"],
                        country=country,
                        status=PhoneStatus.IN_USE,
                    )
            except Exception as e:
                logger.warning(
                    "phone_purchase_failed",
                    provider=provider_name,
                    error=str(e),
                )
                continue

        return None

    async def release_number(
        self,
        phone_id: str,
        success: bool,
        sms_received: bool = False,
        voice_received: bool = False,
    ) -> None:
        """
        Release phone number back to pool.

        Args:
            phone_id: Phone number ID.
            success: Whether the verification was successful.
            sms_received: Whether SMS was received.
            voice_received: Whether voice call was received.
        """
        async with self.lock:
            if phone_id not in self.phones:
                return

            phone = self.phones[phone_id]
            phone.usage_count += 1

            if success:
                if sms_received:
                    phone.sms_received += 1
                if voice_received:
                    phone.voice_received += 1

                # Set cooldown
                phone.cooldown_until = datetime.utcnow() + timedelta(
                    minutes=self.COOLDOWN_AFTER_USE_MINUTES
                )
                phone.status = PhoneStatus.COOLDOWN

                logger.info(
                    "phone_released_success",
                    phone_id=phone_id,
                    cooldown_until=phone.cooldown_until.isoformat(),
                )
            else:
                if sms_received:
                    phone.sms_failed += 1
                if voice_received:
                    phone.voice_failed += 1

                # Check for burn
                total_failures = phone.sms_failed + phone.voice_failed
                if total_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    phone.status = PhoneStatus.BURNED
                    phone.burned_at = datetime.utcnow()
                    logger.warning(
                        "phone_burned",
                        phone_id=phone_id,
                        total_failures=total_failures,
                    )
                else:
                    phone.status = PhoneStatus.AVAILABLE

    async def get_pool_stats(self) -> dict[str, Any]:
        """
        Get pool statistics.

        Returns:
            Pool statistics dictionary.
        """
        stats = {
            "total": len(self.phones),
            "available": 0,
            "in_use": 0,
            "cooldown": 0,
            "burned": 0,
            "voice_only": 0,
        }

        for phone in self.phones.values():
            stats[phone.status.value] += 1

        return stats


# =============================================================================
# SMS Provider Clients
# =============================================================================


class FiveSimClient:
    """5sim.net API client for SMS verification."""

    BASE_URL = "https://5sim.net/v1"

    def __init__(self, api_key: str) -> None:
        """Initialize 5sim client."""
        self.api_key = api_key
        self._client: Any = None

    async def _get_client(self) -> Any:
        """Get or create httpx client."""
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=30.0,
            )
        return self._client

    async def purchase_number(
        self,
        country: str,
        service: str,
    ) -> dict[str, str] | None:
        """
        Purchase phone number.

        Args:
            country: Country code.
            service: Service name.

        Returns:
            Dict with id and number, or None.
        """
        client = await self._get_client()

        # 5sim country mapping
        country_code = {"tr": "turkey", "de": "germany", "gr": "greece"}.get(
            country, country
        )

        try:
            response = await client.get(
                f"{self.BASE_URL}/user/buy/activation/{country_code}/any/{service}"
            )

            if response.status_code == 200:
                data = response.json()
                return {
                    "id": str(data["id"]),
                    "number": data["phone"],
                }
        except Exception as e:
            logger.warning("5sim_purchase_error", error=str(e))

        return None

    async def get_sms(self, order_id: str, timeout: int = 120) -> str | None:
        """
        Wait for and retrieve SMS code.

        Args:
            order_id: Order ID from purchase.
            timeout: Timeout in seconds.

        Returns:
            Verification code or None.
        """
        client = await self._get_client()
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                response = await client.get(f"{self.BASE_URL}/user/check/{order_id}")

                if response.status_code == 200:
                    data = response.json()

                    if data.get("sms"):
                        # Extract code from SMS text
                        code = self._extract_code(data["sms"][0]["text"])
                        if code:
                            logger.info("5sim_sms_received", order_id=order_id)
                            return code

            except Exception as e:
                logger.warning("5sim_check_error", order_id=order_id, error=str(e))

            await asyncio.sleep(5)

        logger.warning("5sim_sms_timeout", order_id=order_id)
        return None

    def _extract_code(self, sms_text: str) -> str | None:
        """
        Extract verification code from SMS text.

        Args:
            sms_text: SMS message text.

        Returns:
            Extracted code or None.
        """
        patterns = [
            r"(\d{6})",  # 6 digit code
            r"(\d{4})",  # 4 digit code
            r"kod[u]?:\s*(\d+)",  # Turkish: "kod: XXXX"
            r"code[:]?\s*(\d+)",  # English: "code: XXXX"
        ]

        for pattern in patterns:
            match = re.search(pattern, sms_text, re.IGNORECASE)
            if match:
                return match.group(1)

        return None

    async def cancel_number(self, order_id: str) -> None:
        """Cancel order for refund."""
        client = await self._get_client()
        try:
            await client.get(f"{self.BASE_URL}/user/cancel/{order_id}")
        except Exception as e:
            logger.warning("5sim_cancel_error", order_id=order_id, error=str(e))

    async def finish_number(self, order_id: str) -> None:
        """Mark order as finished."""
        client = await self._get_client()
        try:
            await client.get(f"{self.BASE_URL}/user/finish/{order_id}")
        except Exception as e:
            logger.warning("5sim_finish_error", order_id=order_id, error=str(e))


class SMSHubClient:
    """SMSHub API client for SMS verification."""

    BASE_URL = "https://smshub.org/stubs/handler_api.php"

    def __init__(self, api_key: str) -> None:
        """Initialize SMSHub client."""
        self.api_key = api_key
        self._client: Any = None

    async def _get_client(self) -> Any:
        """Get or create httpx client."""
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def purchase_number(
        self,
        country: str,
        service: str,
    ) -> dict[str, str] | None:
        """
        Purchase phone number.

        Args:
            country: Country code.
            service: Service name.

        Returns:
            Dict with id and number, or None.
        """
        client = await self._get_client()

        # SMSHub country codes
        country_code = {"tr": "62", "de": "43", "gr": "33"}.get(country, "0")

        try:
            response = await client.get(
                self.BASE_URL,
                params={
                    "api_key": self.api_key,
                    "action": "getNumber",
                    "service": "ot",  # Other service
                    "country": country_code,
                },
            )

            if response.text.startswith("ACCESS_NUMBER"):
                parts = response.text.split(":")
                return {
                    "id": parts[1],
                    "number": parts[2],
                }
        except Exception as e:
            logger.warning("smshub_purchase_error", error=str(e))

        return None

    async def get_sms(self, order_id: str, timeout: int = 120) -> str | None:
        """
        Wait for and retrieve SMS code.

        Args:
            order_id: Order ID from purchase.
            timeout: Timeout in seconds.

        Returns:
            Verification code or None.
        """
        client = await self._get_client()
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                response = await client.get(
                    self.BASE_URL,
                    params={
                        "api_key": self.api_key,
                        "action": "getStatus",
                        "id": order_id,
                    },
                )

                if response.text.startswith("STATUS_OK"):
                    code = response.text.split(":")[1]
                    logger.info("smshub_sms_received", order_id=order_id)
                    return code

            except Exception as e:
                logger.warning("smshub_check_error", order_id=order_id, error=str(e))

            await asyncio.sleep(5)

        logger.warning("smshub_sms_timeout", order_id=order_id)
        return None


class SMSActivateClient:
    """SMSActivate API client for SMS verification."""

    BASE_URL = "https://sms-activate.org/stubs/handler_api.php"

    def __init__(self, api_key: str) -> None:
        """Initialize SMSActivate client."""
        self.api_key = api_key
        self._client: Any = None

    async def _get_client(self) -> Any:
        """Get or create httpx client."""
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def purchase_number(
        self,
        country: str,
        service: str,
    ) -> dict[str, str] | None:
        """
        Purchase phone number.

        Args:
            country: Country code.
            service: Service name.

        Returns:
            Dict with id and number, or None.
        """
        client = await self._get_client()

        # SMSActivate country codes
        country_code = {"tr": "62", "de": "43", "gr": "33"}.get(country, "0")

        try:
            response = await client.get(
                self.BASE_URL,
                params={
                    "api_key": self.api_key,
                    "action": "getNumber",
                    "service": "ot",
                    "country": country_code,
                },
            )

            if response.text.startswith("ACCESS_NUMBER"):
                parts = response.text.split(":")
                return {
                    "id": parts[1],
                    "number": parts[2],
                }
        except Exception as e:
            logger.warning("smsactivate_purchase_error", error=str(e))

        return None

    async def get_sms(self, order_id: str, timeout: int = 120) -> str | None:
        """
        Wait for and retrieve SMS code.

        Args:
            order_id: Order ID from purchase.
            timeout: Timeout in seconds.

        Returns:
            Verification code or None.
        """
        client = await self._get_client()
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                response = await client.get(
                    self.BASE_URL,
                    params={
                        "api_key": self.api_key,
                        "action": "getStatus",
                        "id": order_id,
                    },
                )

                if response.text.startswith("STATUS_OK"):
                    code = response.text.split(":")[1]
                    logger.info("smsactivate_sms_received", order_id=order_id)
                    return code

            except Exception as e:
                logger.warning(
                    "smsactivate_check_error", order_id=order_id, error=str(e)
                )

            await asyncio.sleep(5)

        logger.warning("smsactivate_sms_timeout", order_id=order_id)
        return None


# =============================================================================
# Verification Handler
# =============================================================================


class KKOSMOSVerificationHandler:
    """KKOSMOS SMS/Voice verification handler."""

    async def handle_verification(
        self,
        adapter: KKOSMOSAdapter,
        preference: str = "sms",
    ) -> dict[str, Any]:
        """
        Handle phone verification process.

        Args:
            adapter: KKOSMOS adapter instance.
            preference: Preferred method ("sms" or "voice").

        Returns:
            Dict with success status and method used.
        """
        page = adapter.session.page
        phone = adapter.current_phone

        if not phone:
            return {"success": False, "error": "No phone number available"}

        # Enter phone number
        adapter.flow_state = KKOSMOSFlowState.REQUESTING_VERIFICATION

        phone_input = await page.query_selector(KKOSMOSSelectors.VERIFICATION_PHONE)

        if phone_input:
            await phone_input.fill(phone.number)
            await asyncio.sleep(0.5)

        # Try preferred method first
        if preference == "sms":
            result = await self._try_sms_verification(adapter)

            if not result["success"]:
                # Fallback to voice
                result = await self._try_voice_verification(adapter)
        else:
            result = await self._try_voice_verification(adapter)

            if not result["success"]:
                # Fallback to SMS
                result = await self._try_sms_verification(adapter)

        return result

    async def _try_sms_verification(
        self,
        adapter: KKOSMOSAdapter,
    ) -> dict[str, Any]:
        """
        Try SMS verification.

        Args:
            adapter: KKOSMOS adapter instance.

        Returns:
            Dict with success status.
        """
        page = adapter.session.page
        phone = adapter.current_phone

        if not phone:
            return {"success": False, "error": "No phone available"}

        # Click send SMS
        sms_btn = await page.query_selector(KKOSMOSSelectors.SEND_SMS_BTN)

        if not sms_btn:
            return {"success": False, "error": "SMS button not found"}

        await sms_btn.click()

        adapter.flow_state = KKOSMOSFlowState.WAITING_SMS

        logger.info(
            "kkosmos_waiting_sms",
            phone_id=phone.id,
        )

        # Wait for SMS from provider
        provider = adapter.phone_pool.providers.get(phone.provider)

        if not provider:
            return {"success": False, "error": "SMS provider not available"}

        code = await provider.get_sms(
            order_id=phone.id,
            timeout=KKOSMOSAdapter.SMS_TIMEOUT,
        )

        if not code:
            return {"success": False, "error": "SMS not received", "method": "sms"}

        # Enter code
        adapter.flow_state = KKOSMOSFlowState.ENTERING_CODE

        code_input = await page.query_selector(KKOSMOSSelectors.VERIFICATION_CODE)

        if code_input:
            await code_input.fill(code)
            await asyncio.sleep(0.5)

            # Click verify
            verify_btn = await page.query_selector(KKOSMOSSelectors.VERIFY_BTN)

            if verify_btn:
                await verify_btn.click()
                await asyncio.sleep(2)

                # Check if verification succeeded
                error = await page.query_selector(KKOSMOSSelectors.ERROR_MESSAGE)

                if not error:
                    logger.info(
                        "kkosmos_sms_verification_success",
                        phone_id=phone.id,
                    )
                    return {"success": True, "method": "sms", "code": code}

        return {"success": False, "error": "Code verification failed", "method": "sms"}

    async def _try_voice_verification(
        self,
        adapter: KKOSMOSAdapter,
    ) -> dict[str, Any]:
        """
        Try voice call verification.

        Args:
            adapter: KKOSMOS adapter instance.

        Returns:
            Dict with success status.
        """
        page = adapter.session.page
        phone = adapter.current_phone

        if not phone:
            return {"success": False, "error": "No phone available"}

        # Click call me
        call_btn = await page.query_selector(KKOSMOSSelectors.CALL_ME_BTN)

        if not call_btn:
            return {"success": False, "error": "Call button not found"}

        await call_btn.click()

        adapter.flow_state = KKOSMOSFlowState.WAITING_VOICE

        logger.info(
            "kkosmos_waiting_voice",
            phone_id=phone.id,
        )

        # Voice call handling is more complex
        # Try provider voice code if available
        code = await self._wait_for_voice_code(adapter)

        if not code:
            return {
                "success": False,
                "error": "Voice code not received",
                "method": "voice",
            }

        # Enter code
        adapter.flow_state = KKOSMOSFlowState.ENTERING_CODE

        code_input = await page.query_selector(KKOSMOSSelectors.VERIFICATION_CODE)

        if code_input:
            await code_input.fill(code)
            await asyncio.sleep(0.5)

            verify_btn = await page.query_selector(KKOSMOSSelectors.VERIFY_BTN)

            if verify_btn:
                await verify_btn.click()
                await asyncio.sleep(2)

                error = await page.query_selector(KKOSMOSSelectors.ERROR_MESSAGE)

                if not error:
                    logger.info(
                        "kkosmos_voice_verification_success",
                        phone_id=phone.id,
                    )
                    return {"success": True, "method": "voice", "code": code}

        return {
            "success": False,
            "error": "Voice code verification failed",
            "method": "voice",
        }

    async def _wait_for_voice_code(
        self,
        adapter: KKOSMOSAdapter,
    ) -> str | None:
        """
        Wait for voice call code.

        Args:
            adapter: KKOSMOS adapter instance.

        Returns:
            Voice code or None.
        """
        phone = adapter.current_phone

        if not phone:
            return None

        provider = adapter.phone_pool.providers.get(phone.provider)

        # Try provider's voice code method if available
        if provider and hasattr(provider, "get_voice_code"):
            return await provider.get_voice_code(
                order_id=phone.id,
                timeout=KKOSMOSAdapter.VOICE_TIMEOUT,
            )

        # Fallback: human escalation would go here
        # For now, return None (fail)
        logger.warning(
            "kkosmos_voice_no_handler",
            phone_id=phone.id,
        )
        return None


# =============================================================================
# Error Patterns and Classifier
# =============================================================================


class KKOSMOSErrorPatterns:
    """Error detection patterns for KKOSMOS."""

    VERIFICATION_FAILED = [
        r"verification.*failed",
        r"invalid.*code",
        r"kod.*hatal[ıi]",
    ]

    PHONE_BLOCKED = [
        r"phone.*blocked",
        r"number.*blacklist",
        r"numara.*engel",
    ]

    RATE_LIMIT = [
        r"too.*many.*attempts",
        r"try.*later",
        r"3.*hour",
    ]

    SESSION_EXPIRED = [
        r"session.*expired",
        r"login.*again",
    ]

    SLOT_TAKEN = [
        r"slot.*taken",
        r"no.*available",
        r"already.*booked",
    ]


@dataclass
class KKOSMOSErrorClassification:
    """Classification result for KKOSMOS errors."""

    error_type: str
    retriable: bool
    cooldown_seconds: int
    new_phone_required: bool = False


class KKOSMOSErrorClassifier:
    """Classifies KKOSMOS errors for appropriate handling."""

    ERROR_PATTERNS = {
        "verification_failed": KKOSMOSErrorPatterns.VERIFICATION_FAILED,
        "phone_blocked": KKOSMOSErrorPatterns.PHONE_BLOCKED,
        "rate_limit": KKOSMOSErrorPatterns.RATE_LIMIT,
        "session_expired": KKOSMOSErrorPatterns.SESSION_EXPIRED,
        "slot_taken": KKOSMOSErrorPatterns.SLOT_TAKEN,
    }

    @staticmethod
    def classify(error_message: str) -> KKOSMOSErrorClassification:
        """
        Classify an error message.

        Args:
            error_message: Error message to classify.

        Returns:
            KKOSMOSErrorClassification with handling instructions.
        """
        error_lower = error_message.lower()

        for error_type, patterns in KKOSMOSErrorClassifier.ERROR_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, error_lower):
                    return KKOSMOSErrorClassification(
                        error_type=error_type.upper(),
                        retriable=error_type not in ["phone_blocked"],
                        cooldown_seconds=10800 if error_type == "rate_limit" else 60,
                        new_phone_required=error_type
                        in ["verification_failed", "phone_blocked"],
                    )

        return KKOSMOSErrorClassification(
            error_type="UNKNOWN",
            retriable=True,
            cooldown_seconds=60,
        )


# =============================================================================
# KKOSMOS Adapter Main Class
# =============================================================================


class KKOSMOSAdapter(BaseSiteAdapter):
    """
    KKOSMOS booking adapter.

    Implements the SMS/Voice verification booking flow for KKOSMOS
    Greek Schengen visa appointments.

    HIGH RISK: This adapter handles mandatory phone verification.
    Phone numbers may receive actual calls.

    Attributes:
        flow_state: Current state in the booking flow.
        selectors: KKOSMOS-specific CSS selectors.
        url_builder: KKOSMOS URL builder.
        phone_pool: Phone number pool manager.
        current_phone: Currently acquired phone number.
        verification_handler: SMS/Voice verification handler.
    """

    # Timeouts (seconds)
    PAGE_LOAD_TIMEOUT = 45
    ELEMENT_TIMEOUT = 20
    SMS_TIMEOUT = 120  # 2 minutes for SMS
    VOICE_TIMEOUT = 180  # 3 minutes for voice call
    SESSION_TIMEOUT = 900  # 15 minutes

    def __init__(
        self,
        config: AdapterConfig | None = None,
        stealth_engine: StealthEngine | None = None,
        proxy_manager: ProxyManager | None = None,
        captcha_solver: CaptchaSolver | None = None,
        phone_pool: PhonePoolManager | None = None,
    ) -> None:
        """
        Initialize the KKOSMOS adapter.

        Args:
            config: Adapter configuration. Defaults to KKOSMOS config if None.
            stealth_engine: Browser stealth engine.
            proxy_manager: Proxy rotation manager.
            captcha_solver: CAPTCHA solving service.
            phone_pool: Phone number pool manager for verification.
        """
        if config is None:
            config = AdapterConfig(
                site_code=SiteCode.KKOSMOS,
                base_url=KKOSMOSURLBuilder.BASE_URL,
            )

        super().__init__(
            config=config,
            stealth_engine=stealth_engine,
            proxy_manager=proxy_manager,
            captcha_solver=captcha_solver,
        )

        self.flow_state = KKOSMOSFlowState.INITIAL
        self.selectors = KKOSMOSSelectors
        self.url_builder = KKOSMOSURLBuilder

        # Phone pool for SMS/Voice verification
        self.phone_pool = phone_pool or PhonePoolManager()
        self.current_phone: PhoneNumber | None = None
        self.verification_handler = KKOSMOSVerificationHandler()

        # Session reference for verification handler
        self.session: AdapterSession | None = None

        logger.info(
            "kkosmos_adapter_initialized",
            base_url=config.base_url,
            has_phone_pool=phone_pool is not None,
        )

    # -------------------------------------------------------------------------
    # Abstract Method Implementations
    # -------------------------------------------------------------------------

    async def login(
        self,
        session: AdapterSession,
        account: BotAccount,
    ) -> bool:
        """
        Authenticate with KKOSMOS.

        Args:
            session: Active adapter session.
            account: Bot account with credentials.

        Returns:
            True if login successful.

        Raises:
            LoginFailedError: If login fails after retries.
            AccountBannedError: If account is banned/locked.
        """
        page = session.page
        self.flow_state = KKOSMOSFlowState.NAVIGATING
        self.session = session

        logger.info(
            "kkosmos_login_started",
            session_id=session.id,
            account_id=account.id,
        )

        try:
            # Navigate to login page
            login_url = self.url_builder.get_login_url()
            await page.goto(login_url, wait_until="networkidle")
            session.increment_requests()

            # Verify we're on login page
            self.flow_state = KKOSMOSFlowState.LOGIN_PAGE
            email_input = await self.wait_for_element(
                page,
                self.selectors.EMAIL_INPUT,
                timeout=self.ELEMENT_TIMEOUT * 1000,
            )

            if not email_input:
                raise SelectorNotFoundError(
                    "Email input not found on login page",
                    selector=self.selectors.EMAIL_INPUT,
                    page_url=page.url,
                    site="kkosmos",
                )

            # Fill credentials
            self.flow_state = KKOSMOSFlowState.AUTHENTICATING
            await self._fill_login_form(page, account)

            # Submit login
            await page.click(self.selectors.LOGIN_BUTTON)
            await page.wait_for_load_state("networkidle")
            session.increment_requests()

            # Verify login success
            if await self._is_logged_in(page):
                self.flow_state = KKOSMOSFlowState.AUTHENTICATED
                session.is_authenticated = True
                session.login_time = datetime.utcnow()
                self.metrics.record_login(success=True)

                logger.info(
                    "kkosmos_login_success",
                    session_id=session.id,
                    account_id=account.id,
                )
                return True

            # Check for error messages
            error = await self._get_page_error(page)
            if error:
                classification = KKOSMOSErrorClassifier.classify(error)

                if classification.error_type == "RATE_LIMIT":
                    self.metrics.record_login(success=False)
                    raise AccountBannedError(
                        f"KKOSMOS rate limited (3 hour ban): {error}",
                        account_id=account.id,
                        ban_reason=error,
                        site="kkosmos",
                    )

                if self._check_ban_indicators(error):
                    self.metrics.record_login(success=False)
                    raise AccountBannedError(
                        f"KKOSMOS account banned: {error}",
                        account_id=account.id,
                        ban_reason=error,
                        site="kkosmos",
                    )

                self.metrics.record_login(success=False)
                raise LoginFailedError(
                    f"KKOSMOS login failed: {error}",
                    site="kkosmos",
                    account_id=account.id,
                    reason=error,
                )

            self.metrics.record_login(success=False)
            return False

        except (LoginFailedError, AccountBannedError):
            raise
        except Exception as e:
            self.metrics.record_login(success=False)
            self.metrics.record_error("login_error")
            logger.error(
                "kkosmos_login_error",
                session_id=session.id,
                error=str(e),
            )
            raise LoginFailedError(
                f"KKOSMOS login failed: {e}",
                site="kkosmos",
                account_id=account.id,
            ) from e

    async def search_slots(
        self,
        session: AdapterSession,
        criteria: SlotSearchCriteria,
    ) -> list[Slot]:
        """
        Search for available appointment slots.

        Args:
            session: Active authenticated session.
            criteria: Search criteria for slots.

        Returns:
            List of available Slot objects.

        Raises:
            NoSlotsFoundError: If no slots match criteria.
            SiteAdapterError: For navigation or selection errors.
        """
        page = session.page
        start_time = time.time()
        slots: list[Slot] = []

        if not session.is_authenticated:
            raise SiteAdapterError(
                "Session not authenticated",
                site="kkosmos",
                step="search_slots",
            )

        logger.info(
            "kkosmos_slot_search_started",
            session_id=session.id,
            country=criteria.country,
            category=criteria.category,
        )

        try:
            # Navigate to appointment page
            self.flow_state = KKOSMOSFlowState.SELECTING_SERVICE
            await page.goto(
                self.url_builder.get_appointment_url(),
                wait_until="networkidle",
            )
            session.increment_requests()

            # Select visa type
            await self._select_visa_type(page, criteria.category)
            await self.human_delay(0.3, 0.5)

            # Select center
            center = criteria.city or "istanbul"
            await self._select_center(page, center)
            await self.human_delay(0.3, 0.5)

            # Scan calendar for available slots
            self.flow_state = KKOSMOSFlowState.CHECKING_SLOTS
            slots = await self._scan_calendar(page, criteria, center)

            # Record metrics
            duration_ms = (time.time() - start_time) * 1000
            self.metrics.record_search(len(slots), duration_ms)

            if slots:
                self.flow_state = KKOSMOSFlowState.SLOT_FOUND
                logger.info(
                    "kkosmos_slots_found",
                    session_id=session.id,
                    count=len(slots),
                    duration_ms=duration_ms,
                )
            else:
                logger.info(
                    "kkosmos_no_slots_found",
                    session_id=session.id,
                    criteria=criteria.to_dict(),
                )
                raise NoSlotsFoundError(
                    "No available slots found matching criteria",
                    site="kkosmos",
                    search_criteria=criteria.to_dict(),
                )

            return slots

        except NoSlotsFoundError:
            raise
        except Exception as e:
            self.metrics.record_error("search_error")
            logger.error(
                "kkosmos_slot_search_error",
                session_id=session.id,
                error=str(e),
            )
            raise SiteAdapterError(
                f"KKOSMOS slot search failed: {e}",
                site="kkosmos",
                step="search_slots",
            ) from e

    async def book_slot(
        self,
        session: AdapterSession,
        slot: Slot,
        applicant: dict[str, Any],
    ) -> BookingResult:
        """
        Book an appointment slot.

        INCLUDES MANDATORY SMS/VOICE VERIFICATION.

        Args:
            session: Active authenticated session.
            slot: Selected slot to book.
            applicant: Applicant data for form filling.

        Returns:
            BookingResult with confirmation or error details.

        Raises:
            SlotNotAvailableError: If slot is no longer available.
            VerificationError: If phone verification fails.
            SiteAdapterError: For booking failures.
        """
        page = session.page
        start_time = datetime.utcnow()

        if not session.is_authenticated:
            raise SiteAdapterError(
                "Session not authenticated",
                site="kkosmos",
                step="book_slot",
            )

        logger.info(
            "kkosmos_booking_started",
            session_id=session.id,
            slot_id=slot.id,
            slot_date=slot.date.isoformat(),
        )

        try:
            # Acquire phone number for verification
            self.current_phone = await self.phone_pool.acquire_number(
                country="tr",
                service="kkosmos",
            )

            if not self.current_phone:
                raise VerificationError(
                    "No phone number available for verification",
                    verification_type="sms",
                    site="kkosmos",
                )

            # Fill applicant form
            self.flow_state = KKOSMOSFlowState.FILLING_FORM
            await self._fill_applicant_form(page, applicant)
            await self.human_delay(0.5, 1.0)

            # Handle CAPTCHA if present
            captcha_element = await page.query_selector(self.selectors.RECAPTCHA)
            if captcha_element:
                self.flow_state = KKOSMOSFlowState.SOLVING_CAPTCHA
                captcha_solved = await self._handle_captcha(page, session)
                if not captcha_solved:
                    raise CaptchaError(
                        "Failed to solve CAPTCHA",
                        captcha_type="recaptcha_v2",
                    )

            # CRITICAL: Handle phone verification
            verification_result = await self.verification_handler.handle_verification(
                adapter=self,
                preference=applicant.get("verification_preference", "sms"),
            )

            if not verification_result["success"]:
                # Release phone with failure
                await self.phone_pool.release_number(
                    self.current_phone.id,
                    success=False,
                    sms_received=verification_result.get("method") == "sms",
                    voice_received=verification_result.get("method") == "voice",
                )

                raise VerificationError(
                    f"Phone verification failed: {verification_result.get('error')}",
                    verification_type=verification_result.get("method", "unknown"),
                    site="kkosmos",
                )

            # Submit booking
            self.flow_state = KKOSMOSFlowState.CONFIRMING
            await self._submit_booking(page)

            await page.wait_for_load_state("networkidle")
            session.increment_requests()

            # Check for errors
            error = await self._get_page_error(page)
            if error:
                classification = KKOSMOSErrorClassifier.classify(error)

                # Release phone
                await self.phone_pool.release_number(
                    self.current_phone.id,
                    success=False,
                )

                if classification.error_type == "SLOT_TAKEN":
                    raise SlotNotAvailableError(
                        f"Slot taken: {error}",
                        slot_id=slot.id,
                        slot_datetime=slot.datetime_str,
                        site="kkosmos",
                    )
                raise BookingError(error)

            # Check for success
            if await self._check_booking_result(page):
                self.flow_state = KKOSMOSFlowState.COMPLETED
                confirmation = await self._get_confirmation(page)

                # Take screenshot
                screenshot_path = await self.take_screenshot(page, "kkosmos_confirmation")

                # Release phone with success
                await self.phone_pool.release_number(
                    self.current_phone.id,
                    success=True,
                    sms_received=verification_result.get("method") == "sms",
                    voice_received=verification_result.get("method") == "voice",
                )

                completed_at = datetime.utcnow()
                duration = (completed_at - start_time).total_seconds()
                self.metrics.record_booking(success=True, duration_ms=duration * 1000)

                logger.info(
                    "kkosmos_booking_success",
                    session_id=session.id,
                    confirmation_number=confirmation.get("number"),
                    verification_method=verification_result.get("method"),
                    duration_seconds=duration,
                )

                return BookingResult(
                    status=BookingResultStatus.SUCCESS,
                    confirmation_number=confirmation.get("number"),
                    appointment_date=slot.date,
                    appointment_time=slot.time,
                    appointment_location=slot.location,
                    screenshot_path=screenshot_path,
                    raw_response={
                        "verification_method": verification_result.get("method"),
                        "phone_id": self.current_phone.id if self.current_phone else None,
                    },
                    started_at=start_time,
                    completed_at=completed_at,
                    duration_seconds=duration,
                )

            # Booking failed
            self.flow_state = KKOSMOSFlowState.FAILED

            # Release phone
            if self.current_phone:
                await self.phone_pool.release_number(
                    self.current_phone.id,
                    success=False,
                )

            completed_at = datetime.utcnow()
            duration = (completed_at - start_time).total_seconds()
            self.metrics.record_booking(success=False, duration_ms=duration * 1000)

            return BookingResult(
                status=BookingResultStatus.FAILED,
                error_code="BOOKING_FAILED",
                error_message="Booking confirmation not found",
                started_at=start_time,
                completed_at=completed_at,
                duration_seconds=duration,
            )

        except (SlotNotAvailableError, CaptchaError, VerificationError):
            raise
        except BookingError as e:
            # Release phone on error
            if self.current_phone:
                await self.phone_pool.release_number(
                    self.current_phone.id,
                    success=False,
                )

            completed_at = datetime.utcnow()
            duration = (completed_at - start_time).total_seconds()
            self.metrics.record_booking(success=False, duration_ms=duration * 1000)

            return BookingResult(
                status=BookingResultStatus.FAILED,
                error_code="BOOKING_ERROR",
                error_message=str(e),
                started_at=start_time,
                completed_at=completed_at,
                duration_seconds=duration,
            )
        except Exception as e:
            # Release phone on error
            if self.current_phone:
                await self.phone_pool.release_number(
                    self.current_phone.id,
                    success=False,
                )

            completed_at = datetime.utcnow()
            duration = (completed_at - start_time).total_seconds()
            self.metrics.record_booking(success=False, duration_ms=duration * 1000)
            self.metrics.record_error("booking_error")

            logger.error(
                "kkosmos_booking_error",
                session_id=session.id,
                error=str(e),
            )

            # Take error screenshot
            await self.take_screenshot(page, "kkosmos_booking_error")

            return BookingResult(
                status=BookingResultStatus.FAILED,
                error_code="BOOKING_ERROR",
                error_message=str(e),
                started_at=start_time,
                completed_at=completed_at,
                duration_seconds=duration,
            )

    # -------------------------------------------------------------------------
    # Login Helpers
    # -------------------------------------------------------------------------

    async def _fill_login_form(self, page: Page, account: BotAccount) -> None:
        """
        Fill login form with credentials.

        Args:
            page: Playwright page instance.
            account: Bot account with credentials.
        """
        # Email
        await page.fill(self.selectors.EMAIL_INPUT, account.email)
        await asyncio.sleep(random.uniform(0.3, 0.6))

        # Password
        await page.fill(self.selectors.PASSWORD_INPUT, account.password)
        await asyncio.sleep(random.uniform(0.2, 0.4))

    async def _is_logged_in(self, page: Page) -> bool:
        """
        Check if login was successful.

        Args:
            page: Playwright page instance.

        Returns:
            True if logged in.
        """
        # Check for dashboard/booking form elements
        for selector in self.selectors.DASHBOARD_INDICATORS:
            try:
                element = await page.query_selector(selector)
                if element:
                    return True
            except Exception:
                pass

        # Check URL for appointment/calendar
        if "/appointment" in page.url or "/calendar" in page.url:
            return True

        return False

    async def _get_page_error(self, page: Page) -> str | None:
        """
        Get error message from page if present.

        Args:
            page: Playwright page instance.

        Returns:
            Error message or None.
        """
        try:
            error_element = await page.query_selector(self.selectors.ERROR_MESSAGE)
            if error_element:
                return await error_element.inner_text()
        except Exception:
            pass
        return None

    # -------------------------------------------------------------------------
    # Slot Search Helpers
    # -------------------------------------------------------------------------

    async def _select_visa_type(self, page: Page, visa_type: str) -> None:
        """
        Select visa type from dropdown.

        Args:
            page: Playwright page instance.
            visa_type: Visa type name.
        """
        visa_code = self.url_builder.get_visa_code(visa_type)

        try:
            await page.wait_for_selector(
                self.selectors.VISA_TYPE_SELECT,
                timeout=self.ELEMENT_TIMEOUT * 1000,
            )

            await page.select_option(
                self.selectors.VISA_TYPE_SELECT,
                value=visa_code,
            )
            await asyncio.sleep(1)

        except Exception as e:
            logger.warning(
                "kkosmos_visa_type_selection_error",
                visa_type=visa_type,
                error=str(e),
            )

    async def _select_center(self, page: Page, center: str) -> None:
        """
        Select center from dropdown.

        Args:
            page: Playwright page instance.
            center: Center name.
        """
        center_code = self.url_builder.get_center_code(center)

        try:
            center_select = await page.query_selector(self.selectors.CENTER_SELECT)
            if center_select:
                await page.select_option(
                    self.selectors.CENTER_SELECT,
                    value=center_code,
                )
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(
                "kkosmos_center_selection_error",
                center=center,
                error=str(e),
            )

    async def _scan_calendar(
        self,
        page: Page,
        criteria: SlotSearchCriteria,
        center: str,
        max_months: int = 4,
    ) -> list[Slot]:
        """
        Scan calendar for available slots.

        Args:
            page: Playwright page instance.
            criteria: Search criteria.
            center: Center name.
            max_months: Maximum months to scan.

        Returns:
            List of available slots.
        """
        slots: list[Slot] = []
        target_from = criteria.from_date
        target_to = criteria.to_date or (date.today() + timedelta(days=90))

        for _ in range(max_months):
            # Find available date cells
            available_cells = await page.query_selector_all(
                self.selectors.AVAILABLE_DATE
            )

            for cell in available_cells:
                try:
                    date_attr = await cell.get_attribute("data-date")
                    if not date_attr:
                        continue

                    slot_date = datetime.strptime(date_attr, "%Y-%m-%d").date()

                    # Check date range
                    if slot_date < target_from or slot_date > target_to:
                        continue

                    # Check weekend exclusion
                    if criteria.exclude_weekends and slot_date.weekday() >= 5:
                        continue

                    # Click on date
                    await cell.click()
                    await asyncio.sleep(1)

                    # Get available time slots
                    time_slots = await self._get_available_times(page)

                    for time_slot in time_slots:
                        slots.append(
                            Slot(
                                id=f"kkosmos_{slot_date.isoformat()}_{time_slot}",
                                date=slot_date,
                                time=time_slot,
                                location=center,
                                category=criteria.category,
                                status=SlotStatus.AVAILABLE,
                                raw_data={
                                    "center_code": self.url_builder.get_center_code(
                                        center
                                    ),
                                    "visa_code": self.url_builder.get_visa_code(
                                        criteria.category
                                    ),
                                },
                            )
                        )

                except Exception:
                    continue

            # Check if we have enough slots
            if len(slots) >= 10:
                break

            # Navigate to next month
            next_btn = await page.query_selector(self.selectors.NEXT_MONTH)
            if next_btn:
                await next_btn.click()
                await asyncio.sleep(1)
            else:
                break

        return slots

    async def _get_available_times(self, page: Page) -> list[str]:
        """
        Get available time slots for selected date.

        Args:
            page: Playwright page instance.

        Returns:
            List of time strings.
        """
        times: list[str] = []

        try:
            time_elements = await page.query_selector_all(
                self.selectors.TIME_SLOT_AVAILABLE
            )

            for element in time_elements:
                time_text = await element.inner_text()
                if time_text.strip():
                    times.append(time_text.strip())

        except Exception as e:
            logger.warning(
                "kkosmos_time_selection_error",
                error=str(e),
            )

        return times[:5]  # Limit to first 5 times

    # -------------------------------------------------------------------------
    # Booking Helpers
    # -------------------------------------------------------------------------

    async def _fill_applicant_form(
        self,
        page: Page,
        applicant: dict[str, Any],
    ) -> None:
        """
        Fill applicant form.

        Args:
            page: Playwright page instance.
            applicant: Applicant data dictionary.
        """
        field_mapping = {
            "first_name": self.selectors.FIRST_NAME,
            "last_name": self.selectors.LAST_NAME,
            "birth_date": self.selectors.BIRTH_DATE,
            "passport_number": self.selectors.PASSPORT_NUMBER,
            "passport_expiry": self.selectors.PASSPORT_EXPIRY,
            "phone": self.selectors.PHONE,
            "email": self.selectors.EMAIL_APPLICANT,
        }

        for field_key, selector in field_mapping.items():
            value = applicant.get(field_key)
            if value:
                try:
                    field = await page.query_selector(selector)
                    if field:
                        await field.fill(str(value))
                        await asyncio.sleep(random.uniform(0.2, 0.4))
                except Exception:
                    pass

        # Nationality dropdown
        if applicant.get("nationality"):
            try:
                await page.select_option(
                    self.selectors.NATIONALITY,
                    value=applicant["nationality"],
                )
                await asyncio.sleep(random.uniform(0.2, 0.4))
            except Exception:
                pass

    async def _handle_captcha(
        self,
        page: Page,
        session: AdapterSession,
    ) -> bool:
        """
        Handle CAPTCHA on form submission.

        Args:
            page: Playwright page instance.
            session: Adapter session for tracking.

        Returns:
            True if CAPTCHA solved successfully.
        """
        if not self.captcha_solver:
            logger.warning("kkosmos_captcha_solver_not_available")
            return False

        try:
            solution = await self.captcha_solver.solve(
                page=page,
                captcha_type="recaptcha_v2",
                timeout=self.config.timeouts.captcha_solve,
            )

            if solution:
                session.increment_captchas()
                self.metrics.record_captcha(success=True)
                logger.info(
                    "kkosmos_captcha_solved",
                    session_id=session.id,
                )
                return True

        except Exception as e:
            self.metrics.record_captcha(success=False)
            logger.error(
                "kkosmos_captcha_solve_failed",
                session_id=session.id,
                error=str(e),
            )

        return False

    async def _submit_booking(self, page: Page) -> None:
        """
        Submit booking by checking terms and clicking submit.

        Args:
            page: Playwright page instance.
        """
        # Accept terms
        checkbox = await page.query_selector(self.selectors.TERMS_CHECKBOX)
        if checkbox:
            is_checked = await checkbox.is_checked()
            if not is_checked:
                await checkbox.click()
                await asyncio.sleep(0.3)

        await asyncio.sleep(random.uniform(0.3, 0.5))

        # Submit
        submit_btn = await page.wait_for_selector(
            self.selectors.SUBMIT_BUTTON,
            timeout=self.ELEMENT_TIMEOUT * 1000,
        )

        await submit_btn.click()

    async def _check_booking_result(self, page: Page) -> bool:
        """
        Check if booking was successful.

        Args:
            page: Playwright page instance.

        Returns:
            True if booking succeeded.
        """
        # Check for success message
        success = await page.query_selector(self.selectors.SUCCESS_MESSAGE)
        if success:
            return True

        # Check URL for confirmation
        if "confirmation" in page.url.lower():
            return True

        return False

    async def _get_confirmation(self, page: Page) -> dict[str, Any]:
        """
        Get booking confirmation details.

        Args:
            page: Playwright page instance.

        Returns:
            Confirmation dictionary.
        """
        confirmation: dict[str, Any] = {}

        try:
            # Get confirmation number
            conf_element = await page.query_selector(self.selectors.CONFIRMATION_NUMBER)
            if conf_element:
                confirmation["number"] = await conf_element.inner_text()
                confirmation["number"] = confirmation["number"].strip()

            # Get success message
            success_element = await page.query_selector(self.selectors.SUCCESS_MESSAGE)
            if success_element:
                confirmation["message"] = await success_element.inner_text()

        except Exception as e:
            logger.warning(
                "kkosmos_get_confirmation_error",
                error=str(e),
            )

        return confirmation


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "KKOSMOSAdapter",
    "KKOSMOSFlowState",
    "KKOSMOSURLBuilder",
    "KKOSMOSSelectors",
    "KKOSMOSVerificationHandler",
    "KKOSMOSErrorPatterns",
    "KKOSMOSErrorClassifier",
    "KKOSMOSErrorClassification",
    "PhonePoolManager",
    "PhoneNumber",
    "PhoneStatus",
    "FiveSimClient",
    "SMSHubClient",
    "SMSActivateClient",
]
