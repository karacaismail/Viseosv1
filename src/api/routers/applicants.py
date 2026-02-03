"""
Applicants router for VISE OS API.

This module provides CRUD endpoints for managing applicants in VISE OS.
Applicants are visa appointment candidates submitted by agencies. Their
PII (Personally Identifiable Information) is encrypted at rest and has
a 24-hour retention period per offshore compliance requirements.

Endpoints:
    POST /applicants - Create a new applicant
    GET /applicants - List applicants for the authenticated agency
    GET /applicants/{applicant_id} - Get a specific applicant
    PATCH /applicants/{applicant_id} - Update an applicant (non-PII only)
    DELETE /applicants/{applicant_id} - Soft-delete an applicant
    POST /applicants/batch - Create multiple applicants

Usage:
    from src.api.routers.applicants import router
    app.include_router(router, prefix="/api/applicants", tags=["applicants"])
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.dependencies import (
    DirectusClientDep,
    Pagination,
    RequestId,
    VerifiedAgency,
)
from src.api.schemas.applicant import (
    ApplicantBatchCreate,
    ApplicantCreate,
    ApplicantListResponse,
    ApplicantResponse,
    ApplicantStatus,
    ApplicantSummary,
    ApplicantUpdate,
)
from src.core.applicant.repository import ApplicantRepository

logger = structlog.get_logger()

# =============================================================================
# Router
# =============================================================================

router = APIRouter()


# =============================================================================
# Dependencies
# =============================================================================


async def get_applicant_repository(
    directus: DirectusClientDep,
) -> ApplicantRepository:
    """
    Get an ApplicantRepository instance.

    Args:
        directus: Directus client from dependency.

    Returns:
        ApplicantRepository: Repository instance.
    """
    return ApplicantRepository(client=directus)


# =============================================================================
# Helper Functions
# =============================================================================


async def get_applicant_or_404(
    applicant_id: UUID,
    agency_id: str,
    repo: ApplicantRepository,
) -> dict[str, Any]:
    """
    Get an applicant by ID or raise 404.

    Ensures the applicant belongs to the authenticated agency.

    Args:
        applicant_id: The applicant UUID.
        agency_id: The authenticated agency ID.
        repo: ApplicantRepository instance.

    Returns:
        Applicant data if found and authorized.

    Raises:
        HTTPException: 404 if not found, 403 if unauthorized.
    """
    applicant = await repo.get_by_id(applicant_id)

    if not applicant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Applicant {applicant_id} not found",
        )

    # Verify agency ownership
    if str(applicant.get("agency_id")) != str(agency_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this applicant",
        )

    return applicant


def _format_applicant_response(applicant: dict[str, Any]) -> dict[str, Any]:
    """
    Format an applicant dictionary for API response.

    Ensures proper field formatting and handles None values.

    Args:
        applicant: Raw applicant data from repository.

    Returns:
        Formatted applicant data.
    """
    return {
        "id": applicant.get("id"),
        "agency_id": applicant.get("agency_id"),
        "external_ref": applicant.get("external_ref"),
        "status": applicant.get("status"),
        "first_name": applicant.get("first_name"),
        "last_name": applicant.get("last_name"),
        "birth_date": applicant.get("birth_date"),
        "nationality": applicant.get("nationality"),
        "passport_number": applicant.get("passport_number"),
        "passport_expiry": applicant.get("passport_expiry"),
        "phone": applicant.get("phone"),
        "email": applicant.get("email"),
        "target_country": applicant.get("target_country"),
        "target_city": applicant.get("target_city"),
        "visa_type": applicant.get("visa_type"),
        "preferred_dates": applicant.get("preferred_dates"),
        "exclude_weekends": applicant.get("exclude_weekends", False),
        "family_group_id": applicant.get("family_group_id"),
        "parent_applicant_id": applicant.get("parent_applicant_id"),
        "created_at": applicant.get("date_created") or applicant.get("created_at"),
        "expires_at": applicant.get("expires_at"),
        "deleted_at": applicant.get("deleted_at"),
    }


# =============================================================================
# Endpoints - CRUD
# =============================================================================


@router.post(
    "",
    response_model=ApplicantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an applicant",
    description="Create a new visa appointment applicant for the authenticated agency.",
    responses={
        201: {"description": "Applicant created successfully"},
        400: {"description": "Invalid request data"},
        401: {"description": "Authentication required"},
        403: {"description": "Not authorized or agency inactive"},
    },
)
async def create_applicant(
    applicant: ApplicantCreate,
    agency: VerifiedAgency,
    directus: DirectusClientDep,
    request_id: RequestId,
) -> ApplicantResponse:
    """
    Create a new applicant.

    Creates an applicant for the authenticated agency. PII fields are
    automatically encrypted before storage. The applicant starts in
    PENDING status and PII expires after 24 hours.

    Args:
        applicant: Applicant data.
        agency: Verified agency information.
        directus: Directus client.
        request_id: Request ID for tracing.

    Returns:
        ApplicantResponse: The created applicant.
    """
    repo = ApplicantRepository(client=directus)
    agency_id = str(agency["id"])

    # Verify agency_id matches authenticated agency
    if str(applicant.agency_id) != agency_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create applicants for other agencies",
        )

    logger.info(
        "applicant_create_request",
        request_id=request_id,
        agency_id=agency_id,
        target_country=applicant.target_country,
        visa_type=applicant.visa_type,
    )

    # Create applicant in repository
    applicant_data = applicant.model_dump(mode="json")
    applicant_data["agency_id"] = str(applicant_data["agency_id"])

    # Handle optional UUIDs
    if applicant_data.get("family_group_id"):
        applicant_data["family_group_id"] = str(applicant_data["family_group_id"])
    if applicant_data.get("parent_applicant_id"):
        applicant_data["parent_applicant_id"] = str(applicant_data["parent_applicant_id"])

    # Handle preferred_dates serialization
    if applicant_data.get("preferred_dates"):
        dates = applicant_data["preferred_dates"]
        applicant_data["preferred_dates"] = {
            "from": str(dates.get("from_date", dates.get("from"))),
            "to": str(dates.get("to_date", dates.get("to"))),
        }

    created = await repo.create(applicant_data)

    logger.info(
        "applicant_created",
        request_id=request_id,
        applicant_id=created["id"],
        agency_id=agency_id,
    )

    return ApplicantResponse(**_format_applicant_response(created))


@router.get(
    "",
    response_model=ApplicantListResponse,
    summary="List applicants",
    description="List applicants for the authenticated agency with pagination and filtering.",
    responses={
        200: {"description": "List of applicants"},
        401: {"description": "Authentication required"},
    },
)
async def list_applicants(
    agency: VerifiedAgency,
    directus: DirectusClientDep,
    pagination: Pagination,
    status_filter: ApplicantStatus | None = Query(
        None,
        alias="status",
        description="Filter by applicant status",
    ),
    target_country: str | None = Query(
        None,
        min_length=2,
        max_length=2,
        description="Filter by target country (ISO 3166-1 alpha-2)",
    ),
    include_deleted: bool = Query(
        False,
        description="Include soft-deleted applicants",
    ),
) -> ApplicantListResponse:
    """
    List applicants for the authenticated agency.

    Supports pagination and filtering by status and target country.
    Soft-deleted applicants are excluded by default.

    Args:
        agency: Verified agency information.
        directus: Directus client.
        pagination: Pagination parameters.
        status_filter: Optional status filter.
        target_country: Optional country filter.
        include_deleted: Whether to include soft-deleted applicants.

    Returns:
        ApplicantListResponse: Paginated list of applicants.
    """
    repo = ApplicantRepository(client=directus)
    agency_id = str(agency["id"])

    # Get applicants with filters
    applicants = await repo.find_by_agency(
        agency_id,
        status=status_filter,
        include_deleted=include_deleted,
        limit=pagination.page_size + 1,  # Get one extra to check for more
        offset=pagination.offset,
    )

    # Check if there are more results
    has_more = len(applicants) > pagination.page_size
    if has_more:
        applicants = applicants[:pagination.page_size]

    # Apply target_country filter if provided (post-filter)
    if target_country:
        applicants = [
            a for a in applicants
            if a.get("target_country", "").upper() == target_country.upper()
        ]

    # Format response
    items = [ApplicantResponse(**_format_applicant_response(a)) for a in applicants]

    # Get approximate total count
    total = pagination.offset + len(applicants) + (1 if has_more else 0)

    return ApplicantListResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        has_more=has_more,
    )


@router.get(
    "/{applicant_id}",
    response_model=ApplicantResponse,
    summary="Get an applicant",
    description="Get details of a specific applicant.",
    responses={
        200: {"description": "Applicant details"},
        401: {"description": "Authentication required"},
        403: {"description": "Not authorized to access this applicant"},
        404: {"description": "Applicant not found"},
    },
)
async def get_applicant(
    applicant_id: UUID,
    agency: VerifiedAgency,
    directus: DirectusClientDep,
) -> ApplicantResponse:
    """
    Get a specific applicant by ID.

    Args:
        applicant_id: The applicant UUID.
        agency: Verified agency information.
        directus: Directus client.

    Returns:
        ApplicantResponse: The applicant details.
    """
    repo = ApplicantRepository(client=directus)
    agency_id = str(agency["id"])

    applicant = await get_applicant_or_404(applicant_id, agency_id, repo)

    return ApplicantResponse(**_format_applicant_response(applicant))


@router.patch(
    "/{applicant_id}",
    response_model=ApplicantResponse,
    summary="Update an applicant",
    description="Update an applicant. Only non-PII fields can be updated.",
    responses={
        200: {"description": "Applicant updated"},
        400: {"description": "Invalid update data or applicant cannot be modified"},
        401: {"description": "Authentication required"},
        403: {"description": "Not authorized to access this applicant"},
        404: {"description": "Applicant not found"},
    },
)
async def update_applicant(
    applicant_id: UUID,
    update_data: ApplicantUpdate,
    agency: VerifiedAgency,
    directus: DirectusClientDep,
    request_id: RequestId,
) -> ApplicantResponse:
    """
    Update an applicant.

    Only non-PII fields can be updated after creation:
    - external_ref
    - target_city
    - preferred_dates
    - exclude_weekends

    Args:
        applicant_id: The applicant UUID.
        update_data: Fields to update.
        agency: Verified agency information.
        directus: Directus client.
        request_id: Request ID for tracing.

    Returns:
        ApplicantResponse: The updated applicant.
    """
    repo = ApplicantRepository(client=directus)
    agency_id = str(agency["id"])

    applicant = await get_applicant_or_404(applicant_id, agency_id, repo)

    # Check if applicant is already deleted
    if applicant.get("deleted_at"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot update a deleted applicant",
        )

    # Check if applicant is in a terminal state
    current_status = applicant.get("status")
    if current_status in ("completed", "failed", "expired"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot update applicant in {current_status} status",
        )

    # Filter out None values
    filtered_data = {
        k: v for k, v in update_data.model_dump(mode="json").items()
        if v is not None
    }

    if not filtered_data:
        # No updates provided, return current data
        return ApplicantResponse(**_format_applicant_response(applicant))

    # Handle preferred_dates serialization
    if filtered_data.get("preferred_dates"):
        dates = filtered_data["preferred_dates"]
        filtered_data["preferred_dates"] = {
            "from": str(dates.get("from_date", dates.get("from"))),
            "to": str(dates.get("to_date", dates.get("to"))),
        }

    logger.info(
        "applicant_update_request",
        request_id=request_id,
        applicant_id=str(applicant_id),
        fields=list(filtered_data.keys()),
    )

    updated = await repo.update(applicant_id, filtered_data)

    return ApplicantResponse(**_format_applicant_response(updated))


@router.delete(
    "/{applicant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an applicant",
    description="Soft-delete an applicant by redacting PII.",
    responses={
        204: {"description": "Applicant deleted"},
        400: {"description": "Applicant cannot be deleted"},
        401: {"description": "Authentication required"},
        403: {"description": "Not authorized to access this applicant"},
        404: {"description": "Applicant not found"},
    },
)
async def delete_applicant(
    applicant_id: UUID,
    agency: VerifiedAgency,
    directus: DirectusClientDep,
    request_id: RequestId,
) -> None:
    """
    Soft-delete an applicant.

    Redacts all PII fields and marks the applicant as deleted.
    The record is preserved for audit purposes but PII is removed.

    Args:
        applicant_id: The applicant UUID.
        agency: Verified agency information.
        directus: Directus client.
        request_id: Request ID for tracing.
    """
    repo = ApplicantRepository(client=directus)
    agency_id = str(agency["id"])

    applicant = await get_applicant_or_404(applicant_id, agency_id, repo)

    # Check if already deleted
    if applicant.get("deleted_at"):
        return  # Already deleted, return success

    # Check if applicant is currently processing
    current_status = applicant.get("status")
    if current_status == "processing":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete an applicant that is currently processing",
        )

    logger.info(
        "applicant_delete_request",
        request_id=request_id,
        applicant_id=str(applicant_id),
    )

    await repo.soft_delete(applicant_id)

    logger.info(
        "applicant_deleted",
        request_id=request_id,
        applicant_id=str(applicant_id),
    )


# =============================================================================
# Endpoints - Batch Operations
# =============================================================================


@router.post(
    "/batch",
    response_model=list[ApplicantResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create multiple applicants",
    description="Create multiple applicants in a single operation.",
    responses={
        201: {"description": "Applicants created"},
        400: {"description": "Invalid request data"},
        401: {"description": "Authentication required"},
        403: {"description": "Not authorized"},
    },
)
async def create_batch_applicants(
    batch: ApplicantBatchCreate,
    agency: VerifiedAgency,
    directus: DirectusClientDep,
    request_id: RequestId,
) -> list[ApplicantResponse]:
    """
    Create multiple applicants.

    Creates all applicants in the batch for the authenticated agency.
    All applicants must belong to the authenticated agency.

    Args:
        batch: Batch of applicant requests.
        agency: Verified agency information.
        directus: Directus client.
        request_id: Request ID for tracing.

    Returns:
        list[ApplicantResponse]: The created applicants.
    """
    repo = ApplicantRepository(client=directus)
    agency_id = str(agency["id"])

    # Verify all applicants belong to the authenticated agency
    for applicant in batch.applicants:
        if str(applicant.agency_id) != agency_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="All applicants must belong to the authenticated agency",
            )

    logger.info(
        "batch_applicant_create_request",
        request_id=request_id,
        agency_id=agency_id,
        count=len(batch.applicants),
    )

    # Create all applicants
    created_applicants = []
    for applicant in batch.applicants:
        applicant_data = applicant.model_dump(mode="json")
        applicant_data["agency_id"] = str(applicant_data["agency_id"])

        # Handle optional UUIDs
        if applicant_data.get("family_group_id"):
            applicant_data["family_group_id"] = str(applicant_data["family_group_id"])
        if applicant_data.get("parent_applicant_id"):
            applicant_data["parent_applicant_id"] = str(applicant_data["parent_applicant_id"])

        # Handle preferred_dates serialization
        if applicant_data.get("preferred_dates"):
            dates = applicant_data["preferred_dates"]
            applicant_data["preferred_dates"] = {
                "from": str(dates.get("from_date", dates.get("from"))),
                "to": str(dates.get("to_date", dates.get("to"))),
            }

        created = await repo.create(applicant_data)
        created_applicants.append(
            ApplicantResponse(**_format_applicant_response(created))
        )

    logger.info(
        "batch_applicants_created",
        request_id=request_id,
        agency_id=agency_id,
        count=len(created_applicants),
    )

    return created_applicants


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "router",
]
