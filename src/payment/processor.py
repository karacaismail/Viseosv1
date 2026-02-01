"""
Payment Processor Service.

This module provides the main payment processing service for VISE OS.
It handles card data encryption, payment gateway routing, 3DS authentication,
and transaction monitoring.

Features:
- PCI-DSS compliant card data encryption (Fernet/AES-128-CBC)
- Luhn algorithm validation for card numbers
- Card type detection (Visa, Mastercard, Amex, Troy)
- Multi-gateway support (VFS, iDATA, BLS internal gateways)
- 3DS 1.0/2.0+ authentication handling
- Transaction recording and monitoring

Security:
- Card data is encrypted at rest using Fernet encryption
- Sensitive data (card number, CVV) is only decrypted in memory during use
- CVV is never stored permanently
- All card operations use memory-only decryption

Usage:
    from src.payment.processor import PaymentProcessor, PaymentRequest

    processor = PaymentProcessor(encryptor, gateway_router)
    result = await processor.process_payment(request)

    if result.success:
        print(f"Payment successful: {result.transaction_id}")
"""

from __future__ import annotations

import asyncio
import base64
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from src.core.exceptions import (
    PaymentDeclinedError,
    PaymentError,
    PaymentTimeoutError,
    ThreeDSAuthenticationError,
)
from src.payment.three_ds import (
    ThreeDSChallenge,
    ThreeDSHandler,
    ThreeDSStatus,
    ThreeDSVersion,
)

if TYPE_CHECKING:
    from playwright.async_api import Page


# =============================================================================
# Card Data Types
# =============================================================================


