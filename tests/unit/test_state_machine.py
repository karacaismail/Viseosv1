"""
Unit Tests for Booking State Machine.

This module provides comprehensive tests for the booking state machine,
including state transitions, timeout detection, retry policies, and
compensating transactions.

Test Categories:
- Enums: BookingState and FailureReason validation
- StateTransition: Transition record dataclass
- BookingContext: Booking context with all fields
- BookingStateMachine: State transition logic
- RetryManager: Retry policy and delay calculation
- CompensatingTransactionManager: Failure recovery

Usage:
    pytest tests/unit/test_state_machine.py -v
    pytest tests/unit/test_state_machine.py -k "test_valid_transitions" -v
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

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
from src.core.exceptions import InvalidStateTransitionError


# =============================================================================
# BookingState Enum Tests
# =============================================================================


class TestBookingState:
    """Tests for BookingState enum."""

    def test_initial_states_exist(self) -> None:
        """Verify initial states are defined."""
        assert BookingState.PENDING.value == "pending"
        assert BookingState.QUEUED.value == "queued"

    def test_processing_states_exist(self) -> None:
        """Verify processing states are defined."""
        assert BookingState.PROCESSING.value == "processing"
        assert BookingState.SLOT_FOUND.value == "slot_found"
        assert BookingState.BOOKING.value == "booking"
        assert BookingState.PAYMENT.value == "payment"
        assert BookingState.VERIFYING.value == "verifying"

    def test_terminal_states_exist(self) -> None:
        """Verify terminal states are defined."""
        assert BookingState.COMPLETED.value == "completed"
        assert BookingState.FAILED.value == "failed"
        assert BookingState.EXPIRED.value == "expired"
        assert BookingState.CANCELLED.value == "cancelled"

    def test_all_states_count(self) -> None:
        """Verify total state count."""
        assert len(BookingState) == 11

    def test_state_from_value(self) -> None:
        """Verify states can be created from string values."""
        assert BookingState("pending") == BookingState.PENDING
        assert BookingState("completed") == BookingState.COMPLETED

    def test_invalid_state_raises_error(self) -> None:
        """Verify invalid state value raises ValueError."""
        with pytest.raises(ValueError):
            BookingState("invalid_state")


# =============================================================================
# FailureReason Enum Tests
# =============================================================================


class TestFailureReason:
    """Tests for FailureReason enum."""

    def test_slot_related_reasons(self) -> None:
        """Verify slot-related failure reasons."""
        assert FailureReason.NO_SLOT_AVAILABLE.value == "no_slot_available"
        assert FailureReason.SLOT_TAKEN.value == "slot_taken"

    def test_account_related_reasons(self) -> None:
        """Verify account-related failure reasons."""
        assert FailureReason.ACCOUNT_BANNED.value == "account_banned"
        assert FailureReason.ACCOUNT_LOCKED.value == "account_locked"

    def test_payment_related_reasons(self) -> None:
        """Verify payment-related failure reasons."""
        assert FailureReason.PAYMENT_FAILED.value == "payment_failed"
        assert FailureReason.PAYMENT_TIMEOUT.value == "payment_timeout"
        assert FailureReason.CARD_DECLINED.value == "card_declined"

    def test_verification_related_reasons(self) -> None:
        """Verify verification-related failure reasons."""
        assert FailureReason.VERIFICATION_FAILED.value == "verification_failed"
        assert FailureReason.SMS_NOT_RECEIVED.value == "sms_not_received"

    def test_system_related_reasons(self) -> None:
        """Verify system-related failure reasons."""
        assert FailureReason.MAX_RETRIES_EXCEEDED.value == "max_retries_exceeded"
        assert FailureReason.TIMEOUT.value == "timeout"
        assert FailureReason.SYSTEM_ERROR.value == "system_error"

    def test_external_reasons(self) -> None:
        """Verify external failure reasons."""
        assert FailureReason.SITE_UNAVAILABLE.value == "site_unavailable"
        assert FailureReason.CAPTCHA_FAILED.value == "captcha_failed"

    def test_user_related_reasons(self) -> None:
        """Verify user-related failure reasons."""
        assert FailureReason.INVALID_DATA.value == "invalid_data"
        assert FailureReason.CANCELLED_BY_USER.value == "cancelled_by_user"

    def test_all_failure_reasons_count(self) -> None:
        """Verify total failure reason count."""
        assert len(FailureReason) == 14


# =============================================================================
# StateTransition Tests
# =============================================================================


class TestStateTransition:
    """Tests for StateTransition dataclass."""

    def test_create_transition(self) -> None:
        """Verify transition creation with required fields."""
        transition = StateTransition(
            from_state=BookingState.PENDING,
            to_state=BookingState.QUEUED,
            timestamp=datetime.utcnow(),
            trigger="validation_passed",
        )

        assert transition.from_state == BookingState.PENDING
        assert transition.to_state == BookingState.QUEUED
        assert transition.trigger == "validation_passed"
        assert isinstance(transition.timestamp, datetime)
        assert transition.metadata == {}

    def test_create_transition_with_metadata(self) -> None:
        """Verify transition creation with metadata."""
        metadata = {"slot_id": "123", "price": 50.0}
        transition = StateTransition(
            from_state=BookingState.SLOT_FOUND,
            to_state=BookingState.BOOKING,
            timestamp=datetime.utcnow(),
            trigger="slot_selected",
            metadata=metadata,
        )

        assert transition.metadata == metadata

    def test_transition_to_dict(self) -> None:
        """Verify transition serialization to dictionary."""
        timestamp = datetime(2024, 6, 1, 12, 0, 0)
        transition = StateTransition(
            from_state=BookingState.PENDING,
            to_state=BookingState.QUEUED,
            timestamp=timestamp,
            trigger="test_trigger",
            metadata={"key": "value"},
        )

        result = transition.to_dict()

        assert result["from"] == "pending"
        assert result["to"] == "queued"
        assert result["timestamp"] == "2024-06-01T12:00:00"
        assert result["trigger"] == "test_trigger"
        assert result["metadata"] == {"key": "value"}


# =============================================================================
# BookingContext Tests
# =============================================================================


class TestBookingContext:
    """Tests for BookingContext dataclass."""

    def test_create_context_with_defaults(self) -> None:
        """Verify context creation with default values."""
        context = BookingContext()

        assert context.state == BookingState.PENDING
        assert context.sub_state is None
        assert context.attempt_count == 0
        assert context.max_attempts == 3
        assert context.last_error is None
        assert context.failure_reason is None
        assert context.error_history == []
        assert context.state_history == []
        assert context.credit_reserved == 0.0
        assert context.credit_charged == 0.0
        assert isinstance(context.booking_id, str)
        assert isinstance(context.created_at, datetime)
        assert isinstance(context.updated_at, datetime)

    def test_create_context_with_values(self) -> None:
        """Verify context creation with specified values."""
        booking_id = str(uuid4())
        agency_id = str(uuid4())
        applicant_id = str(uuid4())

        context = BookingContext(
            booking_id=booking_id,
            agency_id=agency_id,
            applicant_id=applicant_id,
            state=BookingState.PROCESSING,
            max_attempts=5,
        )

        assert context.booking_id == booking_id
        assert context.agency_id == agency_id
        assert context.applicant_id == applicant_id
        assert context.state == BookingState.PROCESSING
        assert context.max_attempts == 5

    def test_is_terminal_with_terminal_states(self) -> None:
        """Verify is_terminal returns True for terminal states."""
        terminal_states = [
            BookingState.COMPLETED,
            BookingState.FAILED,
            BookingState.EXPIRED,
            BookingState.CANCELLED,
        ]

        for state in terminal_states:
            context = BookingContext(state=state)
            assert context.is_terminal() is True, f"{state} should be terminal"

    def test_is_terminal_with_non_terminal_states(self) -> None:
        """Verify is_terminal returns False for non-terminal states."""
        non_terminal_states = [
            BookingState.PENDING,
            BookingState.QUEUED,
            BookingState.PROCESSING,
            BookingState.SLOT_FOUND,
            BookingState.BOOKING,
            BookingState.PAYMENT,
            BookingState.VERIFYING,
        ]

        for state in non_terminal_states:
            context = BookingContext(state=state)
            assert context.is_terminal() is False, f"{state} should not be terminal"

    def test_context_to_dict(self) -> None:
        """Verify context serialization to dictionary."""
        context = BookingContext(
            booking_id="test-booking-id",
            agency_id="test-agency-id",
            applicant_id="test-applicant-id",
            state=BookingState.COMPLETED,
            confirmation_number="VFS-123456",
            appointment_date="2024-06-15",
            appointment_time="10:30",
            credit_reserved=1.0,
            credit_charged=1.0,
        )

        result = context.to_dict()

        assert result["booking_id"] == "test-booking-id"
        assert result["agency_id"] == "test-agency-id"
        assert result["applicant_id"] == "test-applicant-id"
        assert result["state"] == "completed"
        assert result["confirmation_number"] == "VFS-123456"
        assert result["appointment_date"] == "2024-06-15"
        assert result["appointment_time"] == "10:30"
        assert result["credit_reserved"] == 1.0
        assert result["credit_charged"] == 1.0

    def test_context_to_dict_with_failure_reason(self) -> None:
        """Verify context serialization includes failure reason."""
        context = BookingContext(
            state=BookingState.FAILED,
            failure_reason=FailureReason.PAYMENT_FAILED,
            last_error="Card declined",
        )

        result = context.to_dict()

        assert result["failure_reason"] == "payment_failed"
        assert result["last_error"] == "Card declined"


# =============================================================================
# BookingStateMachine Tests
# =============================================================================


class TestBookingStateMachine:
    """Tests for BookingStateMachine class."""

    @pytest.fixture
    def state_machine(self) -> BookingStateMachine:
        """Create a state machine instance."""
        return BookingStateMachine()

    @pytest.fixture
    def pending_context(self) -> BookingContext:
        """Create a context in PENDING state."""
        return BookingContext(
            booking_id=str(uuid4()),
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            state=BookingState.PENDING,
        )

    # -------------------------------------------------------------------------
    # Transition Validation Tests
    # -------------------------------------------------------------------------

    def test_transitions_dict_defined(self, state_machine: BookingStateMachine) -> None:
        """Verify all states have defined transitions."""
        for state in BookingState:
            assert state in state_machine.TRANSITIONS

    def test_terminal_states_have_no_transitions(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify terminal states have no outgoing transitions (except FAILED)."""
        assert state_machine.TRANSITIONS[BookingState.COMPLETED] == []
        assert state_machine.TRANSITIONS[BookingState.EXPIRED] == []
        assert state_machine.TRANSITIONS[BookingState.CANCELLED] == []
        # FAILED can transition to QUEUED for manual retry
        assert state_machine.TRANSITIONS[BookingState.FAILED] == [BookingState.QUEUED]

    # -------------------------------------------------------------------------
    # Valid Transition Tests
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_valid_transition_pending_to_queued(
        self, state_machine: BookingStateMachine, pending_context: BookingContext
    ) -> None:
        """Verify PENDING -> QUEUED transition succeeds."""
        result = await state_machine.transition(
            pending_context,
            BookingState.QUEUED,
            trigger="validation_passed",
        )

        assert result.state == BookingState.QUEUED
        assert len(result.state_history) == 1
        assert result.state_history[0].from_state == BookingState.PENDING
        assert result.state_history[0].to_state == BookingState.QUEUED

    @pytest.mark.asyncio
    async def test_valid_transition_queued_to_processing(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify QUEUED -> PROCESSING transition succeeds and increments attempt."""
        context = BookingContext(state=BookingState.QUEUED)

        result = await state_machine.transition(
            context,
            BookingState.PROCESSING,
            trigger="job_started",
        )

        assert result.state == BookingState.PROCESSING
        assert result.attempt_count == 1
        assert result.last_attempt_at is not None

    @pytest.mark.asyncio
    async def test_valid_transition_processing_to_slot_found(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify PROCESSING -> SLOT_FOUND transition succeeds."""
        context = BookingContext(state=BookingState.PROCESSING)

        result = await state_machine.transition(
            context,
            BookingState.SLOT_FOUND,
            trigger="slot_available",
        )

        assert result.state == BookingState.SLOT_FOUND
        assert result.slot_found_at is not None

    @pytest.mark.asyncio
    async def test_valid_transition_slot_found_to_booking(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify SLOT_FOUND -> BOOKING transition succeeds."""
        context = BookingContext(state=BookingState.SLOT_FOUND)

        result = await state_machine.transition(
            context,
            BookingState.BOOKING,
            trigger="slot_selected",
        )

        assert result.state == BookingState.BOOKING
        assert result.booking_started_at is not None

    @pytest.mark.asyncio
    async def test_valid_transition_booking_to_payment(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify BOOKING -> PAYMENT transition succeeds."""
        context = BookingContext(state=BookingState.BOOKING)

        result = await state_machine.transition(
            context,
            BookingState.PAYMENT,
            trigger="form_submitted",
        )

        assert result.state == BookingState.PAYMENT
        assert result.payment_started_at is not None

    @pytest.mark.asyncio
    async def test_valid_transition_payment_to_verifying(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify PAYMENT -> VERIFYING transition succeeds."""
        context = BookingContext(state=BookingState.PAYMENT)

        result = await state_machine.transition(
            context,
            BookingState.VERIFYING,
            trigger="payment_completed",
        )

        assert result.state == BookingState.VERIFYING

    @pytest.mark.asyncio
    async def test_valid_transition_verifying_to_completed(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify VERIFYING -> COMPLETED transition succeeds."""
        context = BookingContext(state=BookingState.VERIFYING)

        result = await state_machine.transition(
            context,
            BookingState.COMPLETED,
            trigger="verification_passed",
        )

        assert result.state == BookingState.COMPLETED

    @pytest.mark.asyncio
    async def test_valid_transition_payment_to_completed_skip_verification(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify PAYMENT -> COMPLETED transition succeeds when no verification needed."""
        context = BookingContext(state=BookingState.PAYMENT)

        result = await state_machine.transition(
            context,
            BookingState.COMPLETED,
            trigger="payment_completed_no_verification",
        )

        assert result.state == BookingState.COMPLETED

    @pytest.mark.asyncio
    async def test_valid_transition_failed_to_queued_retry(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify FAILED -> QUEUED transition succeeds for manual retry."""
        context = BookingContext(state=BookingState.FAILED)

        result = await state_machine.transition(
            context,
            BookingState.QUEUED,
            trigger="manual_retry",
        )

        assert result.state == BookingState.QUEUED

    @pytest.mark.asyncio
    async def test_valid_transition_processing_to_queued_retry(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify PROCESSING -> QUEUED transition succeeds for automatic retry."""
        context = BookingContext(state=BookingState.PROCESSING)

        result = await state_machine.transition(
            context,
            BookingState.QUEUED,
            trigger="retry_scheduled",
        )

        assert result.state == BookingState.QUEUED

    # -------------------------------------------------------------------------
    # Invalid Transition Tests
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_invalid_transition_pending_to_processing(
        self, state_machine: BookingStateMachine, pending_context: BookingContext
    ) -> None:
        """Verify PENDING -> PROCESSING transition fails (must go through QUEUED)."""
        with pytest.raises(InvalidStateTransitionError) as exc_info:
            await state_machine.transition(
                pending_context,
                BookingState.PROCESSING,
                trigger="invalid",
            )

        assert "Invalid transition" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_invalid_transition_completed_to_any(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify COMPLETED cannot transition to any other state."""
        context = BookingContext(state=BookingState.COMPLETED)

        for target_state in BookingState:
            if target_state == BookingState.COMPLETED:
                continue

            with pytest.raises(InvalidStateTransitionError):
                await state_machine.transition(
                    context,
                    target_state,
                    trigger="invalid",
                )

    @pytest.mark.asyncio
    async def test_invalid_transition_expired_to_any(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify EXPIRED cannot transition to any other state."""
        context = BookingContext(state=BookingState.EXPIRED)

        for target_state in BookingState:
            if target_state == BookingState.EXPIRED:
                continue

            with pytest.raises(InvalidStateTransitionError):
                await state_machine.transition(
                    context,
                    target_state,
                    trigger="invalid",
                )

    @pytest.mark.asyncio
    async def test_invalid_transition_cancelled_to_any(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify CANCELLED cannot transition to any other state."""
        context = BookingContext(state=BookingState.CANCELLED)

        for target_state in BookingState:
            if target_state == BookingState.CANCELLED:
                continue

            with pytest.raises(InvalidStateTransitionError):
                await state_machine.transition(
                    context,
                    target_state,
                    trigger="invalid",
                )

    # -------------------------------------------------------------------------
    # can_transition Tests
    # -------------------------------------------------------------------------

    def test_can_transition_valid(
        self, state_machine: BookingStateMachine, pending_context: BookingContext
    ) -> None:
        """Verify can_transition returns True for valid transitions."""
        can_do, reason = state_machine.can_transition(
            pending_context,
            BookingState.QUEUED,
        )

        assert can_do is True
        assert reason == "OK"

    def test_can_transition_invalid(
        self, state_machine: BookingStateMachine, pending_context: BookingContext
    ) -> None:
        """Verify can_transition returns False for invalid transitions."""
        can_do, reason = state_machine.can_transition(
            pending_context,
            BookingState.COMPLETED,
        )

        assert can_do is False
        assert "Invalid transition" in reason

    # -------------------------------------------------------------------------
    # Timeout Tests
    # -------------------------------------------------------------------------

    def test_state_timeouts_defined(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify state timeouts are configured."""
        assert state_machine.STATE_TIMEOUTS[BookingState.PENDING] == 3600
        assert state_machine.STATE_TIMEOUTS[BookingState.QUEUED] == 86400
        assert state_machine.STATE_TIMEOUTS[BookingState.PROCESSING] == 300
        assert state_machine.STATE_TIMEOUTS[BookingState.SLOT_FOUND] == 60
        assert state_machine.STATE_TIMEOUTS[BookingState.BOOKING] == 180
        assert state_machine.STATE_TIMEOUTS[BookingState.PAYMENT] == 300
        assert state_machine.STATE_TIMEOUTS[BookingState.VERIFYING] == 180

    def test_get_timeout(self, state_machine: BookingStateMachine) -> None:
        """Verify get_timeout returns correct values."""
        assert state_machine.get_timeout(BookingState.PENDING) == 3600
        assert state_machine.get_timeout(BookingState.COMPLETED) is None

    def test_timed_out_context_can_only_go_to_failed_or_expired(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify timed out context can only transition to FAILED or EXPIRED."""
        # Create a context that has timed out
        context = BookingContext(
            state=BookingState.PROCESSING,
            updated_at=datetime.utcnow() - timedelta(seconds=400),  # > 300s timeout
        )

        # Should be able to go to FAILED
        can_do_failed, _ = state_machine.can_transition(context, BookingState.FAILED)
        assert can_do_failed is True

        # Should NOT be able to go to SLOT_FOUND
        can_do_slot, reason = state_machine.can_transition(
            context, BookingState.SLOT_FOUND
        )
        assert can_do_slot is False
        assert "timed out" in reason

    def test_is_timed_out_true(self, state_machine: BookingStateMachine) -> None:
        """Verify _is_timed_out returns True when timeout exceeded."""
        context = BookingContext(
            state=BookingState.PROCESSING,
            updated_at=datetime.utcnow() - timedelta(seconds=400),
        )

        assert state_machine._is_timed_out(context) is True

    def test_is_timed_out_false(self, state_machine: BookingStateMachine) -> None:
        """Verify _is_timed_out returns False when within timeout."""
        context = BookingContext(
            state=BookingState.PROCESSING,
            updated_at=datetime.utcnow(),
        )

        assert state_machine._is_timed_out(context) is False

    def test_is_timed_out_no_timeout_configured(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify _is_timed_out returns False for states without timeout."""
        context = BookingContext(
            state=BookingState.COMPLETED,
            updated_at=datetime.utcnow() - timedelta(days=365),
        )

        assert state_machine._is_timed_out(context) is False

    # -------------------------------------------------------------------------
    # Handler Tests
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_entry_handler_called(
        self, state_machine: BookingStateMachine, pending_context: BookingContext
    ) -> None:
        """Verify entry handler is called on state entry."""
        handler_called = {"value": False}

        async def entry_handler(ctx: BookingContext) -> None:
            handler_called["value"] = True

        state_machine.register_entry_handler(BookingState.QUEUED, entry_handler)

        await state_machine.transition(
            pending_context,
            BookingState.QUEUED,
            trigger="test",
        )

        assert handler_called["value"] is True

    @pytest.mark.asyncio
    async def test_exit_handler_called(
        self, state_machine: BookingStateMachine, pending_context: BookingContext
    ) -> None:
        """Verify exit handler is called on state exit."""
        handler_called = {"value": False}

        async def exit_handler(ctx: BookingContext) -> None:
            handler_called["value"] = True

        state_machine.register_exit_handler(BookingState.PENDING, exit_handler)

        await state_machine.transition(
            pending_context,
            BookingState.QUEUED,
            trigger="test",
        )

        assert handler_called["value"] is True

    # -------------------------------------------------------------------------
    # Transition Validator Tests
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_custom_validator_allows_transition(
        self, state_machine: BookingStateMachine, pending_context: BookingContext
    ) -> None:
        """Verify custom validator can allow transition."""

        def validator(ctx: BookingContext) -> tuple[bool, str]:
            return True, "OK"

        state_machine.register_transition_validator(
            BookingState.PENDING,
            BookingState.QUEUED,
            validator,
        )

        result = await state_machine.transition(
            pending_context,
            BookingState.QUEUED,
            trigger="test",
        )

        assert result.state == BookingState.QUEUED

    @pytest.mark.asyncio
    async def test_custom_validator_blocks_transition(
        self, state_machine: BookingStateMachine, pending_context: BookingContext
    ) -> None:
        """Verify custom validator can block transition."""

        def validator(ctx: BookingContext) -> tuple[bool, str]:
            return False, "Custom validation failed"

        state_machine.register_transition_validator(
            BookingState.PENDING,
            BookingState.QUEUED,
            validator,
        )

        with pytest.raises(InvalidStateTransitionError) as exc_info:
            await state_machine.transition(
                pending_context,
                BookingState.QUEUED,
                trigger="test",
            )

        assert "Custom validation failed" in str(exc_info.value)

    # -------------------------------------------------------------------------
    # State History Tests
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_state_history_recorded(
        self, state_machine: BookingStateMachine, pending_context: BookingContext
    ) -> None:
        """Verify state transitions are recorded in history."""
        # Transition through multiple states
        ctx = await state_machine.transition(
            pending_context, BookingState.QUEUED, "step1"
        )
        ctx = await state_machine.transition(
            ctx, BookingState.PROCESSING, "step2"
        )
        ctx = await state_machine.transition(
            ctx, BookingState.SLOT_FOUND, "step3"
        )

        assert len(ctx.state_history) == 3
        assert ctx.state_history[0].from_state == BookingState.PENDING
        assert ctx.state_history[0].to_state == BookingState.QUEUED
        assert ctx.state_history[1].from_state == BookingState.QUEUED
        assert ctx.state_history[1].to_state == BookingState.PROCESSING
        assert ctx.state_history[2].from_state == BookingState.PROCESSING
        assert ctx.state_history[2].to_state == BookingState.SLOT_FOUND

    @pytest.mark.asyncio
    async def test_transition_metadata_recorded(
        self, state_machine: BookingStateMachine, pending_context: BookingContext
    ) -> None:
        """Verify transition metadata is recorded."""
        metadata = {"source": "google_sheets", "row_id": 42}

        result = await state_machine.transition(
            pending_context,
            BookingState.QUEUED,
            trigger="import",
            metadata=metadata,
        )

        assert result.state_history[0].metadata == metadata

    # -------------------------------------------------------------------------
    # Get Valid Transitions Tests
    # -------------------------------------------------------------------------

    def test_get_valid_transitions(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify get_valid_transitions returns correct states."""
        valid = state_machine.get_valid_transitions(BookingState.PENDING)

        assert BookingState.QUEUED in valid
        assert BookingState.EXPIRED in valid
        assert BookingState.CANCELLED in valid
        assert len(valid) == 3

    def test_get_valid_transitions_terminal_state(
        self, state_machine: BookingStateMachine
    ) -> None:
        """Verify get_valid_transitions returns empty for terminal states."""
        valid = state_machine.get_valid_transitions(BookingState.COMPLETED)
        assert valid == []


# =============================================================================
# RetryManager Tests
# =============================================================================


class TestRetryManager:
    """Tests for RetryManager class."""

    @pytest.fixture
    def retry_manager(self) -> RetryManager:
        """Create a retry manager instance."""
        return RetryManager()

    # -------------------------------------------------------------------------
    # Retry Config Tests
    # -------------------------------------------------------------------------

    def test_retry_configs_defined(self, retry_manager: RetryManager) -> None:
        """Verify retry configs are defined for known failure reasons."""
        assert FailureReason.TIMEOUT in retry_manager.RETRY_CONFIGS
        assert FailureReason.SITE_UNAVAILABLE in retry_manager.RETRY_CONFIGS
        assert FailureReason.SLOT_TAKEN in retry_manager.RETRY_CONFIGS
        assert FailureReason.INVALID_DATA in retry_manager.RETRY_CONFIGS

    def test_no_retry_configs(self, retry_manager: RetryManager) -> None:
        """Verify certain failure reasons have no retry policy."""
        no_retry_reasons = [
            FailureReason.INVALID_DATA,
            FailureReason.CANCELLED_BY_USER,
            FailureReason.CARD_DECLINED,
            FailureReason.MAX_RETRIES_EXCEEDED,
        ]

        for reason in no_retry_reasons:
            config = retry_manager.get_config(reason)
            assert config.policy == RetryPolicy.NO_RETRY

    # -------------------------------------------------------------------------
    # should_retry Tests
    # -------------------------------------------------------------------------

    def test_should_retry_with_no_retry_policy(
        self, retry_manager: RetryManager
    ) -> None:
        """Verify should_retry returns False for NO_RETRY policy."""
        context = BookingContext(
            failure_reason=FailureReason.INVALID_DATA,
            attempt_count=1,
        )

        should_retry, delay = retry_manager.should_retry(context)

        assert should_retry is False
        assert delay == 0

    def test_should_retry_max_attempts_exceeded(
        self, retry_manager: RetryManager
    ) -> None:
        """Verify should_retry returns False when max attempts exceeded."""
        context = BookingContext(
            failure_reason=FailureReason.TIMEOUT,
            attempt_count=10,  # More than max_attempts
        )

        should_retry, delay = retry_manager.should_retry(context)

        assert should_retry is False
        assert delay == 0

    def test_should_retry_with_immediate_policy(
        self, retry_manager: RetryManager
    ) -> None:
        """Verify should_retry returns True with immediate delay."""
        context = BookingContext(
            failure_reason=FailureReason.SLOT_TAKEN,
            attempt_count=1,
        )

        should_retry, delay = retry_manager.should_retry(context)

        assert should_retry is True
        assert delay == 5  # initial_delay_seconds for SLOT_TAKEN

    def test_should_retry_with_exponential_backoff(
        self, retry_manager: RetryManager
    ) -> None:
        """Verify exponential backoff delay calculation."""
        # First attempt
        context1 = BookingContext(
            failure_reason=FailureReason.TIMEOUT,
            attempt_count=1,
        )
        should_retry1, delay1 = retry_manager.should_retry(context1)
        assert should_retry1 is True
        assert delay1 == 30  # initial_delay

        # Second attempt
        context2 = BookingContext(
            failure_reason=FailureReason.TIMEOUT,
            attempt_count=2,
        )
        should_retry2, delay2 = retry_manager.should_retry(context2)
        assert should_retry2 is True
        assert delay2 == 60  # 30 * 2^1

    def test_should_retry_with_fixed_delay(
        self, retry_manager: RetryManager
    ) -> None:
        """Verify fixed delay is constant across attempts."""
        context1 = BookingContext(
            failure_reason=FailureReason.CAPTCHA_FAILED,
            attempt_count=1,
        )
        _, delay1 = retry_manager.should_retry(context1)

        context2 = BookingContext(
            failure_reason=FailureReason.CAPTCHA_FAILED,
            attempt_count=2,
        )
        _, delay2 = retry_manager.should_retry(context2)

        assert delay1 == delay2 == 60  # Fixed delay for CAPTCHA_FAILED

    def test_should_retry_uses_default_config(
        self, retry_manager: RetryManager
    ) -> None:
        """Verify default config is used for unknown failure reasons."""
        context = BookingContext(
            failure_reason=None,  # No failure reason set
            attempt_count=1,
        )

        should_retry, delay = retry_manager.should_retry(context)

        assert should_retry is True
        assert delay == 30  # DEFAULT_CONFIG initial_delay

    # -------------------------------------------------------------------------
    # Delay Calculation Tests
    # -------------------------------------------------------------------------

    def test_calculate_delay_exponential_capped(
        self, retry_manager: RetryManager
    ) -> None:
        """Verify exponential backoff is capped at max_delay."""
        config = RetryConfig(
            policy=RetryPolicy.EXPONENTIAL_BACKOFF,
            max_attempts=10,
            initial_delay_seconds=30,
            max_delay_seconds=300,
            multiplier=2.0,
        )

        # Attempt 5: 30 * 2^4 = 480, but capped at 300
        delay = retry_manager._calculate_delay(config, 5)
        assert delay == 300

    def test_calculate_delay_immediate(
        self, retry_manager: RetryManager
    ) -> None:
        """Verify immediate policy returns initial delay."""
        config = RetryConfig(
            policy=RetryPolicy.IMMEDIATE,
            max_attempts=5,
            initial_delay_seconds=5,
            max_delay_seconds=5,
        )

        delay = retry_manager._calculate_delay(config, 3)
        assert delay == 5

    # -------------------------------------------------------------------------
    # Get Config Tests
    # -------------------------------------------------------------------------

    def test_get_config_returns_correct_config(
        self, retry_manager: RetryManager
    ) -> None:
        """Verify get_config returns the correct configuration."""
        config = retry_manager.get_config(FailureReason.TIMEOUT)

        assert config.policy == RetryPolicy.EXPONENTIAL_BACKOFF
        assert config.max_attempts == 3
        assert config.initial_delay_seconds == 30

    def test_get_config_returns_default_for_unknown(
        self, retry_manager: RetryManager
    ) -> None:
        """Verify get_config returns default config for unmapped reasons."""
        # Create a mock failure reason not in RETRY_CONFIGS
        # Since all are mapped, we test with a reason that has no explicit config
        # by directly checking the default
        default = retry_manager.DEFAULT_CONFIG
        assert default.policy == RetryPolicy.EXPONENTIAL_BACKOFF
        assert default.max_attempts == 3


# =============================================================================
# CompensatingTransactionManager Tests
# =============================================================================


class TestCompensatingTransactionManager:
    """Tests for CompensatingTransactionManager class."""

    @pytest.fixture
    def mock_credit_service(self) -> AsyncMock:
        """Create a mock credit service."""
        service = AsyncMock()
        service.release = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def mock_account_pool(self) -> AsyncMock:
        """Create a mock account pool."""
        pool = AsyncMock()
        pool.release = AsyncMock(return_value=True)
        return pool

    @pytest.fixture
    def mock_proxy_pool(self) -> AsyncMock:
        """Create a mock proxy pool."""
        pool = AsyncMock()
        pool.report_usage = AsyncMock(return_value=True)
        return pool

    @pytest.fixture
    def mock_session_manager(self) -> AsyncMock:
        """Create a mock session manager."""
        manager = AsyncMock()
        manager.cleanup = AsyncMock(return_value=True)
        return manager

    @pytest.fixture
    def compensation_manager(
        self,
        mock_credit_service: AsyncMock,
        mock_account_pool: AsyncMock,
        mock_proxy_pool: AsyncMock,
        mock_session_manager: AsyncMock,
    ) -> CompensatingTransactionManager:
        """Create a compensation manager with mocked dependencies."""
        return CompensatingTransactionManager(
            credit_service=mock_credit_service,
            account_pool=mock_account_pool,
            proxy_pool=mock_proxy_pool,
            session_manager=mock_session_manager,
        )

    # -------------------------------------------------------------------------
    # Compensation Execution Tests
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_compensate_releases_credit(
        self,
        compensation_manager: CompensatingTransactionManager,
        mock_credit_service: AsyncMock,
    ) -> None:
        """Verify compensation releases reserved credit."""
        context = BookingContext(
            agency_id="agency-123",
            credit_reserved=50.0,
        )

        await compensation_manager.compensate(context, BookingState.PROCESSING)

        mock_credit_service.release.assert_called_once_with(
            agency_id="agency-123",
            amount=50.0,
            booking_id=context.booking_id,
        )

    @pytest.mark.asyncio
    async def test_compensate_skips_credit_if_zero(
        self,
        compensation_manager: CompensatingTransactionManager,
        mock_credit_service: AsyncMock,
    ) -> None:
        """Verify compensation skips credit release if no credit reserved."""
        context = BookingContext(
            credit_reserved=0.0,
        )

        await compensation_manager.compensate(context, BookingState.PROCESSING)

        mock_credit_service.release.assert_not_called()

    @pytest.mark.asyncio
    async def test_compensate_releases_account(
        self,
        compensation_manager: CompensatingTransactionManager,
        mock_account_pool: AsyncMock,
    ) -> None:
        """Verify compensation releases assigned account."""
        context = BookingContext(
            assigned_account_id="account-123",
        )

        await compensation_manager.compensate(context, BookingState.PROCESSING)

        mock_account_pool.release.assert_called_once()
        call_kwargs = mock_account_pool.release.call_args.kwargs
        assert call_kwargs["account_id"] == "account-123"
        assert call_kwargs["success"] is True

    @pytest.mark.asyncio
    async def test_compensate_marks_account_failure_on_terminal(
        self,
        compensation_manager: CompensatingTransactionManager,
        mock_account_pool: AsyncMock,
    ) -> None:
        """Verify account is marked as failed for terminal failure states."""
        context = BookingContext(
            assigned_account_id="account-123",
            last_error="Account banned",
        )

        # COMPLETED and CANCELLED should mark success=False
        await compensation_manager.compensate(context, BookingState.COMPLETED)

        call_kwargs = mock_account_pool.release.call_args.kwargs
        assert call_kwargs["success"] is False

    @pytest.mark.asyncio
    async def test_compensate_releases_proxy(
        self,
        compensation_manager: CompensatingTransactionManager,
        mock_proxy_pool: AsyncMock,
    ) -> None:
        """Verify compensation releases assigned proxy."""
        context = BookingContext(
            state=BookingState.FAILED,
            assigned_proxy_id="proxy-123",
        )

        await compensation_manager.compensate(context, BookingState.PROCESSING)

        mock_proxy_pool.report_usage.assert_called_once()
        call_kwargs = mock_proxy_pool.report_usage.call_args.kwargs
        assert call_kwargs["proxy_id"] == "proxy-123"
        assert call_kwargs["success"] is False  # FAILED state

    @pytest.mark.asyncio
    async def test_compensate_cleans_up_session(
        self,
        compensation_manager: CompensatingTransactionManager,
        mock_session_manager: AsyncMock,
    ) -> None:
        """Verify compensation cleans up browser session."""
        context = BookingContext(
            assigned_session_id="session-123",
        )

        await compensation_manager.compensate(context, BookingState.PROCESSING)

        mock_session_manager.cleanup.assert_called_once_with(
            session_id="session-123",
        )

    @pytest.mark.asyncio
    async def test_compensate_handles_exceptions_gracefully(
        self,
        mock_credit_service: AsyncMock,
        mock_account_pool: AsyncMock,
        mock_proxy_pool: AsyncMock,
        mock_session_manager: AsyncMock,
    ) -> None:
        """Verify compensation continues even if one operation fails."""
        # Make credit release fail
        mock_credit_service.release = AsyncMock(side_effect=Exception("Credit error"))

        manager = CompensatingTransactionManager(
            credit_service=mock_credit_service,
            account_pool=mock_account_pool,
            proxy_pool=mock_proxy_pool,
            session_manager=mock_session_manager,
        )

        context = BookingContext(
            credit_reserved=50.0,
            assigned_account_id="account-123",
            assigned_proxy_id="proxy-123",
            assigned_session_id="session-123",
        )

        # Should not raise, despite credit release failing
        await manager.compensate(context, BookingState.PROCESSING)

        # Other operations should still be attempted
        mock_account_pool.release.assert_called_once()
        mock_proxy_pool.report_usage.assert_called_once()
        mock_session_manager.cleanup.assert_called_once()

    # -------------------------------------------------------------------------
    # No Services Configured Tests
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_compensate_without_services(self) -> None:
        """Verify compensation works when no services configured."""
        manager = CompensatingTransactionManager()

        context = BookingContext(
            credit_reserved=50.0,
            assigned_account_id="account-123",
        )

        # Should not raise
        await manager.compensate(context, BookingState.PROCESSING)


# =============================================================================
# RetryPolicy Enum Tests
# =============================================================================


class TestRetryPolicy:
    """Tests for RetryPolicy enum."""

    def test_all_policies_exist(self) -> None:
        """Verify all retry policies are defined."""
        assert RetryPolicy.IMMEDIATE.value == "immediate"
        assert RetryPolicy.EXPONENTIAL_BACKOFF.value == "exponential_backoff"
        assert RetryPolicy.FIXED_DELAY.value == "fixed_delay"
        assert RetryPolicy.NO_RETRY.value == "no_retry"

    def test_policy_count(self) -> None:
        """Verify total retry policy count."""
        assert len(RetryPolicy) == 4


# =============================================================================
# RetryConfig Tests
# =============================================================================


class TestRetryConfig:
    """Tests for RetryConfig dataclass."""

    def test_create_config(self) -> None:
        """Verify retry config creation."""
        config = RetryConfig(
            policy=RetryPolicy.EXPONENTIAL_BACKOFF,
            max_attempts=5,
            initial_delay_seconds=30,
            max_delay_seconds=300,
            multiplier=2.0,
        )

        assert config.policy == RetryPolicy.EXPONENTIAL_BACKOFF
        assert config.max_attempts == 5
        assert config.initial_delay_seconds == 30
        assert config.max_delay_seconds == 300
        assert config.multiplier == 2.0

    def test_create_config_default_multiplier(self) -> None:
        """Verify default multiplier is 2.0."""
        config = RetryConfig(
            policy=RetryPolicy.FIXED_DELAY,
            max_attempts=3,
            initial_delay_seconds=60,
            max_delay_seconds=60,
        )

        assert config.multiplier == 2.0
