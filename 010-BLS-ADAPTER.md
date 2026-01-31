# 010 - BLS Spain Adapter Specification

## Amaç

BLS Spain sisteminde (İspanya Schengen vizeleri) keyboard-only form girişi ile tam otomatik vize randevusu alma. Paste engeli nedeniyle character-by-character typing zorunlu. reCAPTCHA korumalı form submission.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 004-STEALTH-ENGINE | Browser session + typing simulation |
| 005-PROXY-MANAGER | Residential proxy |
| 006-CAPTCHA-SOLVER | reCAPTCHA v2 |
| 007-ACCOUNT-POOL-MANAGER | BLS account credentials |
| 013-STATE-MACHINE | Booking state transitions |

---

## BLS Spain Teknik Profil

| Özellik | Değer |
|---------|-------|
| Anti-Bot | Paste engeli + reCAPTCHA |
| CAPTCHA | reCAPTCHA v2 (form submit) |
| TLS Fingerprinting | Minimal |
| Paste Engeli | **AKTİF** - keyboard-only input zorunlu |
| Rate Limiting | Session bazlı, orta düzey |
| Session Timeout | 20 dakika |
| Frontend | Custom JavaScript |

---

## Kritik Kısıtlama: Paste Engeli

BLS Spain tüm input field'larında paste'i engelliyor:

```javascript
// BLS tarafındaki engel
document.querySelectorAll('input').forEach(input => {
    input.addEventListener('paste', e => e.preventDefault());
    input.addEventListener('drop', e => e.preventDefault());
});
```

**Çözüm:** Her karakter için keyboard event simulation

---

## URL Yapısı

```python
class BLSURLBuilder:
    """BLS Spain URL yapıcı"""
    
    BASE_URL = "https://blsspainvisa.com"
    
    # Turkey specific
    TURKEY_BASE = f"{BASE_URL}/turkey"
    
    ENDPOINTS = {
        "login": "/login",
        "register": "/register",
        "appointment": "/book-appointment",
        "calendar": "/appointment-calendar",
        "confirmation": "/confirmation",
    }
    
    # City codes
    CITIES = {
        "istanbul": "IST",
        "ankara": "ANK",
        "izmir": "IZM",
        "antalya": "ANT",
    }
    
    # Visa categories
    VISA_CATEGORIES = {
        "tourist": "TOUR",
        "business": "BUSI",
        "family": "FAMI",
        "student": "STUD",
        "transit": "TRAN",
    }
    
    @classmethod
    def get_login_url(cls) -> str:
        return f"{cls.TURKEY_BASE}{cls.ENDPOINTS['login']}"
    
    @classmethod
    def get_appointment_url(cls) -> str:
        return f"{cls.TURKEY_BASE}{cls.ENDPOINTS['appointment']}"
```

---

## Keyboard-Only Typing Engine

