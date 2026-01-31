# 008 - VFS Global Adapter Specification

## Amaç

VFS Global sisteminde (147 ülke, Cloudflare Enterprise korumalı) tam otomatik vize randevusu alma. Login, slot check, booking ve payment flow'larını handle eden, %95+ başarı oranlı adapter.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 004-STEALTH-ENGINE | Browser session management |
| 005-PROXY-MANAGER | Residential proxy allocation |
| 006-CAPTCHA-SOLVER | Cloudflare Turnstile + reCAPTCHA |
| 007-ACCOUNT-POOL-MANAGER | VFS account credentials |
| 013-STATE-MACHINE | Booking state transitions |
| 015-PAYMENT-AUTOMATION | Card payment handling |

---

## VFS Global Teknik Profil

| Özellik | Değer |
|---------|-------|
| Anti-Bot | Cloudflare Enterprise + Custom |
| CAPTCHA | Turnstile + reCAPTCHA v2 |
| TLS Fingerprinting | Aktif (JA3/JA4) |
| Browser Fingerprinting | Canvas, WebGL, AudioContext |
| Rate Limiting | IP bazlı, 2 saat ban |
| Session Timeout | 15 dakika inaktivite |
| Multi-Page Challenge | Login sonrası tekrar Cloudflare |

---

## URL Yapısı

```python
class VFSURLBuilder:
    """VFS Global URL yapıcı"""
    
    BASE_URL = "https://visa.vfsglobal.com"
    
    # Ülke kodları (VFS format)
    COUNTRY_CODES = {
        "de": "deu",   # Almanya
        "fr": "fra",   # Fransa
        "nl": "nld",   # Hollanda
        "no": "nor",   # Norveç
        "se": "swe",   # İsveç
        "it": "ita",   # İtalya
    }
    
    # Şehir kodları
    CITY_CODES = {
        "istanbul": "ist",
        "ankara": "ank",
        "izmir": "izm",
        "antalya": "ant",
        "gaziantep": "gaz",
        "istanbul_beyoglu": "istb",
        "istanbul_altunizade": "ista",
    }
    
    @classmethod
    def login_url(cls, source_country: str = "tr", target_country: str = "de") -> str:
        """Login URL"""
        target = cls.COUNTRY_CODES.get(target_country, target_country)
        return f"{cls.BASE_URL}/{source_country}/en/{target}/login"
    
    @classmethod
    def appointment_url(cls, source_country: str = "tr", target_country: str = "de") -> str:
        """Randevu sayfası URL"""
        target = cls.COUNTRY_CODES.get(target_country, target_country)
        return f"{cls.BASE_URL}/{source_country}/en/{target}/book-appointment"
    
    @classmethod
    def calendar_url(cls, source_country: str = "tr", target_country: str = "de") -> str:
        """Takvim sayfası URL"""
        target = cls.COUNTRY_CODES.get(target_country, target_country)
        return f"{cls.BASE_URL}/{source_country}/en/{target}/appointment-date"
    
    @classmethod
    def payment_url(cls, source_country: str = "tr", target_country: str = "de") -> str:
        """Ödeme sayfası URL"""
        target = cls.COUNTRY_CODES.get(target_country, target_country)
        return f"{cls.BASE_URL}/{source_country}/en/{target}/payment"
```

---

## VFS Adapter Ana Sınıf

