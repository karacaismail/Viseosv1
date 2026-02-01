"""
Pydantic schemas for applicant data.

This module provides data validation and serialization schemas for applicants
in VISE OS. These schemas correspond to the applicants collection in Directus
as defined in 002-DIRECTUS-SCHEMA.md.

CRITICAL: Applicant data contains PII (Personally Identifiable Information).
Per offshore compliance requirements, PII has a 24-hour retention period.

Encrypted fields:
- first_name, last_name
- passport_number
- phone, email

Schemas:
- ApplicantCreate/ApplicantResponse: Applicant management
- ApplicantBatchCreate: Bulk applicant creation

Usage:
    from src.api.schemas.applicant import ApplicantCreate, ApplicantResponse

    # Create a new applicant
    applicant = ApplicantCreate(
        agency_id=UUID("..."),
        first_name="John",
        last_name="Doe",
        birth_date=date(1990, 1, 15),
        nationality="TR",
        passport_number="U12345678",
        passport_expiry=date(2030, 5, 20),
        phone="+905551234567",
        target_country="DE",
        visa_type="tourist",
    )
"""

from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


# =============================================================================
# Enums
# =============================================================================


class ApplicantStatus(str, Enum):
    """
    Applicant processing status values.

    - pending: Applicant submitted, awaiting processing
    - processing: Booking attempt in progress
    - completed: Appointment successfully booked
    - failed: Booking failed after max attempts
    - expired: PII retention period exceeded
    """

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


# =============================================================================
# Nested Schemas
# =============================================================================


class PreferredDateRange(BaseModel):
    """
    Schema for preferred appointment date range.

    Specifies the date window in which the applicant prefers
    to have their appointment scheduled.
    """

    from_date: date = Field(
        alias="from",
        description="Earliest acceptable appointment date",
    )
    to_date: date = Field(
        alias="to",
        description="Latest acceptable appointment date",
    )

    @model_validator(mode="after")
    def validate_date_range(self) -> "PreferredDateRange":
        """Ensure from_date is before to_date."""
        if self.from_date > self.to_date:
            msg = "from_date must be before or equal to to_date"
            raise ValueError(msg)
        return self

    model_config = ConfigDict(
        str_strip_whitespace=True,
        populate_by_name=True,
    )


# =============================================================================
# Request Schemas
# =============================================================================


