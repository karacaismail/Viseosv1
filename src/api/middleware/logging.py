"""
Logging middleware for VISE OS FastAPI application.

This module provides middleware for structured request/response logging
using structlog. It tracks:
- Request details (method, path, headers)
- Response details (status code, timing)
- Request correlation IDs
- Error information

All logs follow the structlog pattern for searchable, context-aware logging.

Usage:
    from fastapi import FastAPI
    from src.api.middleware.logging import LoggingMiddleware

    app = FastAPI()
    app.add_middleware(LoggingMiddleware)
"""

import time
import uuid
from typing import Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = structlog.get_logger()


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Middleware that adds request context (request ID) to each request.

    This middleware generates or extracts a request ID and makes it
    available throughout the request lifecycle. The request ID is:
    - Extracted from X-Request-ID header if provided by client
    - Generated as a new UUID if not provided
    - Stored in request.state.request_id
    - Added to response headers as X-Request-ID

    Example:
        # Client can provide their own request ID:
        curl -H "X-Request-ID: my-trace-id" http://localhost:8000/api/health

        # Or let the server generate one
        curl http://localhost:8000/api/health
        # Response includes: X-Request-ID: <generated-uuid>
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        """
        Process request and add request ID context.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware or route handler.

        Returns:
            Response: The HTTP response with X-Request-ID header.
        """
        # Get or generate request ID
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        # Store in request state for access in routes
        request.state.request_id = request_id

        # Bind request ID to structlog context for all subsequent logs
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Process request
        response = await call_next(request)

        # Add request ID to response headers
        response.headers["X-Request-ID"] = request_id

        return response


class TimingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that measures request processing time.

    This middleware:
    - Records the start time when a request begins
    - Calculates the total processing time
    - Stores timing in request.state.timing_ms
    - Adds X-Response-Time header to response

    Example:
        # Response includes timing header:
        # X-Response-Time: 45.23ms
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        """
        Process request and measure timing.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware or route handler.

        Returns:
            Response: The HTTP response with timing header.
        """
        # Record start time
        start_time = time.perf_counter()

        # Process request
        response = await call_next(request)

        # Calculate processing time
        process_time = time.perf_counter() - start_time
        process_time_ms = process_time * 1000

        # Store in request state
        request.state.timing_ms = process_time_ms

        # Add timing header
        response.headers["X-Response-Time"] = f"{process_time_ms:.2f}ms"

        return response


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that logs all HTTP requests and responses.

    This middleware logs:
    - Request start (method, path, client IP, user agent)
    - Request completion (status code, timing)
    - Errors with full context

    Sensitive headers (Authorization, X-API-Key) are masked in logs.
    Health check endpoints can be excluded from logging to reduce noise.

    Log levels:
    - INFO: Successful requests (2xx, 3xx)
    - WARNING: Client errors (4xx)
    - ERROR: Server errors (5xx)

    Example log output:
        {
            "event": "request_completed",
            "request_id": "abc-123",
            "method": "POST",
            "path": "/api/bookings",
            "status_code": 201,
            "duration_ms": 45.23,
            "client_ip": "192.168.1.1"
        }
    """

    # Paths to exclude from logging (health checks, metrics)
    EXCLUDED_PATHS: set[str] = {
        "/api/health",
        "/api/health/ready",
        "/api/health/live",
        "/metrics",
        "/favicon.ico",
    }

    # Headers to mask in logs
    SENSITIVE_HEADERS: set[str] = {
        "authorization",
        "x-api-key",
        "cookie",
        "set-cookie",
    }

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        """
        Process request and log request/response details.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware or route handler.

        Returns:
            Response: The HTTP response.
        """
        # Skip logging for excluded paths
        if request.url.path in self.EXCLUDED_PATHS:
            return await call_next(request)

        # Extract request details
        request_id = getattr(request.state, "request_id", "unknown")
        method = request.method
        path = request.url.path
        query_string = str(request.url.query) if request.url.query else None
        client_ip = self._get_client_ip(request)
        user_agent = request.headers.get("user-agent", "unknown")

        # Log request start
        logger.info(
            "request_started",
            request_id=request_id,
            method=method,
            path=path,
            query_string=query_string,
            client_ip=client_ip,
            user_agent=user_agent,
        )

        # Process request
        try:
            response = await call_next(request)

            # Get timing from TimingMiddleware
            duration_ms = getattr(request.state, "timing_ms", 0)

            # Log based on status code
            status_code = response.status_code

            if status_code >= 500:
                logger.error(
                    "request_completed",
                    request_id=request_id,
                    method=method,
                    path=path,
                    status_code=status_code,
                    duration_ms=round(duration_ms, 2),
                    client_ip=client_ip,
                )
            elif status_code >= 400:
                logger.warning(
                    "request_completed",
                    request_id=request_id,
                    method=method,
                    path=path,
                    status_code=status_code,
                    duration_ms=round(duration_ms, 2),
                    client_ip=client_ip,
                )
            else:
                logger.info(
                    "request_completed",
                    request_id=request_id,
                    method=method,
                    path=path,
                    status_code=status_code,
                    duration_ms=round(duration_ms, 2),
                    client_ip=client_ip,
                )

            return response

        except Exception as e:
            # Log unhandled exceptions
            logger.exception(
                "request_failed",
                request_id=request_id,
                method=method,
                path=path,
                error=str(e),
                error_type=type(e).__name__,
                client_ip=client_ip,
            )
            raise

    def _get_client_ip(self, request: Request) -> str:
        """
        Extract the real client IP address from request.

        Handles common proxy headers (X-Forwarded-For, X-Real-IP)
        to get the actual client IP when behind a reverse proxy.

        Args:
            request: The HTTP request.

        Returns:
            str: The client IP address.
        """
        # Check X-Forwarded-For header (may contain multiple IPs)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Return the first IP (original client)
            return forwarded_for.split(",")[0].strip()

        # Check X-Real-IP header
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        # Fall back to direct client IP
        if request.client:
            return request.client.host

        return "unknown"

    def _mask_headers(self, headers: dict) -> dict:
        """
        Mask sensitive header values for safe logging.

        Args:
            headers: Dictionary of HTTP headers.

        Returns:
            dict: Headers with sensitive values masked.
        """
        masked = {}
        for key, value in headers.items():
            if key.lower() in self.SENSITIVE_HEADERS:
                masked[key] = "***MASKED***"
            else:
                masked[key] = value
        return masked


def configure_structlog(
    json_format: bool = True,
    log_level: str = "INFO",
) -> None:
    """
    Configure structlog for the application.

    Sets up structlog with appropriate processors for either
    JSON (production) or console (development) output.

    Args:
        json_format: If True, output JSON logs. If False, output colored console logs.
        log_level: The minimum log level to output.

    Example:
        # Development (colored console output)
        configure_structlog(json_format=False, log_level="DEBUG")

        # Production (JSON output)
        configure_structlog(json_format=True, log_level="INFO")
    """
    import logging

    # Set up standard library logging
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, log_level.upper()),
    )

    # Common processors for all configurations
    common_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_format:
        # JSON format for production
        processors = common_processors + [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Console format for development
        processors = common_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "LoggingMiddleware",
    "RequestContextMiddleware",
    "TimingMiddleware",
    "configure_structlog",
]