```python
from dataclasses import dataclass
from typing import Optional, Dict, List, Any
from enum import Enum
from datetime import datetime, date
import asyncio
import re

class VFSFlowState(Enum):
    """VFS booking flow states"""
    INITIAL = "initial"
    NAVIGATING = "navigating"
    CLOUDFLARE_CHALLENGE = "cloudflare_challenge"
    LOGIN_PAGE = "login_page"
    AUTHENTICATING = "authenticating"
    AUTHENTICATED = "authenticated"
    SELECTING_CATEGORY = "selecting_category"
    SELECTING_CENTER = "selecting_center"
    CHECKING_SLOTS = "checking_slots"
    SLOT_FOUND = "slot_found"
    SELECTING_SLOT = "selecting_slot"
    FILLING_APPLICANT = "filling_applicant"
    CONFIRMING = "confirming"
    PAYMENT_PAGE = "payment_page"
    PROCESSING_PAYMENT = "processing_payment"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class VFSSlot:
    """Randevu slot bilgisi"""
    date: date
    time: str
    location: str
    category: str
    is_premium: bool = False
    slot_id: Optional[str] = None

@dataclass
class VFSBookingResult:
    """Booking sonucu"""
    success: bool
    confirmation_number: Optional[str] = None
    appointment_date: Optional[date] = None
    appointment_time: Optional[str] = None
    appointment_location: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    screenshot_path: Optional[str] = None
    total_attempts: int = 0
    duration_seconds: int = 0

class VFSAdapter:
    """VFS Global booking adapter"""
    
    # Timeouts
    PAGE_LOAD_TIMEOUT = 60000  # 60 saniye
    ELEMENT_TIMEOUT = 30000   # 30 saniye
    CLOUDFLARE_TIMEOUT = 45   # 45 saniye
    
    # Selectors
    SELECTORS = {
        # Login
        "email_input": 'input[name="email"], input[type="email"], #email',
        "password_input": 'input[name="password"], input[type="password"], #password',
        "login_button": 'button[type="submit"], input[type="submit"], .login-btn',
        
        # Category Selection
        "category_dropdown": 'select[name="category"], #category, .category-select',
        "visa_type_dropdown": 'select[name="visaType"], #visaType',
        
        # Center Selection
        "center_dropdown": 'select[name="center"], #center, .center-select',
        "city_dropdown": 'select[name="city"], #city',
        
        # Calendar
        "calendar_container": '.calendar, .datepicker, #calendar',
        "available_date": '.available, .open, :not(.disabled):not(.unavailable)',
        "date_cell": 'td[data-date], .day:not(.disabled)',
        "next_month_btn": '.next, .calendar-next, [aria-label="Next month"]',
        
        # Time Slots
        "time_slot": '.time-slot, .slot, input[name="time"]',
        "time_slot_available": '.time-slot:not(.disabled), .slot.available',
        
        # Applicant Form
        "first_name": 'input[name="firstName"], #firstName',
        "last_name": 'input[name="lastName"], #lastName',
        "passport_number": 'input[name="passportNumber"], #passportNumber',
        "birth_date": 'input[name="birthDate"], #birthDate',
        "phone": 'input[name="phone"], #phone',
        "email_applicant": 'input[name="applicantEmail"], #applicantEmail',
        
        # Confirmation
        "confirm_checkbox": 'input[type="checkbox"][name*="confirm"], .terms-checkbox',
        "submit_button": 'button[type="submit"], .submit-btn, #submitBtn',
        
        # Payment
        "payment_frame": 'iframe[src*="payment"], #paymentFrame',
        "card_number": 'input[name="cardNumber"], #cardNumber',
        "expiry": 'input[name="expiry"], #expiry',
        "cvv": 'input[name="cvv"], #cvv',
        "pay_button": 'button[type="submit"], .pay-btn, #payBtn',
        
        # Messages
        "error_message": '.error, .alert-danger, .error-message',
        "success_message": '.success, .alert-success, .confirmation',
        "confirmation_number": '.confirmation-number, #confirmationNumber, .booking-ref',
    }
    
    # Error patterns
    ERROR_PATTERNS = {
        "rate_limit": [
            r"maximum.*attempts",
            r"too many.*requests",
            r"rate.*limit",
            r"try.*later",
        ],
        "session_expired": [
            r"session.*expired",
            r"please.*login.*again",
            r"401",
        ],
        "slot_taken": [
            r"slot.*no longer.*available",
            r"appointment.*taken",
            r"not.*available",
        ],
        "account_banned": [
            r"account.*suspended",
            r"access.*denied",
            r"blocked",
        ],
    }
    
    def __init__(
        self,
        stealth_engine,
        proxy_manager,
        captcha_solver,
        account_pool,
        payment_processor,
    ):
        self.stealth = stealth_engine
        self.proxy = proxy_manager
        self.captcha = captcha_solver
        self.accounts = account_pool
        self.payment = payment_processor
        
        self.session = None
        self.account = None
        self.proxy_config = None
        self.state = VFSFlowState.INITIAL
        self.attempts = 0
        self.start_time = None
    
    async def book_appointment(
        self,
        target_country: str,
        visa_category: str,
        applicant_data: Dict[str, Any],
        preferred_dates: Dict[str, str],
        payment_card: Dict[str, str],
        max_attempts: int = 3,
    ) -> VFSBookingResult:
        """Ana booking flow"""
        
        self.start_time = datetime.utcnow()
        self.attempts = 0
        
        for attempt in range(max_attempts):
            self.attempts = attempt + 1
            
            try:
                # Resource acquisition
                await self._acquire_resources(target_country)
                
                # Login
                login_success = await self._login()
                if not login_success:
                    await self._handle_login_failure()
                    continue
                
                # Find and select slot
                slot = await self._find_slot(
                    visa_category=visa_category,
                    preferred_dates=preferred_dates,
                )
                
                if not slot:
                    await self._release_resources(success=False)
                    return VFSBookingResult(
                        success=False,
                        error_code="NO_SLOT",
                        error_message="No available slots found",
                        total_attempts=self.attempts,
                        duration_seconds=self._get_duration(),
                    )
                
                # Select slot
                slot_selected = await self._select_slot(slot)
                if not slot_selected:
                    continue
                
                # Fill applicant details
                await self._fill_applicant_form(applicant_data)
                
                # Confirm and proceed to payment
                await self._confirm_booking()
                
                # Process payment
                payment_result = await self._process_payment(payment_card)
                
                if payment_result.success:
                    # Get confirmation
                    confirmation = await self._get_confirmation()
                    
                    await self._release_resources(success=True)
                    
                    return VFSBookingResult(
                        success=True,
                        confirmation_number=confirmation.get("number"),
                        appointment_date=slot.date,
                        appointment_time=slot.time,
                        appointment_location=slot.location,
                        screenshot_path=confirmation.get("screenshot"),
                        total_attempts=self.attempts,
                        duration_seconds=self._get_duration(),
                    )
                else:
                    # Payment failed
                    continue
                    
            except Exception as e:
                await self._handle_error(e)
                
                if self._is_fatal_error(e):
                    break
        
        # All attempts failed
        await self._release_resources(success=False)
        
        return VFSBookingResult(
            success=False,
            error_code="MAX_ATTEMPTS",
            error_message=f"Failed after {self.attempts} attempts",
            total_attempts=self.attempts,
            duration_seconds=self._get_duration(),
        )
```

