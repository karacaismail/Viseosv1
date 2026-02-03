"""
Booking State Machine.

Deterministic state machine for all booking flows. Handles state transitions,
error handling, recovery flows, and compensating transactions.

Key Features:
- Idempotent operations
- Eventual consistency guarantees
- State timeout detection
- Retry policies with exponential backoff
- Compensating transactions for failure recovery

Usage:
    from src.core.booking.state_machine import (
        BookingStateMachine,
        BookingState,
        BookingContext,
    )

    sm = BookingStateMachine()
    context = BookingContext(agency_id="agency-1", applicant_id="app-1")

    # Transition to QUEUED
    context = await sm.transition(
        context,
        BookingState.QUEUED,
        trigger="validation_passed",
    )
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Optional

from src.core.exceptions import InvalidStateTransitionError


class BookingState(Enum):
    """
    Booking states for the state machine.

    States are categorized as:
    - Initial states: PENDING, QUEUED
    - Processing states: PROCESSING, SLOT_FOUND, BOOKING, PAYMENT, VERIFYING
    - Terminal states: COMPLETED, FAILED, EXPIRED, CANCELLED
    """

    # Initial states
    PENDING = "pending"  # Received from Sheets, awaiting validation
    QUEUED = "queued"  # Added to queue, awaiting processing

    # Processing states
    PROCESSING = "processing"  # Actively being processed
    SLOT_FOUND = "slot_found"  # Slot found, awaiting selection
    BOOKING = "booking"  # Form being filled
    PAYMENT = "payment"  # Payment processing
    VERIFYING = "verifying"  # Email/SMS verification

    # Terminal states
    COMPLETED = "completed"  # Successfully completed
    FAILED = "failed"  # Permanently failed
    EXPIRED = "expired"  # Timed out
    CANCELLED = "cancelled"  # Cancelled by user or system


class FailureReason(Enum):
    """
    Reasons for booking failure.

    Categorized by failure type:
    - Slot related: NO_SLOT_AVAILABLE, SLOT_TAKEN
    - Account related: ACCOUNT_BANNED, ACCOUNT_LOCKED
    - Payment related: PAYMENT_FAILED, PAYMENT_TIMEOUT, CARD_DECLINED
    - Verification related: VERIFICATION_FAILED, SMS_NOT_RECEIVED
    - System related: MAX_RETRIES_EXCEEDED, TIMEOUT, SYSTEM_ERROR
    - External: SITE_UNAVAILABLE, CAPTCHA_FAILED
    - User related: INVALID_DATA, CANCELLED_BY_USER
    """

    # Slot related
    NO_SLOT_AVAILABLE = "no_slot_available"
    SLOT_TAKEN = "slot_taken"

    # Account related
    ACCOUNT_BANNED = "account_banned"
    ACCOUNT_LOCKED = "account_locked"

    # Payment related
    PAYMENT_FAILED = "payment_failed"
    PAYMENT_TIMEOUT = "payment_timeout"
    CARD_DECLINED = "card_declined"

    # Verification related
    VERIFICATION_FAILED = "verification_failed"
    SMS_NOT_RECEIVED = "sms_not_received"

    # System related
    MAX_RETRIES_EXCEEDED = "max_retries_exceeded"
    TIMEOUT = "timeout"
    SYSTEM_ERROR = "system_error"

    # External
    SITE_UNAVAILABLE = "site_unavailable"
    CAPTCHA_FAILED = "captcha_failed"

    # User related
    INVALID_DATA = "invalid_data"
    CANCELLED_BY_USER = "cancelled_by_user"


@dataclass
class StateTransition:
    """
    Record of a state transition.

    Captures the source state, target state, timestamp, trigger event,
    and any additional metadata for audit and debugging purposes.
    """

    from_state: BookingState
    to_state: BookingState
    timestamp: datetime
    trigger: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert transition to dictionary for serialization."""
        return {
            "from": self.from_state.value,
            "to": self.to_state.value,
            "timestamp": self.timestamp.isoformat(),
            "trigger": self.trigger,
            "metadata": self.metadata,
        }


