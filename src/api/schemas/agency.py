"""
Pydantic schemas for agency management.

This module provides data validation and serialization schemas for agencies
and their credit system in VISE OS. These schemas correspond to the agencies,
agency_credits, and credit_transactions collections in Directus as defined
in 002-DIRECTUS-SCHEMA.md.

Schemas:
- AgencyCreate/AgencyUpdate/AgencyResponse: Agency management
- AgencyCreditsResponse: Credit balance information
- CreditTransactionResponse: Credit transaction audit

Usage:
    from src.api.schemas.agency import AgencyCreate, AgencyResponse

    # Create a new agency
    agency = AgencyCreate(
        name="Travel Agency Inc.",
        contact_name="John Doe",
        contact_email="john@example.com",
        contact_phone="+905551234567",
    )
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# =============================================================================
# Enums
# =============================================================================


class AgencyStatus(str, Enum):
    """Agency account status values."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    TRIAL = "trial"


class CreditTransactionType(str, Enum):
    """
    Types of credit transactions.

    - purchase: Credits bought by agency
    - usage: Credits consumed for bookings
    - refund: Credits returned after failed booking
    - reserve: Credits temporarily held for pending booking
    - release: Reserved credits released back to available
    """

    PURCHASE = "purchase"
    USAGE = "usage"
    REFUND = "refund"
    RESERVE = "reserve"
    RELEASE = "release"


# =============================================================================
# Nested Schemas
# =============================================================================


class NotificationPreferences(BaseModel):
    """
    Schema for agency notification preferences.

    Configures how and when the agency receives notifications.
    """

    email_enabled: bool = Field(
        default=True,
        description="Enable email notifications",
    )
    telegram_enabled: bool = Field(
        default=False,
        description="Enable Telegram notifications",
    )
    discord_enabled: bool = Field(
        default=False,
        description="Enable Discord notifications",
    )
    notify_on_success: bool = Field(
        default=True,
        description="Notify when booking succeeds",
    )
    notify_on_failure: bool = Field(
        default=True,
        description="Notify when booking fails",
    )
    notify_low_credits: bool = Field(
        default=True,
        description="Notify when credits are low",
    )
    low_credit_threshold: int = Field(
        default=10,
        ge=1,
        description="Threshold for low credit warning",
    )

    model_config = ConfigDict(
        str_strip_whitespace=True,
    )


# =============================================================================
# Request Schemas
# =============================================================================


