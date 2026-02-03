"""
Health check router for VISE OS API.

This module provides health, liveness, and readiness endpoints for monitoring
the application and its dependencies. These endpoints are designed for:
- Load balancer health checks
- Kubernetes liveness and readiness probes
- Monitoring systems (Prometheus, Grafana)
- Operational troubleshooting

Endpoints:
    GET /health - Basic health check (returns immediately)
    GET /health/live - Liveness probe (is the app running?)
    GET /health/ready - Readiness probe (can the app serve traffic?)
    GET /health/detailed - Detailed health with component statuses

Usage:
    from src.api.routers.health import router
    app.include_router(router, prefix="/api", tags=["health"])
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.api.config import Settings, get_settings
from src.api.dependencies import get_current_settings

logger = structlog.get_logger()


# =============================================================================
# Enums
# =============================================================================


class HealthStatus(str, Enum):
    """Health check status values."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ComponentStatus(str, Enum):
    """Individual component status values."""

    UP = "up"
    DOWN = "down"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


# =============================================================================
# Response Models
# =============================================================================


class BasicHealthResponse(BaseModel):
    """Basic health check response."""

    status: HealthStatus = Field(
        description="Overall health status of the application"
    )
    app_name: str = Field(description="Name of the application")
    environment: str = Field(description="Current environment (development, staging, production)")
    timestamp: str = Field(description="ISO 8601 timestamp of the health check")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "healthy",
                "app_name": "VISE OS",
                "environment": "development",
                "timestamp": "2026-02-01T12:00:00Z",
            }
        }
    }


class LivenessResponse(BaseModel):
    """Liveness probe response."""

    status: str = Field(description="Liveness status ('alive' or error)")
    timestamp: str = Field(description="ISO 8601 timestamp")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "alive",
                "timestamp": "2026-02-01T12:00:00Z",
            }
        }
    }


class ComponentHealth(BaseModel):
    """Health status of an individual component."""

    status: ComponentStatus = Field(description="Component status")
    latency_ms: float | None = Field(
        default=None,
        description="Response latency in milliseconds",
    )
    message: str | None = Field(
        default=None,
        description="Additional status message",
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Additional component-specific details",
    )


class ReadinessResponse(BaseModel):
    """Readiness probe response."""

    status: HealthStatus = Field(description="Overall readiness status")
    ready: bool = Field(description="Whether the application is ready to serve traffic")
    components: dict[str, ComponentHealth] = Field(
        description="Status of individual components"
    )
    timestamp: str = Field(description="ISO 8601 timestamp")

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "healthy",
                "ready": True,
                "components": {
                    "database": {
                        "status": "up",
                        "latency_ms": 12.5,
                        "message": "Connected to Directus",
                    },
                    "redis": {
                        "status": "up",
                        "latency_ms": 2.1,
                        "message": "Connected to Redis",
                    },
                },
                "timestamp": "2026-02-01T12:00:00Z",
            }
        }
    }


class DetailedHealthResponse(BaseModel):
    """Detailed health check response with all diagnostics."""

    status: HealthStatus = Field(description="Overall health status")
    app_name: str = Field(description="Application name")
    version: str = Field(description="Application version")
    environment: str = Field(description="Current environment")
    uptime_seconds: float = Field(description="Application uptime in seconds")
    components: dict[str, ComponentHealth] = Field(
        description="Status of all components"
    )
    configuration: dict[str, Any] = Field(
        description="Non-sensitive configuration values"
    )
    timestamp: str = Field(description="ISO 8601 timestamp")


# =============================================================================
# Router
# =============================================================================

router = APIRouter()

# Store startup time for uptime calculation
_startup_time: datetime | None = None


def get_startup_time() -> datetime:
    """Get or initialize the startup time."""
    global _startup_time
    if _startup_time is None:
        _startup_time = datetime.now(timezone.utc)
    return _startup_time


# =============================================================================
# Health Check Functions
# =============================================================================


