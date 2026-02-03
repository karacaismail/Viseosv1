"""
Middleware components for VISE OS FastAPI application.

This module provides HTTP middleware for:
- Request/response logging with structured logging
- CORS handling
- Request timing and metrics
- Error handling
- Authentication (JWT and API key)
- Rate limiting

Usage:
    from fastapi import FastAPI
    from src.api.middleware import LoggingMiddleware, setup_middleware

    app = FastAPI()
    setup_middleware(app)

    # Or add individually:
    app.add_middleware(LoggingMiddleware)
"""

from src.api.middleware.auth import (
    AuthMiddleware,
    create_access_token,
    decode_token,
    get_current_agency,
    get_current_user,
    require_role,
)
from src.api.middleware.logging import (
    LoggingMiddleware,
    RequestContextMiddleware,
    TimingMiddleware,
)
from src.api.middleware.rate_limit import (
    RateLimitMiddleware,
    get_rate_limit_store,
    rate_limit,
)

__all__ = [
    # Logging
    "LoggingMiddleware",
    "RequestContextMiddleware",
    "TimingMiddleware",
    # Auth
    "AuthMiddleware",
    "get_current_user",
    "get_current_agency",
    "require_role",
    "create_access_token",
    "decode_token",
    # Rate Limiting
    "RateLimitMiddleware",
    "get_rate_limit_store",
    "rate_limit",
]


def setup_middleware(app):
    """
    Configure all middleware for the FastAPI application.

    This function sets up middleware in the correct order.
    Middleware is executed in reverse order of addition,
    so the last added middleware runs first.

    Middleware order (execution order, first to last):
    1. LoggingMiddleware - Logs all requests
    2. RequestContextMiddleware - Adds request ID
    3. TimingMiddleware - Measures request duration
    4. RateLimitMiddleware - Enforces rate limits
    5. AuthMiddleware - Validates authentication
    6. CORSMiddleware - Handles CORS
    7. GZipMiddleware - Compresses responses

    Args:
        app: The FastAPI application instance.

    Example:
        from fastapi import FastAPI
        from src.api.middleware import setup_middleware

        app = FastAPI()
        setup_middleware(app)
    """
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.middleware.gzip import GZipMiddleware

    from src.api.config import get_settings

    settings = get_settings()

    # Add GZip compression for responses > 1KB
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # Add CORS middleware
    # In production, restrict origins to known clients
    allowed_origins = ["*"] if settings.DEBUG else [
        "https://vise-os.com",
        "https://app.vise-os.com",
        "https://admin.vise-os.com",
    ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "X-Request-ID",
            "X-Response-Time",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
        ],
    )

    # Add authentication middleware
    # Note: Auth runs after rate limiting to prevent auth overhead on rate-limited requests
    app.add_middleware(AuthMiddleware)

    # Add rate limiting middleware
    app.add_middleware(RateLimitMiddleware)

    # Add timing middleware (measures request duration)
    app.add_middleware(TimingMiddleware)

    # Add request context middleware (adds request ID)
    app.add_middleware(RequestContextMiddleware)

    # Add logging middleware (logs request/response)
    app.add_middleware(LoggingMiddleware)
