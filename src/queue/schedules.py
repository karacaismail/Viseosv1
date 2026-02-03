"""
Celery Beat Schedule Configuration.

This module defines the periodic task schedule for the VISE OS platform.
Celery Beat is the scheduler that triggers tasks at defined intervals.

Schedule Categories:
- Sync Operations: Google Sheets synchronization (every 5 minutes)
- Maintenance: Cleanup, health checks, limits reset
- Recovery: Orphan task recovery, dead letter processing
- Scaling: Worker pool auto-scaling
- Night Operations: Aggressive operations during off-peak hours

Timezone: Europe/Istanbul (UTC+3)
Night Operations: 02:00-06:00 Istanbul time

Usage:
    # Apply schedule to Celery app
    from src.queue.celery_app import celery_app
    from src.queue.schedules import CELERY_BEAT_SCHEDULE

    celery_app.conf.beat_schedule = CELERY_BEAT_SCHEDULE

    # Start Celery Beat
    celery -A src.queue.celery_app beat -l info
"""

from celery.schedules import crontab

# =============================================================================
# Celery Beat Schedule
# =============================================================================

CELERY_BEAT_SCHEDULE: dict = {
    # -------------------------------------------------------------------------
    # Sync Operations
    # -------------------------------------------------------------------------

    # Google Sheets sync - every 5 minutes
    "sync-sheets-every-5-minutes": {
        "task": "src.queue.tasks.sync.sync_google_sheets",
        "schedule": 300.0,  # 5 minutes in seconds
        "options": {
            "queue": "scheduled",
            "priority": 5,
        },
        "args": (),
        "kwargs": {},
    },

    # -------------------------------------------------------------------------
    # Data Maintenance
    # -------------------------------------------------------------------------

    # PII cleanup - every hour at :00
    "cleanup-pii-hourly": {
        "task": "src.queue.tasks.maintenance.cleanup_pii",
        "schedule": crontab(minute=0),  # Every hour at :00
        "options": {
            "queue": "scheduled",
            "priority": 4,
        },
    },

    # Daily credit reset - midnight Istanbul (21:00 UTC)
    "reset-daily-limits": {
        "task": "src.queue.tasks.maintenance.reset_daily_limits",
        "schedule": crontab(hour=21, minute=0),  # 00:00 Istanbul (UTC+3)
        "options": {
            "queue": "scheduled",
            "priority": 5,
        },
    },

    # -------------------------------------------------------------------------
    # Account Pool Health
    # -------------------------------------------------------------------------

    # Account health check - every 15 minutes
    "health-check-accounts": {
        "task": "src.queue.tasks.maintenance.health_check_accounts",
        "schedule": 900.0,  # 15 minutes
        "options": {
            "queue": "scheduled",
            "priority": 5,
        },
    },

    # Rotate expired cooldowns - every 10 minutes
    "rotate-expired-cooldowns": {
        "task": "src.queue.tasks.maintenance.rotate_expired_cooldowns",
        "schedule": 600.0,  # 10 minutes
        "options": {
            "queue": "scheduled",
            "priority": 5,
        },
    },

    # -------------------------------------------------------------------------
    # Task Recovery
    # -------------------------------------------------------------------------

    # Orphan task recovery - every 10 minutes
    "recover-orphan-tasks": {
        "task": "src.queue.tasks.maintenance.recover_orphan_tasks",
        "schedule": 600.0,  # 10 minutes
        "options": {
            "queue": "retry",
            "priority": 6,  # Higher priority for recovery
        },
    },

    # Dead letter processing - every 30 minutes
    "process-dead-letters": {
        "task": "src.queue.tasks.maintenance.process_dead_letter_queue",
        "schedule": 1800.0,  # 30 minutes
        "options": {
            "queue": "scheduled",
            "priority": 4,
        },
    },

    # -------------------------------------------------------------------------
    # Worker Pool Management
    # -------------------------------------------------------------------------

    # Pool auto-scaling - every 2 minutes
    "auto-scale-pools": {
        "task": "src.queue.tasks.maintenance.auto_scale_worker_pools",
        "schedule": 120.0,  # 2 minutes
        "options": {
            "queue": "scheduled",
            "priority": 7,  # High priority for scaling
        },
    },

    # -------------------------------------------------------------------------
    # Night Operations (02:00-06:00 Istanbul = 23:00-03:00 UTC)
    # -------------------------------------------------------------------------

    # Aggressive slot check during night hours
    # Runs every 10 minutes during night window
    "night-slot-check": {
        "task": "src.queue.tasks.maintenance.night_slot_scanning",
        "schedule": crontab(
            hour="23,0,1,2,3",  # UTC hours (02:00-06:00 Istanbul)
            minute="*/10",  # Every 10 minutes
        ),
        "options": {
            "queue": "night_ops",
            "priority": 6,
        },
    },

    # Account warming during night hours
    # Runs at 03:00 Istanbul (00:00 UTC)
    "account-warming-nightly": {
        "task": "src.queue.tasks.maintenance.account_warming",
        "schedule": crontab(
            hour=0,  # 00:00 UTC = 03:00 Istanbul
            minute=0,
        ),
        "options": {
            "queue": "night_ops",
            "priority": 5,
        },
    },

    # Proxy rotation during night hours
    # Runs at 04:00 Istanbul (01:00 UTC)
    "proxy-rotation-nightly": {
        "task": "src.queue.tasks.maintenance.proxy_rotation",
        "schedule": crontab(
            hour=1,  # 01:00 UTC = 04:00 Istanbul
            minute=0,
        ),
        "options": {
            "queue": "night_ops",
            "priority": 5,
        },
    },

    # -------------------------------------------------------------------------
    # Data Reconciliation (Weekly)
    # -------------------------------------------------------------------------

    # Weekly reconciliation - Sunday 05:00 Istanbul (02:00 UTC)
    "weekly-reconciliation": {
        "task": "src.queue.tasks.sync.reconcile_bookings",
        "schedule": crontab(
            hour=2,
            minute=0,
            day_of_week=0,  # Sunday
        ),
        "options": {
            "queue": "night_ops",
            "priority": 4,
        },
    },
}


