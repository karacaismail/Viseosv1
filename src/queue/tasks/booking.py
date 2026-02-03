"""
Booking Tasks for Celery Queue.

This module contains the core booking tasks that process appointment reservations
through the VISE OS platform. Tasks handle the full booking lifecycle including:
- Slot searching
- Form filling
- Payment processing
- Verification handling

Features:
- Priority-based queue routing
- Automatic retry with exponential backoff
- State machine integration
- Dead letter queue for failed tasks
- Site-specific task routing

Usage:
    from src.queue.tasks.booking import process_booking

    # Submit a booking task
    task = process_booking.delay(
        booking_id="booking-123",
        site="vfs",
        priority=8,
    )

    # Check result
    result = task.get(timeout=600)
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

from celery import Task, shared_task
from celery.exceptions import SoftTimeLimitExceeded

from src.core.booking.state_machine import (
    BookingContext,
    BookingState,
    BookingStateMachine,
    FailureReason,
    RetryManager,
)
from src.core.exceptions import (
    AccountBannedError,
    NoSlotsFoundError,
    SlotNotAvailableError,
    ViseOSError,
)
from src.queue.celery_app import celery_app


class BookingTask(Task):
    """
    Base booking task with error handling and retry logic.

    Provides:
    - Automatic retry with exponential backoff
    - Failure handling with dead letter queue routing
    - Retry callbacks for logging and state updates

    Attributes:
        autoretry_for: Exception types that trigger automatic retry.
        retry_backoff: Enable exponential backoff for retries.
        retry_backoff_max: Maximum backoff time in seconds.
        retry_jitter: Add randomness to backoff to prevent thundering herd.
        max_retries: Maximum number of retry attempts.
    """

    autoretry_for = (Exception,)
    retry_backoff = True
    retry_backoff_max = 600  # 10 minutes max
    retry_jitter = True
    max_retries = 3

    def on_failure(
        self,
        exc: Exception,
        task_id: str,
        args: tuple,
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        """
        Handle task failure by routing to dead letter queue.

        Args:
            exc: The exception that caused the failure.
            task_id: Celery task ID.
            args: Positional arguments passed to the task.
            kwargs: Keyword arguments passed to the task.
            einfo: Exception info object.
        """
        booking_id = kwargs.get("booking_id") or (args[0] if args else None)

        if booking_id:
            # Route to dead letter queue for later processing
            self.app.send_task(
                "src.queue.tasks.booking.handle_dead_letter",
                args=[booking_id, str(exc)],
                queue="dead_letter",
            )

    def on_retry(
        self,
        exc: Exception,
        task_id: str,
        args: tuple,
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        """
        Handle task retry.

        Args:
            exc: The exception that triggered the retry.
            task_id: Celery task ID.
            args: Positional arguments passed to the task.
            kwargs: Keyword arguments passed to the task.
            einfo: Exception info object.
        """
        booking_id = kwargs.get("booking_id") or (args[0] if args else None)
        # Retry logging is handled by structlog in production


@shared_task(
    bind=True,
    base=BookingTask,
    name="src.queue.tasks.booking.process_booking",
    queue="normal",
)
def process_booking(
    self: BookingTask,
    booking_id: str,
    site: str,
    priority: int = 5,
) -> dict[str, Any]:
    """
    Main booking task that processes a booking request.

    This task:
    1. Loads the booking context from persistence
    2. Transitions to PROCESSING state
    3. Executes the site-specific adapter
    4. Updates final state based on result

    Args:
        booking_id: Unique booking identifier.
        site: Target site code (vfs, idata, bls, kkosmos).
        priority: Task priority (1-10, higher = more urgent).

    Returns:
        Dictionary with success status and booking details.

    Raises:
        ViseOSError: For platform-specific errors.
        SoftTimeLimitExceeded: When task exceeds soft time limit.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        result = loop.run_until_complete(
            _async_process_booking(self, booking_id, site)
        )
        return result
    except SoftTimeLimitExceeded:
        # Handle soft timeout gracefully
        loop.run_until_complete(_handle_timeout(booking_id))
        raise
    finally:
        loop.close()


