"""
Pydantic schemas for booking requests and results.

This module provides data validation and serialization schemas for the booking
workflow in VISE OS. These schemas correspond to the booking_requests and
booking_results collections in Directus as defined in 002-DIRECTUS-SCHEMA.md.

Schemas:
- BookingRequest/BookingResponse: Main booking request data
- BookingResultResponse: Completed booking results (PII-free audit record)
- BookingStatusUpdate: For status transitions

Usage:
    from src.api.schemas.booking import BookingRequest, BookingResponse

    # Create a new booking request
    booking = BookingRequest(
        agency_id="...",
        applicant_id="...",
        target_system="vfs",
        target_country="DE",
        visa_category="tourist",
    )

    # Validate incoming data
    booking = BookingRequest.model_validate(request_data)
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# =============================================================================
# Enums
# =============================================================================


class BookingStatus(str, Enum):
    """
    Booking request status states.

    State machine transitions:
        pending -> queued -> processing -> slot_found -> booking ->
            -> payment -> verifying -> completed
            -> failed (any state)
            -> expired (timeout)
            -> cancelled (manual)
    """

    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    SLOT_FOUND = "slot_found"
    BOOKING = "booking"
    PAYMENT = "payment"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class TargetSystem(str, Enum):
    """Supported visa appointment booking systems."""

    VFS = "vfs"
    IDATA = "idata"
    BLS = "bls"
    KKOSMOS = "kkosmos"


class BookingResultStatus(str, Enum):
    """Status values for completed booking results."""

    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


# =============================================================================
# Base Schemas
# =============================================================================


class BookingBase(BaseModel):
    """
    Base schema with common booking fields.

    Contains shared fields between request and response schemas.
    """

    priority: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Priority level: 1=urgent, 5=normal, 10=low",
    )
    target_system: TargetSystem = Field(
        description="Target booking system (vfs, idata, bls, kkosmos)",
    )
    target_country: str = Field(
        min_length=2,
        max_length=2,
        description="ISO 3166-1 alpha-2 country code",
    )
    target_location: str | None = Field(
        default=None,
        description="City/branch code for appointment",
    )
    visa_category: str = Field(
        min_length=1,
        max_length=50,
        description="Visa category code",
    )
    max_attempts: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of booking attempts",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Additional booking metadata",
    )

    @field_validator("target_country")
    @classmethod
    def validate_country_code(cls, v: str) -> str:
        """Ensure country code is uppercase."""
        return v.upper()


# =============================================================================
# Request Schemas
# =============================================================================


class BookingRequest(BookingBase):
    """
    Schema for creating a new booking request.

    This schema is used when an agency submits a new visa appointment
    booking request via the API.

    Example:
        booking = BookingRequest(
            agency_id=UUID("..."),
            applicant_id=UUID("..."),
            target_system="vfs",
            target_country="DE",
            visa_category="tourist",
            priority=3,
        )
    """

    agency_id: UUID = Field(
        description="ID of the agency submitting the request",
    )
    applicant_id: UUID = Field(
        description="ID of the applicant for this booking",
    )

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "agency_id": "550e8400-e29b-41d4-a716-446655440000",
                "applicant_id": "550e8400-e29b-41d4-a716-446655440001",
                "target_system": "vfs",
                "target_country": "DE",
                "visa_category": "tourist",
                "priority": 5,
                "max_attempts": 50,
            }
        },
    )


class BookingStatusUpdate(BaseModel):
    """
    Schema for updating booking status.

    Used for status transitions in the booking state machine.
    """

    status: BookingStatus = Field(
        description="New status for the booking",
    )
    error_code: str | None = Field(
        default=None,
        max_length=50,
        description="Error code if status is failed",
    )
    error_message: str | None = Field(
        default=None,
        max_length=500,
        description="Error message if status is failed",
    )

    model_config = ConfigDict(
        str_strip_whitespace=True,
    )


class BookingBatchRequest(BaseModel):
    """
    Schema for batch booking operations.

    Used when processing multiple booking requests at once.
    """

    bookings: list[BookingRequest] = Field(
        min_length=1,
        max_length=100,
        description="List of booking requests to process",
    )

    model_config = ConfigDict(
        str_strip_whitespace=True,
    )


# =============================================================================
# Response Schemas
# =============================================================================


class BookingResponse(BookingBase):
    """
    Schema for booking request responses.

    Returns full booking request data including server-generated fields.
    """

    id: UUID = Field(
        description="Unique booking request ID",
    )
    agency_id: UUID = Field(
        description="ID of the agency that submitted the request",
    )
    applicant_id: UUID = Field(
        description="ID of the applicant for this booking",
    )
    status: BookingStatus = Field(
        description="Current booking status",
    )
    attempts: int = Field(
        default=0,
        ge=0,
        description="Number of booking attempts made",
    )
    slot_found_count: int = Field(
        default=0,
        ge=0,
        description="Number of slots found but not captured",
    )
    last_attempt_at: datetime | None = Field(
        default=None,
        description="Timestamp of the last booking attempt",
    )
    next_attempt_at: datetime | None = Field(
        default=None,
        description="Scheduled time for the next attempt",
    )
    error_code: str | None = Field(
        default=None,
        description="Last error code if failed",
    )
    error_message: str | None = Field(
        default=None,
        description="Last error message if failed",
    )
    assigned_account_id: UUID | None = Field(
        default=None,
        description="ID of assigned bot account",
    )
    assigned_proxy_id: UUID | None = Field(
        default=None,
        description="ID of assigned proxy",
    )
    created_at: datetime = Field(
        description="When the request was created",
    )
    updated_at: datetime = Field(
        description="When the request was last updated",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="When the booking was completed",
    )

    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440002",
                "agency_id": "550e8400-e29b-41d4-a716-446655440000",
                "applicant_id": "550e8400-e29b-41d4-a716-446655440001",
                "status": "pending",
                "priority": 5,
                "target_system": "vfs",
                "target_country": "DE",
                "visa_category": "tourist",
                "attempts": 0,
                "max_attempts": 50,
                "slot_found_count": 0,
                "created_at": "2025-02-01T12:00:00Z",
                "updated_at": "2025-02-01T12:00:00Z",
            }
        },
    )


class BookingResultResponse(BaseModel):
    """
    Schema for booking result responses.

    Represents completed booking audit records. Does NOT contain PII.
    Corresponds to the booking_results collection in Directus.
    """

    id: UUID = Field(
        description="Unique booking result ID",
    )
    agency_id: UUID = Field(
        description="ID of the agency",
    )
    booking_request_id: UUID = Field(
        description="ID of the original booking request",
    )
    status: BookingResultStatus = Field(
        description="Final booking status",
    )
    target_system: TargetSystem = Field(
        description="Target booking system used",
    )
    target_country: str = Field(
        description="Target country code",
    )
    confirmation_number: str | None = Field(
        default=None,
        description="Appointment confirmation number (encrypted)",
    )
    appointment_date: datetime | None = Field(
        default=None,
        description="Scheduled appointment date",
    )
    appointment_time: str | None = Field(
        default=None,
        description="Scheduled appointment time (HH:MM)",
    )
    appointment_location: str | None = Field(
        default=None,
        description="Appointment location/address",
    )
    total_attempts: int = Field(
        ge=0,
        description="Total number of booking attempts made",
    )
    total_duration_seconds: int = Field(
        ge=0,
        description="Total duration from request to completion",
    )
    credits_charged: int = Field(
        ge=0,
        description="Number of credits charged for this booking",
    )
    error_code: str | None = Field(
        default=None,
        description="Error code if failed",
    )
    screenshot_url: str | None = Field(
        default=None,
        description="URL to confirmation screenshot (S3)",
    )
    created_at: datetime = Field(
        description="When the result was recorded",
    )

    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
    )


class BookingListResponse(BaseModel):
    """
    Schema for paginated booking list responses.

    Used for listing booking requests with pagination metadata.
    """

    items: list[BookingResponse] = Field(
        description="List of booking requests",
    )
    total: int = Field(
        ge=0,
        description="Total number of items matching the query",
    )
    page: int = Field(
        ge=1,
        description="Current page number",
    )
    page_size: int = Field(
        ge=1,
        le=100,
        description="Number of items per page",
    )
    has_more: bool = Field(
        description="Whether there are more pages available",
    )

    model_config = ConfigDict(
        str_strip_whitespace=True,
    )


# =============================================================================
# Statistics Schemas
# =============================================================================


class BookingStats(BaseModel):
    """
    Schema for booking statistics.

    Used by the /booking-stats endpoint for agency dashboards.
    """

    total_requests: int = Field(
        ge=0,
        description="Total number of booking requests",
    )
    completed: int = Field(
        ge=0,
        description="Number of successfully completed bookings",
    )
    failed: int = Field(
        ge=0,
        description="Number of failed booking attempts",
    )
    pending: int = Field(
        ge=0,
        description="Number of pending/queued bookings",
    )
    success_rate: float = Field(
        ge=0.0,
        le=1.0,
        description="Success rate as decimal (0.0 - 1.0)",
    )
    avg_duration_seconds: float = Field(
        ge=0.0,
        description="Average time to complete a booking",
    )
    credits_used: int = Field(
        ge=0,
        description="Total credits consumed",
    )
    by_country: dict[str, dict[str, int]] = Field(
        default_factory=dict,
        description="Breakdown by country with completed/failed counts",
    )

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "total_requests": 150,
                "completed": 120,
                "failed": 20,
                "pending": 10,
                "success_rate": 0.857,
                "avg_duration_seconds": 145.5,
                "credits_used": 120,
                "by_country": {
                    "DE": {"completed": 50, "failed": 5},
                    "IT": {"completed": 40, "failed": 10},
                    "FR": {"completed": 30, "failed": 5},
                },
            }
        },
    )


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Enums
    "BookingStatus",
    "TargetSystem",
    "BookingResultStatus",
    # Request Schemas
    "BookingRequest",
    "BookingStatusUpdate",
    "BookingBatchRequest",
    # Response Schemas
    "BookingResponse",
    "BookingResultResponse",
    "BookingListResponse",
    # Statistics
    "BookingStats",
]
