"""
Credit Management Service for VISE OS.

This module provides a high-level service layer for managing agency credits.
It encapsulates business logic for credit operations including purchases,
reservations, usage tracking, and balance management.

Based on 002-DIRECTUS-SCHEMA.md:
- agency_credits: Credit balances
- credit_transactions: Audit trail

The credit lifecycle:
1. Purchase: Agency buys credits
2. Reserve: Credits reserved when booking starts
3. Use: Reserved credits consumed on booking success
4. Release: Reserved credits freed on booking failure/cancellation
5. Refund: Administrative return of used credits

Usage:
    from src.core.agency.credit import CreditService, get_credit_service

    # Using the service
    service = CreditService()

    # Check if agency can afford a booking
    can_book = await service.can_afford_booking("agency-uuid", credits_needed=1)

    # Reserve credits for a booking
    await service.reserve_for_booking("agency-uuid", "booking-uuid", credits=1)

    # Complete the booking (consume credits)
    await service.complete_booking("agency-uuid", "booking-uuid", credits=1)

    # Cancel booking (release credits)
    await service.cancel_booking("agency-uuid", "booking-uuid", credits=1)
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from functools import lru_cache
from typing import Any
from uuid import UUID

import structlog

from src.api.schemas.agency import (
    AgencyCreditsResponse,
    CreditTransactionType,
)
from src.core.agency.repository import AgencyRepository
from src.core.exceptions import InsufficientCreditsError, ViseOSError

logger = structlog.get_logger()


# =============================================================================
# Custom Exceptions
# =============================================================================


class CreditError(ViseOSError):
    """Base exception for credit-related errors."""

    def __init__(
        self,
        message: str = "Credit operation failed",
        *,
        agency_id: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.agency_id = agency_id
        super().__init__(message, code=code, details=details)


class CreditRecordNotFoundError(CreditError):
    """Raised when no credit record exists for an agency."""

    def __init__(
        self,
        agency_id: str,
        *,
        message: str = "Credit record not found for agency",
    ) -> None:
        super().__init__(
            message,
            agency_id=agency_id,
            code="CREDIT_RECORD_NOT_FOUND",
        )


class InvalidCreditAmountError(CreditError):
    """Raised when credit amount is invalid."""

    def __init__(
        self,
        message: str = "Invalid credit amount",
        *,
        amount: int | None = None,
        agency_id: str | None = None,
    ) -> None:
        details = {}
        if amount is not None:
            details["amount"] = amount
        super().__init__(
            message,
            agency_id=agency_id,
            code="INVALID_CREDIT_AMOUNT",
            details=details,
        )


class CreditReservationError(CreditError):
    """Raised when credit reservation fails."""

    def __init__(
        self,
        message: str = "Credit reservation failed",
        *,
        agency_id: str | None = None,
        booking_id: str | None = None,
        amount: int | None = None,
    ) -> None:
        details = {}
        if booking_id:
            details["booking_id"] = booking_id
        if amount is not None:
            details["amount"] = amount
        super().__init__(
            message,
            agency_id=agency_id,
            code="CREDIT_RESERVATION_FAILED",
            details=details,
        )


# =============================================================================
# Data Classes
# =============================================================================


class CreditAlertLevel(str, Enum):
    """Alert levels for credit balance notifications."""

    NORMAL = "normal"
    LOW = "low"
    CRITICAL = "critical"
    EMPTY = "empty"


@dataclass
class CreditBalance:
    """
    Represents an agency's credit balance state.

    Attributes:
        agency_id: The agency UUID.
        total: Total credits ever purchased.
        used: Credits consumed by completed bookings.
        reserved: Credits held for pending bookings.
        available: Credits available for new bookings.
        alert_level: Current alert level based on balance.
    """

    agency_id: str
    total: int
    used: int
    reserved: int
    available: int
    last_purchase_at: datetime | None = None
    last_usage_at: datetime | None = None

    @property
    def alert_level(self) -> CreditAlertLevel:
        """Determine alert level based on available credits."""
        if self.available <= 0:
            return CreditAlertLevel.EMPTY
        elif self.available <= 3:
            return CreditAlertLevel.CRITICAL
        elif self.available <= 10:
            return CreditAlertLevel.LOW
        return CreditAlertLevel.NORMAL

    @property
    def is_low(self) -> bool:
        """Check if credits are at or below low threshold."""
        return self.alert_level in (
            CreditAlertLevel.LOW,
            CreditAlertLevel.CRITICAL,
            CreditAlertLevel.EMPTY,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "agency_id": self.agency_id,
            "total_credits": self.total,
            "used_credits": self.used,
            "reserved_credits": self.reserved,
            "available_credits": self.available,
            "alert_level": self.alert_level.value,
            "last_purchase_at": self.last_purchase_at.isoformat() if self.last_purchase_at else None,
            "last_usage_at": self.last_usage_at.isoformat() if self.last_usage_at else None,
        }

    def to_response(self, credit_record_id: str) -> AgencyCreditsResponse:
        """Convert to Pydantic response model."""
        return AgencyCreditsResponse(
            id=UUID(credit_record_id),
            agency_id=UUID(self.agency_id),
            total_credits=self.total,
            used_credits=self.used,
            reserved_credits=self.reserved,
            available_credits=self.available,
            last_purchase_at=self.last_purchase_at,
            last_usage_at=self.last_usage_at,
        )


@dataclass
class CreditOperation:
    """
    Result of a credit operation.

    Attributes:
        success: Whether the operation succeeded.
        agency_id: The agency UUID.
        amount: Credit amount involved.
        balance_before: Balance before the operation.
        balance_after: Balance after the operation.
        transaction_id: ID of the created transaction.
        booking_id: Related booking ID if applicable.
        operation_type: Type of operation performed.
    """

    success: bool
    agency_id: str
    amount: int
    balance_before: int
    balance_after: int
    transaction_id: str | None = None
    booking_id: str | None = None
    operation_type: CreditTransactionType | None = None
    error_message: str | None = None


# =============================================================================
# Credit Service
# =============================================================================


class CreditService:
    """
    Service for managing agency credits.

    Provides high-level business logic for credit operations including
    balance checking, reservations, usage tracking, and refunds.

    The service wraps AgencyRepository methods with additional validation
    and business logic.

    Attributes:
        _repository: AgencyRepository for data access.
        _logger: Structured logger.

    Example:
        service = CreditService()

        # Check balance
        balance = await service.get_balance("agency-uuid")
        print(f"Available: {balance.available}")

        # Reserve for booking
        result = await service.reserve_for_booking(
            "agency-uuid",
            "booking-uuid",
            credits=1
        )

        if result.success:
            # Proceed with booking...
            pass
    """

    # Default thresholds for alerts
    LOW_CREDIT_THRESHOLD = 10
    CRITICAL_CREDIT_THRESHOLD = 3

    def __init__(self, repository: AgencyRepository | None = None) -> None:
        """
        Initialize the credit service.

        Args:
            repository: Optional AgencyRepository. If not provided,
                creates a new instance.
        """
        self._repository = repository or AgencyRepository()
        self._logger = logger.bind(service="credit")

    # -------------------------------------------------------------------------
    # Balance Operations
    # -------------------------------------------------------------------------

    async def get_balance(self, agency_id: str | UUID) -> CreditBalance:
        """
        Get the current credit balance for an agency.

        Args:
            agency_id: The agency UUID.

        Returns:
            CreditBalance with current state.

        Raises:
            CreditRecordNotFoundError: If no credit record exists.
        """
        agency_id_str = str(agency_id)
        self._logger.debug("credit_get_balance", agency_id=agency_id_str)

        credits = await self._repository.get_credits(agency_id)
        if not credits:
            raise CreditRecordNotFoundError(agency_id_str)

        total = credits.get("total_credits", 0)
        used = credits.get("used_credits", 0)
        reserved = credits.get("reserved_credits", 0)
        available = max(0, total - used - reserved)

        # Parse datetime fields
        last_purchase = None
        if credits.get("last_purchase_at"):
            try:
                last_purchase = datetime.fromisoformat(
                    str(credits["last_purchase_at"]).replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        last_usage = None
        if credits.get("last_usage_at"):
            try:
                last_usage = datetime.fromisoformat(
                    str(credits["last_usage_at"]).replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass

        return CreditBalance(
            agency_id=agency_id_str,
            total=total,
            used=used,
            reserved=reserved,
            available=available,
            last_purchase_at=last_purchase,
            last_usage_at=last_usage,
        )

    async def get_available_credits(self, agency_id: str | UUID) -> int:
        """
        Get the number of available credits for an agency.

        Available = Total - Used - Reserved

        Args:
            agency_id: The agency UUID.

        Returns:
            Number of available credits.
        """
        try:
            balance = await self.get_balance(agency_id)
            return balance.available
        except CreditRecordNotFoundError:
            return 0

    async def can_afford(self, agency_id: str | UUID, amount: int) -> bool:
        """
        Check if an agency can afford a specific credit amount.

        Args:
            agency_id: The agency UUID.
            amount: Number of credits needed.

        Returns:
            True if agency has enough available credits.
        """
        if amount <= 0:
            return True

        available = await self.get_available_credits(agency_id)
        return available >= amount

    async def can_afford_booking(
        self,
        agency_id: str | UUID,
        credits_needed: int = 1,
    ) -> bool:
        """
        Check if an agency can afford a booking.

        Args:
            agency_id: The agency UUID.
            credits_needed: Credits required for the booking.

        Returns:
            True if agency can afford the booking.
        """
        return await self.can_afford(agency_id, credits_needed)

    async def check_and_validate(
        self,
        agency_id: str | UUID,
        required_amount: int,
    ) -> CreditBalance:
        """
        Check balance and validate sufficient credits are available.

        Args:
            agency_id: The agency UUID.
            required_amount: Credits required.

        Returns:
            CreditBalance if sufficient credits available.

        Raises:
            InsufficientCreditsError: If not enough credits available.
        """
        balance = await self.get_balance(agency_id)

        if balance.available < required_amount:
            raise InsufficientCreditsError(
                message=f"Insufficient credits: {balance.available} available, {required_amount} required",
                agency_id=str(agency_id),
                required_credits=required_amount,
                available_credits=balance.available,
            )

        return balance

    # -------------------------------------------------------------------------
    # Credit Purchase Operations
    # -------------------------------------------------------------------------

    async def purchase_credits(
        self,
        agency_id: str | UUID,
        amount: int,
        *,
        description: str | None = None,
    ) -> CreditOperation:
        """
        Add purchased credits to an agency's balance.

        Args:
            agency_id: The agency UUID.
            amount: Number of credits to add.
            description: Optional purchase description.

        Returns:
            CreditOperation result.

        Raises:
            InvalidCreditAmountError: If amount is not positive.
            CreditRecordNotFoundError: If no credit record exists.
        """
        agency_id_str = str(agency_id)

        if amount <= 0:
            raise InvalidCreditAmountError(
                "Credit purchase amount must be positive",
                amount=amount,
                agency_id=agency_id_str,
            )

        self._logger.info(
            "credit_purchase_start",
            agency_id=agency_id_str,
            amount=amount,
        )

        # Get current balance
        balance_before = await self.get_available_credits(agency_id)

        try:
            # Add credits through repository
            await self._repository.add_credits(
                agency_id,
                amount,
                description=description or f"Credit purchase: {amount} credits",
            )

            # Get new balance
            balance_after = await self.get_available_credits(agency_id)

            self._logger.info(
                "credit_purchase_complete",
                agency_id=agency_id_str,
                amount=amount,
                balance_before=balance_before,
                balance_after=balance_after,
            )

            return CreditOperation(
                success=True,
                agency_id=agency_id_str,
                amount=amount,
                balance_before=balance_before,
                balance_after=balance_after,
                operation_type=CreditTransactionType.PURCHASE,
            )

        except ValueError as e:
            self._logger.error(
                "credit_purchase_failed",
                agency_id=agency_id_str,
                amount=amount,
                error=str(e),
            )
            return CreditOperation(
                success=False,
                agency_id=agency_id_str,
                amount=amount,
                balance_before=balance_before,
                balance_after=balance_before,
                operation_type=CreditTransactionType.PURCHASE,
                error_message=str(e),
            )

    # -------------------------------------------------------------------------
    # Booking Credit Operations
    # -------------------------------------------------------------------------

    async def reserve_for_booking(
        self,
        agency_id: str | UUID,
        booking_id: str | UUID,
        credits: int = 1,
    ) -> CreditOperation:
        """
        Reserve credits for a pending booking.

        This is called when a booking is initiated. Credits are moved
        from available to reserved until the booking completes or fails.

        Args:
            agency_id: The agency UUID.
            booking_id: The booking request UUID.
            credits: Number of credits to reserve.

        Returns:
            CreditOperation result.

        Raises:
            InsufficientCreditsError: If not enough credits available.
            InvalidCreditAmountError: If credits is not positive.
        """
        agency_id_str = str(agency_id)
        booking_id_str = str(booking_id)

        if credits <= 0:
            raise InvalidCreditAmountError(
                "Credit reservation amount must be positive",
                amount=credits,
                agency_id=agency_id_str,
            )

        self._logger.info(
            "credit_reserve_start",
            agency_id=agency_id_str,
            booking_id=booking_id_str,
            credits=credits,
        )

        # Check balance first
        balance = await self.check_and_validate(agency_id, credits)
        balance_before = balance.available

        try:
            await self._repository.reserve_credits(
                agency_id,
                credits,
                booking_id=booking_id,
                description=f"Reserved for booking {booking_id_str}",
            )

            balance_after = await self.get_available_credits(agency_id)

            self._logger.info(
                "credit_reserve_complete",
                agency_id=agency_id_str,
                booking_id=booking_id_str,
                credits=credits,
                balance_before=balance_before,
                balance_after=balance_after,
            )

            return CreditOperation(
                success=True,
                agency_id=agency_id_str,
                amount=credits,
                balance_before=balance_before,
                balance_after=balance_after,
                booking_id=booking_id_str,
                operation_type=CreditTransactionType.RESERVE,
            )

        except ValueError as e:
            self._logger.error(
                "credit_reserve_failed",
                agency_id=agency_id_str,
                booking_id=booking_id_str,
                credits=credits,
                error=str(e),
            )
            raise CreditReservationError(
                str(e),
                agency_id=agency_id_str,
                booking_id=booking_id_str,
                amount=credits,
            ) from e

    async def complete_booking(
        self,
        agency_id: str | UUID,
        booking_id: str | UUID,
        credits: int = 1,
    ) -> CreditOperation:
        """
        Mark reserved credits as used when booking completes successfully.

        Args:
            agency_id: The agency UUID.
            booking_id: The booking request UUID.
            credits: Number of credits to consume.

        Returns:
            CreditOperation result.
        """
        agency_id_str = str(agency_id)
        booking_id_str = str(booking_id)

        self._logger.info(
            "credit_complete_start",
            agency_id=agency_id_str,
            booking_id=booking_id_str,
            credits=credits,
        )

        balance_before = await self.get_available_credits(agency_id)

        try:
            await self._repository.use_credits(
                agency_id,
                credits,
                booking_id=booking_id,
                description=f"Booking {booking_id_str} completed",
            )

            balance_after = await self.get_available_credits(agency_id)

            self._logger.info(
                "credit_complete_success",
                agency_id=agency_id_str,
                booking_id=booking_id_str,
                credits=credits,
                balance_before=balance_before,
                balance_after=balance_after,
            )

            return CreditOperation(
                success=True,
                agency_id=agency_id_str,
                amount=credits,
                balance_before=balance_before,
                balance_after=balance_after,
                booking_id=booking_id_str,
                operation_type=CreditTransactionType.USAGE,
            )

        except ValueError as e:
            self._logger.error(
                "credit_complete_failed",
                agency_id=agency_id_str,
                booking_id=booking_id_str,
                credits=credits,
                error=str(e),
            )
            return CreditOperation(
                success=False,
                agency_id=agency_id_str,
                amount=credits,
                balance_before=balance_before,
                balance_after=balance_before,
                booking_id=booking_id_str,
                operation_type=CreditTransactionType.USAGE,
                error_message=str(e),
            )

    async def cancel_booking(
        self,
        agency_id: str | UUID,
        booking_id: str | UUID,
        credits: int = 1,
    ) -> CreditOperation:
        """
        Release reserved credits when a booking is cancelled or fails.

        Args:
            agency_id: The agency UUID.
            booking_id: The booking request UUID.
            credits: Number of credits to release.

        Returns:
            CreditOperation result.
        """
        agency_id_str = str(agency_id)
        booking_id_str = str(booking_id)

        self._logger.info(
            "credit_cancel_start",
            agency_id=agency_id_str,
            booking_id=booking_id_str,
            credits=credits,
        )

        balance_before = await self.get_available_credits(agency_id)

        try:
            await self._repository.release_credits(
                agency_id,
                credits,
                booking_id=booking_id,
                description=f"Booking {booking_id_str} cancelled/failed",
            )

            balance_after = await self.get_available_credits(agency_id)

            self._logger.info(
                "credit_cancel_success",
                agency_id=agency_id_str,
                booking_id=booking_id_str,
                credits=credits,
                balance_before=balance_before,
                balance_after=balance_after,
            )

            return CreditOperation(
                success=True,
                agency_id=agency_id_str,
                amount=credits,
                balance_before=balance_before,
                balance_after=balance_after,
                booking_id=booking_id_str,
                operation_type=CreditTransactionType.RELEASE,
            )

        except ValueError as e:
            self._logger.error(
                "credit_cancel_failed",
                agency_id=agency_id_str,
                booking_id=booking_id_str,
                credits=credits,
                error=str(e),
            )
            return CreditOperation(
                success=False,
                agency_id=agency_id_str,
                amount=credits,
                balance_before=balance_before,
                balance_after=balance_before,
                booking_id=booking_id_str,
                operation_type=CreditTransactionType.RELEASE,
                error_message=str(e),
            )

    # -------------------------------------------------------------------------
    # Refund Operations
    # -------------------------------------------------------------------------

    async def refund_credits(
        self,
        agency_id: str | UUID,
        amount: int,
        *,
        booking_id: str | UUID | None = None,
        reason: str | None = None,
    ) -> CreditOperation:
        """
        Refund previously used credits to an agency.

        This is an administrative action to return credits that were
        consumed by a booking.

        Args:
            agency_id: The agency UUID.
            amount: Number of credits to refund.
            booking_id: Related booking ID if applicable.
            reason: Reason for the refund.

        Returns:
            CreditOperation result.
        """
        agency_id_str = str(agency_id)
        booking_id_str = str(booking_id) if booking_id else None

        if amount <= 0:
            raise InvalidCreditAmountError(
                "Refund amount must be positive",
                amount=amount,
                agency_id=agency_id_str,
            )

        self._logger.info(
            "credit_refund_start",
            agency_id=agency_id_str,
            booking_id=booking_id_str,
            amount=amount,
            reason=reason,
        )

        balance_before = await self.get_available_credits(agency_id)

        try:
            description = reason or "Administrative refund"
            if booking_id_str:
                description = f"{description} for booking {booking_id_str}"

            await self._repository.refund_credits(
                agency_id,
                amount,
                booking_id=booking_id,
                description=description,
            )

            balance_after = await self.get_available_credits(agency_id)

            self._logger.info(
                "credit_refund_success",
                agency_id=agency_id_str,
                booking_id=booking_id_str,
                amount=amount,
                balance_before=balance_before,
                balance_after=balance_after,
            )

            return CreditOperation(
                success=True,
                agency_id=agency_id_str,
                amount=amount,
                balance_before=balance_before,
                balance_after=balance_after,
                booking_id=booking_id_str,
                operation_type=CreditTransactionType.REFUND,
            )

        except ValueError as e:
            self._logger.error(
                "credit_refund_failed",
                agency_id=agency_id_str,
                booking_id=booking_id_str,
                amount=amount,
                error=str(e),
            )
            return CreditOperation(
                success=False,
                agency_id=agency_id_str,
                amount=amount,
                balance_before=balance_before,
                balance_after=balance_before,
                booking_id=booking_id_str,
                operation_type=CreditTransactionType.REFUND,
                error_message=str(e),
            )

    # -------------------------------------------------------------------------
    # Transaction History
    # -------------------------------------------------------------------------

    async def get_transaction_history(
        self,
        agency_id: str | UUID,
        *,
        transaction_type: CreditTransactionType | None = None,
        limit: int | None = 50,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get credit transaction history for an agency.

        Args:
            agency_id: The agency UUID.
            transaction_type: Optional filter by transaction type.
            limit: Maximum number of transactions to return.
            offset: Number of transactions to skip.

        Returns:
            List of transaction records.
        """
        self._logger.debug(
            "credit_get_transactions",
            agency_id=str(agency_id),
            type=transaction_type.value if transaction_type else None,
            limit=limit,
        )

        return await self._repository.get_transactions(
            agency_id,
            type=transaction_type,
            limit=limit,
            offset=offset,
        )

    # -------------------------------------------------------------------------
    # Alert Operations
    # -------------------------------------------------------------------------

    async def check_low_balance_alert(
        self,
        agency_id: str | UUID,
        threshold: int | None = None,
    ) -> tuple[bool, CreditAlertLevel]:
        """
        Check if agency should receive a low balance alert.

        Args:
            agency_id: The agency UUID.
            threshold: Custom threshold (default: LOW_CREDIT_THRESHOLD).

        Returns:
            Tuple of (should_alert, alert_level).
        """
        threshold = threshold or self.LOW_CREDIT_THRESHOLD

        try:
            balance = await self.get_balance(agency_id)
            should_alert = balance.available <= threshold
            return should_alert, balance.alert_level
        except CreditRecordNotFoundError:
            return False, CreditAlertLevel.NORMAL

    async def get_agencies_with_low_credits(
        self,
        threshold: int | None = None,
    ) -> list[CreditBalance]:
        """
        Get all agencies with low credit balances.

        Args:
            threshold: Credit threshold for low balance.

        Returns:
            List of CreditBalance for agencies below threshold.
        """
        # This would require a more complex query through Directus
        # For now, we'll note this as a future enhancement
        self._logger.warning(
            "get_agencies_with_low_credits is not yet fully implemented"
        )
        return []


# =============================================================================
# Factory Functions
# =============================================================================


@lru_cache
def get_credit_service() -> CreditService:
    """
    Get a cached CreditService instance.

    The service is cached for reuse across requests.
    Use this for dependency injection in FastAPI.

    Returns:
        Configured CreditService instance.

    Example:
        from fastapi import Depends

        @router.post("/bookings")
        async def create_booking(
            credit_service: CreditService = Depends(get_credit_service),
        ):
            can_afford = await credit_service.can_afford_booking(agency_id)
            ...
    """
    return CreditService()


def clear_credit_service_cache() -> None:
    """
    Clear the cached CreditService.

    Useful for testing or when dependencies change.
    """
    get_credit_service.cache_clear()


# =============================================================================
# Exports
# =============================================================================

__all__ = [
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