---

## Login Flow

```python
class VFSLoginHandler:
    """VFS login işlemleri"""
    
    async def login(self, adapter: VFSAdapter) -> bool:
        """Login flow"""
        
        page = adapter.session.page
        
        # Navigate to login page
        adapter.state = VFSFlowState.NAVIGATING
        
        login_url = VFSURLBuilder.login_url(
            source_country="tr",
            target_country=adapter.account.country,
        )
        
        await page.goto(login_url, wait_until="networkidle")
        
        # Handle Cloudflare challenge
        if await self._is_cloudflare_challenge(page):
            adapter.state = VFSFlowState.CLOUDFLARE_CHALLENGE
            
            challenge_passed = await adapter.captcha.handle_captcha(
                page=page,
                proxy=adapter.proxy_config,
                max_attempts=3,
            )
            
            if not challenge_passed:
                raise CloudflareChallengeError("Failed to pass Cloudflare challenge")
            
            # Wait for redirect after challenge
            await page.wait_for_load_state("networkidle")
        
        # Verify on login page
        adapter.state = VFSFlowState.LOGIN_PAGE
        
        email_input = await page.wait_for_selector(
            VFSAdapter.SELECTORS["email_input"],
            timeout=VFSAdapter.ELEMENT_TIMEOUT,
        )
        
        if not email_input:
            raise LoginPageError("Email input not found")
        
        # Fill credentials with human-like behavior
        adapter.state = VFSFlowState.AUTHENTICATING
        
        await self._fill_credentials(page, adapter.account)
        
        # Handle CAPTCHA if present
        captcha_detected = await adapter.captcha.detector.detect(page)
        if captcha_detected:
            await adapter.captcha.handle_captcha(
                page=page,
                proxy=adapter.proxy_config,
            )
        
        # Submit login
        await page.click(VFSAdapter.SELECTORS["login_button"])
        
        # Wait for navigation
        await page.wait_for_load_state("networkidle")
        
        # Check for second Cloudflare challenge (multi-page)
        if await self._is_cloudflare_challenge(page):
            challenge_passed = await adapter.captcha.handle_captcha(
                page=page,
                proxy=adapter.proxy_config,
            )
            
            if not challenge_passed:
                raise CloudflareChallengeError("Failed second Cloudflare challenge")
            
            await page.wait_for_load_state("networkidle")
        
        # Verify login success
        if await self._is_logged_in(page):
            adapter.state = VFSFlowState.AUTHENTICATED
            return True
        
        # Check for error messages
        error = await self._get_login_error(page)
        if error:
            raise LoginError(error)
        
        return False
    
    async def _fill_credentials(self, page, account):
        """Credential'ları human-like doldur"""
        
        from human_behavior import HumanTypingSimulator
        typing = HumanTypingSimulator(page)
        
        # Email
        await page.click(VFSAdapter.SELECTORS["email_input"])
        await asyncio.sleep(random.uniform(0.2, 0.5))
        await typing.type_text(VFSAdapter.SELECTORS["email_input"], account.email)
        
        # Tab to password (human behavior)
        await page.keyboard.press("Tab")
        await asyncio.sleep(random.uniform(0.3, 0.7))
        
        # Password
        await typing.type_text(VFSAdapter.SELECTORS["password_input"], account.password)
        
        # Small pause before submit
        await asyncio.sleep(random.uniform(0.5, 1.0))
    
    async def _is_cloudflare_challenge(self, page) -> bool:
        """Cloudflare challenge sayfası mı?"""
        
        content = await page.content()
        url = page.url
        
        indicators = [
            "challenge-running",
            "cf-browser-verification",
            "cf-turnstile",
            "Just a moment",
            "Checking your browser",
            "__cf_chl",
        ]
        
        return any(ind in content or ind in url for ind in indicators)
    
    async def _is_logged_in(self, page) -> bool:
        """Login başarılı mı?"""
        
        # Check for dashboard elements
        dashboard_indicators = [
            ".dashboard",
            ".user-menu",
            ".logout",
            "[href*='logout']",
            ".welcome-message",
        ]
        
        for selector in dashboard_indicators:
            element = await page.query_selector(selector)
            if element:
                return True
        
        # Check URL
        if "/dashboard" in page.url or "/appointment" in page.url:
            return True
        
        return False
    
    async def _get_login_error(self, page) -> Optional[str]:
        """Login hata mesajını al"""
        
        error_element = await page.query_selector(VFSAdapter.SELECTORS["error_message"])
        
        if error_element:
            return await error_element.inner_text()
        
        return None
```

