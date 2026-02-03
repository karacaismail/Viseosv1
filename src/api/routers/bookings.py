"""
Bookings router for VISE OS API.

This module provides CRUD endpoints for managing booking requests in VISE OS.
It handles booking creation, retrieval, updates, status transitions, and
statistics for agency clients.

Endpoints:
    POST /bookings - Create a new booking request
    GET /bookings - List bookings for the authenticated agency
    GET /bookings/{booking_id} - Get a specific booking
    PATCH /bookings/{booking_id} - Update a booking
    DELETE /bookings/{booking_id} - Delete/cancel a booking
    PUT /bookings/{booking_id}/status - Update booking status
    GET /bookings/{booking_id}/result - Get booking result
    POST /bookings/batch - Create multiple bookings
    GET /bookings/stats - Get booking statistics for the agency

Usage:
    from src.api.routers.bookings import router
    app.include_router(router, prefix="/api/bookings", tags=["bookings"])
"""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from src.api.dependencies import (
    AgencyId,
    DirectusClientDep,
    Pagination,
    PaginationParams,
    RequestId,
    VerifiedAgency,
    get_agency_id,
    get_directus,
    get_pagination,
    get_request_id,
    verify_api_key,
)
from src.api.schemas.booking import (
    BookingBatchRequest,
    BookingListResponse,
    BookingRequest,
    BookingResponse,
    BookingResultResponse,
    BookingStats,
    BookingStatus,
    BookingStatusUpdate,
    TargetSystem,
)
from src.core.booking.repository import BookingRepository
from src.core.booking.state_machine import BookingStateMachine

logger = structlog.get_logger()

# =============================================================================
# Router
# =============================================================================

router = APIRouter()


# =============================================================================
# Dependencies
# =============================================================================


async def get_booking_repository(
    directus: DirectusClientDep,
) -> BookingRepository:
    """
    Get a BookingRepository instance.

    Args:
        directus: Directus client from dependency.

    Returns:
        BookingRepository: Repository instance.
    """
    return BookingRepository(client=directus)


# Type alias for repository dependency
BookingRepoDep = BookingRepository


# =============================================================================
# Helper Functions
# =============================================================================


async def get_booking_or_404(
    booking_id: UUID,
    agency_id: str,
    repo: BookingRepository,
) -> dict[str, Any]:
    """
    Get a booking by ID or raise 404.

    Ensures the booking belongs to the authenticated agency.

    Args:
        booking_id: The booking UUID.
        agency_id: The authenticated agency ID.
        repo: BookingRepository instance.

    Returns:
        Booking data if found and authorized.

    Raises:
        HTTPException: 404 if not found, 403 if unauthorized.
    """
    booking = await repo.get_by_id(booking_id)

    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Booking {booking_id} not found",
        )

    # Verify agency ownership
    if str(booking.get("agency_id")) != str(agency_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this booking",
        )

    return booking


def _format_booking_response(booking: dict[str, Any]) -> dict[str, Any]:
    """
    Format a booking dictionary for API response.

    Ensures proper field formatting and handles None values.

    Args:
        booking: Raw booking data from repository.

    Returns:
        Formatted booking data.
    """
    return {
        "id": booking.get("id"),
        "agency_id": booking.get("agency_id"),
        "applicant_id": booking.get("applicant_id"),
        "status": booking.get("status"),
        "priority": booking.get("priority", 5),
        "target_system": booking.get("target_system"),
        "target_country": booking.get("target_country"),
        "target_location": booking.get("target_location"),
        "visa_category": booking.get("visa_category"),
        "max_attempts": booking.get("max_attempts", 50),
        "attempts": booking.get("attempts", 0),
        "slot_found_count": booking.get("slot_found_count", 0),
        "last_attempt_at": booking.get("last_attempt_at"),
        "next_attempt_at": booking.get("next_attempt_at"),
        "error_code": booking.get("error_code"),
        "error_message": booking.get("error_message"),
        "assigned_account_id": booking.get("assigned_account_id"),
        "assigned_proxy_id": booking.get("assigned_proxy_id"),
        "metadata": booking.get("metadata"),
        "created_at": booking.get("date_created") or booking.get("created_at"),
        "updated_at": booking.get("date_updated") or booking.get("updated_at"),
        "completed_at": booking.get("completed_at"),
    }


