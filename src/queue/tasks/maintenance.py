"""
Maintenance Tasks for Celery Queue.

This module contains system maintenance tasks that keep the VISE OS platform
healthy and operational. Responsibilities include:
- PII data cleanup (24-hour retention)
- Account pool health checks
- Orphan task recovery
- Worker pool auto-scaling
- Dead letter queue processing
- Night operations for aggressive slot scanning

Features:
- Scheduled execution via Celery Beat
- Rate-limited operations
- Graceful degradation
- Comprehensive logging

Usage:
    from src.queue.tasks.maintenance import cleanup_pii, health_check_accounts

    # Run PII cleanup
    cleanup_pii.delay()

    # Run account health check
    health_check_accounts.delay()
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from celery import shared_task

from src.queue.celery_app import celery_app


# =============================================================================
# Data Cleanup Tasks
# =============================================================================


@shared_task(
    name="src.queue.tasks.maintenance.cleanup_pii",
    queue="scheduled",
    soft_time_limit=540,
    time_limit=600,
)
def cleanup_pii() -> dict[str, Any]:
    """
    Clean up PII data that has exceeded retention period.

    VISE OS retains PII for 24 hours max after booking completion.
    This task:
    1. Finds completed/failed bookings older than 24 hours
    2. Scrubs PII fields from applicant records
    3. Logs cleanup for audit trail

    Returns:
        Dictionary with cleanup statistics.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        result = loop.run_until_complete(_async_cleanup_pii())
        return result
    finally:
        loop.close()


async def _async_cleanup_pii() -> dict[str, Any]:
    """
    Async implementation of PII cleanup.

    Returns:
        Dictionary with cleanup statistics.
    """
    from src.core.applicant.repository import ApplicantRepository
    from src.integrations.directus import get_directus_client

    cleanup_stats = {
        "started_at": datetime.utcnow().isoformat(),
        "records_processed": 0,
        "records_cleaned": 0,
        "errors": [],
    }

    async with get_directus_client() as client:
        repo = ApplicantRepository(client)

        try:
            # Use the built-in redaction method which handles expired PII
            cleaned_count = await repo.redact_expired_pii(batch_size=100)
            cleanup_stats["records_cleaned"] = cleaned_count
            cleanup_stats["records_processed"] = cleaned_count
        except Exception as e:
            cleanup_stats["errors"].append({
                "error": str(e),
            })

    cleanup_stats["completed_at"] = datetime.utcnow().isoformat()
    return cleanup_stats


@shared_task(
    name="src.queue.tasks.maintenance.reset_daily_limits",
    queue="scheduled",
)
def reset_daily_limits() -> dict[str, Any]:
    """
    Reset daily usage limits at midnight (Istanbul time).

    Resets:
    - Daily booking counts per agency
    - Account usage counters
    - Rate limit counters

    Returns:
        Dictionary with reset statistics.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        result = loop.run_until_complete(_async_reset_limits())
        return result
    finally:
        loop.close()


async def _async_reset_limits() -> dict[str, Any]:
    """
    Async implementation of daily limit reset.

    Returns:
        Dictionary with reset statistics.
    """
    from src.core.agency.repository import AgencyRepository
    from src.integrations.directus import get_directus_client

    async with get_directus_client() as client:
        repo = AgencyRepository(client)

        # Find all active agencies and reset their daily counts
        agencies = await repo.find_active()
        reset_count = 0

        for agency in agencies:
            await repo.update(
                agency["id"],
                {"daily_bookings_used": 0},
            )
            reset_count += 1

    return {
        "agencies_reset": reset_count,
        "reset_at": datetime.utcnow().isoformat(),
    }


# =============================================================================
# Account Pool Health
# =============================================================================


@shared_task(
    name="src.queue.tasks.maintenance.health_check_accounts",
    queue="scheduled",
)
def health_check_accounts() -> dict[str, Any]:
    """
    Perform health check on the account pool.

    Checks:
    - Account login validity
    - Account ban status
    - Cooldown expiration
    - Usage limits

    Returns:
        Dictionary with health check results.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        result = loop.run_until_complete(_async_health_check())
        return result
    finally:
        loop.close()


async def _async_health_check() -> dict[str, Any]:
    """
    Async implementation of account health check.

    Returns:
        Dictionary with health check results.
    """
    # TODO: Integrate with Account Pool Manager (007-ACCOUNT-POOL-MANAGER.md)

    health_stats = {
        "started_at": datetime.utcnow().isoformat(),
        "accounts_checked": 0,
        "healthy": 0,
        "banned": 0,
        "cooldown": 0,
        "inactive": 0,
        "completed_at": datetime.utcnow().isoformat(),
    }

    return health_stats


