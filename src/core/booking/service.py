"""
Booking Service with Full Flow Integration.

This module provides the main orchestration layer for booking operations,
integrating all components of the VISE OS platform:
- State machine for flow control
- Repository for data persistence
- Site adapters for booking execution
- Queue orchestrator for async processing
- Credit management for billing
- Compensation transactions for failure recovery

Features:
- Full booking lifecycle management
- Priority-based queue routing
- Automatic retry with backoff
- Credit reservation and charging
- Compensating transactions
- State-based event handling
- Metrics collection

Usage:
    from src.core.booking.service import BookingService

    service = BookingService()

    # Create a booking
    booking = await service.create_booking(
        agency_id="agency-123",
        applicant_id="applicant-456",
        target_system="vfs",
        target_country="DE",
        visa_category="tourist",
    )

    # Submit for processing
    result = await service.submit_booking(booking["id"])

    # Check status
    status = await service.get_booking_status(booking["id"])
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable
from uuid import UUID

import structlog

from src.core.booking.repository import BookingRepository
from src.core.booking.state_machine import (
    BookingContext,
    BookingState,
    BookingStateMachine,
    CompensatingTransactionManager,
    FailureReason,
    RetryManager,
)
from src.core.exceptions import (
    BookingError,
    InsufficientCreditsError,
    InvalidStateTransitionError,
    ViseOSError,
)

if TYPE_CHECKING:
    from src.bot.adapters.base import BaseSiteAdapter, BookingResult, Slot
    from src.integrations.directus import DirectusClient

logger = structlog.get_logger(__name__)


# =============================================================================
# Priority Configuration
# =============================================================================


class BookingPriority(Enum):
    """
    Booking priority levels for queue routing.

    Higher values indicate higher priority.
    """

    CRITICAL = 10  # Premium agency, urgent appointment
    HIGH = 8  # Premium agency, normal
    ELEVATED = 6  # Standard agency, close date
    NORMAL = 5  # Standard agency
    LOW = 3  # Batch booking, flexible date
    BACKGROUND = 1  # Night operations, lowest priority


@dataclass
class PriorityFactors:
    """
    Factors used to calculate booking priority.

    Attributes:
        is_premium_agency: Whether agency has premium status.
        date_urgency_days: Days until preferred appointment date.
        retry_count: Number of previous retry attempts.
        slot_rarity: Slot rarity score (0-1, higher = rarer).
        payment_value: Payment value for priority boost.
    """

    is_premium_agency: bool = False
    date_urgency_days: int = 30
    retry_count: int = 0
    slot_rarity: float = 0.0
    payment_value: float = 0.0


# =============================================================================
# Service Events
# =============================================================================


class BookingEventType(str, Enum):
    """Types of booking events for callbacks."""

    CREATED = "created"
    QUEUED = "queued"
    PROCESSING_STARTED = "processing_started"
    SLOT_FOUND = "slot_found"
    BOOKING_STARTED = "booking_started"
    PAYMENT_STARTED = "payment_started"
    VERIFICATION_STARTED = "verification_started"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


@dataclass
class BookingEvent:
    """
    Event emitted during booking lifecycle.

    Attributes:
        event_type: Type of the event.
        booking_id: ID of the affected booking.
        timestamp: When the event occurred.
        data: Additional event data.
    """

    event_type: BookingEventType
    booking_id: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    data: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Booking Service
# =============================================================================


class BookingService:
    """
    Main booking service with full flow integration.

    Orchestrates the complete booking lifecycle from creation to completion,
    integrating state machine, repository, adapters, queue, and credits.

    Attributes:
        state_machine: Booking state machine instance.
        retry_manager: Retry policy manager.
        compensation_manager: Compensating transaction manager.
        event_handlers: Registered event callbacks.

    Usage:
        service = BookingService()

        # Create and submit booking
        booking = await service.create_booking(
            agency_id="agency-123",
            applicant_id="applicant-456",
            target_system="vfs",
            target_country="DE",
        )

        result = await service.submit_booking(booking["id"])

        # Register event handler
        service.on_event(BookingEventType.COMPLETED, handle_completion)
    """

    # Default credit cost per booking
    DEFAULT_CREDIT_COST = 1.0

    # Premium credit multiplier
    PREMIUM_CREDIT_MULTIPLIER = 1.5

    def __init__(
        self,
        repository: BookingRepository | None = None,
        state_machine: BookingStateMachine | None = None,
        retry_manager: RetryManager | None = None,
        compensation_manager: CompensatingTransactionManager | None = None,
    ) -> None:
        """
        Initialize the booking service.

        Args:
            repository: Optional repository instance.
            state_machine: Optional state machine instance.
            retry_manager: Optional retry manager instance.
            compensation_manager: Optional compensation manager instance.
        """
        self._repository = repository
        self.state_machine = state_machine or BookingStateMachine()
        self.retry_manager = retry_manager or RetryManager()
        self.compensation_manager = compensation_manager or CompensatingTransactionManager()

        # Event handlers
        self._event_handlers: dict[BookingEventType, list[Callable]] = {
            event_type: [] for event_type in BookingEventType
        }

        # Register state machine handlers
        self._setup_state_handlers()

        logger.info("booking_service_initialized")

    @property
    def repository(self) -> BookingRepository:
        """Get the booking repository, creating if needed."""
        if self._repository is None:
            self._repository = BookingRepository()
        return self._repository

    def _setup_state_handlers(self) -> None:
        """Register handlers for state machine events."""
        # Entry handlers for each state
        self.state_machine.register_entry_handler(
            BookingState.QUEUED,
            self._on_enter_queued,
        )
        self.state_machine.register_entry_handler(
            BookingState.PROCESSING,
            self._on_enter_processing,
        )
        self.state_machine.register_entry_handler(
            BookingState.SLOT_FOUND,
            self._on_enter_slot_found,
        )
        self.state_machine.register_entry_handler(
            BookingState.BOOKING,
            self._on_enter_booking,
        )
        self.state_machine.register_entry_handler(
            BookingState.PAYMENT,
            self._on_enter_payment,
        )
        self.state_machine.register_entry_handler(
            BookingState.VERIFYING,
            self._on_enter_verifying,
        )
        self.state_machine.register_entry_handler(
            BookingState.COMPLETED,
            self._on_enter_completed,
        )
        self.state_machine.register_entry_handler(
            BookingState.FAILED,
            self._on_enter_failed,
        )
        self.state_machine.register_entry_handler(
            BookingState.CANCELLED,
            self._on_enter_cancelled,
        )

    # -------------------------------------------------------------------------
    # State Entry Handlers
    # -------------------------------------------------------------------------

    async def _on_enter_queued(self, context: BookingContext) -> None:
        """Handle entering QUEUED state."""
        await self._emit_event(
            BookingEventType.QUEUED,
            context.booking_id,
            {"attempt_count": context.attempt_count},
        )

    async def _on_enter_processing(self, context: BookingContext) -> None:
        """Handle entering PROCESSING state."""
        await self._emit_event(
            BookingEventType.PROCESSING_STARTED,
            context.booking_id,
            {"attempt_count": context.attempt_count},
        )

    async def _on_enter_slot_found(self, context: BookingContext) -> None:
        """Handle entering SLOT_FOUND state."""
        await self._emit_event(
            BookingEventType.SLOT_FOUND,
            context.booking_id,
            {"slot_found_at": context.slot_found_at.isoformat() if context.slot_found_at else None},
        )

    async def _on_enter_booking(self, context: BookingContext) -> None:
        """Handle entering BOOKING state."""
        await self._emit_event(
            BookingEventType.BOOKING_STARTED,
            context.booking_id,
        )

    async def _on_enter_payment(self, context: BookingContext) -> None:
        """Handle entering PAYMENT state."""
        await self._emit_event(
            BookingEventType.PAYMENT_STARTED,
            context.booking_id,
        )

    async def _on_enter_verifying(self, context: BookingContext) -> None:
        """Handle entering VERIFYING state."""
        await self._emit_event(
            BookingEventType.VERIFICATION_STARTED,
            context.booking_id,
        )

    async def _on_enter_completed(self, context: BookingContext) -> None:
        """Handle entering COMPLETED state."""
        await self._emit_event(
            BookingEventType.COMPLETED,
            context.booking_id,
            {
                "confirmation_number": context.confirmation_number,
                "appointment_date": context.appointment_date,
                "appointment_time": context.appointment_time,
            },
        )

    async def _on_enter_failed(self, context: BookingContext) -> None:
        """Handle entering FAILED state."""
        await self._emit_event(
            BookingEventType.FAILED,
            context.booking_id,
            {
                "failure_reason": context.failure_reason.value if context.failure_reason else None,
                "last_error": context.last_error,
            },
        )

    async def _on_enter_cancelled(self, context: BookingContext) -> None:
        """Handle entering CANCELLED state."""
        await self._emit_event(
            BookingEventType.CANCELLED,
            context.booking_id,
        )

    # -------------------------------------------------------------------------
    # Event Handling
    # -------------------------------------------------------------------------

    def on_event(
        self,
        event_type: BookingEventType,
        handler: Callable[[BookingEvent], Any],
    ) -> None:
        """
        Register an event handler.

        Args:
            event_type: Type of event to handle.
            handler: Callback function to invoke.
        """
        self._event_handlers[event_type].append(handler)

    def off_event(
        self,
        event_type: BookingEventType,
        handler: Callable[[BookingEvent], Any],
    ) -> None:
        """
        Unregister an event handler.

        Args:
            event_type: Type of event.
            handler: Callback function to remove.
        """
        if handler in self._event_handlers[event_type]:
            self._event_handlers[event_type].remove(handler)

    async def _emit_event(
        self,
        event_type: BookingEventType,
        booking_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """
        Emit a booking event to registered handlers.

        Args:
            event_type: Type of event.
            booking_id: ID of the booking.
            data: Additional event data.
        """
        event = BookingEvent(
            event_type=event_type,
            booking_id=booking_id,
            data=data or {},
        )

        logger.debug(
            "booking_event_emitted",
            event_type=event_type.value,
            booking_id=booking_id,
        )

        # Invoke handlers
        for handler in self._event_handlers[event_type]:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(
                    "booking_event_handler_error",
                    event_type=event_type.value,
                    booking_id=booking_id,
                    error=str(e),
                )

    # -------------------------------------------------------------------------
    # Booking CRUD Operations
    # -------------------------------------------------------------------------

    async def create_booking(
        self,
        agency_id: str,
        applicant_id: str,
        target_system: str,
        target_country: str,
        visa_category: str | None = None,
        preferred_dates: list[str] | None = None,
        location: str | None = None,
        priority: int = 5,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Create a new booking request.

        Creates a booking in PENDING state with provided details.
        The booking must be submitted separately to start processing.

        Args:
            agency_id: ID of the agency making the request.
            applicant_id: ID of the applicant.
            target_system: Target booking system (vfs, idata, bls, kkosmos).
            target_country: Target country code (ISO 3166-1 alpha-2).
            visa_category: Visa category code.
            preferred_dates: List of preferred dates (YYYY-MM-DD format).
            location: Preferred location/city.
            priority: Initial priority (1-10, higher = more urgent).
            metadata: Additional metadata.

        Returns:
            Created booking request data.

        Raises:
            ViseOSError: If creation fails.
        """
        logger.info(
            "booking_create_request",
            agency_id=agency_id,
            target_system=target_system,
            target_country=target_country,
        )

        data = {
            "agency_id": agency_id,
            "applicant_id": applicant_id,
            "target_system": target_system,
            "target_country": target_country,
            "visa_category": visa_category,
            "preferred_dates": preferred_dates,
            "location": location,
            "priority": priority,
            "metadata": metadata or {},
        }

        booking = await self.repository.create(data)

        # Emit created event
        await self._emit_event(
            BookingEventType.CREATED,
            booking["id"],
            {"target_system": target_system, "target_country": target_country},
        )

        logger.info(
            "booking_created",
            booking_id=booking["id"],
            agency_id=agency_id,
        )

        return booking

    async def get_booking(
        self,
        booking_id: str | UUID,
    ) -> dict[str, Any] | None:
        """
        Get a booking by ID.

        Args:
            booking_id: The booking UUID.

        Returns:
            Booking data or None if not found.
        """
        return await self.repository.get_by_id(booking_id)

    async def get_booking_status(
        self,
        booking_id: str | UUID,
    ) -> dict[str, Any]:
        """
        Get the current status of a booking.

        Returns status information including state, attempts,
        and any error details.

        Args:
            booking_id: The booking UUID.

        Returns:
            Status information dictionary.

        Raises:
            BookingError: If booking not found.
        """
        booking = await self.repository.get_by_id(booking_id)

        if not booking:
            raise BookingError(
                f"Booking not found: {booking_id}",
                booking_id=str(booking_id),
                code="BOOKING_NOT_FOUND",
            )

        return {
            "booking_id": str(booking_id),
            "status": booking.get("status"),
            "attempts": booking.get("attempts", 0),
            "max_attempts": booking.get("max_attempts", 50),
            "last_attempt_at": booking.get("last_attempt_at"),
            "confirmation_number": booking.get("confirmation_number"),
            "appointment_date": booking.get("appointment_date"),
            "appointment_time": booking.get("appointment_time"),
            "error_code": booking.get("error_code"),
            "error_message": booking.get("error_message"),
            "created_at": booking.get("date_created"),
            "updated_at": booking.get("date_updated"),
        }

    async def update_booking(
        self,
        booking_id: str | UUID,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Update a booking.

        Args:
            booking_id: The booking UUID.
            data: Fields to update.

        Returns:
            Updated booking data.
        """
        return await self.repository.update(booking_id, data)

    async def cancel_booking(
        self,
        booking_id: str | UUID,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """
        Cancel a booking.

        Transitions the booking to CANCELLED state and releases
        any reserved resources.

        Args:
            booking_id: The booking UUID.
            reason: Optional cancellation reason.

        Returns:
            Updated booking data.

        Raises:
            InvalidStateTransitionError: If booking cannot be cancelled.
        """
        booking = await self.repository.get_by_id(booking_id)

        if not booking:
            raise BookingError(
                f"Booking not found: {booking_id}",
                booking_id=str(booking_id),
                code="BOOKING_NOT_FOUND",
            )

        # Create context from booking
        context = self._booking_to_context(booking)

        # Check if cancellation is allowed
        can_cancel, error_msg = self.state_machine.can_transition(
            context,
            BookingState.CANCELLED,
        )

        if not can_cancel:
            raise InvalidStateTransitionError(
                f"Cannot cancel booking: {error_msg}",
                booking_id=str(booking_id),
                current_state=context.state.value,
                target_state="cancelled",
            )

        # Transition to CANCELLED
        context.failure_reason = FailureReason.CANCELLED_BY_USER
        context = await self.state_machine.transition(
            context,
            BookingState.CANCELLED,
            trigger="user_cancelled",
            metadata={"reason": reason},
        )

        # Execute compensating transactions
        await self.compensation_manager.compensate(
            context,
            BookingState.CANCELLED,
        )

        # Update in database
        result = await self.repository.update_status(
            booking_id,
            "cancelled",
            error_message=f"Cancelled by user: {reason}" if reason else "Cancelled by user",
        )

        logger.info(
            "booking_cancelled",
            booking_id=str(booking_id),
            reason=reason,
        )

        return result

    # -------------------------------------------------------------------------
    # Queue Operations
    # -------------------------------------------------------------------------

    async def submit_booking(
        self,
        booking_id: str | UUID,
        is_premium: bool = False,
        scheduled_time: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Submit a booking for processing.

        Transitions the booking to QUEUED state and routes it
        to the appropriate queue based on priority.

        Args:
            booking_id: The booking UUID.
            is_premium: Whether this is a premium agency booking.
            scheduled_time: Optional scheduled processing time.

        Returns:
            Submission result with queue information.

        Raises:
            BookingError: If submission fails.
            InsufficientCreditsError: If agency lacks credits.
        """
        booking = await self.repository.get_by_id(booking_id)

        if not booking:
            raise BookingError(
                f"Booking not found: {booking_id}",
                booking_id=str(booking_id),
                code="BOOKING_NOT_FOUND",
            )

        # Create context from booking
        context = self._booking_to_context(booking)

        # Transition from PENDING to QUEUED
        context = await self.state_machine.transition(
            context,
            BookingState.QUEUED,
            trigger="submitted",
        )

        # Calculate priority
        priority = self._calculate_priority(
            PriorityFactors(
                is_premium_agency=is_premium,
                date_urgency_days=self._calculate_urgency_days(booking),
                retry_count=context.attempt_count,
            )
        )

        # Determine queue
        queue = self._select_queue(
            site=booking.get("target_system", ""),
            priority=priority,
            scheduled_time=scheduled_time,
        )

        # Update booking status
        await self.repository.update_status(booking_id, "queued")

        # Submit to task queue
        task_result = await self._submit_to_queue(
            booking_id=str(booking_id),
            site=booking.get("target_system", ""),
            priority=priority,
            queue=queue,
            scheduled_time=scheduled_time,
        )

        logger.info(
            "booking_submitted",
            booking_id=str(booking_id),
            queue=queue,
            priority=priority.value,
        )

        return {
            "booking_id": str(booking_id),
            "status": "queued",
            "queue": queue,
            "priority": priority.value,
            "scheduled_time": scheduled_time.isoformat() if scheduled_time else None,
            **task_result,
        }

    async def requeue_booking(
        self,
        booking_id: str | UUID,
        delay_seconds: int = 0,
    ) -> dict[str, Any]:
        """
        Requeue a failed booking for retry.

        Args:
            booking_id: The booking UUID.
            delay_seconds: Delay before processing.

        Returns:
            Requeue result.

        Raises:
            BookingError: If requeue fails.
        """
        booking = await self.repository.get_by_id(booking_id)

        if not booking:
            raise BookingError(
                f"Booking not found: {booking_id}",
                booking_id=str(booking_id),
                code="BOOKING_NOT_FOUND",
            )

        context = self._booking_to_context(booking)

        # Check if retry is allowed
        should_retry, calculated_delay = self.retry_manager.should_retry(context)

        if not should_retry:
            raise BookingError(
                "Booking cannot be retried (max attempts reached or non-retriable failure)",
                booking_id=str(booking_id),
                code="RETRY_NOT_ALLOWED",
            )

        # Use calculated delay if none provided
        if delay_seconds == 0:
            delay_seconds = calculated_delay

        # Transition back to QUEUED
        context = await self.state_machine.transition(
            context,
            BookingState.QUEUED,
            trigger="requeued",
        )

        # Update status
        await self.repository.update_status(booking_id, "queued")

        # Calculate scheduled time
        scheduled_time = datetime.utcnow() + timedelta(seconds=delay_seconds)

        # Submit to retry queue
        task_result = await self._submit_to_queue(
            booking_id=str(booking_id),
            site=booking.get("target_system", ""),
            priority=BookingPriority.NORMAL,
            queue="retry",
            scheduled_time=scheduled_time if delay_seconds > 0 else None,
        )

        await self._emit_event(
            BookingEventType.RETRYING,
            str(booking_id),
            {"delay_seconds": delay_seconds, "attempt_count": context.attempt_count},
        )

        logger.info(
            "booking_requeued",
            booking_id=str(booking_id),
            delay_seconds=delay_seconds,
        )

        return {
            "booking_id": str(booking_id),
            "status": "queued",
            "queue": "retry",
            "delay_seconds": delay_seconds,
            **task_result,
        }

    # -------------------------------------------------------------------------
    # Processing Operations
    # -------------------------------------------------------------------------

    async def process_booking(
        self,
        booking_id: str,
        adapter: BaseSiteAdapter,
        account: Any,
        applicant_data: dict[str, Any],
        proxy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Process a booking through the full flow.

        Executes the complete booking workflow:
        1. Transition to PROCESSING
        2. Login with adapter
        3. Search for slots
        4. Select and book slot
        5. Handle payment and verification
        6. Complete or fail

        This method is typically called by a Celery task.

        Args:
            booking_id: The booking UUID.
            adapter: Site adapter instance.
            account: Bot account for authentication.
            applicant_data: Applicant information for form filling.
            proxy: Optional proxy configuration.

        Returns:
            Processing result.
        """
        booking = await self.repository.get_by_id(booking_id)

        if not booking:
            return {
                "success": False,
                "booking_id": booking_id,
                "error": "Booking not found",
            }

        context = self._booking_to_context(booking)

        try:
            # Transition to PROCESSING
            context = await self.state_machine.transition(
                context,
                BookingState.PROCESSING,
                trigger="processing_started",
            )

            await self._persist_context(booking_id, context)

            # Execute booking via adapter
            async with adapter.create_session(account, proxy) as session:
                # Login
                login_success = await adapter.login(session, account)
                if not login_success:
                    raise BookingError(
                        "Login failed",
                        booking_id=booking_id,
                        code="LOGIN_FAILED",
                    )

                # Build search criteria
                from src.bot.adapters.base import SlotSearchCriteria

                criteria = SlotSearchCriteria(
                    country=booking.get("target_country", ""),
                    category=booking.get("visa_category", ""),
                    city=booking.get("location"),
                )

                # Search for slots
                slots = await adapter.search_slots(session, criteria)

                if not slots:
                    context.failure_reason = FailureReason.NO_SLOT_AVAILABLE
                    raise BookingError(
                        "No slots available",
                        booking_id=booking_id,
                        code="NO_SLOTS",
                    )

                # Transition to SLOT_FOUND
                context = await self.state_machine.transition(
                    context,
                    BookingState.SLOT_FOUND,
                    trigger="slots_found",
                    metadata={"slot_count": len(slots)},
                )

                await self._persist_context(booking_id, context)

                # Select best slot
                selected_slot = self._select_best_slot(slots, booking)

                # Transition to BOOKING
                context = await self.state_machine.transition(
                    context,
                    BookingState.BOOKING,
                    trigger="slot_selected",
                    metadata={"slot_id": selected_slot.id},
                )

                await self._persist_context(booking_id, context)

                # Book the slot
                result: BookingResult = await adapter.book_slot(
                    session,
                    selected_slot,
                    applicant_data,
                )

                if not result.is_success:
                    context.last_error = result.error_message
                    context.failure_reason = FailureReason.SYSTEM_ERROR
                    raise BookingError(
                        result.error_message or "Booking failed",
                        booking_id=booking_id,
                        code=result.error_code or "BOOKING_FAILED",
                    )

                # Transition to COMPLETED
                context.confirmation_number = result.confirmation_number
                context.appointment_date = (
                    result.appointment_date.isoformat()
                    if result.appointment_date
                    else None
                )
                context.appointment_time = result.appointment_time

                context = await self.state_machine.transition(
                    context,
                    BookingState.COMPLETED,
                    trigger="booking_success",
                )

                # Persist final state
                await self.repository.update(
                    booking_id,
                    {
                        "status": "completed",
                        "confirmation_number": context.confirmation_number,
                        "appointment_date": context.appointment_date,
                        "appointment_time": context.appointment_time,
                        "completed_at": datetime.utcnow().isoformat(),
                    },
                )

                # Create booking result record
                await self.repository.create_result({
                    "booking_request_id": booking_id,
                    "agency_id": booking.get("agency_id"),
                    "status": "success",
                    "confirmation_number": context.confirmation_number,
                    "appointment_date": context.appointment_date,
                    "appointment_time": context.appointment_time,
                    "target_system": booking.get("target_system"),
                    "target_country": booking.get("target_country"),
                    "duration_seconds": result.duration_seconds,
                })

                logger.info(
                    "booking_completed",
                    booking_id=booking_id,
                    confirmation_number=context.confirmation_number,
                )

                return {
                    "success": True,
                    "booking_id": booking_id,
                    "confirmation_number": context.confirmation_number,
                    "appointment_date": context.appointment_date,
                    "appointment_time": context.appointment_time,
                }

        except InvalidStateTransitionError:
            raise

        except BookingError as e:
            # Handle known booking errors
            await self._handle_booking_failure(booking_id, context, e)
            return {
                "success": False,
                "booking_id": booking_id,
                "error": str(e),
                "error_code": e.code,
            }

        except Exception as e:
            # Handle unexpected errors
            context.last_error = str(e)
            context.failure_reason = FailureReason.SYSTEM_ERROR
            await self._handle_booking_failure(
                booking_id,
                context,
                BookingError(str(e), booking_id=booking_id),
            )
            return {
                "success": False,
                "booking_id": booking_id,
                "error": str(e),
                "error_code": "SYSTEM_ERROR",
            }

    async def _handle_booking_failure(
        self,
        booking_id: str,
        context: BookingContext,
        error: BookingError,
    ) -> None:
        """
        Handle booking failure with retry or failure transition.

        Args:
            booking_id: The booking UUID.
            context: Current booking context.
            error: The error that occurred.
        """
        # Check if we should retry
        should_retry, delay = self.retry_manager.should_retry(context)

        if should_retry:
            # Transition back to QUEUED for retry
            try:
                context = await self.state_machine.transition(
                    context,
                    BookingState.QUEUED,
                    trigger="retry_scheduled",
                )

                await self._persist_context(booking_id, context)

                # Requeue with delay
                await self.requeue_booking(booking_id, delay)

                logger.info(
                    "booking_retry_scheduled",
                    booking_id=booking_id,
                    delay_seconds=delay,
                    attempt_count=context.attempt_count,
                )
            except InvalidStateTransitionError:
                # If we can't transition to QUEUED, fall through to FAILED
                pass
            else:
                return

        # Transition to FAILED
        try:
            context = await self.state_machine.transition(
                context,
                BookingState.FAILED,
                trigger="max_retries_exceeded" if not should_retry else "failure",
            )
        except InvalidStateTransitionError:
            # Already in terminal state
            pass

        # Execute compensating transactions
        await self.compensation_manager.compensate(
            context,
            context.state,
        )

        # Persist failed state
        await self.repository.update_status(
            booking_id,
            "failed",
            error_code=error.code,
            error_message=str(error),
        )

        # Create failure record
        booking = await self.repository.get_by_id(booking_id)
        if booking:
            await self.repository.create_result({
                "booking_request_id": booking_id,
                "agency_id": booking.get("agency_id"),
                "status": "failed",
                "error_code": error.code,
                "error_message": str(error),
                "target_system": booking.get("target_system"),
                "target_country": booking.get("target_country"),
            })

        logger.warning(
            "booking_failed",
            booking_id=booking_id,
            error=str(error),
            attempts=context.attempt_count,
        )

    # -------------------------------------------------------------------------
    # Query Operations
    # -------------------------------------------------------------------------

    async def list_bookings(
        self,
        agency_id: str | UUID | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        List bookings with optional filters.

        Args:
            agency_id: Filter by agency.
            status: Filter by status.
            limit: Maximum results.
            offset: Result offset.

        Returns:
            List of booking records.
        """
        if agency_id:
            return await self.repository.find_by_agency(
                agency_id,
                status=status,
                limit=limit,
                offset=offset,
            )
        elif status:
            return await self.repository.find_by_status(
                status,
                limit=limit,
                offset=offset,
            )
        else:
            # Return all (limited)
            return await self.repository.find_by_status(
                "pending",
                limit=limit,
                offset=offset,
            )

    async def get_agency_stats(
        self,
        agency_id: str | UUID,
    ) -> dict[str, int]:
        """
        Get booking statistics for an agency.

        Args:
            agency_id: The agency UUID.

        Returns:
            Dictionary of status counts.
        """
        return await self.repository.get_stats_by_agency(agency_id)

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    def _booking_to_context(self, booking: dict[str, Any]) -> BookingContext:
        """
        Convert a booking record to a BookingContext.

        Args:
            booking: Booking data from repository.

        Returns:
            BookingContext instance.
        """
        state_value = booking.get("status", "pending")

        # Handle string state values
        try:
            state = BookingState(state_value)
        except ValueError:
            state = BookingState.PENDING

        # Handle failure reason
        failure_reason = None
        if booking.get("error_code"):
            try:
                failure_reason = FailureReason(booking["error_code"].lower())
            except ValueError:
                pass

        return BookingContext(
            booking_id=str(booking.get("id", "")),
            agency_id=str(booking.get("agency_id", "")),
            applicant_id=str(booking.get("applicant_id", "")),
            state=state,
            attempt_count=booking.get("attempts", 0),
            max_attempts=booking.get("max_attempts", 50),
            last_error=booking.get("error_message"),
            failure_reason=failure_reason,
            confirmation_number=booking.get("confirmation_number"),
            appointment_date=booking.get("appointment_date"),
            appointment_time=booking.get("appointment_time"),
        )

    async def _persist_context(
        self,
        booking_id: str,
        context: BookingContext,
    ) -> None:
        """
        Persist booking context to repository.

        Args:
            booking_id: The booking UUID.
            context: Current context to persist.
        """
        await self.repository.update(
            booking_id,
            {
                "status": context.state.value,
                "attempts": context.attempt_count,
                "last_attempt_at": context.last_attempt_at.isoformat() if context.last_attempt_at else None,
                "last_error": context.last_error,
            },
        )

    def _calculate_priority(self, factors: PriorityFactors) -> BookingPriority:
        """
        Calculate booking priority from factors.

        Args:
            factors: Priority calculation factors.

        Returns:
            Calculated BookingPriority.
        """
        score = 5  # Base score (NORMAL)

        # Premium agency bonus
        if factors.is_premium_agency:
            score += 2

        # Date urgency
        if factors.date_urgency_days <= 7:
            score += 3
        elif factors.date_urgency_days <= 14:
            score += 2
        elif factors.date_urgency_days <= 21:
            score += 1

        # Slot rarity
        if factors.slot_rarity >= 0.8:
            score += 2
        elif factors.slot_rarity >= 0.5:
            score += 1

        # Retry penalty
        if factors.retry_count > 0:
            score -= min(factors.retry_count, 2)

        # Payment value bonus
        if factors.payment_value >= 200:
            score += 1

        # Clamp and convert to enum
        score = max(1, min(10, score))

        if score >= 10:
            return BookingPriority.CRITICAL
        elif score >= 8:
            return BookingPriority.HIGH
        elif score >= 6:
            return BookingPriority.ELEVATED
        elif score >= 4:
            return BookingPriority.NORMAL
        elif score >= 2:
            return BookingPriority.LOW
        else:
            return BookingPriority.BACKGROUND

    def _calculate_urgency_days(self, booking: dict[str, Any]) -> int:
        """
        Calculate days until preferred appointment.

        Args:
            booking: Booking data.

        Returns:
            Days until preferred date, or 30 if not specified.
        """
        preferred_dates = booking.get("preferred_dates", [])

        if not preferred_dates:
            return 30

        try:
            # Find earliest preferred date
            earliest = min(
                datetime.fromisoformat(d) for d in preferred_dates if d
            )
            days = (earliest - datetime.utcnow()).days
            return max(0, days)
        except (ValueError, TypeError):
            return 30

    def _select_queue(
        self,
        site: str,
        priority: BookingPriority,
        scheduled_time: datetime | None = None,
    ) -> str:
        """
        Select the appropriate queue for a booking.

        Args:
            site: Target site code.
            priority: Calculated priority.
            scheduled_time: Optional scheduled time.

        Returns:
            Queue name.
        """
        now = datetime.utcnow()

        # Night operations (02:00-06:00 Istanbul)
        istanbul_hour = (now.hour + 3) % 24  # UTC+3
        if 2 <= istanbul_hour < 6:
            return "night_ops"

        # Scheduled task
        if scheduled_time and scheduled_time > now:
            return "scheduled"

        # Priority-based selection
        if priority in [BookingPriority.CRITICAL, BookingPriority.HIGH]:
            return "high_priority"

        # Site-specific for better load balancing
        if site in ["vfs", "idata", "bls", "kkosmos"]:
            return site

        return "normal"

    def _select_best_slot(
        self,
        slots: list[Slot],
        booking: dict[str, Any],
    ) -> Slot:
        """
        Select the best slot from available options.

        Considers:
        - Preferred dates
        - Preferred location
        - Slot capacity

        Args:
            slots: Available slots.
            booking: Booking preferences.

        Returns:
            Selected slot.
        """
        if not slots:
            raise BookingError(
                "No slots to select from",
                booking_id=booking.get("id"),
                code="NO_SLOTS",
            )

        preferred_dates = set(booking.get("preferred_dates") or [])
        preferred_location = booking.get("location", "").lower()

        # Score slots
        scored_slots = []
        for slot in slots:
            score = 0

            # Prefer matching dates
            if slot.date.isoformat() in preferred_dates:
                score += 10

            # Prefer matching location
            if preferred_location and preferred_location in slot.location.lower():
                score += 5

            # Prefer earlier dates
            score -= (slot.date - datetime.utcnow().date()).days * 0.1

            scored_slots.append((score, slot))

        # Sort by score (descending) and return best
        scored_slots.sort(key=lambda x: x[0], reverse=True)
        return scored_slots[0][1]

    async def _submit_to_queue(
        self,
        booking_id: str,
        site: str,
        priority: BookingPriority,
        queue: str,
        scheduled_time: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Submit a booking task to the Celery queue.

        Args:
            booking_id: The booking UUID.
            site: Target site code.
            priority: Task priority.
            queue: Target queue name.
            scheduled_time: Optional scheduled time.

        Returns:
            Task submission result.
        """
        try:
            from src.queue.tasks.booking import process_booking

            # Determine task options
            options: dict[str, Any] = {
                "priority": priority.value,
                "queue": queue,
            }

            if scheduled_time:
                options["eta"] = scheduled_time

            # Submit task
            task = process_booking.apply_async(
                args=[booking_id, site, priority.value],
                **options,
            )

            return {
                "task_id": task.id,
                "submitted": True,
            }

        except ImportError:
            # Queue not configured - return mock result for testing
            logger.warning(
                "queue_not_configured",
                booking_id=booking_id,
            )
            return {
                "task_id": None,
                "submitted": False,
                "reason": "Queue not configured",
            }

        except Exception as e:
            logger.error(
                "queue_submit_error",
                booking_id=booking_id,
                error=str(e),
            )
            return {
                "task_id": None,
                "submitted": False,
                "reason": str(e),
            }


# =============================================================================
# Exports
# =============================================================================


__all__ = [
    "BookingEvent",
    "BookingEventType",
    "BookingPriority",
    "BookingService",
    "PriorityFactors",
]