async def _async_process_booking(
    task: BookingTask,
    booking_id: str,
    site: str,
) -> dict[str, Any]:
    """
    Async booking processing implementation.

    Args:
        task: The Celery task instance.
        booking_id: Unique booking identifier.
        site: Target site code.

    Returns:
        Dictionary with booking result.
    """
    from src.core.booking.repository import BookingRepository
    from src.integrations.directus import get_directus_client

    # Initialize services
    state_machine = BookingStateMachine()
    retry_manager = RetryManager()

    # Load booking context
    async with get_directus_client() as client:
        repo = BookingRepository(client)
        booking_data = await repo.get_by_id(booking_id)

        if not booking_data:
            return {
                "success": False,
                "error": "Booking not found",
                "booking_id": booking_id,
            }

        # Create context from booking data
        context = BookingContext(
            booking_id=booking_id,
            agency_id=booking_data.get("agency_id", ""),
            applicant_id=booking_data.get("applicant_id", ""),
            state=BookingState(booking_data.get("status", "pending")),
        )

        try:
            # Transition to PROCESSING
            context = await state_machine.transition(
                context,
                BookingState.PROCESSING,
                trigger="task_started",
            )

            # Execute booking (adapter integration point)
            result = await _execute_booking(context, site)

            if result["success"]:
                # Transition to COMPLETED
                context.confirmation_number = result.get("confirmation_number")
                context.appointment_date = result.get("appointment_date")
                context.appointment_time = result.get("appointment_time")

                context = await state_machine.transition(
                    context,
                    BookingState.COMPLETED,
                    trigger="booking_success",
                )
            else:
                # Check if should retry
                context.last_error = result.get("error", "Unknown error")
                context.failure_reason = _map_error_to_failure_reason(result.get("error_type"))

                should_retry, delay = retry_manager.should_retry(context)

                if should_retry:
                    # Requeue for retry
                    context = await state_machine.transition(
                        context,
                        BookingState.QUEUED,
                        trigger="retry_scheduled",
                    )
                    # Schedule retry
                    process_booking.apply_async(
                        args=[booking_id, site],
                        countdown=delay,
                        queue="retry",
                    )
                else:
                    # Move to FAILED
                    context.failure_reason = FailureReason.MAX_RETRIES_EXCEEDED
                    context = await state_machine.transition(
                        context,
                        BookingState.FAILED,
                        trigger="booking_failed",
                    )

            # Persist updated context
            await repo.update(
                booking_id,
                {
                    "status": context.state.value,
                    "confirmation_number": context.confirmation_number,
                    "appointment_date": context.appointment_date,
                    "appointment_time": context.appointment_time,
                    "last_error": context.last_error,
                    "attempt_count": context.attempt_count,
                },
            )

            return {
                "success": result["success"],
                "booking_id": booking_id,
                "state": context.state.value,
                **result,
            }

        except AccountBannedError as e:
            context.last_error = str(e)
            context.failure_reason = FailureReason.ACCOUNT_BANNED
            await state_machine.transition(
                context,
                BookingState.FAILED,
                trigger="account_banned",
            )
            raise

        except NoSlotsFoundError:
            context.failure_reason = FailureReason.NO_SLOT_AVAILABLE
            await state_machine.transition(
                context,
                BookingState.QUEUED,
                trigger="no_slots_found",
            )
            # Reschedule for later
            process_booking.apply_async(
                args=[booking_id, site],
                countdown=300,  # 5 minutes
                queue="scheduled",
            )
            return {
                "success": False,
                "booking_id": booking_id,
                "error": "No slots available, scheduled for retry",
            }

        except SlotNotAvailableError:
            context.failure_reason = FailureReason.SLOT_TAKEN
            # Immediately retry with new slot search
            await state_machine.transition(
                context,
                BookingState.PROCESSING,
                trigger="slot_taken_retry",
            )
            raise task.retry(countdown=5, max_retries=5)


async def _execute_booking(context: BookingContext, site: str) -> dict[str, Any]:
    """
    Execute the actual booking via site adapter.

    This is a placeholder for the actual adapter integration.
    In production, this would:
    1. Acquire a bot account from the pool
    2. Create a stealth browser session
    3. Navigate and fill forms
    4. Handle payment and verification

    Args:
        context: The booking context.
        site: Target site code.

    Returns:
        Dictionary with booking result.
    """
    # TODO: Integrate with site adapters (008-011)
    # This is a stub for testing task infrastructure

    return {
        "success": True,
        "confirmation_number": f"CONF-{uuid.uuid4().hex[:8].upper()}",
        "appointment_date": datetime.utcnow().strftime("%Y-%m-%d"),
        "appointment_time": "10:00",
    }


async def _handle_timeout(booking_id: str) -> None:
    """
    Handle task timeout by updating booking state.

    Args:
        booking_id: The booking that timed out.
    """
    from src.core.booking.repository import BookingRepository
    from src.integrations.directus import get_directus_client

    async with get_directus_client() as client:
        repo = BookingRepository(client)
        await repo.update(
            booking_id,
            {
                "status": BookingState.FAILED.value,
                "last_error": "Task execution timed out",
            },
        )