# =============================================================================
# Endpoints
# =============================================================================


@router.post(
    "",
    response_model=BookingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a booking request",
    description="Create a new visa appointment booking request for the authenticated agency.",
    responses={
        201: {"description": "Booking created successfully"},
        400: {"description": "Invalid request data"},
        401: {"description": "Authentication required"},
        403: {"description": "Insufficient credits or agency inactive"},
    },
)
async def create_booking(
    booking: BookingRequest,
    agency: VerifiedAgency,
    directus: DirectusClientDep,
    request_id: RequestId,
) -> BookingResponse:
    """
    Create a new booking request.

    Creates a booking request for the authenticated agency. The booking
    starts in PENDING status and will be queued for processing.

    Args:
        booking: Booking request data.
        agency: Verified agency information.
        directus: Directus client.
        request_id: Request ID for tracing.

    Returns:
        BookingResponse: The created booking.
    """
    repo = BookingRepository(client=directus)
    agency_id = str(agency["id"])

    # Verify agency_id matches authenticated agency
    if str(booking.agency_id) != agency_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create bookings for other agencies",
        )

    logger.info(
        "booking_create_request",
        request_id=request_id,
        agency_id=agency_id,
        target_system=booking.target_system.value,
        target_country=booking.target_country,
    )

    # Create booking in repository
    booking_data = booking.model_dump(mode="json")
    booking_data["agency_id"] = str(booking_data["agency_id"])
    booking_data["applicant_id"] = str(booking_data["applicant_id"])
    booking_data["target_system"] = booking.target_system.value

    created = await repo.create(booking_data)

    logger.info(
        "booking_created",
        request_id=request_id,
        booking_id=created["id"],
        agency_id=agency_id,
    )

    return BookingResponse(**_format_booking_response(created))


@router.get(
    "",
    response_model=BookingListResponse,
    summary="List booking requests",
    description="List booking requests for the authenticated agency with pagination and filtering.",
    responses={
        200: {"description": "List of bookings"},
        401: {"description": "Authentication required"},
    },
)
async def list_bookings(
    agency: VerifiedAgency,
    directus: DirectusClientDep,
    pagination: Pagination,
    status_filter: BookingStatus | None = Query(
        None,
        alias="status",
        description="Filter by booking status",
    ),
    target_system: TargetSystem | None = Query(
        None,
        description="Filter by target system",
    ),
    target_country: str | None = Query(
        None,
        min_length=2,
        max_length=2,
        description="Filter by target country (ISO 3166-1 alpha-2)",
    ),
) -> BookingListResponse:
    """
    List booking requests for the authenticated agency.

    Supports pagination and filtering by status, target system, and country.

    Args:
        agency: Verified agency information.
        directus: Directus client.
        pagination: Pagination parameters.
        status_filter: Optional status filter.
        target_system: Optional target system filter.
        target_country: Optional country filter.

    Returns:
        BookingListResponse: Paginated list of bookings.
    """
    repo = BookingRepository(client=directus)
    agency_id = str(agency["id"])

    # Get bookings with filters
    bookings = await repo.find_by_agency(
        agency_id=agency_id,
        status=status_filter,
        limit=pagination.page_size + 1,  # Get one extra to check for more
        offset=pagination.offset,
    )

    # Check if there are more results
    has_more = len(bookings) > pagination.page_size
    if has_more:
        bookings = bookings[: pagination.page_size]

    # Get total count
    stats = await repo.get_stats_by_agency(agency_id)
    total = sum(stats.values()) if stats else 0

    # Format response
    items = [BookingResponse(**_format_booking_response(b)) for b in bookings]

    return BookingListResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        has_more=has_more,
    )