class AgencyCreate(BaseModel):
    """
    Schema for creating a new agency.

    Used when registering a new agency in the system.
    """

    name: str = Field(
        min_length=2,
        max_length=200,
        description="Agency company name",
    )
    tursab_no: str | None = Field(
        default=None,
        max_length=50,
        description="TÜRSAB registration number",
    )
    contact_name: str = Field(
        min_length=2,
        max_length=100,
        description="Primary contact person name",
    )
    contact_email: EmailStr = Field(
        description="Primary contact email (used for login)",
    )
    contact_phone: str = Field(
        min_length=10,
        max_length=20,
        description="Primary contact phone number",
    )
    telegram_chat_id: str | None = Field(
        default=None,
        max_length=50,
        description="Telegram chat ID for notifications",
    )
    discord_webhook: str | None = Field(
        default=None,
        max_length=500,
        description="Discord webhook URL for notifications",
    )
    google_sheet_id: str | None = Field(
        default=None,
        max_length=100,
        description="Connected Google Sheet ID",
    )
    google_sheet_sync_enabled: bool = Field(
        default=False,
        description="Enable Google Sheets synchronization",
    )
    default_countries: list[str] | None = Field(
        default=None,
        max_length=20,
        description="Preferred target countries (ISO 3166-1 alpha-2)",
    )
    notification_preferences: NotificationPreferences | None = Field(
        default=None,
        description="Notification settings",
    )

    @field_validator("contact_phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        """Validate phone number format."""
        # Remove spaces and dashes for standardization
        cleaned = v.replace(" ", "").replace("-", "")
        if not cleaned.startswith("+"):
            cleaned = "+" + cleaned
        return cleaned

    @field_validator("default_countries")
    @classmethod
    def validate_countries(cls, v: list[str] | None) -> list[str] | None:
        """Ensure country codes are uppercase."""
        if v is None:
            return None
        return [code.upper() for code in v]

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "name": "Acme Travel Agency",
                "tursab_no": "12345",
                "contact_name": "John Doe",
                "contact_email": "john@acmetravel.com",
                "contact_phone": "+905551234567",
                "google_sheet_sync_enabled": False,
                "default_countries": ["DE", "IT", "FR"],
            }
        },
    )


class AgencyUpdate(BaseModel):
    """
    Schema for updating an existing agency.

    All fields are optional - only provided fields will be updated.
    """

    name: str | None = Field(
        default=None,
        min_length=2,
        max_length=200,
        description="Agency company name",
    )
    tursab_no: str | None = Field(
        default=None,
        max_length=50,
        description="TÜRSAB registration number",
    )
    contact_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        description="Primary contact person name",
    )
    contact_phone: str | None = Field(
        default=None,
        min_length=10,
        max_length=20,
        description="Primary contact phone number",
    )
    telegram_chat_id: str | None = Field(
        default=None,
        max_length=50,
        description="Telegram chat ID for notifications",
    )
    discord_webhook: str | None = Field(
        default=None,
        max_length=500,
        description="Discord webhook URL for notifications",
    )
    google_sheet_id: str | None = Field(
        default=None,
        max_length=100,
        description="Connected Google Sheet ID",
    )
    google_sheet_sync_enabled: bool | None = Field(
        default=None,
        description="Enable Google Sheets synchronization",
    )
    default_countries: list[str] | None = Field(
        default=None,
        max_length=20,
        description="Preferred target countries (ISO 3166-1 alpha-2)",
    )
    notification_preferences: NotificationPreferences | None = Field(
        default=None,
        description="Notification settings",
    )

    @field_validator("contact_phone")
    @classmethod
    def validate_phone(cls, v: str | None) -> str | None:
        """Validate phone number format."""
        if v is None:
            return None
        cleaned = v.replace(" ", "").replace("-", "")
        if not cleaned.startswith("+"):
            cleaned = "+" + cleaned
        return cleaned

    @field_validator("default_countries")
    @classmethod
    def validate_countries(cls, v: list[str] | None) -> list[str] | None:
        """Ensure country codes are uppercase."""
        if v is None:
            return None
        return [code.upper() for code in v]

    model_config = ConfigDict(
        str_strip_whitespace=True,
    )


class AgencyStatusUpdate(BaseModel):
    """Schema for updating agency status."""

    status: AgencyStatus = Field(
        description="New agency status",
    )

    model_config = ConfigDict(
        str_strip_whitespace=True,
    )


# =============================================================================
# Response Schemas
# =============================================================================


class AgencyResponse(BaseModel):
    """
    Schema for agency responses.

    Returns full agency data including server-generated fields.
    """

    id: UUID = Field(
        description="Unique agency ID",
    )
    status: AgencyStatus = Field(
        description="Current agency status",
    )
    name: str = Field(
        description="Agency company name",
    )
    tursab_no: str | None = Field(
        default=None,
        description="TÜRSAB registration number",
    )
    contact_name: str = Field(
        description="Primary contact person name",
    )
    contact_email: str = Field(
        description="Primary contact email (used for login)",
    )
    contact_phone: str = Field(
        description="Primary contact phone number",
    )
    telegram_chat_id: str | None = Field(
        default=None,
        description="Telegram chat ID for notifications",
    )
    discord_webhook: str | None = Field(
        default=None,
        description="Discord webhook URL for notifications",
    )
    google_sheet_id: str | None = Field(
        default=None,
        description="Connected Google Sheet ID",
    )
    google_sheet_sync_enabled: bool = Field(
        description="Whether Google Sheets sync is enabled",
    )
    default_countries: list[str] | None = Field(
        default=None,
        description="Preferred target countries",
    )
    notification_preferences: dict[str, Any] | None = Field(
        default=None,
        description="Notification settings",
    )
    created_at: datetime = Field(
        description="When the agency was registered",
    )
    updated_at: datetime = Field(
        description="When the agency was last updated",
    )

    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "active",
                "name": "Acme Travel Agency",
                "tursab_no": "12345",
                "contact_name": "John Doe",
                "contact_email": "john@acmetravel.com",
                "contact_phone": "+905551234567",
                "google_sheet_sync_enabled": False,
                "default_countries": ["DE", "IT", "FR"],
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2025-02-01T12:00:00Z",
            }
        },
    )


class AgencyListResponse(BaseModel):
    """Schema for paginated agency list responses."""

    items: list[AgencyResponse] = Field(
        description="List of agencies",
    )
    total: int = Field(
        ge=0,
        description="Total number of agencies matching the query",
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
# Credit Schemas
# =============================================================================


class AgencyCreditsResponse(BaseModel):
    """
    Schema for agency credit balance response.

    Represents the current credit state for an agency.
    """

    id: UUID = Field(
        description="Credit record ID",
    )
    agency_id: UUID = Field(
        description="Associated agency ID",
    )
    total_credits: int = Field(
        ge=0,
        description="Total credits ever purchased",
    )
    used_credits: int = Field(
        ge=0,
        description="Credits consumed by bookings",
    )
    reserved_credits: int = Field(
        ge=0,
        description="Credits reserved for pending bookings",
    )
    available_credits: int = Field(
        ge=0,
        description="Credits available for new bookings (total - used - reserved)",
    )
    last_purchase_at: datetime | None = Field(
        default=None,
        description="Last credit purchase timestamp",
    )
    last_usage_at: datetime | None = Field(
        default=None,
        description="Last credit usage timestamp",
    )

    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440010",
                "agency_id": "550e8400-e29b-41d4-a716-446655440000",
                "total_credits": 100,
                "used_credits": 45,
                "reserved_credits": 5,
                "available_credits": 50,
                "last_purchase_at": "2025-01-15T10:00:00Z",
                "last_usage_at": "2025-02-01T14:30:00Z",
            }
        },
    )


class CreditPurchaseRequest(BaseModel):
    """Schema for purchasing credits."""

    amount: int = Field(
        ge=1,
        le=10000,
        description="Number of credits to purchase",
    )
    description: str | None = Field(
        default=None,
        max_length=200,
        description="Optional description for the purchase",
    )

    model_config = ConfigDict(
        str_strip_whitespace=True,
    )


class CreditTransactionResponse(BaseModel):
    """
    Schema for credit transaction responses.

    Represents a single credit transaction in the audit trail.
    """

    id: UUID = Field(
        description="Transaction ID",
    )
    agency_id: UUID = Field(
        description="Associated agency ID",
    )
    type: CreditTransactionType = Field(
        description="Transaction type",
    )
    amount: int = Field(
        description="Transaction amount (positive or negative)",
    )
    balance_after: int = Field(
        ge=0,
        description="Balance after this transaction",
    )
    reference_id: UUID | None = Field(
        default=None,
        description="Related booking request ID (for usage/refund)",
    )
    description: str | None = Field(
        default=None,
        description="Transaction description",
    )
    created_at: datetime = Field(
        description="When the transaction occurred",
    )

    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440020",
                "agency_id": "550e8400-e29b-41d4-a716-446655440000",
                "type": "usage",
                "amount": -1,
                "balance_after": 49,
                "reference_id": "550e8400-e29b-41d4-a716-446655440002",
                "description": "Booking completed for DE visa",
                "created_at": "2025-02-01T14:30:00Z",
            }
        },
    )


class CreditTransactionListResponse(BaseModel):
    """Schema for paginated credit transaction list."""

    items: list[CreditTransactionResponse] = Field(
        description="List of transactions",
    )
    total: int = Field(
        ge=0,
        description="Total number of transactions",
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
# Exports
# =============================================================================

__all__ = [
    # Enums
    "AgencyStatus",
    "CreditTransactionType",
    # Nested Schemas
    "NotificationPreferences",
    # Request Schemas
    "AgencyCreate",
    "AgencyUpdate",
    "AgencyStatusUpdate",
    "CreditPurchaseRequest",
    # Response Schemas
    "AgencyResponse",
    "AgencyListResponse",
    "AgencyCreditsResponse",
    "CreditTransactionResponse",
    "CreditTransactionListResponse",
]