```python
import asyncio
import random
from typing import Dict, Any

class BLSKeyboardTyper:
    """BLS için paste-engelli typing engine"""
    
    # Inter-keystroke interval (IKI)
    IKI_MIN = 0.05   # 50ms minimum
    IKI_MAX = 0.15   # 150ms maximum
    IKI_MEAN = 0.08  # 80ms ortalama
    
    # Special character handling
    SHIFT_CHARS = '~!@#$%^&*()_+{}|:"<>?ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    
    def __init__(self, page):
        self.page = page
    
    async def type_field(
        self,
        selector: str,
        text: str,
        clear_first: bool = True,
    ):
        """Field'a keyboard ile yaz (paste engeli bypass)"""
        
        # Focus field
        await self.page.click(selector)
        await asyncio.sleep(random.uniform(0.1, 0.3))
        
        # Clear if needed
        if clear_first:
            await self._clear_field(selector)
        
        # Type character by character
        for i, char in enumerate(text):
            await self._type_character(char)
            
            # Variable delay between keystrokes
            delay = self._get_keystroke_delay(text, i)
            await asyncio.sleep(delay)
    
    async def _clear_field(self, selector: str):
        """Field'ı temizle"""
        
        # Select all and delete
        await self.page.keyboard.press("Control+a")
        await asyncio.sleep(0.05)
        await self.page.keyboard.press("Delete")
        await asyncio.sleep(0.05)
    
    async def _type_character(self, char: str):
        """Tek karakter yaz"""
        
        if char in self.SHIFT_CHARS:
            # Shift + key
            await self.page.keyboard.down("Shift")
            await self.page.keyboard.press(char.lower() if char.isalpha() else char)
            await self.page.keyboard.up("Shift")
        else:
            # Normal key
            await self.page.keyboard.press(char)
    
    def _get_keystroke_delay(self, text: str, index: int) -> float:
        """Context-aware keystroke delay"""
        
        # Base delay with variation
        delay = random.gauss(self.IKI_MEAN, 0.02)
        delay = max(self.IKI_MIN, min(self.IKI_MAX, delay))
        
        # Slower at word boundaries
        if index > 0 and text[index - 1] == " ":
            delay *= 1.3
        
        # Slower for numbers (looking at keyboard)
        if text[index].isdigit():
            delay *= 1.2
        
        # Occasional longer pause (thinking)
        if random.random() < 0.05:  # 5% chance
            delay += random.uniform(0.2, 0.5)
        
        return delay
    
    async def type_passport_number(self, selector: str, passport: str):
        """Pasaport numarası özel handling"""
        
        # Pasaport numaraları genelde büyük harf + rakam
        # Daha yavaş yazılır (bakarak)
        
        await self.page.click(selector)
        await asyncio.sleep(0.2)
        
        for char in passport.upper():
            await self._type_character(char)
            # Slower for passport (verifying each char)
            await asyncio.sleep(random.uniform(0.1, 0.2))
    
    async def type_phone_number(self, selector: str, phone: str):
        """Telefon numarası özel handling"""
        
        await self.page.click(selector)
        await asyncio.sleep(0.2)
        
        # Remove non-digits for cleaner input
        digits = ''.join(filter(str.isdigit, phone))
        
        for i, digit in enumerate(digits):
            await self.page.keyboard.press(digit)
            
            # Pause after country code and area code
            if i in [2, 5]:  # After +90 and area code
                await asyncio.sleep(random.uniform(0.15, 0.25))
            else:
                await asyncio.sleep(random.uniform(0.05, 0.1))
    
    async def type_date(self, selector: str, date_str: str, format: str = "DD/MM/YYYY"):
        """Tarih girişi - format aware"""
        
        await self.page.click(selector)
        await asyncio.sleep(0.2)
        
        # Clear existing
        await self._clear_field(selector)
        
        # Type with pauses at separators
        for char in date_str:
            await self.page.keyboard.press(char)
            
            if char in ["/", "-", "."]:
                await asyncio.sleep(random.uniform(0.1, 0.2))
            else:
                await asyncio.sleep(random.uniform(0.05, 0.1))
```

---

## BLS Adapter Ana Sınıf

