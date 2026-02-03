"""
VISE OS Agency Module.

Provides agency management functionality including:
- Agency CRUD operations
- Agency lifecycle management
- Credit balance management
- Credit transaction tracking
- Agency and credit service for business logic

Components:
- repository: AgencyRepository for data access
- service: AgencyService for agency management business logic
- credit: CreditService for credit management business logic

Usage:
    from src.core.agency import AgencyRepository, AgencyService, CreditService

    # Repository for data access
    repo = AgencyRepository()
    agency = await repo.get_by_id("uuid-here")

    # Agency service for business logic
    agency_service = AgencyService()
    agency = await agency_service.create_agency(
        name="Acme Travel",
        contact_name="John Doe",
        contact_email="john@acme.com",
        contact_phone="+905551234567",
    )

    # Credit service for credit operations
    credit_service = CreditService()
    balance = await credit_service.get_balance("uuid-here")
    can_book = await credit_service.can_afford_booking("uuid-here")

    # Reserve credits for booking
    result = await credit_service.reserve_for_booking("agency-uuid", "booking-uuid")
"""

from src.core.agency.credit import (
    CreditAlertLevel,
    CreditBalance,
    CreditError,
    CreditOperation,
    CreditRecordNotFoundError,
    CreditReservationError,
    CreditService,
    InvalidCreditAmountError,
    clear_credit_service_cache,
    get_credit_service,
)
from src.core.agency.repository import AgencyRepository
from src.core.agency.service import (
    AgencyError,
    AgencyEvent,
    AgencyEventType,
    AgencyNotFoundError,
    AgencyAlreadyExistsError,
    AgencyService,
    AgencyStats,
    AgencyWithCredits,
    InvalidAgencyStatusError,
    clear_agency_service_cache,
    get_agency_service,
)

__all__ = [
    # Repository
    "AgencyRepository",
    # Agency Service
    "AgencyService",
    "get_agency_service",
    "clear_agency_service_cache",
    # Credit Service
    "CreditService",
    "get_credit_service",
    "clear_credit_service_cache",
    # Agency Data classes
    "AgencyWithCredits",
    "AgencyStats",
    "AgencyEvent",
    "AgencyEventType",
    # Credit Data classes
    "CreditBalance",
    "CreditOperation",
    "CreditAlertLevel",
    # Agency Exceptions
    "AgencyError",
    "AgencyNotFoundError",
    "AgencyAlreadyExistsError",
    "InvalidAgencyStatusError",
    # Credit Exceptions
    "CreditError",
    "CreditRecordNotFoundError",
    "InvalidCreditAmountError",
    "CreditReservationError",
]