@router.get(
    "/stats",
    response_model=BookingStats,
    summary="Get booking statistics",
    description="Get aggregated booking statistics for the authenticated agency.",
    responses={
        200: {"description": "Booking statistics"},
        401: {"description": "Authentication required"},
    },
)
async def get_booking_stats(
    agency: VerifiedAgency,
    directus: DirectusClientDep,
) -> BookingStats:
    """
    Get booking statistics for the authenticated agency.

    Returns counts by status and aggregated metrics.

    Args:
        agency: Verified agency information.
        directus: Directus client.

    Returns:
        BookingStats: Aggregated booking statistics.
    """
    repo = BookingRepository(client=directus)
    agency_id = str(agency["id"])

    # Get status counts
    stats = await repo.get_stats_by_agency(agency_id)

    # Calculate metrics
    completed = stats.get("completed", 0)
    failed = stats.get("failed", 0) + stats.get("expired", 0)
    pending = (
        stats.get("pending", 0)
        + stats.get("queued", 0)
        + stats.get("processing", 0)
        + stats.get("slot_found", 0)
        + stats.get("booking", 0)
        + stats.get("payment", 0)
        + stats.get("verifying", 0)
    )
    total = sum(stats.values()) if stats else 0

    success_rate = completed / (completed + failed) if (completed + failed) > 0 else 0.0

    # TODO: Calculate avg_duration and credits_used from booking_results
    # For now, return placeholder values
    return BookingStats(
        total_requests=total,
        completed=completed,
        failed=failed,
        pending=pending,
        success_rate=round(success_rate, 3),
        avg_duration_seconds=0.0,
        credits_used=completed,  # Placeholder: 1 credit per booking
        by_country={},  # TODO: Aggregate by country
    )


@router.get(
    "/{booking_id}",
    response_model=BookingResponse,
    summary="Get a booking request",
    description="Get details of a specific booking request.",
    responses={
        200: {"description": "Booking details"},
        401: {"description": "Authentication required"},
        403: {"description": "Not authorized to access this booking"},
        404: {"description": "Booking not found"},
    },
)
async def get_booking(
    booking_id: UUID,
    agency: VerifiedAgency,
    directus: DirectusClientDep,
) -> BookingResponse:
    """
    Get a specific booking request by ID.

    Args:
        booking_id: The booking UUID.
        agency: Verified agency information.
        directus: Directus client.

    Returns:
        BookingResponse: The booking details.
    """
    repo = BookingRepository(client=directus)
    agency_id = str(agency["id"])

    booking = await get_booking_or_404(booking_id, agency_id, repo)

    return BookingResponse(**_format_booking_response(booking))


@router.patch(
    "/{booking_id}",
    response_model=BookingResponse,
    summary="Update a booking request",
    description="Update a booking request. Only certain fields can be updated.",
    responses={
        200: {"description": "Booking updated"},
        400: {"description": "Invalid update data or booking cannot be modified"},
        401: {"description": "Authentication required"},
        403: {"description": "Not authorized to access this booking"},
        404: {"description": "Booking not found"},
    },
)
async def update_booking(
    booking_id: UUID,
    update_data: dict[str, Any],
    agency: VerifiedAgency,
    directus: DirectusClientDep,
    request_id: RequestId,
) -> BookingResponse:
    """
    Update a booking request.

    Only allows updating priority, max_attempts, target_location, and metadata
    for bookings that are not yet processing.

    Args:
        booking_id: The booking UUID.
        update_data: Fields to update.
        agency: Verified agency information.
        directus: Directus client.
        request_id: Request ID for tracing.

    Returns:
        BookingResponse: The updated booking.
    """
    repo = BookingRepository(client=directus)
    agency_id = str(agency["id"])

    booking = await get_booking_or_404(booking_id, agency_id, repo)

    # Only allow updates to pending/queued bookings
    current_status = booking.get("status")
    if current_status not in ("pending", "queued"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot update booking in {current_status} status",
        )

    # Only allow updating specific fields
    allowed_fields = {"priority", "max_attempts", "target_location", "metadata"}
    filtered_data = {k: v for k, v in update_data.items() if k in allowed_fields}

    if not filtered_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No valid fields to update. Allowed: {allowed_fields}",
        )

    logger.info(
        "booking_update_request",
        request_id=request_id,
        booking_id=str(booking_id),
        fields=list(filtered_data.keys()),
    )

    updated = await repo.update(booking_id, filtered_data)

    return BookingResponse(**_format_booking_response(updated))


