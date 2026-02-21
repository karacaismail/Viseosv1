"""
FastAPI dependency injection utilities for VISE OS.

This module provides reusable dependencies for FastAPI routes, including:
- Settings configuration access
- Directus client access
- Authentication and authorization
- Rate limiting
- Request context management

Usage:
    from fastapi import Depends
    from src.api.dependencies import get_current_settings, get_directus

    @router.get("/items")
    async def get_items(
        settings: Settings = Depends(get_current_settings),
        directus: DirectusClient = Depends(get_directus),
    ):
        ...
"""

from typing import Annotated, AsyncGenerator

import structlog
from fastapi import Depends, Header, HTTPException, Request, status

from src.api.config import Settings, get_settings
from src.integrations.directus import DirectusClient, get_directus_client

logger = structlog.get_logger()


# =============================================================================
# Settings Dependencies
# =============================================================================


def get_current_settings() -> Settings:
    """
    Get the current application settings.

    This dependency provides access to the cached Settings instance.
    Use this in routes that need configuration values.

    Returns:
        Settings: The application settings instance.

    Example:
        @router.get("/config")
        async def get_config(settings: Settings = Depends(get_current_settings)):
            return {"app_name": settings.APP_NAME}
    """
    return get_settings()


# Type alias for settings dependency
CurrentSettings = Annotated[Settings, Depends(get_current_settings)]


# =============================================================================
# Directus Client Dependencies
# =============================================================================


async def get_directus() -> AsyncGenerator[DirectusClient, None]:
    """
    Get a Directus client instance.

    This dependency provides an async Directus client for database operations.
    The client is properly cleaned up after the request completes.

    Yields:
        DirectusClient: An async Directus client instance.

    Example:
        @router.get("/bookings")
        async def get_bookings(directus: DirectusClient = Depends(get_directus)):
            return await directus.get_items("booking_requests")
    """
    client = get_directus_client()
    try:
        yield client
    finally:
        # Client cleanup is handled by the cached instance
        pass


# Type alias for Directus client dependency
DirectusClientDep = Annotated[DirectusClient, Depends(get_directus)]


# =============================================================================
# Request Context Dependencies
# =============================================================================


async def get_request_id(
    request: Request,
    x_request_id: str | None = Header(None, alias="X-Request-ID"),
) -> str:
    """
    Get or generate a request ID for tracing.

    If the client provides an X-Request-ID header, it will be used.
    Otherwise, a new UUID is generated. The request ID is stored
    in the request state for use in logging.

    Args:
        request: The FastAPI request object.
        x_request_id: Optional request ID from client header.

    Returns:
        str: The request ID for tracing.
    """
    import uuid

    request_id = x_request_id or str(uuid.uuid4())
    request.state.request_id = request_id
    return request_id


# Type alias for request ID dependency
RequestId = Annotated[str, Depends(get_request_id)]


# =============================================================================
# Authentication Dependencies
# =============================================================================


async def get_api_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
) -> str:
    """
    Extract API key from request headers.

    Supports two authentication methods:
    1. X-API-Key header (preferred)
    2. Authorization: Bearer <token> header

    Args:
        x_api_key: API key from X-API-Key header.
        authorization: Authorization header value.

    Returns:
        str: The extracted API key.

    Raises:
        HTTPException: If no valid API key is provided.
    """
    # Try X-API-Key header first
    if x_api_key:
        return x_api_key

    # Try Authorization: Bearer header
    if authorization:
        if authorization.startswith("Bearer "):
            return authorization[7:]  # Remove "Bearer " prefix

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="API key required. Provide X-API-Key header or Authorization: Bearer <token>",
        headers={"WWW-Authenticate": "Bearer"},
    )


# Type alias for API key dependency
ApiKey = Annotated[str, Depends(get_api_key)]