```python
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, List, Any
from datetime import datetime, date
import asyncio

class BLSFlowState(Enum):
    """BLS booking flow states"""
    INITIAL = "initial"
    NAVIGATING = "navigating"
    LOGIN_PAGE = "login_page"
    AUTHENTICATING = "authenticating"
    AUTHENTICATED = "authenticated"
    SELECTING_CATEGORY = "selecting_category"
    SELECTING_CENTER = "selecting_center"
    CHECKING_SLOTS = "checking_slots"
    SLOT_FOUND = "slot_found"
    FILLING_FORM = "filling_form"
    SOLVING_CAPTCHA = "solving_captcha"
    CONFIRMING = "confirming"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class BLSSlot:
    """BLS randevu slot"""
    date: date
    time: str
    center: str
    category: str
    slot_id: Optional[str] = None

@dataclass
class BLSBookingResult:
    """Booking sonucu"""
    success: bool
    confirmation_number: Optional[str] = None
    appointment_date: Optional[date] = None
    appointment_time: Optional[str] = None
    center: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    total_attempts: int = 0
    duration_seconds: int = 0

class BLSAdapter:
    """BLS Spain booking adapter"""
    
    # Timeouts
    PAGE_LOAD_TIMEOUT = 45000
    ELEMENT_TIMEOUT = 20000
    
    # Selectors
    SELECTORS = {
        # Login
        "email_input": 'input[name="email"], input[type="email"], #email',
        "password_input": 'input[name="password"], input[type="password"], #password',
        "login_button": 'button[type="submit"], .login-btn, #loginBtn',
        
        # Category Selection
        "visa_category": 'select[name="category"], #visaCategory',
        "visa_subcategory": 'select[name="subcategory"], #visaSubcategory',
        
        # Center Selection
        "center_select": 'select[name="center"], #centerSelect',
        "city_select": 'select[name="city"], #citySelect',
        
        # Calendar
        "calendar": '.calendar, #appointmentCalendar',
        "available_date": '.available, .open-date',
        "selected_date": '.selected, .active-date',
        "next_month": '.next-month, .calendar-next',
        "prev_month": '.prev-month, .calendar-prev',
        
        # Time Selection
        "time_slots": '.time-slot, .slot-time',
        "time_slot_available": '.time-slot:not(.unavailable)',
        
        # Applicant Form - KEYBOARD ONLY INPUT REQUIRED
        "first_name": '#firstName, input[name="firstName"]',
        "last_name": '#lastName, input[name="lastName"]',
        "birth_date": '#birthDate, input[name="birthDate"]',
        "nationality": '#nationality, select[name="nationality"]',
        "passport_number": '#passportNumber, input[name="passportNumber"]',
        "passport_issue_date": '#passportIssueDate, input[name="passportIssueDate"]',
        "passport_expiry_date": '#passportExpiryDate, input[name="passportExpiryDate"]',
        "phone": '#phone, input[name="phone"]',
        "email_applicant": '#applicantEmail, input[name="applicantEmail"]',
        "address": '#address, textarea[name="address"]',
        
        # CAPTCHA
        "recaptcha": '.g-recaptcha, #recaptcha',
        
        # Confirmation
        "terms_checkbox": '#termsAccepted, input[name="terms"]',
        "submit_button": '#submitBtn, button[type="submit"]',
        
        # Result
        "success_message": '.success, .confirmation-success',
        "error_message": '.error, .alert-error',
        "confirmation_number": '.confirmation-number, #bookingReference',
    }
    
    def __init__(
        self,
        stealth_engine,
        proxy_manager,
        captcha_solver,
        account_pool,
    ):
        self.stealth = stealth_engine
        self.proxy = proxy_manager
        self.captcha = captcha_solver
        self.accounts = account_pool
        
        self.session = None
        self.account = None
        self.proxy_config = None
        self.keyboard_typer = None
        self.state = BLSFlowState.INITIAL
        self.attempts = 0
        self.start_time = None
    
    async def book_appointment(
        self,
        visa_category: str,
        center: str,
        applicant_data: Dict[str, Any],
        preferred_dates: Dict[str, str],
        max_attempts: int = 3,
    ) -> BLSBookingResult:
        """Ana booking flow"""
        
        self.start_time = datetime.utcnow()
        self.attempts = 0
        
        for attempt in range(max_attempts):
            self.attempts = attempt + 1
            
            try:
                # Acquire resources
                await self._acquire_resources()
                
                # Initialize keyboard typer
                self.keyboard_typer = BLSKeyboardTyper(self.session.page)
                
                # Login
                login_success = await self._login()
                if not login_success:
                    await self._handle_login_failure()
                    continue
                
                # Find slot
                slot = await self._find_slot(
                    visa_category=visa_category,
                    center=center,
                    preferred_dates=preferred_dates,
                )
                
                if not slot:
                    await self._release_resources(success=False)
                    return BLSBookingResult(
                        success=False,
                        error_code="NO_SLOT",
                        error_message="No available slots found",
                        total_attempts=self.attempts,
                        duration_seconds=self._get_duration(),
                    )
                
                # Fill form (KEYBOARD ONLY)
                await self._fill_applicant_form(applicant_data)
                
                # Handle CAPTCHA
                await self._handle_captcha()
                
                # Submit
                booking_success = await self._submit_booking()
                
                if booking_success:
                    confirmation = await self._get_confirmation()
                    
                    await self._release_resources(success=True)
                    
                    return BLSBookingResult(
                        success=True,
                        confirmation_number=confirmation.get("number"),
                        appointment_date=slot.date,
                        appointment_time=slot.time,
                        center=slot.center,
                        total_attempts=self.attempts,
                        duration_seconds=self._get_duration(),
                    )
                
            except Exception as e:
                await self._handle_error(e)
        
        await self._release_resources(success=False)
        
        return BLSBookingResult(
            success=False,
            error_code="MAX_ATTEMPTS",
            error_message=f"Failed after {self.attempts} attempts",
            total_attempts=self.attempts,
            duration_seconds=self._get_duration(),
        )
    
    async def _acquire_resources(self):
        """Resources acquire"""
        
        # Get account
        self.account = await self.accounts.acquire(
            system="bls",
            country="es",
            session_id=str(uuid.uuid4()),
        )
        
        # Get proxy
        self.proxy_config = await self.proxy.get_proxy(
            target_site="bls",
            country="tr",
            sticky_session=True,
        )
        
        # Launch browser
        self.session = await self.stealth.launch(
            proxy=self.proxy_config,
        )
    
    async def _release_resources(self, success: bool):
        """Resources release"""
        
        if self.account:
            await self.accounts.release(
                self.account.id,
                success=success,
            )
        
        if self.session:
            await self.session.close()
    
    def _get_duration(self) -> int:
        if self.start_time:
            return int((datetime.utcnow() - self.start_time).total_seconds())
        return 0
```

