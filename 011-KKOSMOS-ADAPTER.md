# 011 - KKOSMOS Adapter Specification

## Amaç

KKOSMOS sisteminde (Yunanistan Schengen vizeleri) SMS verification zorunluluğu ile tam otomatik vize randevusu alma. Phone number pool yönetimi, SMS code extraction ve voice call handling.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 004-STEALTH-ENGINE | Browser session |
| 005-PROXY-MANAGER | Residential proxy |
| 006-CAPTCHA-SOLVER | reCAPTCHA v2 |
| 007-ACCOUNT-POOL-MANAGER | KKOSMOS account credentials |
| 017-SMS-VERIFICATION | Phone pool + SMS extraction |
| 013-STATE-MACHINE | Booking state transitions |

---

## KKOSMOS Teknik Profil

| Özellik | Değer |
|---------|-------|
| Anti-Bot | reCAPTCHA + SMS/Phone Verification |
| CAPTCHA | reCAPTCHA v2 |
| TLS Fingerprinting | Minimal |
| SMS Verification | **ZORUNLU** - her işlemde |
| Phone Call | Alternatif - IVR sistemi |
| Rate Limiting | Agresif, 3 saat ban |
| Session Timeout | 15 dakika |
| Risk Seviyesi | **YÜKSEK** - telefon arıyor |

---

## Kritik Uyarı: Telefon Doğrulama

KKOSMOS iki yöntem kullanıyor:

1. **SMS Verification**: Kod SMS ile geliyor
2. **Voice Call (IVR)**: Telefon arıyor, kod söyleniyor

**Botçular riski nedeniyle genelde KKOSMOS'a girmiyorlar.** Bu adapter yüksek riskli operasyon için.

---

## URL Yapısı

```python
class KKOSMOSURLBuilder:
    """KKOSMOS URL yapıcı"""
    
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
        return f"{cls.BASE_URL}{cls.ENDPOINTS['login']}"
    
    @classmethod
    def get_appointment_url(cls) -> str:
        return f"{cls.APPOINTMENT_BASE}{cls.ENDPOINTS['appointment']}"
    
    @classmethod
    def get_verification_url(cls) -> str:
        return f"{cls.BASE_URL}{cls.ENDPOINTS['verification']}"
```

---

## Phone Number Pool Manager

```python
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, List
from datetime import datetime, timedelta
import asyncio

class PhoneStatus(Enum):
    AVAILABLE = "available"
    IN_USE = "in_use"
    COOLDOWN = "cooldown"
    BURNED = "burned"
    VOICE_ONLY = "voice_only"  # SMS almıyor ama call alabilir

@dataclass
class PhoneNumber:
    """Sanal telefon numarası"""
    id: str
    provider: str  # "5sim", "smshub", "smsactivate"
    number: str    # Full number with country code
    country: str   # "tr", "de", etc.
    status: PhoneStatus
    
    usage_count: int = 0
    max_usage: int = 10
    
    last_used_at: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None
    burned_at: Optional[datetime] = None
    
    # SMS tracking
    sms_received: int = 0
    sms_failed: int = 0
    
    # Voice call tracking
    voice_received: int = 0
    voice_failed: int = 0

class PhonePoolManager:
    """Sanal telefon numarası havuzu"""
    
    # Cooldown settings
    COOLDOWN_AFTER_USE_MINUTES = 30
    MAX_CONSECUTIVE_FAILURES = 3
    
    def __init__(self):
        self.phones: Dict[str, PhoneNumber] = {}
        self.lock = asyncio.Lock()
        
        # Provider API clients
        self.providers: Dict[str, Any] = {}
    
    async def initialize(self, provider_configs: Dict[str, str]):
        """Provider'ları başlat"""
        
        if provider_configs.get("5sim_api_key"):
            self.providers["5sim"] = FiveSimClient(provider_configs["5sim_api_key"])
        
        if provider_configs.get("smshub_api_key"):
            self.providers["smshub"] = SMSHubClient(provider_configs["smshub_api_key"])
        
        if provider_configs.get("smsactivate_api_key"):
            self.providers["smsactivate"] = SMSActivateClient(provider_configs["smsactivate_api_key"])
    
    async def acquire_number(
        self,
        country: str = "tr",
        service: str = "kkosmos",
        prefer_voice: bool = False,
    ) -> Optional[PhoneNumber]:
        """Havuzdan numara al veya yeni satın al"""
        
        async with self.lock:
            # Try to find available number
            phone = self._find_available(country, prefer_voice)
            
            if phone:
                phone.status = PhoneStatus.IN_USE
                phone.last_used_at = datetime.utcnow()
                return phone
            
            # No available, purchase new
            phone = await self._purchase_new(country, service)
            
            if phone:
                self.phones[phone.id] = phone
                phone.status = PhoneStatus.IN_USE
                return phone
            
            return None
    
    def _find_available(
        self,
        country: str,
        prefer_voice: bool,
    ) -> Optional[PhoneNumber]:
        """Müsait numara bul"""
        
        now = datetime.utcnow()
        candidates = []
        
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
            
            # Voice preference
            if prefer_voice and phone.status == PhoneStatus.VOICE_ONLY:
                candidates.append((phone, 10))  # Higher priority
            elif phone.status == PhoneStatus.AVAILABLE:
                candidates.append((phone, 5))
        
        if not candidates:
            return None
        
        # Sort by priority
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]
    
    async def _purchase_new(
        self,
        country: str,
        service: str,
    ) -> Optional[PhoneNumber]:
        """Yeni numara satın al"""
        
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
                continue
        
        return None
    
    async def release_number(
        self,
        phone_id: str,
        success: bool,
        sms_received: bool = False,
        voice_received: bool = False,
    ):
        """Numarayı serbest bırak"""
        
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
                else:
                    phone.status = PhoneStatus.AVAILABLE
    
    async def get_pool_stats(self) -> Dict:
        """Havuz istatistikleri"""
        
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
```