async def check_redis_health(settings: Settings) -> ComponentHealth:
    """
    Check Redis connection health.

    Args:
        settings: Application settings.

    Returns:
        ComponentHealth: Redis health status.
    """
    import time

    try:
        import redis.asyncio as redis

        start_time = time.monotonic()

        # Connect to Redis and ping
        client = redis.from_url(settings.REDIS_URL)
        try:
            await client.ping()
            latency = (time.monotonic() - start_time) * 1000

            # Get Redis info for additional details
            info = await client.info("server")

            return ComponentHealth(
                status=ComponentStatus.UP,
                latency_ms=round(latency, 2),
                message="Connected to Redis",
                details={
                    "redis_version": info.get("redis_version"),
                    "connected_clients": info.get("connected_clients"),
                },
            )
        finally:
            await client.aclose()

    except ImportError:
        return ComponentHealth(
            status=ComponentStatus.UNKNOWN,
            message="Redis client not installed",
        )
    except Exception as e:
        logger.warning("redis_health_check_failed", error=str(e))
        return ComponentHealth(
            status=ComponentStatus.DOWN,
            message=f"Redis connection failed: {str(e)}",
        )


async def check_directus_health(settings: Settings) -> ComponentHealth:
    """
    Check Directus API connection health.

    Args:
        settings: Application settings.

    Returns:
        ComponentHealth: Directus health status.
    """
    import time

    try:
        import httpx

        start_time = time.monotonic()

        # Check Directus health endpoint
        async with httpx.AsyncClient(timeout=5.0) as client:
            url = f"{settings.DIRECTUS_URL}/server/health"
            response = await client.get(url)
            latency = (time.monotonic() - start_time) * 1000

            if response.status_code == 200:
                data = response.json()
                return ComponentHealth(
                    status=ComponentStatus.UP,
                    latency_ms=round(latency, 2),
                    message="Connected to Directus",
                    details={
                        "directus_status": data.get("status"),
                    },
                )
            else:
                return ComponentHealth(
                    status=ComponentStatus.DEGRADED,
                    latency_ms=round(latency, 2),
                    message=f"Directus returned status {response.status_code}",
                )

    except ImportError:
        return ComponentHealth(
            status=ComponentStatus.UNKNOWN,
            message="HTTP client not installed",
        )
    except Exception as e:
        logger.warning("directus_health_check_failed", error=str(e))
        return ComponentHealth(
            status=ComponentStatus.DOWN,
            message=f"Directus connection failed: {str(e)}",
        )


async def check_celery_health(settings: Settings) -> ComponentHealth:
    """
    Check Celery worker health by pinging via Redis.

    Args:
        settings: Application settings.

    Returns:
        ComponentHealth: Celery health status.
    """
    try:
        import redis.asyncio as redis

        # Check for worker heartbeats in Redis
        client = redis.from_url(settings.CELERY_BROKER_URL)
        try:
            # Celery workers store heartbeats in Redis
            # This is a basic check - in production, you might want to
            # use Celery's inspect() functionality
            keys = await client.keys("celery-task-meta-*")

            return ComponentHealth(
                status=ComponentStatus.UP,
                message="Celery broker accessible",
                details={
                    "pending_results": len(keys) if keys else 0,
                },
            )
        finally:
            await client.aclose()

    except ImportError:
        return ComponentHealth(
            status=ComponentStatus.UNKNOWN,
            message="Redis client not installed",
        )
    except Exception as e:
        logger.warning("celery_health_check_failed", error=str(e))
        return ComponentHealth(
            status=ComponentStatus.DOWN,
            message=f"Celery broker check failed: {str(e)}",
        )


def determine_overall_status(
    components: dict[str, ComponentHealth],
) -> tuple[HealthStatus, bool]:
    """
    Determine overall health status from component statuses.

    Args:
        components: Dictionary of component health statuses.

    Returns:
        tuple: (overall_status, is_ready)
    """
    if not components:
        return HealthStatus.HEALTHY, True

    statuses = [c.status for c in components.values()]

    # If any component is down, we're unhealthy
    if ComponentStatus.DOWN in statuses:
        return HealthStatus.UNHEALTHY, False

    # If any component is degraded, we're degraded but still ready
    if ComponentStatus.DEGRADED in statuses:
        return HealthStatus.DEGRADED, True

    # All components are up
    return HealthStatus.HEALTHY, True


# =============================================================================
# Endpoints
# =============================================================================