---

## Slot Check Flow

```python
class VFSSlotChecker:
    """VFS slot arama ve kontrol"""
    
    async def find_slot(
        self,
        adapter: VFSAdapter,
        visa_category: str,
        preferred_dates: Dict[str, str],
        preferred_cities: List[str] = None,
        exclude_weekends: bool = False,
        exclude_premium: bool = False,
    ) -> Optional[VFSSlot]:
        """Uygun slot bul"""
        
        page = adapter.session.page
        
        # Navigate to appointment page
        adapter.state = VFSFlowState.SELECTING_CATEGORY
        
        appointment_url = VFSURLBuilder.appointment_url(
            source_country="tr",
            target_country=adapter.account.country,
        )
        
        await page.goto(appointment_url, wait_until="networkidle")
        
        # Select visa category
        await self._select_category(page, visa_category)
        
        # Select center/city
        adapter.state = VFSFlowState.SELECTING_CENTER
        
        if preferred_cities:
            for city in preferred_cities:
                center_selected = await self._select_center(page, city)
                if center_selected:
                    break
        else:
            await self._select_center(page, "istanbul")
        
        # Navigate to calendar
        adapter.state = VFSFlowState.CHECKING_SLOTS
        
        await page.click('button:has-text("Continue"), .continue-btn')
        await page.wait_for_load_state("networkidle")
        
        # Check for available dates
        slot = await self._scan_calendar(
            page=page,
            date_from=preferred_dates.get("from"),
            date_to=preferred_dates.get("to"),
            exclude_weekends=exclude_weekends,
            exclude_premium=exclude_premium,
        )
        
        if slot:
            adapter.state = VFSFlowState.SLOT_FOUND
        
        return slot
    
    async def _select_category(self, page, category: str):
        """Vize kategorisi seç"""
        
        # Wait for dropdown
        dropdown = await page.wait_for_selector(
            VFSAdapter.SELECTORS["category_dropdown"],
            timeout=VFSAdapter.ELEMENT_TIMEOUT,
        )
        
        # Click to open
        await dropdown.click()
        await asyncio.sleep(0.5)
        
        # Find option
        option = await page.query_selector(f'option[value*="{category}"], option:has-text("{category}")')
        
        if option:
            value = await option.get_attribute("value")
            await page.select_option(VFSAdapter.SELECTORS["category_dropdown"], value)
        else:
            # Try text-based selection
            await page.select_option(VFSAdapter.SELECTORS["category_dropdown"], label=category)
        
        await asyncio.sleep(1)  # Wait for dependent dropdowns
    
    async def _select_center(self, page, city: str) -> bool:
        """Merkez/şehir seç"""
        
        city_code = VFSURLBuilder.CITY_CODES.get(city.lower(), city)
        
        try:
            dropdown = await page.wait_for_selector(
                VFSAdapter.SELECTORS["center_dropdown"],
                timeout=5000,
            )
            
            if dropdown:
                await dropdown.click()
                await asyncio.sleep(0.5)
                
                # Try to select by value or text
                await page.select_option(
                    VFSAdapter.SELECTORS["center_dropdown"],
                    value=city_code,
                )
                
                return True
        except:
            pass
        
        return False
    
    async def _scan_calendar(
        self,
        page,
        date_from: str,
        date_to: str,
        exclude_weekends: bool,
        exclude_premium: bool,
        max_months: int = 6,
    ) -> Optional[VFSSlot]:
        """Takvimi tara"""
        
        from datetime import datetime, timedelta
        
        target_from = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else date.today()
        target_to = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else (date.today() + timedelta(days=180))
        
        for month_offset in range(max_months):
            # Get available dates in current view
            available_dates = await self._get_available_dates(page)
            
            for available_date in available_dates:
                # Date range check
                if available_date < target_from or available_date > target_to:
                    continue
                
                # Weekend check
                if exclude_weekends and available_date.weekday() >= 5:
                    continue
                
                # Get time slots for this date
                slots = await self._get_time_slots(page, available_date)
                
                for slot in slots:
                    # Premium check
                    if exclude_premium and slot.is_premium:
                        continue
                    
                    return slot
            
            # Navigate to next month
            next_btn = await page.query_selector(VFSAdapter.SELECTORS["next_month_btn"])
            if next_btn:
                await next_btn.click()
                await asyncio.sleep(1)
            else:
                break
        
        return None
    
    async def _get_available_dates(self, page) -> List[date]:
        """Mevcut ayda müsait tarihleri al"""
        
        available = []
        
        # Find all available date cells
        cells = await page.query_selector_all(
            f'{VFSAdapter.SELECTORS["date_cell"]}{VFSAdapter.SELECTORS["available_date"]}'
        )
        
        for cell in cells:
            date_str = await cell.get_attribute("data-date")
            if date_str:
                try:
                    available.append(datetime.strptime(date_str, "%Y-%m-%d").date())
                except:
                    pass
        
        return available
    
    async def _get_time_slots(self, page, target_date: date) -> List[VFSSlot]:
        """Belirli tarih için saat slotlarını al"""
        
        slots = []
        
        # Click on the date
        date_cell = await page.query_selector(f'[data-date="{target_date.isoformat()}"]')
        if date_cell:
            await date_cell.click()
            await asyncio.sleep(1)
        
        # Get time slots
        time_elements = await page.query_selector_all(
            VFSAdapter.SELECTORS["time_slot_available"]
        )
        
        for elem in time_elements:
            time_text = await elem.inner_text()
            is_premium = "premium" in (await elem.get_attribute("class") or "").lower()
            slot_id = await elem.get_attribute("data-slot-id") or await elem.get_attribute("value")
            
            slots.append(VFSSlot(
                date=target_date,
                time=time_text.strip(),
                location="",  # Will be filled from context
                category="",
                is_premium=is_premium,
                slot_id=slot_id,
            ))
        
        return slots
```

