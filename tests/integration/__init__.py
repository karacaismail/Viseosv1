"""
VISE OS Integration Tests.

This package contains integration tests that verify the interaction between
different services and components of the VISE OS platform.

Test Modules:
- test_directus.py: Directus CMS CRUD operations (requires running Directus)
- test_celery.py: Celery task queue and worker execution

Running Integration Tests:
    # All integration tests (requires all services running)
    pytest tests/integration/ -v

    # Skip Directus tests (only test Celery)
    pytest tests/integration/ -v --ignore=tests/integration/test_directus.py

    # Only run Directus tests
    pytest tests/integration/test_directus.py -v

Test Markers:
    @pytest.mark.integration - All integration tests
    @pytest.mark.requires_redis - Tests requiring Redis connection
    @pytest.mark.requires_directus - Tests requiring Directus connection

Environment Requirements:
    - Redis: redis://localhost:6379
    - Directus: http://localhost:8055 (for test_directus.py)
    - Environment variables set in conftest.py

Note:
    Integration tests may have side effects and should be run in isolation
    from production data. Use dedicated test databases and queues.
"""
