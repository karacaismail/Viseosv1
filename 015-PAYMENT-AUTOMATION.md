# 015 - Payment Automation Specification

## Amaç

Vize randevu ödemelerinde tam otomatik kart işleme. 3D Secure handling, multi-gateway support, PCI-DSS uyumlu güvenli kart verisi yönetimi ve transaction monitoring. VFS Global, iDATA ve diğer sistemlerde farklı ödeme flow'larını destekleme.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 004-STEALTH-ENGINE | Browser context for payment forms |
| 008-011 | Site adapters - payment integration |
| 013-STATE-MACHINE | PAYMENT state handling |
| 002-DIRECTUS-SCHEMA | Payment records storage |

---

## Payment Flow Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        PAYMENT AUTOMATION FLOW                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │
│  │  Card Data   │    │   Gateway    │    │   3DS        │                  │
│  │  Decryptor   │───►│   Router     │───►│   Handler    │                  │
│  └──────────────┘    └──────────────┘    └──────────────┘                  │
│         │                   │                   │                           │
│         ▼                   ▼                   ▼                           │
│  ┌──────────────────────────────────────────────────────────┐              │
│  │                    PAYMENT GATEWAYS                       │              │
│  ├──────────────────────────────────────────────────────────┤              │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐    │              │
│  │  │  VFS    │  │ iDATA   │  │   BLS   │  │ Direct  │    │              │
│  │  │ PayGate │  │ PayGate │  │ PayGate │  │ 3DS     │    │              │
│  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘    │              │
│  └──────────────────────────────────────────────────────────┘              │
│                                │                                            │
│                                ▼                                            │
│  ┌──────────────────────────────────────────────────────────┐              │
│  │                 TRANSACTION MONITOR                       │              │
│  │  • Success/Failure tracking                               │              │
│  │  • Fraud detection                                        │              │
│  │  • Refund management                                      │              │
│  └──────────────────────────────────────────────────────────┘              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Card Data Security

```python
from dataclasses import dataclass
from typing import Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
import os

@dataclass
class SecureCardData:
    """Şifrelenmiş kart verisi"""
    encrypted_number: bytes
    encrypted_cvv: bytes
    expiry_month: str  # Not sensitive, plain
    expiry_year: str   # Not sensitive, plain
    cardholder_name: str
    
    # Metadata
    card_type: str  # visa, mastercard, amex
    last_four: str  # Display için
    
class CardDataEncryptor:
    """PCI-DSS uyumlu kart şifreleme"""
    
    def __init__(self, master_key: bytes):
        # Derive encryption key from master
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"vise_os_card_salt",  # In production: per-card salt
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(master_key))
        self.cipher = Fernet(key)
    
    def encrypt_card(
        self,
        card_number: str,
        cvv: str,
        expiry_month: str,
        expiry_year: str,
        cardholder_name: str,
    ) -> SecureCardData:
        """Kart verisini şifrele"""
        
        # Validate card number (Luhn check)
        if not self._luhn_check(card_number):
            raise ValueError("Invalid card number")
        
        # Detect card type
        card_type = self._detect_card_type(card_number)
        
        # Encrypt sensitive fields
        encrypted_number = self.cipher.encrypt(card_number.encode())
        encrypted_cvv = self.cipher.encrypt(cvv.encode())
        
        return SecureCardData(
            encrypted_number=encrypted_number,
            encrypted_cvv=encrypted_cvv,
            expiry_month=expiry_month,
            expiry_year=expiry_year,
            cardholder_name=cardholder_name,
            card_type=card_type,
            last_four=card_number[-4:],
        )
    
    def decrypt_for_use(
        self,
        secure_card: SecureCardData,
    ) -> dict:
        """Kullanım için decrypt (memory-only)"""
        
        return {
            "card_number": self.cipher.decrypt(secure_card.encrypted_number).decode(),
            "cvv": self.cipher.decrypt(secure_card.encrypted_cvv).decode(),
            "expiry_month": secure_card.expiry_month,
            "expiry_year": secure_card.expiry_year,
            "cardholder_name": secure_card.cardholder_name,
        }
    
    def _luhn_check(self, card_number: str) -> bool:
        """Luhn algorithm validation"""
        
        digits = [int(d) for d in card_number if d.isdigit()]
        
        if len(digits) < 13 or len(digits) > 19:
            return False
        
        # Luhn algorithm
        odd_digits = digits[-1::-2]
        even_digits = digits[-2::-2]
        
        total = sum(odd_digits)
        for d in even_digits:
            total += sum(divmod(d * 2, 10))
        
        return total % 10 == 0
    
    def _detect_card_type(self, card_number: str) -> str:
        """Kart tipini tespit et"""
        
        if card_number.startswith("4"):
            return "visa"
        elif card_number.startswith(("51", "52", "53", "54", "55")):
            return "mastercard"
        elif card_number.startswith(("34", "37")):
            return "amex"
        elif card_number.startswith("9792"):
            return "troy"
        else:
            return "unknown"
```

