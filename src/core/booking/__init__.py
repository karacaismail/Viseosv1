"""
VISE OS Booking Module.

Provides booking management functionality including:
- State machine for booking flow control
- Booking context and state transitions
- Retry policies and compensation transactions

Components:
- state_machine: BookingStateMachine, BookingState, BookingContext
- FailureReason, StateTransition for tracking
- RetryManager, RetryPolicy for retry handling
"""

from src.core.booking.state_machine import (
    BookingContext,
    BookingState,
    BookingStateMachine,
    CompensatingTransactionManager,
    FailureReason,
    RetryConfig,
    RetryManager,
    RetryPolicy,
    StateTransition,
)

__all__ = [
    # Core classes
    "BookingStateMachine",
    "BookingState",
    "BookingContext",
    # Supporting classes
    "FailureReason",
    "StateTransition",
    # Retry management
    "RetryManager",
    "RetryPolicy",
    "RetryConfig",
    # Compensation
    "CompensatingTransactionManager",
]
