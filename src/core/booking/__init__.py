"""
VISE OS Booking Module.

Provides booking management functionality including:
- State machine for booking flow control
- Booking context and state transitions
- Retry policies and compensation transactions
- Repository for data access

Components:
- state_machine: BookingStateMachine, BookingState, BookingContext
- FailureReason, StateTransition for tracking
- RetryManager, RetryPolicy for retry handling
- repository: BookingRepository for data access
"""

from src.core.booking.repository import BookingRepository
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
    # Repository
    "BookingRepository",
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
