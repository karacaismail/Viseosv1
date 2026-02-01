"""
VISE OS Queue Module.

Celery-based task queue for asynchronous booking processing.
Manages task scheduling, priority queues, and retry logic.

Components:
- celery_app: Celery application configuration
- tasks: Task definitions (booking, sync, maintenance)
- schedules: Celery Beat scheduled tasks

Usage:
    from src.queue import celery_app

    @celery_app.task(queue="high_priority")
    def process_booking(booking_id: str) -> dict:
        ...

Worker Launch:
    celery -A src.queue.celery_app worker -l info
"""

from src.queue.celery_app import (
    QUEUE_CONFIG,
    SITE_QUEUES,
    celery_app,
    create_celery_app,
    get_queue_for_priority,
    get_queue_for_site,
)

__all__ = [
    "QUEUE_CONFIG",
    "SITE_QUEUES",
    "celery_app",
    "create_celery_app",
    "get_queue_for_priority",
    "get_queue_for_site",
]
