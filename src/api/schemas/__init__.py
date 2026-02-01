"""
Pydantic schemas for VISE OS API.

This module provides centralized access to all Pydantic schemas used
for API request/response validation and serialization.

Schema Categories:
- Booking: Booking requests, results, and status updates
- Agency: Agency management and credit system
- Applicant: Applicant data with PII handling

Usage:
    from src.api.schemas import (
        BookingRequest,
        BookingResponse,
        AgencyCreate,
        ApplicantCreate,
    )

    # Validate incoming booking request
    booking = BookingRequest.model_validate(request_data)

    # Create response from ORM object
    response = BookingResponse.model_validate(db_booking)
"""

# Booking schemas
from src.api.schemas.booking import (
    BookingBatchRequest,
    BookingListResponse,
    BookingRequest,
    BookingResponse,
    BookingResultResponse,
    BookingResultStatus,
    BookingStats,
    BookingStatus,
    BookingStatusUpdate,
    TargetSystem,
)

# Agency schemas
from src.api.schemas.agency import (
    AgencyCreate,
    AgencyCreditsResponse,
    AgencyListResponse,
    AgencyResponse,
    AgencyStatus,
    AgencyStatusUpdate,
    AgencyUpdate,
    CreditPurchaseRequest,
    CreditTransactionListResponse,
    CreditTransactionResponse,
    CreditTransactionType,
    NotificationPreferences,
)

# Applicant schemas
from src.api.schemas.applicant import (
    ApplicantBatchCreate,
    ApplicantCreate,
    ApplicantListResponse,
    ApplicantResponse,
    ApplicantStatus,
    ApplicantSummary,
    ApplicantUpdate,
    PreferredDateRange,
)

__all__ = [
    # Booking
    "BookingStatus",
    "TargetSystem",
    "BookingResultStatus",
    "BookingRequest",
    "BookingStatusUpdate",
    "BookingBatchRequest",
    "BookingResponse",
    "BookingResultResponse",
    "BookingListResponse",
    "BookingStats",
    # Agency
    "AgencyStatus",
    "CreditTransactionType",
    "NotificationPreferences",
    "AgencyCreate",
    "AgencyUpdate",
    "AgencyStatusUpdate",
    "CreditPurchaseRequest",
    "AgencyResponse",
    "AgencyListResponse",
    "AgencyCreditsResponse",
    "CreditTransactionResponse",
    "CreditTransactionListResponse",
    # Applicant
    "ApplicantStatus",
    "PreferredDateRange",
    "ApplicantCreate",
    "ApplicantUpdate",
    "ApplicantBatchCreate",
    "ApplicantResponse",
    "ApplicantListResponse",
    "ApplicantSummary",
]