def apply_schedule(celery_app) -> None:
    """
    Apply the beat schedule to a Celery app.

    Args:
        celery_app: Celery application instance.

    Example:
        from src.queue.celery_app import celery_app
        from src.queue.schedules import apply_schedule

        apply_schedule(celery_app)
    """
    celery_app.conf.beat_schedule = CELERY_BEAT_SCHEDULE
    celery_app.conf.beat_schedule_filename = "celerybeat-schedule"


def get_schedule_info() -> dict:
    """
    Get human-readable schedule information.

    Returns:
        Dictionary with schedule summary for each task.

    Example:
        info = get_schedule_info()
        for name, details in info.items():
            print(f"{name}: {details['description']}")
    """
    info = {}

    schedule_descriptions = {
        "sync-sheets-every-5-minutes": "Sync Google Sheets every 5 minutes",
        "cleanup-pii-hourly": "Clean up expired PII every hour at :00",
        "reset-daily-limits": "Reset daily limits at midnight Istanbul",
        "health-check-accounts": "Check account pool health every 15 minutes",
        "rotate-expired-cooldowns": "Rotate accounts out of cooldown every 10 minutes",
        "recover-orphan-tasks": "Recover orphan tasks every 10 minutes",
        "process-dead-letters": "Process dead letter queue every 30 minutes",
        "auto-scale-pools": "Auto-scale worker pools every 2 minutes",
        "night-slot-check": "Aggressive slot scanning during night hours (02:00-06:00)",
        "account-warming-nightly": "Warm up bot accounts at 03:00 Istanbul",
        "proxy-rotation-nightly": "Rotate proxy pool at 04:00 Istanbul",
        "weekly-reconciliation": "Weekly data reconciliation on Sunday 05:00 Istanbul",
    }

    for name, config in CELERY_BEAT_SCHEDULE.items():
        schedule = config.get("schedule")

        if isinstance(schedule, (int, float)):
            interval = f"every {int(schedule)} seconds"
        elif isinstance(schedule, crontab):
            interval = f"crontab: {schedule}"
        else:
            interval = str(schedule)

        info[name] = {
            "task": config.get("task"),
            "schedule": interval,
            "queue": config.get("options", {}).get("queue", "default"),
            "priority": config.get("options", {}).get("priority", 5),
            "description": schedule_descriptions.get(name, ""),
        }

    return info


__all__ = [
    "CELERY_BEAT_SCHEDULE",
    "apply_schedule",
    "get_schedule_info",
]
