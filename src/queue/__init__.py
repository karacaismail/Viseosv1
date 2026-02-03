"""
VISE OS Queue Module.

Celery-based task queue for asynchronous booking processing.
Manages task scheduling, priority queues, and retry logic.

Components:
- celery_app: Celery application configuration
- tasks: Task definitions (booking, sync, maintenance)
- schedules: Celery Beat scheduled tasks

Usage:
    from src.queue import celery_app, process_booking

    # Submit a booking task
    task = process_booking.delay(
        booking_id="booking-123",
        site="vfs",
    )

Worker Launch:
    celery -A src.queue.celery_app worker -l info

Beat Scheduler Launch:
    celery -A src.queue.celery_app beat -l info
"""

from src.queue.celery_app import (
    QUEUE_CONFIG,
    SITE_QUEUES,
    celery_app,
    create_celery_app,
    get_queue_for_priority,
    get_queue_for_site,
)
from src.queue.schedules import (
    CELERY_BEAT_SCHEDULE,
    apply_schedule,
    get_schedule_info,
)

# Import all tasks to register them with Celery
from src.queue.tasks import (
    # Booking tasks
    BookingTask,
    bls_booking,
    handle_dead_letter,
    idata_booking,
    kkosmos_booking,
    process_booking,
    vfs_booking,
    # Sync tasks
    batch_sync_statuses,
    import_new_bookings,
    reconcile_bookings,
    sync_booking_status,
    sync_google_sheets,
    # Maintenance tasks
    account_warming,
    auto_scale_worker_pools,
    cleanup_pii,
    health_check_accounts,
    night_slot_scanning,
    process_dead_letter_queue,
    proxy_rotation,
    recover_orphan_tasks,
    reset_daily_limits,
    rotate_expired_cooldowns,
)

__all__ = [
    # Celery app
    "QUEUE_CONFIG",
    "SITE_QUEUES",
    "celery_app",
    "create_celery_app",
    "get_queue_for_priority",
    "get_queue_for_site",
    # Schedules
    "CELERY_BEAT_SCHEDULE",
    "apply_schedule",
    "get_schedule_info",
    # Booking tasks
    "BookingTask",
    "bls_booking",
    "handle_dead_letter",
    "idata_booking",
    "kkosmos_booking",
    "process_booking",
    "vfs_booking",
    # Sync tasks
    "batch_sync_statuses",
    "import_new_bookings",
    "reconcile_bookings",
    "sync_booking_status",
    "sync_google_sheets",
    # Maintenance tasks
    "account_warming",
    "auto_scale_worker_pools",
    "cleanup_pii",
    "health_check_accounts",
    "night_slot_scanning",
    "process_dead_letter_queue",
    "proxy_rotation",
    "recover_orphan_tasks",
    "reset_daily_limits",
    "rotate_expired_cooldowns",
]