---

## SMS/Voice Provider Clients

```python
class FiveSimClient:
    """5sim.net API client"""
    
    BASE_URL = "https://5sim.net/v1"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"}
        )
    
    async def purchase_number(
        self,
        country: str,
        service: str,
    ) -> Optional[Dict]:
        """Numara satın al"""
        
        # 5sim country/operator mapping
        country_code = {"tr": "turkey", "de": "germany"}.get(country, country)
        
        response = await self.client.get(
            f"{self.BASE_URL}/user/buy/activation/{country_code}/any/{service}"
        )
        
        if response.status_code == 200:
            data = response.json()
            return {
                "id": str(data["id"]),
                "number": data["phone"],
            }
        
        return None
    
    async def get_sms(self, order_id: str, timeout: int = 120) -> Optional[str]:
        """SMS kodunu bekle ve al"""
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            response = await self.client.get(
                f"{self.BASE_URL}/user/check/{order_id}"
            )
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get("sms"):
                    # Extract code from SMS
                    code = self._extract_code(data["sms"][0]["text"])
                    if code:
                        return code
            
            await asyncio.sleep(5)
        
        return None
    
    def _extract_code(self, sms_text: str) -> Optional[str]:
        """SMS metninden kodu çıkar"""
        
        # Common patterns
        patterns = [
            r"(\d{6})",      # 6 digit code
            r"(\d{4})",      # 4 digit code
            r"kod[u]?:\s*(\d+)",  # "kod: XXXX"
            r"code[:]?\s*(\d+)",  # "code: XXXX"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, sms_text, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None
    
    async def cancel_number(self, order_id: str):
        """Numarayı iptal et (refund için)"""
        await self.client.get(f"{self.BASE_URL}/user/cancel/{order_id}")
    
    async def finish_number(self, order_id: str):
        """İşlemi tamamla"""
        await self.client.get(f"{self.BASE_URL}/user/finish/{order_id}")


class SMSHubClient:
    """SMSHub API client"""
    
    BASE_URL = "https://smshub.org/stubs/handler_api.php"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient()
    
    async def purchase_number(
        self,
        country: str,
        service: str,
    ) -> Optional[Dict]:
        """Numara satın al"""
        
        # SMSHub country codes
        country_code = {"tr": "62", "de": "43"}.get(country, "0")
        
        response = await self.client.get(
            self.BASE_URL,
            params={
                "api_key": self.api_key,
                "action": "getNumber",
                "service": "ot",  # Other service
                "country": country_code,
            }
        )
        
        if response.text.startswith("ACCESS_NUMBER"):
            parts = response.text.split(":")
            return {
                "id": parts[1],
                "number": parts[2],
            }
        
        return None
    
    async def get_sms(self, order_id: str, timeout: int = 120) -> Optional[str]:
        """SMS kodunu bekle"""
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            response = await self.client.get(
                self.BASE_URL,
                params={
                    "api_key": self.api_key,
                    "action": "getStatus",
                    "id": order_id,
                }
            )
            
            if response.text.startswith("STATUS_OK"):
                code = response.text.split(":")[1]
                return code
            
            await asyncio.sleep(5)
        
        return None
```