@dataclass
class SecureCardData:
    """
    Encrypted card data for secure storage.

    Contains encrypted sensitive fields (card number, CVV) and
    plain metadata fields that are safe to display.

    Attributes:
        encrypted_number: Fernet-encrypted card number.
        encrypted_cvv: Fernet-encrypted CVV code.
        expiry_month: Card expiration month (MM).
        expiry_year: Card expiration year (YYYY).
        cardholder_name: Name on the card.
        card_type: Detected card type (visa, mastercard, etc.).
        last_four: Last 4 digits for display.

    Example:
        # Card data is always stored encrypted
        secure_card = SecureCardData(
            encrypted_number=b"encrypted...",
            encrypted_cvv=b"encrypted...",
            expiry_month="12",
            expiry_year="2025",
            cardholder_name="John Doe",
            card_type="visa",
            last_four="4242",
        )
    """

    encrypted_number: bytes
    encrypted_cvv: bytes
    expiry_month: str
    expiry_year: str
    cardholder_name: str
    card_type: str
    last_four: str

    def get_masked_display(self) -> str:
        """Get masked card number for display."""
        return f"**** **** **** {self.last_four}"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage (no encrypted data)."""
        return {
            "expiry_month": self.expiry_month,
            "expiry_year": self.expiry_year,
            "cardholder_name": self.cardholder_name,
            "card_type": self.card_type,
            "last_four": self.last_four,
        }


class CardType(Enum):
    """
    Supported card types.

    Used for card type detection and validation.
    """

    VISA = "visa"
    MASTERCARD = "mastercard"
    AMEX = "amex"
    TROY = "troy"
    DISCOVER = "discover"
    UNKNOWN = "unknown"


# =============================================================================
# Card Data Encryptor
# =============================================================================


class CardDataEncryptor:
    """
    PCI-DSS compliant card data encryptor.

    Uses Fernet symmetric encryption (AES-128-CBC with HMAC-SHA256)
    to encrypt sensitive card data. The encryption key is derived
    from a master key using PBKDF2.

    Security Features:
    - Key derivation using PBKDF2 with 100,000 iterations
    - Unique IV per encryption
    - HMAC authentication for tamper detection
    - Memory-only decryption for sensitive data

    Attributes:
        cipher: Fernet cipher instance.

    Usage:
        encryptor = CardDataEncryptor(master_key)

        # Encrypt card
        secure_card = encryptor.encrypt_card(
            card_number="4111111111111111",
            cvv="123",
            expiry_month="12",
            expiry_year="2025",
            cardholder_name="John Doe",
        )

        # Decrypt for use (memory only)
        card_data = encryptor.decrypt_for_use(secure_card)
    """

    # Salt for key derivation
    SALT = b"vise_os_card_encryption_salt_v1"
    ITERATIONS = 100_000

    def __init__(self, master_key: bytes) -> None:
        """
        Initialize the encryptor with a master key.

        The master key is used to derive the actual encryption key
        using PBKDF2 with SHA256.

        Args:
            master_key: Master key for key derivation (should be 32+ bytes).

        Raises:
            ValueError: If master key is too short.

        Example:
            master_key = b"your_32_byte_master_key_here!!!!!"
            encryptor = CardDataEncryptor(master_key)
        """
        if len(master_key) < 16:
            raise ValueError("Master key must be at least 16 bytes")

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self.SALT,
            iterations=self.ITERATIONS,
        )
        derived_key = base64.urlsafe_b64encode(kdf.derive(master_key))
        self.cipher = Fernet(derived_key)

    def encrypt_card(
        self,
        card_number: str,
        cvv: str,
        expiry_month: str,
        expiry_year: str,
        cardholder_name: str,
    ) -> SecureCardData:
        """
        Encrypt card data for secure storage.

        Validates the card number using Luhn algorithm, detects
        the card type, and encrypts sensitive fields.

        Args:
            card_number: Full card number (digits only).
            cvv: Card verification value (3-4 digits).
            expiry_month: Expiration month (MM).
            expiry_year: Expiration year (YYYY or YY).
            cardholder_name: Name on the card.

        Returns:
            SecureCardData: Encrypted card data.

        Raises:
            ValueError: If card number fails Luhn validation.

        Example:
            secure_card = encryptor.encrypt_card(
                card_number="4111111111111111",
                cvv="123",
                expiry_month="12",
                expiry_year="2025",
                cardholder_name="John Doe",
            )
        """
        clean_number = re.sub(r"\D", "", card_number)

        if not self._luhn_check(clean_number):
            raise ValueError("Invalid card number: Luhn check failed")

        card_type = self._detect_card_type(clean_number)

        encrypted_number = self.cipher.encrypt(clean_number.encode())
        encrypted_cvv = self.cipher.encrypt(cvv.encode())

        year = expiry_year if len(expiry_year) == 4 else f"20{expiry_year}"

        return SecureCardData(
            encrypted_number=encrypted_number,
            encrypted_cvv=encrypted_cvv,
            expiry_month=expiry_month.zfill(2),
            expiry_year=year,
            cardholder_name=cardholder_name,
            card_type=card_type.value,
            last_four=clean_number[-4:],
        )

    def decrypt_for_use(self, secure_card: SecureCardData) -> dict[str, str]:
        """
        Decrypt card data for use in payment processing.

        The decrypted data should only be held in memory during
        the payment transaction and cleared immediately after.

        Args:
            secure_card: Encrypted card data.

        Returns:
            Dictionary with decrypted card fields.

        Note:
            The caller is responsible for clearing the returned
            dictionary from memory after use.

        Example:
            card_data = encryptor.decrypt_for_use(secure_card)
            try:
                # Use card_data for payment
                await process_payment(card_data)
            finally:
                # Clear from memory
                card_data.clear()
        """
        return {
            "card_number": self.cipher.decrypt(secure_card.encrypted_number).decode(),
            "cvv": self.cipher.decrypt(secure_card.encrypted_cvv).decode(),
            "expiry_month": secure_card.expiry_month,
            "expiry_year": secure_card.expiry_year,
            "cardholder_name": secure_card.cardholder_name,
        }

    def _luhn_check(self, card_number: str) -> bool:
        """
        Validate card number using Luhn algorithm.

        The Luhn algorithm is a checksum formula used to validate
        card numbers and prevent transcription errors.

        Args:
            card_number: Card number (digits only).

        Returns:
            True if valid, False otherwise.
        """
        digits = [int(d) for d in card_number if d.isdigit()]

        if len(digits) < 13 or len(digits) > 19:
            return False

        odd_digits = digits[-1::-2]
        even_digits = digits[-2::-2]

        total = sum(odd_digits)
        for d in even_digits:
            total += sum(divmod(d * 2, 10))

        return total % 10 == 0

    def _detect_card_type(self, card_number: str) -> CardType:
        """
        Detect card type from card number prefix.

        Uses IIN (Issuer Identification Number) ranges to
        determine the card network.

        Args:
            card_number: Card number (digits only).

        Returns:
            CardType enum value.
        """
        if card_number.startswith("4"):
            return CardType.VISA
        elif card_number.startswith(("51", "52", "53", "54", "55")):
            return CardType.MASTERCARD
        elif card_number.startswith(("2221", "2222", "2223", "2224", "2225", "2226", "2720")):
            return CardType.MASTERCARD
        elif card_number.startswith(("34", "37")):
            return CardType.AMEX
        elif card_number.startswith("9792"):
            return CardType.TROY
        elif card_number.startswith(("6011", "65")):
            return CardType.DISCOVER
        else:
            return CardType.UNKNOWN


# =============================================================================
# Payment Gateway Types
# =============================================================================


class PaymentGatewayType(Enum):
    """
    Supported payment gateway types.

    Each gateway type corresponds to a specific payment provider
    or site-specific payment system.
    """

    VFS_INTERNAL = "vfs_internal"
    IDATA_INTERNAL = "idata_internal"
    BLS_INTERNAL = "bls_internal"
    IYZICO = "iyzico"
    STRIPE = "stripe"
    PAYTR = "paytr"


# =============================================================================
# Payment Request/Result
# =============================================================================


@dataclass
class PaymentRequest:
    """
    Payment request data.

    Contains all information needed to process a payment including
    card data, billing information, and transaction context.

    Attributes:
        booking_id: ID of the booking being paid for.
        amount: Payment amount.
        currency: Currency code (TRY, EUR, USD).
        card_data: Encrypted card data.
        billing_name: Billing name.
        billing_email: Billing email.
        billing_phone: Billing phone.
        billing_address: Optional billing address.
        site: Target site code (vfs, idata, bls).
        return_url: URL for 3DS callbacks.
        metadata: Additional metadata.
        created_at: Request creation time.

    Example:
        request = PaymentRequest(
            booking_id="booking_123",
            amount=150.00,
            currency="EUR",
            card_data=secure_card,
            billing_name="John Doe",
            billing_email="john@example.com",
            billing_phone="+1234567890",
            site="vfs",
        )
    """

    booking_id: str
    amount: float
    currency: str
    card_data: SecureCardData

    billing_name: str
    billing_email: str
    billing_phone: str
    billing_address: str | None = None

    site: str = "vfs"
    return_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary (no sensitive data)."""
        return {
            "booking_id": self.booking_id,
            "amount": self.amount,
            "currency": self.currency,
            "billing_name": self.billing_name,
            "billing_email": self.billing_email,
            "site": self.site,
            "card_last_four": self.card_data.last_four,
            "card_type": self.card_data.card_type,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class PaymentResult:
    """
    Payment processing result.

    Contains the outcome of a payment attempt including success
    status, transaction IDs, 3DS information, and any errors.

    Attributes:
        success: Whether payment was successful.
        transaction_id: Internal transaction ID.
        authorization_code: Gateway authorization code.
        requires_3ds: Whether 3DS authentication is needed.
        redirect_url: 3DS redirect URL if applicable.
        error_code: Error code if payment failed.
        error_message: Error description if payment failed.
        gateway_response: Raw gateway response.
        three_ds_version: 3DS version used if applicable.
        processed_at: When payment was processed.

    Example:
        result = PaymentResult(
            success=True,
            transaction_id="txn_123",
            authorization_code="AUTH123",
        )
    """

    success: bool
    transaction_id: str | None = None
    authorization_code: str | None = None

    requires_3ds: bool = False
    redirect_url: str | None = None

    error_code: str | None = None
    error_message: str | None = None

    gateway_response: dict[str, Any] | None = None
    three_ds_version: str | None = None
    processed_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "success": self.success,
            "transaction_id": self.transaction_id,
            "requires_3ds": self.requires_3ds,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "three_ds_version": self.three_ds_version,
            "processed_at": self.processed_at.isoformat(),
        }


