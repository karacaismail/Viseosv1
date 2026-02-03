"""
Rate limiting middleware for VISE OS FastAPI application.

This module provides token bucket rate limiting using Redis for distributed
rate limit tracking across multiple API instances.

Features:
- Sliding window rate limiting per client/agency
- Configurable limits per endpoint
- Redis-backed for distributed deployments
- Fallback to in-memory limiting when Redis unavailable
- Rate limit headers in responses

Usage:
    from fastapi import FastAPI
    from src.api.middleware.rate_limit import RateLimitMiddleware

    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)
"""

import asyncio
import time
from collections import defaultdict
from typing import Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.api.config import get_settings

logger = structlog.get_logger()


# =============================================================================
# In-Memory Rate Limit Store (Fallback)
# =============================================================================


class InMemoryRateLimitStore:
    """
    In-memory rate limit store for single-instance deployments.

    Uses a simple sliding window counter implementation. This is used
    as a fallback when Redis is unavailable.

    Note: This store is NOT suitable for multi-instance deployments
    as rate limits won't be shared across instances.
    """

    def __init__(self) -> None:
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def is_rate_limited(
        self,
        key: str,
        max_requests: int,
        window_seconds: int,
    ) -> tuple[bool, int, int]:
        """
        Check if a key is rate limited.

        Args:
            key: The rate limit key (e.g., client IP or agency ID).
            max_requests: Maximum requests allowed in the window.
            window_seconds: The time window in seconds.

        Returns:
            Tuple of (is_limited, remaining_requests, reset_time).
        """
        now = time.time()
        window_start = now - window_seconds

        async with self._lock:
            # Clean up old entries
            self._requests[key] = [
                ts for ts in self._requests[key] if ts > window_start
            ]

            # Check if rate limited
            current_count = len(self._requests[key])

            if current_count >= max_requests:
                # Rate limited
                oldest = min(self._requests[key]) if self._requests[key] else now
                reset_time = int(oldest + window_seconds)
                return True, 0, reset_time

            # Not rate limited - record this request
            self._requests[key].append(now)
            remaining = max_requests - current_count - 1
            reset_time = int(now + window_seconds)

            return False, remaining, reset_time

    async def cleanup_old_entries(self, max_age_seconds: int = 3600) -> int:
        """
        Clean up entries older than max_age_seconds.

        Args:
            max_age_seconds: Maximum age for entries.

        Returns:
            Number of keys cleaned up.
        """
        now = time.time()
        cutoff = now - max_age_seconds
        cleaned = 0

        async with self._lock:
            keys_to_delete = []
            for key, timestamps in self._requests.items():
                # Remove old timestamps
                self._requests[key] = [ts for ts in timestamps if ts > cutoff]
                # If no timestamps remain, mark for deletion
                if not self._requests[key]:
                    keys_to_delete.append(key)

            for key in keys_to_delete:
                del self._requests[key]
                cleaned += 1

        return cleaned


# =============================================================================
# Redis Rate Limit Store
# =============================================================================