@router.delete(
    "/{booking_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel a booking request",
    description="Cancel a booking request. Only pending or queued bookings can be cancelled.",
    responses={
        204: {"description": "Booking cancelled"},
        400: {"description": "Booking cannot be cancelled"},
        401: {"description": "Authentication required"},
        403: {"description": "Not authorized to access this booking"},
        404: {"description": "Booking not found"},
    },
)
async def cancel_booking(
    booking_id: UUID,
    agency: VerifiedAgency,
    directus: DirectusClientDep,
    request_id: RequestId,
) -> None:
    """
    Cancel a booking request.

    Only allows cancelling bookings that are pending or queued.
    Processing bookings cannot be cancelled through this endpoint.

    Args:
        booking_id: The booking UUID.
        agency: Verified agency information.
        directus: Directus client.
        request_id: Request ID for tracing.
    """
    repo = BookingRepository(client=directus)
    agency_id = str(agency["id"])

    booking = await get_booking_or_404(booking_id, agency_id, repo)

    # Only allow cancelling pending/queued bookings
    current_status = booking.get("status")
    if current_status not in ("pending", "queued"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel booking in {current_status} status. Contact support for processing bookings.",
        )

    logger.info(
        "booking_cancel_request",
        request_id=request_id,
        booking_id=str(booking_id),
        previous_status=current_status,
    )

    # Update status to cancelled
    await repo.update_status(
        booking_id,
        BookingStatus.CANCELLED,
        error_code="CANCELLED_BY_USER",
        error_message="Booking cancelled by agency",
    )

    logger.info(
        "booking_cancelled",
        request_id=request_id,
        booking_id=str(booking_id),
    )


@router.put(
    "/{booking_id}/status",
    response_model=BookingResponse,
    summary="Update booking status",
    description="Update the status of a booking request with state machine validation.",
    responses={
        200: {"description": "Status updated"},
        400: {"description": "Invalid status transition"},
        401: {"description": "Authentication required"},
        403: {"description": "Not authorized to access this booking"},
        404: {"description": "Booking not found"},
    },
)
async def update_booking_status(
    booking_id: UUID,
    status_update: BookingStatusUpdate,
    agency: VerifiedAgency,
    directus: DirectusClientDep,
    request_id: RequestId,
) -> BookingResponse:
    """
    Update booking status with state machine validation.

    Validates the status transition using the booking state machine.
    Used primarily for internal status updates.

    Args:
        booking_id: The booking UUID.
        status_update: New status and optional error details.
        agency: Verified agency information.
        directus: Directus client.
        request_id: Request ID for tracing.

    Returns:
        BookingResponse: The updated booking.
    """
    repo = BookingRepository(client=directus)
    agency_id = str(agency["id"])

    booking = await get_booking_or_404(booking_id, agency_id, repo)

    # Validate state transition using state machine
    state_machine = BookingStateMachine()
    current_status = booking.get("status")

    # Map string status to BookingState enum
    from src.core.booking.state_machine import BookingState as SMState

    try:
        current_state = SMState(current_status)
        target_state = SMState(status_update.status.value)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status value: {e}",
        )

    # Check if transition is valid
    valid_targets = state_machine.get_valid_transitions(current_state)
    if target_state not in valid_targets:
        allowed = [s.value for s in valid_targets]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status transition from {current_status} to {status_update.status.value}. "
            f"Allowed transitions: {allowed}",
        )

    logger.info(
        "booking_status_update_request",
        request_id=request_id,
        booking_id=str(booking_id),
        from_status=current_status,
        to_status=status_update.status.value,
    )

    # Update status
    updated = await repo.update_status(
        booking_id,
        status_update.status,
        error_code=status_update.error_code,
        error_message=status_update.error_message,
    )

    return BookingResponse(**_format_booking_response(updated))