---

## Form Filling (Keyboard-Only)

```python
class BLSFormHandler:
    """BLS form doldurma - KEYBOARD ONLY"""
    
    async def fill_applicant_form(
        self,
        adapter: BLSAdapter,
        data: Dict[str, Any],
    ):
        """Tüm form alanlarını keyboard ile doldur"""
        
        page = adapter.session.page
        typer = adapter.keyboard_typer
        
        adapter.state = BLSFlowState.FILLING_FORM
        
        # First Name - KEYBOARD ONLY
        if await self._field_exists(page, BLSAdapter.SELECTORS["first_name"]):
            await typer.type_field(
                BLSAdapter.SELECTORS["first_name"],
                data["first_name"],
            )
            await asyncio.sleep(random.uniform(0.3, 0.6))
        
        # Last Name - KEYBOARD ONLY
        if await self._field_exists(page, BLSAdapter.SELECTORS["last_name"]):
            await typer.type_field(
                BLSAdapter.SELECTORS["last_name"],
                data["last_name"],
            )
            await asyncio.sleep(random.uniform(0.3, 0.6))
        
        # Birth Date - KEYBOARD ONLY (DD/MM/YYYY format)
        if await self._field_exists(page, BLSAdapter.SELECTORS["birth_date"]):
            birth_date = self._format_date(data["birth_date"])
            await typer.type_date(
                BLSAdapter.SELECTORS["birth_date"],
                birth_date,
            )
            await asyncio.sleep(random.uniform(0.3, 0.6))
        
        # Nationality - Dropdown (can use select)
        if await self._field_exists(page, BLSAdapter.SELECTORS["nationality"]):
            await page.select_option(
                BLSAdapter.SELECTORS["nationality"],
                value=data.get("nationality", "TR"),
            )
            await asyncio.sleep(random.uniform(0.2, 0.4))
        
        # Passport Number - KEYBOARD ONLY (special handling)
        if await self._field_exists(page, BLSAdapter.SELECTORS["passport_number"]):
            await typer.type_passport_number(
                BLSAdapter.SELECTORS["passport_number"],
                data["passport_number"],
            )
            await asyncio.sleep(random.uniform(0.3, 0.6))
        
        # Passport Issue Date - KEYBOARD ONLY
        if await self._field_exists(page, BLSAdapter.SELECTORS["passport_issue_date"]):
            if data.get("passport_issue_date"):
                issue_date = self._format_date(data["passport_issue_date"])
                await typer.type_date(
                    BLSAdapter.SELECTORS["passport_issue_date"],
                    issue_date,
                )
                await asyncio.sleep(random.uniform(0.3, 0.6))
        
        # Passport Expiry Date - KEYBOARD ONLY
        if await self._field_exists(page, BLSAdapter.SELECTORS["passport_expiry_date"]):
            expiry_date = self._format_date(data["passport_expiry"])
            await typer.type_date(
                BLSAdapter.SELECTORS["passport_expiry_date"],
                expiry_date,
            )
            await asyncio.sleep(random.uniform(0.3, 0.6))
        
        # Phone - KEYBOARD ONLY (special handling)
        if await self._field_exists(page, BLSAdapter.SELECTORS["phone"]):
            await typer.type_phone_number(
                BLSAdapter.SELECTORS["phone"],
                data["phone"],
            )
            await asyncio.sleep(random.uniform(0.3, 0.6))
        
        # Email - KEYBOARD ONLY
        if await self._field_exists(page, BLSAdapter.SELECTORS["email_applicant"]):
            await typer.type_field(
                BLSAdapter.SELECTORS["email_applicant"],
                data.get("email", ""),
            )
            await asyncio.sleep(random.uniform(0.3, 0.6))
        
        # Address - KEYBOARD ONLY (textarea)
        if await self._field_exists(page, BLSAdapter.SELECTORS["address"]):
            if data.get("address"):
                await typer.type_field(
                    BLSAdapter.SELECTORS["address"],
                    data["address"],
                )
                await asyncio.sleep(random.uniform(0.3, 0.6))
    
    async def _field_exists(self, page, selector: str) -> bool:
        """Field mevcut mu?"""
        try:
            element = await page.query_selector(selector)
            return element is not None
        except:
            return False
    
    def _format_date(self, date_value: str) -> str:
        """Tarihi DD/MM/YYYY formatına çevir"""
        
        # Input: YYYY-MM-DD or DD.MM.YYYY or other formats
        try:
            if "-" in date_value:
                # YYYY-MM-DD
                dt = datetime.strptime(date_value, "%Y-%m-%d")
            elif "." in date_value:
                # DD.MM.YYYY
                dt = datetime.strptime(date_value, "%d.%m.%Y")
            else:
                # Assume YYYY-MM-DD
                dt = datetime.strptime(date_value, "%Y-%m-%d")
            
            return dt.strftime("%d/%m/%Y")
        except:
            return date_value  # Return as-is if parsing fails
```