class RedisRateLimitStore:
    """
    Redis-backed rate limit store for distributed deployments.

    Uses Redis sorted sets for efficient sliding window rate limiting.
    Falls back to in-memory store if Redis is unavailable.
    """

    def __init__(self) -> None:
        self._redis_client = None
        self._fallback = InMemoryRateLimitStore()
        self._redis_available = False

    async def _get_redis(self):
        """Get or create Redis connection."""
        if self._redis_client is not None:
            return self._redis_client

        try:
            import redis.asyncio as redis

            settings = get_settings()
            self._redis_client = redis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
            )
            # Test connection
            await self._redis_client.ping()
            self._redis_available = True
            logger.info("redis_rate_limit_connected")
            return self._redis_client

        except Exception as e:
            logger.warning(
                "redis_rate_limit_unavailable",
                error=str(e),
                fallback="in_memory",
            )
            self._redis_available = False
            return None

    async def is_rate_limited(
        self,
        key: str,
        max_requests: int,
        window_seconds: int,
    ) -> tuple[bool, int, int]:
        """
        Check if a key is rate limited using Redis.

        Uses a sliding window implemented with sorted sets.

        Args:
            key: The rate limit key.
            max_requests: Maximum requests allowed.
            window_seconds: The time window in seconds.

        Returns:
            Tuple of (is_limited, remaining_requests, reset_time).
        """
        redis_client = await self._get_redis()

        if redis_client is None:
            # Fallback to in-memory store
            return await self._fallback.is_rate_limited(
                key, max_requests, window_seconds
            )

        try:
            now = time.time()
            window_start = now - window_seconds
            redis_key = f"rate_limit:{key}"

            # Use a pipeline for atomic operations
            async with redis_client.pipeline() as pipe:
                # Remove old entries
                pipe.zremrangebyscore(redis_key, 0, window_start)
                # Count current entries
                pipe.zcard(redis_key)
                # Add current request
                pipe.zadd(redis_key, {str(now): now})
                # Set expiry on the key
                pipe.expire(redis_key, window_seconds + 1)

                results = await pipe.execute()

            current_count = results[1]  # zcard result

            if current_count >= max_requests:
                # Rate limited - get oldest entry for reset time
                oldest = await redis_client.zrange(
                    redis_key, 0, 0, withscores=True
                )
                reset_time = (
                    int(oldest[0][1] + window_seconds) if oldest else int(now + window_seconds)
                )
                return True, 0, reset_time

            remaining = max_requests - current_count - 1
            reset_time = int(now + window_seconds)
            return False, remaining, reset_time

        except Exception as e:
            logger.warning(
                "redis_rate_limit_error",
                error=str(e),
                fallback="in_memory",
            )
            # Fallback to in-memory store
            return await self._fallback.is_rate_limited(
                key, max_requests, window_seconds
            )

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis_client is not None:
            await self._redis_client.close()
            self._redis_client = None


# =============================================================================
# Rate Limit Middleware
# =============================================================================

# Global rate limit store instance
_rate_limit_store: RedisRateLimitStore | None = None