@router.get(
    "/{booking_id}/result",
    response_model=BookingResultResponse,
    summary="Get booking result",
    description="Get the result/outcome of a completed booking.",
    responses={
        200: {"description": "Booking result"},
        401: {"description": "Authentication required"},
        403: {"description": "Not authorized to access this booking"},
        404: {"description": "Booking or result not found"},
    },
)
async def get_booking_result(
    booking_id: UUID,
    agency: VerifiedAgency,
    directus: DirectusClientDep,
) -> BookingResultResponse:
    """
    Get the result of a completed booking.

    Returns the booking result record which contains confirmation
    details, timing metrics, and outcome information.

    Args:
        booking_id: The booking UUID.
        agency: Verified agency information.
        directus: Directus client.

    Returns:
        BookingResultResponse: The booking result.
    """
    repo = BookingRepository(client=directus)
    agency_id = str(agency["id"])

    # First verify the booking exists and belongs to the agency
    await get_booking_or_404(booking_id, agency_id, repo)

    # Get the result
    result = await repo.get_result_by_booking_id(booking_id)

    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking result not found. The booking may not be completed yet.",
        )

    return BookingResultResponse(
        id=result["id"],
        agency_id=result["agency_id"],
        booking_request_id=result["booking_request_id"],
        status=result["status"],
        target_system=result["target_system"],
        target_country=result["target_country"],
        confirmation_number=result.get("confirmation_number"),
        appointment_date=result.get("appointment_date"),
        appointment_time=result.get("appointment_time"),
        appointment_location=result.get("appointment_location"),
        total_attempts=result.get("total_attempts", 0),
        total_duration_seconds=result.get("total_duration_seconds", 0),
        credits_charged=result.get("credits_charged", 0),
        error_code=result.get("error_code"),
        screenshot_url=result.get("screenshot_url"),
        created_at=result.get("date_created") or result.get("created_at"),
    )


@router.post(
    "/batch",
    response_model=list[BookingResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create multiple booking requests",
    description="Create multiple booking requests in a single operation.",
    responses={
        201: {"description": "Bookings created"},
        400: {"description": "Invalid request data"},
        401: {"description": "Authentication required"},
        403: {"description": "Not authorized or insufficient credits"},
    },
)
async def create_batch_bookings(
    batch: BookingBatchRequest,
    agency: VerifiedAgency,
    directus: DirectusClientDep,
    request_id: RequestId,
) -> list[BookingResponse]:
    """
    Create multiple booking requests.

    Creates all bookings in the batch for the authenticated agency.
    All bookings must belong to the authenticated agency.

    Args:
        batch: Batch of booking requests.
        agency: Verified agency information.
        directus: Directus client.
        request_id: Request ID for tracing.

    Returns:
        list[BookingResponse]: The created bookings.
    """
    repo = BookingRepository(client=directus)
    agency_id = str(agency["id"])

    # Verify all bookings belong to the authenticated agency
    for booking in batch.bookings:
        if str(booking.agency_id) != agency_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="All bookings must belong to the authenticated agency",
            )

    logger.info(
        "batch_booking_create_request",
        request_id=request_id,
        agency_id=agency_id,
        count=len(batch.bookings),
    )

    # Create all bookings
    created_bookings = []
    for booking in batch.bookings:
        booking_data = booking.model_dump(mode="json")
        booking_data["agency_id"] = str(booking_data["agency_id"])
        booking_data["applicant_id"] = str(booking_data["applicant_id"])
        booking_data["target_system"] = booking.target_system.value

        created = await repo.create(booking_data)
        created_bookings.append(BookingResponse(**_format_booking_response(created)))

    logger.info(
        "batch_bookings_created",
        request_id=request_id,
        agency_id=agency_id,
        count=len(created_bookings),
    )

    return created_bookings


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "router",
]
