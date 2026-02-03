"""
SMS Verification Service.

This module provides SMS verification automation for visa appointment systems.
Features include virtual phone number provider integration, SMS/voice code extraction,
OTP entry automation, and multi-provider phone pool management.

Dependencies:
- Account Pool Manager: Phone-account linking
- State Machine: VERIFYING state handling
- Site Adapters: Verification trigger handling
- Directus: Phone pool storage

Usage:
    from src.bot.verification.sms import SMSVerifier, SMSProvider

    verifier = SMSVerifier()
    await verifier.initialize(configs)

    result = await verifier.verify_by_sms(
        country="tr",
        service="vfs",
        phone_entry_callback=enter_phone,
        code_entry_callback=enter_code,
    )

    if result["success"]:
        print(f"Verification code: {result['code']}")
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Coroutine

import httpx
import structlog

from src.core.exceptions import (
    OTPExtractionError,
    VerificationError,
    VerificationTimeoutError,
)

logger = structlog.get_logger(__name__)


# =============================================================================
# SMS Provider and Status Enums
# =============================================================================


class SMSProvider(Enum):
    """
    Supported SMS provider services.

    Attributes:
        FIVE_SIM: 5sim.net virtual phone service.
        SMS_HUB: SMSHub virtual phone service.
        SMS_ACTIVATE: SMS-Activate virtual phone service.
        SMS_PVA: SMSPVA virtual phone service.
        ONLINESIM: OnlineSim virtual phone service.
    """

    FIVE_SIM = "5sim"
    SMS_HUB = "smshub"
    SMS_ACTIVATE = "smsactivate"
    SMS_PVA = "smspva"
    ONLINESIM = "onlinesim"


class PhoneStatus(Enum):
    """
    Virtual phone number status.

    Attributes:
        AVAILABLE: Phone is available for use.
        WAITING_SMS: Waiting for SMS code.
        WAITING_CALL: Waiting for voice call.
        CODE_RECEIVED: Verification code received.
        COMPLETED: Verification completed successfully.
        EXPIRED: Phone number expired.
        CANCELLED: Phone number cancelled.
    """

    AVAILABLE = "available"
    WAITING_SMS = "waiting_sms"
    WAITING_CALL = "waiting_call"
    CODE_RECEIVED = "code_received"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class VirtualPhone:
    """
    Virtual phone number representation.

    Contains phone number details and received verification data.

    Attributes:
        id: Provider-assigned phone ID.
        provider: SMS provider service.
        number: Phone number string.
        country: Country code (e.g., "tr", "de").
        status: Current phone status.
        sms_code: Extracted SMS verification code.
        sms_text: Full SMS text content.
        voice_code: Extracted voice verification code.
        created_at: When the phone was acquired.
        expires_at: When the phone expires.
        code_received_at: When the code was received.
        service: Target service name (e.g., "vfs", "kkosmos").
        cost: Cost of the phone number.

    Example:
        phone = VirtualPhone(
            id="12345",
            provider=SMSProvider.FIVE_SIM,
            number="+905551234567",
            country="tr",
            service="vfs",
        )
    """

    id: str
    provider: SMSProvider
    number: str
    country: str
    status: PhoneStatus = PhoneStatus.AVAILABLE
    sms_code: str | None = None
    sms_text: str | None = None
    voice_code: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    code_received_at: datetime | None = None
    service: str = ""
    cost: float = 0.0


@dataclass
class SMSProviderConfig:
    """
    SMS provider configuration.

    Contains API credentials and settings for SMS providers.

    Attributes:
        provider: SMS provider type.
        api_key: Provider API key.
        base_url: Provider API base URL.
        price_sms: Price per SMS number.
        price_voice: Price per voice-capable number.
        timeout_seconds: Maximum wait time for code.
        max_retries: Maximum retry attempts.

    Example:
        config = SMSProviderConfig(
            provider=SMSProvider.FIVE_SIM,
            api_key="your_api_key",
            base_url="https://5sim.net/v1",
        )
    """

    provider: SMSProvider
    api_key: str
    base_url: str
    price_sms: float = 0.50
    price_voice: float = 1.00
    timeout_seconds: int = 300
    max_retries: int = 3


# =============================================================================
# SMS Code Extractor
# =============================================================================


class SMSCodeExtractor:
    """
    SMS verification code extractor.

    Extracts verification codes from SMS text using site-specific
    and generic patterns.

    Attributes:
        SITE_PATTERNS: Site-specific extraction patterns.

    Usage:
        code = SMSCodeExtractor.extract(sms_text, site="vfs")
        if code:
            print(f"Found code: {code}")
    """

    SITE_PATTERNS: dict[str, dict[str, Any]] = {
        "kkosmos": {
            "patterns": [
                r"(?:code|κωδικός)[:\s]*(\d{6})",
                r"(?:verification|επαλήθευση)[:\s]*(\d{6})",
                r"\b(\d{6})\b",
            ],
            "length": 6,
        },
        "vfs": {
            "patterns": [
                r"(?:verification code|doğrulama kodu)[:\s]*(\d{6})",
                r"(?:OTP)[:\s]*(\d{6})",
                r"\b(\d{6})\b",
            ],
            "length": 6,
        },
        "idata": {
            "patterns": [
                r"(?:onay kodu|confirmation code)[:\s]*(\d{6})",
                r"(?:doğrulama)[:\s]*(\d{6})",
                r"\b(\d{6})\b",
            ],
            "length": 6,
        },
        "bls": {
            "patterns": [
                r"(?:code|código)[:\s]*(\d{6})",
                r"(?:verification)[:\s]*(\d{6})",
                r"\b(\d{6})\b",
            ],
            "length": 6,
        },
        "generic": {
            "patterns": [
                r"(?:code|kod|OTP)[:\s]*(\d{4,8})",
                r"\b(\d{6})\b",
                r"\b(\d{4})\b",
            ],
            "length": None,
        },
    }

    @classmethod
    def extract(cls, sms_text: str, site: str | None = None) -> str | None:
        """
        Extract verification code from SMS text.

        Args:
            sms_text: SMS text content.
            site: Optional site for specific patterns.

        Returns:
            Extracted code string or None.

        Example:
            code = SMSCodeExtractor.extract("Your code is 123456", site="vfs")
        """
        config = cls.SITE_PATTERNS.get(site or "", cls.SITE_PATTERNS["generic"])
        expected_length = config.get("length")

        for pattern in config["patterns"]:
            match = re.search(pattern, sms_text, re.IGNORECASE)
            if match:
                code = match.group(1)

                # Validate length if specified
                if expected_length and len(code) != expected_length:
                    continue

                return code

        return None

    @classmethod
    def validate_code(cls, code: str, site: str | None = None) -> bool:
        """
        Validate a verification code.

        Args:
            code: Code to validate.
            site: Optional site for specific validation.

        Returns:
            True if code is valid.

        Example:
            if SMSCodeExtractor.validate_code("123456", site="vfs"):
                print("Valid code")
        """
        if not code or not code.isdigit():
            return False

        config = cls.SITE_PATTERNS.get(site or "", cls.SITE_PATTERNS["generic"])
        expected_length = config.get("length")

        if expected_length:
            return len(code) == expected_length

        return 4 <= len(code) <= 8


# =============================================================================
# Base SMS Provider Client
# =============================================================================


class SMSProviderClient:
    """
    Base class for SMS provider API clients.

    Provides common interface for virtual phone number operations.

    Attributes:
        api_key: Provider API key.
        client: HTTP client for API requests.
    """

    def __init__(self, api_key: str) -> None:
        """Initialize SMS provider client."""
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30)

    async def buy_number(
        self,
        country: str,
        service: str,
    ) -> VirtualPhone | None:
        """
        Purchase a virtual phone number.

        Args:
            country: Country code (e.g., "tr", "de").
            service: Target service name.

        Returns:
            VirtualPhone if successful, None otherwise.
        """
        raise NotImplementedError

    async def get_sms(
        self,
        phone_id: str,
        timeout: int = 120,
    ) -> str | None:
        """
        Wait for and retrieve SMS code.

        Args:
            phone_id: Phone number ID.
            timeout: Maximum wait time in seconds.

        Returns:
            Verification code if received, None if timeout.
        """
        raise NotImplementedError

    async def get_voice_code(
        self,
        phone_id: str,
        timeout: int = 180,
    ) -> str | None:
        """
        Wait for and retrieve voice call code.

        Args:
            phone_id: Phone number ID.
            timeout: Maximum wait time in seconds.

        Returns:
            Verification code if received, None if timeout.
        """
        raise NotImplementedError

    async def cancel_number(self, phone_id: str) -> None:
        """
        Cancel phone number (for refund).

        Args:
            phone_id: Phone number ID to cancel.
        """
        raise NotImplementedError

    async def finish_number(self, phone_id: str) -> None:
        """
        Mark phone number as finished.

        Args:
            phone_id: Phone number ID to finish.
        """
        raise NotImplementedError

    async def get_balance(self) -> float:
        """
        Get account balance.

        Returns:
            Account balance in USD.
        """
        raise NotImplementedError

    async def close(self) -> None:
        """Close HTTP client."""
        await self.client.aclose()


# =============================================================================
# 5sim Provider Client
# =============================================================================


class FiveSimClient(SMSProviderClient):
    """
    5sim.net API client.

    Primary SMS provider with good country coverage and fast delivery.

    Attributes:
        BASE_URL: 5sim API base URL.
        COUNTRY_CODES: Country code mapping.
        SERVICE_CODES: Service code mapping.
    """

    BASE_URL = "https://5sim.net/v1"

    COUNTRY_CODES: dict[str, str] = {
        "tr": "turkey",
        "de": "germany",
        "nl": "netherlands",
        "fr": "france",
        "gr": "greece",
        "ru": "russia",
        "us": "usa",
        "gb": "england",
        "es": "spain",
        "it": "italy",
    }

    SERVICE_CODES: dict[str, str] = {
        "kkosmos": "other",
        "vfs": "other",
        "idata": "other",
        "bls": "other",
    }

    def __init__(self, api_key: str) -> None:
        """Initialize 5sim client."""
        super().__init__(api_key)
        self.client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            timeout=30,
        )

    async def buy_number(
        self,
        country: str,
        service: str,
    ) -> VirtualPhone | None:
        """
        Purchase a phone number from 5sim.

        Args:
            country: Country code.
            service: Target service name.

        Returns:
            VirtualPhone if successful, None otherwise.
        """
        country_code = self.COUNTRY_CODES.get(country, country)
        service_code = self.SERVICE_CODES.get(service, "other")

        try:
            response = await self.client.get(
                f"{self.BASE_URL}/user/buy/activation/{country_code}/any/{service_code}"
            )

            if response.status_code == 200:
                data = response.json()

                logger.info(
                    "phone_number_purchased",
                    provider="5sim",
                    country=country,
                    phone_id=data.get("id"),
                )

                return VirtualPhone(
                    id=str(data["id"]),
                    provider=SMSProvider.FIVE_SIM,
                    number=data["phone"],
                    country=country,
                    status=PhoneStatus.WAITING_SMS,
                    service=service,
                    cost=data.get("price", 0),
                    created_at=datetime.utcnow(),
                    expires_at=datetime.utcnow() + timedelta(minutes=20),
                )

            logger.warning(
                "phone_purchase_failed",
                provider="5sim",
                status_code=response.status_code,
                response=response.text[:200],
            )
            return None

        except Exception as e:
            logger.error("5sim_buy_error", error=str(e))
            return None

    async def get_sms(
        self,
        phone_id: str,
        timeout: int = 120,
    ) -> str | None:
        """
        Wait for SMS code from 5sim.

        Args:
            phone_id: Phone number ID.
            timeout: Maximum wait time in seconds.

        Returns:
            Verification code if received, None if timeout.
        """
        start_time = datetime.utcnow()

        while (datetime.utcnow() - start_time).total_seconds() < timeout:
            try:
                response = await self.client.get(
                    f"{self.BASE_URL}/user/check/{phone_id}"
                )

                if response.status_code == 200:
                    data = response.json()
                    status = data.get("status")

                    if status == "RECEIVED" and data.get("sms"):
                        sms_list = data["sms"]
                        if sms_list:
                            sms_text = sms_list[0].get("text", "")
                            code = SMSCodeExtractor.extract(sms_text)
                            if code:
                                logger.info(
                                    "sms_code_received",
                                    provider="5sim",
                                    phone_id=phone_id,
                                )
                                return code

                    elif status in ["TIMEOUT", "BANNED", "CANCELED"]:
                        logger.warning(
                            "sms_reception_failed",
                            provider="5sim",
                            phone_id=phone_id,
                            status=status,
                        )
                        return None

            except Exception as e:
                logger.error("5sim_check_error", error=str(e), phone_id=phone_id)

            await asyncio.sleep(5)

        logger.warning("sms_timeout", provider="5sim", phone_id=phone_id)
        return None

    async def get_voice_code(
        self,
        phone_id: str,
        timeout: int = 180,
    ) -> str | None:
        """
        Wait for voice code (fallback to SMS for 5sim).

        Args:
            phone_id: Phone number ID.
            timeout: Maximum wait time in seconds.

        Returns:
            Verification code if received, None if timeout.
        """
        # 5sim primarily SMS, voice support limited
        return await self.get_sms(phone_id, timeout)

    async def cancel_number(self, phone_id: str) -> None:
        """Cancel phone number for refund."""
        try:
            await self.client.get(f"{self.BASE_URL}/user/cancel/{phone_id}")
            logger.info("phone_cancelled", provider="5sim", phone_id=phone_id)
        except Exception as e:
            logger.error("5sim_cancel_error", error=str(e), phone_id=phone_id)

    async def finish_number(self, phone_id: str) -> None:
        """Mark phone number as finished."""
        try:
            await self.client.get(f"{self.BASE_URL}/user/finish/{phone_id}")
            logger.info("phone_finished", provider="5sim", phone_id=phone_id)
        except Exception as e:
            logger.error("5sim_finish_error", error=str(e), phone_id=phone_id)

    async def get_balance(self) -> float:
        """Get account balance."""
        try:
            response = await self.client.get(f"{self.BASE_URL}/user/profile")

            if response.status_code == 200:
                data = response.json()
                return float(data.get("balance", 0))

        except Exception as e:
            logger.error("5sim_balance_error", error=str(e))

        return 0.0


# =============================================================================
# SMSHub Provider Client
# =============================================================================


class SMSHubClient(SMSProviderClient):
    """
    SMSHub API client.

    Secondary SMS provider with competitive pricing.

    Attributes:
        BASE_URL: SMSHub API base URL.
        COUNTRY_CODES: Country code mapping.
    """

    BASE_URL = "https://smshub.org/stubs/handler_api.php"

    COUNTRY_CODES: dict[str, str] = {
        "tr": "62",
        "de": "43",
        "nl": "48",
        "fr": "78",
        "gr": "33",
        "ru": "0",
        "us": "187",
        "gb": "16",
        "es": "56",
        "it": "86",
    }

    async def buy_number(
        self,
        country: str,
        service: str,
    ) -> VirtualPhone | None:
        """
        Purchase a phone number from SMSHub.

        Args:
            country: Country code.
            service: Target service name.

        Returns:
            VirtualPhone if successful, None otherwise.
        """
        country_code = self.COUNTRY_CODES.get(country, "0")

        try:
            response = await self.client.get(
                self.BASE_URL,
                params={
                    "api_key": self.api_key,
                    "action": "getNumber",
                    "service": "ot",  # Other
                    "country": country_code,
                },
            )

            text = response.text

            if text.startswith("ACCESS_NUMBER"):
                parts = text.split(":")

                logger.info(
                    "phone_number_purchased",
                    provider="smshub",
                    country=country,
                    phone_id=parts[1],
                )

                return VirtualPhone(
                    id=parts[1],
                    provider=SMSProvider.SMS_HUB,
                    number=parts[2],
                    country=country,
                    status=PhoneStatus.WAITING_SMS,
                    service=service,
                    created_at=datetime.utcnow(),
                    expires_at=datetime.utcnow() + timedelta(minutes=20),
                )

            logger.warning(
                "phone_purchase_failed",
                provider="smshub",
                response=text[:200],
            )
            return None

        except Exception as e:
            logger.error("smshub_buy_error", error=str(e))
            return None

    async def get_sms(
        self,
        phone_id: str,
        timeout: int = 120,
    ) -> str | None:
        """
        Wait for SMS code from SMSHub.

        Args:
            phone_id: Phone number ID.
            timeout: Maximum wait time in seconds.

        Returns:
            Verification code if received, None if timeout.
        """
        # Set status to waiting
        await self._set_status(phone_id, "1")

        start_time = datetime.utcnow()

        while (datetime.utcnow() - start_time).total_seconds() < timeout:
            try:
                response = await self.client.get(
                    self.BASE_URL,
                    params={
                        "api_key": self.api_key,
                        "action": "getStatus",
                        "id": phone_id,
                    },
                )

                text = response.text

                if text.startswith("STATUS_OK"):
                    code = text.split(":")[1]
                    logger.info(
                        "sms_code_received",
                        provider="smshub",
                        phone_id=phone_id,
                    )
                    return code

                elif text in ["STATUS_CANCEL", "STATUS_ERROR"]:
                    logger.warning(
                        "sms_reception_failed",
                        provider="smshub",
                        phone_id=phone_id,
                        status=text,
                    )
                    return None

            except Exception as e:
                logger.error("smshub_check_error", error=str(e), phone_id=phone_id)

            await asyncio.sleep(5)

        logger.warning("sms_timeout", provider="smshub", phone_id=phone_id)
        return None

    async def get_voice_code(
        self,
        phone_id: str,
        timeout: int = 180,
    ) -> str | None:
        """Wait for voice code (fallback to SMS)."""
        return await self.get_sms(phone_id, timeout)

    async def cancel_number(self, phone_id: str) -> None:
        """Cancel phone number."""
        await self._set_status(phone_id, "8")
        logger.info("phone_cancelled", provider="smshub", phone_id=phone_id)

    async def finish_number(self, phone_id: str) -> None:
        """Mark phone number as finished."""
        await self._set_status(phone_id, "6")
        logger.info("phone_finished", provider="smshub", phone_id=phone_id)

    async def get_balance(self) -> float:
        """Get account balance."""
        try:
            response = await self.client.get(
                self.BASE_URL,
                params={
                    "api_key": self.api_key,
                    "action": "getBalance",
                },
            )

            text = response.text

            if text.startswith("ACCESS_BALANCE"):
                return float(text.split(":")[1])

        except Exception as e:
            logger.error("smshub_balance_error", error=str(e))

        return 0.0

    async def _set_status(self, phone_id: str, status: str) -> None:
        """Update phone status."""
        try:
            await self.client.get(
                self.BASE_URL,
                params={
                    "api_key": self.api_key,
                    "action": "setStatus",
                    "id": phone_id,
                    "status": status,
                },
            )
        except Exception as e:
            logger.error("smshub_set_status_error", error=str(e), phone_id=phone_id)


# =============================================================================
# SMS-Activate Provider Client
# =============================================================================


class SMSActivateClient(SMSProviderClient):
    """
    SMS-Activate API client.

    Tertiary SMS provider for additional redundancy.

    Attributes:
        BASE_URL: SMS-Activate API base URL.
        COUNTRY_CODES: Country code mapping.
    """

    BASE_URL = "https://api.sms-activate.org/stubs/handler_api.php"

    COUNTRY_CODES: dict[str, str] = {
        "tr": "62",
        "de": "43",
        "nl": "48",
        "fr": "78",
        "gr": "33",
        "ru": "0",
        "us": "187",
        "gb": "16",
        "es": "56",
        "it": "86",
    }

    async def buy_number(
        self,
        country: str,
        service: str,
    ) -> VirtualPhone | None:
        """
        Purchase a phone number from SMS-Activate.

        Args:
            country: Country code.
            service: Target service name.

        Returns:
            VirtualPhone if successful, None otherwise.
        """
        country_code = self.COUNTRY_CODES.get(country, "0")

        try:
            response = await self.client.get(
                self.BASE_URL,
                params={
                    "api_key": self.api_key,
                    "action": "getNumber",
                    "service": "ot",
                    "country": country_code,
                },
            )

            text = response.text

            if text.startswith("ACCESS_NUMBER"):
                parts = text.split(":")

                logger.info(
                    "phone_number_purchased",
                    provider="smsactivate",
                    country=country,
                    phone_id=parts[1],
                )

                return VirtualPhone(
                    id=parts[1],
                    provider=SMSProvider.SMS_ACTIVATE,
                    number=parts[2],
                    country=country,
                    status=PhoneStatus.WAITING_SMS,
                    service=service,
                    created_at=datetime.utcnow(),
                    expires_at=datetime.utcnow() + timedelta(minutes=20),
                )

            logger.warning(
                "phone_purchase_failed",
                provider="smsactivate",
                response=text[:200],
            )
            return None

        except Exception as e:
            logger.error("smsactivate_buy_error", error=str(e))
            return None

    async def get_sms(
        self,
        phone_id: str,
        timeout: int = 120,
    ) -> str | None:
        """
        Wait for SMS code from SMS-Activate.

        Args:
            phone_id: Phone number ID.
            timeout: Maximum wait time in seconds.

        Returns:
            Verification code if received, None if timeout.
        """
        start_time = datetime.utcnow()

        while (datetime.utcnow() - start_time).total_seconds() < timeout:
            try:
                response = await self.client.get(
                    self.BASE_URL,
                    params={
                        "api_key": self.api_key,
                        "action": "getStatus",
                        "id": phone_id,
                    },
                )

                text = response.text

                if text.startswith("STATUS_OK"):
                    code = text.split(":")[1]
                    logger.info(
                        "sms_code_received",
                        provider="smsactivate",
                        phone_id=phone_id,
                    )
                    return code

                elif text == "STATUS_CANCEL":
                    logger.warning(
                        "sms_reception_failed",
                        provider="smsactivate",
                        phone_id=phone_id,
                        status=text,
                    )
                    return None

            except Exception as e:
                logger.error("smsactivate_check_error", error=str(e), phone_id=phone_id)

            await asyncio.sleep(5)

        logger.warning("sms_timeout", provider="smsactivate", phone_id=phone_id)
        return None

    async def get_voice_code(
        self,
        phone_id: str,
        timeout: int = 180,
    ) -> str | None:
        """Wait for voice code (fallback to SMS)."""
        return await self.get_sms(phone_id, timeout)

    async def cancel_number(self, phone_id: str) -> None:
        """Cancel phone number."""
        try:
            await self.client.get(
                self.BASE_URL,
                params={
                    "api_key": self.api_key,
                    "action": "setStatus",
                    "id": phone_id,
                    "status": "8",
                },
            )
            logger.info("phone_cancelled", provider="smsactivate", phone_id=phone_id)
        except Exception as e:
            logger.error("smsactivate_cancel_error", error=str(e), phone_id=phone_id)

    async def finish_number(self, phone_id: str) -> None:
        """Mark phone number as finished."""
        try:
            await self.client.get(
                self.BASE_URL,
                params={
                    "api_key": self.api_key,
                    "action": "setStatus",
                    "id": phone_id,
                    "status": "6",
                },
            )
            logger.info("phone_finished", provider="smsactivate", phone_id=phone_id)
        except Exception as e:
            logger.error("smsactivate_finish_error", error=str(e), phone_id=phone_id)

    async def get_balance(self) -> float:
        """Get account balance."""
        try:
            response = await self.client.get(
                self.BASE_URL,
                params={
                    "api_key": self.api_key,
                    "action": "getBalance",
                },
            )

            text = response.text

            if text.startswith("ACCESS_BALANCE"):
                return float(text.split(":")[1])

        except Exception as e:
            logger.error("smsactivate_balance_error", error=str(e))

        return 0.0


# =============================================================================
# Phone Pool Manager
# =============================================================================


class PhonePoolManager:
    """
    Virtual phone number pool management.

    Manages multiple SMS providers with automatic failover,
    balance tracking, and phone lifecycle management.

    Attributes:
        PROVIDER_PRIORITY: Provider priority order.
        MIN_BALANCE_ALERT: Minimum balance threshold.
        PHONE_COOLDOWN_MINUTES: Cooldown between uses.
        MAX_USAGE_PER_PHONE: Maximum uses per phone.

    Usage:
        pool = PhonePoolManager()
        await pool.initialize(configs)

        phone = await pool.acquire_phone("tr", "vfs")
        if phone:
            code = await pool.get_sms_code(phone.id)
            await pool.release_phone(phone.id, success=True)
    """

    PROVIDER_PRIORITY: list[SMSProvider] = [
        SMSProvider.FIVE_SIM,
        SMSProvider.SMS_HUB,
        SMSProvider.SMS_ACTIVATE,
    ]

    MIN_BALANCE_ALERT: float = 5.0
    PHONE_COOLDOWN_MINUTES: int = 30
    MAX_USAGE_PER_PHONE: int = 3

    def __init__(self) -> None:
        """Initialize phone pool manager."""
        self.configs: dict[SMSProvider, SMSProviderConfig] = {}
        self.clients: dict[SMSProvider, SMSProviderClient] = {}
        self.active_phones: dict[str, VirtualPhone] = {}
        self.phone_history: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(
        self,
        provider_configs: dict[SMSProvider, SMSProviderConfig],
    ) -> None:
        """
        Initialize with provider configurations.

        Args:
            provider_configs: Provider configurations.
        """
        self.configs = provider_configs

        for provider, config in provider_configs.items():
            if provider == SMSProvider.FIVE_SIM:
                self.clients[provider] = FiveSimClient(config.api_key)
            elif provider == SMSProvider.SMS_HUB:
                self.clients[provider] = SMSHubClient(config.api_key)
            elif provider == SMSProvider.SMS_ACTIVATE:
                self.clients[provider] = SMSActivateClient(config.api_key)

        self._initialized = True

        logger.info(
            "phone_pool_initialized",
            providers=[p.value for p in self.clients.keys()],
        )

    async def acquire_phone(
        self,
        country: str,
        service: str,
        preferred_provider: SMSProvider | None = None,
    ) -> VirtualPhone | None:
        """
        Acquire a phone number from the pool.

        Args:
            country: Country code.
            service: Target service name.
            preferred_provider: Optional preferred provider.

        Returns:
            VirtualPhone if successful, None otherwise.
        """
        if not self._initialized:
            raise VerificationError("PhonePoolManager not initialized")

        async with self._lock:
            providers = (
                [preferred_provider] if preferred_provider else self.PROVIDER_PRIORITY
            )

            for provider in providers:
                if provider not in self.clients:
                    continue

                client = self.clients[provider]

                # Check balance first
                try:
                    balance = await client.get_balance()
                    if balance < self.MIN_BALANCE_ALERT:
                        await self._send_balance_alert(provider, balance)
                        logger.warning(
                            "low_balance_skipping",
                            provider=provider.value,
                            balance=balance,
                        )
                        continue
                except Exception as e:
                    logger.error(
                        "balance_check_failed",
                        provider=provider.value,
                        error=str(e),
                    )
                    continue

                # Buy number
                phone = await client.buy_number(country, service)

                if phone:
                    self.active_phones[phone.id] = phone
                    return phone

            logger.error(
                "phone_acquisition_failed",
                country=country,
                service=service,
            )
            return None

    async def get_sms_code(
        self,
        phone_id: str,
        timeout: int = 120,
    ) -> str | None:
        """
        Wait for SMS code.

        Args:
            phone_id: Phone number ID.
            timeout: Maximum wait time in seconds.

        Returns:
            Verification code if received, None if timeout.
        """
        if phone_id not in self.active_phones:
            return None

        phone = self.active_phones[phone_id]
        client = self.clients.get(phone.provider)

        if not client:
            return None

        phone.status = PhoneStatus.WAITING_SMS

        code = await client.get_sms(phone_id, timeout)

        if code:
            phone.status = PhoneStatus.CODE_RECEIVED
            phone.sms_code = code
            phone.code_received_at = datetime.utcnow()
        else:
            phone.status = PhoneStatus.EXPIRED

        return code

    async def get_voice_code(
        self,
        phone_id: str,
        timeout: int = 180,
    ) -> str | None:
        """
        Wait for voice call code.

        Args:
            phone_id: Phone number ID.
            timeout: Maximum wait time in seconds.

        Returns:
            Verification code if received, None if timeout.
        """
        if phone_id not in self.active_phones:
            return None

        phone = self.active_phones[phone_id]
        client = self.clients.get(phone.provider)

        if not client:
            return None

        phone.status = PhoneStatus.WAITING_CALL

        code = await client.get_voice_code(phone_id, timeout)

        if code:
            phone.status = PhoneStatus.CODE_RECEIVED
            phone.voice_code = code
            phone.code_received_at = datetime.utcnow()
        else:
            phone.status = PhoneStatus.EXPIRED

        return code

    async def release_phone(
        self,
        phone_id: str,
        success: bool = True,
    ) -> None:
        """
        Release phone back to pool.

        Args:
            phone_id: Phone number ID.
            success: Whether verification was successful.
        """
        async with self._lock:
            if phone_id not in self.active_phones:
                return

            phone = self.active_phones[phone_id]
            client = self.clients.get(phone.provider)

            if not client:
                return

            if success:
                phone.status = PhoneStatus.COMPLETED
                await client.finish_number(phone_id)
            else:
                phone.status = PhoneStatus.CANCELLED
                await client.cancel_number(phone_id)

            # Track history
            self.phone_history.append(
                {
                    "phone_id": phone_id,
                    "provider": phone.provider.value,
                    "country": phone.country,
                    "service": phone.service,
                    "success": success,
                    "code_received": phone.sms_code or phone.voice_code,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )

            # Remove from active
            del self.active_phones[phone_id]

            logger.info(
                "phone_released",
                phone_id=phone_id,
                success=success,
            )

    async def get_pool_stats(self) -> dict[str, Any]:
        """
        Get pool statistics.

        Returns:
            Dictionary with pool statistics.
        """
        stats: dict[str, Any] = {
            "active_phones": len(self.active_phones),
            "by_provider": {},
            "by_status": {},
            "balances": {},
        }

        for provider, client in self.clients.items():
            try:
                balance = await client.get_balance()
                stats["balances"][provider.value] = balance
            except Exception:
                stats["balances"][provider.value] = -1

            stats["by_provider"][provider.value] = len(
                [p for p in self.active_phones.values() if p.provider == provider]
            )

        for phone in self.active_phones.values():
            status = phone.status.value
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

        return stats

    async def _send_balance_alert(
        self,
        provider: SMSProvider,
        balance: float,
    ) -> None:
        """Send low balance alert."""
        logger.warning(
            "low_balance_alert",
            provider=provider.value,
            balance=balance,
            threshold=self.MIN_BALANCE_ALERT,
        )

    async def close(self) -> None:
        """Close all provider connections."""
        for client in self.clients.values():
            await client.close()


# =============================================================================
# Human Escalation Service
# =============================================================================


class HumanEscalationService:
    """
    Human-in-the-loop escalation for voice verification.

    Provides Telegram-based escalation for manual voice code entry
    when automated systems cannot handle the verification.

    Attributes:
        telegram_token: Telegram bot token.
        telegram_chat_id: Telegram chat ID for alerts.

    Usage:
        service = HumanEscalationService(token, chat_id)
        code = await service.request_voice_code(phone, booking_id)
    """

    def __init__(
        self,
        telegram_bot_token: str,
        telegram_chat_id: str,
    ) -> None:
        """Initialize human escalation service."""
        self.telegram_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        self.client = httpx.AsyncClient()
        self.pending_requests: dict[str, dict[str, Any]] = {}

    async def request_voice_code(
        self,
        phone_number: str,
        booking_id: str,
        timeout_seconds: int = 300,
    ) -> str | None:
        """
        Request voice code from human operator.

        Args:
            phone_number: Phone number receiving the call.
            booking_id: Associated booking ID.
            timeout_seconds: Maximum wait time.

        Returns:
            Verification code if received, None if timeout.
        """
        request_id = str(uuid.uuid4())[:8]

        message = f"""
