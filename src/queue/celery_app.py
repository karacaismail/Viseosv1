"""
Celery Application Configuration.

This module provides the Celery application instance configured for VISE OS.
Handles queue definitions, task routing, worker settings, and broker configuration.

Features:
- Priority-based queue routing (high, normal, retry, scheduled)
- Site-specific queues (vfs, idata, bls, kkosmos)
- Dead letter queue for failed tasks
- Fair worker distribution with prefetch multiplier
- Task acknowledgment after completion
- Memory leak prevention with max_tasks_per_child

Usage:
    from src.queue.celery_app import celery_app

    @celery_app.task(queue="high_priority")
    def process_premium_booking(booking_id: str) -> dict:
        ...

Worker Launch:
    celery -A src.queue.celery_app worker -l info -Q high_priority,normal
"""

from typing import Any

from celery import Celery
from kombu import Exchange, Queue


# Queue configurations with routing keys and arguments
QUEUE_CONFIG: dict[str, dict[str, Any]] = {
    "high_priority": {
        "exchange": "booking",
        "routing_key": "booking.high",
        "queue_arguments": {
            "x-max-priority": 10,
        },
    },
    "normal": {
        "exchange": "booking",
        "routing_key": "booking.normal",
        "queue_arguments": {
            "x-max-priority": 5,
        },
    },
    "retry": {
        "exchange": "booking",
        "routing_key": "booking.retry",
        "queue_arguments": {
            "x-message-ttl": 3600000,  # 1 hour max TTL
        },
    },
    "scheduled": {
        "exchange": "booking",
        "routing_key": "booking.scheduled",
        "queue_arguments": {},
    },
    "night_ops": {
        "exchange": "booking",
        "routing_key": "booking.night",
        "queue_arguments": {},
    },
    "dead_letter": {
        "exchange": "booking",
        "routing_key": "booking.dead",
        "queue_arguments": {},
    },
}

# Site-specific queue configurations
SITE_QUEUES: dict[str, dict[str, str]] = {
    "vfs": {
        "exchange": "sites",
        "routing_key": "sites.vfs",
    },
    "idata": {
        "exchange": "sites",
        "routing_key": "sites.idata",
    },
    "bls": {
        "exchange": "sites",
        "routing_key": "sites.bls",
    },
    "kkosmos": {
        "exchange": "sites",
        "routing_key": "sites.kkosmos",
    },
}


def _get_settings() -> Any:
    """
    Lazy import of settings to avoid circular imports.

    Returns:
        Settings instance from src.api.config.
    """
    from src.api.config import get_settings
    return get_settings()


