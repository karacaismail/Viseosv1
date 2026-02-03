"""
VISE OS Queue Tasks Module.

This package contains all Celery task definitions for the VISE OS platform.
Tasks are organized by category:
- booking: Core booking processing tasks
- sync: Google Sheets synchronization tasks
- maintenance: System maintenance and cleanup tasks

Usage:
    from src.queue.tasks import process_booking, sync_google_sheets

    # Submit a booking task
    task = process_booking.delay(
        booking_id="booking-123",
        site="vfs",
    )

    # Submit a sync task
    sync_google_sheets.delay(agency_id="agency-123")

Task Categories:
    Booking Tasks:
        - process_booking: Main booking processor
        - vfs_booking: VFS-specific booking
        - idata_booking: iDATA-specific booking
        - bls_booking: BLS-specific booking
        - kkosmos_booking: KKosmos-specific booking
        - handle_dead_letter: Dead letter queue handler

    Sync Tasks:
        - sync_google_sheets: Sync booking requests from sheets
        - sync_booking_status: Update sheet with booking status
        - batch_sync_statuses: Batch status updates
        - reconcile_bookings: Reconcile sheet vs Directus
        - import_new_bookings: Import from specific sheet

    Maintenance Tasks:
        - cleanup_pii: PII data cleanup (24h retention)
        - reset_daily_limits: Reset daily usage counters
        - health_check_accounts: Account pool health check
        - recover_orphan_tasks: Recover stuck tasks
        - process_dead_letter_queue: Process DLQ
        - auto_scale_worker_pools: Auto-scale workers
        - night_slot_scanning: Aggressive night scanning
        - account_warming: Warm up bot accounts
        - proxy_rotation: Rotate proxy pool
"""

from src.queue.tasks.booking import (
    BookingTask,
    bls_booking,
    handle_dead_letter,
    idata_booking,
    kkosmos_booking,
    process_booking,
    vfs_booking,
)
from src.queue.tasks.maintenance import (
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
from src.queue.tasks.sync import (
    batch_sync_statuses,
    import_new_bookings,
    reconcile_bookings,
    sync_booking_status,
    sync_google_sheets,
)

__all__ = [
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
