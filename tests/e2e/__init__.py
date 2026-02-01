"""
End-to-End Tests for VISE OS.

This package contains E2E tests that verify the complete booking flows
from request creation to completion, simulating real-world scenarios.

Test Categories:
- Complete Booking Flow: Full flow from creation to confirmation
- Account Ban Recovery: Automatic account rotation on ban detection
- Slot Unavailable Retry: Re-search when slot becomes unavailable
- Payment Processing: 3DS and verification flows
- Error Recovery: Graceful handling of various failure scenarios

Running Tests:
    # Run all E2E tests
    pytest tests/e2e/ -v

    # Run with markers
    pytest tests/e2e/ -v -m "e2e"

    # Run specific test file
    pytest tests/e2e/test_booking_flow.py -v

Prerequisites:
    - Mocked services (no external dependencies required)
    - Test fixtures from conftest.py

Note:
    E2E tests use mocked adapters and services to avoid requiring
    real external services. They verify the integration of all
    components in the booking flow.
"""
