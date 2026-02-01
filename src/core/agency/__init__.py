"""
VISE OS Agency Module.

Provides agency management functionality including:
- Agency CRUD operations
- Credit balance management
- Credit transaction tracking
- Credit service for business logic

Components:
- repository: AgencyRepository for data access
- credit: CreditService for credit management business logic

Usage:
    from src.core.agency import AgencyRepository, CreditService

    # Repository for data access
    repo = AgencyRepository()
    agency = await repo.get_by_id("uuid-here")

    # Service for business logic
    service = CreditService()
    balance = await service.get_balance("uuid-here")
    can_book = await service.can_afford_booking("uuid-here")

    # Reserve credits for booking
    result = await service.reserve_for_booking("agency-uuid", "booking-uuid")
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

__all__ = [
    # Repository
    "AgencyRepository",
    # Service
    "CreditService",
    "get_credit_service",
    "clear_credit_service_cache",
    # Data classes
    "CreditBalance",
    "CreditOperation",
    "CreditAlertLevel",
    # Exceptions
    "CreditError",
    "CreditRecordNotFoundError",
    "InvalidCreditAmountError",
    "CreditReservationError",
]