---

## KKOSMOS Adapter Ana Sınıf

```python
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, List, Any
from datetime import datetime, date
import asyncio

class KKOSMOSFlowState(Enum):
    """KKOSMOS booking flow states"""
    INITIAL = "initial"
    NAVIGATING = "navigating"
    LOGIN_PAGE = "login_page"
    AUTHENTICATING = "authenticating"
    AUTHENTICATED = "authenticated"
    SELECTING_SERVICE = "selecting_service"
    CHECKING_SLOTS = "checking_slots"
    SLOT_FOUND = "slot_found"
    FILLING_FORM = "filling_form"
    REQUESTING_VERIFICATION = "requesting_verification"
    WAITING_SMS = "waiting_sms"
    WAITING_VOICE = "waiting_voice"
    ENTERING_CODE = "entering_code"
    CONFIRMING = "confirming"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class KKOSMOSSlot:
    """KKOSMOS randevu slot"""
    date: date
    time: str
    center: str
    visa_type: str
    slot_id: Optional[str] = None

@dataclass
class KKOSMOSBookingResult:
    """Booking sonucu"""
    success: bool
    confirmation_number: Optional[str] = None
    appointment_date: Optional[date] = None
    appointment_time: Optional[str] = None
    center: Optional[str] = None
    verification_method: Optional[str] = None  # "sms" or "voice"
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    total_attempts: int = 0
    duration_seconds: int = 0

class KKOSMOSAdapter:
    """KKOSMOS booking adapter - SMS/Voice verification required"""
    
    # Timeouts
    PAGE_LOAD_TIMEOUT = 45000
    ELEMENT_TIMEOUT = 20000
    SMS_TIMEOUT = 120      # 2 dakika SMS bekleme
    VOICE_TIMEOUT = 180    # 3 dakika telefon bekleme
    
    # Selectors
    SELECTORS = {
        # Login
        "email_input": '#email, input[name="email"]',
        "password_input": '#password, input[name="password"]',
        "login_button": '#loginBtn, button[type="submit"]',
        
        # Service Selection
        "visa_type_select": '#visaType, select[name="visaType"]',
        "center_select": '#center, select[name="center"]',
        
        # Calendar
        "calendar": '#calendar, .appointment-calendar',
        "available_date": '.available, .open',
        "time_slot": '.time-slot, .slot',
        
        # Applicant Form
        "first_name": '#firstName',
        "last_name": '#lastName',
        "birth_date": '#birthDate',
        "passport_number": '#passportNumber',
        "passport_expiry": '#passportExpiry',
        "nationality": '#nationality',
        "phone": '#phone',
        "email_applicant": '#applicantEmail',
        
        # Verification
        "verification_phone": '#verificationPhone',
        "send_sms_btn": '#sendSMS, button:has-text("SMS")',
        "call_me_btn": '#callMe, button:has-text("Call")',
        "verification_code": '#verificationCode, input[name="code"]',
        "verify_btn": '#verifyBtn, button:has-text("Verify")',
        
        # CAPTCHA
        "recaptcha": '.g-recaptcha',
        
        # Confirmation
        "terms_checkbox": '#terms, input[name="terms"]',
        "submit_button": '#submitBtn',
        
        # Result
        "success_message": '.success, .confirmation',
        "error_message": '.error, .alert-danger',
        "confirmation_number": '#confirmationNumber',
    }
    
    def __init__(
        self,
        stealth_engine,
        proxy_manager,
        captcha_solver,
        account_pool,
        phone_pool: PhonePoolManager,
    ):
        self.stealth = stealth_engine
        self.proxy = proxy_manager
        self.captcha = captcha_solver
        self.accounts = account_pool
        self.phones = phone_pool
        
        self.session = None
        self.account = None
        self.proxy_config = None
        self.current_phone = None
        self.state = KKOSMOSFlowState.INITIAL
        self.attempts = 0
        self.start_time = None
    
    async def book_appointment(
        self,
        visa_type: str,
        center: str,
        applicant_data: Dict[str, Any],
        preferred_dates: Dict[str, str],
        verification_preference: str = "sms",  # "sms" or "voice"
        max_attempts: int = 2,  # Lower due to high risk
    ) -> KKOSMOSBookingResult:
        """Ana booking flow - SMS/Voice verification dahil"""
        
        self.start_time = datetime.utcnow()
        self.attempts = 0
        
        for attempt in range(max_attempts):
            self.attempts = attempt + 1
            
            try:
                # Acquire resources
                await self._acquire_resources()
                
                # Login
                login_success = await self._login()
                if not login_success:
                    await self._handle_login_failure()
                    continue
                
                # Find slot
                slot = await self._find_slot(
                    visa_type=visa_type,
                    center=center,
                    preferred_dates=preferred_dates,
                )
                
                if not slot:
                    await self._release_resources(success=False)
                    return KKOSMOSBookingResult(
                        success=False,
                        error_code="NO_SLOT",
                        error_message="No available slots found",
                        total_attempts=self.attempts,
                        duration_seconds=self._get_duration(),
                    )
                
                # Fill applicant form
                await self._fill_form(applicant_data)
                
                # Handle CAPTCHA
                await self._handle_captcha()
                
                # CRITICAL: Phone verification
                verification_result = await self._handle_verification(
                    preference=verification_preference,
                )
                
                if not verification_result["success"]:
                    # Verification failed, retry with different number
                    continue
                
                # Submit booking
                booking_success = await self._submit_booking()
                
                if booking_success:
                    confirmation = await self._get_confirmation()
                    
                    await self._release_resources(success=True)
                    
                    return KKOSMOSBookingResult(
                        success=True,
                        confirmation_number=confirmation.get("number"),
                        appointment_date=slot.date,
                        appointment_time=slot.time,
                        center=slot.center,
                        verification_method=verification_result.get("method"),
                        total_attempts=self.attempts,
                        duration_seconds=self._get_duration(),
                    )
                
            except Exception as e:
                await self._handle_error(e)
        
        await self._release_resources(success=False)
        
        return KKOSMOSBookingResult(
            success=False,
            error_code="MAX_ATTEMPTS",
            error_message=f"Failed after {self.attempts} attempts",
            total_attempts=self.attempts,
            duration_seconds=self._get_duration(),
        )
    
    async def _acquire_resources(self):
        """Resources acquire (phone dahil)"""
        
        # Get account
        self.account = await self.accounts.acquire(
            system="kkosmos",
            country="gr",
            session_id=str(uuid.uuid4()),
        )
        
        # Get proxy
        self.proxy_config = await self.proxy.get_proxy(
            target_site="kkosmos",
            country="tr",
            sticky_session=True,
        )
        
        # Get phone number for verification
        self.current_phone = await self.phones.acquire_number(
            country="tr",
            service="kkosmos",
        )
        
        if not self.current_phone:
            raise NoPhoneAvailableError("No phone number available for verification")
        
        # Launch browser
        self.session = await self.stealth.launch(
            proxy=self.proxy_config,
        )
    
    async def _release_resources(self, success: bool):
        """Resources release"""
        
        # Release phone
        if self.current_phone:
            await self.phones.release_number(
                self.current_phone.id,
                success=success,
            )
        
        # Release account
        if self.account:
            await self.accounts.release(
                self.account.id,
                success=success,
            )
        
        # Close browser
        if self.session:
            await self.session.close()
    
    def _get_duration(self) -> int:
        if self.start_time:
            return int((datetime.utcnow() - self.start_time).total_seconds())
        return 0
```

