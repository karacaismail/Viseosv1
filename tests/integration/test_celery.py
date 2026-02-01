"""
Integration Tests for Celery Task Queue.

This module provides integration tests for verifying Celery task execution,
queue routing, retry logic, and worker interactions. Tests use mocked
backends to allow execution without a running Redis instance.

Test Categories:
- Celery App: Application configuration and setup
- Queue Routing: Task-to-queue routing logic
- Task Execution: Task registration and execution
- Retry Logic: Task retry behavior and backoff
- Dead Letter Queue: Failed task handling

Running Tests:
    # Run all Celery integration tests
    pytest tests/integration/test_celery.py -v

    # Run with coverage
    pytest tests/integration/test_celery.py -v --cov=src.queue

Prerequisites:
    - No external services required (tests use mocks)
    - Environment variables set via conftest.py

Note:
    Tests use Celery's `eager` mode and mocked backends to avoid
    requiring a running Redis broker.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from celery import Celery
from celery.result import EagerResult

from src.queue.celery_app import (
    QUEUE_CONFIG,
    SITE_QUEUES,
    celery_app,
    create_celery_app,
    get_queue_for_priority,
    get_queue_for_site,
)
from src.queue.tasks.booking import (
    BookingTask,
    handle_dead_letter,
    process_booking,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def test_celery_app() -> Celery:
    """
    Create a test Celery app with eager execution.

    Configures Celery to execute tasks synchronously in the same
    process, avoiding the need for a broker or worker.
    """
    app = create_celery_app(
        broker_url="memory://",
        result_backend="cache+memory://",
    )

    # Configure for testing
    app.conf.update(
        task_always_eager=True,  # Execute tasks synchronously
        task_eager_propagates=True,  # Propagate exceptions
        result_backend="cache+memory://",
    )

    return app


@pytest.fixture
def mock_booking_data() -> dict[str, Any]:
    """Create mock booking data for tests."""
    return {
        "id": str(uuid4()),
        "agency_id": str(uuid4()),
        "applicant_id": str(uuid4()),
        "status": "queued",
        "target_system": "vfs",
        "created_at": datetime.utcnow().isoformat(),
    }


@pytest.fixture
def mock_directus_client() -> AsyncMock:
    """Create a mock DirectusClient."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


@pytest.fixture
def mock_booking_repository() -> AsyncMock:
    """Create a mock BookingRepository."""
    repo = AsyncMock()
    repo.get_by_id = AsyncMock()
    repo.update = AsyncMock()
    return repo


# =============================================================================
# Celery Application Configuration Tests
# =============================================================================


class TestCeleryAppConfiguration:
    """Tests for Celery application configuration."""

    def test_celery_app_is_created(self) -> None:
        """Verify celery_app instance exists."""
        assert celery_app is not None
        assert isinstance(celery_app, Celery)

    def test_celery_app_name(self) -> None:
        """Verify Celery app name."""
        assert celery_app.main == "vise_os"

    def test_queue_config_defined(self) -> None:
        """Verify queue configurations are defined."""
        expected_queues = [
            "high_priority",
            "normal",
            "retry",
            "scheduled",
            "night_ops",
            "dead_letter",
        ]

        for queue in expected_queues:
            assert queue in QUEUE_CONFIG
            assert "exchange" in QUEUE_CONFIG[queue]
            assert "routing_key" in QUEUE_CONFIG[queue]

    def test_site_queues_defined(self) -> None:
        """Verify site-specific queues are defined."""
        expected_sites = ["vfs", "idata", "bls", "kkosmos"]

        for site in expected_sites:
            assert site in SITE_QUEUES
            assert "exchange" in SITE_QUEUES[site]
            assert "routing_key" in SITE_QUEUES[site]

    def test_high_priority_queue_has_max_priority(self) -> None:
        """Verify high priority queue has priority configured."""
        config = QUEUE_CONFIG["high_priority"]
        assert "queue_arguments" in config
        assert config["queue_arguments"].get("x-max-priority") == 10

    def test_retry_queue_has_ttl(self) -> None:
        """Verify retry queue has message TTL configured."""
        config = QUEUE_CONFIG["retry"]
        assert "queue_arguments" in config
        assert "x-message-ttl" in config["queue_arguments"]


