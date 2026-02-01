"""
VISE OS Abstract Base Site Adapter.

Provides the abstract base class and supporting data structures for all
visa appointment booking site adapters. Concrete adapters for VFS Global,
iDATA, BLS Spain, and KKOSMOS extend this class.

Features:
- Abstract methods for login, slot search, and booking flow
- Session management with stealth browser integration
- Retry and timeout configuration
- Metrics collection for monitoring
- Error handling with site-specific exceptions
- Page object model support

Usage:
    from src.bot.adapters.base import (
        BaseSiteAdapter,
        Slot,
        SlotSearchCriteria,
        BookingResult,
    )

    class VFSAdapter(BaseSiteAdapter):
        async def login(self, session, account):
            # VFS-specific login implementation
            ...

        async def search_slots(self, session, criteria):
            # VFS-specific slot search
            ...

        async def book_slot(self, session, slot, applicant):
            # VFS-specific booking flow
            ...
"""

from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncGenerator

import structlog

from src.core.exceptions import (
    AccountBannedError,
    BrowserTimeoutError,
    LoginFailedError,
    NoSlotsFoundError,
    SelectorNotFoundError,
    SiteAdapterError,
    SiteStructureChangedError,
    SlotNotAvailableError,
)

if TYPE_CHECKING:
    from playwright.async_api import Page

    from src.bot.engine.session import SessionManager
    from src.bot.engine.stealth import StealthEngine
    from src.bot.services.account import BotAccount
    from src.bot.services.captcha import CaptchaSolver
    from src.bot.services.proxy import ProxyManager


logger = structlog.get_logger(__name__)


# =============================================================================
# Enums
# =============================================================================


class SiteCode(str, Enum):
    """
    Supported visa booking site codes.

    Each site has its own adapter implementation.
    """

    VFS = "vfs"
    IDATA = "idata"
    BLS = "bls"
    KKOSMOS = "kkosmos"


class SlotStatus(str, Enum):
    """
    Appointment slot availability status.

    - AVAILABLE: Slot is available for booking
    - SELECTED: Slot has been selected (pre-booking hold)
    - BOOKED: Slot has been successfully booked
    - EXPIRED: Slot selection/hold has expired
    - TAKEN: Slot was taken by another user
    """

    AVAILABLE = "available"
    SELECTED = "selected"
    BOOKED = "booked"
    EXPIRED = "expired"
    TAKEN = "taken"


class BookingResultStatus(str, Enum):
    """
    Booking operation result status.

    - SUCCESS: Booking completed successfully
    - FAILED: Booking failed
    - PARTIAL: Partial completion (e.g., slot reserved but payment pending)
    - TIMEOUT: Operation timed out
    """

    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
    TIMEOUT = "timeout"


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class Slot:
    """
    Appointment slot information.

    Represents an available or selected appointment slot from
    a visa booking system.

    Attributes:
        id: Unique slot identifier (site-specific format).
        date: Appointment date.
        time: Appointment time string (HH:MM format).
        location: Appointment location/center.
        category: Visa category for the slot.
        capacity: Remaining capacity (if available).
        status: Current slot status.
        raw_data: Original slot data from the site.
        discovered_at: When the slot was discovered.
        expires_at: When the slot selection expires.
    """

    id: str
    date: date
    time: str
    location: str
    category: str
    capacity: int = 1
    status: SlotStatus = SlotStatus.AVAILABLE
    raw_data: dict[str, Any] = field(default_factory=dict)
    discovered_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None

    @property
    def datetime_str(self) -> str:
        """Get formatted date-time string."""
        return f"{self.date.isoformat()} {self.time}"

    @property
    def is_valid(self) -> bool:
        """Check if slot is still valid for booking."""
        if self.status not in (SlotStatus.AVAILABLE, SlotStatus.SELECTED):
            return False
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "date": self.date.isoformat(),
            "time": self.time,
            "location": self.location,
            "category": self.category,
            "capacity": self.capacity,
            "status": self.status.value,
            "discovered_at": self.discovered_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