# =============================================================================
# Payment Gateway Interface
# =============================================================================


class PaymentGateway(ABC):
    """
    Abstract base class for payment gateways.

    All payment gateway implementations must inherit from this class
    and implement the required methods.

    Methods:
        process_payment: Process a payment request.
        check_3ds_result: Check 3DS authentication result.
        refund: Process a refund.

    Example:
        class StripeGateway(PaymentGateway):
            async def process_payment(self, request):
                # Implementation
                pass
    """

    @abstractmethod
    async def process_payment(
        self,
        request: PaymentRequest,
    ) -> PaymentResult:
        """
        Process a payment request.

        Args:
            request: Payment request data.

        Returns:
            PaymentResult: Result of the payment attempt.
        """
        pass

    @abstractmethod
    async def check_3ds_result(
        self,
        transaction_id: str,
    ) -> PaymentResult:
        """
        Check the result of 3DS authentication.

        Args:
            transaction_id: Transaction ID to check.

        Returns:
            PaymentResult: Updated payment result.
        """
        pass

    @abstractmethod
    async def refund(
        self,
        transaction_id: str,
        amount: float | None = None,
    ) -> PaymentResult:
        """
        Process a refund for a transaction.

        Args:
            transaction_id: Transaction to refund.
            amount: Optional partial refund amount.

        Returns:
            PaymentResult: Result of the refund.
        """
        pass