---

## Payment Gateway Router

```python
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, Any
from abc import ABC, abstractmethod

class PaymentGatewayType(Enum):
    """Ödeme gateway tipleri"""
    VFS_INTERNAL = "vfs_internal"      # VFS kendi gateway'i
    IDATA_INTERNAL = "idata_internal"  # iDATA kendi gateway'i
    BLS_INTERNAL = "bls_internal"      # BLS kendi gateway'i
    IYZICO = "iyzico"                  # Türkiye yerel
    STRIPE = "stripe"                  # International
    PAYTR = "paytr"                    # Türkiye yerel

@dataclass
class PaymentRequest:
    """Ödeme isteği"""
    booking_id: str
    amount: float
    currency: str  # TRY, EUR, USD
    card_data: SecureCardData
    
    # Billing info
    billing_name: str
    billing_email: str
    billing_phone: str
    billing_address: Optional[str] = None
    
    # Context
    site: str  # vfs, idata, bls
    return_url: Optional[str] = None
    
@dataclass
class PaymentResult:
    """Ödeme sonucu"""
    success: bool
    transaction_id: Optional[str] = None
    authorization_code: Optional[str] = None
    
    # 3DS
    requires_3ds: bool = False
    redirect_url: Optional[str] = None
    
    # Error
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    
    # Metadata
    gateway_response: Optional[Dict] = None

class PaymentGateway(ABC):
    """Abstract payment gateway"""
    
    @abstractmethod
    async def process_payment(
        self,
        request: PaymentRequest,
    ) -> PaymentResult:
        pass
    
    @abstractmethod
    async def check_3ds_result(
        self,
        transaction_id: str,
    ) -> PaymentResult:
        pass
    
    @abstractmethod
    async def refund(
        self,
        transaction_id: str,
        amount: Optional[float] = None,
    ) -> PaymentResult:
        pass

class PaymentGatewayRouter:
    """Gateway routing"""
    
    # Site -> Gateway mapping
    GATEWAY_MAP = {
        "vfs": PaymentGatewayType.VFS_INTERNAL,
        "idata": PaymentGatewayType.IDATA_INTERNAL,
        "bls": PaymentGatewayType.BLS_INTERNAL,
    }
    
    # Fallback gateways
    FALLBACK_GATEWAYS = {
        "TR": PaymentGatewayType.IYZICO,
        "EU": PaymentGatewayType.STRIPE,
        "DEFAULT": PaymentGatewayType.PAYTR,
    }
    
    def __init__(self, gateways: Dict[PaymentGatewayType, PaymentGateway]):
        self.gateways = gateways
    
    def get_gateway(
        self,
        site: str,
        currency: str = "TRY",
    ) -> PaymentGateway:
        """Uygun gateway seç"""
        
        # Site-specific gateway
        gateway_type = self.GATEWAY_MAP.get(site)
        
        if gateway_type and gateway_type in self.gateways:
            return self.gateways[gateway_type]
        
        # Fallback based on currency/region
        if currency == "TRY":
            fallback_type = self.FALLBACK_GATEWAYS["TR"]
        elif currency == "EUR":
            fallback_type = self.FALLBACK_GATEWAYS["EU"]
        else:
            fallback_type = self.FALLBACK_GATEWAYS["DEFAULT"]
        
        return self.gateways.get(fallback_type)
```

---

## 3D Secure Handler