@router.get(
    "/health",
    response_model=BasicHealthResponse,
    summary="Basic health check",
    description="Quick health check that returns immediately. Use this for load balancer checks.",
    responses={
        200: {"description": "Application is healthy"},
    },
)
async def health_check(
    settings: Settings = Depends(get_current_settings),
) -> BasicHealthResponse:
    """
    Basic health check endpoint.

    Returns immediately with the application's health status.
    This endpoint does not check dependencies and is suitable
    for high-frequency health checks from load balancers.

    Returns:
        BasicHealthResponse: Basic health status.
    """
    return BasicHealthResponse(
        status=HealthStatus.HEALTHY,
        app_name=settings.APP_NAME,
        environment=settings.ENVIRONMENT,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.get(
    "/health/live",
    response_model=LivenessResponse,
    summary="Liveness probe",
    description="Kubernetes liveness probe. Returns 200 if the application is running.",
    responses={
        200: {"description": "Application is alive"},
    },
)
async def liveness_probe() -> LivenessResponse:
    """
    Liveness probe for Kubernetes.

    Returns a simple response indicating the application process is running.
    Kubernetes uses this to determine if the container needs to be restarted.

    Returns:
        LivenessResponse: Liveness status.
    """
    return LivenessResponse(
        status="alive",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description="Kubernetes readiness probe. Checks if the application can serve traffic.",
    responses={
        200: {"description": "Application is ready to serve traffic"},
        503: {"description": "Application is not ready"},
    },
)
async def readiness_probe(
    settings: Settings = Depends(get_current_settings),
) -> JSONResponse:
    """
    Readiness probe for Kubernetes.

    Checks all critical dependencies (database, Redis, etc.) and returns
    503 if any are unavailable. Kubernetes uses this to determine if the
    pod should receive traffic.

    Args:
        settings: Application settings.

    Returns:
        JSONResponse: Readiness status with component details.
    """
    # Check all components
    components: dict[str, ComponentHealth] = {}

    # Check Redis
    components["redis"] = await check_redis_health(settings)

    # Check Directus (database)
    components["directus"] = await check_directus_health(settings)

    # Check Celery broker
    components["celery"] = await check_celery_health(settings)

    # Determine overall status
    overall_status, is_ready = determine_overall_status(components)

    response = ReadinessResponse(
        status=overall_status,
        ready=is_ready,
        components=components,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # Return 503 if not ready
    status_code = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(),
    )


@router.get(
    "/health/detailed",
    response_model=DetailedHealthResponse,
    summary="Detailed health check",
    description="Comprehensive health check with all diagnostics. Use for troubleshooting.",
    responses={
        200: {"description": "Detailed health information"},
    },
)
async def detailed_health(
    settings: Settings = Depends(get_current_settings),
) -> DetailedHealthResponse:
    """
    Detailed health check for operational monitoring.

    Returns comprehensive health information including:
    - All component statuses with latencies
    - Application uptime
    - Configuration summary (non-sensitive values only)

    Args:
        settings: Application settings.

    Returns:
        DetailedHealthResponse: Detailed health status.
    """
    startup_time = get_startup_time()
    uptime = (datetime.now(timezone.utc) - startup_time).total_seconds()

    # Check all components
    components: dict[str, ComponentHealth] = {}
    components["redis"] = await check_redis_health(settings)
    components["directus"] = await check_directus_health(settings)
    components["celery"] = await check_celery_health(settings)

    # Determine overall status
    overall_status, _ = determine_overall_status(components)

    # Non-sensitive configuration values
    configuration = {
        "debug": settings.DEBUG,
        "log_level": settings.LOG_LEVEL,
        "log_format": settings.LOG_FORMAT,
        "rate_limit_per_minute": settings.RATE_LIMIT_PER_MINUTE,
        "directus_configured": bool(settings.DIRECTUS_URL),
        "redis_configured": bool(settings.REDIS_URL),
        "celery_configured": bool(settings.CELERY_BROKER_URL),
        "anthropic_configured": settings.has_anthropic,
        "openai_configured": settings.has_openai,
        "proxy_configured": settings.has_proxy,
        "captcha_configured": settings.has_captcha,
        "telegram_configured": settings.has_telegram,
    }

    return DetailedHealthResponse(
        status=overall_status,
        app_name=settings.APP_NAME,
        version="0.1.0",
        environment=settings.ENVIRONMENT,
        uptime_seconds=round(uptime, 2),
        components=components,
        configuration=configuration,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "router",
    "HealthStatus",
    "ComponentStatus",
    "BasicHealthResponse",
    "LivenessResponse",
    "ReadinessResponse",
    "DetailedHealthResponse",
    "ComponentHealth",
]
