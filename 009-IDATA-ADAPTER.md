# 009 - iDATA Adapter Specification

## Amaç

iDATA sisteminde (Almanya/İtalya Schengen vizeleri) API-first yaklaşımla tam otomatik vize randevusu alma. VFS'e göre daha az agresif anti-bot koruması, jQuery tabanlı form, keşfedilmiş API endpoint'leri ile hızlı slot check.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 004-STEALTH-ENGINE | Browser session (login için) |
| 005-PROXY-MANAGER | Residential proxy (daha az kritik) |
| 006-CAPTCHA-SOLVER | reCAPTCHA v2 (sadece login) |
| 007-ACCOUNT-POOL-MANAGER | iDATA account credentials |
| 013-STATE-MACHINE | Booking state transitions |

---

## iDATA Teknik Profil

| Özellik | Değer |
|---------|-------|
| Anti-Bot | Basit - Session token + reCAPTCHA |
| CAPTCHA | reCAPTCHA v2 (sadece ilk login) |
| TLS Fingerprinting | **YOK** |
| Browser Fingerprinting | Minimal |
| Rate Limiting | Session bazlı, toleranslı |
| Session Timeout | 30 dakika |
| API Endpoint | Keşfedilmiş (slot check için) |
| Frontend | jQuery + Bootstrap |

---

## Keşfedilmiş API Endpoint'leri

```python
class iDATAEndpoints:
    """iDATA API endpoints"""
    
    # Base URLs
    BASE_GERMANY = "https://ita-schengen.idata.com.tr"
    BASE_ITALY = "https://service2.idata.com.tr"
    
    # API Endpoints
    ENDPOINTS = {
        "germany": {
            "check_slot": "/tr/getavailablefirstdate",
            "get_times": "/tr/getavailabletimes",
            "book": "/tr/bookappointment",
            "login": "/tr/login",
            "calendar": "/tr/calendar",
        },
        "italy": {
            "check_slot": "/tr/getavailablefirstdate",
            "get_times": "/tr/getavailabletimes",
            "book": "/tr/bookappointment",
            "login": "/tr/login",
            "calendar": "/tr/calendar",
        },
    }
    
    @classmethod
    def get_base_url(cls, country: str) -> str:
        if country == "de":
            return cls.BASE_GERMANY
        elif country == "it":
            return cls.BASE_ITALY
        raise ValueError(f"Unknown country: {country}")
    
    @classmethod
    def get_endpoint(cls, country: str, action: str) -> str:
        base = cls.get_base_url(country)
        country_key = "germany" if country == "de" else "italy"
        endpoint = cls.ENDPOINTS[country_key].get(action)
        return f"{base}{endpoint}"
```

---

## API Client (Slot Check)

```python
import httpx
from typing import Optional, Dict, List, Any
from datetime import date, datetime
import asyncio

class iDATAAPIClient:
    """iDATA API client - hızlı slot check için"""
    
    def __init__(self, session_token: str = None):
        self.session_token = session_token
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            }
        )
    
    async def check_available_dates(
        self,
        country: str,
        visa_type: str,
        center_id: str,
        proxy: Dict = None,
    ) -> List[Dict]:
        """Müsait tarihleri API ile sorgula"""
        
        endpoint = iDATAEndpoints.get_endpoint(country, "check_slot")
        
        # Add session cookie if available
        cookies = {}
        if self.session_token:
            cookies["ASP.NET_SessionId"] = self.session_token
        
        # Proxy configuration
        client_kwargs = {}
        if proxy:
            client_kwargs["proxies"] = {
                "http://": f"http://{proxy['username']}:{proxy['password']}@{proxy['host']}:{proxy['port']}",
                "https://": f"http://{proxy['username']}:{proxy['password']}@{proxy['host']}:{proxy['port']}",
            }
        
        response = await self.client.post(
            endpoint,
            json={
                "visaType": visa_type,
                "centerId": center_id,
            },
            cookies=cookies,
            **client_kwargs,
        )
        
        if response.status_code == 200:
            data = response.json()
            return self._parse_dates(data)
        
        return []
    
    async def get_available_times(
        self,
        country: str,
        date_str: str,
        center_id: str,
    ) -> List[Dict]:
        """Belirli tarih için müsait saatleri sorgula"""
        
        endpoint = iDATAEndpoints.get_endpoint(country, "get_times")
        
        cookies = {}
        if self.session_token:
            cookies["ASP.NET_SessionId"] = self.session_token
        
        response = await self.client.post(
            endpoint,
            json={
                "date": date_str,
                "centerId": center_id,
            },
            cookies=cookies,
        )
        
        if response.status_code == 200:
            return response.json().get("times", [])
        
        return []
    
    def _parse_dates(self, data: Any) -> List[Dict]:
        """API response'dan tarihleri parse et"""
        
        dates = []
        
        if isinstance(data, dict):
            # Single date response
            if data.get("date"):
                dates.append({
                    "date": data["date"],
                    "available": data.get("available", True),
                })
        elif isinstance(data, list):
            # Multiple dates
            for item in data:
                if item.get("date"):
                    dates.append({
                        "date": item["date"],
                        "available": item.get("available", True),
                    })
        
        return dates
    
    async def close(self):
        await self.client.aclose()
```