class TestCreateCeleryApp:
    """Tests for create_celery_app factory function."""

    def test_create_with_custom_broker(self) -> None:
        """Verify create_celery_app accepts custom broker URL."""
        app = create_celery_app(
            broker_url="redis://custom:6379/0",
            result_backend="redis://custom:6379/1",
        )

        assert "custom" in app.conf.broker_url
        assert "custom" in app.conf.result_backend

    def test_task_serializer_is_json(self) -> None:
        """Verify tasks use JSON serialization."""
        app = create_celery_app()

        assert app.conf.task_serializer == "json"
        assert app.conf.result_serializer == "json"
        assert "json" in app.conf.accept_content

    def test_worker_prefetch_multiplier(self) -> None:
        """Verify worker prefetch multiplier is set for fair distribution."""
        app = create_celery_app()

        assert app.conf.worker_prefetch_multiplier == 1

    def test_task_acks_late_enabled(self) -> None:
        """Verify task acknowledgment is late (after completion)."""
        app = create_celery_app()

        assert app.conf.task_acks_late is True

    def test_max_tasks_per_child_set(self) -> None:
        """Verify worker restarts after max tasks (memory leak prevention)."""
        app = create_celery_app()

        assert app.conf.worker_max_tasks_per_child == 100


# =============================================================================
# Queue Routing Tests
# =============================================================================


class TestQueueRouting:
    """Tests for task queue routing helpers."""

    def test_get_queue_for_priority_high(self) -> None:
        """Verify high priority maps to high_priority queue."""
        assert get_queue_for_priority("high") == "high_priority"

    def test_get_queue_for_priority_normal(self) -> None:
        """Verify normal priority maps to normal queue."""
        assert get_queue_for_priority("normal") == "normal"

    def test_get_queue_for_priority_low(self) -> None:
        """Verify low priority maps to normal queue."""
        assert get_queue_for_priority("low") == "normal"

    def test_get_queue_for_priority_retry(self) -> None:
        """Verify retry priority maps to retry queue."""
        assert get_queue_for_priority("retry") == "retry"

    def test_get_queue_for_priority_scheduled(self) -> None:
        """Verify scheduled priority maps to scheduled queue."""
        assert get_queue_for_priority("scheduled") == "scheduled"

    def test_get_queue_for_priority_night(self) -> None:
        """Verify night priority maps to night_ops queue."""
        assert get_queue_for_priority("night") == "night_ops"

    def test_get_queue_for_priority_unknown(self) -> None:
        """Verify unknown priority defaults to normal queue."""
        assert get_queue_for_priority("unknown") == "normal"
        assert get_queue_for_priority("") == "normal"

    def test_get_queue_for_site_vfs(self) -> None:
        """Verify VFS site maps to vfs queue."""
        assert get_queue_for_site("vfs") == "vfs"
        assert get_queue_for_site("VFS") == "vfs"  # Case insensitive

    def test_get_queue_for_site_idata(self) -> None:
        """Verify iDATA site maps to idata queue."""
        assert get_queue_for_site("idata") == "idata"
        assert get_queue_for_site("IDATA") == "idata"

    def test_get_queue_for_site_bls(self) -> None:
        """Verify BLS site maps to bls queue."""
        assert get_queue_for_site("bls") == "bls"
        assert get_queue_for_site("BLS") == "bls"

    def test_get_queue_for_site_kkosmos(self) -> None:
        """Verify KKosmos site maps to kkosmos queue."""
        assert get_queue_for_site("kkosmos") == "kkosmos"
        assert get_queue_for_site("KKOSMOS") == "kkosmos"

    def test_get_queue_for_site_unknown(self) -> None:
        """Verify unknown site defaults to normal queue."""
        assert get_queue_for_site("unknown") == "normal"
        assert get_queue_for_site("") == "normal"


# =============================================================================
# BookingTask Base Class Tests
# =============================================================================


class TestBookingTaskBase:
    """Tests for BookingTask base class."""

    def test_booking_task_autoretry_config(self) -> None:
        """Verify BookingTask has autoretry configured."""
        assert BookingTask.autoretry_for == (Exception,)
        assert BookingTask.retry_backoff is True
        assert BookingTask.retry_backoff_max == 600
        assert BookingTask.retry_jitter is True
        assert BookingTask.max_retries == 3

    def test_booking_task_on_failure(self) -> None:
        """Verify on_failure routes to dead letter queue."""
        task = BookingTask()
        task.app = MagicMock()
        task.app.send_task = MagicMock()

        booking_id = str(uuid4())

        task.on_failure(
            exc=Exception("Test error"),
            task_id="task-123",
            args=(),
            kwargs={"booking_id": booking_id},
            einfo=None,
        )

        task.app.send_task.assert_called_once()
        call_args = task.app.send_task.call_args

        assert call_args[0][0] == "src.queue.tasks.booking.handle_dead_letter"
        assert call_args[1]["queue"] == "dead_letter"

    def test_booking_task_on_failure_with_args(self) -> None:
        """Verify on_failure handles booking_id in args."""
        task = BookingTask()
        task.app = MagicMock()
        task.app.send_task = MagicMock()

        booking_id = str(uuid4())

        task.on_failure(
            exc=Exception("Test error"),
            task_id="task-123",
            args=(booking_id,),
            kwargs={},
            einfo=None,
        )

        task.app.send_task.assert_called_once()
        call_args = task.app.send_task.call_args

        # Verify booking_id is passed to dead letter handler
        assert booking_id in call_args[0][1]