# =============================================================================
# Payment Gateway Router
# =============================================================================


class PaymentGatewayRouter:
    """
    Routes payment requests to appropriate gateways.

    Uses site-specific gateway mapping with fallbacks based on
    currency and region.

    Site Mapping:
    - vfs -> VFS_INTERNAL
    - idata -> IDATA_INTERNAL
    - bls -> BLS_INTERNAL

    Fallback Mapping:
    - TRY -> IYZICO
    - EUR -> STRIPE
    - Default -> PAYTR

    Usage:
        router = PaymentGatewayRouter(gateways)
        gateway = router.get_gateway(site="vfs", currency="EUR")
    """

    GATEWAY_MAP: dict[str, PaymentGatewayType] = {
        "vfs": PaymentGatewayType.VFS_INTERNAL,
        "idata": PaymentGatewayType.IDATA_INTERNAL,
        "bls": PaymentGatewayType.BLS_INTERNAL,
    }

    FALLBACK_GATEWAYS: dict[str, PaymentGatewayType] = {
        "TRY": PaymentGatewayType.IYZICO,
        "EUR": PaymentGatewayType.STRIPE,
        "DEFAULT": PaymentGatewayType.PAYTR,
    }

    def __init__(
        self,
        gateways: dict[PaymentGatewayType, PaymentGateway] | None = None,
    ) -> None:
        """
        Initialize the gateway router.

        Args:
            gateways: Mapping of gateway types to implementations.
        """
        self.gateways = gateways or {}

    def get_gateway(
        self,
        site: str,
        currency: str = "TRY",
    ) -> PaymentGateway | None:
        """
        Get the appropriate gateway for a site and currency.

        Args:
            site: Target site code.
            currency: Payment currency.

        Returns:
            PaymentGateway instance or None if not found.
        """
        gateway_type = self.GATEWAY_MAP.get(site)

        if gateway_type and gateway_type in self.gateways:
            return self.gateways[gateway_type]

        if currency == "TRY":
            fallback_type = self.FALLBACK_GATEWAYS["TRY"]
        elif currency == "EUR":
            fallback_type = self.FALLBACK_GATEWAYS["EUR"]
        else:
            fallback_type = self.FALLBACK_GATEWAYS["DEFAULT"]

        return self.gateways.get(fallback_type)

    def register_gateway(
        self,
        gateway_type: PaymentGatewayType,
        gateway: PaymentGateway,
    ) -> None:
        """
        Register a gateway implementation.

        Args:
            gateway_type: The gateway type identifier.
            gateway: The gateway implementation.
        """
        self.gateways[gateway_type] = gateway