---

## iDATA Adapter Ana Sınıf

```python
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, List, Any
from datetime import datetime, date
import asyncio

class iDATAFlowState(Enum):
    """iDATA booking flow states"""
    INITIAL = "initial"
    NAVIGATING = "navigating"
    LOGIN_PAGE = "login_page"
    SOLVING_CAPTCHA = "solving_captcha"
    AUTHENTICATING = "authenticating"
    AUTHENTICATED = "authenticated"
    API_SLOT_CHECK = "api_slot_check"
    SLOT_FOUND = "slot_found"
    BROWSER_BOOKING = "browser_booking"
    FILLING_FORM = "filling_form"
    CONFIRMING = "confirming"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class iDATASlot:
    """iDATA randevu slot"""
    date: date
    time: str
    center_id: str
    center_name: str
    slot_id: Optional[str] = None

@dataclass
class iDATABookingResult:
    """Booking sonucu"""
    success: bool
    confirmation_number: Optional[str] = None
    appointment_date: Optional[date] = None
    appointment_time: Optional[str] = None
    center_name: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    total_attempts: int = 0
    duration_seconds: int = 0

class iDATAAdapter:
    """iDATA booking adapter"""
    
    # Timeouts
    PAGE_LOAD_TIMEOUT = 30000
    ELEMENT_TIMEOUT = 15000
    
    # Selectors (jQuery-based, simpler than VFS)
    SELECTORS = {
        # Login
        "email_input": '#Email, input[name="Email"]',
        "password_input": '#Password, input[name="Password"]',
        "login_button": '#btnLogin, button[type="submit"]',
        "captcha_container": '.g-recaptcha, #recaptcha',
        
        # Category/Center Selection
        "visa_type_select": '#VisaType, select[name="VisaType"]',
        "center_select": '#Center, select[name="Center"]',
        "applicant_count": '#ApplicantCount, input[name="ApplicantCount"]',
        
        # Calendar
        "calendar": '#calendar, .datepicker',
        "available_day": '.day.available, td.available',
        "next_month": '.next, .calendar-next',
        
        # Time Selection
        "time_select": '#Time, select[name="Time"]',
        "time_option": 'option:not([disabled])',
        
        # Applicant Form
        "first_name": '#FirstName, input[name="FirstName"]',
        "last_name": '#LastName, input[name="LastName"]',
        "birth_date": '#BirthDate, input[name="BirthDate"]',
        "passport_number": '#PassportNumber, input[name="PassportNumber"]',
        "passport_expiry": '#PassportExpiry, input[name="PassportExpiry"]',
        "nationality": '#Nationality, select[name="Nationality"]',
        "phone": '#Phone, input[name="Phone"]',
        "email_applicant": '#ApplicantEmail, input[name="ApplicantEmail"]',
        
        # Confirmation
        "terms_checkbox": '#TermsAccepted, input[name="TermsAccepted"]',
        "submit_button": '#btnSubmit, button[type="submit"]',
        
        # Result
        "success_message": '.alert-success, .success-message',
        "error_message": '.alert-danger, .error-message',
        "confirmation_number": '#ConfirmationNumber, .confirmation-code',
    }
    
    # Visa type mappings
    VISA_TYPES = {
        "germany": {
            "tourist": "TUR",
            "business": "BUS",
            "family": "FAM",
            "student": "STU",
            "medical": "MED",
        },
        "italy": {
            "tourist": "TUR",
            "business": "BUS",
            "family": "FAM",
            "student": "STU",
            "elective_residence": "ELR",
        },
    }
    
    # Center mappings
    CENTERS = {
        "germany": {
            "istanbul": "IST",
            "ankara": "ANK",
            "izmir": "IZM",
            "antalya": "ANT",
            "gaziantep": "GAZ",
        },
        "italy": {
            "istanbul": "IST",
            "ankara": "ANK",
            "izmir": "IZM",
        },
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
        self.api_client = None
        self.state = iDATAFlowState.INITIAL
        self.attempts = 0
        self.start_time = None
    
    async def book_appointment(
        self,
        target_country: str,  # "de" or "it"
        visa_category: str,
        center: str,
        applicant_data: Dict[str, Any],
        preferred_dates: Dict[str, str],
        max_attempts: int = 3,
    ) -> iDATABookingResult:
        """Ana booking flow - Hybrid approach (API + Browser)"""
        
        self.start_time = datetime.utcnow()
        self.attempts = 0
        
        for attempt in range(max_attempts):
            self.attempts = attempt + 1
            
            try:
                # Phase 1: API-based slot check (hızlı)
                slot = await self._api_slot_check(
                    country=target_country,
                    visa_type=visa_category,
                    center=center,
                    preferred_dates=preferred_dates,
                )
                
                if not slot:
                    return iDATABookingResult(
                        success=False,
                        error_code="NO_SLOT",
                        error_message="No available slots found",
                        total_attempts=self.attempts,
                        duration_seconds=self._get_duration(),
                    )
                
                # Phase 2: Browser-based booking
                await self._acquire_resources(target_country)
                
                # Login (sadece burada CAPTCHA olabilir)
                login_success = await self._login(target_country)
                if not login_success:
                    await self._handle_login_failure()
                    continue
                
                # Complete booking in browser
                booking_success = await self._complete_booking(
                    country=target_country,
                    slot=slot,
                    visa_type=visa_category,
                    applicant_data=applicant_data,
                )
                
                if booking_success:
                    confirmation = await self._get_confirmation()
                    
                    await self._release_resources(success=True)
                    
                    return iDATABookingResult(
                        success=True,
                        confirmation_number=confirmation.get("number"),
                        appointment_date=slot.date,
                        appointment_time=slot.time,
                        center_name=slot.center_name,
                        total_attempts=self.attempts,
                        duration_seconds=self._get_duration(),
                    )
                
            except Exception as e:
                await self._handle_error(e)
        
        await self._release_resources(success=False)
        
        return iDATABookingResult(
            success=False,
            error_code="MAX_ATTEMPTS",
            error_message=f"Failed after {self.attempts} attempts",
            total_attempts=self.attempts,
            duration_seconds=self._get_duration(),
        )
    
    async def _api_slot_check(
        self,
        country: str,
        visa_type: str,
        center: str,
        preferred_dates: Dict[str, str],
    ) -> Optional[iDATASlot]:
        """API ile hızlı slot kontrolü"""
        
        self.state = iDATAFlowState.API_SLOT_CHECK
        
        # Get proxy for API call
        proxy = await self.proxy.get_proxy(
            target_site="idata",
            country="tr",
            sticky_session=False,  # API calls don't need sticky
        )
        
        # Initialize API client
        api = iDATAAPIClient()
        
        try:
            country_key = "germany" if country == "de" else "italy"
            visa_code = self.VISA_TYPES[country_key].get(visa_type, visa_type)
            center_code = self.CENTERS[country_key].get(center.lower(), center)
            
            # Check available dates
            available_dates = await api.check_available_dates(
                country=country,
                visa_type=visa_code,
                center_id=center_code,
                proxy=proxy,
            )
            
            if not available_dates:
                return None
            
            # Filter by preferred dates
            from_date = datetime.strptime(preferred_dates.get("from", "2025-01-01"), "%Y-%m-%d").date()
            to_date = datetime.strptime(preferred_dates.get("to", "2025-12-31"), "%Y-%m-%d").date()
            
            for date_info in available_dates:
                slot_date = datetime.strptime(date_info["date"], "%Y-%m-%d").date()
                
                if from_date <= slot_date <= to_date:
                    # Get times for this date
                    times = await api.get_available_times(
                        country=country,
                        date_str=date_info["date"],
                        center_id=center_code,
                    )
                    
                    if times:
                        self.state = iDATAFlowState.SLOT_FOUND
                        
                        return iDATASlot(
                            date=slot_date,
                            time=times[0].get("time", "09:00"),
                            center_id=center_code,
                            center_name=center,
                            slot_id=times[0].get("id"),
                        )
            
            return None
            
        finally:
            await api.close()
    
    def _get_duration(self) -> int:
        if self.start_time:
            return int((datetime.utcnow() - self.start_time).total_seconds())
        return 0
```