```python
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, Any
import asyncio

class ThreeDSVersion(Enum):
    """3DS versiyonları"""
    V1 = "1.0"
    V2 = "2.0"
    V2_1 = "2.1"
    V2_2 = "2.2"

class ThreeDSStatus(Enum):
    """3DS durumları"""
    PENDING = "pending"
    CHALLENGE_REQUIRED = "challenge_required"
    FRICTIONLESS = "frictionless"
    AUTHENTICATED = "authenticated"
    FAILED = "failed"
    TIMEOUT = "timeout"

@dataclass
class ThreeDSChallenge:
    """3DS challenge bilgisi"""
    version: ThreeDSVersion
    status: ThreeDSStatus
    
    # Challenge details
    acs_url: Optional[str] = None
    pareq: Optional[str] = None  # 3DS 1.0
    creq: Optional[str] = None   # 3DS 2.0
    
    # Transaction reference
    transaction_id: str = ""
    md: Optional[str] = None  # Merchant Data
    
    # Iframe/redirect
    method: str = "iframe"  # iframe, redirect, popup
    
class ThreeDSHandler:
    """3D Secure handling"""
    
    # Timeout settings
    CHALLENGE_TIMEOUT = 180  # 3 minutes
    POLL_INTERVAL = 2  # seconds
    
    def __init__(self, browser_session):
        self.browser = browser_session
    
    async def handle_3ds(
        self,
        challenge: ThreeDSChallenge,
    ) -> ThreeDSStatus:
        """3DS challenge handle et"""
        
        if challenge.version == ThreeDSVersion.V1:
            return await self._handle_3ds_v1(challenge)
        else:
            return await self._handle_3ds_v2(challenge)
    
    async def _handle_3ds_v1(
        self,
        challenge: ThreeDSChallenge,
    ) -> ThreeDSStatus:
        """3DS 1.0 handling (redirect/iframe)"""
        
        page = self.browser.page
        
        if challenge.method == "redirect":
            # Full page redirect to ACS
            await page.goto(challenge.acs_url)
        else:
            # Handle in iframe
            frame = await self._wait_for_3ds_frame(page)
            
            if not frame:
                return ThreeDSStatus.FAILED
        
        # Wait for 3DS completion
        return await self._wait_for_completion(challenge.transaction_id)
    
    async def _handle_3ds_v2(
        self,
        challenge: ThreeDSChallenge,
    ) -> ThreeDSStatus:
        """3DS 2.0/2.1/2.2 handling"""
        
        page = self.browser.page
        
        if challenge.status == ThreeDSStatus.FRICTIONLESS:
            # No challenge needed
            return ThreeDSStatus.AUTHENTICATED
        
        # Challenge required - inject challenge request
        if challenge.creq:
            # Submit CReq to ACS
            await self._submit_creq(page, challenge)
        
        # Wait for challenge completion
        return await self._wait_for_completion(challenge.transaction_id)
    
    async def _wait_for_3ds_frame(self, page) -> Optional[Any]:
        """3DS iframe bekle"""
        
        frame_selectors = [
            'iframe[name*="3ds"]',
            'iframe[src*="3dsecure"]',
            'iframe[src*="acs"]',
            'iframe[id*="cardinal"]',
            '#threeDSIframe',
        ]
        
        for selector in frame_selectors:
            try:
                frame_element = await page.wait_for_selector(
                    selector,
                    timeout=10000,
                )
                if frame_element:
                    return await frame_element.content_frame()
            except:
                continue
        
        return None
    
    async def _submit_creq(self, page, challenge: ThreeDSChallenge):
        """3DS 2.0 CReq submit"""
        
        # Create hidden form and submit
        await page.evaluate(f"""
            const form = document.createElement('form');
            form.method = 'POST';
            form.action = '{challenge.acs_url}';
            form.target = 'threeDSFrame';
            
            const creqInput = document.createElement('input');
            creqInput.type = 'hidden';
            creqInput.name = 'creq';
            creqInput.value = '{challenge.creq}';
            form.appendChild(creqInput);
            
            document.body.appendChild(form);
            form.submit();
        """)
    
    async def _wait_for_completion(
        self,
        transaction_id: str,
    ) -> ThreeDSStatus:
        """3DS tamamlanmasını bekle"""
        
        page = self.browser.page
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < self.CHALLENGE_TIMEOUT:
            # Check for success indicators
            url = page.url
            content = await page.content()
            
            # Success patterns
            if any(pattern in url.lower() for pattern in ["success", "complete", "return"]):
                return ThreeDSStatus.AUTHENTICATED
            
            if any(pattern in content.lower() for pattern in ["payment successful", "authenticated"]):
                return ThreeDSStatus.AUTHENTICATED
            
            # Failure patterns
            if any(pattern in content.lower() for pattern in ["failed", "declined", "error"]):
                return ThreeDSStatus.FAILED
            
            await asyncio.sleep(self.POLL_INTERVAL)
        
        return ThreeDSStatus.TIMEOUT
    
    async def handle_bank_sms_otp(
        self,
        page,
        otp_code: str,
    ) -> bool:
        """Banka SMS OTP girişi"""
        
        # Common OTP input selectors
        otp_selectors = [
            'input[name*="otp"]',
            'input[name*="sms"]',
            'input[id*="otp"]',
            'input[type="tel"]',
            'input[maxlength="6"]',
            '#otpInput',
        ]
        
        for selector in otp_selectors:
            try:
                otp_input = await page.wait_for_selector(selector, timeout=5000)
                if otp_input:
                    await otp_input.fill(otp_code)
                    
                    # Find and click submit
                    submit_btn = await page.query_selector(
                        'button[type="submit"], input[type="submit"], .otp-submit'
                    )
                    if submit_btn:
                        await submit_btn.click()
                        return True
            except:
                continue
        
        return False
```