---

## Slot Finder

```python
class BLSSlotFinder:
    """BLS slot arama"""
    
    async def find_slot(
        self,
        adapter: BLSAdapter,
        visa_category: str,
        center: str,
        preferred_dates: Dict[str, str],
    ) -> Optional[BLSSlot]:
        """Uygun slot bul"""
        
        page = adapter.session.page
        
        # Navigate to appointment page
        adapter.state = BLSFlowState.SELECTING_CATEGORY
        
        await page.goto(
            BLSURLBuilder.get_appointment_url(),
            wait_until="networkidle",
        )
        
        # Select visa category
        await self._select_category(page, visa_category)
        
        # Select center
        adapter.state = BLSFlowState.SELECTING_CENTER
        await self._select_center(page, center)
        
        # Check calendar
        adapter.state = BLSFlowState.CHECKING_SLOTS
        
        slot = await self._scan_calendar(
            page=page,
            preferred_dates=preferred_dates,
        )
        
        if slot:
            adapter.state = BLSFlowState.SLOT_FOUND
            slot.category = visa_category
            slot.center = center
        
        return slot
    
    async def _select_category(self, page, category: str):
        """Vize kategorisi seç"""
        
        category_code = BLSURLBuilder.VISA_CATEGORIES.get(category, category)
        
        await page.wait_for_selector(
            BLSAdapter.SELECTORS["visa_category"],
            timeout=BLSAdapter.ELEMENT_TIMEOUT,
        )
        
        await page.select_option(
            BLSAdapter.SELECTORS["visa_category"],
            value=category_code,
        )
        
        await asyncio.sleep(1)  # Wait for subcategory to load
        
        # Select subcategory if present
        subcategory = await page.query_selector(BLSAdapter.SELECTORS["visa_subcategory"])
        if subcategory:
            options = await page.query_selector_all(
                f'{BLSAdapter.SELECTORS["visa_subcategory"]} option'
            )
            if len(options) > 1:  # More than just placeholder
                await page.select_option(
                    BLSAdapter.SELECTORS["visa_subcategory"],
                    index=1,  # First real option
                )
                await asyncio.sleep(0.5)
    
    async def _select_center(self, page, center: str):
        """Merkez seç"""
        
        center_code = BLSURLBuilder.CITIES.get(center.lower(), center)
        
        center_select = await page.query_selector(BLSAdapter.SELECTORS["center_select"])
        
        if center_select:
            await page.select_option(
                BLSAdapter.SELECTORS["center_select"],
                value=center_code,
            )
            await asyncio.sleep(0.5)
    
    async def _scan_calendar(
        self,
        page,
        preferred_dates: Dict[str, str],
        max_months: int = 4,
    ) -> Optional[BLSSlot]:
        """Takvimi tara"""
        
        from_date = datetime.strptime(
            preferred_dates.get("from", "2025-01-01"),
            "%Y-%m-%d"
        ).date()
        
        to_date = datetime.strptime(
            preferred_dates.get("to", "2025-12-31"),
            "%Y-%m-%d"
        ).date()
        
        for _ in range(max_months):
            # Find available dates
            available_cells = await page.query_selector_all(
                BLSAdapter.SELECTORS["available_date"]
            )
            
            for cell in available_cells:
                date_attr = await cell.get_attribute("data-date")
                
                if date_attr:
                    try:
                        slot_date = datetime.strptime(date_attr, "%Y-%m-%d").date()
                        
                        if from_date <= slot_date <= to_date:
                            # Click on date
                            await cell.click()
                            await asyncio.sleep(1)
                            
                            # Get available time
                            time_slot = await self._get_first_available_time(page)
                            
                            if time_slot:
                                return BLSSlot(
                                    date=slot_date,
                                    time=time_slot,
                                    center="",
                                    category="",
                                )
                    except:
                        continue
            
            # Next month
            next_btn = await page.query_selector(BLSAdapter.SELECTORS["next_month"])
            if next_btn:
                await next_btn.click()
                await asyncio.sleep(1)
            else:
                break
        
        return None
    
    async def _get_first_available_time(self, page) -> Optional[str]:
        """İlk müsait saati al"""
        
        time_slots = await page.query_selector_all(
            BLSAdapter.SELECTORS["time_slot_available"]
        )
        
        for slot in time_slots:
            time_text = await slot.inner_text()
            if time_text.strip():
                # Click to select
                await slot.click()
                await asyncio.sleep(0.5)
                return time_text.strip()
        
        return None
```