---

## Login Handler

```python
class iDATALoginHandler:
    """iDATA login işlemleri"""
    
    async def login(self, adapter: iDATAAdapter, country: str) -> bool:
        """Login flow"""
        
        page = adapter.session.page
        
        # Navigate to login
        adapter.state = iDATAFlowState.NAVIGATING
        
        login_url = iDATAEndpoints.get_endpoint(country, "login")
        await page.goto(login_url, wait_until="networkidle")
        
        adapter.state = iDATAFlowState.LOGIN_PAGE
        
        # Wait for form
        await page.wait_for_selector(
            iDATAAdapter.SELECTORS["email_input"],
            timeout=iDATAAdapter.ELEMENT_TIMEOUT,
        )
        
        # Fill credentials
        await self._fill_credentials(page, adapter.account)
        
        # Handle CAPTCHA (iDATA sadece login'de CAPTCHA kullanır)
        captcha_element = await page.query_selector(iDATAAdapter.SELECTORS["captcha_container"])
        
        if captcha_element:
            adapter.state = iDATAFlowState.SOLVING_CAPTCHA
            
            captcha_solved = await adapter.captcha.handle_captcha(
                page=page,
                proxy=adapter.proxy_config,
            )
            
            if not captcha_solved:
                raise CaptchaError("Failed to solve CAPTCHA")
        
        # Submit
        adapter.state = iDATAFlowState.AUTHENTICATING
        
        await page.click(iDATAAdapter.SELECTORS["login_button"])
        await page.wait_for_load_state("networkidle")
        
        # Extract session token for API calls
        cookies = await page.context.cookies()
        for cookie in cookies:
            if cookie["name"] == "ASP.NET_SessionId":
                adapter.api_client = iDATAAPIClient(session_token=cookie["value"])
                break
        
        # Verify login
        if await self._is_logged_in(page):
            adapter.state = iDATAFlowState.AUTHENTICATED
            return True
        
        return False
    
    async def _fill_credentials(self, page, account):
        """Credential doldur (iDATA için human simulation daha az kritik)"""
        
        # Email
        await page.fill(iDATAAdapter.SELECTORS["email_input"], account.email)
        await asyncio.sleep(random.uniform(0.2, 0.4))
        
        # Password
        await page.fill(iDATAAdapter.SELECTORS["password_input"], account.password)
        await asyncio.sleep(random.uniform(0.2, 0.4))
    
    async def _is_logged_in(self, page) -> bool:
        """Login başarılı mı?"""
        
        # Check for dashboard/appointment elements
        indicators = [
            ".user-info",
            ".logout",
            "#VisaType",  # Booking form visible
            ".welcome",
        ]
        
        for selector in indicators:
            if await page.query_selector(selector):
                return True
        
        return False
```

