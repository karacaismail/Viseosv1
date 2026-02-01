"""
VISE OS Test Suite.

This package contains all tests for the VISE OS platform:
- unit/: Unit tests for individual components
- integration/: Integration tests for service interactions
- e2e/: End-to-end tests for complete flows

Test Categories:
- State Machine: Booking state transitions and validation
- Encryption: PII encryption/decryption roundtrip
- Configuration: Environment variable parsing
- Exceptions: Exception hierarchy and error handling
- Proxy: Proxy rotation and health tracking
- Account Pool: Account selection and lifecycle
- Adapters: Site-specific adapter logic
- Payment: Payment processing and 3DS handling
- API: FastAPI endpoints and middleware

Running Tests:
    # All tests
    pytest tests/ -v

    # Unit tests only
    pytest tests/unit/ -v

    # Integration tests only
    pytest tests/integration/ -v

    # E2E tests only
    pytest tests/e2e/ -v

    # With coverage
    pytest tests/ --cov=src --cov-report=html
"""

__all__: list[str] = []