def get_rate_limit_store() -> RedisRateLimitStore:
    """Get or create the global rate limit store."""
    global _rate_limit_store
    if _rate_limit_store is None:
        _rate_limit_store = RedisRateLimitStore()
    return _rate_limit_store


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware that enforces rate limiting on API requests.

    This middleware:
    - Tracks request counts per client (by IP or agency ID)
    - Returns 429 Too Many Requests when limits are exceeded
    - Adds rate limit headers to all responses
    - Skips rate limiting for health check endpoints

    Rate limit headers:
    - X-RateLimit-Limit: Maximum requests allowed
    - X-RateLimit-Remaining: Requests remaining in window
    - X-RateLimit-Reset: Unix timestamp when window resets
    - Retry-After: Seconds to wait (only on 429 responses)

    Example:
        app.add_middleware(RateLimitMiddleware)
    """

    # Endpoints to skip rate limiting
    EXCLUDED_PATHS: set[str] = {
        "/api/health",
        "/api/health/ready",
        "/api/health/live",
        "/metrics",
        "/favicon.ico",
        "/docs",
        "/redoc",
        "/openapi.json",
    }

    # Endpoint-specific rate limits (path prefix -> requests per minute)
    ENDPOINT_LIMITS: dict[str, int] = {
        "/api/bookings": 30,  # More restrictive for booking endpoints
        "/api/webhooks": 120,  # Higher limit for webhooks
    }

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        """
        Process request and enforce rate limits.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware or route handler.

        Returns:
            Response: The HTTP response with rate limit headers.
        """
        # Skip rate limiting for excluded paths
        if request.url.path in self.EXCLUDED_PATHS:
            return await call_next(request)

        settings = get_settings()
        store = get_rate_limit_store()

        # Determine rate limit key
        rate_limit_key = self._get_rate_limit_key(request)

        # Determine applicable rate limit
        max_requests = self._get_rate_limit(
            request.url.path,
            settings.RATE_LIMIT_REQUESTS_PER_MINUTE,
        )

        # Check rate limit (60 second window)
        is_limited, remaining, reset_time = await store.is_rate_limited(
            rate_limit_key,
            max_requests,
            window_seconds=60,
        )

        if is_limited:
            logger.warning(
                "rate_limit_exceeded",
                key=rate_limit_key,
                path=request.url.path,
                limit=max_requests,
            )

            retry_after = max(1, reset_time - int(time.time()))

            return JSONResponse(
                status_code=429,
                content={
                    "error": "RateLimitExceeded",
                    "message": "Too many requests. Please slow down.",
                    "code": "RATE_LIMIT_EXCEEDED",
                    "details": {
                        "limit": max_requests,
                        "window_seconds": 60,
                        "retry_after": retry_after,
                    },
                },
                headers={
                    "X-RateLimit-Limit": str(max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_time),
                    "Retry-After": str(retry_after),
                },
            )

        # Process request
        response = await call_next(request)

        # Add rate limit headers to response
        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_time)

        return response

    def _get_rate_limit_key(self, request: Request) -> str:
        """
        Generate a rate limit key for the request.

        Uses agency_id if authenticated, otherwise falls back to client IP.

        Args:
            request: The HTTP request.

        Returns:
            str: The rate limit key.
        """
        # Use agency_id if available (set by AuthMiddleware)
        agency_id = getattr(request.state, "agency_id", None)
        if agency_id:
            return f"agency:{agency_id}"

        # Fall back to client IP
        client_ip = self._get_client_ip(request)
        return f"ip:{client_ip}"

    def _get_client_ip(self, request: Request) -> str:
        """
        Extract the real client IP address.

        Args:
            request: The HTTP request.

        Returns:
            str: The client IP address.
        """
        # Check X-Forwarded-For header
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        # Check X-Real-IP header
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        # Fall back to direct client
        if request.client:
            return request.client.host

        return "unknown"

    def _get_rate_limit(self, path: str, default_limit: int) -> int:
        """
        Get the rate limit for a specific endpoint.

        Args:
            path: The request path.
            default_limit: Default rate limit if no specific limit.

        Returns:
            int: The rate limit (requests per minute).
        """
        # Check for endpoint-specific limits
        for prefix, limit in self.ENDPOINT_LIMITS.items():
            if path.startswith(prefix):
                return limit

        return default_limit


# =============================================================================
# Rate Limit Decorators
# =============================================================================


def rate_limit(
    requests_per_minute: int = 60,
    key_func: Callable[[Request], str] | None = None,
):
    """
    Decorator for custom rate limiting on specific routes.

    Use this when you need different rate limits for specific endpoints
    that differ from the global middleware settings.

    Args:
        requests_per_minute: Maximum requests per minute.
        key_func: Optional function to generate rate limit key.

    Returns:
        Decorator function.

    Example:
        @app.post("/api/bookings")
        @rate_limit(requests_per_minute=10)
        async def create_booking(request: Request, booking: BookingCreate):
            return await booking_service.create(booking)
    """

    def decorator(func: Callable):
        async def wrapper(request: Request, *args, **kwargs):
            store = get_rate_limit_store()

            # Generate rate limit key
            if key_func:
                rate_key = key_func(request)
            else:
                # Default: use agency_id or IP
                agency_id = getattr(request.state, "agency_id", None)
                if agency_id:
                    rate_key = f"route:{func.__name__}:agency:{agency_id}"
                elif request.client:
                    rate_key = f"route:{func.__name__}:ip:{request.client.host}"
                else:
                    rate_key = f"route:{func.__name__}:unknown"

            # Check rate limit
            is_limited, remaining, reset_time = await store.is_rate_limited(
                rate_key,
                requests_per_minute,
                window_seconds=60,
            )

            if is_limited:
                retry_after = max(1, reset_time - int(time.time()))
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "RateLimitExceeded",
                        "message": "Too many requests for this endpoint",
                        "code": "RATE_LIMIT_EXCEEDED",
                    },
                    headers={
                        "Retry-After": str(retry_after),
                    },
                )

            return await func(request, *args, **kwargs)

        # Preserve function metadata
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__

        return wrapper

    return decorator


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "RateLimitMiddleware",
    "InMemoryRateLimitStore",
    "RedisRateLimitStore",
    "get_rate_limit_store",
    "rate_limit",
]
