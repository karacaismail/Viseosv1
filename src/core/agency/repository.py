"""
Agency Repository for data access.

This module provides a repository class for agency and credit CRUD operations.
It encapsulates all Directus API interactions for agency-related data.

Based on 002-DIRECTUS-SCHEMA.md collections:
- agencies: Agency profiles and configuration
- agency_credits: Credit balances
- credit_transactions: Credit transaction audit trail

Usage:
    from src.core.agency.repository import AgencyRepository

    repo = AgencyRepository()

    # Get agency by ID
    agency = await repo.get_by_id("uuid-here")

    # Get credit balance
    credits = await repo.get_credits("uuid-here")

    # Reserve credits for booking
    await repo.reserve_credits("uuid-here", amount=1, booking_id="...")
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import structlog

from src.api.schemas.agency import (
    AgencyStatus,
    CreditTransactionType,
)
from src.integrations.directus import (
    COLLECTIONS,
    DirectusClient,
    DirectusFilter,
    get_directus_client,
)

logger = structlog.get_logger()


class AgencyRepository:
    """
    Repository for agency and credit data access.

    Provides CRUD operations and query methods for the agencies,
    agency_credits, and credit_transactions Directus collections.

    Attributes:
        _client: DirectusClient instance for API calls.
        _logger: Structured logger for this repository.

    Example:
        repo = AgencyRepository()

        # Get agency
        agency = await repo.get_by_id("uuid-here")

        # Get credits
        credits = await repo.get_credits("uuid-here")

        # Reserve credits
        await repo.reserve_credits("uuid-here", amount=1, booking_id="...")
    """

    COLLECTION = COLLECTIONS.get("agencies", "agencies")
    CREDITS_COLLECTION = COLLECTIONS.get("agency_credits", "agency_credits")
    TRANSACTIONS_COLLECTION = COLLECTIONS.get("credit_transactions", "credit_transactions")

    def __init__(self, client: DirectusClient | None = None) -> None:
        """
        Initialize the repository with a Directus client.

        Args:
            client: Optional DirectusClient instance. If not provided,
                uses the global cached client.
        """
        self._client = client or get_directus_client()
        self._logger = logger.bind(repository="agency")

    # -------------------------------------------------------------------------
    # Agency CRUD Operations
    # -------------------------------------------------------------------------

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a new agency.

        Sets default values:
        - status: "trial"
        - google_sheet_sync_enabled: False

        Args:
            data: Agency data.

        Returns:
            Created agency with generated fields.
        """
        # Set defaults
        data.setdefault("status", AgencyStatus.TRIAL.value)
        data.setdefault("google_sheet_sync_enabled", False)

        self._logger.info(
            "agency_create",
            name=data.get("name"),
            contact_email=data.get("contact_email"),
        )

        result = await self._client.create_item(self.COLLECTION, data)

        # Create initial credit balance record
        if result and result.get("id"):
            await self._create_initial_credits(result["id"])

        return result

    async def _create_initial_credits(self, agency_id: str) -> dict[str, Any]:
        """
        Create initial credit balance record for a new agency.

        Args:
            agency_id: The agency UUID.

        Returns:
            Created credit record.
        """
        return await self._client.create_item(
            self.CREDITS_COLLECTION,
            {
                "agency_id": agency_id,
                "total_credits": 0,
                "used_credits": 0,
                "reserved_credits": 0,
            },
        )

    async def get_by_id(
        self,
        agency_id: str | UUID,
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
        return await self._client.get_item(self.COLLECTION, agency_id, fields=fields)

    async def get_by_email(self, email: str) -> dict[str, Any] | None:
        """
        Get an agency by contact email.

        Args:
            email: The contact email address.

        Returns:
            Agency data or None if not found.
        """
        results = await self._client.get_items(
            self.COLLECTION,
            filter={"contact_email": {"_eq": email}},
            limit=1,
        )
        return results[0] if results else None

    async def update(
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
        """
        self._logger.info(
            "agency_update",
            agency_id=str(agency_id),
            fields=list(data.keys()),
        )
        return await self._client.update_item(self.COLLECTION, agency_id, data)

    async def update_status(
        self,
        agency_id: str | UUID,
        status: AgencyStatus | str,
    ) -> dict[str, Any]:
        """
        Update an agency's status.

        Args:
            agency_id: The agency UUID.
            status: New status value.

        Returns:
            Updated agency data.
        """
        status_value = status.value if isinstance(status, AgencyStatus) else status

        self._logger.info(
            "agency_status_update",
            agency_id=str(agency_id),
            status=status_value,
        )

        return await self.update(agency_id, {"status": status_value})

    async def delete(self, agency_id: str | UUID) -> None:
        """
        Delete an agency.

        Args:
            agency_id: The agency UUID.
        """
        self._logger.info("agency_delete", agency_id=str(agency_id))
        await self._client.delete_item(self.COLLECTION, agency_id)

    # -------------------------------------------------------------------------
    # Query Methods
    # -------------------------------------------------------------------------

    async def find_by_status(
        self,
        status: AgencyStatus | str,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Find agencies by status.

        Args:
            status: The status to filter by.
            limit: Maximum number of results.
            offset: Number of results to skip.

        Returns:
            List of agencies matching the filter.
        """
        status_value = status.value if isinstance(status, AgencyStatus) else status

        return await self._client.get_items(
            self.COLLECTION,
            filter={"status": {"_eq": status_value}},
            sort=["name"],
            limit=limit,
            offset=offset,
        )

    async def find_active(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Find all active agencies.

        Args:
            limit: Maximum number of results.
            offset: Number of results to skip.

        Returns:
            List of active agencies.
        """
        return await self.find_by_status(AgencyStatus.ACTIVE, limit=limit, offset=offset)

    async def find_with_google_sheets_sync(self) -> list[dict[str, Any]]:
        """
        Find agencies with Google Sheets sync enabled.

        Returns:
            List of agencies with sync enabled.
        """
        filter_dict = {
            "_and": [
                {"status": {"_eq": AgencyStatus.ACTIVE.value}},
                {"google_sheet_sync_enabled": {"_eq": True}},
                {"google_sheet_id": {"_nnull": True}},
            ]
        }

        return await self._client.get_items(
            self.COLLECTION,
            filter=filter_dict,
        )

    async def count(
        self,
        *,
        status: AgencyStatus | str | None = None,
    ) -> int:
        """
        Count agencies.

        Args:
            status: Optional status filter.

        Returns:
            Number of agencies matching the filter.
        """
        filter_builder = DirectusFilter()
        if status:
            status_value = status.value if isinstance(status, AgencyStatus) else status
            filter_builder = filter_builder.eq("status", status_value)

        return await self._client.count_items(
            self.COLLECTION,
            filter=filter_builder if status else None,
        )

    # -------------------------------------------------------------------------
    # Credit Operations
    # -------------------------------------------------------------------------

    async def get_credits(
        self,
        agency_id: str | UUID,
    ) -> dict[str, Any] | None:
        """
        Get credit balance for an agency.

        Args:
            agency_id: The agency UUID.

        Returns:
            Credit balance record or None if not found.
        """
        results = await self._client.get_items(
            self.CREDITS_COLLECTION,
            filter={"agency_id": {"_eq": str(agency_id)}},
            limit=1,
        )
        return results[0] if results else None

    async def get_available_credits(self, agency_id: str | UUID) -> int:
        """
        Get available credits for an agency.

        Available credits = total - used - reserved

        Args:
            agency_id: The agency UUID.

        Returns:
            Available credit amount.
        """
        credits = await self.get_credits(agency_id)
        if not credits:
            return 0

        total = credits.get("total_credits", 0)
        used = credits.get("used_credits", 0)
        reserved = credits.get("reserved_credits", 0)

        return max(0, total - used - reserved)

    async def add_credits(
        self,
        agency_id: str | UUID,
        amount: int,
        *,
        description: str | None = None,
    ) -> dict[str, Any]:
        """
        Add credits to an agency (purchase).

        Args:
            agency_id: The agency UUID.
            amount: Number of credits to add.
            description: Optional transaction description.

        Returns:
            Updated credit record.
        """
        if amount <= 0:
            raise ValueError("Amount must be positive")

        credits = await self.get_credits(agency_id)
        if not credits:
            raise ValueError(f"No credit record found for agency {agency_id}")

        new_total = credits.get("total_credits", 0) + amount
        credit_record_id = credits["id"]

        # Update credit balance
        updated = await self._client.update_item(
            self.CREDITS_COLLECTION,
            credit_record_id,
            {
                "total_credits": new_total,
                "last_purchase_at": datetime.utcnow().isoformat(),
            },
        )

        # Create transaction record
        await self._create_transaction(
            agency_id=str(agency_id),
            type=CreditTransactionType.PURCHASE,
            amount=amount,
            balance_after=new_total - credits.get("used_credits", 0) - credits.get("reserved_credits", 0),
            description=description or "Credit purchase",
        )

        self._logger.info(
            "credits_added",
            agency_id=str(agency_id),
            amount=amount,
            new_total=new_total,
        )

        return updated

    async def reserve_credits(
        self,
        agency_id: str | UUID,
        amount: int,
        *,
        booking_id: str | UUID | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """
        Reserve credits for a pending booking.

        Args:
            agency_id: The agency UUID.
            amount: Number of credits to reserve.
            booking_id: Related booking request ID.
            description: Optional transaction description.

        Returns:
            Updated credit record.

        Raises:
            ValueError: If insufficient credits available.
        """
        if amount <= 0:
            raise ValueError("Amount must be positive")

        credits = await self.get_credits(agency_id)
        if not credits:
            raise ValueError(f"No credit record found for agency {agency_id}")

        available = await self.get_available_credits(agency_id)
        if available < amount:
            raise ValueError(f"Insufficient credits: {available} available, {amount} required")

        new_reserved = credits.get("reserved_credits", 0) + amount
        credit_record_id = credits["id"]

        # Update credit balance
        updated = await self._client.update_item(
            self.CREDITS_COLLECTION,
            credit_record_id,
            {"reserved_credits": new_reserved},
        )

        # Create transaction record
        await self._create_transaction(
            agency_id=str(agency_id),
            type=CreditTransactionType.RESERVE,
            amount=-amount,
            balance_after=available - amount,
            reference_id=str(booking_id) if booking_id else None,
            description=description or "Credit reserved for booking",
        )

        self._logger.info(
            "credits_reserved",
            agency_id=str(agency_id),
            amount=amount,
            booking_id=str(booking_id) if booking_id else None,
        )

        return updated

    async def use_credits(
        self,
        agency_id: str | UUID,
        amount: int,
        *,
        booking_id: str | UUID | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """
        Convert reserved credits to used (booking completed).

        Args:
            agency_id: The agency UUID.
            amount: Number of credits to use.
            booking_id: Related booking request ID.
            description: Optional transaction description.

        Returns:
            Updated credit record.
        """
        if amount <= 0:
            raise ValueError("Amount must be positive")

        credits = await self.get_credits(agency_id)
        if not credits:
            raise ValueError(f"No credit record found for agency {agency_id}")

        reserved = credits.get("reserved_credits", 0)
        if reserved < amount:
            raise ValueError(f"Not enough reserved credits: {reserved} reserved, {amount} to use")

        new_reserved = reserved - amount
        new_used = credits.get("used_credits", 0) + amount
        credit_record_id = credits["id"]

        # Update credit balance
        updated = await self._client.update_item(
            self.CREDITS_COLLECTION,
            credit_record_id,
            {
                "reserved_credits": new_reserved,
                "used_credits": new_used,
                "last_usage_at": datetime.utcnow().isoformat(),
            },
        )

        # Create transaction record
        await self._create_transaction(
            agency_id=str(agency_id),
            type=CreditTransactionType.USAGE,
            amount=-amount,
            balance_after=credits.get("total_credits", 0) - new_used - new_reserved,
            reference_id=str(booking_id) if booking_id else None,
            description=description or "Credit used for completed booking",
        )

        self._logger.info(
            "credits_used",
            agency_id=str(agency_id),
            amount=amount,
            booking_id=str(booking_id) if booking_id else None,
        )

        return updated

    async def release_credits(
        self,
        agency_id: str | UUID,
        amount: int,
        *,
        booking_id: str | UUID | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """
        Release reserved credits back to available (booking failed/cancelled).

        Args:
            agency_id: The agency UUID.
            amount: Number of credits to release.
            booking_id: Related booking request ID.
            description: Optional transaction description.

        Returns:
            Updated credit record.
        """
        if amount <= 0:
            raise ValueError("Amount must be positive")

        credits = await self.get_credits(agency_id)
        if not credits:
            raise ValueError(f"No credit record found for agency {agency_id}")

        reserved = credits.get("reserved_credits", 0)
        release_amount = min(amount, reserved)  # Don't release more than reserved

        if release_amount == 0:
            return credits

        new_reserved = reserved - release_amount
        credit_record_id = credits["id"]

        # Update credit balance
        updated = await self._client.update_item(
            self.CREDITS_COLLECTION,
            credit_record_id,
            {"reserved_credits": new_reserved},
        )

        # Create transaction record
        await self._create_transaction(
            agency_id=str(agency_id),
            type=CreditTransactionType.RELEASE,
            amount=release_amount,
            balance_after=credits.get("total_credits", 0) - credits.get("used_credits", 0) - new_reserved,
            reference_id=str(booking_id) if booking_id else None,
            description=description or "Credit released from failed/cancelled booking",
        )

        self._logger.info(
            "credits_released",
            agency_id=str(agency_id),
            amount=release_amount,
            booking_id=str(booking_id) if booking_id else None,
        )

        return updated

    async def refund_credits(
        self,
        agency_id: str | UUID,
        amount: int,
        *,
        booking_id: str | UUID | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """
        Refund used credits (administrative refund).

        Args:
            agency_id: The agency UUID.
            amount: Number of credits to refund.
            booking_id: Related booking request ID.
            description: Optional transaction description.

        Returns:
            Updated credit record.
        """
        if amount <= 0:
            raise ValueError("Amount must be positive")

        credits = await self.get_credits(agency_id)
        if not credits:
            raise ValueError(f"No credit record found for agency {agency_id}")

        used = credits.get("used_credits", 0)
        refund_amount = min(amount, used)  # Don't refund more than used

        if refund_amount == 0:
            return credits

        new_used = used - refund_amount
        credit_record_id = credits["id"]

        # Update credit balance
        updated = await self._client.update_item(
            self.CREDITS_COLLECTION,
            credit_record_id,
            {"used_credits": new_used},
        )

        # Create transaction record
        await self._create_transaction(
            agency_id=str(agency_id),
            type=CreditTransactionType.REFUND,
            amount=refund_amount,
            balance_after=credits.get("total_credits", 0) - new_used - credits.get("reserved_credits", 0),
            reference_id=str(booking_id) if booking_id else None,
            description=description or "Credit refund",
        )

        self._logger.info(
            "credits_refunded",
            agency_id=str(agency_id),
            amount=refund_amount,
            booking_id=str(booking_id) if booking_id else None,
        )

        return updated

    # -------------------------------------------------------------------------
    # Transaction Operations
    # -------------------------------------------------------------------------

    async def _create_transaction(
        self,
        agency_id: str,
        type: CreditTransactionType,
        amount: int,
        balance_after: int,
        *,
        reference_id: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a credit transaction record.

        Args:
            agency_id: The agency UUID.
            type: Transaction type.
            amount: Transaction amount (positive or negative).
            balance_after: Balance after transaction.
            reference_id: Related booking request ID.
            description: Transaction description.

        Returns:
            Created transaction record.
        """
        type_value = type.value if isinstance(type, CreditTransactionType) else type

        data = {
            "agency_id": agency_id,
            "type": type_value,
            "amount": amount,
            "balance_after": balance_after,
        }

        if reference_id:
            data["reference_id"] = reference_id
        if description:
            data["description"] = description

        return await self._client.create_item(self.TRANSACTIONS_COLLECTION, data)

    async def get_transactions(
        self,
        agency_id: str | UUID,
        *,
        type: CreditTransactionType | str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get credit transactions for an agency.

        Args:
            agency_id: The agency UUID.
            type: Optional transaction type filter.
            limit: Maximum number of results.
            offset: Number of results to skip.

        Returns:
            List of transaction records.
        """
        filter_builder = DirectusFilter().eq("agency_id", str(agency_id))

        if type:
            type_value = type.value if isinstance(type, CreditTransactionType) else type
            filter_builder = filter_builder.raw({"type": {"_eq": type_value}})

        return await self._client.get_items(
            self.TRANSACTIONS_COLLECTION,
            filter=filter_builder,
            sort=["-created_at"],
            limit=limit,
            offset=offset,
        )


__all__ = [
    "AgencyRepository",
]
