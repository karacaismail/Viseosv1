"""
Agencies router for VISE OS API.

This module provides endpoints for agency profile management and credit operations
in VISE OS. Agencies access their own profile and credits through authenticated
endpoints.

Endpoints:
    GET /agencies/me - Get current agency profile
    PATCH /agencies/me - Update agency profile
    GET /agencies/me/credits - Get credit balance
    GET /agencies/me/credits/transactions - Get credit transactions
    POST /agencies/me/credits/purchase - Purchase credits

Usage:
    from src.api.routers.agencies import router
    app.include_router(router, prefix="/api/agencies", tags=["agencies"])
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
from src.api.schemas.agency import (
    AgencyCreditsResponse,
    AgencyResponse,
    AgencyStatusUpdate,
    AgencyUpdate,
    CreditPurchaseRequest,
    CreditTransactionListResponse,
    CreditTransactionResponse,
    CreditTransactionType,
)
from src.core.agency.repository import AgencyRepository

logger = structlog.get_logger()

# =============================================================================
# Router
# =============================================================================

router = APIRouter()


# =============================================================================
# Dependencies
# =============================================================================


async def get_agency_repository(
    directus: DirectusClientDep,
) -> AgencyRepository:
    """
    Get an AgencyRepository instance.

    Args:
        directus: Directus client from dependency.

    Returns:
        AgencyRepository: Repository instance.
    """
    return AgencyRepository(client=directus)


# =============================================================================
# Helper Functions
# =============================================================================


def _format_agency_response(agency: dict[str, Any]) -> dict[str, Any]:
    """
    Format an agency dictionary for API response.

    Ensures proper field formatting and handles None values.

    Args:
        agency: Raw agency data from repository.

    Returns:
        Formatted agency data.
    """
    return {
        "id": agency.get("id"),
        "status": agency.get("status"),
        "name": agency.get("name"),
        "tursab_no": agency.get("tursab_no"),
        "contact_name": agency.get("contact_name"),
        "contact_email": agency.get("contact_email"),
        "contact_phone": agency.get("contact_phone"),
        "telegram_chat_id": agency.get("telegram_chat_id"),
        "discord_webhook": agency.get("discord_webhook"),
        "google_sheet_id": agency.get("google_sheet_id"),
        "google_sheet_sync_enabled": agency.get("google_sheet_sync_enabled", False),
        "default_countries": agency.get("default_countries"),
        "notification_preferences": agency.get("notification_preferences"),
        "created_at": agency.get("date_created") or agency.get("created_at"),
        "updated_at": agency.get("date_updated") or agency.get("updated_at"),
    }


def _format_credits_response(credits: dict[str, Any]) -> dict[str, Any]:
    """
    Format a credits dictionary for API response.

    Args:
        credits: Raw credits data from repository.

    Returns:
        Formatted credits data.
    """
    total = credits.get("total_credits", 0)
    used = credits.get("used_credits", 0)
    reserved = credits.get("reserved_credits", 0)

    return {
        "id": credits.get("id"),
        "agency_id": credits.get("agency_id"),
        "total_credits": total,
        "used_credits": used,
        "reserved_credits": reserved,
        "available_credits": max(0, total - used - reserved),
        "last_purchase_at": credits.get("last_purchase_at"),
        "last_usage_at": credits.get("last_usage_at"),
    }


def _format_transaction_response(transaction: dict[str, Any]) -> dict[str, Any]:
    """
    Format a transaction dictionary for API response.

    Args:
        transaction: Raw transaction data from repository.

    Returns:
        Formatted transaction data.
    """
    return {
        "id": transaction.get("id"),
        "agency_id": transaction.get("agency_id"),
        "type": transaction.get("type"),
        "amount": transaction.get("amount"),
        "balance_after": transaction.get("balance_after"),
        "reference_id": transaction.get("reference_id"),
        "description": transaction.get("description"),
        "created_at": transaction.get("date_created") or transaction.get("created_at"),
    }


# =============================================================================
# Endpoints - Agency Profile
# =============================================================================


@router.get(
    "/me",
    response_model=AgencyResponse,
    summary="Get current agency profile",
    description="Get the profile information for the authenticated agency.",
    responses={
        200: {"description": "Agency profile"},
        401: {"description": "Authentication required"},
    },
)
async def get_my_agency(
    agency: VerifiedAgency,
) -> AgencyResponse:
    """
    Get the current agency's profile.

    Returns the full profile for the authenticated agency.

    Args:
        agency: Verified agency information from authentication.

    Returns:
        AgencyResponse: The agency profile.
    """
    return AgencyResponse(**_format_agency_response(agency))


@router.patch(
    "/me",
    response_model=AgencyResponse,
    summary="Update agency profile",
    description="Update the profile for the authenticated agency.",
    responses={
        200: {"description": "Agency profile updated"},
        400: {"description": "Invalid update data"},
        401: {"description": "Authentication required"},
    },
)
async def update_my_agency(
    update_data: AgencyUpdate,
    agency: VerifiedAgency,
    directus: DirectusClientDep,
    request_id: RequestId,
) -> AgencyResponse:
    """
    Update the current agency's profile.

    Only provided fields will be updated. The agency cannot change
    their own status or contact_email.

    Args:
        update_data: Fields to update.
        agency: Verified agency information.
        directus: Directus client.
        request_id: Request ID for tracing.

    Returns:
        AgencyResponse: The updated agency profile.
    """
    repo = AgencyRepository(client=directus)
    agency_id = str(agency["id"])

    # Filter out None values for update
    filtered_data = {
        k: v for k, v in update_data.model_dump(mode="json").items()
        if v is not None
    }

    if not filtered_data:
        # No updates provided, return current data
        return AgencyResponse(**_format_agency_response(agency))

    logger.info(
        "agency_update_request",
        request_id=request_id,
        agency_id=agency_id,
        fields=list(filtered_data.keys()),
    )

    updated = await repo.update(agency_id, filtered_data)

    logger.info(
        "agency_updated",
        request_id=request_id,
        agency_id=agency_id,
    )

    return AgencyResponse(**_format_agency_response(updated))


# =============================================================================
# Endpoints - Credits
# =============================================================================


@router.get(
    "/me/credits",
    response_model=AgencyCreditsResponse,
    summary="Get credit balance",
    description="Get the credit balance for the authenticated agency.",
    responses={
        200: {"description": "Credit balance"},
        401: {"description": "Authentication required"},
        404: {"description": "Credit record not found"},
    },
)
async def get_my_credits(
    agency: VerifiedAgency,
    directus: DirectusClientDep,
) -> AgencyCreditsResponse:
    """
    Get the current agency's credit balance.

    Returns the credit balance including total, used, reserved,
    and available credits.

    Args:
        agency: Verified agency information.
        directus: Directus client.

    Returns:
        AgencyCreditsResponse: The credit balance.
    """
    repo = AgencyRepository(client=directus)
    agency_id = str(agency["id"])

    credits = await repo.get_credits(agency_id)

    if not credits:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credit record not found",
        )

    return AgencyCreditsResponse(**_format_credits_response(credits))


@router.get(
    "/me/credits/transactions",
    response_model=CreditTransactionListResponse,
    summary="Get credit transactions",
    description="Get credit transaction history for the authenticated agency.",
    responses={
        200: {"description": "Credit transaction history"},
        401: {"description": "Authentication required"},
    },
)
async def get_my_credit_transactions(
    agency: VerifiedAgency,
    directus: DirectusClientDep,
    pagination: Pagination,
    transaction_type: CreditTransactionType | None = Query(
        None,
        alias="type",
        description="Filter by transaction type",
    ),
) -> CreditTransactionListResponse:
    """
    Get credit transaction history for the authenticated agency.

    Supports pagination and filtering by transaction type.

    Args:
        agency: Verified agency information.
        directus: Directus client.
        pagination: Pagination parameters.
        transaction_type: Optional type filter.

    Returns:
        CreditTransactionListResponse: Paginated list of transactions.
    """
    repo = AgencyRepository(client=directus)
    agency_id = str(agency["id"])

    # Get transactions with filters
    transactions = await repo.get_transactions(
        agency_id,
        type=transaction_type,
        limit=pagination.page_size + 1,  # Get one extra to check for more
        offset=pagination.offset,
    )

    # Check if there are more results
    has_more = len(transactions) > pagination.page_size
    if has_more:
        transactions = transactions[:pagination.page_size]

    # Format response
    items = [
        CreditTransactionResponse(**_format_transaction_response(t))
        for t in transactions
    ]

    # Get total count (approximate from current batch)
    total = pagination.offset + len(transactions) + (1 if has_more else 0)

    return CreditTransactionListResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        has_more=has_more,
    )


@router.post(
    "/me/credits/purchase",
    response_model=AgencyCreditsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Purchase credits",
    description="Purchase credits for the authenticated agency.",
    responses={
        201: {"description": "Credits purchased successfully"},
        400: {"description": "Invalid purchase request"},
        401: {"description": "Authentication required"},
    },
)
async def purchase_credits(
    purchase: CreditPurchaseRequest,
    agency: VerifiedAgency,
    directus: DirectusClientDep,
    request_id: RequestId,
) -> AgencyCreditsResponse:
    """
    Purchase credits for the authenticated agency.

    Creates a credit transaction and updates the balance.
    Note: In production, this would integrate with payment processing.

    Args:
        purchase: Credit purchase request.
        agency: Verified agency information.
        directus: Directus client.
        request_id: Request ID for tracing.

    Returns:
        AgencyCreditsResponse: Updated credit balance.
    """
    repo = AgencyRepository(client=directus)
    agency_id = str(agency["id"])

    logger.info(
        "credit_purchase_request",
        request_id=request_id,
        agency_id=agency_id,
        amount=purchase.amount,
    )

    try:
        # TODO: In production, integrate with payment gateway (iyzico)
        # For now, directly add credits
        updated = await repo.add_credits(
            agency_id,
            purchase.amount,
            description=purchase.description or "Credit purchase",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    logger.info(
        "credits_purchased",
        request_id=request_id,
        agency_id=agency_id,
        amount=purchase.amount,
    )

    # Get updated credits
    credits = await repo.get_credits(agency_id)

    return AgencyCreditsResponse(**_format_credits_response(credits))


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "router",
]
