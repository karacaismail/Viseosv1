"""
Middleware components for VISE OS FastAPI application.

This module provides HTTP middleware for:
- Request/response logging with structured logging
- CORS handling
- Request timing and metrics
- Error handling

Usage:
    from fastapi import FastAPI
    from src.api.middleware import LoggingMiddleware, setup_middleware

    app = FastAPI()
    setup_middleware(app)

    # Or add individually:
    app.add_middleware(LoggingMiddleware)
"""

from src.api.middleware.logging import (
    LoggingMiddleware,
    RequestContextMiddleware,
    TimingMiddleware,
)

__all__ = [
    "LoggingMiddleware",
    "RequestContextMiddleware",
    "TimingMiddleware",
]


def setup_middleware(app):
    """
    Configure all middleware for the FastAPI application.

    This function sets up middleware in the correct order.
    Middleware is executed in reverse order of addition,
    so the last added middleware runs first.

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
        expose_headers=["X-Request-ID", "X-Response-Time"],
    )

    # Add timing middleware (measures request duration)
    app.add_middleware(TimingMiddleware)

    # Add request context middleware (adds request ID)
    app.add_middleware(RequestContextMiddleware)

    # Add logging middleware (logs request/response)
    app.add_middleware(LoggingMiddleware)