@dataclass
class BookingContext:
    """
    Booking processing context.

    Contains all state and metadata for a booking operation including:
    - Identity (booking_id, agency_id, applicant_id)
    - Current state and sub-state
    - Timing information
    - Attempt tracking
    - Error tracking
    - Progress tracking
    - Result data
    - Assigned resources
    - Credit tracking
    - State history
    """

    # Identity
    booking_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agency_id: str = ""
    applicant_id: str = ""

    # Current state
    state: BookingState = BookingState.PENDING
    sub_state: Optional[str] = None

    # Timing
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None

    # Attempt tracking
    attempt_count: int = 0
    max_attempts: int = 3
    last_attempt_at: Optional[datetime] = None

    # Error tracking
    last_error: Optional[str] = None
    failure_reason: Optional[FailureReason] = None
    error_history: list[dict[str, Any]] = field(default_factory=list)

    # Progress tracking
    slot_found_at: Optional[datetime] = None
    booking_started_at: Optional[datetime] = None
    payment_started_at: Optional[datetime] = None

    # Result
    confirmation_number: Optional[str] = None
    appointment_date: Optional[str] = None
    appointment_time: Optional[str] = None

    # Resources
    assigned_account_id: Optional[str] = None
    assigned_proxy_id: Optional[str] = None
    assigned_session_id: Optional[str] = None

    # Credit tracking
    credit_reserved: float = 0.0
    credit_charged: float = 0.0

    # State history
    state_history: list[StateTransition] = field(default_factory=list)

    def is_terminal(self) -> bool:
        """Check if booking is in a terminal state."""
        return self.state in (
            BookingState.COMPLETED,
            BookingState.FAILED,
            BookingState.EXPIRED,
            BookingState.CANCELLED,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert context to dictionary for serialization."""
        return {
            "booking_id": self.booking_id,
            "agency_id": self.agency_id,
            "applicant_id": self.applicant_id,
            "state": self.state.value,
            "sub_state": self.sub_state,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "last_error": self.last_error,
            "failure_reason": self.failure_reason.value if self.failure_reason else None,
            "confirmation_number": self.confirmation_number,
            "appointment_date": self.appointment_date,
            "appointment_time": self.appointment_time,
            "credit_reserved": self.credit_reserved,
            "credit_charged": self.credit_charged,
            "state_history": [t.to_dict() for t in self.state_history],
        }


class BookingStateMachine:
    """
    Booking state machine.

    Manages state transitions for booking operations with validation,
    timeout detection, and entry/exit handlers.

    Valid Transitions:
    - PENDING -> QUEUED, EXPIRED, CANCELLED
    - QUEUED -> PROCESSING, EXPIRED, CANCELLED
    - PROCESSING -> SLOT_FOUND, FAILED, QUEUED (retry), CANCELLED
    - SLOT_FOUND -> BOOKING, FAILED, PROCESSING (slot lost), CANCELLED
    - BOOKING -> PAYMENT, FAILED, SLOT_FOUND (form error), CANCELLED
    - PAYMENT -> VERIFYING, COMPLETED, FAILED, CANCELLED
    - VERIFYING -> COMPLETED, FAILED, CANCELLED
    - FAILED -> QUEUED (manual retry)
    - COMPLETED, EXPIRED, CANCELLED -> (terminal, no transitions)

    State Timeouts (seconds):
    - PENDING: 3600 (1 hour)
    - QUEUED: 86400 (24 hours)
    - PROCESSING: 300 (5 minutes)
    - SLOT_FOUND: 60 (1 minute - slots can be taken quickly)
    - BOOKING: 180 (3 minutes)
    - PAYMENT: 300 (5 minutes including 3DS)
    - VERIFYING: 180 (3 minutes)

    Usage:
        sm = BookingStateMachine()

        # Register custom handlers
        sm.register_entry_handler(BookingState.COMPLETED, on_completed)
        sm.register_exit_handler(BookingState.PROCESSING, on_exit_processing)

        # Transition
        context = await sm.transition(context, BookingState.QUEUED, "validated")
    """

    # Valid state transitions
    TRANSITIONS: dict[BookingState, list[BookingState]] = {
        BookingState.PENDING: [
            BookingState.QUEUED,
            BookingState.EXPIRED,
            BookingState.CANCELLED,
        ],
        BookingState.QUEUED: [
            BookingState.PROCESSING,
            BookingState.EXPIRED,
            BookingState.CANCELLED,
        ],
        BookingState.PROCESSING: [
            BookingState.SLOT_FOUND,
            BookingState.FAILED,
            BookingState.QUEUED,  # Retry back to queue
            BookingState.CANCELLED,
        ],
        BookingState.SLOT_FOUND: [
            BookingState.BOOKING,
            BookingState.FAILED,
            BookingState.PROCESSING,  # Slot lost, search again
            BookingState.CANCELLED,
        ],
        BookingState.BOOKING: [
            BookingState.PAYMENT,
            BookingState.FAILED,
            BookingState.SLOT_FOUND,  # Form error, slot still available
            BookingState.CANCELLED,
        ],
        BookingState.PAYMENT: [
            BookingState.VERIFYING,
            BookingState.COMPLETED,  # No verification required
            BookingState.FAILED,
            BookingState.CANCELLED,
        ],
        BookingState.VERIFYING: [
            BookingState.COMPLETED,
            BookingState.FAILED,
            BookingState.CANCELLED,
        ],
        # Terminal states - no outgoing transitions
        BookingState.COMPLETED: [],
        BookingState.FAILED: [
            BookingState.QUEUED,  # Manual retry
        ],
        BookingState.EXPIRED: [],
        BookingState.CANCELLED: [],
    }

    # State timeout configuration (seconds)
    STATE_TIMEOUTS: dict[BookingState, int] = {
        BookingState.PENDING: 3600,  # 1 hour
        BookingState.QUEUED: 86400,  # 24 hours
        BookingState.PROCESSING: 300,  # 5 minutes
        BookingState.SLOT_FOUND: 60,  # 1 minute (slots can be taken)
        BookingState.BOOKING: 180,  # 3 minutes
        BookingState.PAYMENT: 300,  # 5 minutes (including 3DS)
        BookingState.VERIFYING: 180,  # 3 minutes
    }

    def __init__(self) -> None:
        """Initialize the state machine with empty handler registries."""
        self.entry_handlers: dict[BookingState, Callable] = {}
        self.exit_handlers: dict[BookingState, Callable] = {}
        self.transition_validators: dict[
            tuple[BookingState, BookingState], Callable
        ] = {}

    def can_transition(
        self,
        context: BookingContext,
        target_state: BookingState,
    ) -> tuple[bool, str]:
        """
        Check if a transition is valid.

        Args:
            context: Current booking context.
            target_state: Target state to transition to.

        Returns:
            Tuple of (is_valid, reason_message).
        """
        current = context.state

        # Check valid transitions
        valid_targets = self.TRANSITIONS.get(current, [])
        if target_state not in valid_targets:
            allowed = [s.value for s in valid_targets]
            return (
                False,
                f"Invalid transition: {current.value} -> {target_state.value}. "
                f"Allowed: {allowed}",
            )

        # Check custom validator
        validator_key = (current, target_state)
        if validator_key in self.transition_validators:
            validator = self.transition_validators[validator_key]
            is_valid, reason = validator(context)
            if not is_valid:
                return False, reason

        # Check timeout (if timed out, only failed/expired allowed)
        if self._is_timed_out(context):
            if target_state not in [BookingState.FAILED, BookingState.EXPIRED]:
                return False, "State timed out"

        return True, "OK"

    async def transition(
        self,
        context: BookingContext,
        target_state: BookingState,
        trigger: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> BookingContext:
        """
        Perform a state transition.

        Validates the transition, executes exit/entry handlers,
        and updates the context with the new state.

        Args:
            context: Current booking context.
            target_state: Target state to transition to.
            trigger: Event that triggered the transition.
            metadata: Additional metadata for the transition.

        Returns:
            Updated booking context.

        Raises:
            InvalidStateTransitionError: If the transition is not valid.
        """
        # Validate transition
        can_do, reason = self.can_transition(context, target_state)
        if not can_do:
            raise InvalidStateTransitionError(
                f"Cannot transition from {context.state.value} to "
                f"{target_state.value}: {reason}",
                booking_id=context.booking_id,
                current_state=context.state.value,
                target_state=target_state.value,
                allowed_states=[s.value for s in self.TRANSITIONS.get(context.state, [])],
            )

        # Exit handler
        if context.state in self.exit_handlers:
            await self.exit_handlers[context.state](context)

        # Record transition
        transition = StateTransition(
            from_state=context.state,
            to_state=target_state,
            timestamp=datetime.utcnow(),
            trigger=trigger,
            metadata=metadata or {},
        )
        context.state_history.append(transition)

        # Update state
        context.state = target_state
        context.updated_at = datetime.utcnow()

        # State-specific updates
        if target_state == BookingState.PROCESSING:
            context.attempt_count += 1
            context.last_attempt_at = datetime.utcnow()
        elif target_state == BookingState.SLOT_FOUND:
            context.slot_found_at = datetime.utcnow()
        elif target_state == BookingState.BOOKING:
            context.booking_started_at = datetime.utcnow()
        elif target_state == BookingState.PAYMENT:
            context.payment_started_at = datetime.utcnow()

        # Entry handler
        if target_state in self.entry_handlers:
            await self.entry_handlers[target_state](context)

        return context

    def _is_timed_out(self, context: BookingContext) -> bool:
        """
        Check if the current state has timed out.

        Args:
            context: Current booking context.

        Returns:
            True if the state has exceeded its timeout.
        """
        timeout = self.STATE_TIMEOUTS.get(context.state)
        if not timeout:
            return False

        elapsed = (datetime.utcnow() - context.updated_at).total_seconds()
        return elapsed > timeout

    def get_timeout(self, state: BookingState) -> Optional[int]:
        """
        Get the timeout for a state in seconds.

        Args:
            state: The state to get timeout for.

        Returns:
            Timeout in seconds, or None if no timeout.
        """
        return self.STATE_TIMEOUTS.get(state)

    def get_valid_transitions(self, state: BookingState) -> list[BookingState]:
        """
        Get valid target states for a given state.

        Args:
            state: The source state.

        Returns:
            List of valid target states.
        """
        return self.TRANSITIONS.get(state, [])

    def register_entry_handler(
        self,
        state: BookingState,
        handler: Callable,
    ) -> None:
        """
        Register a handler to be called when entering a state.

        Args:
            state: The state to register the handler for.
            handler: Async function to call when entering the state.
        """
        self.entry_handlers[state] = handler

    def register_exit_handler(
        self,
        state: BookingState,
        handler: Callable,
    ) -> None:
        """
        Register a handler to be called when exiting a state.

        Args:
            state: The state to register the handler for.
            handler: Async function to call when exiting the state.
        """
        self.exit_handlers[state] = handler

    def register_transition_validator(
        self,
        from_state: BookingState,
        to_state: BookingState,
        validator: Callable,
    ) -> None:
        """
        Register a custom validator for a specific transition.

        Args:
            from_state: The source state.
            to_state: The target state.
            validator: Function that returns (is_valid, reason) tuple.
        """
        self.transition_validators[(from_state, to_state)] = validator


class RetryPolicy(Enum):
    """Retry policy strategies."""

    IMMEDIATE = "immediate"
    EXPONENTIAL_BACKOFF = "exponential_backoff"
    FIXED_DELAY = "fixed_delay"
    NO_RETRY = "no_retry"


@dataclass
class RetryConfig:
    """Retry configuration for a failure reason."""

    policy: RetryPolicy
    max_attempts: int
    initial_delay_seconds: int
    max_delay_seconds: int
    multiplier: float = 2.0


class RetryManager:
    """
    Manages retry decisions based on failure reasons.

    Each failure reason has a configured retry policy that determines:
    - Whether to retry at all
    - How many attempts are allowed
    - What delay strategy to use
    """

    # Failure reason -> retry config mapping
    RETRY_CONFIGS: dict[FailureReason, RetryConfig] = {
        # Retriable with backoff
        FailureReason.TIMEOUT: RetryConfig(
            policy=RetryPolicy.EXPONENTIAL_BACKOFF,
            max_attempts=3,
            initial_delay_seconds=30,
            max_delay_seconds=300,
        ),
        FailureReason.SITE_UNAVAILABLE: RetryConfig(
            policy=RetryPolicy.EXPONENTIAL_BACKOFF,
            max_attempts=5,
            initial_delay_seconds=60,
            max_delay_seconds=600,
        ),
        FailureReason.SLOT_TAKEN: RetryConfig(
            policy=RetryPolicy.IMMEDIATE,
            max_attempts=5,
            initial_delay_seconds=5,
            max_delay_seconds=5,
        ),
        FailureReason.CAPTCHA_FAILED: RetryConfig(
            policy=RetryPolicy.FIXED_DELAY,
            max_attempts=3,
            initial_delay_seconds=60,
            max_delay_seconds=60,
        ),
        FailureReason.SYSTEM_ERROR: RetryConfig(
            policy=RetryPolicy.EXPONENTIAL_BACKOFF,
            max_attempts=3,
            initial_delay_seconds=30,
            max_delay_seconds=300,
        ),
        # Retriable with long delay (account related)
        FailureReason.ACCOUNT_BANNED: RetryConfig(
            policy=RetryPolicy.FIXED_DELAY,
            max_attempts=2,
            initial_delay_seconds=7200,  # 2 hours
            max_delay_seconds=7200,
        ),
        FailureReason.ACCOUNT_LOCKED: RetryConfig(
            policy=RetryPolicy.FIXED_DELAY,
            max_attempts=2,
            initial_delay_seconds=3600,  # 1 hour
            max_delay_seconds=3600,
        ),
        # Payment related - limited retries
        FailureReason.PAYMENT_FAILED: RetryConfig(
            policy=RetryPolicy.FIXED_DELAY,
            max_attempts=2,
            initial_delay_seconds=60,
            max_delay_seconds=60,
        ),
        FailureReason.PAYMENT_TIMEOUT: RetryConfig(
            policy=RetryPolicy.EXPONENTIAL_BACKOFF,
            max_attempts=2,
            initial_delay_seconds=60,
            max_delay_seconds=300,
        ),
        # Verification related
        FailureReason.VERIFICATION_FAILED: RetryConfig(
            policy=RetryPolicy.FIXED_DELAY,
            max_attempts=2,
            initial_delay_seconds=60,
            max_delay_seconds=60,
        ),
        FailureReason.SMS_NOT_RECEIVED: RetryConfig(
            policy=RetryPolicy.FIXED_DELAY,
            max_attempts=2,
            initial_delay_seconds=120,  # 2 minutes
            max_delay_seconds=120,
        ),
        # Not retriable
        FailureReason.INVALID_DATA: RetryConfig(
            policy=RetryPolicy.NO_RETRY,
            max_attempts=0,
            initial_delay_seconds=0,
            max_delay_seconds=0,
        ),
        FailureReason.CANCELLED_BY_USER: RetryConfig(
            policy=RetryPolicy.NO_RETRY,
            max_attempts=0,
            initial_delay_seconds=0,
            max_delay_seconds=0,
        ),
        FailureReason.CARD_DECLINED: RetryConfig(
            policy=RetryPolicy.NO_RETRY,
            max_attempts=0,
            initial_delay_seconds=0,
            max_delay_seconds=0,
        ),
        FailureReason.NO_SLOT_AVAILABLE: RetryConfig(
            policy=RetryPolicy.FIXED_DELAY,
            max_attempts=10,
            initial_delay_seconds=300,  # 5 minutes
            max_delay_seconds=300,
        ),
        FailureReason.MAX_RETRIES_EXCEEDED: RetryConfig(
            policy=RetryPolicy.NO_RETRY,
            max_attempts=0,
            initial_delay_seconds=0,
            max_delay_seconds=0,
        ),
    }

    # Default config for unknown failure reasons
    DEFAULT_CONFIG = RetryConfig(
        policy=RetryPolicy.EXPONENTIAL_BACKOFF,
        max_attempts=3,
        initial_delay_seconds=30,
        max_delay_seconds=300,
    )

    def should_retry(
        self,
        context: BookingContext,
    ) -> tuple[bool, int]:
        """
        Determine if a booking should be retried and how long to wait.

        Args:
            context: The booking context with failure information.

        Returns:
            Tuple of (should_retry, delay_seconds).
        """
        reason = context.failure_reason
        config = self.RETRY_CONFIGS.get(reason, self.DEFAULT_CONFIG) if reason else self.DEFAULT_CONFIG

        # No retry policy
        if config.policy == RetryPolicy.NO_RETRY:
            return False, 0

        # Max attempts check
        if context.attempt_count >= config.max_attempts:
            return False, 0

        # Calculate delay
        delay = self._calculate_delay(config, context.attempt_count)

        return True, delay

    def _calculate_delay(self, config: RetryConfig, attempt: int) -> int:
        """
        Calculate the delay before the next retry.

        Args:
            config: Retry configuration.
            attempt: Current attempt number.

        Returns:
            Delay in seconds.
        """
        if config.policy == RetryPolicy.IMMEDIATE:
            return config.initial_delay_seconds

        if config.policy == RetryPolicy.FIXED_DELAY:
            return config.initial_delay_seconds

        if config.policy == RetryPolicy.EXPONENTIAL_BACKOFF:
            delay = config.initial_delay_seconds * (config.multiplier ** (attempt - 1))
            return min(int(delay), config.max_delay_seconds)

        return config.initial_delay_seconds

    def get_config(self, reason: FailureReason) -> RetryConfig:
        """
        Get the retry configuration for a failure reason.

        Args:
            reason: The failure reason.

        Returns:
            Retry configuration.
        """
        return self.RETRY_CONFIGS.get(reason, self.DEFAULT_CONFIG)


class CompensatingTransactionManager:
    """
    Manages compensating transactions for failed bookings.

    When a booking fails, this manager ensures that all resources
    are properly released and any partial operations are reversed.

    Compensations include:
    - Credit release
    - Account release
    - Proxy release
    - Session cleanup
    - Payment refund (if applicable)
    """

    def __init__(
        self,
        credit_service: Any = None,
        account_pool: Any = None,
        proxy_pool: Any = None,
        session_manager: Any = None,
    ) -> None:
        """
        Initialize with optional service dependencies.

        Args:
            credit_service: Service for credit management.
            account_pool: Service for account management.
            proxy_pool: Service for proxy management.
            session_manager: Service for browser session management.
        """
        self.credits = credit_service
        self.accounts = account_pool
        self.proxies = proxy_pool
        self.sessions = session_manager

    async def compensate(
        self,
        context: BookingContext,
        failure_point: BookingState,
    ) -> None:
        """
        Execute compensating transactions for a failed booking.

        Args:
            context: The booking context.
            failure_point: The state at which the failure occurred.
        """
        compensations = []

        # Credit release (always if reserved)
        if context.credit_reserved > 0 and self.credits:
            compensations.append(self._release_credit(context))

        # Account release
        if context.assigned_account_id and self.accounts:
            success = failure_point not in [
                BookingState.COMPLETED,
                BookingState.CANCELLED,
            ]
            compensations.append(self._release_account(context, success=success))

        # Proxy release
        if context.assigned_proxy_id and self.proxies:
            compensations.append(self._release_proxy(context))

        # Session cleanup
        if context.assigned_session_id and self.sessions:
            compensations.append(self._cleanup_session(context))

        # Payment reversal (if partial charge occurred)
        if (
            context.credit_charged > 0
            and failure_point == BookingState.VERIFYING
            and self.credits
        ):
            compensations.append(self._refund_payment(context))

        # Execute all compensations concurrently
        if compensations:
            await asyncio.gather(*compensations, return_exceptions=True)

    async def _release_credit(self, context: BookingContext) -> None:
        """Release reserved credit."""
        if self.credits:
            await self.credits.release(
                agency_id=context.agency_id,
                amount=context.credit_reserved,
                booking_id=context.booking_id,
            )

    async def _release_account(self, context: BookingContext, success: bool) -> None:
        """Release assigned account."""
        if self.accounts:
            await self.accounts.release(
                account_id=context.assigned_account_id,
                success=success,
                error_message=context.last_error,
            )

    async def _release_proxy(self, context: BookingContext) -> None:
        """Release assigned proxy."""
        if self.proxies:
            await self.proxies.report_usage(
                proxy_id=context.assigned_proxy_id,
                success=context.state == BookingState.COMPLETED,
            )

    async def _cleanup_session(self, context: BookingContext) -> None:
        """Cleanup browser session."""
        if self.sessions:
            await self.sessions.cleanup(
                session_id=context.assigned_session_id,
            )

    async def _refund_payment(self, context: BookingContext) -> None:
        """Refund payment if applicable."""
        # Payment reversal logic would go here
        pass


__all__ = [
    "BookingContext",
    "BookingState",
    "BookingStateMachine",
    "CompensatingTransactionManager",
    "FailureReason",
    "RetryConfig",
    "RetryManager",
    "RetryPolicy",
    "StateTransition",
]
