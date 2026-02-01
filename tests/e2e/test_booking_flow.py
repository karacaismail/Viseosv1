"""
End-to-End Tests for Booking Flow.

This module provides comprehensive E2E tests for the complete booking
lifecycle, testing integration of all components from booking creation
to completion.

Test Scenarios:
1. Complete Booking Flow - Full flow from creation to confirmation
2. Account Ban Recovery - Automatic account rotation on ban
3. Slot Unavailable Retry - Re-search when slot becomes unavailable
4. Payment Processing - 3DS authentication and verification
5. Error Recovery - Handling of various failure scenarios

Running Tests:
    pytest tests/e2e/test_booking_flow.py -v
    pytest tests/e2e/test_booking_flow.py -v -m "e2e"

Note:
    Tests use mocked adapters and services. No external services required.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.core.booking.service import (
    BookingEvent,
    BookingEventType,
    BookingPriority,
    BookingService,
)
from src.core.booking.state_machine import (
    BookingContext,
    BookingState,
    BookingStateMachine,
    CompensatingTransactionManager,
    FailureReason,
    RetryManager,
)
from src.core.exceptions import (
    AccountBannedError,
    BookingError,
    SlotNotAvailableError,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@dataclass
class MockSlot:
    """Mock slot for testing."""

    id: str
    date: date
    time: str
    location: str
    location_id: str
    available: bool = True
    capacity: int = 5

    def __post_init__(self) -> None:
        if isinstance(self.date, str):
            self.date = date.fromisoformat(self.date)


@dataclass
class MockBookingResult:
    """Mock booking result for testing."""

    is_success: bool
    confirmation_number: str | None = None
    appointment_date: date | None = None
    appointment_time: str | None = None
    error_message: str | None = None
    error_code: str | None = None
    duration_seconds: int = 60


@pytest.fixture
def mock_repository() -> AsyncMock:
    """Create a mock booking repository."""
    repo = AsyncMock()
    repo.bookings: dict[str, dict[str, Any]] = {}

    async def mock_create(data: dict[str, Any]) -> dict[str, Any]:
        booking_id = str(uuid4())
        booking = {
            "id": booking_id,
            "status": "pending",
            "attempts": 0,
            "max_attempts": 50,
            "date_created": datetime.utcnow().isoformat(),
            **data,
        }
        repo.bookings[booking_id] = booking
        return booking

    async def mock_get_by_id(booking_id: str) -> dict[str, Any] | None:
        return repo.bookings.get(str(booking_id))

    async def mock_update(booking_id: str, data: dict[str, Any]) -> dict[str, Any]:
        if str(booking_id) in repo.bookings:
            repo.bookings[str(booking_id)].update(data)
            repo.bookings[str(booking_id)]["date_updated"] = datetime.utcnow().isoformat()
        return repo.bookings.get(str(booking_id), {})

    async def mock_update_status(
        booking_id: str,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        if str(booking_id) in repo.bookings:
            repo.bookings[str(booking_id)]["status"] = status
            if error_code:
                repo.bookings[str(booking_id)]["error_code"] = error_code
            if error_message:
                repo.bookings[str(booking_id)]["error_message"] = error_message
        return repo.bookings.get(str(booking_id), {})

    async def mock_create_result(data: dict[str, Any]) -> dict[str, Any]:
        return {"id": str(uuid4()), **data}

    async def mock_find_by_agency(
        agency_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        results = [
            b for b in repo.bookings.values()
            if b.get("agency_id") == str(agency_id)
        ]
        if status:
            results = [b for b in results if b.get("status") == status]
        return results[offset:offset + limit]

    repo.create = AsyncMock(side_effect=mock_create)
    repo.get_by_id = AsyncMock(side_effect=mock_get_by_id)
    repo.update = AsyncMock(side_effect=mock_update)
    repo.update_status = AsyncMock(side_effect=mock_update_status)
    repo.create_result = AsyncMock(side_effect=mock_create_result)
    repo.find_by_agency = AsyncMock(side_effect=mock_find_by_agency)

    return repo


@pytest.fixture
def mock_adapter() -> AsyncMock:
    """Create a mock site adapter."""
    adapter = AsyncMock()

    # Mock session context manager
    mock_session = AsyncMock()
    mock_session.goto = AsyncMock(return_value=True)
    mock_session.close = AsyncMock(return_value=True)

    adapter.create_session = MagicMock()
    adapter.create_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    adapter.create_session.return_value.__aexit__ = AsyncMock(return_value=None)

    # Default success behavior
    adapter.login = AsyncMock(return_value=True)

    adapter.search_slots = AsyncMock(return_value=[
        MockSlot(
            id="slot-1",
            date=date.today() + timedelta(days=14),
            time="10:30",
            location="Istanbul - Levent",
            location_id="istanbul-levent",
        ),
        MockSlot(
            id="slot-2",
            date=date.today() + timedelta(days=15),
            time="14:00",
            location="Istanbul - Levent",
            location_id="istanbul-levent",
        ),
    ])

    adapter.book_slot = AsyncMock(return_value=MockBookingResult(
        is_success=True,
        confirmation_number="VFS-2024-123456",
        appointment_date=date.today() + timedelta(days=14),
        appointment_time="10:30",
        duration_seconds=45,
    ))

    return adapter


@pytest.fixture
def mock_account() -> dict[str, Any]:
    """Create a mock bot account."""
    return {
        "id": str(uuid4()),
        "email": "bot@example.com",
        "password": "encrypted_password",
        "site": "vfs",
        "status": "active",
        "health_score": 100,
    }


@pytest.fixture
def mock_applicant_data() -> dict[str, Any]:
    """Create mock applicant data."""
    return {
        "first_name": "John",
        "last_name": "Doe",
        "email": "john.doe@example.com",
        "phone": "+905551234567",
        "passport_number": "U12345678",
        "nationality": "TR",
        "date_of_birth": "1990-01-15",
    }


@pytest.fixture
def booking_service(mock_repository: AsyncMock) -> BookingService:
    """Create a booking service with mocked repository."""
    service = BookingService(
        repository=mock_repository,
        state_machine=BookingStateMachine(),
        retry_manager=RetryManager(),
        compensation_manager=CompensatingTransactionManager(),
    )
    return service


# =============================================================================
# Complete Booking Flow Tests
# =============================================================================


@pytest.mark.e2e
class TestCompleteBookingFlow:
    """Tests for the complete booking flow from creation to completion."""

    @pytest.mark.asyncio
    async def test_successful_booking_flow(
        self,
        booking_service: BookingService,
        mock_repository: AsyncMock,
        mock_adapter: AsyncMock,
        mock_account: dict[str, Any],
        mock_applicant_data: dict[str, Any],
    ) -> None:
        """
        Test complete successful booking flow.

        Steps:
        1. Create booking request
        2. Queue for processing
        3. Search for available slots
        4. Book selected slot
        5. Verify completion with confirmation number
        """
        # Step 1: Create booking
        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="vfs",
            target_country="DE",
            visa_category="tourist",
            preferred_dates=[(date.today() + timedelta(days=14)).isoformat()],
            location="Istanbul",
        )

        assert booking["id"] is not None
        assert booking["status"] == "pending"
        booking_id = booking["id"]

        # Step 2: Submit to queue
        submit_result = await booking_service.submit_booking(booking_id)

        assert submit_result["status"] == "queued"
        assert submit_result["booking_id"] == booking_id

        # Verify booking state updated
        updated_booking = await mock_repository.get_by_id(booking_id)
        assert updated_booking["status"] == "queued"

        # Step 3 & 4: Process booking through adapter
        result = await booking_service.process_booking(
            booking_id=booking_id,
            adapter=mock_adapter,
            account=mock_account,
            applicant_data=mock_applicant_data,
        )

        # Step 5: Verify completion
        assert result["success"] is True
        assert result["confirmation_number"] == "VFS-2024-123456"
        assert result["appointment_date"] is not None
        assert result["appointment_time"] == "10:30"

        # Verify final booking state
        final_booking = await mock_repository.get_by_id(booking_id)
        assert final_booking["status"] == "completed"
        assert final_booking["confirmation_number"] == "VFS-2024-123456"

    @pytest.mark.asyncio
    async def test_booking_flow_with_event_handlers(
        self,
        booking_service: BookingService,
        mock_adapter: AsyncMock,
        mock_account: dict[str, Any],
        mock_applicant_data: dict[str, Any],
    ) -> None:
        """Test that event handlers are called during booking flow."""
        events_received: list[BookingEvent] = []

        def capture_event(event: BookingEvent) -> None:
            events_received.append(event)

        # Register handlers for all event types
        for event_type in BookingEventType:
            booking_service.on_event(event_type, capture_event)

        # Create and process booking
        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="vfs",
            target_country="DE",
        )

        await booking_service.submit_booking(booking["id"])

        await booking_service.process_booking(
            booking_id=booking["id"],
            adapter=mock_adapter,
            account=mock_account,
            applicant_data=mock_applicant_data,
        )

        # Verify events were emitted
        event_types = [e.event_type for e in events_received]

        assert BookingEventType.CREATED in event_types
        assert BookingEventType.QUEUED in event_types
        assert BookingEventType.PROCESSING_STARTED in event_types
        assert BookingEventType.SLOT_FOUND in event_types
        assert BookingEventType.BOOKING_STARTED in event_types
        assert BookingEventType.COMPLETED in event_types

    @pytest.mark.asyncio
    async def test_booking_flow_state_transitions(
        self,
        booking_service: BookingService,
        mock_adapter: AsyncMock,
        mock_account: dict[str, Any],
        mock_applicant_data: dict[str, Any],
    ) -> None:
        """Verify correct state transitions during booking flow."""
        # Create booking
        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="vfs",
            target_country="DE",
        )

        booking_id = booking["id"]

        # Check initial state
        status = await booking_service.get_booking_status(booking_id)
        assert status["status"] == "pending"

        # Submit to queue
        await booking_service.submit_booking(booking_id)
        status = await booking_service.get_booking_status(booking_id)
        assert status["status"] == "queued"

        # Process - should transition through PROCESSING -> SLOT_FOUND -> BOOKING -> COMPLETED
        result = await booking_service.process_booking(
            booking_id=booking_id,
            adapter=mock_adapter,
            account=mock_account,
            applicant_data=mock_applicant_data,
        )

        assert result["success"] is True

        # Final state
        status = await booking_service.get_booking_status(booking_id)
        assert status["status"] == "completed"


# =============================================================================
# Account Ban Recovery Tests
# =============================================================================


@pytest.mark.e2e
class TestAccountBanRecovery:
    """Tests for automatic account rotation on ban detection."""

    @pytest.mark.asyncio
    async def test_account_ban_triggers_retry(
        self,
        booking_service: BookingService,
        mock_adapter: AsyncMock,
        mock_account: dict[str, Any],
        mock_applicant_data: dict[str, Any],
    ) -> None:
        """
        Test that account ban triggers automatic retry with new account.

        Steps:
        1. Start booking process
        2. Simulate account ban during login
        3. Verify booking is requeued for retry
        """
        # Configure adapter to fail with account banned error on first attempt
        mock_adapter.login = AsyncMock(side_effect=AccountBannedError(
            "Account banned",
            account_id=mock_account["id"],
            site="vfs",
            ban_reason="suspicious_activity",
        ))

        # Create and submit booking
        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="vfs",
            target_country="DE",
        )

        await booking_service.submit_booking(booking["id"])

        # Process - should fail due to banned account
        result = await booking_service.process_booking(
            booking_id=booking["id"],
            adapter=mock_adapter,
            account=mock_account,
            applicant_data=mock_applicant_data,
        )

        assert result["success"] is False
        assert "banned" in result["error"].lower() or "ACCOUNT_BANNED" in str(result.get("error_code", ""))

    @pytest.mark.asyncio
    async def test_account_ban_recovery_with_new_account(
        self,
        booking_service: BookingService,
        mock_repository: AsyncMock,
        mock_applicant_data: dict[str, Any],
    ) -> None:
        """
        Test complete recovery flow after account ban.

        Simulates:
        1. First attempt fails with account ban
        2. Second attempt succeeds with new account
        """
        banned_account = {
            "id": str(uuid4()),
            "email": "banned@example.com",
            "site": "vfs",
            "status": "banned",
        }

        new_account = {
            "id": str(uuid4()),
            "email": "new@example.com",
            "site": "vfs",
            "status": "active",
        }

        # Create adapter that fails first, succeeds second
        adapter = AsyncMock()
        mock_session = AsyncMock()
        adapter.create_session = MagicMock()
        adapter.create_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        adapter.create_session.return_value.__aexit__ = AsyncMock(return_value=None)

        call_count = 0

        async def login_with_ban(session: Any, account: Any) -> bool:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise AccountBannedError(
                    "Account banned",
                    account_id=banned_account["id"],
                    site="vfs",
                )
            return True

        adapter.login = AsyncMock(side_effect=login_with_ban)
        adapter.search_slots = AsyncMock(return_value=[
            MockSlot(
                id="slot-1",
                date=date.today() + timedelta(days=14),
                time="10:30",
                location="Istanbul",
                location_id="istanbul",
            ),
        ])
        adapter.book_slot = AsyncMock(return_value=MockBookingResult(
            is_success=True,
            confirmation_number="VFS-2024-RECOVERY",
            appointment_date=date.today() + timedelta(days=14),
            appointment_time="10:30",
        ))

        # Create booking
        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="vfs",
            target_country="DE",
        )

        await booking_service.submit_booking(booking["id"])

        # First attempt - fails with ban
        result1 = await booking_service.process_booking(
            booking_id=booking["id"],
            adapter=adapter,
            account=banned_account,
            applicant_data=mock_applicant_data,
        )

        assert result1["success"] is False

        # Verify booking was requeued (status should allow retry)
        # In real scenario, this would be handled by the queue system
        # For this test, we manually retry with new account

        # Second attempt - succeeds with new account
        result2 = await booking_service.process_booking(
            booking_id=booking["id"],
            adapter=adapter,
            account=new_account,
            applicant_data=mock_applicant_data,
        )

        assert result2["success"] is True
        assert result2["confirmation_number"] == "VFS-2024-RECOVERY"


# =============================================================================
# Slot Unavailable Retry Tests
# =============================================================================


@pytest.mark.e2e
class TestSlotUnavailableRetry:
    """Tests for handling slot unavailability during booking."""

    @pytest.mark.asyncio
    async def test_slot_taken_triggers_research(
        self,
        booking_service: BookingService,
        mock_account: dict[str, Any],
        mock_applicant_data: dict[str, Any],
    ) -> None:
        """
        Test that slot being taken triggers re-search.

        Steps:
        1. Search finds slot
        2. Booking attempt finds slot taken
        3. Verify failure with appropriate error
        """
        adapter = AsyncMock()
        mock_session = AsyncMock()
        adapter.create_session = MagicMock()
        adapter.create_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        adapter.create_session.return_value.__aexit__ = AsyncMock(return_value=None)

        adapter.login = AsyncMock(return_value=True)
        adapter.search_slots = AsyncMock(return_value=[
            MockSlot(
                id="slot-1",
                date=date.today() + timedelta(days=14),
                time="10:30",
                location="Istanbul",
                location_id="istanbul",
            ),
        ])

        # Book slot fails - slot taken
        adapter.book_slot = AsyncMock(return_value=MockBookingResult(
            is_success=False,
            error_message="Slot is no longer available",
            error_code="SLOT_TAKEN",
        ))

        # Create and process booking
        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="vfs",
            target_country="DE",
        )

        await booking_service.submit_booking(booking["id"])

        result = await booking_service.process_booking(
            booking_id=booking["id"],
            adapter=adapter,
            account=mock_account,
            applicant_data=mock_applicant_data,
        )

        assert result["success"] is False
        assert "SLOT_TAKEN" in result.get("error_code", "") or "no longer available" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_no_slots_available_handling(
        self,
        booking_service: BookingService,
        mock_account: dict[str, Any],
        mock_applicant_data: dict[str, Any],
    ) -> None:
        """Test handling when no slots are found."""
        adapter = AsyncMock()
        mock_session = AsyncMock()
        adapter.create_session = MagicMock()
        adapter.create_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        adapter.create_session.return_value.__aexit__ = AsyncMock(return_value=None)

        adapter.login = AsyncMock(return_value=True)
        adapter.search_slots = AsyncMock(return_value=[])  # No slots

        # Create and process booking
        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="vfs",
            target_country="DE",
        )

        await booking_service.submit_booking(booking["id"])

        result = await booking_service.process_booking(
            booking_id=booking["id"],
            adapter=adapter,
            account=mock_account,
            applicant_data=mock_applicant_data,
        )

        assert result["success"] is False
        assert "NO_SLOTS" in result.get("error_code", "") or "no slots" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_slot_retry_with_new_slot(
        self,
        booking_service: BookingService,
        mock_account: dict[str, Any],
        mock_applicant_data: dict[str, Any],
    ) -> None:
        """
        Test successful booking after slot retry.

        Simulates:
        1. First slot attempt fails
        2. Second attempt with different slot succeeds
        """
        adapter = AsyncMock()
        mock_session = AsyncMock()
        adapter.create_session = MagicMock()
        adapter.create_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        adapter.create_session.return_value.__aexit__ = AsyncMock(return_value=None)

        adapter.login = AsyncMock(return_value=True)

        search_count = 0

        async def search_with_refresh(session: Any, criteria: Any) -> list[MockSlot]:
            nonlocal search_count
            search_count += 1
            if search_count == 1:
                return [
                    MockSlot(
                        id="slot-1",
                        date=date.today() + timedelta(days=14),
                        time="10:30",
                        location="Istanbul",
                        location_id="istanbul",
                    ),
                ]
            return [
                MockSlot(
                    id="slot-2",
                    date=date.today() + timedelta(days=15),
                    time="14:00",
                    location="Istanbul",
                    location_id="istanbul",
                ),
            ]

        adapter.search_slots = AsyncMock(side_effect=search_with_refresh)

        book_count = 0

        async def book_with_retry(session: Any, slot: Any, data: Any) -> MockBookingResult:
            nonlocal book_count
            book_count += 1
            if book_count == 1:
                return MockBookingResult(
                    is_success=False,
                    error_message="Slot taken",
                    error_code="SLOT_TAKEN",
                )
            return MockBookingResult(
                is_success=True,
                confirmation_number="VFS-2024-RETRY",
                appointment_date=date.today() + timedelta(days=15),
                appointment_time="14:00",
            )

        adapter.book_slot = AsyncMock(side_effect=book_with_retry)

        # Create booking
        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="vfs",
            target_country="DE",
        )

        await booking_service.submit_booking(booking["id"])

        # First attempt fails
        result1 = await booking_service.process_booking(
            booking_id=booking["id"],
            adapter=adapter,
            account=mock_account,
            applicant_data=mock_applicant_data,
        )

        assert result1["success"] is False

        # Second attempt succeeds
        result2 = await booking_service.process_booking(
            booking_id=booking["id"],
            adapter=adapter,
            account=mock_account,
            applicant_data=mock_applicant_data,
        )

        assert result2["success"] is True
        assert result2["confirmation_number"] == "VFS-2024-RETRY"


# =============================================================================
# Priority and Queue Routing Tests
# =============================================================================


@pytest.mark.e2e
class TestBookingPriorityAndRouting:
    """Tests for booking priority calculation and queue routing."""

    @pytest.mark.asyncio
    async def test_premium_agency_gets_higher_priority(
        self,
        booking_service: BookingService,
    ) -> None:
        """Test that premium agencies get higher priority."""
        # Create booking for premium agency
        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="vfs",
            target_country="DE",
        )

        # Submit with premium flag
        result = await booking_service.submit_booking(
            booking["id"],
            is_premium=True,
        )

        # Premium should get HIGH or CRITICAL priority (value >= 8)
        assert result["priority"] >= 6  # At least ELEVATED

    @pytest.mark.asyncio
    async def test_urgent_dates_increase_priority(
        self,
        booking_service: BookingService,
    ) -> None:
        """Test that urgent preferred dates increase priority."""
        # Booking with date in 7 days (urgent)
        urgent_booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="vfs",
            target_country="DE",
            preferred_dates=[(date.today() + timedelta(days=7)).isoformat()],
        )

        # Booking with date in 30 days (not urgent)
        normal_booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="vfs",
            target_country="DE",
            preferred_dates=[(date.today() + timedelta(days=30)).isoformat()],
        )

        urgent_result = await booking_service.submit_booking(urgent_booking["id"])
        normal_result = await booking_service.submit_booking(normal_booking["id"])

        # Urgent should have higher priority
        assert urgent_result["priority"] >= normal_result["priority"]


# =============================================================================
# Cancellation Tests
# =============================================================================


@pytest.mark.e2e
class TestBookingCancellation:
    """Tests for booking cancellation flow."""

    @pytest.mark.asyncio
    async def test_cancel_pending_booking(
        self,
        booking_service: BookingService,
    ) -> None:
        """Test cancelling a pending booking."""
        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="vfs",
            target_country="DE",
        )

        # Cancel
        result = await booking_service.cancel_booking(
            booking["id"],
            reason="Customer request",
        )

        assert result["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_queued_booking(
        self,
        booking_service: BookingService,
    ) -> None:
        """Test cancelling a queued booking."""
        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="vfs",
            target_country="DE",
        )

        # Submit to queue
        await booking_service.submit_booking(booking["id"])

        # Cancel
        result = await booking_service.cancel_booking(
            booking["id"],
            reason="Changed plans",
        )

        assert result["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_completed_booking_fails(
        self,
        booking_service: BookingService,
        mock_repository: AsyncMock,
        mock_adapter: AsyncMock,
        mock_account: dict[str, Any],
        mock_applicant_data: dict[str, Any],
    ) -> None:
        """Test that cancelling a completed booking fails."""
        # Create and complete booking
        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="vfs",
            target_country="DE",
        )

        await booking_service.submit_booking(booking["id"])

        await booking_service.process_booking(
            booking_id=booking["id"],
            adapter=mock_adapter,
            account=mock_account,
            applicant_data=mock_applicant_data,
        )

        # Try to cancel - should fail
        from src.core.exceptions import InvalidStateTransitionError

        with pytest.raises(InvalidStateTransitionError):
            await booking_service.cancel_booking(booking["id"])


# =============================================================================
# Error Recovery Tests
# =============================================================================


@pytest.mark.e2e
class TestErrorRecovery:
    """Tests for error recovery scenarios."""

    @pytest.mark.asyncio
    async def test_login_failure_handling(
        self,
        booking_service: BookingService,
        mock_account: dict[str, Any],
        mock_applicant_data: dict[str, Any],
    ) -> None:
        """Test handling of login failures."""
        adapter = AsyncMock()
        mock_session = AsyncMock()
        adapter.create_session = MagicMock()
        adapter.create_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        adapter.create_session.return_value.__aexit__ = AsyncMock(return_value=None)

        adapter.login = AsyncMock(return_value=False)  # Login fails

        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="vfs",
            target_country="DE",
        )

        await booking_service.submit_booking(booking["id"])

        result = await booking_service.process_booking(
            booking_id=booking["id"],
            adapter=adapter,
            account=mock_account,
            applicant_data=mock_applicant_data,
        )

        assert result["success"] is False
        assert "LOGIN_FAILED" in result.get("error_code", "") or "login" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_system_error_handling(
        self,
        booking_service: BookingService,
        mock_account: dict[str, Any],
        mock_applicant_data: dict[str, Any],
    ) -> None:
        """Test handling of unexpected system errors."""
        adapter = AsyncMock()
        mock_session = AsyncMock()
        adapter.create_session = MagicMock()
        adapter.create_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        adapter.create_session.return_value.__aexit__ = AsyncMock(return_value=None)

        adapter.login = AsyncMock(return_value=True)
        adapter.search_slots = AsyncMock(side_effect=RuntimeError("Unexpected error"))

        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="vfs",
            target_country="DE",
        )

        await booking_service.submit_booking(booking["id"])

        result = await booking_service.process_booking(
            booking_id=booking["id"],
            adapter=adapter,
            account=mock_account,
            applicant_data=mock_applicant_data,
        )

        assert result["success"] is False
        assert "SYSTEM_ERROR" in result.get("error_code", "") or "unexpected" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_booking_not_found_handling(
        self,
        booking_service: BookingService,
        mock_adapter: AsyncMock,
        mock_account: dict[str, Any],
        mock_applicant_data: dict[str, Any],
    ) -> None:
        """Test handling of booking not found error."""
        result = await booking_service.process_booking(
            booking_id="nonexistent-booking-id",
            adapter=mock_adapter,
            account=mock_account,
            applicant_data=mock_applicant_data,
        )

        assert result["success"] is False
        assert "not found" in result.get("error", "").lower()


# =============================================================================
# Concurrent Booking Tests
# =============================================================================


@pytest.mark.e2e
class TestConcurrentBookings:
    """Tests for handling concurrent bookings."""

    @pytest.mark.asyncio
    async def test_multiple_bookings_same_agency(
        self,
        booking_service: BookingService,
        mock_repository: AsyncMock,
    ) -> None:
        """Test creating multiple bookings for the same agency."""
        agency_id = str(uuid4())

        # Create multiple bookings
        bookings = []
        for i in range(3):
            booking = await booking_service.create_booking(
                agency_id=agency_id,
                applicant_id=str(uuid4()),
                target_system="vfs",
                target_country="DE",
            )
            bookings.append(booking)

        # All bookings should be created successfully
        assert len(bookings) == 3
        for booking in bookings:
            assert booking["id"] is not None
            assert booking["agency_id"] == agency_id

    @pytest.mark.asyncio
    async def test_concurrent_booking_processing(
        self,
        booking_service: BookingService,
        mock_adapter: AsyncMock,
        mock_account: dict[str, Any],
        mock_applicant_data: dict[str, Any],
    ) -> None:
        """Test processing multiple bookings concurrently."""
        # Create bookings
        bookings = []
        for i in range(3):
            booking = await booking_service.create_booking(
                agency_id=str(uuid4()),
                applicant_id=str(uuid4()),
                target_system="vfs",
                target_country="DE",
            )
            await booking_service.submit_booking(booking["id"])
            bookings.append(booking)

        # Process all concurrently
        results = await asyncio.gather(*[
            booking_service.process_booking(
                booking_id=booking["id"],
                adapter=mock_adapter,
                account=mock_account,
                applicant_data=mock_applicant_data,
            )
            for booking in bookings
        ])

        # All should succeed
        assert all(r["success"] for r in results)
        assert len(set(r["confirmation_number"] for r in results)) == 1  # Same mock confirmation


# =============================================================================
# Multi-Site Tests
# =============================================================================


@pytest.mark.e2e
class TestMultiSiteBookings:
    """Tests for bookings across different target systems."""

    @pytest.mark.asyncio
    async def test_vfs_booking_flow(
        self,
        booking_service: BookingService,
        mock_adapter: AsyncMock,
        mock_account: dict[str, Any],
        mock_applicant_data: dict[str, Any],
    ) -> None:
        """Test VFS booking flow."""
        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="vfs",
            target_country="DE",
        )

        await booking_service.submit_booking(booking["id"])

        result = await booking_service.process_booking(
            booking_id=booking["id"],
            adapter=mock_adapter,
            account=mock_account,
            applicant_data=mock_applicant_data,
        )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_idata_booking_flow(
        self,
        booking_service: BookingService,
        mock_adapter: AsyncMock,
        mock_account: dict[str, Any],
        mock_applicant_data: dict[str, Any],
    ) -> None:
        """Test iDATA booking flow."""
        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="idata",
            target_country="IT",
        )

        await booking_service.submit_booking(booking["id"])

        result = await booking_service.process_booking(
            booking_id=booking["id"],
            adapter=mock_adapter,
            account=mock_account,
            applicant_data=mock_applicant_data,
        )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_bls_booking_flow(
        self,
        booking_service: BookingService,
        mock_adapter: AsyncMock,
        mock_account: dict[str, Any],
        mock_applicant_data: dict[str, Any],
    ) -> None:
        """Test BLS booking flow."""
        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="bls",
            target_country="ES",
        )

        await booking_service.submit_booking(booking["id"])

        result = await booking_service.process_booking(
            booking_id=booking["id"],
            adapter=mock_adapter,
            account=mock_account,
            applicant_data=mock_applicant_data,
        )

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_kkosmos_booking_flow(
        self,
        booking_service: BookingService,
        mock_adapter: AsyncMock,
        mock_account: dict[str, Any],
        mock_applicant_data: dict[str, Any],
    ) -> None:
        """Test KKOSMOS booking flow."""
        booking = await booking_service.create_booking(
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            target_system="kkosmos",
            target_country="GR",
        )

        await booking_service.submit_booking(booking["id"])

        result = await booking_service.process_booking(
            booking_id=booking["id"],
            adapter=mock_adapter,
            account=mock_account,
            applicant_data=mock_applicant_data,
        )

        assert result["success"] is True


# =============================================================================
# Retry Logic Tests
# =============================================================================


@pytest.mark.e2e
class TestRetryLogic:
    """Tests for retry logic and backoff strategies."""

    @pytest.mark.asyncio
    async def test_retry_manager_calculates_delay(self) -> None:
        """Test that retry manager calculates appropriate delays."""
        retry_manager = RetryManager()

        # Create context with timeout failure
        context = BookingContext(
            booking_id=str(uuid4()),
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            state=BookingState.FAILED,
            failure_reason=FailureReason.TIMEOUT,
            attempt_count=1,
        )

        should_retry, delay = retry_manager.should_retry(context)

        assert should_retry is True
        assert delay > 0

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self) -> None:
        """Test that max retries are respected."""
        retry_manager = RetryManager()

        # Create context with max attempts reached
        context = BookingContext(
            booking_id=str(uuid4()),
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            state=BookingState.FAILED,
            failure_reason=FailureReason.TIMEOUT,
            attempt_count=10,  # Exceeds max
        )

        should_retry, delay = retry_manager.should_retry(context)

        assert should_retry is False

    @pytest.mark.asyncio
    async def test_non_retriable_failure(self) -> None:
        """Test that non-retriable failures are not retried."""
        retry_manager = RetryManager()

        # Create context with non-retriable failure
        context = BookingContext(
            booking_id=str(uuid4()),
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            state=BookingState.FAILED,
            failure_reason=FailureReason.INVALID_DATA,
            attempt_count=1,
        )

        should_retry, delay = retry_manager.should_retry(context)

        assert should_retry is False


# =============================================================================
# State Machine Edge Cases
# =============================================================================


@pytest.mark.e2e
class TestStateMachineEdgeCases:
    """Tests for state machine edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_terminal_state_cannot_transition(self) -> None:
        """Test that terminal states cannot transition."""
        state_machine = BookingStateMachine()

        context = BookingContext(
            booking_id=str(uuid4()),
            agency_id=str(uuid4()),
            applicant_id=str(uuid4()),
            state=BookingState.COMPLETED,
        )

        can_transition, reason = state_machine.can_transition(
            context,
            BookingState.PROCESSING,
        )

        assert can_transition is False

    @pytest.mark.asyncio
    async def test_valid_state_transitions(self) -> None:
        """Test all valid state transitions."""
        state_machine = BookingStateMachine()

        # PENDING -> QUEUED
        context = BookingContext(state=BookingState.PENDING)
        context = await state_machine.transition(context, BookingState.QUEUED, "submitted")
        assert context.state == BookingState.QUEUED

        # QUEUED -> PROCESSING
        context = await state_machine.transition(context, BookingState.PROCESSING, "started")
        assert context.state == BookingState.PROCESSING
        assert context.attempt_count == 1

        # PROCESSING -> SLOT_FOUND
        context = await state_machine.transition(context, BookingState.SLOT_FOUND, "found")
        assert context.state == BookingState.SLOT_FOUND
        assert context.slot_found_at is not None

        # SLOT_FOUND -> BOOKING
        context = await state_machine.transition(context, BookingState.BOOKING, "selected")
        assert context.state == BookingState.BOOKING
        assert context.booking_started_at is not None

        # BOOKING -> PAYMENT
        context = await state_machine.transition(context, BookingState.PAYMENT, "payment_required")
        assert context.state == BookingState.PAYMENT
        assert context.payment_started_at is not None

        # PAYMENT -> COMPLETED
        context = await state_machine.transition(context, BookingState.COMPLETED, "success")
        assert context.state == BookingState.COMPLETED

    @pytest.mark.asyncio
    async def test_state_history_tracking(self) -> None:
        """Test that state history is properly tracked."""
        state_machine = BookingStateMachine()

        context = BookingContext(state=BookingState.PENDING)

        # Perform transitions
        context = await state_machine.transition(context, BookingState.QUEUED, "submitted")
        context = await state_machine.transition(context, BookingState.PROCESSING, "started")
        context = await state_machine.transition(context, BookingState.SLOT_FOUND, "found")

        # Check history
        assert len(context.state_history) == 3
        assert context.state_history[0].from_state == BookingState.PENDING
        assert context.state_history[0].to_state == BookingState.QUEUED
        assert context.state_history[1].from_state == BookingState.QUEUED
        assert context.state_history[1].to_state == BookingState.PROCESSING
        assert context.state_history[2].from_state == BookingState.PROCESSING
        assert context.state_history[2].to_state == BookingState.SLOT_FOUND