@shared_task(
    name="src.queue.tasks.maintenance.rotate_expired_cooldowns",
    queue="scheduled",
)
def rotate_expired_cooldowns() -> dict[str, Any]:
    """
    Rotate accounts whose cooldown period has expired.

    Returns accounts to the active pool after:
    - Rate limit cooldown
    - Error cooldown
    - Ban recovery period

    Returns:
        Dictionary with rotation results.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        result = loop.run_until_complete(_async_rotate_cooldowns())
        return result
    finally:
        loop.close()


async def _async_rotate_cooldowns() -> dict[str, Any]:
    """
    Async implementation of cooldown rotation.

    Returns:
        Dictionary with rotation results.
    """
    # TODO: Integrate with Account Pool Manager

    return {
        "accounts_rotated": 0,
        "rotated_at": datetime.utcnow().isoformat(),
    }


# =============================================================================
# Task Recovery
# =============================================================================


@shared_task(
    name="src.queue.tasks.maintenance.recover_orphan_tasks",
    queue="retry",
)
def recover_orphan_tasks() -> dict[str, Any]:
    """
    Recover orphaned tasks from crashed workers.

    Finds bookings stuck in processing states longer than expected
    and reschedules them for processing.

    Returns:
        Dictionary with recovery statistics.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        result = loop.run_until_complete(_async_recover_orphans())
        return result
    finally:
        loop.close()


async def _async_recover_orphans() -> dict[str, Any]:
    """
    Async implementation of orphan recovery.

    Returns:
        Dictionary with recovery statistics.
    """
    from src.core.booking.repository import BookingRepository
    from src.core.booking.state_machine import BookingState
    from src.integrations.directus import get_directus_client
    from src.queue.tasks.booking import process_booking

    recovery_stats = {
        "started_at": datetime.utcnow().isoformat(),
        "orphans_found": 0,
        "recovered": 0,
        "failed_to_recover": 0,
        "errors": [],
    }

    async with get_directus_client() as client:
        repo = BookingRepository(client)

        # Find stuck bookings in PROCESSING state
        # The repository will return bookings ordered by created_at desc
        stuck_bookings = await repo.find_by_status(
            BookingState.PROCESSING.value,
            limit=50,
        )

        # Filter for those stuck longer than 5 minutes
        timeout_threshold = datetime.utcnow() - timedelta(minutes=5)
        orphaned = []
        for booking in stuck_bookings:
            updated_at = booking.get("date_updated") or booking.get("updated_at")
            if updated_at:
                if isinstance(updated_at, str):
                    try:
                        updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                        if updated_dt.replace(tzinfo=None) < timeout_threshold:
                            orphaned.append(booking)
                    except (ValueError, TypeError):
                        pass

        recovery_stats["orphans_found"] = len(orphaned)

        for booking in orphaned:
            try:
                # Reset to queued and reschedule
                await repo.update_status(
                    booking["id"],
                    BookingState.QUEUED.value,
                    error_message="Recovered from orphan state",
                )

                # Reschedule the task
                process_booking.apply_async(
                    args=[booking["id"], booking.get("target_system", "vfs")],
                    queue="retry",
                    priority=7,  # Higher priority for retries
                )

                recovery_stats["recovered"] += 1

            except Exception as e:
                recovery_stats["failed_to_recover"] += 1
                recovery_stats["errors"].append({
                    "booking_id": booking.get("id"),
                    "error": str(e),
                })

    recovery_stats["completed_at"] = datetime.utcnow().isoformat()
    return recovery_stats


@shared_task(
    name="src.queue.tasks.maintenance.process_dead_letter_queue",
    queue="scheduled",
)
def process_dead_letter_queue() -> dict[str, Any]:
    """
    Process tasks in the dead letter queue.

    Attempts to:
    1. Analyze failure patterns
    2. Identify recoverable failures
    3. Reschedule or permanently fail tasks

    Returns:
        Dictionary with processing statistics.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        result = loop.run_until_complete(_async_process_dlq())
        return result
    finally:
        loop.close()


async def _async_process_dlq() -> dict[str, Any]:
    """
    Async implementation of DLQ processing.

    Returns:
        Dictionary with processing statistics.
    """
    from src.core.booking.repository import BookingRepository
    from src.core.booking.state_machine import BookingState
    from src.integrations.directus import get_directus_client

    dlq_stats = {
        "started_at": datetime.utcnow().isoformat(),
        "processed": 0,
        "rescheduled": 0,
        "permanently_failed": 0,
        "errors": [],
    }

    async with get_directus_client() as client:
        repo = BookingRepository(client)

        # Find failed bookings that might be recoverable
        failed_bookings = await repo.find_by_status(
            BookingState.FAILED.value,
            limit=50,
        )

        # Filter for those with retries remaining
        for booking in failed_bookings:
            attempt_count = booking.get("attempt_count", 0) or 0
            if attempt_count >= 5:
                continue  # Skip those at max attempts

            dlq_stats["processed"] += 1

            # Analyze if recoverable
            last_error = booking.get("error_message", "") or booking.get("last_error", "")

            # Transient errors can be retried
            if _is_transient_error(last_error):
                await repo.update_status(
                    booking["id"],
                    BookingState.QUEUED.value,
                )
                dlq_stats["rescheduled"] += 1
            else:
                dlq_stats["permanently_failed"] += 1

            # Stop after processing 20 to avoid long running task
            if dlq_stats["processed"] >= 20:
                break

    dlq_stats["completed_at"] = datetime.utcnow().isoformat()
    return dlq_stats


def _is_transient_error(error_message: str) -> bool:
    """
    Check if an error is transient and can be retried.

    Args:
        error_message: The error message to analyze.

    Returns:
        True if the error is transient.
    """
    transient_patterns = [
        "timeout",
        "connection",
        "temporary",
        "rate limit",
        "site unavailable",
    ]

    error_lower = error_message.lower()
    return any(pattern in error_lower for pattern in transient_patterns)


# =============================================================================
# Worker Pool Management
# =============================================================================


@shared_task(
    name="src.queue.tasks.maintenance.auto_scale_worker_pools",
    queue="scheduled",
)
def auto_scale_worker_pools() -> dict[str, Any]:
    """
    Auto-scale worker pools based on queue utilization.

    Monitors:
    - Queue lengths
    - Worker utilization
    - Task throughput

    Triggers scaling actions when thresholds are crossed.

    Returns:
        Dictionary with scaling actions taken.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        result = loop.run_until_complete(_async_auto_scale())
        return result
    finally:
        loop.close()