---

## Booking Form Handler

```python
class VFSBookingFormHandler:
    """VFS başvuru formu doldurma"""
    
    async def fill_applicant_form(
        self,
        adapter: VFSAdapter,
        applicant_data: Dict[str, Any],
    ):
        """Başvuru formunu doldur"""
        
        page = adapter.session.page
        adapter.state = VFSFlowState.FILLING_APPLICANT
        
        from human_behavior import HumanTypingSimulator
        typing = HumanTypingSimulator(page)
        
        # First Name
        if await self._field_exists(page, VFSAdapter.SELECTORS["first_name"]):
            await typing.type_text(
                VFSAdapter.SELECTORS["first_name"],
                applicant_data["first_name"],
            )
        
        await asyncio.sleep(random.uniform(0.3, 0.7))
        
        # Last Name
        if await self._field_exists(page, VFSAdapter.SELECTORS["last_name"]):
            await typing.type_text(
                VFSAdapter.SELECTORS["last_name"],
                applicant_data["last_name"],
            )
        
        await asyncio.sleep(random.uniform(0.3, 0.7))
        
        # Passport Number
        if await self._field_exists(page, VFSAdapter.SELECTORS["passport_number"]):
            await typing.type_text(
                VFSAdapter.SELECTORS["passport_number"],
                applicant_data["passport_number"],
            )
        
        await asyncio.sleep(random.uniform(0.3, 0.7))
        
        # Birth Date - DatePicker handling
        await self._fill_date_field(
            page,
            VFSAdapter.SELECTORS["birth_date"],
            applicant_data["birth_date"],
        )
        
        await asyncio.sleep(random.uniform(0.3, 0.7))
        
        # Phone
        if await self._field_exists(page, VFSAdapter.SELECTORS["phone"]):
            await typing.type_text(
                VFSAdapter.SELECTORS["phone"],
                applicant_data["phone"],
            )
        
        await asyncio.sleep(random.uniform(0.3, 0.7))
        
        # Email (if different from account email)
        if applicant_data.get("email"):
            if await self._field_exists(page, VFSAdapter.SELECTORS["email_applicant"]):
                await typing.type_text(
                    VFSAdapter.SELECTORS["email_applicant"],
                    applicant_data["email"],
                )
    
    async def _field_exists(self, page, selector: str) -> bool:
        """Field var mı?"""
        try:
            element = await page.query_selector(selector)
            return element is not None
        except:
            return False
    
    async def _fill_date_field(self, page, selector: str, date_value: str):
        """DatePicker field doldur"""
        
        # Try direct input first
        field = await page.query_selector(selector)
        
        if field:
            # Check if it's a readonly datepicker
            readonly = await field.get_attribute("readonly")
            
            if readonly:
                # Click to open datepicker
                await field.click()
                await asyncio.sleep(0.5)
                
                # Parse date and navigate datepicker
                target_date = datetime.strptime(date_value, "%Y-%m-%d")
                await self._navigate_datepicker(page, target_date)
            else:
                # Direct input
                await field.fill("")  # Clear
                await field.type(date_value, delay=100)
    
    async def _navigate_datepicker(self, page, target_date):
        """DatePicker'da tarih seç"""
        
        # Implementation depends on datepicker library
        # Common approach: navigate months then click day
        
        day_selector = f'[data-date="{target_date.strftime("%Y-%m-%d")}"], ' \
                       f'.day:has-text("{target_date.day}")'
        
        day_cell = await page.query_selector(day_selector)
        if day_cell:
            await day_cell.click()
    
    async def confirm_booking(self, adapter: VFSAdapter):
        """Booking'i onayla"""
        
        page = adapter.session.page
        adapter.state = VFSFlowState.CONFIRMING
        
        # Check terms checkbox
        checkbox = await page.query_selector(VFSAdapter.SELECTORS["confirm_checkbox"])
        if checkbox:
            is_checked = await checkbox.is_checked()
            if not is_checked:
                await checkbox.click()
        
        await asyncio.sleep(random.uniform(0.5, 1.0))
        
        # Submit
        await page.click(VFSAdapter.SELECTORS["submit_button"])
        
        # Wait for navigation to payment
        await page.wait_for_load_state("networkidle")
```