---

## Phone Verification Handler

```python
class KKOSMOSVerificationHandler:
    """KKOSMOS SMS/Voice verification handling"""
    
    async def handle_verification(
        self,
        adapter: KKOSMOSAdapter,
        preference: str = "sms",
    ) -> Dict[str, Any]:
        """Telefon doğrulama işlemi"""
        
        page = adapter.session.page
        phone = adapter.current_phone
        
        # Enter phone number
        adapter.state = KKOSMOSFlowState.REQUESTING_VERIFICATION
        
        phone_input = await page.query_selector(
            KKOSMOSAdapter.SELECTORS["verification_phone"]
        )
        
        if phone_input:
            await phone_input.fill(phone.number)
            await asyncio.sleep(0.5)
        
        # Try SMS first (or voice if preferred)
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
    ) -> Dict[str, Any]:
        """SMS doğrulama dene"""
        
        page = adapter.session.page
        phone = adapter.current_phone
        
        # Click send SMS
        sms_btn = await page.query_selector(KKOSMOSAdapter.SELECTORS["send_sms_btn"])
        
        if not sms_btn:
            return {"success": False, "error": "SMS button not found"}
        
        await sms_btn.click()
        
        adapter.state = KKOSMOSFlowState.WAITING_SMS
        
        # Wait for SMS
        provider = adapter.phones.providers.get(phone.provider)
        
        if not provider:
            return {"success": False, "error": "SMS provider not available"}
        
        code = await provider.get_sms(
            order_id=phone.id,
            timeout=KKOSMOSAdapter.SMS_TIMEOUT,
        )
        
        if not code:
            return {"success": False, "error": "SMS not received", "method": "sms"}
        
        # Enter code
        adapter.state = KKOSMOSFlowState.ENTERING_CODE
        
        code_input = await page.query_selector(
            KKOSMOSAdapter.SELECTORS["verification_code"]
        )
        
        if code_input:
            await code_input.fill(code)
            await asyncio.sleep(0.5)
            
            # Click verify
            verify_btn = await page.query_selector(
                KKOSMOSAdapter.SELECTORS["verify_btn"]
            )
            
            if verify_btn:
                await verify_btn.click()
                await asyncio.sleep(2)
                
                # Check if verification succeeded
                error = await page.query_selector(
                    KKOSMOSAdapter.SELECTORS["error_message"]
                )
                
                if not error:
                    return {"success": True, "method": "sms", "code": code}
        
        return {"success": False, "error": "Code verification failed", "method": "sms"}
    
    async def _try_voice_verification(
        self,
        adapter: KKOSMOSAdapter,
    ) -> Dict[str, Any]:
        """Voice call doğrulama dene"""
        
        page = adapter.session.page
        phone = adapter.current_phone
        
        # Click call me
        call_btn = await page.query_selector(KKOSMOSAdapter.SELECTORS["call_me_btn"])
        
        if not call_btn:
            return {"success": False, "error": "Call button not found"}
        
        await call_btn.click()
        
        adapter.state = KKOSMOSFlowState.WAITING_VOICE
        
        # Voice call handling is more complex
        # Options:
        # 1. Use voicemail-to-text service
        # 2. Use IVR DTMF capture
        # 3. Human-in-the-loop (alert operator)
        
        # For automated: try voicemail transcription
        code = await self._wait_for_voice_code(adapter)
        
        if not code:
            return {"success": False, "error": "Voice code not received", "method": "voice"}
        
        # Enter code
        adapter.state = KKOSMOSFlowState.ENTERING_CODE
        
        code_input = await page.query_selector(
            KKOSMOSAdapter.SELECTORS["verification_code"]
        )
        
        if code_input:
            await code_input.fill(code)
            await asyncio.sleep(0.5)
            
            verify_btn = await page.query_selector(
                KKOSMOSAdapter.SELECTORS["verify_btn"]
            )
            
            if verify_btn:
                await verify_btn.click()
                await asyncio.sleep(2)
                
                error = await page.query_selector(
                    KKOSMOSAdapter.SELECTORS["error_message"]
                )
                
                if not error:
                    return {"success": True, "method": "voice", "code": code}
        
        return {"success": False, "error": "Voice code verification failed", "method": "voice"}
    
    async def _wait_for_voice_code(
        self,
        adapter: KKOSMOSAdapter,
    ) -> Optional[str]:
        """Voice call'dan kod bekle"""
        
        # Bu kısım provider'a bağlı
        # Bazı provider'lar voicemail transcription sunuyor
        
        # Option 1: Provider API (5sim voice)
        phone = adapter.current_phone
        provider = adapter.phones.providers.get(phone.provider)
        
        if hasattr(provider, "get_voice_code"):
            return await provider.get_voice_code(
                order_id=phone.id,
                timeout=KKOSMOSAdapter.VOICE_TIMEOUT,
            )
        
        # Option 2: Human escalation
        # Send alert and wait for manual input
        return await self._human_escalation_voice(adapter)
    
    async def _human_escalation_voice(
        self,
        adapter: KKOSMOSAdapter,
    ) -> Optional[str]:
        """Operatör'e yönlendir (voice call için)"""
        
        # Send Telegram alert
        # Wait for response
        # This is a fallback for when automated voice handling isn't available
        
        # For now, return None (fail)
        # In production, implement human-in-the-loop
        return None
```