async def _async_auto_scale() -> dict[str, Any]:
    """
    Async implementation of auto-scaling.

    Returns:
        Dictionary with scaling actions.
    """
    # TODO: Integrate with container orchestration (Docker Swarm / K8s)
    # This would use the WorkerPoolManager from the spec

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "pool_status": {},
        "scaling_actions": [],
    }


# =============================================================================
# Night Operations
# =============================================================================


@shared_task(
    name="src.queue.tasks.maintenance.night_slot_scanning",
    queue="night_ops",
)
def night_slot_scanning() -> dict[str, Any]:
    """
    Aggressive slot scanning during off-peak hours (02:00-06:00 Istanbul).

    Night operations:
    - Higher request rate (reduced risk of detection)
    - More accounts in rotation
    - Broader slot search criteria

    Returns:
        Dictionary with scanning results.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        result = loop.run_until_complete(_async_night_scan())
        return result
    finally:
        loop.close()


async def _async_night_scan() -> dict[str, Any]:
    """
    Async implementation of night scanning.

    Returns:
        Dictionary with scanning results.
    """
    from src.core.booking.repository import BookingRepository
    from src.core.booking.state_machine import BookingState
    from src.integrations.directus import get_directus_client
    from src.queue.tasks.booking import process_booking

    scan_stats = {
        "started_at": datetime.utcnow().isoformat(),
        "queued_bookings": 0,
        "slots_found": 0,
        "bookings_submitted": 0,
    }

    async with get_directus_client() as client:
        repo = BookingRepository(client)

        # Find queued bookings waiting for slots
        waiting_bookings = await repo.find_by_status(
            BookingState.QUEUED.value,
            limit=50,
        )

        scan_stats["queued_bookings"] = len(waiting_bookings)

        # Submit with higher priority during night ops
        for booking in waiting_bookings:
            process_booking.apply_async(
                args=[booking["id"], booking.get("target_system", "vfs")],
                queue="night_ops",
                priority=6,  # Elevated priority
            )
            scan_stats["bookings_submitted"] += 1

    scan_stats["completed_at"] = datetime.utcnow().isoformat()
    return scan_stats


@shared_task(
    name="src.queue.tasks.maintenance.account_warming",
    queue="night_ops",
)
def account_warming() -> dict[str, Any]:
    """
    Warm up bot accounts during night hours.

    Account warming:
    - Logs into accounts to refresh sessions
    - Performs benign browsing actions
    - Updates cookie/fingerprint data

    Returns:
        Dictionary with warming results.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        result = loop.run_until_complete(_async_account_warming())
        return result
    finally:
        loop.close()


async def _async_account_warming() -> dict[str, Any]:
    """
    Async implementation of account warming.

    Returns:
        Dictionary with warming results.
    """
    # TODO: Integrate with Account Pool Manager (007-ACCOUNT-POOL-MANAGER.md)

    return {
        "started_at": datetime.utcnow().isoformat(),
        "accounts_warmed": 0,
        "sessions_refreshed": 0,
        "completed_at": datetime.utcnow().isoformat(),
    }


@shared_task(
    name="src.queue.tasks.maintenance.proxy_rotation",
    queue="night_ops",
)
def proxy_rotation() -> dict[str, Any]:
    """
    Rotate and validate proxy pool during night hours.

    Proxy rotation:
    - Tests proxy connectivity
    - Measures latency
    - Identifies blocked proxies
    - Requests new proxies from providers

    Returns:
        Dictionary with rotation results.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        result = loop.run_until_complete(_async_proxy_rotation())
        return result
    finally:
        loop.close()


async def _async_proxy_rotation() -> dict[str, Any]:
    """
    Async implementation of proxy rotation.

    Returns:
        Dictionary with rotation results.
    """
    # TODO: Integrate with Proxy Manager (005-PROXY-MANAGER.md)

    return {
        "started_at": datetime.utcnow().isoformat(),
        "proxies_tested": 0,
        "proxies_healthy": 0,
        "proxies_removed": 0,
        "proxies_added": 0,
        "completed_at": datetime.utcnow().isoformat(),
    }


__all__ = [
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