def create_celery_app(
    broker_url: str | None = None,
    result_backend: str | None = None,
) -> Celery:
    """
    Create and configure the Celery application.

    Creates a Celery instance with:
    - Priority queue support
    - Site-specific queue routing
    - Fair worker distribution
    - Late task acknowledgment
    - Memory leak prevention

    Args:
        broker_url: Redis broker URL. Defaults to settings.CELERY_BROKER_URL.
        result_backend: Redis result backend URL. Defaults to settings.CELERY_RESULT_BACKEND.

    Returns:
        Configured Celery application instance.

    Example:
        app = create_celery_app()

        @app.task(queue="high_priority", priority=9)
        def urgent_booking(booking_id: str) -> dict:
            ...
    """
    settings = _get_settings()

    # Use provided URLs or fall back to settings
    broker = broker_url or settings.CELERY_BROKER_URL
    backend = result_backend or settings.CELERY_RESULT_BACKEND

    app = Celery(
        "vise_os",
        broker=broker,
        backend=backend,
    )

    # Define exchanges
    booking_exchange = Exchange("booking", type="direct")
    sites_exchange = Exchange("sites", type="direct")

    # Build queue tuple from configs
    task_queues = []

    # Add booking queues
    for queue_name, config in QUEUE_CONFIG.items():
        task_queues.append(
            Queue(
                queue_name,
                booking_exchange,
                routing_key=config["routing_key"],
                queue_arguments=config.get("queue_arguments", {}),
            )
        )

    # Add site-specific queues
    for site_name, config in SITE_QUEUES.items():
        task_queues.append(
            Queue(
                site_name,
                sites_exchange,
                routing_key=config["routing_key"],
            )
        )

    # Apply Celery configuration
    app.conf.update(
        # -------------------------------------------------------------------------
        # Task Serialization
        # -------------------------------------------------------------------------
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",

        # -------------------------------------------------------------------------
        # Timezone
        # -------------------------------------------------------------------------
        timezone="Europe/Istanbul",
        enable_utc=True,

        # -------------------------------------------------------------------------
        # Worker Settings
        # -------------------------------------------------------------------------
        worker_prefetch_multiplier=1,  # Fair distribution across workers
        worker_concurrency=settings.CELERY_WORKER_CONCURRENCY,
        worker_max_tasks_per_child=100,  # Restart worker after 100 tasks (memory leak prevention)

        # -------------------------------------------------------------------------
        # Task Execution
        # -------------------------------------------------------------------------
        task_acks_late=True,  # Acknowledge after task completion (reliability)
        task_reject_on_worker_lost=True,  # Requeue if worker dies
        task_time_limit=settings.CELERY_TASK_TIME_LIMIT,  # Hard limit
        task_soft_time_limit=settings.CELERY_TASK_TIME_LIMIT - 60,  # Soft limit (1 min before hard)

        # -------------------------------------------------------------------------
        # Result Backend
        # -------------------------------------------------------------------------
        result_expires=3600,  # Results expire after 1 hour

        # -------------------------------------------------------------------------
        # Queue Configuration
        # -------------------------------------------------------------------------
        task_queues=tuple(task_queues),

        # Default queue settings
        task_default_queue="normal",
        task_default_exchange="booking",
        task_default_routing_key="booking.normal",

        # Priority support
        task_queue_max_priority=10,
        task_default_priority=5,

        # -------------------------------------------------------------------------
        # Task Routing
        # -------------------------------------------------------------------------
        task_routes={
            # High priority tasks
            "src.queue.tasks.process_premium_booking": {"queue": "high_priority"},
            "src.queue.tasks.urgent_slot_grab": {"queue": "high_priority"},

            # Site-specific tasks
            "src.queue.tasks.vfs_*": {"queue": "vfs"},
            "src.queue.tasks.idata_*": {"queue": "idata"},
            "src.queue.tasks.bls_*": {"queue": "bls"},
            "src.queue.tasks.kkosmos_*": {"queue": "kkosmos"},

            # Scheduled tasks
            "src.queue.tasks.check_slots": {"queue": "scheduled"},
            "src.queue.tasks.sync_sheets": {"queue": "scheduled"},

            # Night operations
            "src.queue.tasks.account_warming": {"queue": "night_ops"},
            "src.queue.tasks.proxy_rotation": {"queue": "night_ops"},
        },

        # -------------------------------------------------------------------------
        # Retry Policy (handled per-task, but set defaults)
        # -------------------------------------------------------------------------
        task_annotations={
            "*": {
                "rate_limit": "10/s",  # Default rate limit
            },
        },
    )

    return app


# Create the default Celery application instance
# This is imported by workers and task modules
celery_app = create_celery_app()


# =============================================================================
# Worker Lifecycle: Bot Service Container
# =============================================================================

_worker_container = None


from celery.signals import worker_init, worker_shutdown


@worker_init.connect
def _init_worker_container(**kwargs):
    """Initialize BotServiceContainer when Celery worker starts."""
    global _worker_container
    import asyncio
    from src.bot.container import BotServiceContainer

    loop = asyncio.new_event_loop()
    _worker_container = BotServiceContainer.get_instance()
    try:
        loop.run_until_complete(_worker_container.initialize())
    except Exception:
        import structlog
        structlog.get_logger().warning("worker_container_init_failed", exc_info=True)
    finally:
        loop.close()


@worker_shutdown.connect
def _shutdown_worker_container(**kwargs):
    """Shutdown BotServiceContainer when Celery worker stops."""
    global _worker_container
    if _worker_container and _worker_container.is_initialized:
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_worker_container.shutdown())
        finally:
            loop.close()
    _worker_container = None


def get_worker_container():
    """
    Get the worker's BotServiceContainer instance.

    Returns:
        BotServiceContainer or None if not initialized.
    """
    return _worker_container


# Task routing helper functions
def get_queue_for_priority(priority: str) -> str:
    """
    Get the appropriate queue name for a priority level.

    Args:
        priority: Priority level ("high", "normal", "low").

    Returns:
        Queue name.

    Example:
        queue = get_queue_for_priority("high")  # Returns "high_priority"
    """
    priority_map = {
        "high": "high_priority",
        "normal": "normal",
        "low": "normal",
        "retry": "retry",
        "scheduled": "scheduled",
        "night": "night_ops",
    }
    return priority_map.get(priority, "normal")


def get_queue_for_site(site_code: str) -> str:
    """
    Get the appropriate queue name for a visa site.

    Args:
        site_code: Site identifier (e.g., "vfs", "idata", "bls").

    Returns:
        Queue name.

    Example:
        queue = get_queue_for_site("vfs")  # Returns "vfs"
    """
    site_code_lower = site_code.lower()
    if site_code_lower in SITE_QUEUES:
        return site_code_lower
    return "normal"


__all__ = [
    "QUEUE_CONFIG",
    "SITE_QUEUES",
    "celery_app",
    "create_celery_app",
    "get_queue_for_priority",
    "get_queue_for_site",
]
