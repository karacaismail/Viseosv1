"""
Applicant Service for VISE OS.

This module provides a high-level service layer for managing applicants.
It encapsulates business logic for applicant operations including creation,
updates, status management, PII expiration handling, and family grouping.

CRITICAL: Applicant data contains PII. Per offshore compliance,
PII has a 24-hour retention period and is automatically redacted.

Based on 002-DIRECTUS-SCHEMA.md:
- applicants: Applicant profiles with encrypted PII

The service provides:
- Applicant CRUD operations with automatic PII encryption
- Status lifecycle management (pending -> processing -> completed/failed/expired)
- PII expiration management and extension
- Family group handling for linked bookings
- Applicant validation for booking eligibility
- Integration with booking workflow
- Batch applicant creation for Google Sheets import

Usage:
    from src.core.applicant.service import ApplicantService, get_applicant_service

    # Using the service
    service = ApplicantService()

    # Create a new applicant
    applicant = await service.create_applicant(
        agency_id="agency-uuid",
        first_name="John",
        last_name="Doe",
        birth_date="1990-01-15",
        nationality="TR",
        passport_number="U12345678",
        passport_expiry="2030-05-20",
        phone="+905551234567",
        target_country="DE",
        visa_type="tourist",
    )

    # Get applicant with decrypted PII
    applicant = await service.get_applicant("applicant-uuid")

    # Check if applicant is eligible for booking
    is_eligible = await service.is_eligible_for_booking("applicant-uuid")
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Callable
from uuid import UUID, uuid4

import structlog

from src.api.schemas.applicant import (
    ApplicantCreate,
    ApplicantStatus,
    ApplicantUpdate,
)
from src.core.applicant.repository import ApplicantRepository
from src.core.exceptions import ViseOSError

if TYPE_CHECKING:
    from src.integrations.directus import DirectusClient

logger = structlog.get_logger(__name__)


# =============================================================================
# Custom Exceptions
# =============================================================================


class ApplicantError(ViseOSError):
    """Base exception for applicant-related errors."""

    def __init__(
        self,
        message: str = "Applicant operation failed",
        *,
        applicant_id: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.applicant_id = applicant_id
        details = details or {}
        if applicant_id:
            details["applicant_id"] = applicant_id
        super().__init__(message, code=code or "APPLICANT_ERROR", details=details)


class ApplicantNotFoundError(ApplicantError):
    """Raised when an applicant cannot be found."""

    def __init__(
        self,
        applicant_id: str,
        *,
        message: str = "Applicant not found",
    ) -> None:
        super().__init__(
            message,
            applicant_id=applicant_id,
            code="APPLICANT_NOT_FOUND",
        )


class ApplicantExpiredError(ApplicantError):
    """Raised when trying to access an expired applicant."""

    def __init__(
        self,
        applicant_id: str,
        *,
        message: str = "Applicant PII has expired and been redacted",
    ) -> None:
        super().__init__(
            message,
            applicant_id=applicant_id,
            code="APPLICANT_EXPIRED",
        )


class ApplicantNotEligibleError(ApplicantError):
    """Raised when an applicant is not eligible for an operation."""

    def __init__(
        self,
        message: str = "Applicant is not eligible for this operation",
        *,
        applicant_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        details = {}
        if reason:
            details["reason"] = reason
        super().__init__(
            message,
            applicant_id=applicant_id,
            code="APPLICANT_NOT_ELIGIBLE",
            details=details,
        )


class InvalidApplicantDataError(ApplicantError):
    """Raised when applicant data validation fails."""

    def __init__(
        self,
        message: str = "Invalid applicant data",
        *,
        validation_errors: list[str] | None = None,
    ) -> None:
        details = {}
        if validation_errors:
            details["validation_errors"] = validation_errors
        super().__init__(
            message,
            code="INVALID_APPLICANT_DATA",
            details=details,
        )


# =============================================================================
# Event Types
# =============================================================================


class ApplicantEventType(str, Enum):
    """Types of applicant events for callbacks."""

    CREATED = "created"
    UPDATED = "updated"
    STATUS_CHANGED = "status_changed"
    PROCESSING_STARTED = "processing_started"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    REDACTED = "redacted"
    EXPIRY_EXTENDED = "expiry_extended"
    FAMILY_GROUP_CREATED = "family_group_created"


@dataclass
class ApplicantEvent:
    """
    Event emitted during applicant lifecycle.

    Attributes:
        event_type: Type of the event.
        applicant_id: ID of the affected applicant.
        timestamp: When the event occurred.
        data: Additional event data.
    """

    event_type: ApplicantEventType
    applicant_id: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    data: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ApplicantSummary:
    """
    Summary of an applicant without PII.

    Used for listings and status displays where full PII is not needed.
    """

    applicant_id: str
    agency_id: str
    external_ref: str | None
    status: ApplicantStatus
    nationality: str
    target_country: str
    visa_type: str
    created_at: datetime
    expires_at: datetime
    is_expired: bool


@dataclass
class FamilyGroup:
    """
    Represents a family group of applicants.

    Attributes:
        group_id: UUID of the family group.
        members: List of applicant IDs in the group.
        parent_applicant_id: ID of the primary/parent applicant.
    """

    group_id: str
    members: list[str]
    parent_applicant_id: str | None = None


# =============================================================================
# Applicant Service
# =============================================================================


class ApplicantService:
    """
    Service for managing applicants.

    Provides high-level business logic for applicant operations including
    creation, updates, status management, and PII lifecycle handling.

    Attributes:
        _repository: ApplicantRepository for data access.
        _logger: Structured logger.
        _event_handlers: Registered event callbacks.

    Example:
        service = ApplicantService()

        # Create applicant
        applicant = await service.create_applicant(
            agency_id="agency-uuid",
            first_name="John",
            last_name="Doe",
            birth_date="1990-01-15",
            nationality="TR",
            passport_number="U12345678",
            passport_expiry="2030-05-20",
            phone="+905551234567",
            target_country="DE",
            visa_type="tourist",
        )

        # Check eligibility
        is_eligible = await service.is_eligible_for_booking(applicant["id"])

        # Start processing
        await service.start_processing(applicant["id"])
    """

    # Default PII retention hours
    DEFAULT_PII_RETENTION_HOURS = 24

    # Maximum extension hours
    MAX_EXTENSION_HOURS = 48

    def __init__(
        self,
        repository: ApplicantRepository | None = None,
    ) -> None:
        """
        Initialize the applicant service.

        Args:
            repository: Optional ApplicantRepository. If not provided,
                creates a new instance.
        """
        self._repository = repository or ApplicantRepository()
        self._logger = logger.bind(service="applicant")

        # Event handlers
        self._event_handlers: dict[ApplicantEventType, list[Callable]] = {
            event_type: [] for event_type in ApplicantEventType
        }

        self._logger.info("applicant_service_initialized")

    # -------------------------------------------------------------------------
    # Event Handling
    # -------------------------------------------------------------------------

    def on_event(
        self,
        event_type: ApplicantEventType,
        handler: Callable[[ApplicantEvent], Any],
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
        event_type: ApplicantEventType,
        handler: Callable[[ApplicantEvent], Any],
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
        event_type: ApplicantEventType,
        applicant_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """
        Emit an applicant event to registered handlers.

        Args:
            event_type: Type of event.
            applicant_id: ID of the applicant.
            data: Additional event data.
        """
        event = ApplicantEvent(
            event_type=event_type,
            applicant_id=applicant_id,
            data=data or {},
        )

        self._logger.debug(
            "applicant_event_emitted",
            event_type=event_type.value,
            applicant_id=applicant_id,
        )

        for handler in self._event_handlers[event_type]:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                self._logger.error(
                    "applicant_event_handler_error",
                    event_type=event_type.value,
                    applicant_id=applicant_id,
                    error=str(e),
                )

    # -------------------------------------------------------------------------
    # Applicant CRUD Operations
    # -------------------------------------------------------------------------

    async def create_applicant(
        self,
        agency_id: str,
        first_name: str,
        last_name: str,
        birth_date: str | date,
        nationality: str,
        passport_number: str,
        passport_expiry: str | date,
        phone: str,
        target_country: str,
        visa_type: str,
        *,
        email: str | None = None,
        external_ref: str | None = None,
        target_city: str | None = None,
        preferred_dates: dict[str, str] | None = None,
        exclude_weekends: bool = False,
        family_group_id: str | None = None,
        parent_applicant_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a new applicant.

        Creates the applicant record with encrypted PII and sets
        the PII expiration time (24 hours from creation).

        Args:
            agency_id: ID of the agency submitting the applicant.
            first_name: Applicant's first name (will be encrypted).
            last_name: Applicant's last name (will be encrypted).
            birth_date: Applicant's date of birth.
            nationality: Nationality (ISO 3166-1 alpha-2).
            passport_number: Passport number (will be encrypted).
            passport_expiry: Passport expiration date.
            phone: Phone number with country code (will be encrypted).
            target_country: Target visa country code.
            visa_type: Visa type code.
            email: Optional email address (will be encrypted).
            external_ref: Optional agency's internal reference.
            target_city: Optional preferred appointment city.
            preferred_dates: Optional date range {"from": "...", "to": "..."}.
            exclude_weekends: Whether to exclude weekend appointments.
            family_group_id: Optional family group UUID.
            parent_applicant_id: Optional parent applicant UUID (for minors).

        Returns:
            Created applicant data with decrypted PII.

        Raises:
            InvalidApplicantDataError: If validation fails.
        """
        self._logger.info(
            "applicant_create_start",
            agency_id=agency_id,
            target_country=target_country,
            visa_type=visa_type,
        )

        # Normalize date fields
        if isinstance(birth_date, date):
            birth_date = birth_date.isoformat()
        if isinstance(passport_expiry, date):
            passport_expiry = passport_expiry.isoformat()

        # Validate dates
        validation_errors = self._validate_dates(birth_date, passport_expiry)
        if validation_errors:
            raise InvalidApplicantDataError(
                "Date validation failed",
                validation_errors=validation_errors,
            )

        # Prepare applicant data
        data: dict[str, Any] = {
            "agency_id": str(agency_id),
            "first_name": first_name,
            "last_name": last_name,
            "birth_date": birth_date,
            "nationality": nationality.upper(),
            "passport_number": passport_number.upper().replace(" ", ""),
            "passport_expiry": passport_expiry,
            "phone": self._normalize_phone(phone),
            "target_country": target_country.upper(),
            "visa_type": visa_type,
            "exclude_weekends": exclude_weekends,
        }

        if email:
            data["email"] = email
        if external_ref:
            data["external_ref"] = external_ref
        if target_city:
            data["target_city"] = target_city
        if preferred_dates:
            data["preferred_dates"] = preferred_dates
        if family_group_id:
            data["family_group_id"] = family_group_id
        if parent_applicant_id:
            data["parent_applicant_id"] = parent_applicant_id

        # Create applicant (repository handles encryption and expiry)
        applicant = await self._repository.create(data)
        applicant_id = str(applicant["id"])

        await self._emit_event(
            ApplicantEventType.CREATED,
            applicant_id,
            {
                "agency_id": agency_id,
                "target_country": target_country,
                "visa_type": visa_type,
            },
        )

        self._logger.info(
            "applicant_created",
            applicant_id=applicant_id,
            agency_id=agency_id,
        )

        return applicant

    async def create_applicant_from_schema(
        self,
        data: ApplicantCreate,
    ) -> dict[str, Any]:
        """
        Create an applicant from a Pydantic schema.

        Args:
            data: ApplicantCreate schema with validated data.

        Returns:
            Created applicant data.
        """
        preferred_dates = None
        if data.preferred_dates:
            preferred_dates = {
                "from": data.preferred_dates.from_date.isoformat(),
                "to": data.preferred_dates.to_date.isoformat(),
            }

        return await self.create_applicant(
            agency_id=str(data.agency_id),
            first_name=data.first_name,
            last_name=data.last_name,
            birth_date=data.birth_date,
            nationality=data.nationality,
            passport_number=data.passport_number,
            passport_expiry=data.passport_expiry,
            phone=data.phone,
            target_country=data.target_country,
            visa_type=data.visa_type,
            email=str(data.email) if data.email else None,
            external_ref=data.external_ref,
            target_city=data.target_city,
            preferred_dates=preferred_dates,
            exclude_weekends=data.exclude_weekends,
            family_group_id=str(data.family_group_id) if data.family_group_id else None,
            parent_applicant_id=str(data.parent_applicant_id) if data.parent_applicant_id else None,
        )

    async def get_applicant(
        self,
        applicant_id: str | UUID,
        *,
        fields: list[str] | None = None,
        decrypt: bool = True,
    ) -> dict[str, Any] | None:
        """
        Get an applicant by ID.

        Args:
            applicant_id: The applicant UUID.
            fields: Optional list of fields to return.
            decrypt: Whether to decrypt PII fields (default: True).

        Returns:
            Applicant data or None if not found.
        """
        self._logger.debug("applicant_get", applicant_id=str(applicant_id))
        return await self._repository.get_by_id(
            applicant_id,
            fields=fields,
            decrypt=decrypt,
        )

    async def get_applicant_or_raise(
        self,
        applicant_id: str | UUID,
        *,
        check_expiry: bool = True,
    ) -> dict[str, Any]:
        """
        Get an applicant by ID, raising if not found or expired.

        Args:
            applicant_id: The applicant UUID.
            check_expiry: Whether to check PII expiration.

        Returns:
            Applicant data with decrypted PII.

        Raises:
            ApplicantNotFoundError: If applicant not found.
            ApplicantExpiredError: If applicant PII has expired.
        """
        applicant = await self.get_applicant(applicant_id)

        if not applicant:
            raise ApplicantNotFoundError(str(applicant_id))

        if check_expiry and self._is_pii_expired(applicant):
            raise ApplicantExpiredError(str(applicant_id))

        return applicant

    async def update_applicant(
        self,
        applicant_id: str | UUID,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Update an applicant.

        Note: PII fields should not be updated after creation
        (per audit requirements). Only non-PII fields are allowed.

        Args:
            applicant_id: The applicant UUID.
            data: Fields to update (non-PII only).

        Returns:
            Updated applicant data.

        Raises:
            ApplicantNotFoundError: If applicant not found.
            InvalidApplicantDataError: If trying to update PII fields.
        """
        applicant_id_str = str(applicant_id)

        # Verify applicant exists
        await self.get_applicant_or_raise(applicant_id, check_expiry=False)

        # Prevent PII updates
        pii_fields = {"first_name", "last_name", "passport_number", "phone", "email"}
        attempted_pii_updates = set(data.keys()) & pii_fields
        if attempted_pii_updates:
            raise InvalidApplicantDataError(
                "Cannot update PII fields after creation",
                validation_errors=[f"Cannot update: {', '.join(attempted_pii_updates)}"],
            )

        self._logger.info(
            "applicant_update",
            applicant_id=applicant_id_str,
            fields=list(data.keys()),
        )

        updated = await self._repository.update(applicant_id, data)

        await self._emit_event(
            ApplicantEventType.UPDATED,
            applicant_id_str,
            {"updated_fields": list(data.keys())},
        )

        return updated

    async def update_applicant_from_schema(
        self,
        applicant_id: str | UUID,
        data: ApplicantUpdate,
    ) -> dict[str, Any]:
        """
        Update an applicant from a Pydantic schema.

        Args:
            applicant_id: The applicant UUID.
            data: ApplicantUpdate schema with fields to update.

        Returns:
            Updated applicant data.
        """
        update_data = {
            k: v for k, v in data.model_dump().items() if v is not None
        }

        # Handle preferred_dates separately
        if "preferred_dates" in update_data and update_data["preferred_dates"]:
            pref = update_data["preferred_dates"]
            if hasattr(pref, "from_date"):
                update_data["preferred_dates"] = {
                    "from": pref.from_date.isoformat(),
                    "to": pref.to_date.isoformat(),
                }

        return await self.update_applicant(applicant_id, update_data)

    async def delete_applicant(self, applicant_id: str | UUID) -> None:
        """
        Soft delete an applicant by redacting PII.

        Per GDPR/offshore compliance, this redacts PII fields rather than
        deleting the record entirely, maintaining audit trail.

        Args:
            applicant_id: The applicant UUID.

        Raises:
            ApplicantNotFoundError: If applicant not found.
        """
        applicant_id_str = str(applicant_id)

        # Verify applicant exists
        await self.get_applicant_or_raise(applicant_id, check_expiry=False)

        self._logger.info("applicant_delete", applicant_id=applicant_id_str)

        await self._repository.soft_delete(applicant_id)

        await self._emit_event(ApplicantEventType.REDACTED, applicant_id_str)

    # -------------------------------------------------------------------------
    # Status Management
    # -------------------------------------------------------------------------

    async def update_status(
        self,
        applicant_id: str | UUID,
        status: ApplicantStatus | str,
    ) -> dict[str, Any]:
        """
        Update an applicant's status.

        Args:
            applicant_id: The applicant UUID.
            status: New status value.

        Returns:
            Updated applicant data.

        Raises:
            ApplicantNotFoundError: If applicant not found.
        """
        applicant_id_str = str(applicant_id)
        status_value = status.value if isinstance(status, ApplicantStatus) else status

        # Verify applicant exists
        applicant = await self.get_applicant_or_raise(applicant_id, check_expiry=False)
        previous_status = applicant.get("status")

        self._logger.info(
            "applicant_status_update",
            applicant_id=applicant_id_str,
            previous_status=previous_status,
            new_status=status_value,
        )

        updated = await self._repository.update_status(applicant_id, status)

        await self._emit_event(
            ApplicantEventType.STATUS_CHANGED,
            applicant_id_str,
            {"previous_status": previous_status, "new_status": status_value},
        )

        return updated

    async def start_processing(
        self,
        applicant_id: str | UUID,
    ) -> dict[str, Any]:
        """
        Mark applicant as processing (booking in progress).

        Also extends PII expiry to ensure data is available during booking.

        Args:
            applicant_id: The applicant UUID.

        Returns:
            Updated applicant data.

        Raises:
            ApplicantNotFoundError: If applicant not found.
            ApplicantNotEligibleError: If applicant cannot be processed.
        """
        applicant_id_str = str(applicant_id)
        applicant = await self.get_applicant_or_raise(applicant_id)

        # Verify eligibility
        if not await self.is_eligible_for_booking(applicant_id):
            raise ApplicantNotEligibleError(
                "Applicant cannot be processed",
                applicant_id=applicant_id_str,
                reason="Not in pending status or already expired",
            )

        # Extend expiry for processing
        await self.extend_expiry(applicant_id, hours=4)

        updated = await self.update_status(applicant_id, ApplicantStatus.PROCESSING)

        await self._emit_event(
            ApplicantEventType.PROCESSING_STARTED,
            applicant_id_str,
        )

        return updated

    async def mark_completed(
        self,
        applicant_id: str | UUID,
    ) -> dict[str, Any]:
        """
        Mark applicant as completed (booking successful).

        Args:
            applicant_id: The applicant UUID.

        Returns:
            Updated applicant data.
        """
        applicant_id_str = str(applicant_id)

        updated = await self.update_status(applicant_id, ApplicantStatus.COMPLETED)

        await self._emit_event(
            ApplicantEventType.COMPLETED,
            applicant_id_str,
        )

        return updated

    async def mark_failed(
        self,
        applicant_id: str | UUID,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """
        Mark applicant as failed (booking unsuccessful).

        Args:
            applicant_id: The applicant UUID.
            reason: Optional failure reason.

        Returns:
            Updated applicant data.
        """
        applicant_id_str = str(applicant_id)

        updated = await self.update_status(applicant_id, ApplicantStatus.FAILED)

        await self._emit_event(
            ApplicantEventType.FAILED,
            applicant_id_str,
            {"reason": reason} if reason else {},
        )

        return updated

    # -------------------------------------------------------------------------
    # PII Expiration Management
    # -------------------------------------------------------------------------

    async def extend_expiry(
        self,
        applicant_id: str | UUID,
        hours: int = 1,
    ) -> dict[str, Any]:
        """
        Extend PII expiry for an applicant.

        Used when applicant is in processing and needs more time.
        Maximum extension is limited to prevent indefinite retention.

        Args:
            applicant_id: The applicant UUID.
            hours: Number of hours to extend (max 48).

        Returns:
            Updated applicant data.

        Raises:
            ApplicantNotFoundError: If applicant not found.
            InvalidApplicantDataError: If extension exceeds maximum.
        """
        applicant_id_str = str(applicant_id)

        if hours > self.MAX_EXTENSION_HOURS:
            raise InvalidApplicantDataError(
                f"Extension cannot exceed {self.MAX_EXTENSION_HOURS} hours",
                validation_errors=[f"Requested {hours} hours, max is {self.MAX_EXTENSION_HOURS}"],
            )

        # Verify applicant exists
        await self.get_applicant_or_raise(applicant_id, check_expiry=False)

        self._logger.info(
            "applicant_expiry_extend",
            applicant_id=applicant_id_str,
            hours=hours,
        )

        updated = await self._repository.extend_expiry(applicant_id, hours=hours)

        await self._emit_event(
            ApplicantEventType.EXPIRY_EXTENDED,
            applicant_id_str,
            {"hours_extended": hours},
        )

        return updated

    async def redact_expired_applicants(
        self,
        *,
        batch_size: int = 100,
    ) -> int:
        """
        Redact PII for expired applicants.

        This implements the auto-cleanup job from 002-DIRECTUS-SCHEMA.md.
        Should be called by a scheduled task (e.g., hourly via Celery Beat).

        Args:
            batch_size: Number of records to process per batch.

        Returns:
            Number of applicants redacted.
        """
        self._logger.info(
            "applicant_pii_redaction_start",
            batch_size=batch_size,
        )

        redacted_count = await self._repository.redact_expired_pii(
            batch_size=batch_size,
        )

        if redacted_count > 0:
            self._logger.info(
                "applicant_pii_redaction_complete",
                redacted_count=redacted_count,
            )

        return redacted_count

    # -------------------------------------------------------------------------
    # Eligibility Checks
    # -------------------------------------------------------------------------

    async def is_eligible_for_booking(
        self,
        applicant_id: str | UUID,
    ) -> bool:
        """
        Check if an applicant is eligible for booking.

        An applicant is eligible if:
        - Status is PENDING
        - PII has not expired
        - Passport is not expired

        Args:
            applicant_id: The applicant UUID.

        Returns:
            True if applicant is eligible for booking.
        """
        applicant = await self.get_applicant(applicant_id)

        if not applicant:
            return False

        # Check status
        if applicant.get("status") != ApplicantStatus.PENDING.value:
            return False

        # Check PII expiration
        if self._is_pii_expired(applicant):
            return False

        # Check passport validity
        passport_expiry = applicant.get("passport_expiry")
        if passport_expiry:
            try:
                expiry_date = (
                    datetime.fromisoformat(passport_expiry).date()
                    if isinstance(passport_expiry, str)
                    else passport_expiry
                )
                if expiry_date <= date.today():
                    return False
            except (ValueError, TypeError):
                pass

        return True

    async def get_eligible_applicants(
        self,
        *,
        agency_id: str | UUID | None = None,
        target_country: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Get applicants eligible for booking.

        Returns pending applicants that have not expired.

        Args:
            agency_id: Optional agency ID filter.
            target_country: Optional country filter.
            limit: Maximum number of results.

        Returns:
            List of eligible applicants.
        """
        return await self._repository.find_pending_for_booking(
            agency_id=agency_id,
            target_country=target_country,
            limit=limit,
        )

    # -------------------------------------------------------------------------
    # Family Group Operations
    # -------------------------------------------------------------------------

    async def create_family_group(
        self,
        applicant_ids: list[str | UUID],
        parent_applicant_id: str | UUID | None = None,
    ) -> FamilyGroup:
        """
        Create a family group linking multiple applicants.

        Family groups allow booking appointments together.

        Args:
            applicant_ids: List of applicant UUIDs to group.
            parent_applicant_id: Optional primary applicant UUID.

        Returns:
            FamilyGroup with group details.

        Raises:
            ApplicantNotFoundError: If any applicant not found.
        """
        group_id = str(uuid4())

        # Verify all applicants exist and are from same agency
        agency_ids = set()
        for applicant_id in applicant_ids:
            applicant = await self.get_applicant_or_raise(applicant_id, check_expiry=False)
            agency_ids.add(applicant.get("agency_id"))

        if len(agency_ids) > 1:
            raise InvalidApplicantDataError(
                "All applicants in a family group must belong to the same agency",
            )

        # Set parent if specified
        parent_id = str(parent_applicant_id) if parent_applicant_id else str(applicant_ids[0])

        # Update all applicants with family group info
        for applicant_id in applicant_ids:
            update_data: dict[str, Any] = {"family_group_id": group_id}
            if str(applicant_id) != parent_id:
                update_data["parent_applicant_id"] = parent_id

            await self._repository.update(applicant_id, update_data)

        family_group = FamilyGroup(
            group_id=group_id,
            members=[str(aid) for aid in applicant_ids],
            parent_applicant_id=parent_id,
        )

        await self._emit_event(
            ApplicantEventType.FAMILY_GROUP_CREATED,
            parent_id,
            {"group_id": group_id, "member_count": len(applicant_ids)},
        )

        self._logger.info(
            "family_group_created",
            group_id=group_id,
            member_count=len(applicant_ids),
        )

        return family_group

    async def get_family_group(
        self,
        family_group_id: str | UUID,
    ) -> list[dict[str, Any]]:
        """
        Get all applicants in a family group.

        Args:
            family_group_id: The family group UUID.

        Returns:
            List of applicants in the group.
        """
        return await self._repository.find_by_family_group(family_group_id)

    # -------------------------------------------------------------------------
    # Query Operations
    # -------------------------------------------------------------------------

    async def list_applicants(
        self,
        agency_id: str | UUID,
        *,
        status: ApplicantStatus | str | None = None,
        include_deleted: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        List applicants for an agency.

        Args:
            agency_id: The agency UUID.
            status: Optional status filter.
            include_deleted: Include soft-deleted applicants.
            limit: Maximum results.
            offset: Result offset.

        Returns:
            List of applicant records.
        """
        return await self._repository.find_by_agency(
            agency_id,
            status=status,
            include_deleted=include_deleted,
            limit=limit,
            offset=offset,
        )

    async def count_applicants(
        self,
        *,
        agency_id: str | UUID | None = None,
        status: ApplicantStatus | str | None = None,
    ) -> int:
        """
        Count applicants.

        Args:
            agency_id: Optional agency filter.
            status: Optional status filter.

        Returns:
            Number of applicants.
        """
        if status:
            return await self._repository.count_by_status(
                status,
                agency_id=agency_id,
            )
        # Default to counting pending
        return await self._repository.count_by_status(
            ApplicantStatus.PENDING,
            agency_id=agency_id,
        )

    async def get_applicant_summary(
        self,
        applicant_id: str | UUID,
    ) -> ApplicantSummary | None:
        """
        Get a summary of an applicant without PII.

        Args:
            applicant_id: The applicant UUID.

        Returns:
            ApplicantSummary or None if not found.
        """
        applicant = await self._repository.get_by_id(
            applicant_id,
            decrypt=False,  # Don't need PII for summary
        )

        if not applicant:
            return None

        expires_at = applicant.get("expires_at")
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))

        created_at = applicant.get("created_at") or applicant.get("date_created")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))

        return ApplicantSummary(
            applicant_id=str(applicant["id"]),
            agency_id=str(applicant.get("agency_id", "")),
            external_ref=applicant.get("external_ref"),
            status=ApplicantStatus(applicant.get("status", "pending")),
            nationality=applicant.get("nationality", ""),
            target_country=applicant.get("target_country", ""),
            visa_type=applicant.get("visa_type", ""),
            created_at=created_at,
            expires_at=expires_at,
            is_expired=self._is_pii_expired(applicant),
        )

    # -------------------------------------------------------------------------
    # Batch Operations
    # -------------------------------------------------------------------------

    async def create_applicants_batch(
        self,
        applicants: list[ApplicantCreate],
    ) -> list[dict[str, Any]]:
        """
        Create multiple applicants in batch.

        Used for Google Sheets sync or bulk import.

        Args:
            applicants: List of ApplicantCreate schemas.

        Returns:
            List of created applicant data.
        """
        results = []

        for applicant_data in applicants:
            try:
                created = await self.create_applicant_from_schema(applicant_data)
                results.append(created)
            except ApplicantError as e:
                self._logger.warning(
                    "batch_create_failed",
                    external_ref=applicant_data.external_ref,
                    error=str(e),
                )
                # Continue with next applicant
                results.append({
                    "error": str(e),
                    "external_ref": applicant_data.external_ref,
                })

        self._logger.info(
            "batch_create_complete",
            total=len(applicants),
            success=len([r for r in results if "error" not in r]),
        )

        return results

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    def _is_pii_expired(self, applicant: dict[str, Any]) -> bool:
        """
        Check if applicant PII has expired.

        Args:
            applicant: Applicant data.

        Returns:
            True if PII is expired or redacted.
        """
        # Check if already redacted
        if applicant.get("deleted_at") is not None:
            return True

        if applicant.get("first_name") == "[REDACTED]":
            return True

        # Check expiration time
        expires_at = applicant.get("expires_at")
        if expires_at:
            try:
                if isinstance(expires_at, str):
                    expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if expires_at.tzinfo:
                    now = datetime.now(expires_at.tzinfo)
                else:
                    now = datetime.utcnow()
                return expires_at < now
            except (ValueError, TypeError):
                pass

        return False

    def _validate_dates(
        self,
        birth_date: str,
        passport_expiry: str,
    ) -> list[str]:
        """
        Validate birth date and passport expiry.

        Args:
            birth_date: Birth date string (YYYY-MM-DD).
            passport_expiry: Passport expiry string (YYYY-MM-DD).

        Returns:
            List of validation error messages.
        """
        errors = []

        try:
            bd = datetime.fromisoformat(birth_date).date()
            if bd >= date.today():
                errors.append("Birth date must be in the past")
        except ValueError:
            errors.append("Invalid birth date format")

        try:
            pe = datetime.fromisoformat(passport_expiry).date()
            if pe <= date.today():
                errors.append("Passport must not be expired")
        except ValueError:
            errors.append("Invalid passport expiry format")

        return errors

    def _normalize_phone(self, phone: str) -> str:
        """
        Normalize phone number format.

        Args:
            phone: Raw phone number.

        Returns:
            Normalized phone number with country code.
        """
        cleaned = phone.replace(" ", "").replace("-", "")
        if not cleaned.startswith("+"):
            cleaned = "+" + cleaned
        return cleaned


# =============================================================================
# Factory Functions
# =============================================================================


@lru_cache
def get_applicant_service() -> ApplicantService:
    """
    Get a cached ApplicantService instance.

    The service is cached for reuse across requests.
    Use this for dependency injection in FastAPI.

    Returns:
        Configured ApplicantService instance.

    Example:
        from fastapi import Depends

        @router.get("/applicants/{applicant_id}")
        async def get_applicant(
            applicant_id: str,
            service: ApplicantService = Depends(get_applicant_service),
        ):
            return await service.get_applicant(applicant_id)
    """
    return ApplicantService()


def clear_applicant_service_cache() -> None:
    """
    Clear the cached ApplicantService.

    Useful for testing or when dependencies change.
    """
    get_applicant_service.cache_clear()


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Service
    "ApplicantService",
    "get_applicant_service",
    "clear_applicant_service_cache",
    # Data classes
    "ApplicantSummary",
    "FamilyGroup",
    "ApplicantEvent",
    "ApplicantEventType",
    # Exceptions
    "ApplicantError",
    "ApplicantNotFoundError",
    "ApplicantExpiredError",
    "ApplicantNotEligibleError",
    "InvalidApplicantDataError",
]