async def verify_api_key(
    api_key: str = Depends(get_api_key),
    settings: Settings = Depends(get_current_settings),
    directus: DirectusClient = Depends(get_directus),
) -> dict:
    """
    Verify API key and return agency information.

    Validates the API key against stored agency credentials in Directus.
    Returns the agency information if valid.

    Args:
        api_key: The API key to verify.
        settings: Application settings.
        directus: Directus client for database lookup.

    Returns:
        dict: Agency information if key is valid.

    Raises:
        HTTPException: If API key is invalid or agency is inactive.
    """
    try:
        # Query Directus for agency with matching API key
        agencies = await directus.get_items(
            "agency_profiles",
            filter_dict={"api_key": {"_eq": api_key}},
            limit=1,
        )

        if not agencies:
            logger.warning("invalid_api_key", api_key_prefix=api_key[:8] + "...")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )

        agency = agencies[0]

        # Check agency status
        if agency.get("status") != "active":
            logger.warning(
                "inactive_agency",
                agency_id=agency["id"],
                status=agency.get("status"),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Agency account is {agency.get('status')}",
            )

        # Log successful authentication
        logger.info(
            "api_key_verified",
            agency_id=agency["id"],
            agency_name=agency.get("name"),
        )

        return agency

    except HTTPException:
        raise
    except Exception as e:
        logger.error("api_key_verification_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication service error",
        )


# Type alias for verified agency dependency
VerifiedAgency = Annotated[dict, Depends(verify_api_key)]


# =============================================================================
# Pagination Dependencies
# =============================================================================


class PaginationParams:
    """
    Pagination parameters for list endpoints.

    Provides standardized pagination with sensible defaults and limits.

    Attributes:
        page: Current page number (1-indexed).
        page_size: Number of items per page.
        offset: Calculated offset for database queries.
    """

    def __init__(
        self,
        page: int = 1,
        page_size: int = 20,
    ):
        """
        Initialize pagination parameters.

        Args:
            page: Page number (1-indexed, default 1).
            page_size: Items per page (default 20, max 100).
        """
        # Ensure positive values
        self.page = max(1, page)
        self.page_size = max(1, min(100, page_size))  # Clamp between 1 and 100

    @property
    def offset(self) -> int:
        """Calculate the offset for database queries."""
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        """Alias for page_size for compatibility."""
        return self.page_size


def get_pagination(
    page: int = 1,
    page_size: int = 20,
) -> PaginationParams:
    """
    Get pagination parameters from query string.

    Args:
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        PaginationParams: Validated pagination parameters.
    """
    return PaginationParams(page=page, page_size=page_size)


# Type alias for pagination dependency
Pagination = Annotated[PaginationParams, Depends(get_pagination)]


# =============================================================================
# Agency Context Dependencies
# =============================================================================


async def get_agency_id(agency: dict = Depends(verify_api_key)) -> str:
    """
    Extract agency ID from verified agency context.

    This dependency is useful when you only need the agency ID
    rather than the full agency object.

    Args:
        agency: Verified agency information.

    Returns:
        str: The agency's unique identifier.
    """
    return agency["id"]


# Type alias for agency ID dependency
AgencyId = Annotated[str, Depends(get_agency_id)]


# =============================================================================
# Optional Authentication Dependencies
# =============================================================================


async def get_optional_api_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
) -> str | None:
    """
    Get API key if provided, or None if not.

    Use this for endpoints that support both authenticated
    and unauthenticated access.

    Args:
        x_api_key: API key from X-API-Key header.
        authorization: Authorization header value.

    Returns:
        str | None: The API key if provided, None otherwise.
    """
    if x_api_key:
        return x_api_key

    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]

    return None


# Type alias for optional API key
OptionalApiKey = Annotated[str | None, Depends(get_optional_api_key)]


# =============================================================================
# Bot Service Container Dependencies
# =============================================================================


async def get_bot_container():
    """
    Get the initialized BotServiceContainer singleton.

    Returns:
        BotServiceContainer: The initialized bot service container.

    Raises:
        HTTPException: If container is not initialized.
    """
    from src.bot.container import BotServiceContainer

    container = BotServiceContainer.get_instance()
    if not container.is_initialized:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bot services not initialized",
        )
    return container


# Type alias for bot container dependency
BotContainer = Annotated["BotServiceContainer", Depends(get_bot_container)]


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Settings
    "get_current_settings",
    "CurrentSettings",
    # Directus
    "get_directus",
    "DirectusClientDep",
    # Request context
    "get_request_id",
    "RequestId",
    # Authentication
    "get_api_key",
    "ApiKey",
    "verify_api_key",
    "VerifiedAgency",
    "get_optional_api_key",
    "OptionalApiKey",
    # Pagination
    "PaginationParams",
    "get_pagination",
    "Pagination",
    # Agency context
    "get_agency_id",
    "AgencyId",
    # Bot services
    "get_bot_container",
    "BotContainer",
]