---

## CAPTCHA Handler

```python
class BLSCaptchaHandler:
    """BLS CAPTCHA handling"""
    
    async def handle_captcha(self, adapter: BLSAdapter) -> bool:
        """Form submit öncesi CAPTCHA çöz"""
        
        page = adapter.session.page
        
        # Check for reCAPTCHA
        recaptcha = await page.query_selector(BLSAdapter.SELECTORS["recaptcha"])
        
        if not recaptcha:
            return True  # No CAPTCHA
        
        adapter.state = BLSFlowState.SOLVING_CAPTCHA
        
        result = await adapter.captcha.handle_captcha(
            page=page,
            proxy=adapter.proxy_config,
            max_attempts=3,
        )
        
        return result
```

---

## Submission Handler

```python
class BLSSubmissionHandler:
    """BLS form submission"""
    
    async def submit_booking(self, adapter: BLSAdapter) -> bool:
        """Booking'i submit et"""
        
        page = adapter.session.page
        
        adapter.state = BLSFlowState.CONFIRMING
        
        # Accept terms
        checkbox = await page.query_selector(BLSAdapter.SELECTORS["terms_checkbox"])
        if checkbox:
            is_checked = await checkbox.is_checked()
            if not is_checked:
                await checkbox.click()
                await asyncio.sleep(0.3)
        
        # Submit
        submit_btn = await page.wait_for_selector(
            BLSAdapter.SELECTORS["submit_button"],
            timeout=BLSAdapter.ELEMENT_TIMEOUT,
        )
        
        await submit_btn.click()
        
        # Wait for result
        await page.wait_for_load_state("networkidle")
        
        # Check result
        return await self._check_result(page)
    
    async def _check_result(self, page) -> bool:
        """Submission sonucunu kontrol et"""
        
        # Success check
        success = await page.query_selector(BLSAdapter.SELECTORS["success_message"])
        if success:
            return True
        
        # Error check
        error = await page.query_selector(BLSAdapter.SELECTORS["error_message"])
        if error:
            error_text = await error.inner_text()
            raise BookingError(f"BLS booking failed: {error_text}")
        
        # URL check
        if "confirmation" in page.url.lower():
            return True
        
        return False
    
    async def get_confirmation(self, page) -> Dict[str, str]:
        """Onay bilgilerini al"""
        
        result = {}
        
        # Confirmation number
        conf_elem = await page.query_selector(BLSAdapter.SELECTORS["confirmation_number"])
        if conf_elem:
            result["number"] = await conf_elem.inner_text()
        
        # Screenshot
        screenshot_path = f"/tmp/bls_confirmation_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
        await page.screenshot(path=screenshot_path)
        result["screenshot"] = screenshot_path
        
        return result
```