class ApplicantCreate(BaseModel):
    """
    Schema for creating a new applicant.

    Contains all required and optional fields for visa appointment applicants.
    PII fields are encrypted at rest in the database.
    """

    agency_id: UUID = Field(
        description="ID of the agency submitting the applicant",
    )
    external_ref: str | None = Field(
        default=None,
        max_length=100,
        description="Agency's internal reference number",
    )
    # PII Fields (encrypted at rest)
    first_name: Annotated[str, Field(
        min_length=1,
        max_length=100,
        description="Applicant's first name (encrypted)",
    )]
    last_name: Annotated[str, Field(
        min_length=1,
        max_length=100,
        description="Applicant's last name (encrypted)",
    )]
    birth_date: date = Field(
        description="Applicant's date of birth",
    )
    nationality: str = Field(
        min_length=2,
        max_length=2,
        description="Nationality (ISO 3166-1 alpha-2)",
    )
    passport_number: Annotated[str, Field(
        min_length=5,
        max_length=20,
        description="Passport number (encrypted)",
    )]
    passport_expiry: date = Field(
        description="Passport expiration date",
    )
    phone: Annotated[str, Field(
        min_length=10,
        max_length=20,
        description="Phone number with country code (encrypted)",
    )]
    email: EmailStr | None = Field(
        default=None,
        description="Email address (encrypted)",
    )
    # Appointment preferences
    target_country: str = Field(
        min_length=2,
        max_length=2,
        description="Target visa country (ISO 3166-1 alpha-2)",
    )
    target_city: str | None = Field(
        default=None,
        max_length=100,
        description="Preferred appointment city",
    )
    visa_type: str = Field(
        min_length=1,
        max_length=50,
        description="Visa type code",
    )
    preferred_dates: PreferredDateRange | None = Field(
        default=None,
        description="Preferred appointment date range",
    )
    exclude_weekends: bool = Field(
        default=False,
        description="Exclude weekend appointments",
    )
    # Family grouping
    family_group_id: UUID | None = Field(
        default=None,
        description="Group ID for family bookings",
    )
    parent_applicant_id: UUID | None = Field(
        default=None,
        description="Parent applicant ID (for minor children)",
    )

    @field_validator("nationality", "target_country")
    @classmethod
    def validate_country_code(cls, v: str) -> str:
        """Ensure country codes are uppercase."""
        return v.upper()

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        """Validate and normalize phone number."""
        cleaned = v.replace(" ", "").replace("-", "")
        if not cleaned.startswith("+"):
            cleaned = "+" + cleaned
        return cleaned

    @field_validator("passport_number")
    @classmethod
    def validate_passport(cls, v: str) -> str:
        """Normalize passport number (uppercase, no spaces)."""
        return v.upper().replace(" ", "")

    @field_validator("birth_date")
    @classmethod
    def validate_birth_date(cls, v: date) -> date:
        """Ensure birth date is in the past."""
        if v >= date.today():
            msg = "Birth date must be in the past"
            raise ValueError(msg)
        return v

    @field_validator("passport_expiry")
    @classmethod
    def validate_passport_expiry(cls, v: date) -> date:
        """Ensure passport is not expired."""
        if v <= date.today():
            msg = "Passport must not be expired"
            raise ValueError(msg)
        return v

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "agency_id": "550e8400-e29b-41d4-a716-446655440000",
                "external_ref": "AGY-2025-001",
                "first_name": "John",
                "last_name": "Doe",
                "birth_date": "1990-05-15",
                "nationality": "TR",
                "passport_number": "U12345678",
                "passport_expiry": "2030-05-20",
                "phone": "+905551234567",
                "email": "john.doe@example.com",
                "target_country": "DE",
                "target_city": "Berlin",
                "visa_type": "tourist",
                "preferred_dates": {
                    "from": "2025-03-01",
                    "to": "2025-04-30",
                },
                "exclude_weekends": False,
            }
        },
    )


class ApplicantUpdate(BaseModel):
    """
    Schema for updating an existing applicant.

    Only non-PII fields can be updated after creation.
    PII fields are immutable to maintain audit integrity.
    """

    external_ref: str | None = Field(
        default=None,
        max_length=100,
        description="Agency's internal reference number",
    )
    target_city: str | None = Field(
        default=None,
        max_length=100,
        description="Preferred appointment city",
    )
    preferred_dates: PreferredDateRange | None = Field(
        default=None,
        description="Preferred appointment date range",
    )
    exclude_weekends: bool | None = Field(
        default=None,
        description="Exclude weekend appointments",
    )

    model_config = ConfigDict(
        str_strip_whitespace=True,
    )


class ApplicantBatchCreate(BaseModel):
    """
    Schema for batch applicant creation.

    Used when importing multiple applicants at once,
    e.g., from Google Sheets sync.
    """

    applicants: list[ApplicantCreate] = Field(
        min_length=1,
        max_length=100,
        description="List of applicants to create",
    )

    model_config = ConfigDict(
        str_strip_whitespace=True,
    )


# =============================================================================
# Response Schemas
# =============================================================================


