"""
VISE OS BLS Spain Adapter.

BLS Spain booking adapter implementing keyboard-only form input
for Spain Schengen visa appointments. Paste is blocked on all input
fields, requiring character-by-character keyboard simulation.

Features:
- Keyboard-only typing engine (paste blocked)
- Human-like inter-keystroke timing
- reCAPTCHA v2 handling on form submit
- Session-based rate limiting
- 20 minute session timeout

Technical Profile:
- Anti-Bot: Paste blocking + reCAPTCHA
- Rate Limiting: Session-based, moderate
- Session Timeout: 20 minutes
- CAPTCHA: reCAPTCHA v2 on form submit
- TLS Fingerprinting: Minimal

Usage:
    from src.bot.adapters.bls import BLSAdapter
    from src.bot.adapters import AdapterConfig, SiteCode

    config = AdapterConfig(
        site_code=SiteCode.BLS,
        base_url="https://blsspainvisa.com/turkey",
    )

    adapter = BLSAdapter(
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


class BLSFlowState(Enum):
    """BLS booking flow states."""

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


# =============================================================================
# BLS URL and Endpoint Builder
# =============================================================================


class BLSURLBuilder:
    """BLS Spain URL builder."""

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
        """Get login URL for Turkey."""
        return f"{cls.TURKEY_BASE}{cls.ENDPOINTS['login']}"

    @classmethod
    def get_appointment_url(cls) -> str:
        """Get appointment booking URL."""
        return f"{cls.TURKEY_BASE}{cls.ENDPOINTS['appointment']}"

    @classmethod
    def get_calendar_url(cls) -> str:
        """Get calendar URL."""
        return f"{cls.TURKEY_BASE}{cls.ENDPOINTS['calendar']}"

    @classmethod
    def get_confirmation_url(cls) -> str:
        """Get confirmation URL."""
        return f"{cls.TURKEY_BASE}{cls.ENDPOINTS['confirmation']}"

    @classmethod
    def get_city_code(cls, city: str) -> str:
        """Get city code from city name."""
        return cls.CITIES.get(city.lower(), city.upper())

    @classmethod
    def get_visa_code(cls, category: str) -> str:
        """Get visa code from category name."""
        return cls.VISA_CATEGORIES.get(category.lower(), category.upper())


# =============================================================================
# BLS Selectors
# =============================================================================


class BLSSelectors:
    """CSS selectors for BLS Spain pages."""

    # Login page
    EMAIL_INPUT = 'input[name="email"], input[type="email"], #email'
    PASSWORD_INPUT = 'input[name="password"], input[type="password"], #password'
    LOGIN_BUTTON = 'button[type="submit"], .login-btn, #loginBtn'

    # Category Selection
    VISA_CATEGORY = 'select[name="category"], #visaCategory'
    VISA_SUBCATEGORY = 'select[name="subcategory"], #visaSubcategory'

    # Center Selection
    CENTER_SELECT = 'select[name="center"], #centerSelect'
    CITY_SELECT = 'select[name="city"], #citySelect'

    # Calendar
    CALENDAR = ".calendar, #appointmentCalendar"
    AVAILABLE_DATE = ".available, .open-date"
    SELECTED_DATE = ".selected, .active-date"
    NEXT_MONTH = ".next-month, .calendar-next"
    PREV_MONTH = ".prev-month, .calendar-prev"

    # Time Selection
    TIME_SLOTS = ".time-slot, .slot-time"
    TIME_SLOT_AVAILABLE = ".time-slot:not(.unavailable)"

    # Applicant Form - KEYBOARD ONLY INPUT REQUIRED
    FIRST_NAME = '#firstName, input[name="firstName"]'
    LAST_NAME = '#lastName, input[name="lastName"]'
    BIRTH_DATE = '#birthDate, input[name="birthDate"]'
    NATIONALITY = '#nationality, select[name="nationality"]'
    PASSPORT_NUMBER = '#passportNumber, input[name="passportNumber"]'
    PASSPORT_ISSUE_DATE = '#passportIssueDate, input[name="passportIssueDate"]'
    PASSPORT_EXPIRY_DATE = '#passportExpiryDate, input[name="passportExpiryDate"]'
    PHONE = '#phone, input[name="phone"]'
    EMAIL_APPLICANT = '#applicantEmail, input[name="applicantEmail"]'
    ADDRESS = '#address, textarea[name="address"]'

    # CAPTCHA
    RECAPTCHA = ".g-recaptcha, #recaptcha"

    # Confirmation
    TERMS_CHECKBOX = '#termsAccepted, input[name="terms"]'
    SUBMIT_BUTTON = '#submitBtn, button[type="submit"]'

    # Result
    SUCCESS_MESSAGE = ".success, .confirmation-success"
    ERROR_MESSAGE = ".error, .alert-error"
    CONFIRMATION_NUMBER = ".confirmation-number, #bookingReference"

    # Dashboard indicators (post-login)
    DASHBOARD_INDICATORS = [
        ".user-info",
        ".logout",
        "#visaCategory",
        ".welcome",
        ".user-menu",
        ".appointment-form",
    ]


# =============================================================================
# Keyboard Typing Engine (Paste Bypass)
# =============================================================================


class BLSKeyboardTyper:
    """
    BLS keyboard-only typing engine.

    BLS Spain blocks paste on all input fields. This class provides
    character-by-character keyboard simulation with human-like timing.
    """

    # Inter-keystroke interval (IKI)
    IKI_MIN = 0.05  # 50ms minimum
    IKI_MAX = 0.15  # 150ms maximum
    IKI_MEAN = 0.08  # 80ms average

    # Special characters requiring Shift key
    SHIFT_CHARS = '~!@#$%^&*()_+{}|:"<>?ABCDEFGHIJKLMNOPQRSTUVWXYZ'

    def __init__(self, page: Page) -> None:
        """
        Initialize keyboard typer.

        Args:
            page: Playwright page instance.
        """
        self.page = page

    async def type_field(
        self,
        selector: str,
        text: str,
        clear_first: bool = True,
    ) -> None:
        """
        Type text into a field using keyboard (paste bypass).

        Args:
            selector: CSS selector for the input field.
            text: Text to type.
            clear_first: Whether to clear the field first.
        """
        # Focus field
        await self.page.click(selector)
        await asyncio.sleep(random.uniform(0.1, 0.3))

        # Clear if needed
        if clear_first:
            await self._clear_field()

        # Type character by character
        for i, char in enumerate(text):
            await self._type_character(char)

            # Variable delay between keystrokes
            delay = self._get_keystroke_delay(text, i)
            await asyncio.sleep(delay)

    async def _clear_field(self) -> None:
        """Clear current field content."""
        # Select all and delete
        await self.page.keyboard.press("Control+a")
        await asyncio.sleep(0.05)
        await self.page.keyboard.press("Delete")
        await asyncio.sleep(0.05)

    async def _type_character(self, char: str) -> None:
        """
        Type a single character.

        Args:
            char: Character to type.
        """
        if char in self.SHIFT_CHARS:
            # Shift + key
            await self.page.keyboard.down("Shift")
            await self.page.keyboard.press(char.lower() if char.isalpha() else char)
            await self.page.keyboard.up("Shift")
        else:
            # Normal key
            await self.page.keyboard.press(char)

    def _get_keystroke_delay(self, text: str, index: int) -> float:
        """
        Get context-aware keystroke delay.

        Args:
            text: Full text being typed.
            index: Current character index.

        Returns:
            Delay in seconds.
        """
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

    async def type_passport_number(self, selector: str, passport: str) -> None:
        """
        Type passport number with special handling.

        Passport numbers are typed slower as users verify each character.

        Args:
            selector: CSS selector for the input field.
            passport: Passport number to type.
        """
        await self.page.click(selector)
        await asyncio.sleep(0.2)

        await self._clear_field()

        for char in passport.upper():
            await self._type_character(char)
            # Slower for passport (verifying each char)
            await asyncio.sleep(random.uniform(0.1, 0.2))

    async def type_phone_number(self, selector: str, phone: str) -> None:
        """
        Type phone number with special handling.

        Args:
            selector: CSS selector for the input field.
            phone: Phone number to type.
        """
        await self.page.click(selector)
        await asyncio.sleep(0.2)

        await self._clear_field()

        # Remove non-digits for cleaner input
        digits = "".join(filter(str.isdigit, phone))

        for i, digit in enumerate(digits):
            await self.page.keyboard.press(digit)

            # Pause after country code and area code
            if i in [2, 5]:  # After +90 and area code
                await asyncio.sleep(random.uniform(0.15, 0.25))
            else:
                await asyncio.sleep(random.uniform(0.05, 0.1))

    async def type_date(
        self,
        selector: str,
        date_str: str,
        date_format: str = "DD/MM/YYYY",
    ) -> None:
        """
        Type date with format awareness.

        Args:
            selector: CSS selector for the input field.
            date_str: Date string to type.
            date_format: Expected date format.
        """
        await self.page.click(selector)
        await asyncio.sleep(0.2)

        await self._clear_field()

        # Type with pauses at separators
        for char in date_str:
            await self.page.keyboard.press(char)

            if char in ["/", "-", "."]:
                await asyncio.sleep(random.uniform(0.1, 0.2))
            else:
                await asyncio.sleep(random.uniform(0.05, 0.1))


# =============================================================================
# Error Patterns and Classifier
# =============================================================================


class BLSErrorPatterns:
    """Error detection patterns for BLS Spain."""

    SESSION_EXPIRED = [
        r"session.*expired",
        r"please.*login",
    ]

    SLOT_TAKEN = [
        r"slot.*taken",
        r"no.*available",
        r"already.*booked",
    ]

    VALIDATION = [
        r"invalid",
        r"required.*field",
        r"format.*incorrect",
    ]

    CAPTCHA_FAILED = [
        r"captcha.*failed",
        r"verification.*failed",
    ]


@dataclass
class BLSErrorClassification:
    """Classification result for BLS errors."""

    error_type: str
    retriable: bool
    cooldown_seconds: int


class BLSErrorClassifier:
    """Classifies BLS errors for appropriate handling."""

    ERROR_PATTERNS = {
        "session_expired": BLSErrorPatterns.SESSION_EXPIRED,
        "slot_taken": BLSErrorPatterns.SLOT_TAKEN,
        "validation": BLSErrorPatterns.VALIDATION,
        "captcha_failed": BLSErrorPatterns.CAPTCHA_FAILED,
    }

    @staticmethod
    def classify(error_message: str) -> BLSErrorClassification:
        """
        Classify an error message.

        Args:
            error_message: Error message to classify.

        Returns:
            BLSErrorClassification with handling instructions.
        """
        error_lower = error_message.lower()

        for error_type, patterns in BLSErrorClassifier.ERROR_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, error_lower):
                    return BLSErrorClassification(
                        error_type=error_type.upper(),
                        retriable=error_type not in ["validation"],
                        cooldown_seconds=60 if error_type == "captcha_failed" else 0,
                    )

        return BLSErrorClassification(
            error_type="UNKNOWN",
            retriable=True,
            cooldown_seconds=30,
        )


# =============================================================================
# BLS Adapter Main Class
# =============================================================================


class BLSAdapter(BaseSiteAdapter):
    """
    BLS Spain booking adapter.

    Implements the keyboard-only booking flow for BLS Spain
    visa appointments due to paste blocking on input fields.

    Supports Spain Schengen visa appointments for Turkey.

    Attributes:
        flow_state: Current state in the booking flow.
        selectors: BLS-specific CSS selectors.
        url_builder: BLS URL builder.
        keyboard_typer: Keyboard typing engine for paste bypass.
    """

    # Timeouts (milliseconds)
    PAGE_LOAD_TIMEOUT = 45000  # 45 seconds
    ELEMENT_TIMEOUT = 20000  # 20 seconds

    def __init__(
        self,
        config: AdapterConfig | None = None,
        stealth_engine: StealthEngine | None = None,
        proxy_manager: ProxyManager | None = None,
        captcha_solver: CaptchaSolver | None = None,
    ) -> None:
        """
        Initialize the BLS adapter.

        Args:
            config: Adapter configuration. Defaults to BLS config if None.
            stealth_engine: Browser stealth engine.
            proxy_manager: Proxy rotation manager.
            captcha_solver: CAPTCHA solving service.
        """
        if config is None:
            config = AdapterConfig(
                site_code=SiteCode.BLS,
                base_url=BLSURLBuilder.TURKEY_BASE,
            )

        super().__init__(
            config=config,
            stealth_engine=stealth_engine,
            proxy_manager=proxy_manager,
            captcha_solver=captcha_solver,
        )

        self.flow_state = BLSFlowState.INITIAL
        self.selectors = BLSSelectors
        self.url_builder = BLSURLBuilder
        self.keyboard_typer: BLSKeyboardTyper | None = None

        logger.info(
            "bls_adapter_initialized",
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
        Authenticate with BLS Spain.

        Uses keyboard-only input for credentials due to paste blocking.

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
        self.flow_state = BLSFlowState.NAVIGATING

        # Initialize keyboard typer
        self.keyboard_typer = BLSKeyboardTyper(page)

        logger.info(
            "bls_login_started",
            session_id=session.id,
            account_id=account.id,
        )

        try:
            # Navigate to login page
            login_url = self.url_builder.get_login_url()
            await page.goto(login_url, wait_until="networkidle")
            session.increment_requests()

            # Verify we're on login page
            self.flow_state = BLSFlowState.LOGIN_PAGE
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
                    site="bls",
                )

            # Fill credentials using KEYBOARD ONLY
            self.flow_state = BLSFlowState.AUTHENTICATING
            await self._fill_login_form(page, account)

            # Submit login
            await page.click(self.selectors.LOGIN_BUTTON)
            await page.wait_for_load_state("networkidle")
            session.increment_requests()

            # Verify login success
            if await self._is_logged_in(page):
                self.flow_state = BLSFlowState.AUTHENTICATED
                session.is_authenticated = True
                session.login_time = datetime.utcnow()
                self.metrics.record_login(success=True)

                logger.info(
                    "bls_login_success",
                    session_id=session.id,
                    account_id=account.id,
                )
                return True

            # Check for error messages
            error = await self._get_page_error(page)
            if error:
                if self._check_ban_indicators(error):
                    self.metrics.record_login(success=False)
                    raise AccountBannedError(
                        f"BLS account banned: {error}",
                        account_id=account.id,
                        ban_reason=error,
                        site="bls",
                    )

                self.metrics.record_login(success=False)
                raise LoginFailedError(
                    f"BLS login failed: {error}",
                    site="bls",
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
                "bls_login_error",
                session_id=session.id,
                error=str(e),
            )
            raise LoginFailedError(
                f"BLS login failed: {e}",
                site="bls",
                account_id=account.id,
            ) from e

    async def search_slots(
        self,
        session: AdapterSession,
        criteria: SlotSearchCriteria,
    ) -> list[Slot]:
        """
        Search for available appointment slots.

        Navigates calendar and finds available dates/times.

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
                site="bls",
                step="search_slots",
            )

        logger.info(
            "bls_slot_search_started",
            session_id=session.id,
            country=criteria.country,
            category=criteria.category,
        )

        try:
            # Navigate to appointment page
            self.flow_state = BLSFlowState.SELECTING_CATEGORY
            await page.goto(
                self.url_builder.get_appointment_url(),
                wait_until="networkidle",
            )
            session.increment_requests()

            # Select visa category
            await self._select_category(page, criteria.category)
            await self.human_delay(0.3, 0.5)

            # Select center
            self.flow_state = BLSFlowState.SELECTING_CENTER
            center = criteria.city or "istanbul"
            await self._select_center(page, center)
            await self.human_delay(0.3, 0.5)

            # Scan calendar for available slots
            self.flow_state = BLSFlowState.CHECKING_SLOTS
            slots = await self._scan_calendar(page, criteria, center)

            # Record metrics
            duration_ms = (time.time() - start_time) * 1000
            self.metrics.record_search(len(slots), duration_ms)

            if slots:
                self.flow_state = BLSFlowState.SLOT_FOUND
                logger.info(
                    "bls_slots_found",
                    session_id=session.id,
                    count=len(slots),
                    duration_ms=duration_ms,
                )
            else:
                logger.info(
                    "bls_no_slots_found",
                    session_id=session.id,
                    criteria=criteria.to_dict(),
                )
                raise NoSlotsFoundError(
                    "No available slots found matching criteria",
                    site="bls",
                    search_criteria=criteria.to_dict(),
                )

            return slots

        except NoSlotsFoundError:
            raise
        except Exception as e:
            self.metrics.record_error("search_error")
            logger.error(
                "bls_slot_search_error",
                session_id=session.id,
                error=str(e),
            )
            raise SiteAdapterError(
                f"BLS slot search failed: {e}",
                site="bls",
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

        Uses keyboard-only input for form filling due to paste blocking.
        Handles reCAPTCHA on form submission.

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
                site="bls",
                step="book_slot",
            )

        # Ensure keyboard typer is initialized
        if not self.keyboard_typer:
            self.keyboard_typer = BLSKeyboardTyper(page)

        logger.info(
            "bls_booking_started",
            session_id=session.id,
            slot_id=slot.id,
            slot_date=slot.date.isoformat(),
        )

        try:
            # Fill applicant form (KEYBOARD ONLY)
            self.flow_state = BLSFlowState.FILLING_FORM
            await self._fill_applicant_form(page, applicant)
            await self.human_delay(0.5, 1.0)

            # Handle CAPTCHA if present
            captcha_element = await page.query_selector(self.selectors.RECAPTCHA)
            if captcha_element:
                self.flow_state = BLSFlowState.SOLVING_CAPTCHA
                captcha_solved = await self._handle_captcha(page, session)
                if not captcha_solved:
                    raise CaptchaError(
                        "Failed to solve CAPTCHA",
                        captcha_type="recaptcha_v2",
                    )

            # Submit booking
            self.flow_state = BLSFlowState.CONFIRMING
            await self._submit_booking(page)

            await page.wait_for_load_state("networkidle")
            session.increment_requests()

            # Check for errors
            error = await self._get_page_error(page)
            if error:
                classification = BLSErrorClassifier.classify(error)
                if classification.error_type == "SLOT_TAKEN":
                    raise SlotNotAvailableError(
                        f"Slot taken: {error}",
                        slot_id=slot.id,
                        slot_datetime=slot.datetime_str,
                        site="bls",
                    )
                raise BookingError(error)

            # Check for success
            if await self._check_booking_result(page):
                self.flow_state = BLSFlowState.COMPLETED
                confirmation = await self._get_confirmation(page)

                # Take screenshot
                screenshot_path = await self.take_screenshot(page, "bls_confirmation")

                completed_at = datetime.utcnow()
                duration = (completed_at - start_time).total_seconds()
                self.metrics.record_booking(success=True, duration_ms=duration * 1000)

                logger.info(
                    "bls_booking_success",
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
            self.flow_state = BLSFlowState.FAILED
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

        except (SlotNotAvailableError, CaptchaError):
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
                "bls_booking_error",
                session_id=session.id,
                error=str(e),
            )

            # Take error screenshot
            await self.take_screenshot(page, "bls_booking_error")

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
        Fill login form with credentials using KEYBOARD ONLY.

        Args:
            page: Playwright page instance.
            account: Bot account with credentials.
        """
        if not self.keyboard_typer:
            self.keyboard_typer = BLSKeyboardTyper(page)

        # Email - KEYBOARD ONLY
        await self.keyboard_typer.type_field(
            self.selectors.EMAIL_INPUT,
            account.email,
        )
        await asyncio.sleep(random.uniform(0.3, 0.6))

        # Password - KEYBOARD ONLY
        await self.keyboard_typer.type_field(
            self.selectors.PASSWORD_INPUT,
            account.password,
        )
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

    async def _select_category(self, page: Page, category: str) -> None:
        """
        Select visa category from dropdown.

        Args:
            page: Playwright page instance.
            category: Visa category name.
        """
        category_code = self.url_builder.get_visa_code(category)

        try:
            await page.wait_for_selector(
                self.selectors.VISA_CATEGORY,
                timeout=self.ELEMENT_TIMEOUT,
            )

            await page.select_option(
                self.selectors.VISA_CATEGORY,
                value=category_code,
            )
            await asyncio.sleep(1)  # Wait for subcategory to load

            # Select subcategory if present
            subcategory = await page.query_selector(self.selectors.VISA_SUBCATEGORY)
            if subcategory:
                options = await page.query_selector_all(
                    f"{self.selectors.VISA_SUBCATEGORY} option"
                )
                if len(options) > 1:  # More than just placeholder
                    await page.select_option(
                        self.selectors.VISA_SUBCATEGORY,
                        index=1,  # First real option
                    )
                    await asyncio.sleep(0.5)

        except Exception as e:
            logger.warning(
                "bls_category_selection_error",
                category=category,
                error=str(e),
            )

    async def _select_center(self, page: Page, center: str) -> None:
        """
        Select center from dropdown.

        Args:
            page: Playwright page instance.
            center: Center name.
        """
        center_code = self.url_builder.get_city_code(center)

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
                "bls_center_selection_error",
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

                    # Get available time
                    time_slot = await self._get_first_available_time(page)

                    if time_slot:
                        slots.append(
                            Slot(
                                id=f"bls_{slot_date.isoformat()}_{time_slot}",
                                date=slot_date,
                                time=time_slot,
                                location=center,
                                category=criteria.category,
                                status=SlotStatus.AVAILABLE,
                                raw_data={
                                    "center_code": self.url_builder.get_city_code(
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

    async def _get_first_available_time(self, page: Page) -> str | None:
        """
        Get first available time slot.

        Args:
            page: Playwright page instance.

        Returns:
            Time string or None.
        """
        try:
            time_slots = await page.query_selector_all(
                self.selectors.TIME_SLOT_AVAILABLE
            )

            for slot in time_slots:
                time_text = await slot.inner_text()
                if time_text.strip():
                    # Click to select
                    await slot.click()
                    await asyncio.sleep(0.5)
                    return time_text.strip()

        except Exception as e:
            logger.warning(
                "bls_time_selection_error",
                error=str(e),
            )

        return None

    # -------------------------------------------------------------------------
    # Booking Helpers
    # -------------------------------------------------------------------------

    async def _fill_applicant_form(
        self,
        page: Page,
        applicant: dict[str, Any],
    ) -> None:
        """
        Fill applicant form using KEYBOARD ONLY.

        Args:
            page: Playwright page instance.
            applicant: Applicant data dictionary.
        """
        if not self.keyboard_typer:
            self.keyboard_typer = BLSKeyboardTyper(page)

        typer = self.keyboard_typer

        # First Name - KEYBOARD ONLY
        if await self._field_exists(page, self.selectors.FIRST_NAME):
            await typer.type_field(
                self.selectors.FIRST_NAME,
                applicant.get("first_name", ""),
            )
            await asyncio.sleep(random.uniform(0.3, 0.6))

        # Last Name - KEYBOARD ONLY
        if await self._field_exists(page, self.selectors.LAST_NAME):
            await typer.type_field(
                self.selectors.LAST_NAME,
                applicant.get("last_name", ""),
            )
            await asyncio.sleep(random.uniform(0.3, 0.6))

        # Birth Date - KEYBOARD ONLY (DD/MM/YYYY format)
        if await self._field_exists(page, self.selectors.BIRTH_DATE):
            birth_date = self._format_date(applicant.get("birth_date", ""))
            await typer.type_date(
                self.selectors.BIRTH_DATE,
                birth_date,
            )
            await asyncio.sleep(random.uniform(0.3, 0.6))

        # Nationality - Dropdown (can use select)
        if await self._field_exists(page, self.selectors.NATIONALITY):
            nationality = applicant.get("nationality", "TR")
            await page.select_option(
                self.selectors.NATIONALITY,
                value=nationality,
            )
            await asyncio.sleep(random.uniform(0.2, 0.4))

        # Passport Number - KEYBOARD ONLY (special handling)
        if await self._field_exists(page, self.selectors.PASSPORT_NUMBER):
            await typer.type_passport_number(
                self.selectors.PASSPORT_NUMBER,
                applicant.get("passport_number", ""),
            )
            await asyncio.sleep(random.uniform(0.3, 0.6))

        # Passport Issue Date - KEYBOARD ONLY
        if await self._field_exists(page, self.selectors.PASSPORT_ISSUE_DATE):
            if applicant.get("passport_issue_date"):
                issue_date = self._format_date(applicant["passport_issue_date"])
                await typer.type_date(
                    self.selectors.PASSPORT_ISSUE_DATE,
                    issue_date,
                )
                await asyncio.sleep(random.uniform(0.3, 0.6))

        # Passport Expiry Date - KEYBOARD ONLY
        if await self._field_exists(page, self.selectors.PASSPORT_EXPIRY_DATE):
            expiry_date = self._format_date(applicant.get("passport_expiry", ""))
            await typer.type_date(
                self.selectors.PASSPORT_EXPIRY_DATE,
                expiry_date,
            )
            await asyncio.sleep(random.uniform(0.3, 0.6))

        # Phone - KEYBOARD ONLY (special handling)
        if await self._field_exists(page, self.selectors.PHONE):
            await typer.type_phone_number(
                self.selectors.PHONE,
                applicant.get("phone", ""),
            )
            await asyncio.sleep(random.uniform(0.3, 0.6))

        # Email - KEYBOARD ONLY
        if await self._field_exists(page, self.selectors.EMAIL_APPLICANT):
            await typer.type_field(
                self.selectors.EMAIL_APPLICANT,
                applicant.get("email", ""),
            )
            await asyncio.sleep(random.uniform(0.3, 0.6))

        # Address - KEYBOARD ONLY (textarea)
        if await self._field_exists(page, self.selectors.ADDRESS):
            if applicant.get("address"):
                await typer.type_field(
                    self.selectors.ADDRESS,
                    applicant["address"],
                )
                await asyncio.sleep(random.uniform(0.3, 0.6))

    async def _field_exists(self, page: Page, selector: str) -> bool:
        """
        Check if field exists on page.

        Args:
            page: Playwright page instance.
            selector: CSS selector.

        Returns:
            True if field exists.
        """
        try:
            element = await page.query_selector(selector)
            return element is not None
        except Exception:
            return False

    def _format_date(self, date_value: str) -> str:
        """
        Format date to DD/MM/YYYY.

        Args:
            date_value: Input date string.

        Returns:
            Formatted date string.
        """
        if not date_value:
            return ""

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
        except Exception:
            return date_value  # Return as-is if parsing fails

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
            logger.warning("bls_captcha_solver_not_available")
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
                    "bls_captcha_solved",
                    session_id=session.id,
                )
                return True

        except Exception as e:
            self.metrics.record_captcha(success=False)
            logger.error(
                "bls_captcha_solve_failed",
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
            timeout=self.ELEMENT_TIMEOUT,
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
            conf_element = await page.query_selector(
                self.selectors.CONFIRMATION_NUMBER
            )
            if conf_element:
                confirmation["number"] = await conf_element.inner_text()
                confirmation["number"] = confirmation["number"].strip()

            # Get success message
            success_element = await page.query_selector(
                self.selectors.SUCCESS_MESSAGE
            )
            if success_element:
                confirmation["message"] = await success_element.inner_text()

            # Take screenshot
            screenshot_path = (
                f"/tmp/bls_confirmation_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
            )
            await page.screenshot(path=screenshot_path)
            confirmation["screenshot"] = screenshot_path

        except Exception as e:
            logger.warning(
                "bls_get_confirmation_error",
                error=str(e),
            )

        return confirmation


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "BLSAdapter",
    "BLSFlowState",
    "BLSURLBuilder",
    "BLSSelectors",
    "BLSKeyboardTyper",
    "BLSErrorPatterns",
    "BLSErrorClassifier",
    "BLSErrorClassification",
]