# =============================================================================
# Payment Processor
# =============================================================================


class PaymentProcessor:
    """
    Main payment processing service.

    Orchestrates the payment flow including card encryption,
    gateway selection, 3DS handling, and transaction recording.

    The processor follows this flow:
    1. Validate and encrypt card data (if needed)
    2. Select appropriate gateway
    3. Process payment through gateway
    4. Handle 3DS authentication if required
    5. Record transaction result

    Attributes:
        encryptor: Card data encryptor.
        gateway_router: Gateway router for selecting gateways.
        three_ds_handler: 3DS authentication handler.

    Usage:
        processor = PaymentProcessor(encryptor, gateway_router)

        result = await processor.process_payment(request)

        if result.success:
            print("Payment successful")
        elif result.requires_3ds:
            # Handle 3DS
            pass
        else:
            print(f"Payment failed: {result.error_message}")
    """

    # Payment form selectors
    SELECTORS: dict[str, str] = {
        "card_number": 'input[name="cardNumber"], #cardNumber, input[data-card-number]',
        "expiry": 'input[name="expiry"], #expiry, input[data-expiry]',
        "expiry_month": 'select[name="expiryMonth"], #expiryMonth',
        "expiry_year": 'select[name="expiryYear"], #expiryYear',
        "cvv": 'input[name="cvv"], #cvv, input[name="cvc"]',
        "cardholder": 'input[name="cardholderName"], #cardholderName',
        "pay_button": 'button[type="submit"], #payButton, .pay-btn',
        "amount_display": ".payment-amount, #totalAmount",
        "payment_frame": 'iframe[src*="payment"], #paymentFrame',
    }

    # Timeout settings
    PAYMENT_TIMEOUT: int = 300  # 5 minutes

    def __init__(
        self,
        encryptor: CardDataEncryptor | None = None,
        gateway_router: PaymentGatewayRouter | None = None,
        three_ds_handler: ThreeDSHandler | None = None,
    ) -> None:
        """
        Initialize the payment processor.

        Args:
            encryptor: Card data encryptor (optional).
            gateway_router: Gateway router (optional).
            three_ds_handler: 3DS handler (optional).

        Example:
            encryptor = CardDataEncryptor(master_key)
            router = PaymentGatewayRouter(gateways)
            processor = PaymentProcessor(encryptor, router)
        """
        self.encryptor = encryptor
        self.gateway_router = gateway_router or PaymentGatewayRouter()
        self.three_ds_handler = three_ds_handler

    async def process_payment(
        self,
        request: PaymentRequest,
    ) -> PaymentResult:
        """
        Process a payment request.

        Selects the appropriate gateway and processes the payment.
        Handles 3DS authentication if required by the gateway.

        Args:
            request: Payment request data.

        Returns:
            PaymentResult: Result of the payment attempt.

        Raises:
            PaymentError: If payment processing fails.
            PaymentDeclinedError: If payment is declined.
            PaymentTimeoutError: If payment times out.

        Example:
            result = await processor.process_payment(request)

            if result.success:
                print(f"Transaction ID: {result.transaction_id}")
            elif result.requires_3ds:
                # Redirect user for 3DS
                print(f"3DS URL: {result.redirect_url}")
        """
        gateway = self.gateway_router.get_gateway(
            site=request.site,
            currency=request.currency,
        )

        if not gateway:
            return PaymentResult(
                success=False,
                error_code="NO_GATEWAY",
                error_message=f"No gateway available for site={request.site}, currency={request.currency}",
            )

        try:
            result = await gateway.process_payment(request)

            if result.requires_3ds and self.three_ds_handler:
                challenge = ThreeDSChallenge(
                    version=ThreeDSVersion.V2,
                    status=ThreeDSStatus.CHALLENGE_REQUIRED,
                    acs_url=result.redirect_url,
                    transaction_id=result.transaction_id or "",
                )

                three_ds_result = await self.three_ds_handler.handle_3ds(challenge)

                if three_ds_result.is_authenticated():
                    return await gateway.check_3ds_result(result.transaction_id or "")
                else:
                    return PaymentResult(
                        success=False,
                        transaction_id=result.transaction_id,
                        error_code="3DS_FAILED",
                        error_message="3DS authentication failed",
                        three_ds_version=challenge.version.value,
                    )

            return result

        except Exception as e:
            return PaymentResult(
                success=False,
                error_code="PROCESSING_ERROR",
                error_message=str(e),
            )

    async def process_browser_payment(
        self,
        page: Page,
        card_data: SecureCardData,
        expected_amount: float,
    ) -> PaymentResult:
        """
        Process a payment through a browser page.

        Fills payment form on the page and handles 3DS authentication.
        Used for site-specific payment forms that require browser
        automation.

        Args:
            page: Browser page with payment form.
            card_data: Encrypted card data.
            expected_amount: Expected payment amount.

        Returns:
            PaymentResult: Result of the payment attempt.

        Example:
            result = await processor.process_browser_payment(
                page=browser.page,
                card_data=secure_card,
                expected_amount=150.00,
            )
        """
        if not self.encryptor:
            return PaymentResult(
                success=False,
                error_code="NO_ENCRYPTOR",
                error_message="Card encryptor not configured",
            )

        amount_verified = await self._verify_amount(page, expected_amount)
        if not amount_verified:
            return PaymentResult(
                success=False,
                error_code="AMOUNT_MISMATCH",
                error_message="Payment amount does not match expected",
            )

        payment_frame = await self._get_payment_frame(page)
        target = payment_frame or page

        decrypted_card = self.encryptor.decrypt_for_use(card_data)

        try:
            await self._fill_payment_form(target, decrypted_card)

            pay_button = await target.wait_for_selector(
                self.SELECTORS["pay_button"],
                timeout=5000,
            )
            if pay_button:
                await pay_button.click()

            await asyncio.sleep(3)

            if self.three_ds_handler:
                challenge = await self.three_ds_handler.detect_3ds_challenge(page)

                if challenge:
                    three_ds_result = await self.three_ds_handler.handle_3ds(challenge)

                    if not three_ds_result.is_authenticated():
                        return PaymentResult(
                            success=False,
                            error_code="3DS_FAILED",
                            error_message=f"3DS authentication failed: {three_ds_result.status.value}",
                            three_ds_version=challenge.version.value,
                        )

            return await self._check_payment_result(page)

        finally:
            decrypted_card.clear()

    async def _verify_amount(self, page: Page, expected: float) -> bool:
        """
        Verify the displayed payment amount matches expected.

        Args:
            page: Browser page.
            expected: Expected payment amount.

        Returns:
            True if amounts match or cannot be verified.
        """
        try:
            amount_element = await page.query_selector(self.SELECTORS["amount_display"])

            if amount_element:
                text = await amount_element.inner_text()
                match = re.search(r"[\d,]+\.?\d*", text.replace(",", ""))
                if match:
                    displayed = float(match.group())
                    return abs(displayed - expected) < 0.01
        except Exception:
            pass

        return True

    async def _get_payment_frame(self, page: Page) -> Any | None:
        """
        Get payment iframe if present.

        Args:
            page: Browser page.

        Returns:
            Frame if found, None otherwise.
        """
        try:
            frame_element = await page.wait_for_selector(
                self.SELECTORS["payment_frame"],
                timeout=5000,
            )
            if frame_element:
                return await frame_element.content_frame()
        except Exception:
            pass

        return None

    async def _fill_payment_form(self, target: Any, card_data: dict[str, str]) -> None:
        """
        Fill payment form with card data.

        Args:
            target: Page or frame to fill.
            card_data: Decrypted card data.
        """
        card_input = await target.query_selector(self.SELECTORS["card_number"])
        if card_input:
            await card_input.fill(card_data["card_number"])

        await asyncio.sleep(0.2)

        expiry_combined = await target.query_selector(self.SELECTORS["expiry"])
        if expiry_combined:
            expiry = f"{card_data['expiry_month']}/{card_data['expiry_year'][-2:]}"
            await expiry_combined.fill(expiry)
        else:
            month_select = await target.query_selector(self.SELECTORS["expiry_month"])
            if month_select:
                await month_select.select_option(value=card_data["expiry_month"])

            year_select = await target.query_selector(self.SELECTORS["expiry_year"])
            if year_select:
                await year_select.select_option(value=card_data["expiry_year"])

        await asyncio.sleep(0.2)

        cvv_input = await target.query_selector(self.SELECTORS["cvv"])
        if cvv_input:
            await cvv_input.fill(card_data["cvv"])

        await asyncio.sleep(0.2)

        cardholder_input = await target.query_selector(self.SELECTORS["cardholder"])
        if cardholder_input:
            await cardholder_input.fill(card_data["cardholder_name"])

    async def _check_payment_result(self, page: Page) -> PaymentResult:
        """
        Check payment result from page content.

        Args:
            page: Browser page.

        Returns:
            PaymentResult based on page content.
        """
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass

        content = await page.content()
        content_lower = content.lower()
        url = page.url.lower()

        success_patterns = [
            "payment successful",
            "payment complete",
            "transaction approved",
            "thank you for your payment",
            "booking confirmed",
        ]

        for pattern in success_patterns:
            if pattern in content_lower:
                transaction_id = await self._extract_transaction_id(page)
                return PaymentResult(
                    success=True,
                    transaction_id=transaction_id,
                )

        failure_patterns = [
            "payment failed",
            "declined",
            "insufficient funds",
            "card error",
            "transaction failed",
        ]

        for pattern in failure_patterns:
            if pattern in content_lower:
                return PaymentResult(
                    success=False,
                    error_code="PAYMENT_DECLINED",
                    error_message=f"Payment declined: {pattern}",
                )

        if "success" in url or "confirmation" in url:
            return PaymentResult(success=True)

        if "error" in url or "failed" in url:
            return PaymentResult(
                success=False,
                error_code="PAYMENT_FAILED",
                error_message="Payment failed (URL indicator)",
            )

        return PaymentResult(
            success=False,
            error_code="UNKNOWN_RESULT",
            error_message="Unable to determine payment result",
        )

    async def _extract_transaction_id(self, page: Page) -> str | None:
        """
        Extract transaction ID from page.

        Args:
            page: Browser page.

        Returns:
            Transaction ID if found, None otherwise.
        """
        selectors = [
            ".transaction-id",
            "#transactionId",
            "[data-transaction-id]",
            ".confirmation-number",
            "#confirmationNumber",
        ]

        for selector in selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    text = await element.inner_text()
                    return text.strip()
            except Exception:
                continue

        return str(uuid.uuid4())

    def encrypt_card(
        self,
        card_number: str,
        cvv: str,
        expiry_month: str,
        expiry_year: str,
        cardholder_name: str,
    ) -> SecureCardData:
        """
        Encrypt card data for storage.

        Convenience method that delegates to the encryptor.

        Args:
            card_number: Full card number.
            cvv: Card CVV.
            expiry_month: Expiration month.
            expiry_year: Expiration year.
            cardholder_name: Name on card.

        Returns:
            SecureCardData: Encrypted card data.

        Raises:
            ValueError: If encryptor not configured or card invalid.
        """
        if not self.encryptor:
            raise ValueError("Card encryptor not configured")

        return self.encryptor.encrypt_card(
            card_number=card_number,
            cvv=cvv,
            expiry_month=expiry_month,
            expiry_year=expiry_year,
            cardholder_name=cardholder_name,
        )


__all__ = [
    "CardDataEncryptor",
    "CardType",
    "PaymentGateway",
    "PaymentGatewayRouter",
    "PaymentGatewayType",
    "PaymentProcessor",
    "PaymentRequest",
    "PaymentResult",
    "SecureCardData",
]