---

## Site-Specific Payment Handlers

```python
class VFSPaymentHandler:
    """VFS Global ödeme işlemi"""
    
    # VFS payment form selectors
    SELECTORS = {
        "payment_frame": 'iframe[src*="payment"], #paymentFrame',
        "card_number": 'input[name="cardNumber"], #cardNumber',
        "expiry": 'input[name="expiry"], #expiry',
        "expiry_month": 'select[name="expiryMonth"], #expiryMonth',
        "expiry_year": 'select[name="expiryYear"], #expiryYear',
        "cvv": 'input[name="cvv"], #cvv, input[name="cvc"]',
        "cardholder": 'input[name="cardholderName"], #cardholderName',
        "pay_button": 'button[type="submit"], #payButton, .pay-btn',
        "amount_display": '.payment-amount, #totalAmount',
    }
    
    def __init__(
        self,
        browser_session,
        card_encryptor: CardDataEncryptor,
        three_ds_handler: ThreeDSHandler,
    ):
        self.browser = browser_session
        self.encryptor = card_encryptor
        self.three_ds = three_ds_handler
    
    async def process_payment(
        self,
        secure_card: SecureCardData,
        expected_amount: float,
    ) -> PaymentResult:
        """VFS ödeme işlemi"""
        
        page = self.browser.page
        
        # Verify amount matches
        amount_verified = await self._verify_amount(page, expected_amount)
        if not amount_verified:
            return PaymentResult(
                success=False,
                error_code="AMOUNT_MISMATCH",
                error_message="Payment amount doesn't match expected",
            )
        
        # Check for payment iframe
        payment_frame = await self._get_payment_frame(page)
        target = payment_frame or page
        
        # Decrypt card data (memory only)
        card_data = self.encryptor.decrypt_for_use(secure_card)
        
        try:
            # Fill payment form
            await self._fill_payment_form(target, card_data)
            
            # Submit payment
            await target.click(self.SELECTORS["pay_button"])
            
            # Wait for response
            await asyncio.sleep(3)
            
            # Check for 3DS
            three_ds_challenge = await self._detect_3ds(page)
            
            if three_ds_challenge:
                three_ds_result = await self.three_ds.handle_3ds(three_ds_challenge)
                
                if three_ds_result != ThreeDSStatus.AUTHENTICATED:
                    return PaymentResult(
                        success=False,
                        error_code="3DS_FAILED",
                        error_message=f"3DS authentication failed: {three_ds_result.value}",
                    )
            
            # Check final result
            return await self._check_payment_result(page)
            
        finally:
            # Clear card data from memory
            card_data = None
    
    async def _verify_amount(self, page, expected: float) -> bool:
        """Ödeme tutarını doğrula"""
        
        amount_element = await page.query_selector(self.SELECTORS["amount_display"])
        
        if amount_element:
            text = await amount_element.inner_text()
            # Extract number from text (e.g., "€150.00" -> 150.00)
            import re
            match = re.search(r'[\d,]+\.?\d*', text.replace(',', ''))
            if match:
                displayed = float(match.group())
                return abs(displayed - expected) < 0.01
        
        return True  # Can't verify, proceed
    
    async def _get_payment_frame(self, page):
        """Payment iframe al"""
        
        try:
            frame_element = await page.wait_for_selector(
                self.SELECTORS["payment_frame"],
                timeout=5000,
            )
            if frame_element:
                return await frame_element.content_frame()
        except:
            pass
        
        return None
    
    async def _fill_payment_form(self, target, card_data: dict):
        """Ödeme formunu doldur"""
        
        from human_behavior import HumanTypingSimulator
        typing = HumanTypingSimulator(target)
        
        # Card number
        await typing.type_text(
            self.SELECTORS["card_number"],
            card_data["card_number"],
        )
        await asyncio.sleep(0.3)
        
        # Expiry - check if combined or separate fields
        expiry_combined = await target.query_selector(self.SELECTORS["expiry"])
        
        if expiry_combined:
            # MM/YY format
            expiry = f"{card_data['expiry_month']}/{card_data['expiry_year'][-2:]}"
            await typing.type_text(self.SELECTORS["expiry"], expiry)
        else:
            # Separate dropdowns
            await target.select_option(
                self.SELECTORS["expiry_month"],
                value=card_data["expiry_month"],
            )
            await target.select_option(
                self.SELECTORS["expiry_year"],
                value=card_data["expiry_year"],
            )
        
        await asyncio.sleep(0.3)
        
        # CVV
        await typing.type_text(self.SELECTORS["cvv"], card_data["cvv"])
        await asyncio.sleep(0.3)
        
        # Cardholder name (if present)
        cardholder_field = await target.query_selector(self.SELECTORS["cardholder"])
        if cardholder_field:
            await typing.type_text(
                self.SELECTORS["cardholder"],
                card_data["cardholder_name"],
            )
    
    async def _detect_3ds(self, page) -> Optional[ThreeDSChallenge]:
        """3DS challenge tespit et"""
        
        # Check for 3DS indicators
        indicators = [
            'iframe[src*="3ds"]',
            'iframe[src*="secure"]',
            '#threeDSContainer',
            '.3ds-frame',
        ]
        
        for selector in indicators:
            element = await page.query_selector(selector)
            if element:
                # Extract 3DS details
                frame = await element.content_frame() if "iframe" in selector else None
                
                return ThreeDSChallenge(
                    version=ThreeDSVersion.V2,
                    status=ThreeDSStatus.CHALLENGE_REQUIRED,
                    method="iframe",
                    transaction_id="",
                )
        
        # Check URL for 3DS redirect
        if "3ds" in page.url.lower() or "secure" in page.url.lower():
            return ThreeDSChallenge(
                version=ThreeDSVersion.V1,
                status=ThreeDSStatus.CHALLENGE_REQUIRED,
                acs_url=page.url,
                method="redirect",
                transaction_id="",
            )
        
        return None
    
    async def _check_payment_result(self, page) -> PaymentResult:
        """Ödeme sonucunu kontrol et"""
        
        await page.wait_for_load_state("networkidle")
        
        content = await page.content()
        url = page.url
        
        # Success indicators
        success_patterns = [
            "payment successful",
            "payment complete",
            "transaction approved",
            "thank you for your payment",
        ]
        
        for pattern in success_patterns:
            if pattern in content.lower():
                return PaymentResult(
                    success=True,
                    transaction_id=await self._extract_transaction_id(page),
                )
        
        # Failure indicators
        failure_patterns = [
            "payment failed",
            "declined",
            "insufficient funds",
            "card error",
            "transaction failed",
        ]
        
        for pattern in failure_patterns:
            if pattern in content.lower():
                return PaymentResult(
                    success=False,
                    error_code="PAYMENT_DECLINED",
                    error_message=f"Payment declined: {pattern}",
                )
        
        # Check URL
        if "success" in url.lower() or "confirmation" in url.lower():
            return PaymentResult(success=True)
        
        if "error" in url.lower() or "failed" in url.lower():
            return PaymentResult(
                success=False,
                error_code="PAYMENT_FAILED",
                error_message="Payment failed (URL indicator)",
            )
        
        # Ambiguous
        return PaymentResult(
            success=False,
            error_code="UNKNOWN_RESULT",
            error_message="Unable to determine payment result",
        )
    
    async def _extract_transaction_id(self, page) -> Optional[str]:
        """Transaction ID çıkar"""
        
        selectors = [
            '.transaction-id',
            '#transactionId',
            '[data-transaction-id]',
        ]
        
        for selector in selectors:
            element = await page.query_selector(selector)
            if element:
                return await element.inner_text()
        
        return None
```