---

## Booking Form Handler

```python
class iDATABookingHandler:
    """iDATA booking form işlemleri"""
    
    async def complete_booking(
        self,
        adapter: iDATAAdapter,
        country: str,
        slot: iDATASlot,
        visa_type: str,
        applicant_data: Dict[str, Any],
    ) -> bool:
        """Browser'da booking tamamla"""
        
        page = adapter.session.page
        adapter.state = iDATAFlowState.BROWSER_BOOKING
        
        # Navigate to booking page
        booking_url = iDATAEndpoints.get_endpoint(country, "calendar")
        await page.goto(booking_url, wait_until="networkidle")
        
        # Select visa type
        await self._select_visa_type(page, country, visa_type)
        
        # Select center
        await self._select_center(page, country, slot.center_id)
        
        # Select date (from API-found slot)
        await self._select_date(page, slot.date)
        
        # Select time
        await self._select_time(page, slot.time)
        
        # Fill applicant form
        adapter.state = iDATAFlowState.FILLING_FORM
        await self._fill_applicant_form(page, applicant_data)
        
        # Confirm
        adapter.state = iDATAFlowState.CONFIRMING
        await self._confirm_booking(page)
        
        # Check result
        return await self._check_booking_result(page)
    
    async def _select_visa_type(self, page, country: str, visa_type: str):
        """Vize tipi seç"""
        
        country_key = "germany" if country == "de" else "italy"
        visa_code = iDATAAdapter.VISA_TYPES[country_key].get(visa_type, visa_type)
        
        await page.select_option(
            iDATAAdapter.SELECTORS["visa_type_select"],
            value=visa_code,
        )
        
        await asyncio.sleep(0.5)  # Wait for form update
    
    async def _select_center(self, page, country: str, center_id: str):
        """Merkez seç"""
        
        await page.select_option(
            iDATAAdapter.SELECTORS["center_select"],
            value=center_id,
        )
        
        await asyncio.sleep(0.5)
    
    async def _select_date(self, page, target_date: date):
        """Tarih seç"""
        
        # iDATA jQuery datepicker
        calendar = await page.wait_for_selector(
            iDATAAdapter.SELECTORS["calendar"],
            timeout=10000,
        )
        
        # Click on the date
        date_selector = f'td[data-date="{target_date.isoformat()}"], ' \
                        f'.day[data-date="{target_date.strftime("%d/%m/%Y")}"]'
        
        date_cell = await page.query_selector(date_selector)
        
        if date_cell:
            await date_cell.click()
        else:
            # Try alternative: input the date directly
            date_input = await page.query_selector('input[name*="Date"], #AppointmentDate')
            if date_input:
                await date_input.fill(target_date.strftime("%d/%m/%Y"))
        
        await asyncio.sleep(0.5)
    
    async def _select_time(self, page, time_str: str):
        """Saat seç"""
        
        time_select = await page.query_selector(iDATAAdapter.SELECTORS["time_select"])
        
        if time_select:
            # Find matching option
            options = await page.query_selector_all(
                f'{iDATAAdapter.SELECTORS["time_select"]} option'
            )
            
            for option in options:
                option_text = await option.inner_text()
                if time_str in option_text:
                    value = await option.get_attribute("value")
                    await page.select_option(
                        iDATAAdapter.SELECTORS["time_select"],
                        value=value,
                    )
                    break
        
        await asyncio.sleep(0.5)
    
    async def _fill_applicant_form(self, page, data: Dict[str, Any]):
        """Başvuran bilgilerini doldur"""
        
        field_mapping = {
            "first_name": ("first_name", data.get("first_name", "")),
            "last_name": ("last_name", data.get("last_name", "")),
            "birth_date": ("birth_date", data.get("birth_date", "")),
            "passport_number": ("passport_number", data.get("passport_number", "")),
            "passport_expiry": ("passport_expiry", data.get("passport_expiry", "")),
            "phone": ("phone", data.get("phone", "")),
            "email_applicant": ("email_applicant", data.get("email", "")),
        }
        
        for field_key, (selector_key, value) in field_mapping.items():
            if value:
                selector = iDATAAdapter.SELECTORS.get(selector_key)
                if selector:
                    field = await page.query_selector(selector)
                    if field:
                        await field.fill(str(value))
                        await asyncio.sleep(random.uniform(0.1, 0.3))
        
        # Nationality dropdown
        if data.get("nationality"):
            nationality_select = await page.query_selector(
                iDATAAdapter.SELECTORS["nationality"]
            )
            if nationality_select:
                await page.select_option(
                    iDATAAdapter.SELECTORS["nationality"],
                    value=data["nationality"],
                )
    
    async def _confirm_booking(self, page):
        """Booking'i onayla"""
        
        # Check terms
        checkbox = await page.query_selector(iDATAAdapter.SELECTORS["terms_checkbox"])
        if checkbox:
            is_checked = await checkbox.is_checked()
            if not is_checked:
                await checkbox.click()
        
        await asyncio.sleep(0.3)
        
        # Submit
        await page.click(iDATAAdapter.SELECTORS["submit_button"])
        await page.wait_for_load_state("networkidle")
    
    async def _check_booking_result(self, page) -> bool:
        """Booking sonucunu kontrol et"""
        
        # Success check
        success = await page.query_selector(iDATAAdapter.SELECTORS["success_message"])
        if success:
            return True
        
        # Error check
        error = await page.query_selector(iDATAAdapter.SELECTORS["error_message"])
        if error:
            error_text = await error.inner_text()
            raise BookingError(f"Booking failed: {error_text}")
        
        # Check URL
        if "confirmation" in page.url.lower() or "success" in page.url.lower():
            return True
        
        return False
```