@dataclass
class SlotSearchCriteria:
    """
    Criteria for searching available appointment slots.

    Defines the search parameters for finding suitable slots.

    Attributes:
        country: Target country code (ISO 3166-1 alpha-2).
        city: Preferred city/location (optional).
        category: Visa category code.
        from_date: Earliest acceptable date.
        to_date: Latest acceptable date.
        exclude_weekends: Whether to exclude weekend slots.
        min_capacity: Minimum required slot capacity.
        preferred_times: List of preferred time ranges (HH:MM-HH:MM).
        family_size: Number of slots needed for family booking.
        metadata: Additional site-specific search parameters.
    """

    country: str
    category: str
    from_date: date = field(default_factory=date.today)
    to_date: date | None = None
    city: str | None = None
    exclude_weekends: bool = False
    min_capacity: int = 1
    preferred_times: list[str] = field(default_factory=list)
    family_size: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Set default to_date if not provided."""
        if self.to_date is None:
            # Default to 90 days from from_date
            self.to_date = self.from_date + timedelta(days=90)

    def matches_slot(self, slot: Slot) -> bool:
        """
        Check if a slot matches this search criteria.

        Args:
            slot: Slot to check.

        Returns:
            True if slot matches all criteria.
        """
        # Date range check
        if slot.date < self.from_date:
            return False
        if self.to_date and slot.date > self.to_date:
            return False

        # Weekend check
        if self.exclude_weekends and slot.date.weekday() >= 5:
            return False

        # Capacity check
        if slot.capacity < self.min_capacity:
            return False

        # Location check
        if self.city and slot.location.lower() != self.city.lower():
            return False

        # Category check
        if slot.category.lower() != self.category.lower():
            return False

        return True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "country": self.country,
            "category": self.category,
            "city": self.city,
            "from_date": self.from_date.isoformat(),
            "to_date": self.to_date.isoformat() if self.to_date else None,
            "exclude_weekends": self.exclude_weekends,
            "min_capacity": self.min_capacity,
            "preferred_times": self.preferred_times,
            "family_size": self.family_size,
        }


@dataclass
class BookingResult:
    """
    Result of a booking operation.

    Contains confirmation details or error information.

    Attributes:
        status: Booking result status.
        confirmation_number: Appointment confirmation number.
        appointment_date: Confirmed appointment date.
        appointment_time: Confirmed appointment time.
        appointment_location: Appointment location.
        booking_id: Internal booking ID from the site.
        error_code: Error code if failed.
        error_message: Error message if failed.
        screenshot_path: Path to confirmation screenshot.
        pdf_path: Path to confirmation PDF.
        raw_response: Raw response data from the site.
        started_at: When booking started.
        completed_at: When booking completed.
        duration_seconds: Total duration in seconds.
    """

    status: BookingResultStatus
    confirmation_number: str | None = None
    appointment_date: date | None = None
    appointment_time: str | None = None
    appointment_location: str | None = None
    booking_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    screenshot_path: str | None = None
    pdf_path: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    duration_seconds: float = 0.0

    @property
    def is_success(self) -> bool:
        """Check if booking was successful."""
        return self.status == BookingResultStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "status": self.status.value,
            "confirmation_number": self.confirmation_number,
            "appointment_date": (
                self.appointment_date.isoformat() if self.appointment_date else None
            ),
            "appointment_time": self.appointment_time,
            "appointment_location": self.appointment_location,
            "booking_id": self.booking_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "screenshot_path": self.screenshot_path,
            "pdf_path": self.pdf_path,
            "duration_seconds": self.duration_seconds,
        }


# =============================================================================
# Configuration Classes
# =============================================================================


@dataclass
class AdapterTimeouts:
    """
    Timeout configuration for adapter operations.

    All values are in seconds.

    Attributes:
        page_load: Page load timeout.
        navigation: Navigation timeout.
        element_wait: Element visibility wait.
        captcha_solve: CAPTCHA solving timeout.
        form_submit: Form submission timeout.
        slot_search: Slot search timeout.
        booking: Full booking flow timeout.
        payment: Payment processing timeout.
    """

    page_load: int = 30
    navigation: int = 30
    element_wait: int = 10
    captcha_solve: int = 120
    form_submit: int = 30
    slot_search: int = 60
    booking: int = 300
    payment: int = 180


@dataclass
class AdapterRetryConfig:
    """
    Retry configuration for adapter operations.

    Attributes:
        max_login_attempts: Maximum login retry attempts.
        max_search_attempts: Maximum slot search attempts.
        max_booking_attempts: Maximum booking attempts.
        retry_delay_seconds: Base delay between retries.
        backoff_multiplier: Exponential backoff multiplier.
        max_retry_delay_seconds: Maximum retry delay.
    """

    max_login_attempts: int = 3
    max_search_attempts: int = 5
    max_booking_attempts: int = 3
    retry_delay_seconds: int = 5
    backoff_multiplier: float = 2.0
    max_retry_delay_seconds: int = 60


@dataclass
class AdapterConfig:
    """
    Configuration for a site adapter.

    Attributes:
        site_code: Site identifier.
        base_url: Base URL for the site.
        timeouts: Timeout configuration.
        retries: Retry configuration.
        human_delay_range: Random delay range (min, max) in seconds.
        screenshot_on_error: Whether to capture screenshots on error.
        debug_mode: Enable debug logging.
        selectors_version: Version of CSS selectors to use.
        custom_settings: Site-specific custom settings.
    """

    site_code: SiteCode
    base_url: str
    timeouts: AdapterTimeouts = field(default_factory=AdapterTimeouts)
    retries: AdapterRetryConfig = field(default_factory=AdapterRetryConfig)
    human_delay_range: tuple[float, float] = (0.5, 2.0)
    screenshot_on_error: bool = True
    debug_mode: bool = False
    selectors_version: str = "v1"
    custom_settings: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Session Management
# =============================================================================


@dataclass
class AdapterSession:
    """
    Session state for adapter operations.

    Tracks session information, authentication state,
    and operation metrics.

    Attributes:
        id: Unique session identifier.
        account_id: ID of the bot account.
        proxy_id: ID of the proxy being used.
        page: Playwright page instance.
        is_authenticated: Whether logged in.
        login_time: When authentication occurred.
        last_activity: Last activity timestamp.
        cookies: Session cookies.
        storage_state: Browser storage state.
        request_count: Number of requests made.
        captcha_count: Number of CAPTCHAs solved.
        error_count: Number of errors encountered.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    account_id: str | None = None
    proxy_id: str | None = None
    page: Any = None  # Playwright Page
    is_authenticated: bool = False
    login_time: datetime | None = None
    last_activity: datetime = field(default_factory=datetime.utcnow)
    cookies: list[dict[str, Any]] = field(default_factory=list)
    storage_state: dict[str, Any] = field(default_factory=dict)
    request_count: int = 0
    captcha_count: int = 0
    error_count: int = 0

    def update_activity(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = datetime.utcnow()

    def increment_requests(self) -> None:
        """Increment request counter."""
        self.request_count += 1
        self.update_activity()

    def increment_captchas(self) -> None:
        """Increment CAPTCHA counter."""
        self.captcha_count += 1

    def increment_errors(self) -> None:
        """Increment error counter."""
        self.error_count += 1


class AdapterSessionContext:
    """
    Context manager for adapter session lifecycle.

    Manages session creation, cleanup, and error handling.

    Usage:
        async with adapter.create_session(account) as session:
            slots = await adapter.search_slots(session, criteria)
    """

    def __init__(
        self,
        adapter: BaseSiteAdapter,
        account: BotAccount,
        proxy: dict[str, Any] | None = None,
    ) -> None:
        """
        Initialize session context.

        Args:
            adapter: Parent adapter instance.
            account: Bot account for authentication.
            proxy: Optional proxy configuration.
        """
        self.adapter = adapter
        self.account = account
        self.proxy = proxy
        self.session: AdapterSession | None = None
        self._cleanup_done = False

    async def __aenter__(self) -> AdapterSession:
        """Create and initialize session."""
        self.session = AdapterSession(
            account_id=self.account.id,
            proxy_id=self.proxy.get("proxy_id") if self.proxy else None,
        )

        try:
            # Create browser page with stealth settings
            page = await self.adapter._create_page(self.proxy)
            self.session.page = page

            logger.debug(
                "adapter_session_created",
                session_id=self.session.id,
                account_id=self.account.id,
                site=self.adapter.config.site_code.value,
            )

            return self.session

        except Exception as e:
            await self._cleanup()
            raise SiteAdapterError(
                f"Failed to create adapter session: {e}",
                site=self.adapter.config.site_code.value,
            ) from e

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        """Cleanup session resources."""
        await self._cleanup()

        if exc_val is not None:
            logger.warning(
                "adapter_session_error",
                session_id=self.session.id if self.session else None,
                error=str(exc_val),
                error_type=exc_type.__name__ if exc_type else None,
            )

        return False  # Don't suppress exceptions

    async def _cleanup(self) -> None:
        """Cleanup session resources."""
        if self._cleanup_done:
            return

        self._cleanup_done = True

        if self.session and self.session.page:
            try:
                await self.session.page.close()
            except Exception as e:
                logger.warning(
                    "session_page_close_error",
                    session_id=self.session.id,
                    error=str(e),
                )

        logger.debug(
            "adapter_session_closed",
            session_id=self.session.id if self.session else None,
            request_count=self.session.request_count if self.session else 0,
            captcha_count=self.session.captcha_count if self.session else 0,
        )


# =============================================================================
# Metrics
# =============================================================================


@dataclass
class AdapterMetrics:
    """
    Metrics for adapter performance tracking.

    Collects statistics for monitoring and optimization.

    Attributes:
        login_attempts: Total login attempts.
        login_successes: Successful logins.
        login_failures: Failed logins.
        search_attempts: Total slot searches.
        slots_found: Total slots discovered.
        booking_attempts: Total booking attempts.
        booking_successes: Successful bookings.
        booking_failures: Failed bookings.
        captchas_solved: Total CAPTCHAs solved.
        captcha_failures: Failed CAPTCHA attempts.
        avg_search_time_ms: Average slot search time.
        avg_booking_time_ms: Average booking time.
        errors: Error counter by type.
    """

    login_attempts: int = 0
    login_successes: int = 0
    login_failures: int = 0
    search_attempts: int = 0
    slots_found: int = 0
    booking_attempts: int = 0
    booking_successes: int = 0
    booking_failures: int = 0
    captchas_solved: int = 0
    captcha_failures: int = 0
    avg_search_time_ms: float = 0.0
    avg_booking_time_ms: float = 0.0
    errors: dict[str, int] = field(default_factory=dict)

    def record_login(self, success: bool) -> None:
        """Record login attempt result."""
        self.login_attempts += 1
        if success:
            self.login_successes += 1
        else:
            self.login_failures += 1

    def record_search(self, slots_count: int, duration_ms: float) -> None:
        """Record slot search result."""
        self.search_attempts += 1
        self.slots_found += slots_count

        # Update running average
        if self.search_attempts == 1:
            self.avg_search_time_ms = duration_ms
        else:
            self.avg_search_time_ms = (
                self.avg_search_time_ms * (self.search_attempts - 1) + duration_ms
            ) / self.search_attempts

    def record_booking(self, success: bool, duration_ms: float) -> None:
        """Record booking attempt result."""
        self.booking_attempts += 1
        if success:
            self.booking_successes += 1
        else:
            self.booking_failures += 1

        # Update running average
        if self.booking_attempts == 1:
            self.avg_booking_time_ms = duration_ms
        else:
            self.avg_booking_time_ms = (
                self.avg_booking_time_ms * (self.booking_attempts - 1) + duration_ms
            ) / self.booking_attempts

    def record_captcha(self, success: bool) -> None:
        """Record CAPTCHA solve result."""
        if success:
            self.captchas_solved += 1
        else:
            self.captcha_failures += 1

    def record_error(self, error_type: str) -> None:
        """Record error occurrence."""
        self.errors[error_type] = self.errors.get(error_type, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for reporting."""
        return {
            "login": {
                "attempts": self.login_attempts,
                "successes": self.login_successes,
                "failures": self.login_failures,
                "success_rate": (
                    self.login_successes / self.login_attempts
                    if self.login_attempts > 0
                    else 0
                ),
            },
            "search": {
                "attempts": self.search_attempts,
                "slots_found": self.slots_found,
                "avg_time_ms": round(self.avg_search_time_ms, 2),
            },
            "booking": {
                "attempts": self.booking_attempts,
                "successes": self.booking_successes,
                "failures": self.booking_failures,
                "success_rate": (
                    self.booking_successes / self.booking_attempts
                    if self.booking_attempts > 0
                    else 0
                ),
                "avg_time_ms": round(self.avg_booking_time_ms, 2),
            },
            "captcha": {
                "solved": self.captchas_solved,
                "failures": self.captcha_failures,
            },
            "errors": self.errors,
        }


# =============================================================================
# Abstract Base Adapter
# =============================================================================


class BaseSiteAdapter(ABC):
    """
    Abstract base class for visa booking site adapters.

    Provides the interface and common functionality for all site-specific
    adapters. Concrete implementations must implement the abstract methods
    for login, slot search, and booking operations.

    Subclasses:
    - VFSAdapter: VFS Global visa appointments
    - IDataAdapter: iDATA visa appointments
    - BLSAdapter: BLS Spain visa appointments
    - KKOSMOSAdapter: KKOSMOS Greek visa appointments

    Attributes:
        config: Adapter configuration.
        stealth_engine: Browser stealth engine.
        proxy_manager: Proxy rotation manager.
        captcha_solver: CAPTCHA solving service.
        session_manager: Browser session manager.
        metrics: Performance metrics.

    Usage:
        class VFSAdapter(BaseSiteAdapter):
            async def login(self, session, account):
                # Site-specific implementation
                ...

            async def search_slots(self, session, criteria):
                # Site-specific implementation
                ...

            async def book_slot(self, session, slot, applicant):
                # Site-specific implementation
                ...

        # Instantiate and use
        adapter = VFSAdapter(
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

    def __init__(
        self,
        config: AdapterConfig,
        stealth_engine: StealthEngine | None = None,
        proxy_manager: ProxyManager | None = None,
        captcha_solver: CaptchaSolver | None = None,
        session_manager: SessionManager | None = None,
    ) -> None:
        """
        Initialize the site adapter.

        Args:
            config: Adapter configuration.
            stealth_engine: Browser stealth engine for anti-detection.
            proxy_manager: Proxy rotation and management service.
            captcha_solver: CAPTCHA solving service.
            session_manager: Browser session lifecycle manager.
        """
        self.config = config
        self.stealth_engine = stealth_engine
        self.proxy_manager = proxy_manager
        self.captcha_solver = captcha_solver
        self.session_manager = session_manager
        self.metrics = AdapterMetrics()

        logger.info(
            "adapter_initialized",
            site=config.site_code.value,
            base_url=config.base_url,
        )

    @property
    def site_code(self) -> SiteCode:
        """Get the site code for this adapter."""
        return self.config.site_code

    # -------------------------------------------------------------------------
    # Session Management
    # -------------------------------------------------------------------------

    @asynccontextmanager
    async def create_session(
        self,
        account: BotAccount,
        proxy: dict[str, Any] | None = None,
    ) -> AsyncGenerator[AdapterSession, None]:
        """
        Create a new adapter session.

        Creates a browser session with stealth settings and proxy
        configuration for the given bot account.

        Args:
            account: Bot account for authentication.
            proxy: Optional proxy configuration.

        Yields:
            AdapterSession instance.

        Usage:
            async with adapter.create_session(account) as session:
                await adapter.login(session, account)
                slots = await adapter.search_slots(session, criteria)
        """
        context = AdapterSessionContext(self, account, proxy)
        session = await context.__aenter__()
        try:
            yield session
        finally:
            await context.__aexit__(None, None, None)

    async def _create_page(self, proxy: dict[str, Any] | None = None) -> Page:
        """
        Create a new browser page with stealth settings.

        Args:
            proxy: Optional proxy configuration.

        Returns:
            Configured Playwright Page instance.
        """
        if self.stealth_engine:
            session = await self.stealth_engine.create_session(
                proxy=proxy,
                target_site=self.config.site_code.value,
            )
            return session.page

        # Fallback without stealth engine (for testing)
        from playwright.async_api import async_playwright

        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        return await context.new_page()

    # -------------------------------------------------------------------------
    # Abstract Methods - Must be implemented by subclasses
    # -------------------------------------------------------------------------

    @abstractmethod
    async def login(
        self,
        session: AdapterSession,
        account: BotAccount,
    ) -> bool:
        """
        Authenticate with the visa booking site.

        Performs login using the provided bot account credentials.
        Must handle CAPTCHAs, rate limiting, and account lockouts.

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
        pass

    @abstractmethod
    async def search_slots(
        self,
        session: AdapterSession,
        criteria: SlotSearchCriteria,
    ) -> list[Slot]:
        """
        Search for available appointment slots.

        Searches the site for slots matching the given criteria.
        Returns all matching slots sorted by date.

        Args:
            session: Active authenticated session.
            criteria: Search criteria for slots.

        Returns:
            List of available Slot objects.

        Raises:
            NoSlotsFoundError: If no slots match criteria.
            SiteStructureChangedError: If page structure has changed.
            SessionError: If session is invalid.
        """
        pass

    @abstractmethod
    async def book_slot(
        self,
        session: AdapterSession,
        slot: Slot,
        applicant: dict[str, Any],
    ) -> BookingResult:
        """
        Book an appointment slot.

        Completes the booking flow for the selected slot.
        Fills forms, handles payment if required, and confirms.

        Args:
            session: Active authenticated session.
            slot: Selected slot to book.
            applicant: Applicant data for form filling.

        Returns:
            BookingResult with confirmation or error details.

        Raises:
            SlotNotAvailableError: If slot is no longer available.
            PaymentFailedError: If payment processing fails.
            SiteAdapterError: For other booking failures.
        """
        pass

    # -------------------------------------------------------------------------
    # Optional Methods - Can be overridden by subclasses
    # -------------------------------------------------------------------------

    async def logout(self, session: AdapterSession) -> bool:
        """
        Log out from the booking site.

        Default implementation does nothing. Override if
        explicit logout is required for the site.

        Args:
            session: Active session to log out.

        Returns:
            True if logout successful.
        """
        logger.debug(
            "logout_skipped",
            session_id=session.id,
            site=self.config.site_code.value,
            reason="No logout required",
        )
        return True

    async def cancel_booking(
        self,
        session: AdapterSession,
        booking_id: str,
    ) -> bool:
        """
        Cancel an existing booking.

        Default implementation raises NotImplementedError.
        Override if cancellation is supported by the site.

        Args:
            session: Active authenticated session.
            booking_id: ID of booking to cancel.

        Returns:
            True if cancellation successful.

        Raises:
            NotImplementedError: If not supported.
        """
        raise NotImplementedError(
            f"Booking cancellation not supported for {self.config.site_code.value}"
        )

    async def reschedule_booking(
        self,
        session: AdapterSession,
        booking_id: str,
        new_slot: Slot,
    ) -> BookingResult:
        """
        Reschedule an existing booking.

        Default implementation raises NotImplementedError.
        Override if rescheduling is supported by the site.

        Args:
            session: Active authenticated session.
            booking_id: ID of booking to reschedule.
            new_slot: New slot for the appointment.

        Returns:
            BookingResult with new confirmation.

        Raises:
            NotImplementedError: If not supported.
        """
        raise NotImplementedError(
            f"Booking rescheduling not supported for {self.config.site_code.value}"
        )

    async def verify_booking(
        self,
        session: AdapterSession,
        confirmation_number: str,
    ) -> bool:
        """
        Verify a booking exists on the site.

        Default implementation raises NotImplementedError.
        Override if verification is supported by the site.

        Args:
            session: Active authenticated session.
            confirmation_number: Confirmation number to verify.

        Returns:
            True if booking is valid.

        Raises:
            NotImplementedError: If not supported.
        """
        raise NotImplementedError(
            f"Booking verification not supported for {self.config.site_code.value}"
        )

    # -------------------------------------------------------------------------
    # Helper Methods - Common functionality
    # -------------------------------------------------------------------------

    async def wait_for_element(
        self,
        page: Page,
        selector: str,
        timeout: int | None = None,
        state: str = "visible",
    ) -> Any:
        """
        Wait for an element to be present on the page.

        Args:
            page: Playwright page instance.
            selector: CSS selector for the element.
            timeout: Wait timeout in milliseconds.
            state: Element state to wait for (visible, hidden, attached).

        Returns:
            Element handle if found.

        Raises:
            SelectorNotFoundError: If element not found.
            BrowserTimeoutError: If timeout exceeded.
        """
        timeout_ms = timeout or self.config.timeouts.element_wait * 1000

        try:
            element = await page.wait_for_selector(
                selector,
                timeout=timeout_ms,
                state=state,
            )
            return element
        except Exception as e:
            if "timeout" in str(e).lower():
                raise BrowserTimeoutError(
                    f"Timeout waiting for selector: {selector}",
                    timeout_seconds=timeout_ms / 1000,
                )
            raise SelectorNotFoundError(
                f"Selector not found: {selector}",
                selector=selector,
                page_url=page.url,
            )

    async def human_delay(
        self,
        min_seconds: float | None = None,
        max_seconds: float | None = None,
    ) -> None:
        """
        Add a human-like random delay.

        Args:
            min_seconds: Minimum delay (uses config default if None).
            max_seconds: Maximum delay (uses config default if None).
        """
        import random

        min_s = min_seconds or self.config.human_delay_range[0]
        max_s = max_seconds or self.config.human_delay_range[1]
        delay = random.uniform(min_s, max_s)
        await asyncio.sleep(delay)

    async def take_screenshot(
        self,
        page: Page,
        name: str,
        full_page: bool = False,
    ) -> str | None:
        """
        Capture a screenshot of the current page.

        Args:
            page: Playwright page instance.
            name: Screenshot name (without extension).
            full_page: Whether to capture full page.

        Returns:
            Path to saved screenshot, or None if failed.
        """
        import tempfile
        from pathlib import Path

        try:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"{name}_{timestamp}.png"
            filepath = Path(tempfile.gettempdir()) / "vise_os_screenshots" / filename

            filepath.parent.mkdir(parents=True, exist_ok=True)

            await page.screenshot(path=str(filepath), full_page=full_page)

            logger.debug(
                "screenshot_captured",
                name=name,
                path=str(filepath),
            )
            return str(filepath)

        except Exception as e:
            logger.warning(
                "screenshot_failed",
                name=name,
                error=str(e),
            )
            return None

    async def handle_captcha(
        self,
        page: Page,
        captcha_type: str = "recaptcha",
    ) -> str | None:
        """
        Solve CAPTCHA if present on the page.

        Args:
            page: Playwright page instance.
            captcha_type: Type of CAPTCHA (recaptcha, turnstile, hcaptcha).

        Returns:
            CAPTCHA solution token, or None if no CAPTCHA/failed.
        """
        if not self.captcha_solver:
            logger.warning(
                "captcha_solver_not_available",
                captcha_type=captcha_type,
            )
            return None

        try:
            # Detect and solve CAPTCHA
            solution = await self.captcha_solver.solve(
                page=page,
                captcha_type=captcha_type,
                timeout=self.config.timeouts.captcha_solve,
            )

            if solution:
                self.metrics.record_captcha(success=True)
                logger.info(
                    "captcha_solved",
                    captcha_type=captcha_type,
                    site=self.config.site_code.value,
                )
            return solution

        except Exception as e:
            self.metrics.record_captcha(success=False)
            logger.error(
                "captcha_solve_failed",
                captcha_type=captcha_type,
                error=str(e),
            )
            return None

    def _check_ban_indicators(self, page_content: str) -> bool:
        """
        Check if page content indicates account ban.

        Args:
            page_content: Page HTML or text content.

        Returns:
            True if ban indicators detected.
        """
        ban_keywords = [
            "account has been suspended",
            "account is locked",
            "too many attempts",
            "temporarily blocked",
            "access denied",
            "unusual activity",
            "security reasons",
            "banned",
            "forbidden",
        ]

        content_lower = page_content.lower()
        return any(keyword in content_lower for keyword in ban_keywords)

    def _check_site_changed(self, page_content: str, expected_elements: list[str]) -> bool:
        """
        Check if site structure has changed.

        Args:
            page_content: Page HTML content.
            expected_elements: List of expected element identifiers.

        Returns:
            True if site appears to have changed.
        """
        missing = 0
        for element in expected_elements:
            if element not in page_content:
                missing += 1

        # If more than 50% of expected elements are missing, site may have changed
        return missing > len(expected_elements) * 0.5

    def get_metrics(self) -> dict[str, Any]:
        """
        Get adapter performance metrics.

        Returns:
            Dictionary of metrics for monitoring.
        """
        return {
            "site": self.config.site_code.value,
            **self.metrics.to_dict(),
        }


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Main adapter class
    "BaseSiteAdapter",
    # Data classes
    "AdapterConfig",
    "BookingResult",
    "BookingResultStatus",
    "Slot",
    "SlotSearchCriteria",
    # Enums
    "SiteCode",
    "SlotStatus",
    # Session management
    "AdapterSession",
    "AdapterSessionContext",
    # Retry and timeout
    "AdapterRetryConfig",
    "AdapterTimeouts",
    # Metrics
    "AdapterMetrics",
]