---

## Form Handler

```python
class KKOSMOSFormHandler:
    """KKOSMOS form doldurma"""
    
    async def fill_form(
        self,
        adapter: KKOSMOSAdapter,
        data: Dict[str, Any],
    ):
        """Başvuru formunu doldur"""
        
        page = adapter.session.page
        adapter.state = KKOSMOSFlowState.FILLING_FORM
        
        field_mapping = {
            "first_name": data.get("first_name"),
            "last_name": data.get("last_name"),
            "birth_date": data.get("birth_date"),
            "passport_number": data.get("passport_number"),
            "passport_expiry": data.get("passport_expiry"),
            "phone": data.get("phone"),
            "email_applicant": data.get("email"),
        }
        
        for field_key, value in field_mapping.items():
            if value:
                selector = KKOSMOSAdapter.SELECTORS.get(field_key)
                if selector:
                    field = await page.query_selector(selector)
                    if field:
                        await field.fill(str(value))
                        await asyncio.sleep(random.uniform(0.2, 0.4))
        
        # Nationality dropdown
        if data.get("nationality"):
            await page.select_option(
                KKOSMOSAdapter.SELECTORS["nationality"],
                value=data["nationality"],
            )
```

---

## Error Handling

```python
class KKOSMOSErrorClassifier:
    """KKOSMOS hata sınıflandırma"""
    
    ERROR_PATTERNS = {
        "verification_failed": [
            r"verification.*failed",
            r"invalid.*code",
            r"kod.*hatalı",
        ],
        "phone_blocked": [
            r"phone.*blocked",
            r"number.*blacklist",
            r"numara.*engel",
        ],
        "rate_limit": [
            r"too.*many.*attempts",
            r"try.*later",
            r"3.*hour",
        ],
        "session_expired": [
            r"session.*expired",
            r"login.*again",
        ],
    }
    
    @staticmethod
    def classify(error_message: str) -> Dict[str, Any]:
        error_lower = error_message.lower()
        
        for error_type, patterns in KKOSMOSErrorClassifier.ERROR_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, error_lower):
                    return {
                        "type": error_type.upper(),
                        "retriable": error_type not in ["phone_blocked"],
                        "cooldown_seconds": 10800 if error_type == "rate_limit" else 60,  # 3 saat
                        "new_phone_required": error_type in ["verification_failed", "phone_blocked"],
                    }
        
        return {"type": "UNKNOWN", "retriable": True, "cooldown_seconds": 60}
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | SMS verification çalışmalı | Integration test |
| AC-002 | Phone pool rotation çalışmalı | Pool test |
| AC-003 | Fallback (SMS → Voice) çalışmalı | Failover test |
| AC-004 | Code extraction doğru olmalı | Unit test |
| AC-005 | Phone number cooldown uygulanmalı | Timing test |
| AC-006 | Login %90+ başarı | Login test |

---

## Risk Uyarıları

**YÜKSEK RİSK SEVİYESİ:**

1. **Telefon numarası tükenmesi**: SMS provider bakiyesi ve stok kontrol
2. **Phone blacklisting**: Aynı numarayı çok kullanma
3. **3 saat ban**: Rate limit aşımında uzun süre bekleme
4. **Voice call complexity**: Otomatik voice handling zor
5. **Maliyet**: Her verification için phone number maliyeti

**Önerilen Strateji:**
- KKOSMOS için manuel override mekanizması
- Human-in-the-loop için Telegram alert
- Düşük öncelik, sadece kritik müşteriler için

---

## Sonraki Adımlar

Grup 3 (Site Adapters) tamamlandı.

Sonraki: **Grup 4 - AI ve Decision Engine** (012, 013, 014)