---

## Continuous Slot Monitor (API-based)

```python
class iDATASlotMonitor:
    """Sürekli slot monitoring - API ile hızlı"""
    
    def __init__(self, proxy_manager):
        self.proxy = proxy_manager
        self.running = False
        self.found_slots: asyncio.Queue = asyncio.Queue()
    
    async def start_monitoring(
        self,
        country: str,
        visa_type: str,
        centers: List[str],
        interval_seconds: int = 30,
    ):
        """Slot monitoring başlat"""
        
        self.running = True
        api = iDATAAPIClient()
        
        try:
            while self.running:
                for center in centers:
                    try:
                        proxy = await self.proxy.get_proxy(
                            target_site="idata",
                            sticky_session=False,
                        )
                        
                        country_key = "germany" if country == "de" else "italy"
                        visa_code = iDATAAdapter.VISA_TYPES[country_key].get(visa_type, visa_type)
                        center_code = iDATAAdapter.CENTERS[country_key].get(center.lower(), center)
                        
                        dates = await api.check_available_dates(
                            country=country,
                            visa_type=visa_code,
                            center_id=center_code,
                            proxy=proxy,
                        )
                        
                        if dates:
                            await self.found_slots.put({
                                "country": country,
                                "visa_type": visa_type,
                                "center": center,
                                "dates": dates,
                                "timestamp": datetime.utcnow(),
                            })
                        
                        # Small delay between centers
                        await asyncio.sleep(random.uniform(1, 3))
                        
                    except Exception as e:
                        # Log and continue
                        pass
                
                # Wait for next cycle
                await asyncio.sleep(interval_seconds)
                
        finally:
            await api.close()
    
    async def stop_monitoring(self):
        self.running = False
    
    async def get_next_slot(self, timeout: float = None) -> Optional[Dict]:
        """Bulunan slot'u al"""
        try:
            return await asyncio.wait_for(
                self.found_slots.get(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return None
```

