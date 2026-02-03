"""
Agency Service for VISE OS.

This module provides a high-level service layer for managing agencies.
It encapsulates business logic for agency operations including creation,
updates, status management, and integration with the credit system.

Based on 002-DIRECTUS-SCHEMA.md:
- agencies: Agency profiles and configuration
- agency_credits: Credit balances (managed via CreditService)

The service provides:
- Agency CRUD operations with validation
- Agency status lifecycle management
- Google Sheets sync configuration
- Notification preferences management
- Integration with CreditService for credit operations
- Agency statistics and reporting

Usage:
    from src.core.agency.service import AgencyService, get_agency_service

    # Using the service
    service = AgencyService()

    # Create a new agency
    agency = await service.create_agency(
        name="Acme Travel",
        contact_name="John Doe",
        contact_email="john@acme.com",
        contact_phone="+905551234567",
    )

    # Get agency with credits
    agency_with_credits = await service.get_agency_with_credits("agency-uuid")

    # Update agency status
    await service.activate_agency("agency-uuid")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Callable
from uuid import UUID

import structlog

from src.api.schemas.agency import (
    AgencyCreate,
    AgencyResponse,
    AgencyStatus,
    AgencyUpdate,
    NotificationPreferences,
)
from src.core.agency.credit import CreditBalance, CreditService, get_credit_service
from src.core.agency.repository import AgencyRepository
from src.core.exceptions import ViseOSError

if TYPE_CHECKING:
    from src.integrations.directus import DirectusClient

logger = structlog.get_logger(__name__)


# =============================================================================
# Custom Exceptions
# =============================================================================


class AgencyError(ViseOSError):
    """Base exception for agency-related errors."""

    def __init__(
        self,
        message: str = "Agency operation failed",
        *,
        agency_id: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.agency_id = agency_id
        details = details or {}
        if agency_id:
            details["agency_id"] = agency_id
        super().__init__(message, code=code or "AGENCY_ERROR", details=details)


class AgencyNotFoundError(AgencyError):
    """Raised when an agency cannot be found."""

    def __init__(
        self,
        agency_id: str,
        *,
        message: str = "Agency not found",
    ) -> None:
        super().__init__(
            message,
            agency_id=agency_id,
            code="AGENCY_NOT_FOUND",
        )


class AgencyAlreadyExistsError(AgencyError):
    """Raised when trying to create an agency with an existing email."""

    def __init__(
        self,
        email: str,
        *,
        message: str = "Agency with this email already exists",
    ) -> None:
        super().__init__(
            message,
            code="AGENCY_ALREADY_EXISTS",
            details={"email": email},
        )


class InvalidAgencyStatusError(AgencyError):
    """Raised when an invalid status transition is attempted."""

    def __init__(
        self,
        message: str = "Invalid agency status transition",
        *,
        agency_id: str | None = None,
        current_status: str | None = None,
        target_status: str | None = None,
    ) -> None:
        details = {}
        if current_status:
            details["current_status"] = current_status
        if target_status:
            details["target_status"] = target_status
        super().__init__(
            message,
            agency_id=agency_id,
            code="INVALID_AGENCY_STATUS",
            details=details,
        )


# =============================================================================
# Event Types
# =============================================================================


class AgencyEventType(str, Enum):
    """Types of agency events for callbacks."""

    CREATED = "created"
    UPDATED = "updated"
    ACTIVATED = "activated"
    SUSPENDED = "suspended"
    TRIAL_STARTED = "trial_started"
    DELETED = "deleted"
    SHEETS_SYNC_ENABLED = "sheets_sync_enabled"
    SHEETS_SYNC_DISABLED = "sheets_sync_disabled"


@dataclass
class AgencyEvent:
    """
    Event emitted during agency lifecycle.

    Attributes:
        event_type: Type of the event.
        agency_id: ID of the affected agency.
        timestamp: When the event occurred.
        data: Additional event data.
    """

    event_type: AgencyEventType
    agency_id: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    data: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class AgencyWithCredits:
    """
    Agency data combined with credit balance.

    Provides a complete view of an agency including its credit state.
    """

    agency: dict[str, Any]
    credits: CreditBalance | None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        result = dict(self.agency)
        if self.credits:
            result["credits"] = self.credits.to_dict()
        return result


@dataclass
class AgencyStats:
    """
    Statistics for an agency.

    Attributes:
        agency_id: The agency UUID.
        total_bookings: Total booking requests made.
        completed_bookings: Successfully completed bookings.
        failed_bookings: Failed booking attempts.
        pending_bookings: Currently processing bookings.
        success_rate: Percentage of successful bookings.
        total_credits_used: Total credits consumed.
    """

    agency_id: str
    total_bookings: int = 0
    completed_bookings: int = 0
    failed_bookings: int = 0
    pending_bookings: int = 0
    success_rate: float = 0.0
    total_credits_used: int = 0


# =============================================================================
# Agency Service
# =============================================================================


class AgencyService:
    """
    Service for managing agencies.

    Provides high-level business logic for agency operations including
    creation, updates, status management, and credit integration.

    Attributes:
        _repository: AgencyRepository for data access.
        _credit_service: CreditService for credit operations.
        _logger: Structured logger.
        _event_handlers: Registered event callbacks.

    Example:
        service = AgencyService()

        # Create agency
        agency = await service.create_agency(
            name="Acme Travel",
            contact_name="John Doe",
            contact_email="john@acme.com",
            contact_phone="+905551234567",
        )

        # Get with credits
        data = await service.get_agency_with_credits(agency["id"])

        # Activate
        await service.activate_agency(agency["id"])
    """

    def __init__(
        self,
        repository: AgencyRepository | None = None,
        credit_service: CreditService | None = None,
    ) -> None:
        """
        Initialize the agency service.

        Args:
            repository: Optional AgencyRepository. If not provided,
                creates a new instance.
            credit_service: Optional CreditService. If not provided,
                uses the cached global instance.
        """
        self._repository = repository or AgencyRepository()
        self._credit_service = credit_service or get_credit_service()
        self._logger = logger.bind(service="agency")

        # Event handlers
        self._event_handlers: dict[AgencyEventType, list[Callable]] = {
            event_type: [] for event_type in AgencyEventType
        }

        self._logger.info("agency_service_initialized")

    # -------------------------------------------------------------------------
    # Event Handling
    # -------------------------------------------------------------------------

    def on_event(
        self,
        event_type: AgencyEventType,
        handler: Callable[[AgencyEvent], Any],
    ) -> None:
        """
        Register an event handler.

        Args:
            event_type: Type of event to handle.
            handler: Callback function to invoke.
        """
        self._event_handlers[event_type].append(handler)

    def off_event(
        self,
        event_type: AgencyEventType,
        handler: Callable[[AgencyEvent], Any],
    ) -> None:
        """
        Unregister an event handler.

        Args:
            event_type: Type of event.
            handler: Callback function to remove.
        """
        if handler in self._event_handlers[event_type]:
            self._event_handlers[event_type].remove(handler)

    async def _emit_event(
        self,
        event_type: AgencyEventType,
        agency_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """
        Emit an agency event to registered handlers.

        Args:
            event_type: Type of event.
            agency_id: ID of the agency.
            data: Additional event data.
        """
        import asyncio

        event = AgencyEvent(
            event_type=event_type,
            agency_id=agency_id,
            data=data or {},
        )

        self._logger.debug(
            "agency_event_emitted",
            event_type=event_type.value,
            agency_id=agency_id,
        )

        for handler in self._event_handlers[event_type]:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                self._logger.error(
                    "agency_event_handler_error",
                    event_type=event_type.value,
                    agency_id=agency_id,
                    error=str(e),
                )

    # -------------------------------------------------------------------------
    # Agency CRUD Operations
    # -------------------------------------------------------------------------

    async def create_agency(
        self,
        name: str,
        contact_name: str,
        contact_email: str,
        contact_phone: str,
        *,
        tursab_no: str | None = None,
        telegram_chat_id: str | None = None,
        discord_webhook: str | None = None,
        google_sheet_id: str | None = None,
        google_sheet_sync_enabled: bool = False,
        default_countries: list[str] | None = None,
        notification_preferences: dict[str, Any] | None = None,
        initial_credits: int = 0,
    ) -> dict[str, Any]:
        """
        Create a new agency.

        Creates the agency record with trial status and initializes
        the credit balance. Optionally adds initial credits.

        Args:
            name: Agency company name.
            contact_name: Primary contact person.
            contact_email: Contact email (used for login).
            contact_phone: Contact phone number.
            tursab_no: Optional TÜRSAB registration number.
            telegram_chat_id: Optional Telegram chat ID.
            discord_webhook: Optional Discord webhook URL.
            google_sheet_id: Optional Google Sheet ID.
            google_sheet_sync_enabled: Enable Sheets sync.
            default_countries: Preferred target countries.
            notification_preferences: Notification settings.
            initial_credits: Initial credit balance to add.

        Returns:
            Created agency data.

        Raises:
            AgencyAlreadyExistsError: If email already registered.
        """
        self._logger.info(
            "agency_create_start",
            name=name,
            contact_email=contact_email,
        )

        # Check for existing agency with same email
        existing = await self._repository.get_by_email(contact_email)
        if existing:
            raise AgencyAlreadyExistsError(contact_email)

        # Prepare agency data
        data = {
            "name": name,
            "contact_name": contact_name,
            "contact_email": contact_email,
            "contact_phone": contact_phone,
            "status": AgencyStatus.TRIAL.value,
            "google_sheet_sync_enabled": google_sheet_sync_enabled,
        }

        if tursab_no:
            data["tursab_no"] = tursab_no
        if telegram_chat_id:
            data["telegram_chat_id"] = telegram_chat_id
        if discord_webhook:
            data["discord_webhook"] = discord_webhook
        if google_sheet_id:
            data["google_sheet_id"] = google_sheet_id
        if default_countries:
            data["default_countries"] = default_countries
        if notification_preferences:
            data["notification_preferences"] = notification_preferences

        # Create agency (repository also creates credit record)
        agency = await self._repository.create(data)
        agency_id = str(agency["id"])

        # Add initial credits if specified
        if initial_credits > 0:
            await self._credit_service.purchase_credits(
                agency_id,
                initial_credits,
                description="Initial credit allocation",
            )

        await self._emit_event(
            AgencyEventType.CREATED,
            agency_id,
            {"name": name, "contact_email": contact_email},
        )

        self._logger.info(
            "agency_created",
            agency_id=agency_id,
            name=name,
        )

        return agency

    async def create_agency_from_schema(
        self,
        data: AgencyCreate,
        *,
        initial_credits: int = 0,
    ) -> dict[str, Any]:
        """
        Create an agency from a Pydantic schema.

        Args:
            data: AgencyCreate schema with validated data.
            initial_credits: Initial credit balance to add.

        Returns:
            Created agency data.
        """
        return await self.create_agency(
            name=data.name,
            contact_name=data.contact_name,
            contact_email=str(data.contact_email),
            contact_phone=data.contact_phone,
            tursab_no=data.tursab_no,
            telegram_chat_id=data.telegram_chat_id,
            discord_webhook=data.discord_webhook,
            google_sheet_id=data.google_sheet_id,
            google_sheet_sync_enabled=data.google_sheet_sync_enabled,
            default_countries=data.default_countries,
            notification_preferences=(
                data.notification_preferences.model_dump()
                if data.notification_preferences
                else None
            ),
            initial_credits=initial_credits,
        )

    async def get_agency(
        self,
        agency_id: str | UUID,
        *,
        fields: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """
        Get an agency by ID.

        Args:
            agency_id: The agency UUID.
            fields: Optional list of fields to return.

        Returns:
            Agency data or None if not found.
        """
        self._logger.debug("agency_get", agency_id=str(agency_id))
        return await self._repository.get_by_id(agency_id, fields=fields)

    async def get_agency_or_raise(
        self,
        agency_id: str | UUID,
    ) -> dict[str, Any]:
        """
        Get an agency by ID, raising if not found.

        Args:
            agency_id: The agency UUID.

        Returns:
            Agency data.

        Raises:
            AgencyNotFoundError: If agency not found.
        """
        agency = await self.get_agency(agency_id)
        if not agency:
            raise AgencyNotFoundError(str(agency_id))
        return agency

    async def get_agency_by_email(self, email: str) -> dict[str, Any] | None:
        """
        Get an agency by contact email.

        Args:
            email: The contact email address.

        Returns:
            Agency data or None if not found.
        """
        return await self._repository.get_by_email(email)

    async def get_agency_with_credits(
        self,
        agency_id: str | UUID,
    ) -> AgencyWithCredits:
        """
        Get an agency with its credit balance.

        Args:
            agency_id: The agency UUID.

        Returns:
            AgencyWithCredits containing agency and credit data.

        Raises:
            AgencyNotFoundError: If agency not found.
        """
        agency = await self.get_agency_or_raise(agency_id)

        try:
            credits = await self._credit_service.get_balance(agency_id)
        except Exception:
            credits = None

        return AgencyWithCredits(agency=agency, credits=credits)

    async def update_agency(
        self,
        agency_id: str | UUID,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Update an agency.

        Args:
            agency_id: The agency UUID.
            data: Fields to update.

        Returns:
            Updated agency data.

        Raises:
            AgencyNotFoundError: If agency not found.
        """
        agency_id_str = str(agency_id)

        # Verify agency exists
        await self.get_agency_or_raise(agency_id)

        self._logger.info(
            "agency_update",
            agency_id=agency_id_str,
            fields=list(data.keys()),
        )

        updated = await self._repository.update(agency_id, data)

        await self._emit_event(
            AgencyEventType.UPDATED,
            agency_id_str,
            {"updated_fields": list(data.keys())},
        )

        return updated

    async def update_agency_from_schema(
        self,
        agency_id: str | UUID,
        data: AgencyUpdate,
    ) -> dict[str, Any]:
        """
        Update an agency from a Pydantic schema.

        Args:
            agency_id: The agency UUID.
            data: AgencyUpdate schema with fields to update.

        Returns:
            Updated agency data.
        """
        # Only include non-None fields
        update_data = {
            k: v for k, v in data.model_dump().items() if v is not None
        }

        # Handle notification preferences separately
        if "notification_preferences" in update_data and update_data["notification_preferences"]:
            if isinstance(update_data["notification_preferences"], NotificationPreferences):
                update_data["notification_preferences"] = (
                    update_data["notification_preferences"].model_dump()
                )

        return await self.update_agency(agency_id, update_data)

    async def delete_agency(self, agency_id: str | UUID) -> None:
        """
        Delete an agency.

        Args:
            agency_id: The agency UUID.

        Raises:
            AgencyNotFoundError: If agency not found.
        """
        agency_id_str = str(agency_id)

        # Verify agency exists
        await self.get_agency_or_raise(agency_id)

        self._logger.info("agency_delete", agency_id=agency_id_str)

        await self._repository.delete(agency_id)

        await self._emit_event(AgencyEventType.DELETED, agency_id_str)

    # -------------------------------------------------------------------------
    # Status Management
    # -------------------------------------------------------------------------

    async def activate_agency(self, agency_id: str | UUID) -> dict[str, Any]:
        """
        Activate an agency (trial -> active or suspended -> active).

        Args:
            agency_id: The agency UUID.

        Returns:
            Updated agency data.

        Raises:
            AgencyNotFoundError: If agency not found.
        """
        agency_id_str = str(agency_id)
        agency = await self.get_agency_or_raise(agency_id)
        current_status = agency.get("status")

        if current_status == AgencyStatus.ACTIVE.value:
            return agency  # Already active

        self._logger.info(
            "agency_activate",
            agency_id=agency_id_str,
            from_status=current_status,
        )

        updated = await self._repository.update_status(agency_id, AgencyStatus.ACTIVE)

        await self._emit_event(
            AgencyEventType.ACTIVATED,
            agency_id_str,
            {"previous_status": current_status},
        )

        return updated

    async def suspend_agency(
        self,
        agency_id: str | UUID,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """
        Suspend an agency.

        Args:
            agency_id: The agency UUID.
            reason: Optional suspension reason.

        Returns:
            Updated agency data.

        Raises:
            AgencyNotFoundError: If agency not found.
            InvalidAgencyStatusError: If already suspended.
        """
        agency_id_str = str(agency_id)
        agency = await self.get_agency_or_raise(agency_id)
        current_status = agency.get("status")

        if current_status == AgencyStatus.SUSPENDED.value:
            raise InvalidAgencyStatusError(
                "Agency is already suspended",
                agency_id=agency_id_str,
                current_status=current_status,
                target_status=AgencyStatus.SUSPENDED.value,
            )

        self._logger.info(
            "agency_suspend",
            agency_id=agency_id_str,
            from_status=current_status,
            reason=reason,
        )

        updated = await self._repository.update_status(agency_id, AgencyStatus.SUSPENDED)

        await self._emit_event(
            AgencyEventType.SUSPENDED,
            agency_id_str,
            {"previous_status": current_status, "reason": reason},
        )

        return updated

    async def start_trial(self, agency_id: str | UUID) -> dict[str, Any]:
        """
        Start or restart a trial period for an agency.

        Args:
            agency_id: The agency UUID.

        Returns:
            Updated agency data.

        Raises:
            AgencyNotFoundError: If agency not found.
        """
        agency_id_str = str(agency_id)
        agency = await self.get_agency_or_raise(agency_id)
        current_status = agency.get("status")

        self._logger.info(
            "agency_start_trial",
            agency_id=agency_id_str,
            from_status=current_status,
        )

        updated = await self._repository.update_status(agency_id, AgencyStatus.TRIAL)

        await self._emit_event(
            AgencyEventType.TRIAL_STARTED,
            agency_id_str,
            {"previous_status": current_status},
        )

        return updated

    async def is_agency_active(self, agency_id: str | UUID) -> bool:
        """
        Check if an agency is active.

        Args:
            agency_id: The agency UUID.

        Returns:
            True if agency is active.
        """
        agency = await self.get_agency(agency_id)
        if not agency:
            return False
        return agency.get("status") == AgencyStatus.ACTIVE.value

    async def is_agency_operational(self, agency_id: str | UUID) -> bool:
        """
        Check if an agency can perform bookings.

        An agency is operational if it's active or in trial status.

        Args:
            agency_id: The agency UUID.

        Returns:
            True if agency can perform bookings.
        """
        agency = await self.get_agency(agency_id)
        if not agency:
            return False
        status = agency.get("status")
        return status in [AgencyStatus.ACTIVE.value, AgencyStatus.TRIAL.value]

    # -------------------------------------------------------------------------
    # Google Sheets Integration
    # -------------------------------------------------------------------------

    async def enable_sheets_sync(
        self,
        agency_id: str | UUID,
        sheet_id: str,
    ) -> dict[str, Any]:
        """
        Enable Google Sheets synchronization for an agency.

        Args:
            agency_id: The agency UUID.
            sheet_id: The Google Sheet ID to sync with.

        Returns:
            Updated agency data.

        Raises:
            AgencyNotFoundError: If agency not found.
        """
        agency_id_str = str(agency_id)

        self._logger.info(
            "agency_enable_sheets_sync",
            agency_id=agency_id_str,
            sheet_id=sheet_id,
        )

        updated = await self.update_agency(
            agency_id,
            {
                "google_sheet_id": sheet_id,
                "google_sheet_sync_enabled": True,
            },
        )

        await self._emit_event(
            AgencyEventType.SHEETS_SYNC_ENABLED,
            agency_id_str,
            {"sheet_id": sheet_id},
        )

        return updated

    async def disable_sheets_sync(self, agency_id: str | UUID) -> dict[str, Any]:
        """
        Disable Google Sheets synchronization for an agency.

        Args:
            agency_id: The agency UUID.

        Returns:
            Updated agency data.

        Raises:
            AgencyNotFoundError: If agency not found.
        """
        agency_id_str = str(agency_id)

        self._logger.info(
            "agency_disable_sheets_sync",
            agency_id=agency_id_str,
        )

        updated = await self.update_agency(
            agency_id,
            {"google_sheet_sync_enabled": False},
        )

        await self._emit_event(
            AgencyEventType.SHEETS_SYNC_DISABLED,
            agency_id_str,
        )

        return updated

    # -------------------------------------------------------------------------
    # Notification Preferences
    # -------------------------------------------------------------------------

    async def update_notification_preferences(
        self,
        agency_id: str | UUID,
        preferences: NotificationPreferences | dict[str, Any],
    ) -> dict[str, Any]:
        """
        Update notification preferences for an agency.

        Args:
            agency_id: The agency UUID.
            preferences: Notification settings.

        Returns:
            Updated agency data.

        Raises:
            AgencyNotFoundError: If agency not found.
        """
        if isinstance(preferences, NotificationPreferences):
            prefs_dict = preferences.model_dump()
        else:
            prefs_dict = preferences

        return await self.update_agency(
            agency_id,
            {"notification_preferences": prefs_dict},
        )

    # -------------------------------------------------------------------------
    # Query Operations
    # -------------------------------------------------------------------------

    async def list_agencies(
        self,
        *,
        status: AgencyStatus | str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        List agencies with optional filters.

        Args:
            status: Optional status filter.
            limit: Maximum results.
            offset: Result offset.

        Returns:
            List of agency records.
        """
        if status:
            return await self._repository.find_by_status(
                status,
                limit=limit,
                offset=offset,
            )
        else:
            return await self._repository.find_active(limit=limit, offset=offset)

    async def list_active_agencies(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        List all active agencies.

        Args:
            limit: Maximum results.
            offset: Result offset.

        Returns:
            List of active agencies.
        """
        return await self._repository.find_active(limit=limit, offset=offset)

    async def list_agencies_with_sheets_sync(self) -> list[dict[str, Any]]:
        """
        List agencies with Google Sheets sync enabled.

        Returns:
            List of agencies with sync enabled.
        """
        return await self._repository.find_with_google_sheets_sync()

    async def count_agencies(
        self,
        *,
        status: AgencyStatus | str | None = None,
    ) -> int:
        """
        Count agencies.

        Args:
            status: Optional status filter.

        Returns:
            Number of agencies.
        """
        return await self._repository.count(status=status)

    # -------------------------------------------------------------------------
    # Credit Integration
    # -------------------------------------------------------------------------

    async def get_agency_credits(self, agency_id: str | UUID) -> CreditBalance:
        """
        Get credit balance for an agency.

        Args:
            agency_id: The agency UUID.

        Returns:
            CreditBalance with current state.

        Raises:
            AgencyNotFoundError: If agency not found.
        """
        # Verify agency exists
        await self.get_agency_or_raise(agency_id)

        return await self._credit_service.get_balance(agency_id)

    async def can_agency_book(
        self,
        agency_id: str | UUID,
        credits_needed: int = 1,
    ) -> bool:
        """
        Check if an agency can make a booking.

        Verifies both operational status and credit availability.

        Args:
            agency_id: The agency UUID.
            credits_needed: Credits required for booking.

        Returns:
            True if agency can book.
        """
        # Check operational status
        if not await self.is_agency_operational(agency_id):
            return False

        # Check credit availability
        return await self._credit_service.can_afford_booking(
            agency_id,
            credits_needed=credits_needed,
        )

    async def add_credits(
        self,
        agency_id: str | UUID,
        amount: int,
        *,
        description: str | None = None,
    ) -> CreditBalance:
        """
        Add credits to an agency's balance.

        Args:
            agency_id: The agency UUID.
            amount: Number of credits to add.
            description: Optional purchase description.

        Returns:
            Updated CreditBalance.

        Raises:
            AgencyNotFoundError: If agency not found.
        """
        # Verify agency exists
        await self.get_agency_or_raise(agency_id)

        await self._credit_service.purchase_credits(
            agency_id,
            amount,
            description=description,
        )

        return await self._credit_service.get_balance(agency_id)


# =============================================================================
# Factory Functions
# =============================================================================


@lru_cache
def get_agency_service() -> AgencyService:
    """
    Get a cached AgencyService instance.

    The service is cached for reuse across requests.
    Use this for dependency injection in FastAPI.

    Returns:
        Configured AgencyService instance.

    Example:
        from fastapi import Depends

        @router.get("/agencies/{agency_id}")
        async def get_agency(
            agency_id: str,
            service: AgencyService = Depends(get_agency_service),
        ):
            return await service.get_agency(agency_id)
    """
    return AgencyService()


def clear_agency_service_cache() -> None:
    """
    Clear the cached AgencyService.

    Useful for testing or when dependencies change.
    """
    get_agency_service.cache_clear()


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Service
    "AgencyService",
    "get_agency_service",
    "clear_agency_service_cache",
    # Data classes
    "AgencyWithCredits",
    "AgencyStats",
    "AgencyEvent",
    "AgencyEventType",
    # Exceptions
    "AgencyError",
    "AgencyNotFoundError",
    "AgencyAlreadyExistsError",
    "InvalidAgencyStatusError",
]