---

## Payment Handler

```python
class VFSPaymentHandler:
    """VFS ödeme işlemleri"""
    
    async def process_payment(
        self,
        adapter: VFSAdapter,
        card_data: Dict[str, str],
    ) -> Dict[str, Any]:
        """Ödeme işle"""
        
        page = adapter.session.page
        adapter.state = VFSFlowState.PAYMENT_PAGE
        
        # Check for payment iframe
        payment_frame = await self._get_payment_frame(page)
        
        if payment_frame:
            # Payment is in iframe
            await self._fill_payment_iframe(payment_frame, card_data)
        else:
            # Payment on same page
            await self._fill_payment_form(page, card_data)
        
        adapter.state = VFSFlowState.PROCESSING_PAYMENT
        
        # Submit payment
        target = payment_frame or page
        await target.click(VFSAdapter.SELECTORS["pay_button"])
        
        # Wait for processing (3DS might appear)
        await self._handle_3ds(page)
        
        # Check result
        return await self._check_payment_result(page)
    
    async def _get_payment_frame(self, page):
        """Payment iframe'i al"""
        
        try:
            frame_element = await page.wait_for_selector(
                VFSAdapter.SELECTORS["payment_frame"],
                timeout=5000,
            )
            
            if frame_element:
                return await frame_element.content_frame()
        except:
            pass
        
        return None
    
    async def _fill_payment_form(self, page, card_data: Dict[str, str]):
        """Ödeme formunu doldur"""
        
        from human_behavior import HumanTypingSimulator
        typing = HumanTypingSimulator(page)
        
        # Card Number
        await typing.type_text(
            VFSAdapter.SELECTORS["card_number"],
            card_data["card_number"],
        )
        
        await asyncio.sleep(random.uniform(0.3, 0.5))
        
        # Expiry (MM/YY format)
        expiry = f"{card_data['expiry_month']}/{card_data['expiry_year']}"
        await typing.type_text(VFSAdapter.SELECTORS["expiry"], expiry)
        
        await asyncio.sleep(random.uniform(0.3, 0.5))
        
        # CVV
        await typing.type_text(VFSAdapter.SELECTORS["cvv"], card_data["cvv"])
        
        await asyncio.sleep(random.uniform(0.3, 0.5))
        
        # Cardholder name (if required)
        cardholder_selector = 'input[name*="holder"], input[name*="name"]'
        if await page.query_selector(cardholder_selector):
            await typing.type_text(cardholder_selector, card_data["cardholder_name"])
    
    async def _fill_payment_iframe(self, frame, card_data: Dict[str, str]):
        """Payment iframe içini doldur"""
        # Same as _fill_payment_form but on frame context
        await self._fill_payment_form(frame, card_data)
    
    async def _handle_3ds(self, page):
        """3D Secure handling"""
        
        # Wait for potential 3DS redirect
        await asyncio.sleep(3)
        
        # Check for 3DS frame/popup
        frames = page.frames
        
        for frame in frames:
            if "3ds" in frame.url.lower() or "secure" in frame.url.lower():
                # 3DS detected - wait for completion or timeout
                await self._wait_for_3ds_completion(page, timeout=120)
                break
    
    async def _wait_for_3ds_completion(self, page, timeout: int):
        """3DS tamamlanmasını bekle"""
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            # Check if we're back on VFS
            if "vfsglobal" in page.url:
                return
            
            # Check for success indicators
            content = await page.content()
            if "success" in content.lower() or "confirmed" in content.lower():
                return
            
            await asyncio.sleep(2)
        
        raise PaymentTimeoutError("3DS verification timeout")
    
    async def _check_payment_result(self, page) -> Dict[str, Any]:
        """Ödeme sonucunu kontrol et"""
        
        await page.wait_for_load_state("networkidle")
        
        # Check for success
        success_element = await page.query_selector(VFSAdapter.SELECTORS["success_message"])
        
        if success_element:
            return {"success": True}
        
        # Check for error
        error_element = await page.query_selector(VFSAdapter.SELECTORS["error_message"])
        
        if error_element:
            error_text = await error_element.inner_text()
            return {"success": False, "error": error_text}
        
        # Ambiguous - check URL and content
        if "confirmation" in page.url or "success" in page.url:
            return {"success": True}
        
        return {"success": False, "error": "Unknown payment result"}
```