---

## Error Handling

```python
class BLSErrorClassifier:
    """BLS hata sınıflandırma"""
    
    ERROR_PATTERNS = {
        "session_expired": [
            r"session.*expired",
            r"please.*login",
        ],
        "slot_taken": [
            r"slot.*taken",
            r"no.*available",
            r"already.*booked",
        ],
        "validation": [
            r"invalid",
            r"required.*field",
            r"format.*incorrect",
        ],
        "captcha_failed": [
            r"captcha.*failed",
            r"verification.*failed",
        ],
    }
    
    @staticmethod
    def classify(error_message: str) -> Dict[str, Any]:
        error_lower = error_message.lower()
        
        for error_type, patterns in BLSErrorClassifier.ERROR_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, error_lower):
                    return {
                        "type": error_type.upper(),
                        "retriable": error_type not in ["validation"],
                        "cooldown_seconds": 60 if error_type == "captcha_failed" else 0,
                    }
        
        return {"type": "UNKNOWN", "retriable": True, "cooldown_seconds": 30}
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | Keyboard-only typing çalışmalı | Form test |
| AC-002 | Paste engeli bypass edilmeli | Integration test |
| AC-003 | Login %95+ başarı | Login test |
| AC-004 | Slot bulunduğunda booking %90+ başarı | E2E test |
| AC-005 | reCAPTCHA çözümü çalışmalı | CAPTCHA test |
| AC-006 | Tarih formatı doğru dönüşmeli | Unit test |

---

## Edge Cases

### Paste Detection

```
Detection: BLS paste event'i engelledi
Action: 
  - Zaten keyboard-only mode aktif
  - Fallback: Her karakter için individual key event
```

### Form Validation Error

```
Detection: Client-side validation hatası
Action:
  1. Hatalı field'ı bul
  2. Clear ve re-type
  3. Doğru format kullan
```

### Session Timeout Mid-Form

```
Detection: 20 dakika timeout
Action:
  1. Session'ı yenile (re-login)
  2. Form'u tekrar doldur
  3. Max 2 retry
```

---

## Sonraki Adımlar

1. **011-KKOSMOS-ADAPTER.md** - KKOSMOS (Yunanistan) adapter
2. BLS keyboard timing tuning
3. Form field mapping verification