---

## Transaction Monitor

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict
from enum import Enum

class TransactionStatus(Enum):
    """Transaction durumları"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"
    PARTIAL_REFUND = "partial_refund"
    CHARGEBACK = "chargeback"

@dataclass
class Transaction:
    """Transaction kaydı"""
    id: str
    booking_id: str
    agency_id: str
    
    # Amount
    amount: float
    currency: str
    
    # Status
    status: TransactionStatus
    
    # Gateway info
    gateway: PaymentGatewayType
    gateway_transaction_id: Optional[str] = None
    authorization_code: Optional[str] = None
    
    # Card info (masked)
    card_type: str = ""
    card_last_four: str = ""
    
    # Timestamps
    created_at: datetime = None
    completed_at: Optional[datetime] = None
    
    # Error
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    
    # 3DS
    three_ds_version: Optional[str] = None
    three_ds_status: Optional[str] = None
    
    # Refund
    refund_amount: float = 0.0
    refund_reason: Optional[str] = None

class TransactionMonitor:
    """Transaction monitoring ve tracking"""
    
    def __init__(self, db_client):
        self.db = db_client
    
    async def record_transaction(
        self,
        booking_id: str,
        agency_id: str,
        amount: float,
        currency: str,
        gateway: PaymentGatewayType,
        card_type: str,
        card_last_four: str,
    ) -> Transaction:
        """Yeni transaction kaydet"""
        
        transaction = Transaction(
            id=str(uuid.uuid4()),
            booking_id=booking_id,
            agency_id=agency_id,
            amount=amount,
            currency=currency,
            status=TransactionStatus.PENDING,
            gateway=gateway,
            card_type=card_type,
            card_last_four=card_last_four,
            created_at=datetime.utcnow(),
        )
        
        await self.db.items("payment_transactions").create(
            self._to_dict(transaction)
        )
        
        return transaction
    
    async def update_transaction(
        self,
        transaction_id: str,
        status: TransactionStatus,
        gateway_transaction_id: Optional[str] = None,
        authorization_code: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        three_ds_version: Optional[str] = None,
        three_ds_status: Optional[str] = None,
    ):
        """Transaction güncelle"""
        
        update_data = {
            "status": status.value,
            "updated_at": datetime.utcnow().isoformat(),
        }
        
        if gateway_transaction_id:
            update_data["gateway_transaction_id"] = gateway_transaction_id
        if authorization_code:
            update_data["authorization_code"] = authorization_code
        if error_code:
            update_data["error_code"] = error_code
        if error_message:
            update_data["error_message"] = error_message
        if three_ds_version:
            update_data["three_ds_version"] = three_ds_version
        if three_ds_status:
            update_data["three_ds_status"] = three_ds_status
        
        if status == TransactionStatus.COMPLETED:
            update_data["completed_at"] = datetime.utcnow().isoformat()
        
        await self.db.items("payment_transactions").update(
            transaction_id,
            update_data,
        )
    
    async def record_refund(
        self,
        transaction_id: str,
        amount: float,
        reason: str,
    ):
        """Refund kaydet"""
        
        transaction = await self.db.items("payment_transactions").read_one(transaction_id)
        
        new_refund_total = transaction.get("refund_amount", 0) + amount
        
        status = TransactionStatus.REFUNDED
        if new_refund_total < transaction["amount"]:
            status = TransactionStatus.PARTIAL_REFUND
        
        await self.db.items("payment_transactions").update(
            transaction_id,
            {
                "status": status.value,
                "refund_amount": new_refund_total,
                "refund_reason": reason,
                "updated_at": datetime.utcnow().isoformat(),
            }
        )
    
    async def get_daily_stats(
        self,
        agency_id: Optional[str] = None,
    ) -> Dict:
        """Günlük istatistikler"""
        
        today = datetime.utcnow().date()
        
        filter_query = {
            "created_at": {"_gte": today.isoformat()}
        }
        
        if agency_id:
            filter_query["agency_id"] = {"_eq": agency_id}
        
        transactions = await self.db.items("payment_transactions").read(
            filter=filter_query
        )
        
        total = len(transactions)
        completed = len([t for t in transactions if t["status"] == "completed"])
        failed = len([t for t in transactions if t["status"] == "failed"])
        
        total_amount = sum(
            t["amount"] for t in transactions
            if t["status"] == "completed"
        )
        
        return {
            "date": today.isoformat(),
            "total_transactions": total,
            "completed": completed,
            "failed": failed,
            "success_rate": completed / max(1, total),
            "total_amount": total_amount,
            "by_gateway": self._group_by_gateway(transactions),
            "by_card_type": self._group_by_card_type(transactions),
        }
    
    def _group_by_gateway(self, transactions: List[Dict]) -> Dict:
        """Gateway'e göre grupla"""
        
        result = {}
        for t in transactions:
            gateway = t.get("gateway", "unknown")
            if gateway not in result:
                result[gateway] = {"count": 0, "amount": 0}
            result[gateway]["count"] += 1
            if t["status"] == "completed":
                result[gateway]["amount"] += t["amount"]
        return result
    
    def _group_by_card_type(self, transactions: List[Dict]) -> Dict:
        """Kart tipine göre grupla"""
        
        result = {}
        for t in transactions:
            card_type = t.get("card_type", "unknown")
            if card_type not in result:
                result[card_type] = 0
            result[card_type] += 1
        return result
    
    def _to_dict(self, transaction: Transaction) -> Dict:
        """Transaction'ı dict'e çevir"""
        
        return {
            "id": transaction.id,
            "booking_id": transaction.booking_id,
            "agency_id": transaction.agency_id,
            "amount": transaction.amount,
            "currency": transaction.currency,
            "status": transaction.status.value,
            "gateway": transaction.gateway.value,
            "gateway_transaction_id": transaction.gateway_transaction_id,
            "authorization_code": transaction.authorization_code,
            "card_type": transaction.card_type,
            "card_last_four": transaction.card_last_four,
            "created_at": transaction.created_at.isoformat() if transaction.created_at else None,
            "error_code": transaction.error_code,
            "error_message": transaction.error_message,
        }
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | Kart verisi şifrelenmeli (AES-256) | Security audit |
| AC-002 | Luhn validation çalışmalı | Unit test |
| AC-003 | 3DS 1.0/2.0 handling çalışmalı | Integration test |
| AC-004 | VFS payment flow %95+ başarı | E2E test |
| AC-005 | Transaction logging complete olmalı | Audit test |
| AC-006 | Refund işlemi çalışmalı | Refund test |
| AC-007 | Card data memory-only olmalı | Security test |

---

## Güvenlik Notları

1. **PCI-DSS Compliance:**
   - Kart verisi encrypted at rest
   - Memory-only decryption
   - No logging of full card number
   - CVV never stored

2. **Card Data Flow:**
   ```
   User Input → Encrypt (client) → Store (encrypted) → 
   Decrypt (memory) → Use → Clear (memory)
   ```

3. **3DS Security:**
   - Bank'ın 3DS sayfasına müdahale yok
   - Sadece completion bekleme
   - OTP handling sadece user consent ile

---

## Sonraki Adımlar

1. **016-EMAIL-VERIFICATION.md** - Email doğrulama
2. PCI-DSS compliance audit
3. 3DS test kartları ile integration test