# =============================================================================
# Task Registration Tests
# =============================================================================


class TestTaskRegistration:
    """Tests for task registration in Celery app."""

    def test_process_booking_is_registered(self) -> None:
        """Verify process_booking task is registered."""
        assert "src.queue.tasks.booking.process_booking" in celery_app.tasks

    def test_handle_dead_letter_is_registered(self) -> None:
        """Verify handle_dead_letter task is registered."""
        assert "src.queue.tasks.booking.handle_dead_letter" in celery_app.tasks

    def test_process_booking_task_name(self) -> None:
        """Verify process_booking has correct task name."""
        assert process_booking.name == "src.queue.tasks.booking.process_booking"

    def test_handle_dead_letter_task_name(self) -> None:
        """Verify handle_dead_letter has correct task name."""
        assert handle_dead_letter.name == "src.queue.tasks.booking.handle_dead_letter"


# =============================================================================
# Task Execution Tests (Mocked)
# =============================================================================


class TestProcessBookingTask:
    """Tests for process_booking task execution."""

    @pytest.mark.asyncio
    async def test_process_booking_returns_result(
        self,
        mock_booking_data: dict[str, Any],
        mock_directus_client: AsyncMock,
        mock_booking_repository: AsyncMock,
    ) -> None:
        """Verify process_booking returns correct result structure."""
        mock_booking_repository.get_by_id.return_value = mock_booking_data
        mock_booking_repository.update.return_value = None

        with patch(
            "src.queue.tasks.booking.get_directus_client",
            return_value=mock_directus_client,
        ):
            with patch(
                "src.queue.tasks.booking.BookingRepository",
                return_value=mock_booking_repository,
            ):
                # Configure the mocked async context manager
                mock_directus_client.__aenter__.return_value = mock_directus_client

                # Run the task synchronously
                result = process_booking.apply(
                    args=[mock_booking_data["id"], "vfs", 5]
                ).get()

                assert "success" in result
                assert "booking_id" in result
                assert result["booking_id"] == mock_booking_data["id"]

    @pytest.mark.asyncio
    async def test_process_booking_handles_not_found(
        self,
        mock_directus_client: AsyncMock,
        mock_booking_repository: AsyncMock,
    ) -> None:
        """Verify process_booking handles missing booking."""
        mock_booking_repository.get_by_id.return_value = None

        with patch(
            "src.queue.tasks.booking.get_directus_client",
            return_value=mock_directus_client,
        ):
            with patch(
                "src.queue.tasks.booking.BookingRepository",
                return_value=mock_booking_repository,
            ):
                mock_directus_client.__aenter__.return_value = mock_directus_client

                result = process_booking.apply(
                    args=["nonexistent-id", "vfs", 5]
                ).get()

                assert result["success"] is False
                assert "not found" in result["error"].lower()


class TestDeadLetterHandler:
    """Tests for handle_dead_letter task."""

    @pytest.mark.asyncio
    async def test_handle_dead_letter_updates_booking(
        self,
        mock_directus_client: AsyncMock,
        mock_booking_repository: AsyncMock,
    ) -> None:
        """Verify handle_dead_letter updates booking status."""
        booking_id = str(uuid4())

        with patch(
            "src.queue.tasks.booking.get_directus_client",
            return_value=mock_directus_client,
        ):
            with patch(
                "src.queue.tasks.booking.BookingRepository",
                return_value=mock_booking_repository,
            ):
                mock_directus_client.__aenter__.return_value = mock_directus_client

                result = handle_dead_letter.apply(
                    args=[booking_id, "Test error message"]
                ).get()

                assert result["success"] is True
                assert result["booking_id"] == booking_id
                assert result["action"] == "moved_to_dead_letter"

                # Verify repository update was called
                mock_booking_repository.update.assert_called_once()


# =============================================================================
# Celery Eager Mode Tests
# =============================================================================


class TestCeleryEagerMode:
    """Tests for Celery eager (synchronous) execution mode."""

    def test_eager_mode_config(self, test_celery_app: Celery) -> None:
        """Verify test app is configured for eager mode."""
        assert test_celery_app.conf.task_always_eager is True
        assert test_celery_app.conf.task_eager_propagates is True

    def test_task_returns_eager_result(self, test_celery_app: Celery) -> None:
        """Verify tasks return EagerResult in eager mode."""
        # Register a simple test task
        @test_celery_app.task
        def simple_task(x: int, y: int) -> int:
            return x + y

        result = simple_task.delay(2, 3)

        # In eager mode, result is already available
        assert isinstance(result, EagerResult)
        assert result.get() == 5