---

## Error Classification

```python
class VFSErrorClassifier:
    """VFS hata sınıflandırma"""
    
    @staticmethod
    def classify(error_message: str) -> Dict[str, Any]:
        """Hatayı sınıflandır"""
        
        error_lower = error_message.lower()
        
        # Rate limit / ban
        for pattern in VFSAdapter.ERROR_PATTERNS["rate_limit"]:
            if re.search(pattern, error_lower):
                return {
                    "type": "RATE_LIMIT",
                    "retriable": True,
                    "cooldown_seconds": 7200,  # 2 saat
                    "action": "account_cooldown",
                }
        
        # Session expired
        for pattern in VFSAdapter.ERROR_PATTERNS["session_expired"]:
            if re.search(pattern, error_lower):
                return {
                    "type": "SESSION_EXPIRED",
                    "retriable": True,
                    "cooldown_seconds": 0,
                    "action": "re_login",
                }
        
        # Slot taken
        for pattern in VFSAdapter.ERROR_PATTERNS["slot_taken"]:
            if re.search(pattern, error_lower):
                return {
                    "type": "SLOT_TAKEN",
                    "retriable": True,
                    "cooldown_seconds": 0,
                    "action": "find_new_slot",
                }
        
        # Account banned
        for pattern in VFSAdapter.ERROR_PATTERNS["account_banned"]:
            if re.search(pattern, error_lower):
                return {
                    "type": "ACCOUNT_BANNED",
                    "retriable": False,
                    "cooldown_seconds": 86400,  # 24 saat
                    "action": "account_ban",
                }
        
        # Default: unknown error
        return {
            "type": "UNKNOWN",
            "retriable": True,
            "cooldown_seconds": 300,  # 5 dakika
            "action": "retry",
        }
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | Cloudflare challenge %90+ bypass | 100 attempt test |
| AC-002 | Login flow %95+ başarı | Integration test |
| AC-003 | Slot bulunduğunda booking %95+ başarı | E2E test |
| AC-004 | Payment flow çalışmalı (3DS dahil) | Payment test |
| AC-005 | Error classification doğru çalışmalı | Unit test |
| AC-006 | Human behavior simulation tespit edilmemeli | Stealth test |
| AC-007 | Multi-page Cloudflare handle edilmeli | Flow test |

---

## Edge Cases

### Slot Kayboldu (Booking Sırasında)

```
Detection: Form submit sonrası "slot not available" hatası
Action:
  1. slot_lost_counter++
  2. Return to calendar
  3. Find alternative slot (same date different time, or next day)
  4. Max 3 retry, sonra fail
```

### 401101 Session Hatası

```
Detection: Herhangi bir sayfada 401101 kodu
Action:
  1. 5-10 dakika bekle
  2. Session'ı yenile (re-login)
  3. Kaldığı yerden devam et
```

### DatePicker Disabled Hack

```
Detection: Kırmızı (disabled) tarihler
Action:
  1. Inspect element ile disabled → enabled
  2. Tarih seçilebilir hale gelir
  3. Booking proceed (VFS backend check yok)
Note: Risky, sadece edge case olarak kullan
```

---

## Güvenlik Notları

1. **Credential Memory:** Password memory-only, form doldurduktan sonra clear
2. **Screenshot Policy:** Sadece confirmation ekranı, PII mask'lı
3. **Card Data:** Encrypted transit, memory-only decrypt
4. **Session Isolation:** Her booking ayrı browser context
5. **Audit Trail:** İşlem steps logged (credential olmadan)

---

## Sonraki Adımlar

1. **009-IDATA-ADAPTER.md** - iDATA adapter
2. VFS ülke-specific test suite
3. Cloudflare bypass tuning
