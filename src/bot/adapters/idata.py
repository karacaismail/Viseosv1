"""
VISE OS iDATA Adapter.

iDATA booking adapter implementing the complete booking flow for
Germany and Italy Schengen visa appointments. Uses a hybrid API-first
approach for slot checking with browser-based booking for higher success rates.

Features:
- API-based fast slot checking (discovered endpoints)
- reCAPTCHA v2 handling (login only)
- Minimal browser fingerprinting requirements
- Datacenter proxy support (less strict than VFS)
- Session token reuse for API calls
- Continuous slot monitoring support

Technical Profile:
- Anti-Bot: Simple - Session token + reCAPTCHA
- Rate Limiting: Session-based, tolerant
- Session Timeout: 30 minutes
- CAPTCHA: reCAPTCHA v2 on login only
- TLS Fingerprinting: None
- Browser Fingerprinting: Minimal

Usage:
    from src.bot.adapters.idata import IDataAdapter
    from src.bot.adapters import AdapterConfig, SiteCode

    config = AdapterConfig(
        site_code=SiteCode.IDATA,
        base_url="https://ita-schengen.idata.com.tr",
    )

    adapter = IDataAdapter(
        config=config,
        stealth_engine=stealth_engine,
        proxy_manager=proxy_manager,
        captcha_solver=captcha_solver,
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
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

import httpx
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


class IDataFlowState(Enum):
    """iDATA booking flow states."""

    INITIAL = "initial"
    NAVIGATING = "navigating"
    LOGIN_PAGE = "login_page"
    SOLVING_CAPTCHA = "solving_captcha"
    AUTHENTICATING = "authenticating"
    AUTHENTICATED = "authenticated"
    API_SLOT_CHECK = "api_slot_check"
    SLOT_FOUND = "slot_found"
    BROWSER_BOOKING = "browser_booking"
    SELECTING_CATEGORY = "selecting_category"
    SELECTING_CENTER = "selecting_center"
    SELECTING_DATE = "selecting_date"
    SELECTING_TIME = "selecting_time"
    FILLING_FORM = "filling_form"
    CONFIRMING = "confirming"
    COMPLETED = "completed"
    FAILED = "failed"


# =============================================================================
# iDATA URL and Endpoint Builder
# =============================================================================


class IDataEndpoints:
    """iDATA API endpoints and URL builder."""

    # Base URLs for different countries
    BASE_GERMANY = "https://ita-schengen.idata.com.tr"
    BASE_ITALY = "https://service2.idata.com.tr"

    # API Endpoints by country
    ENDPOINTS: dict[str, dict[str, str]] = {
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
        """
        Get base URL for a country.

        Args:
            country: Country code (de for Germany, it for Italy).

        Returns:
            Base URL for the country.

        Raises:
            ValueError: If country is not supported.
        """
        if country == "de":
            return cls.BASE_GERMANY
        elif country == "it":
            return cls.BASE_ITALY
        raise ValueError(f"Unknown country: {country}")

    @classmethod
    def get_endpoint(cls, country: str, action: str) -> str:
        """
        Get full endpoint URL for an action.

        Args:
            country: Country code (de for Germany, it for Italy).
            action: API action name.

        Returns:
            Full endpoint URL.
        """
        base = cls.get_base_url(country)
        country_key = "germany" if country == "de" else "italy"
        endpoint = cls.ENDPOINTS[country_key].get(action, "")
        return f"{base}{endpoint}"

    @classmethod
    def get_country_key(cls, country: str) -> str:
        """Get country key from country code."""
        return "germany" if country == "de" else "italy"


# =============================================================================
# iDATA Selectors
# =============================================================================


class IDataSelectors:
    """CSS selectors for iDATA pages (jQuery-based, simpler than VFS)."""

    # Login page
    EMAIL_INPUT = '#Email, input[name="Email"]'
    PASSWORD_INPUT = '#Password, input[name="Password"]'
    LOGIN_BUTTON = '#btnLogin, button[type="submit"]'
    CAPTCHA_CONTAINER = ".g-recaptcha, #recaptcha"

    # Category/Center selection
    VISA_TYPE_SELECT = '#VisaType, select[name="VisaType"]'
    CENTER_SELECT = '#Center, select[name="Center"]'
    APPLICANT_COUNT = '#ApplicantCount, input[name="ApplicantCount"]'

    # Calendar
    CALENDAR = "#calendar, .datepicker"
    AVAILABLE_DAY = ".day.available, td.available"
    NEXT_MONTH = ".next, .calendar-next"
    PREV_MONTH = ".prev, .calendar-prev"

    # Time selection
    TIME_SELECT = '#Time, select[name="Time"]'
    TIME_OPTION = "option:not([disabled])"

    # Applicant form
    FIRST_NAME = '#FirstName, input[name="FirstName"]'
    LAST_NAME = '#LastName, input[name="LastName"]'
    BIRTH_DATE = '#BirthDate, input[name="BirthDate"]'
    PASSPORT_NUMBER = '#PassportNumber, input[name="PassportNumber"]'
    PASSPORT_EXPIRY = '#PassportExpiry, input[name="PassportExpiry"]'
    NATIONALITY = '#Nationality, select[name="Nationality"]'
    PHONE = '#Phone, input[name="Phone"]'
    EMAIL_APPLICANT = '#ApplicantEmail, input[name="ApplicantEmail"]'

    # Confirmation
    TERMS_CHECKBOX = '#TermsAccepted, input[name="TermsAccepted"]'
    SUBMIT_BUTTON = '#btnSubmit, button[type="submit"]'
    CONTINUE_BUTTON = 'button:has-text("Continue"), .continue-btn, .btn-continue'

    # Result
    SUCCESS_MESSAGE = ".alert-success, .success-message"
    ERROR_MESSAGE = ".alert-danger, .error-message"
    CONFIRMATION_NUMBER = "#ConfirmationNumber, .confirmation-code"

    # Dashboard indicators (post-login)
    DASHBOARD_INDICATORS = [
        ".user-info",
        ".logout",
        "#VisaType",  # Booking form visible
        ".welcome",
        ".user-menu",
    ]


# =============================================================================
# Error Patterns and Classifier
# =============================================================================


class IDataErrorPatterns:
    """Error detection patterns for iDATA."""

    SESSION_EXPIRED = [
        r"session.*expired",
        r"please.*login",
        r"unauthorized",
    ]

    SLOT_TAKEN = [
        r"slot.*taken",
        r"no.*available",
        r"not.*found",
        r"already.*booked",
    ]

    VALIDATION_ERROR = [
        r"invalid",
        r"required",
        r"format",
    ]

    RATE_LIMIT = [
        r"too.*many",
        r"try.*later",
    ]


@dataclass
class IDataErrorClassification:
    """Classification result for iDATA errors."""

    error_type: str
    retriable: bool
    cooldown_seconds: int
    action: str


class IDataErrorClassifier:
    """Classifies iDATA errors for appropriate handling."""

    @staticmethod
    def classify(error_message: str) -> IDataErrorClassification:
        """
        Classify an error message.

        Args:
            error_message: Error message to classify.

        Returns:
            IDataErrorClassification with handling instructions.
        """
        error_lower = error_message.lower()

        # Session expired
        for pattern in IDataErrorPatterns.SESSION_EXPIRED:
            if re.search(pattern, error_lower):
                return IDataErrorClassification(
                    error_type="SESSION_EXPIRED",
                    retriable=True,
                    cooldown_seconds=0,
                    action="re_login",
                )

        # Slot taken
        for pattern in IDataErrorPatterns.SLOT_TAKEN:
            if re.search(pattern, error_lower):
                return IDataErrorClassification(
                    error_type="SLOT_TAKEN",
                    retriable=True,
                    cooldown_seconds=0,
                    action="find_new_slot",
                )

        # Validation error
        for pattern in IDataErrorPatterns.VALIDATION_ERROR:
            if re.search(pattern, error_lower):
                return IDataErrorClassification(
                    error_type="VALIDATION_ERROR",
                    retriable=False,
                    cooldown_seconds=0,
                    action="fix_data",
                )

        # Rate limit
        for pattern in IDataErrorPatterns.RATE_LIMIT:
            if re.search(pattern, error_lower):
                return IDataErrorClassification(
                    error_type="RATE_LIMIT",
                    retriable=True,
                    cooldown_seconds=60,
                    action="wait_and_retry",
                )

        # Default: unknown error
        return IDataErrorClassification(
            error_type="UNKNOWN",
            retriable=True,
            cooldown_seconds=30,
            action="retry",
        )


# =============================================================================
# iDATA API Client
# =============================================================================


class IDataAPIClient:
    """
    iDATA API client for fast slot checking.

    Uses discovered API endpoints for rapid slot availability checks
    without requiring browser automation.
    """

    def __init__(self, session_token: str | None = None) -> None:
        """
        Initialize API client.

        Args:
            session_token: ASP.NET session token for authenticated requests.
        """
        self.session_token = session_token
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )

    async def check_available_dates(
        self,
        country: str,
        visa_type: str,
        center_id: str,
        proxy: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Query available dates via API.

        Args:
            country: Country code (de or it).
            visa_type: Visa type code.
            center_id: Center ID code.
            proxy: Optional proxy configuration.

        Returns:
            List of available date dictionaries.
        """
        endpoint = IDataEndpoints.get_endpoint(country, "check_slot")

        # Add session cookie if available
        cookies: dict[str, str] = {}
        if self.session_token:
            cookies["ASP.NET_SessionId"] = self.session_token

        try:
            response = await self.client.post(
                endpoint,
                json={
                    "visaType": visa_type,
                    "centerId": center_id,
                },
                cookies=cookies,
            )

            if response.status_code == 200:
                data = response.json()
                return self._parse_dates(data)

        except Exception as e:
            logger.warning(
                "idata_api_check_dates_error",
                country=country,
                error=str(e),
            )

        return []

    async def get_available_times(
        self,
        country: str,
        date_str: str,
        center_id: str,
    ) -> list[dict[str, Any]]:
        """
        Query available times for a specific date.

        Args:
            country: Country code.
            date_str: Date string (YYYY-MM-DD format).
            center_id: Center ID code.

        Returns:
            List of available time slot dictionaries.
        """
        endpoint = IDataEndpoints.get_endpoint(country, "get_times")

        cookies: dict[str, str] = {}
        if self.session_token:
            cookies["ASP.NET_SessionId"] = self.session_token

        try:
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

        except Exception as e:
            logger.warning(
                "idata_api_get_times_error",
                country=country,
                date=date_str,
                error=str(e),
            )

        return []

    def _parse_dates(self, data: Any) -> list[dict[str, Any]]:
        """
        Parse dates from API response.

        Args:
            data: API response data.

        Returns:
            List of parsed date dictionaries.
        """
        dates: list[dict[str, Any]] = []

        if isinstance(data, dict):
            # Single date response
            if data.get("date"):
                dates.append(
                    {
                        "date": data["date"],
                        "available": data.get("available", True),
                    }
                )
        elif isinstance(data, list):
            # Multiple dates
            for item in data:
                if item.get("date"):
                    dates.append(
                        {
                            "date": item["date"],
                            "available": item.get("available", True),
                        }
                    )

        return dates

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()