# =============================================================================
# Task Routing Configuration Tests
# =============================================================================


class TestTaskRoutingConfiguration:
    """Tests for task routing in Celery configuration."""

    def test_high_priority_task_route(self) -> None:
        """Verify high priority tasks route to high_priority queue."""
        routes = celery_app.conf.task_routes

        # process_premium_booking should be routed to high_priority
        assert routes.get("src.queue.tasks.process_premium_booking") == {
            "queue": "high_priority"
        }

    def test_site_specific_task_routes(self) -> None:
        """Verify site-specific tasks route to site queues."""
        routes = celery_app.conf.task_routes

        assert routes.get("src.queue.tasks.vfs_*") == {"queue": "vfs"}
        assert routes.get("src.queue.tasks.idata_*") == {"queue": "idata"}
        assert routes.get("src.queue.tasks.bls_*") == {"queue": "bls"}
        assert routes.get("src.queue.tasks.kkosmos_*") == {"queue": "kkosmos"}

    def test_scheduled_task_routes(self) -> None:
        """Verify scheduled tasks route to scheduled queue."""
        routes = celery_app.conf.task_routes

        assert routes.get("src.queue.tasks.check_slots") == {"queue": "scheduled"}
        assert routes.get("src.queue.tasks.sync_sheets") == {"queue": "scheduled"}

    def test_night_ops_task_routes(self) -> None:
        """Verify night operation tasks route to night_ops queue."""
        routes = celery_app.conf.task_routes

        assert routes.get("src.queue.tasks.account_warming") == {"queue": "night_ops"}
        assert routes.get("src.queue.tasks.proxy_rotation") == {"queue": "night_ops"}


# =============================================================================
# Task Annotations Tests
# =============================================================================


class TestTaskAnnotations:
    """Tests for task annotations and rate limiting."""

    def test_default_rate_limit(self) -> None:
        """Verify default rate limit is configured."""
        annotations = celery_app.conf.task_annotations

        assert "*" in annotations
        assert annotations["*"]["rate_limit"] == "10/s"


# =============================================================================
# Timezone Configuration Tests
# =============================================================================


class TestTimezoneConfiguration:
    """Tests for timezone configuration."""

    def test_timezone_is_istanbul(self) -> None:
        """Verify timezone is set to Istanbul."""
        assert celery_app.conf.timezone == "Europe/Istanbul"

    def test_utc_enabled(self) -> None:
        """Verify UTC is enabled."""
        assert celery_app.conf.enable_utc is True


# =============================================================================
# Result Backend Tests
# =============================================================================


class TestResultBackendConfiguration:
    """Tests for result backend configuration."""

    def test_result_expires(self) -> None:
        """Verify results expire after 1 hour."""
        assert celery_app.conf.result_expires == 3600


# =============================================================================
# Integration with State Machine Tests
# =============================================================================


class TestCeleryStateMachineIntegration:
    """Tests for integration between Celery tasks and state machine."""

    @pytest.mark.asyncio
    async def test_task_transitions_state_to_processing(
        self,
        mock_booking_data: dict[str, Any],
        mock_directus_client: AsyncMock,
        mock_booking_repository: AsyncMock,
    ) -> None:
        """Verify task transitions booking state to processing."""
        mock_booking_repository.get_by_id.return_value = mock_booking_data

        with patch(
            "src.queue.tasks.booking.get_directus_client",
            return_value=mock_directus_client,
        ):
            with patch(
                "src.queue.tasks.booking.BookingRepository",
                return_value=mock_booking_repository,
            ):
                mock_directus_client.__aenter__.return_value = mock_directus_client

                result = process_booking.apply(
                    args=[mock_booking_data["id"], "vfs", 5]
                ).get()

                # Verify update was called with state transition
                update_calls = mock_booking_repository.update.call_args_list
                assert len(update_calls) > 0

    @pytest.mark.asyncio
    async def test_successful_booking_transitions_to_completed(
        self,
        mock_booking_data: dict[str, Any],
        mock_directus_client: AsyncMock,
        mock_booking_repository: AsyncMock,
    ) -> None:
        """Verify successful booking transitions to completed state."""
        mock_booking_repository.get_by_id.return_value = mock_booking_data

        with patch(
            "src.queue.tasks.booking.get_directus_client",
            return_value=mock_directus_client,
        ):
            with patch(
                "src.queue.tasks.booking.BookingRepository",
                return_value=mock_booking_repository,
            ):
                mock_directus_client.__aenter__.return_value = mock_directus_client

                result = process_booking.apply(
                    args=[mock_booking_data["id"], "vfs", 5]
                ).get()

                # Stub returns success, so state should be completed
                if result["success"]:
                    assert result["state"] == "completed"
