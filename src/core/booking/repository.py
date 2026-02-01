"""
Booking Repository for data access.

This module provides a repository class for booking request and result CRUD operations.
It encapsulates all Directus API interactions for booking-related data.

Based on 002-DIRECTUS-SCHEMA.md collections:
- booking_requests: Main booking request records
- booking_results: Completed booking audit records (PII-free)

Usage:
    from src.core.booking.repository import BookingRepository

    repo = BookingRepository()

    # Create a booking
    booking = await repo.create({
        "agency_id": "...",
        "applicant_id": "...",
        "target_system": "vfs",
        "target_country": "DE",
    })

    # Query bookings
    pending = await repo.find_by_status("pending")
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import structlog

from src.api.schemas.booking import (
    BookingResponse,
    BookingResultResponse,
    BookingStatus,
)
from src.integrations.directus import (
    COLLECTIONS,
    DirectusClient,
    DirectusFilter,
    get_directus_client,
)

logger = structlog.get_logger()


class BookingRepository:
    """
    Repository for booking request and result data access.

    Provides CRUD operations and query methods for the booking_requests
    and booking_results Directus collections.

    Attributes:
        _client: DirectusClient instance for API calls.
        _logger: Structured logger for this repository.

    Example:
        repo = BookingRepository()

        # Create a booking request
        booking = await repo.create({
            "agency_id": "uuid-here",
            "applicant_id": "uuid-here",
            "target_system": "vfs",
            "target_country": "DE",
            "visa_category": "tourist",
        })

        # Get booking by ID
        booking = await repo.get_by_id(booking["id"])

        # Find pending bookings
        pending = await repo.find_by_status(BookingStatus.PENDING)
    """

    COLLECTION = COLLECTIONS.get("booking_requests", "booking_requests")
    RESULTS_COLLECTION = COLLECTIONS.get("booking_results", "booking_results")

    def __init__(self, client: DirectusClient | None = None) -> None:
        """
        Initialize the repository with a Directus client.

        Args:
            client: Optional DirectusClient instance. If not provided,
                uses the global cached client.
        """
        self._client = client or get_directus_client()
        self._logger = logger.bind(repository="booking")

    # -------------------------------------------------------------------------
    # Booking Request CRUD Operations
    # -------------------------------------------------------------------------

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a new booking request.

        Sets default values for required fields if not provided:
        - status: "pending"
        - priority: 5
        - attempts: 0
        - max_attempts: 50
        - slot_found_count: 0

        Args:
            data: Booking request data.

        Returns:
            Created booking request with generated fields.

        Example:
            booking = await repo.create({
                "agency_id": "uuid",
                "applicant_id": "uuid",
                "target_system": "vfs",
                "target_country": "DE",
                "visa_category": "tourist",
            })
        """
        # Set defaults
        data.setdefault("status", BookingStatus.PENDING.value)
        data.setdefault("priority", 5)
        data.setdefault("attempts", 0)
        data.setdefault("max_attempts", 50)
        data.setdefault("slot_found_count", 0)

        self._logger.info(
            "booking_create",
            agency_id=data.get("agency_id"),
            target_system=data.get("target_system"),
            target_country=data.get("target_country"),
        )

        result = await self._client.create_item(self.COLLECTION, data)
        return result

    async def get_by_id(
        self,
        booking_id: str | UUID,
        fields: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """
        Get a booking request by ID.

        Args:
            booking_id: The booking request UUID.
            fields: Optional list of fields to return.

        Returns:
            Booking request data or None if not found.
        """
        self._logger.debug("booking_get", booking_id=str(booking_id))
        return await self._client.get_item(self.COLLECTION, booking_id, fields=fields)

    async def update(
        self,
        booking_id: str | UUID,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Update a booking request.

        Args:
            booking_id: The booking request UUID.
            data: Fields to update.

        Returns:
            Updated booking request data.
        """
        self._logger.info(
            "booking_update",
            booking_id=str(booking_id),
            fields=list(data.keys()),
        )
        return await self._client.update_item(self.COLLECTION, booking_id, data)

    async def delete(self, booking_id: str | UUID) -> None:
        """
        Delete a booking request.

        Args:
            booking_id: The booking request UUID.
        """
        self._logger.info("booking_delete", booking_id=str(booking_id))
        await self._client.delete_item(self.COLLECTION, booking_id)

    # -------------------------------------------------------------------------
    # Status Updates
    # -------------------------------------------------------------------------

    async def update_status(
        self,
        booking_id: str | UUID,
        status: BookingStatus | str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """
        Update a booking's status with optional error details.

        Args:
            booking_id: The booking request UUID.
            status: New status value.
            error_code: Optional error code for failed status.
            error_message: Optional error message for failed status.

        Returns:
            Updated booking request data.
        """
        status_value = status.value if isinstance(status, BookingStatus) else status
        data: dict[str, Any] = {"status": status_value}

        if error_code is not None:
            data["error_code"] = error_code
        if error_message is not None:
            data["error_message"] = error_message

        # Set completed_at for terminal states
        if status_value in ("completed", "failed", "expired", "cancelled"):
            data["completed_at"] = datetime.utcnow().isoformat()

        self._logger.info(
            "booking_status_update",
            booking_id=str(booking_id),
            status=status_value,
        )

        return await self.update(booking_id, data)

    async def increment_attempts(
        self,
        booking_id: str | UUID,
        *,
        slot_found: bool = False,
    ) -> dict[str, Any]:
        """
        Increment attempt counter and update timing.

        Args:
            booking_id: The booking request UUID.
            slot_found: Whether a slot was found in this attempt.

        Returns:
            Updated booking request data.
        """
        # Get current values first
        current = await self.get_by_id(booking_id)
        if not current:
            raise ValueError(f"Booking {booking_id} not found")

        data: dict[str, Any] = {
            "attempts": current.get("attempts", 0) + 1,
            "last_attempt_at": datetime.utcnow().isoformat(),
        }

        if slot_found:
            data["slot_found_count"] = current.get("slot_found_count", 0) + 1

        return await self.update(booking_id, data)

    # -------------------------------------------------------------------------
    # Query Methods
    # -------------------------------------------------------------------------

    async def find_by_status(
        self,
        status: BookingStatus | str,
        *,
        agency_id: str | UUID | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Find booking requests by status.

        Args:
            status: The status to filter by.
            agency_id: Optional agency ID filter.
            limit: Maximum number of results.
            offset: Number of results to skip.

        Returns:
            List of booking requests matching the filter.
        """
        status_value = status.value if isinstance(status, BookingStatus) else status

        filter_builder = DirectusFilter().eq("status", status_value)
        if agency_id:
            filter_builder = filter_builder.raw(
                {"agency_id": {"_eq": str(agency_id)}}
            )

        self._logger.debug(
            "booking_find_by_status",
            status=status_value,
            agency_id=str(agency_id) if agency_id else None,
        )

        return await self._client.get_items(
            self.COLLECTION,
            filter=filter_builder,
            sort=["-created_at"],
            limit=limit,
            offset=offset,
        )

    async def find_by_agency(
        self,
        agency_id: str | UUID,
        *,
        status: BookingStatus | str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Find booking requests for an agency.

        Args:
            agency_id: The agency UUID.
            status: Optional status filter.
            limit: Maximum number of results.
            offset: Number of results to skip.

        Returns:
            List of booking requests for the agency.
        """
        filter_builder = DirectusFilter().eq("agency_id", str(agency_id))

        if status:
            status_value = status.value if isinstance(status, BookingStatus) else status
            filter_builder = filter_builder.raw({"status": {"_eq": status_value}})

        return await self._client.get_items(
            self.COLLECTION,
            filter=filter_builder,
            sort=["-created_at"],
            limit=limit,
            offset=offset,
        )

    async def find_queued_for_processing(
        self,
        *,
        target_system: str | None = None,
        target_country: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Find queued bookings ready for processing.

        Returns bookings in QUEUED status, ordered by priority and age.

        Args:
            target_system: Optional filter for specific system.
            target_country: Optional filter for specific country.
            limit: Maximum number of results.

        Returns:
            List of queued bookings ready to process.
        """
        filter_builder = DirectusFilter().eq("status", BookingStatus.QUEUED.value)

        if target_system:
            filter_builder = filter_builder.raw(
                {"target_system": {"_eq": target_system}}
            )
        if target_country:
            filter_builder = filter_builder.raw(
                {"target_country": {"_eq": target_country}}
            )

        return await self._client.get_items(
            self.COLLECTION,
            filter=filter_builder,
            sort=["priority", "created_at"],  # Lower priority first, then oldest
            limit=limit,
        )

    async def find_stale_processing(
        self,
        timeout_seconds: int = 300,
    ) -> list[dict[str, Any]]:
        """
        Find bookings stuck in PROCESSING state beyond timeout.

        Args:
            timeout_seconds: Processing timeout threshold.

        Returns:
            List of stale processing bookings.
        """
        cutoff = datetime.utcnow()
        cutoff_iso = cutoff.isoformat()

        # Find processing bookings with last_attempt_at older than cutoff
        filter_dict = {
            "_and": [
                {"status": {"_eq": BookingStatus.PROCESSING.value}},
                {"last_attempt_at": {"_lt": cutoff_iso}},
            ]
        }

        return await self._client.get_items(
            self.COLLECTION,
            filter=filter_dict,
        )

    async def count_by_status(
        self,
        status: BookingStatus | str,
        *,
        agency_id: str | UUID | None = None,
    ) -> int:
        """
        Count booking requests by status.

        Args:
            status: The status to count.
            agency_id: Optional agency ID filter.

        Returns:
            Number of bookings matching the filter.
        """
        status_value = status.value if isinstance(status, BookingStatus) else status
        filter_builder = DirectusFilter().eq("status", status_value)

        if agency_id:
            filter_builder = filter_builder.raw(
                {"agency_id": {"_eq": str(agency_id)}}
            )

        return await self._client.count_items(self.COLLECTION, filter=filter_builder)

    # -------------------------------------------------------------------------
    # Booking Results Operations
    # -------------------------------------------------------------------------

    async def create_result(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a booking result record.

        Booking results are created when a booking completes (success or failure).
        They provide a PII-free audit trail.

        Args:
            data: Booking result data.

        Returns:
            Created booking result.
        """
        self._logger.info(
            "booking_result_create",
            agency_id=data.get("agency_id"),
            booking_request_id=data.get("booking_request_id"),
            status=data.get("status"),
        )

        return await self._client.create_item(self.RESULTS_COLLECTION, data)

    async def get_result_by_booking_id(
        self,
        booking_request_id: str | UUID,
    ) -> dict[str, Any] | None:
        """
        Get the result for a booking request.

        Args:
            booking_request_id: The original booking request UUID.

        Returns:
            Booking result or None if not found.
        """
        results = await self._client.get_items(
            self.RESULTS_COLLECTION,
            filter={"booking_request_id": {"_eq": str(booking_request_id)}},
            limit=1,
        )
        return results[0] if results else None

    async def find_results_by_agency(
        self,
        agency_id: str | UUID,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Find booking results for an agency.

        Args:
            agency_id: The agency UUID.
            limit: Maximum number of results.
            offset: Number of results to skip.

        Returns:
            List of booking results.
        """
        return await self._client.get_items(
            self.RESULTS_COLLECTION,
            filter={"agency_id": {"_eq": str(agency_id)}},
            sort=["-created_at"],
            limit=limit,
            offset=offset,
        )

    # -------------------------------------------------------------------------
    # Aggregate Methods
    # -------------------------------------------------------------------------

    async def get_stats_by_agency(
        self,
        agency_id: str | UUID,
    ) -> dict[str, int]:
        """
        Get booking statistics for an agency.

        Returns counts by status.

        Args:
            agency_id: The agency UUID.

        Returns:
            Dictionary of status -> count.
        """
        results = await self._client.aggregate(
            self.COLLECTION,
            aggregate={"count": "*"},
            filter={"agency_id": {"_eq": str(agency_id)}},
            group_by=["status"],
        )

        stats = {}
        for row in results:
            status = row.get("status")
            count = int(row.get("count", 0))
            if status:
                stats[status] = count

        return stats


__all__ = [
    "BookingRepository",
]