*Voice Verification Required*

Booking ID: `{booking_id}`
Phone: `{phone_number}`
Request ID: `{request_id}`

Reply with the code received in the phone call.
Format: `/code {request_id} XXXXXX`
        """

        await self._send_telegram(message)

        # Store pending request
        self.pending_requests[request_id] = {
            "booking_id": booking_id,
            "phone": phone_number,
            "created_at": datetime.utcnow(),
            "code": None,
        }

        logger.info(
            "voice_escalation_requested",
            request_id=request_id,
            booking_id=booking_id,
        )

        # Wait for response
        start_time = datetime.utcnow()

        while (datetime.utcnow() - start_time).total_seconds() < timeout_seconds:
            request = self.pending_requests.get(request_id)

            if request and request.get("code"):
                code = request["code"]
                del self.pending_requests[request_id]
                logger.info(
                    "voice_code_received_from_operator",
                    request_id=request_id,
                )
                return code

            await asyncio.sleep(5)

        # Timeout
        if request_id in self.pending_requests:
            del self.pending_requests[request_id]

        logger.warning(
            "voice_escalation_timeout",
            request_id=request_id,
        )
        return None

    async def submit_code(self, request_id: str, code: str) -> bool:
        """
        Submit code from operator.

        Args:
            request_id: Escalation request ID.
            code: Verification code.

        Returns:
            True if request was pending, False otherwise.
        """
        if request_id not in self.pending_requests:
            return False

        self.pending_requests[request_id]["code"] = code
        return True

    async def _send_telegram(self, message: str) -> None:
        """Send Telegram message."""
        try:
            await self.client.post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                json={
                    "chat_id": self.telegram_chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                },
            )
        except Exception as e:
            logger.error("telegram_send_error", error=str(e))

    async def close(self) -> None:
        """Close HTTP client."""
        await self.client.aclose()


# =============================================================================
# SMS Verification Executor
# =============================================================================


class SMSVerificationExecutor:
    """
    SMS verification execution manager.

    Executes SMS and voice verification flows with automatic fallback.

    Usage:
        executor = SMSVerificationExecutor(phone_pool)
        result = await executor.verify_by_sms(
            country="tr",
            service="vfs",
            phone_entry_callback=enter_phone,
            code_entry_callback=enter_code,
        )
    """

    def __init__(
        self,
        phone_pool: PhonePoolManager,
        escalation_service: HumanEscalationService | None = None,
    ) -> None:
        """Initialize SMS verification executor."""
        self.pool = phone_pool
        self.escalation = escalation_service

    async def verify_by_sms(
        self,
        country: str,
        service: str,
        phone_entry_callback: Callable[[str], Coroutine[Any, Any, bool]],
        code_entry_callback: Callable[[str], Coroutine[Any, Any, bool]],
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """
        Perform SMS-based verification.

        Args:
            country: Country code.
            service: Target service name.
            phone_entry_callback: Async callback to enter phone number.
            code_entry_callback: Async callback to enter verification code.
            timeout_seconds: Maximum wait time for SMS.

        Returns:
            Dictionary with verification result.
        """
        # Acquire phone
        phone = await self.pool.acquire_phone(country, service)

        if not phone:
            return {
                "success": False,
                "error": "No phone number available",
            }

        code = None

        try:
            # Enter phone number in form
            entry_success = await phone_entry_callback(phone.number)

            if not entry_success:
                return {
                    "success": False,
                    "error": "Failed to enter phone number",
                    "phone": phone.number,
                }

            # Wait for SMS
            code = await self.pool.get_sms_code(
                phone.id,
                timeout=timeout_seconds,
            )

            if not code:
                return {
                    "success": False,
                    "error": "SMS code not received",
                    "phone": phone.number,
                }

            # Enter code
            code_success = await code_entry_callback(code)

            if not code_success:
                return {
                    "success": False,
                    "error": "Failed to enter verification code",
                    "phone": phone.number,
                    "code": code,
                }

            logger.info(
                "sms_verification_success",
                phone=phone.number,
            )

            return {
                "success": True,
                "phone": phone.number,
                "code": code,
                "method": "sms",
            }

        finally:
            # Release phone
            await self.pool.release_phone(
                phone.id,
                success=code is not None,
            )

    async def verify_by_voice(
        self,
        country: str,
        service: str,
        phone_entry_callback: Callable[[str], Coroutine[Any, Any, bool]],
        code_entry_callback: Callable[[str], Coroutine[Any, Any, bool]],
        timeout_seconds: int = 180,
        booking_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Perform voice call verification.

        Args:
            country: Country code.
            service: Target service name.
            phone_entry_callback: Async callback to enter phone number.
            code_entry_callback: Async callback to enter verification code.
            timeout_seconds: Maximum wait time for voice call.
            booking_id: Optional booking ID for escalation.

        Returns:
            Dictionary with verification result.
        """
        # Acquire phone
        phone = await self.pool.acquire_phone(country, service)

        if not phone:
            return {
                "success": False,
                "error": "No phone number available",
            }

        code = None

        try:
            # Enter phone number
            entry_success = await phone_entry_callback(phone.number)

            if not entry_success:
                return {
                    "success": False,
                    "error": "Failed to enter phone number",
                    "phone": phone.number,
                }

            # Wait for voice call
            code = await self.pool.get_voice_code(
                phone.id,
                timeout=timeout_seconds,
            )

            # If automated voice fails, try human escalation
            if not code and self.escalation and booking_id:
                logger.info(
                    "escalating_to_human",
                    phone=phone.number,
                    booking_id=booking_id,
                )
                code = await self.escalation.request_voice_code(
                    phone.number,
                    booking_id,
                    timeout_seconds=300,
                )

            if not code:
                return {
                    "success": False,
                    "error": "Voice code not received",
                    "phone": phone.number,
                }

            # Enter code
            code_success = await code_entry_callback(code)

            return {
                "success": code_success,
                "phone": phone.number,
                "code": code,
                "method": "voice",
            }

        finally:
            await self.pool.release_phone(
                phone.id,
                success=code is not None,
            )

    async def verify_with_fallback(
        self,
        country: str,
        service: str,
        phone_entry_callback: Callable[[str], Coroutine[Any, Any, bool]],
        code_entry_callback: Callable[[str], Coroutine[Any, Any, bool]],
        primary_method: str = "sms",
        timeout_seconds: int = 120,
        booking_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Verify with SMS -> Voice fallback.

        Args:
            country: Country code.
            service: Target service name.
            phone_entry_callback: Async callback to enter phone number.
            code_entry_callback: Async callback to enter verification code.
            primary_method: Primary method ("sms" or "voice").
            timeout_seconds: Maximum wait time.
            booking_id: Optional booking ID for escalation.

        Returns:
            Dictionary with verification result.
        """
        if primary_method == "sms":
            result = await self.verify_by_sms(
                country,
                service,
                phone_entry_callback,
                code_entry_callback,
                timeout_seconds,
            )

            if result["success"]:
                return result

            logger.info("falling_back_to_voice")

            # Fallback to voice
            return await self.verify_by_voice(
                country,
                service,
                phone_entry_callback,
                code_entry_callback,
                timeout_seconds + 60,
                booking_id,
            )
        else:
            result = await self.verify_by_voice(
                country,
                service,
                phone_entry_callback,
                code_entry_callback,
                timeout_seconds,
                booking_id,
            )

            if result["success"]:
                return result

            logger.info("falling_back_to_sms")

            # Fallback to SMS
            return await self.verify_by_sms(
                country,
                service,
                phone_entry_callback,
                code_entry_callback,
                timeout_seconds,
            )


# =============================================================================
# Main SMS Verifier
# =============================================================================


class SMSVerifier:
    """
    Main SMS verification service.

    Provides high-level methods for SMS-based verification including
    phone pool management, code extraction, and verification execution.

    Usage:
        verifier = SMSVerifier()
        await verifier.initialize(configs)

        result = await verifier.verify_by_sms(
            country="tr",
            service="vfs",
            phone_entry_callback=enter_phone,
            code_entry_callback=enter_code,
        )
    """

    def __init__(self) -> None:
        """Initialize SMS verifier."""
        self.pool = PhonePoolManager()
        self.executor: SMSVerificationExecutor | None = None
        self.escalation: HumanEscalationService | None = None
        self._initialized = False

    async def initialize(
        self,
        provider_configs: dict[SMSProvider, SMSProviderConfig],
        telegram_bot_token: str | None = None,
        telegram_chat_id: str | None = None,
    ) -> None:
        """
        Initialize SMS verifier with provider configurations.

        Args:
            provider_configs: Provider configurations.
            telegram_bot_token: Optional Telegram bot token for escalation.
            telegram_chat_id: Optional Telegram chat ID for escalation.
        """
        await self.pool.initialize(provider_configs)

        if telegram_bot_token and telegram_chat_id:
            self.escalation = HumanEscalationService(
                telegram_bot_token,
                telegram_chat_id,
            )

        self.executor = SMSVerificationExecutor(self.pool, self.escalation)
        self._initialized = True

        logger.info("sms_verifier_initialized")

    async def verify_by_sms(
        self,
        country: str,
        service: str,
        phone_entry_callback: Callable[[str], Coroutine[Any, Any, bool]],
        code_entry_callback: Callable[[str], Coroutine[Any, Any, bool]],
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """
        Perform SMS-based verification.

        Args:
            country: Country code (e.g., "tr", "de").
            service: Target service name (e.g., "vfs", "kkosmos").
            phone_entry_callback: Async callback to enter phone number.
            code_entry_callback: Async callback to enter verification code.
            timeout_seconds: Maximum wait time for SMS.

        Returns:
            Dictionary with verification result.

        Raises:
            VerificationError: If verifier not initialized.
        """
        if not self._initialized or not self.executor:
            raise VerificationError("SMSVerifier not initialized")

        return await self.executor.verify_by_sms(
            country,
            service,
            phone_entry_callback,
            code_entry_callback,
            timeout_seconds,
        )

    async def verify_by_voice(
        self,
        country: str,
        service: str,
        phone_entry_callback: Callable[[str], Coroutine[Any, Any, bool]],
        code_entry_callback: Callable[[str], Coroutine[Any, Any, bool]],
        timeout_seconds: int = 180,
        booking_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Perform voice call verification.

        Args:
            country: Country code.
            service: Target service name.
            phone_entry_callback: Async callback to enter phone number.
            code_entry_callback: Async callback to enter verification code.
            timeout_seconds: Maximum wait time for voice call.
            booking_id: Optional booking ID for human escalation.

        Returns:
            Dictionary with verification result.

        Raises:
            VerificationError: If verifier not initialized.
        """
        if not self._initialized or not self.executor:
            raise VerificationError("SMSVerifier not initialized")

        return await self.executor.verify_by_voice(
            country,
            service,
            phone_entry_callback,
            code_entry_callback,
            timeout_seconds,
            booking_id,
        )

    async def verify_with_fallback(
        self,
        country: str,
        service: str,
        phone_entry_callback: Callable[[str], Coroutine[Any, Any, bool]],
        code_entry_callback: Callable[[str], Coroutine[Any, Any, bool]],
        primary_method: str = "sms",
        timeout_seconds: int = 120,
        booking_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Verify with SMS -> Voice fallback.

        Args:
            country: Country code.
            service: Target service name.
            phone_entry_callback: Async callback to enter phone number.
            code_entry_callback: Async callback to enter verification code.
            primary_method: Primary method ("sms" or "voice").
            timeout_seconds: Maximum wait time.
            booking_id: Optional booking ID for human escalation.

        Returns:
            Dictionary with verification result.

        Raises:
            VerificationError: If verifier not initialized.
        """
        if not self._initialized or not self.executor:
            raise VerificationError("SMSVerifier not initialized")

        return await self.executor.verify_with_fallback(
            country,
            service,
            phone_entry_callback,
            code_entry_callback,
            primary_method,
            timeout_seconds,
            booking_id,
        )

    async def get_pool_stats(self) -> dict[str, Any]:
        """
        Get phone pool statistics.

        Returns:
            Dictionary with pool statistics.
        """
        return await self.pool.get_pool_stats()

    async def close(self) -> None:
        """Close all connections."""
        await self.pool.close()
        if self.escalation:
            await self.escalation.close()


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Main interface
    "SMSVerifier",
    # Enums
    "SMSProvider",
    "PhoneStatus",
    # Data classes
    "VirtualPhone",
    "SMSProviderConfig",
    # Code extractor
    "SMSCodeExtractor",
    # Provider clients
    "SMSProviderClient",
    "FiveSimClient",
    "SMSHubClient",
    "SMSActivateClient",
    # Pool manager
    "PhonePoolManager",
    # Verification executor
    "SMSVerificationExecutor",
    # Human escalation
    "HumanEscalationService",
]