# =============================================================================
# iDATA Adapter Main Class
# =============================================================================


class IDataAdapter(BaseSiteAdapter):
    """
    iDATA booking adapter.

    Implements the hybrid booking flow for iDATA visa appointments
    with API-based slot checking and browser-based booking.

    Supports Germany and Italy Schengen visa appointments.

    Attributes:
        flow_state: Current state in the booking flow.
        selectors: iDATA-specific CSS selectors.
        endpoints: iDATA endpoint builder.
        api_client: API client for slot checking.
    """

    # Timeouts (milliseconds)
    PAGE_LOAD_TIMEOUT = 30000  # 30 seconds
    ELEMENT_TIMEOUT = 15000  # 15 seconds

    # Visa type mappings
    VISA_TYPES: dict[str, dict[str, str]] = {
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
    CENTERS: dict[str, dict[str, str]] = {
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
        config: AdapterConfig | None = None,
        stealth_engine: StealthEngine | None = None,
        proxy_manager: ProxyManager | None = None,
        captcha_solver: CaptchaSolver | None = None,
    ) -> None:
        """
        Initialize the iDATA adapter.

        Args:
            config: Adapter configuration. Defaults to iDATA config if None.
            stealth_engine: Browser stealth engine.
            proxy_manager: Proxy rotation manager.
            captcha_solver: CAPTCHA solving service.
        """
        if config is None:
            config = AdapterConfig(
                site_code=SiteCode.IDATA,
                base_url=IDataEndpoints.BASE_GERMANY,
            )

        super().__init__(
            config=config,
            stealth_engine=stealth_engine,
            proxy_manager=proxy_manager,
            captcha_solver=captcha_solver,
        )

        self.flow_state = IDataFlowState.INITIAL
        self.selectors = IDataSelectors
        self.endpoints = IDataEndpoints
        self.api_client: IDataAPIClient | None = None

        logger.info(
            "idata_adapter_initialized",
            base_url=config.base_url,
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
        Authenticate with iDATA.

        Handles login form with reCAPTCHA v2 solving (only on login page).
        iDATA has simpler anti-bot protection than VFS.

        Args:
            session: Active adapter session.
            account: Bot account with credentials.

        Returns:
            True if login successful.

        Raises:
            LoginFailedError: If login fails after retries.
            AccountBannedError: If account is banned/locked.
            CaptchaSolveTimeoutError: If CAPTCHA cannot be solved.
        """
        page = session.page
        self.flow_state = IDataFlowState.NAVIGATING

        logger.info(
            "idata_login_started",
            session_id=session.id,
            account_id=account.id,
            country=getattr(account, "country", "de"),
        )

        try:
            # Build login URL
            country = getattr(account, "country", "de")
            login_url = self.endpoints.get_endpoint(country, "login")

            # Navigate to login page
            await page.goto(login_url, wait_until="networkidle")
            session.increment_requests()

            # Verify we're on login page
            self.flow_state = IDataFlowState.LOGIN_PAGE
            email_input = await self.wait_for_element(
                page,
                self.selectors.EMAIL_INPUT,
                timeout=self.ELEMENT_TIMEOUT,
            )

            if not email_input:
                raise SelectorNotFoundError(
                    "Email input not found on login page",
                    selector=self.selectors.EMAIL_INPUT,
                    page_url=page.url,
                    site="idata",
                )

            # Fill credentials
            self.flow_state = IDataFlowState.AUTHENTICATING
            await self._fill_login_form(page, account)

            # Handle CAPTCHA if present (iDATA only uses CAPTCHA on login)
            captcha_element = await page.query_selector(
                self.selectors.CAPTCHA_CONTAINER
            )

            if captcha_element:
                self.flow_state = IDataFlowState.SOLVING_CAPTCHA
                captcha_solved = await self._handle_login_captcha(page, session)

                if not captcha_solved:
                    raise CaptchaError(
                        "Failed to solve CAPTCHA",
                        captcha_type="recaptcha_v2",
                    )

            # Submit login
            await page.click(self.selectors.LOGIN_BUTTON)
            await page.wait_for_load_state("networkidle")
            session.increment_requests()

            # Extract session token for API calls
            cookies = await page.context.cookies()
            for cookie in cookies:
                if cookie["name"] == "ASP.NET_SessionId":
                    self.api_client = IDataAPIClient(session_token=cookie["value"])
                    break

            # Verify login success
            if await self._is_logged_in(page):
                self.flow_state = IDataFlowState.AUTHENTICATED
                session.is_authenticated = True
                session.login_time = datetime.utcnow()
                self.metrics.record_login(success=True)

                logger.info(
                    "idata_login_success",
                    session_id=session.id,
                    account_id=account.id,
                )
                return True

            # Check for error messages
            error = await self._get_page_error(page)
            if error:
                classification = IDataErrorClassifier.classify(error)

                if "banned" in error.lower() or "suspended" in error.lower():
                    self.metrics.record_login(success=False)
                    raise AccountBannedError(
                        f"iDATA account banned: {error}",
                        account_id=account.id,
                        ban_reason=error,
                        site="idata",
                    )

                self.metrics.record_login(success=False)
                raise LoginFailedError(
                    f"iDATA login failed: {error}",
                    site="idata",
                    account_id=account.id,
                    reason=error,
                )

            self.metrics.record_login(success=False)
            return False

        except (LoginFailedError, AccountBannedError, CaptchaError):
            raise
        except Exception as e:
            self.metrics.record_login(success=False)
            self.metrics.record_error("login_error")
            logger.error(
                "idata_login_error",
                session_id=session.id,
                error=str(e),
            )
            raise LoginFailedError(
                f"iDATA login failed: {e}",
                site="idata",
                account_id=account.id,
            ) from e

    async def search_slots(
        self,
        session: AdapterSession,
        criteria: SlotSearchCriteria,
    ) -> list[Slot]:
        """
        Search for available appointment slots using hybrid approach.

        Uses API-based fast slot checking first, with browser fallback
        for detailed time slot retrieval.

        Args:
            session: Active authenticated session.
            criteria: Search criteria for slots.

        Returns:
            List of available Slot objects.

        Raises:
            NoSlotsFoundError: If no slots match criteria.
            SiteAdapterError: For navigation or selection errors.
        """
        start_time = time.time()
        slots: list[Slot] = []

        if not session.is_authenticated:
            raise SiteAdapterError(
                "Session not authenticated",
                site="idata",
                step="search_slots",
            )

        logger.info(
            "idata_slot_search_started",
            session_id=session.id,
            country=criteria.country,
            category=criteria.category,
        )

        try:
            self.flow_state = IDataFlowState.API_SLOT_CHECK

            # Get visa and center codes
            country_key = self.endpoints.get_country_key(criteria.country)
            visa_code = self.VISA_TYPES.get(country_key, {}).get(
                criteria.category, criteria.category
            )
            center = criteria.city or "istanbul"
            center_code = self.CENTERS.get(country_key, {}).get(
                center.lower(), center
            )

            # Try API-based slot check first (faster)
            if self.api_client:
                slots = await self._api_slot_search(
                    country=criteria.country,
                    visa_code=visa_code,
                    center_code=center_code,
                    criteria=criteria,
                )

            # If API fails or no slots, try browser-based search
            if not slots:
                slots = await self._browser_slot_search(session, criteria)

            # Record search metrics
            duration_ms = (time.time() - start_time) * 1000
            self.metrics.record_search(len(slots), duration_ms)

            if slots:
                self.flow_state = IDataFlowState.SLOT_FOUND
                logger.info(
                    "idata_slots_found",
                    session_id=session.id,
                    count=len(slots),
                    duration_ms=duration_ms,
                )
            else:
                logger.info(
                    "idata_no_slots_found",
                    session_id=session.id,
                    criteria=criteria.to_dict(),
                )
                raise NoSlotsFoundError(
                    "No available slots found matching criteria",
                    site="idata",
                    search_criteria=criteria.to_dict(),
                )

            return slots

        except NoSlotsFoundError:
            raise
        except Exception as e:
            self.metrics.record_error("search_error")
            logger.error(
                "idata_slot_search_error",
                session_id=session.id,
                error=str(e),
            )
            raise SiteAdapterError(
                f"iDATA slot search failed: {e}",
                site="idata",
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

        Uses browser automation to complete the booking flow,
        filling applicant forms and confirming the appointment.

        Args:
            session: Active authenticated session.
            slot: Selected slot to book.
            applicant: Applicant data for form filling.

        Returns:
            BookingResult with confirmation or error details.

        Raises:
            SlotNotAvailableError: If slot is no longer available.
            SiteAdapterError: For booking failures.
        """
        page = session.page
        start_time = datetime.utcnow()

        if not session.is_authenticated:
            raise SiteAdapterError(
                "Session not authenticated",
                site="idata",
                step="book_slot",
            )

        logger.info(
            "idata_booking_started",
            session_id=session.id,
            slot_id=slot.id,
            slot_date=slot.date.isoformat(),
        )

        try:
            self.flow_state = IDataFlowState.BROWSER_BOOKING

            # Navigate to booking/calendar page
            country = applicant.get("country", "de")
            calendar_url = self.endpoints.get_endpoint(country, "calendar")
            await page.goto(calendar_url, wait_until="networkidle")
            session.increment_requests()

            # Select visa type
            self.flow_state = IDataFlowState.SELECTING_CATEGORY
            await self._select_visa_type(page, country, slot.category)
            await self.human_delay(0.3, 0.5)

            # Select center
            self.flow_state = IDataFlowState.SELECTING_CENTER
            await self._select_center(page, country, slot.raw_data.get("center_id", slot.location))
            await self.human_delay(0.3, 0.5)

            # Select date
            self.flow_state = IDataFlowState.SELECTING_DATE
            date_selected = await self._select_date(page, slot.date)
            if not date_selected:
                raise SlotNotAvailableError(
                    "Failed to select date - may no longer be available",
                    slot_id=slot.id,
                    slot_datetime=slot.datetime_str,
                    site="idata",
                )
            await self.human_delay(0.3, 0.5)

            # Select time
            self.flow_state = IDataFlowState.SELECTING_TIME
            time_selected = await self._select_time(page, slot.time)
            if not time_selected:
                raise SlotNotAvailableError(
                    "Failed to select time slot - may no longer be available",
                    slot_id=slot.id,
                    slot_datetime=slot.datetime_str,
                    site="idata",
                )
            await self.human_delay(0.3, 0.5)

            # Fill applicant form
            self.flow_state = IDataFlowState.FILLING_FORM
            await self._fill_applicant_form(page, applicant)
            await self.human_delay(0.5, 1.0)

            # Confirm booking
            self.flow_state = IDataFlowState.CONFIRMING
            await self._confirm_booking(page)

            await page.wait_for_load_state("networkidle")
            session.increment_requests()

            # Check for errors
            error = await self._get_page_error(page)
            if error:
                classification = IDataErrorClassifier.classify(error)
                if classification.error_type == "SLOT_TAKEN":
                    raise SlotNotAvailableError(
                        f"Slot taken: {error}",
                        slot_id=slot.id,
                        slot_datetime=slot.datetime_str,
                        site="idata",
                    )
                raise BookingError(error)

            # Check for success
            if await self._check_booking_result(page):
                self.flow_state = IDataFlowState.COMPLETED
                confirmation = await self._get_confirmation(page)

                # Take screenshot
                screenshot_path = await self.take_screenshot(page, "idata_confirmation")

                completed_at = datetime.utcnow()
                duration = (completed_at - start_time).total_seconds()
                self.metrics.record_booking(success=True, duration_ms=duration * 1000)

                logger.info(
                    "idata_booking_success",
                    session_id=session.id,
                    confirmation_number=confirmation.get("number"),
                    duration_seconds=duration,
                )

                return BookingResult(
                    status=BookingResultStatus.SUCCESS,
                    confirmation_number=confirmation.get("number"),
                    appointment_date=slot.date,
                    appointment_time=slot.time,
                    appointment_location=slot.location,
                    screenshot_path=screenshot_path,
                    started_at=start_time,
                    completed_at=completed_at,
                    duration_seconds=duration,
                )

            # Booking failed
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

        except SlotNotAvailableError:
            raise
        except BookingError as e:
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
            completed_at = datetime.utcnow()
            duration = (completed_at - start_time).total_seconds()
            self.metrics.record_booking(success=False, duration_ms=duration * 1000)
            self.metrics.record_error("booking_error")

            logger.error(
                "idata_booking_error",
                session_id=session.id,
                error=str(e),
            )

            # Take error screenshot
            await self.take_screenshot(page, "idata_booking_error")

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

        iDATA requires less human simulation than VFS due to simpler anti-bot.

        Args:
            page: Playwright page instance.
            account: Bot account with credentials.
        """
        # Email
        await page.fill(self.selectors.EMAIL_INPUT, account.email)
        await asyncio.sleep(random.uniform(0.2, 0.4))

        # Password
        await page.fill(self.selectors.PASSWORD_INPUT, account.password)
        await asyncio.sleep(random.uniform(0.2, 0.4))

    async def _handle_login_captcha(
        self,
        page: Page,
        session: AdapterSession,
    ) -> bool:
        """
        Handle CAPTCHA on login page.

        iDATA uses reCAPTCHA v2 only on login page.

        Args:
            page: Playwright page instance.
            session: Adapter session for tracking.

        Returns:
            True if CAPTCHA solved successfully.
        """
        if not self.captcha_solver:
            logger.warning(
                "idata_captcha_solver_not_available",
            )
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
                    "idata_captcha_solved",
                    session_id=session.id,
                )
                return True

        except Exception as e:
            self.metrics.record_captcha(success=False)
            logger.error(
                "idata_captcha_solve_failed",
                session_id=session.id,
                error=str(e),
            )

        return False

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

        # Check URL for calendar/appointment
        if "/calendar" in page.url or "/appointment" in page.url:
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

    async def _api_slot_search(
        self,
        country: str,
        visa_code: str,
        center_code: str,
        criteria: SlotSearchCriteria,
    ) -> list[Slot]:
        """
        Search for slots using API (fast path).

        Args:
            country: Country code.
            visa_code: Visa type code.
            center_code: Center code.
            criteria: Search criteria.

        Returns:
            List of found slots.
        """
        slots: list[Slot] = []

        if not self.api_client:
            return slots

        try:
            # Check available dates
            available_dates = await self.api_client.check_available_dates(
                country=country,
                visa_type=visa_code,
                center_id=center_code,
            )

            if not available_dates:
                return slots

            # Filter by date range
            from_date = criteria.from_date
            to_date = criteria.to_date or (date.today() + timedelta(days=180))

            for date_info in available_dates:
                try:
                    slot_date = datetime.strptime(date_info["date"], "%Y-%m-%d").date()
                except ValueError:
                    continue

                if slot_date < from_date or slot_date > to_date:
                    continue

                # Check weekend exclusion
                if criteria.exclude_weekends and slot_date.weekday() >= 5:
                    continue

                # Get times for this date
                times = await self.api_client.get_available_times(
                    country=country,
                    date_str=date_info["date"],
                    center_id=center_code,
                )

                for time_info in times:
                    time_str = time_info.get("time", "09:00")
                    slot_id = time_info.get("id", f"idata_{slot_date.isoformat()}_{time_str}")

                    slots.append(
                        Slot(
                            id=slot_id,
                            date=slot_date,
                            time=time_str,
                            location=criteria.city or "istanbul",
                            category=criteria.category,
                            status=SlotStatus.AVAILABLE,
                            raw_data={
                                "center_id": center_code,
                                "visa_code": visa_code,
                                "from_api": True,
                            },
                        )
                    )

                # Limit slots per date
                if len(slots) >= 10:
                    break

        except Exception as e:
            logger.warning(
                "idata_api_slot_search_error",
                error=str(e),
            )

        return slots

    async def _browser_slot_search(
        self,
        session: AdapterSession,
        criteria: SlotSearchCriteria,
    ) -> list[Slot]:
        """
        Search for slots using browser (fallback path).

        Args:
            session: Adapter session.
            criteria: Search criteria.

        Returns:
            List of found slots.
        """
        page = session.page
        slots: list[Slot] = []

        try:
            # Navigate to calendar page
            calendar_url = self.endpoints.get_endpoint(criteria.country, "calendar")
            await page.goto(calendar_url, wait_until="networkidle")
            session.increment_requests()

            # Select visa type
            country_key = self.endpoints.get_country_key(criteria.country)
            visa_code = self.VISA_TYPES.get(country_key, {}).get(
                criteria.category, criteria.category
            )
            await self._select_visa_type(page, criteria.country, visa_code)
            await asyncio.sleep(0.5)

            # Select center
            center = criteria.city or "istanbul"
            center_code = self.CENTERS.get(country_key, {}).get(center.lower(), center)
            await self._select_center(page, criteria.country, center_code)
            await asyncio.sleep(0.5)

            # Scan calendar
            slots = await self._scan_calendar(page, criteria, center)

        except Exception as e:
            logger.warning(
                "idata_browser_slot_search_error",
                error=str(e),
            )

        return slots

    async def _scan_calendar(
        self,
        page: Page,
        criteria: SlotSearchCriteria,
        center: str,
        max_months: int = 6,
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
        target_to = criteria.to_date or (date.today() + timedelta(days=180))

        for _ in range(max_months):
            # Find available date cells
            available_cells = await page.query_selector_all(self.selectors.AVAILABLE_DAY)

            for cell in available_cells:
                try:
                    date_str = await cell.get_attribute("data-date")
                    if not date_str:
                        continue

                    slot_date = datetime.strptime(date_str, "%Y-%m-%d").date()

                    # Check date range
                    if slot_date < target_from or slot_date > target_to:
                        continue

                    # Check weekend exclusion
                    if criteria.exclude_weekends and slot_date.weekday() >= 5:
                        continue

                    # Add slot
                    slots.append(
                        Slot(
                            id=f"idata_{slot_date.isoformat()}_00",
                            date=slot_date,
                            time="09:00",  # Default time
                            location=center,
                            category=criteria.category,
                            status=SlotStatus.AVAILABLE,
                            raw_data={"from_browser": True},
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

    # -------------------------------------------------------------------------
    # Booking Helpers
    # -------------------------------------------------------------------------

    async def _select_visa_type(self, page: Page, country: str, visa_type: str) -> None:
        """
        Select visa type from dropdown.

        Args:
            page: Playwright page instance.
            country: Country code.
            visa_type: Visa type code or name.
        """
        country_key = self.endpoints.get_country_key(country)
        visa_code = self.VISA_TYPES.get(country_key, {}).get(visa_type, visa_type)

        try:
            await page.select_option(
                self.selectors.VISA_TYPE_SELECT,
                value=visa_code,
            )
            await asyncio.sleep(0.5)  # Wait for form update
        except Exception as e:
            logger.warning(
                "idata_visa_type_selection_error",
                visa_type=visa_type,
                error=str(e),
            )

    async def _select_center(self, page: Page, country: str, center_id: str) -> None:
        """
        Select center from dropdown.

        Args:
            page: Playwright page instance.
            country: Country code.
            center_id: Center ID code.
        """
        try:
            await page.select_option(
                self.selectors.CENTER_SELECT,
                value=center_id,
            )
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(
                "idata_center_selection_error",
                center_id=center_id,
                error=str(e),
            )

    async def _select_date(self, page: Page, target_date: date) -> bool:
        """
        Select date from calendar.

        Args:
            page: Playwright page instance.
            target_date: Date to select.

        Returns:
            True if date was selected.
        """
        try:
            # Wait for calendar
            await page.wait_for_selector(
                self.selectors.CALENDAR,
                timeout=10000,
            )

            # Try to click on the date cell
            date_selector = (
                f'td[data-date="{target_date.isoformat()}"], '
                f'.day[data-date="{target_date.strftime("%d/%m/%Y")}"]'
            )

            date_cell = await page.query_selector(date_selector)

            if date_cell:
                await date_cell.click()
                await asyncio.sleep(0.5)
                return True

            # Try alternative: input the date directly
            date_input = await page.query_selector('input[name*="Date"], #AppointmentDate')
            if date_input:
                await date_input.fill(target_date.strftime("%d/%m/%Y"))
                await asyncio.sleep(0.5)
                return True

        except Exception as e:
            logger.warning(
                "idata_date_selection_error",
                date=target_date.isoformat(),
                error=str(e),
            )

        return False

    async def _select_time(self, page: Page, time_str: str) -> bool:
        """
        Select time slot.

        Args:
            page: Playwright page instance.
            time_str: Time string to select.

        Returns:
            True if time was selected.
        """
        try:
            time_select = await page.query_selector(self.selectors.TIME_SELECT)

            if time_select:
                # Find matching option
                options = await page.query_selector_all(
                    f"{self.selectors.TIME_SELECT} option"
                )

                for option in options:
                    option_text = await option.inner_text()
                    if time_str in option_text:
                        value = await option.get_attribute("value")
                        await page.select_option(
                            self.selectors.TIME_SELECT,
                            value=value,
                        )
                        await asyncio.sleep(0.5)
                        return True

                # If no exact match, select first available
                if options:
                    value = await options[0].get_attribute("value")
                    if value:
                        await page.select_option(
                            self.selectors.TIME_SELECT,
                            value=value,
                        )
                        return True

        except Exception as e:
            logger.warning(
                "idata_time_selection_error",
                time=time_str,
                error=str(e),
            )

        return False

    async def _fill_applicant_form(
        self,
        page: Page,
        applicant: dict[str, Any],
    ) -> None:
        """
        Fill applicant details form.

        Args:
            page: Playwright page instance.
            applicant: Applicant data dictionary.
        """
        field_mapping = {
            "first_name": (self.selectors.FIRST_NAME, applicant.get("first_name", "")),
            "last_name": (self.selectors.LAST_NAME, applicant.get("last_name", "")),
            "birth_date": (self.selectors.BIRTH_DATE, applicant.get("birth_date", "")),
            "passport_number": (
                self.selectors.PASSPORT_NUMBER,
                applicant.get("passport_number", ""),
            ),
            "passport_expiry": (
                self.selectors.PASSPORT_EXPIRY,
                applicant.get("passport_expiry", ""),
            ),
            "phone": (self.selectors.PHONE, applicant.get("phone", "")),
            "email": (self.selectors.EMAIL_APPLICANT, applicant.get("email", "")),
        }

        for field_key, (selector, value) in field_mapping.items():
            if value:
                try:
                    field = await page.query_selector(selector)
                    if field:
                        await field.fill(str(value))
                        await asyncio.sleep(random.uniform(0.1, 0.3))
                except Exception as e:
                    logger.warning(
                        "idata_form_field_error",
                        field=field_key,
                        error=str(e),
                    )

        # Nationality dropdown
        nationality = applicant.get("nationality")
        if nationality:
            try:
                nationality_select = await page.query_selector(self.selectors.NATIONALITY)
                if nationality_select:
                    await page.select_option(
                        self.selectors.NATIONALITY,
                        value=nationality,
                    )
            except Exception as e:
                logger.warning(
                    "idata_nationality_selection_error",
                    error=str(e),
                )

    async def _confirm_booking(self, page: Page) -> None:
        """
        Confirm booking by checking terms and submitting.

        Args:
            page: Playwright page instance.
        """
        # Check terms checkbox
        checkbox = await page.query_selector(self.selectors.TERMS_CHECKBOX)
        if checkbox:
            is_checked = await checkbox.is_checked()
            if not is_checked:
                await checkbox.click()
                await asyncio.sleep(0.3)

        await asyncio.sleep(random.uniform(0.3, 0.5))

        # Submit
        await page.click(self.selectors.SUBMIT_BUTTON)

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
        if "confirmation" in page.url.lower() or "success" in page.url.lower():
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
                "idata_get_confirmation_error",
                error=str(e),
            )

        return confirmation

    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------

    async def close(self) -> None:
        """Close adapter resources."""
        if self.api_client:
            await self.api_client.close()
            self.api_client = None


# =============================================================================
# Slot Monitor
# =============================================================================


class IDataSlotMonitor:
    """
    Continuous slot monitoring using API.

    Provides fast, efficient slot monitoring for iDATA
    without requiring browser sessions.
    """

    def __init__(self, proxy_manager: ProxyManager | None = None) -> None:
        """
        Initialize slot monitor.

        Args:
            proxy_manager: Proxy manager for rotating proxies.
        """
        self.proxy_manager = proxy_manager
        self.running = False
        self.found_slots: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def start_monitoring(
        self,
        country: str,
        visa_type: str,
        centers: list[str],
        interval_seconds: int = 30,
    ) -> None:
        """
        Start slot monitoring.

        Args:
            country: Country code (de or it).
            visa_type: Visa type name.
            centers: List of center names to monitor.
            interval_seconds: Check interval in seconds.
        """
        self.running = True
        api = IDataAPIClient()

        try:
            while self.running:
                for center in centers:
                    try:
                        # Get codes
                        country_key = IDataEndpoints.get_country_key(country)
                        visa_code = IDataAdapter.VISA_TYPES.get(country_key, {}).get(
                            visa_type, visa_type
                        )
                        center_code = IDataAdapter.CENTERS.get(country_key, {}).get(
                            center.lower(), center
                        )

                        # Check for available dates
                        dates = await api.check_available_dates(
                            country=country,
                            visa_type=visa_code,
                            center_id=center_code,
                        )

                        if dates:
                            await self.found_slots.put(
                                {
                                    "country": country,
                                    "visa_type": visa_type,
                                    "center": center,
                                    "dates": dates,
                                    "timestamp": datetime.utcnow(),
                                }
                            )

                        # Small delay between centers
                        await asyncio.sleep(random.uniform(1, 3))

                    except Exception as e:
                        logger.warning(
                            "idata_monitor_check_error",
                            center=center,
                            error=str(e),
                        )

                # Wait for next cycle
                await asyncio.sleep(interval_seconds)

        finally:
            await api.close()

    async def stop_monitoring(self) -> None:
        """Stop slot monitoring."""
        self.running = False

    async def get_next_slot(self, timeout: float | None = None) -> dict[str, Any] | None:
        """
        Get next found slot.

        Args:
            timeout: Maximum wait time in seconds.

        Returns:
            Slot data dictionary or None if timeout.
        """
        try:
            return await asyncio.wait_for(
                self.found_slots.get(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return None


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "IDataAdapter",
    "IDataFlowState",
    "IDataEndpoints",
    "IDataSelectors",
    "IDataErrorPatterns",
    "IDataErrorClassifier",
    "IDataErrorClassification",
    "IDataAPIClient",
    "IDataSlotMonitor",
]