def _map_error_to_failure_reason(error_type: str | None) -> FailureReason:
    """
    Map error type string to FailureReason enum.

    Args:
        error_type: String error type from adapter.

    Returns:
        Corresponding FailureReason enum value.
    """
    mapping = {
        "no_slots": FailureReason.NO_SLOT_AVAILABLE,
        "slot_taken": FailureReason.SLOT_TAKEN,
        "account_banned": FailureReason.ACCOUNT_BANNED,
        "account_locked": FailureReason.ACCOUNT_LOCKED,
        "payment_failed": FailureReason.PAYMENT_FAILED,
        "captcha_failed": FailureReason.CAPTCHA_FAILED,
        "timeout": FailureReason.TIMEOUT,
        "site_unavailable": FailureReason.SITE_UNAVAILABLE,
    }
    return mapping.get(error_type or "", FailureReason.SYSTEM_ERROR)


# =============================================================================
# Site-Specific Tasks
# =============================================================================


@shared_task(
    bind=True,
    base=BookingTask,
    name="src.queue.tasks.booking.vfs_booking",
    queue="vfs",
)
def vfs_booking(self: BookingTask, booking_id: str) -> dict[str, Any]:
    """
    VFS-specific booking task.

    Routes to the VFS worker pool for processing.

    Args:
        booking_id: Unique booking identifier.

    Returns:
        Dictionary with booking result.
    """
    return process_booking.apply(args=[booking_id, "vfs"]).get()


@shared_task(
    bind=True,
    base=BookingTask,
    name="src.queue.tasks.booking.idata_booking",
    queue="idata",
)
def idata_booking(self: BookingTask, booking_id: str) -> dict[str, Any]:
    """
    iDATA-specific booking task.

    Routes to the iDATA worker pool for processing.

    Args:
        booking_id: Unique booking identifier.

    Returns:
        Dictionary with booking result.
    """
    return process_booking.apply(args=[booking_id, "idata"]).get()


@shared_task(
    bind=True,
    base=BookingTask,
    name="src.queue.tasks.booking.bls_booking",
    queue="bls",
)
def bls_booking(self: BookingTask, booking_id: str) -> dict[str, Any]:
    """
    BLS-specific booking task.

    Routes to the BLS worker pool for processing.

    Args:
        booking_id: Unique booking identifier.

    Returns:
        Dictionary with booking result.
    """
    return process_booking.apply(args=[booking_id, "bls"]).get()


@shared_task(
    bind=True,
    base=BookingTask,
    name="src.queue.tasks.booking.kkosmos_booking",
    queue="kkosmos",
)
def kkosmos_booking(self: BookingTask, booking_id: str) -> dict[str, Any]:
    """
    KKosmos-specific booking task.

    Routes to the KKosmos worker pool for processing.

    Args:
        booking_id: Unique booking identifier.

    Returns:
        Dictionary with booking result.
    """
    return process_booking.apply(args=[booking_id, "kkosmos"]).get()


# =============================================================================
# Dead Letter Queue Handler
# =============================================================================


@shared_task(
    name="src.queue.tasks.booking.handle_dead_letter",
    queue="dead_letter",
)
def handle_dead_letter(booking_id: str, error_message: str) -> dict[str, Any]:
    """
    Handle tasks that ended up in the dead letter queue.

    Performs:
    - Final state update to FAILED
    - Notification to monitoring system
    - Credit release if applicable

    Args:
        booking_id: The failed booking ID.
        error_message: The error message that caused the failure.

    Returns:
        Dictionary with handling result.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        result = loop.run_until_complete(
            _async_handle_dead_letter(booking_id, error_message)
        )
        return result
    finally:
        loop.close()


async def _async_handle_dead_letter(
    booking_id: str,
    error_message: str,
) -> dict[str, Any]:
    """
    Async implementation of dead letter handling.

    Args:
        booking_id: The failed booking ID.
        error_message: The error message.

    Returns:
        Dictionary with handling result.
    """
    from src.core.booking.repository import BookingRepository
    from src.integrations.directus import get_directus_client

    async with get_directus_client() as client:
        repo = BookingRepository(client)

        # Update to final failed state
        await repo.update(
            booking_id,
            {
                "status": BookingState.FAILED.value,
                "last_error": f"Dead letter: {error_message}",
            },
        )

        # TODO: Send notification to monitoring system
        # TODO: Release any reserved credits

        return {
            "success": True,
            "booking_id": booking_id,
            "action": "moved_to_dead_letter",
            "error": error_message,
        }


__all__ = [
    "BookingTask",
    "bls_booking",
    "handle_dead_letter",
    "idata_booking",
    "kkosmos_booking",
    "process_booking",
    "vfs_booking",
]