class ApplicantResponse(BaseModel):
    """
    Schema for applicant responses.

    Returns applicant data including server-generated fields.
    PII fields may be redacted after 24-hour retention period.
    """

    id: UUID = Field(
        description="Unique applicant ID",
    )
    agency_id: UUID = Field(
        description="Associated agency ID",
    )
    external_ref: str | None = Field(
        default=None,
        description="Agency's internal reference number",
    )
    status: ApplicantStatus = Field(
        description="Current processing status",
    )
    # PII Fields (may be redacted)
    first_name: str = Field(
        description="Applicant's first name (may be [REDACTED])",
    )
    last_name: str = Field(
        description="Applicant's last name (may be [REDACTED])",
    )
    birth_date: date = Field(
        description="Applicant's date of birth",
    )
    nationality: str = Field(
        description="Nationality (ISO 3166-1 alpha-2)",
    )
    passport_number: str = Field(
        description="Passport number (may be [REDACTED])",
    )
    passport_expiry: date = Field(
        description="Passport expiration date",
    )
    phone: str = Field(
        description="Phone number (may be [REDACTED])",
    )
    email: str | None = Field(
        default=None,
        description="Email address (may be [REDACTED])",
    )
    # Appointment preferences
    target_country: str = Field(
        description="Target visa country",
    )
    target_city: str | None = Field(
        default=None,
        description="Preferred appointment city",
    )
    visa_type: str = Field(
        description="Visa type code",
    )
    preferred_dates: dict[str, str] | None = Field(
        default=None,
        description="Preferred appointment date range",
    )
    exclude_weekends: bool = Field(
        description="Whether to exclude weekend appointments",
    )
    # Family grouping
    family_group_id: UUID | None = Field(
        default=None,
        description="Group ID for family bookings",
    )
    parent_applicant_id: UUID | None = Field(
        default=None,
        description="Parent applicant ID (for minor children)",
    )
    # Timestamps
    created_at: datetime = Field(
        description="When the applicant was created",
    )
    expires_at: datetime = Field(
        description="When PII will be redacted (created + 24h)",
    )
    deleted_at: datetime | None = Field(
        default=None,
        description="Soft delete timestamp",
    )

    @property
    def is_pii_valid(self) -> bool:
        """Check if PII is still valid (not expired/redacted)."""
        return self.deleted_at is None and self.first_name != "[REDACTED]"

    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440001",
                "agency_id": "550e8400-e29b-41d4-a716-446655440000",
                "external_ref": "AGY-2025-001",
                "status": "pending",
                "first_name": "John",
                "last_name": "Doe",
                "birth_date": "1990-05-15",
                "nationality": "TR",
                "passport_number": "U12345678",
                "passport_expiry": "2030-05-20",
                "phone": "+905551234567",
                "email": "john.doe@example.com",
                "target_country": "DE",
                "target_city": "Berlin",
                "visa_type": "tourist",
                "preferred_dates": {
                    "from": "2025-03-01",
                    "to": "2025-04-30",
                },
                "exclude_weekends": False,
                "created_at": "2025-02-01T12:00:00Z",
                "expires_at": "2025-02-02T12:00:00Z",
            }
        },
    )


class ApplicantListResponse(BaseModel):
    """Schema for paginated applicant list responses."""

    items: list[ApplicantResponse] = Field(
        description="List of applicants",
    )
    total: int = Field(
        ge=0,
        description="Total number of applicants matching the query",
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


class ApplicantSummary(BaseModel):
    """
    Minimal applicant summary without PII.

    Used in contexts where full PII is not needed,
    such as booking status displays.
    """

    id: UUID = Field(
        description="Unique applicant ID",
    )
    agency_id: UUID = Field(
        description="Associated agency ID",
    )
    external_ref: str | None = Field(
        default=None,
        description="Agency's internal reference",
    )
    status: ApplicantStatus = Field(
        description="Current processing status",
    )
    nationality: str = Field(
        description="Nationality (ISO 3166-1 alpha-2)",
    )
    target_country: str = Field(
        description="Target visa country",
    )
    visa_type: str = Field(
        description="Visa type code",
    )
    created_at: datetime = Field(
        description="When the applicant was created",
    )

    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
    )


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Enums
    "ApplicantStatus",
    # Nested Schemas
    "PreferredDateRange",
    # Request Schemas
    "ApplicantCreate",
    "ApplicantUpdate",
    "ApplicantBatchCreate",
    # Response Schemas
    "ApplicantResponse",
    "ApplicantListResponse",
    "ApplicantSummary",
]