---

## Error Handling

```python
class iDATAErrorClassifier:
    """iDATA hata sınıflandırma"""
    
    ERROR_PATTERNS = {
        "session_expired": [
            r"session.*expired",
            r"please.*login",
            r"unauthorized",
        ],
        "slot_taken": [
            r"slot.*taken",
            r"no.*available",
            r"not.*found",
        ],
        "validation_error": [
            r"invalid",
            r"required",
            r"format",
        ],
        "rate_limit": [
            r"too.*many",
            r"try.*later",
        ],
    }
    
    @staticmethod
    def classify(error_message: str) -> Dict[str, Any]:
        error_lower = error_message.lower()
        
        for error_type, patterns in iDATAErrorClassifier.ERROR_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, error_lower):
                    return {
                        "type": error_type.upper(),
                        "retriable": error_type != "validation_error",
                        "cooldown_seconds": 60 if error_type == "rate_limit" else 0,
                    }
        
        return {
            "type": "UNKNOWN",
            "retriable": True,
            "cooldown_seconds": 30,
        }
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | API slot check çalışmalı | Unit test |
| AC-002 | Login %98+ başarı (daha az CAPTCHA) | Integration test |
| AC-003 | Slot bulunduğunda booking %95+ başarı | E2E test |
| AC-004 | Hybrid flow (API + Browser) çalışmalı | Flow test |
| AC-005 | Continuous monitor interval uyumlu | Timing test |
| AC-006 | Datacenter proxy kabul edilmeli | Proxy test |

---

## VFS vs iDATA Karşılaştırma

| Özellik | VFS | iDATA |
|---------|-----|-------|
| Cloudflare | Enterprise | Yok |
| CAPTCHA | Her sayfa | Sadece login |
| TLS Fingerprint | Aktif | Yok |
| API Endpoint | Yok | Mevcut |
| Proxy Gereksinimi | Residential zorunlu | Datacenter kabul |
| Rate Limit | 2 saat ban | Toleranslı |
| Zorluk | Yüksek | Düşük |

---

## Sonraki Adımlar

1. **010-BLS-ADAPTER.md** - BLS Spain adapter
2. iDATA API endpoint monitoring
3. Production integration test
