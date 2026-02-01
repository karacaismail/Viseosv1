"""
FastAPI main application entry point for VISE OS.

This module creates and configures the FastAPI application instance with:
- API routers for bookings, agencies, applicants, and webhooks
- Middleware for logging, CORS, timing, and error handling
- Health check endpoints
- Exception handlers for custom exceptions
- OpenAPI documentation configuration

Usage:
    # Development server
    uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

    # Production server
    uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 4
"""

from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.api.config import get_settings
from src.api.middleware import setup_middleware
from src.api.middleware.logging import configure_structlog
from src.core.exceptions import ViseOSError

logger = structlog.get_logger()

# =============================================================================
# Application Lifespan
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application startup and shutdown events.

    This async context manager handles:
    - Startup: Initialize logging, connections, and resources
    - Shutdown: Clean up connections and resources gracefully

    Args:
        app: The FastAPI application instance.

    Yields:
        None: Allows the application to run between startup and shutdown.
    """
    settings = get_settings()

    # Configure structured logging
    configure_structlog(
        json_format=settings.LOG_FORMAT == "json",
        log_level=settings.LOG_LEVEL,
    )

    logger.info(
        "application_starting",
        app_name=settings.APP_NAME,
        environment=settings.ENVIRONMENT,
        debug=settings.DEBUG,
    )

    # Startup: Initialize resources
    # TODO: Initialize Redis connection pool
    # TODO: Initialize Directus client
    # TODO: Warm up caches

    logger.info("application_started")

    yield

    # Shutdown: Clean up resources
    logger.info("application_stopping")

    # TODO: Close Redis connections
    # TODO: Close HTTP client connections
    # TODO: Flush pending metrics

    logger.info("application_stopped")


# =============================================================================
# Application Factory
# =============================================================================


def create_application() -> FastAPI:
    """
    Create and configure the FastAPI application.

    This factory function creates a fully configured FastAPI instance with:
    - OpenAPI documentation configuration
    - Middleware setup
    - Exception handlers
    - Router registration

    Returns:
        FastAPI: The configured application instance.
    """
    settings = get_settings()

    # Create FastAPI application
    application = FastAPI(
        title=settings.APP_NAME,
        description=(
            "VISE OS - B2B2C automation platform for Turkish visa agencies. "
            "Automates appointment booking processes across multiple visa service providers."
        ),
        version="0.1.0",
        docs_url="/docs" if settings.DEBUG else None,  # Disable docs in production
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
        lifespan=lifespan,
        # OpenAPI configuration
        openapi_tags=[
            {
                "name": "health",
                "description": "Health check and readiness endpoints",
            },
            {
                "name": "bookings",
                "description": "Booking request management",
            },
            {
                "name": "agencies",
                "description": "Agency profile and credit management",
            },
            {
                "name": "applicants",
                "description": "Applicant data management",
            },
            {
                "name": "webhooks",
                "description": "External webhook endpoints",
            },
        ],
    )

    # Setup middleware
    setup_middleware(application)

    # Register exception handlers
    register_exception_handlers(application)

    # Register routers
    register_routers(application)

    return application


# =============================================================================
# Exception Handlers
# =============================================================================


def register_exception_handlers(app: FastAPI) -> None:
    """
    Register custom exception handlers for the application.

    Args:
        app: The FastAPI application instance.
    """

    @app.exception_handler(ViseOSError)
    async def vise_os_exception_handler(
        request: Request,
        exc: ViseOSError,
    ) -> JSONResponse:
        """
        Handle all VISE OS custom exceptions.

        Converts ViseOSError and subclasses to JSON responses with
        appropriate status codes.
        """
        # Map exception types to HTTP status codes
        status_code = _get_status_code_for_exception(exc)

        # Log the exception
        logger.error(
            "vise_os_error",
            error_type=type(exc).__name__,
            error_code=exc.code,
            message=exc.message,
            details=exc.details,
            path=request.url.path,
        )

        return JSONResponse(
            status_code=status_code,
            content=exc.to_dict(),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """
        Handle Pydantic validation errors.

        Converts validation errors to a consistent JSON format.
        """
        errors = []
        for error in exc.errors():
            errors.append({
                "field": ".".join(str(loc) for loc in error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            })

        logger.warning(
            "validation_error",
            path=request.url.path,
            errors=errors,
        )

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "ValidationError",
                "message": "Request validation failed",
                "details": {"errors": errors},
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        """
        Handle FastAPI HTTP exceptions.

        Converts HTTP exceptions to a consistent JSON format.
        """
        logger.warning(
            "http_error",
            status_code=exc.status_code,
            detail=exc.detail,
            path=request.url.path,
        )

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "HTTPException",
                "message": exc.detail,
                "code": f"HTTP_{exc.status_code}",
            },
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """
        Handle all unhandled exceptions.

        Catches any exception not handled by other handlers and returns
        a generic 500 error. The actual error is logged but not exposed
        to the client for security.
        """
        settings = get_settings()

        logger.exception(
            "unhandled_exception",
            error_type=type(exc).__name__,
            error=str(exc),
            path=request.url.path,
        )

        # In debug mode, include the actual error message
        if settings.DEBUG:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "code": "INTERNAL_ERROR",
                },
            )

        # In production, return a generic message
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "InternalError",
                "message": "An internal error occurred",
                "code": "INTERNAL_ERROR",
            },
        )


def _get_status_code_for_exception(exc: ViseOSError) -> int:
    """
    Map VISE OS exceptions to HTTP status codes.

    Args:
        exc: The exception to map.

    Returns:
        int: The appropriate HTTP status code.
    """
    from src.core.exceptions import (
        AccountBannedError,
        AccountCooldownError,
        AccountNotFoundError,
        AccountPoolExhaustedError,
        BookingError,
        DirectusAPIError,
        DirectusRateLimitError,
        InsufficientCreditsError,
        InvalidStateTransitionError,
        LoginFailedError,
        NetworkError,
        NetworkTimeoutError,
        PaymentDeclinedError,
        PaymentError,
        ProxyPoolExhaustedError,
        RedisConnectionError,
        SlotNotAvailableError,
    )

    # Map exception types to status codes
    exception_status_map: dict[type, int] = {
        # 400 Bad Request
        InvalidStateTransitionError: status.HTTP_400_BAD_REQUEST,
        # 401 Unauthorized
        LoginFailedError: status.HTTP_401_UNAUTHORIZED,
        # 402 Payment Required
        InsufficientCreditsError: status.HTTP_402_PAYMENT_REQUIRED,
        PaymentDeclinedError: status.HTTP_402_PAYMENT_REQUIRED,
        # 403 Forbidden
        AccountBannedError: status.HTTP_403_FORBIDDEN,
        AccountCooldownError: status.HTTP_403_FORBIDDEN,
        # 404 Not Found
        AccountNotFoundError: status.HTTP_404_NOT_FOUND,
        SlotNotAvailableError: status.HTTP_404_NOT_FOUND,
        # 408 Request Timeout
        NetworkTimeoutError: status.HTTP_408_REQUEST_TIMEOUT,
        # 429 Too Many Requests
        DirectusRateLimitError: status.HTTP_429_TOO_MANY_REQUESTS,
        # 500 Internal Server Error
        RedisConnectionError: status.HTTP_500_INTERNAL_SERVER_ERROR,
        # 502 Bad Gateway
        DirectusAPIError: status.HTTP_502_BAD_GATEWAY,
        # 503 Service Unavailable
        AccountPoolExhaustedError: status.HTTP_503_SERVICE_UNAVAILABLE,
        ProxyPoolExhaustedError: status.HTTP_503_SERVICE_UNAVAILABLE,
    }

    # Check for exact type match first
    for exc_type, status_code in exception_status_map.items():
        if type(exc) is exc_type:
            return status_code

    # Check for subclass matches
    for exc_type, status_code in exception_status_map.items():
        if isinstance(exc, exc_type):
            return status_code

    # Default to 500 for unknown errors
    return status.HTTP_500_INTERNAL_SERVER_ERROR


# =============================================================================
# Router Registration
# =============================================================================


def register_routers(app: FastAPI) -> None:
    """
    Register API routers with the application.

    Routers are registered with the /api prefix. This function will
    include routers as they are implemented in subsequent subtasks.

    Args:
        app: The FastAPI application instance.
    """
    # Health check endpoint (inline for now, will be moved to health router)
    @app.get(
        "/api/health",
        tags=["health"],
        summary="Health check",
        response_model=dict[str, Any],
    )
    async def health_check() -> dict[str, Any]:
        """
        Check if the API is healthy and running.

        Returns basic health status. For detailed health checks
        including dependencies, use /api/health/ready.
        """
        settings = get_settings()
        return {
            "status": "healthy",
            "app_name": settings.APP_NAME,
            "environment": settings.ENVIRONMENT,
        }

    # Root endpoint
    @app.get(
        "/",
        include_in_schema=False,
    )
    async def root() -> dict[str, str]:
        """Root endpoint redirects to API documentation."""
        settings = get_settings()
        return {
            "app": settings.APP_NAME,
            "version": "0.1.0",
            "docs": "/docs" if settings.DEBUG else None,
        }

    # TODO: Register routers as they are implemented
    # from src.api.routers import bookings, agencies, applicants, webhooks, health
    # app.include_router(health.router, prefix="/api", tags=["health"])
    # app.include_router(bookings.router, prefix="/api/bookings", tags=["bookings"])
    # app.include_router(agencies.router, prefix="/api/agencies", tags=["agencies"])
    # app.include_router(applicants.router, prefix="/api/applicants", tags=["applicants"])
    # app.include_router(webhooks.router, prefix="/api/webhooks", tags=["webhooks"])


# =============================================================================
# Application Instance
# =============================================================================

# Create the application instance
app = create_application()


# =============================================================================
# Development Server
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()

    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
