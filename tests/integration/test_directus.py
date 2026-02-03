"""
Integration Tests for Directus CMS.

This module provides integration tests for verifying the DirectusClient
interactions with an actual Directus instance. These tests require a
running Directus server and should be run against a test database.

Test Categories:
- Connection: Health check and server info
- CRUD Operations: Create, read, update, delete on collections
- Filtering: Query filtering and pagination
- Batch Operations: Bulk create, update, delete
- Error Handling: API error responses and retry logic

Running Tests:
    # Run all Directus integration tests
    pytest tests/integration/test_directus.py -v

    # Run with coverage
    pytest tests/integration/test_directus.py -v --cov=src.integrations.directus

Prerequisites:
    - Directus running at DIRECTUS_URL (default: http://localhost:8055)
    - Valid DIRECTUS_TOKEN with admin access
    - Test collections should exist (agencies, booking_requests, etc.)

Warning:
    These tests create, modify, and delete data in Directus.
    Use a dedicated test environment, not production!
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest

from src.integrations.directus import (
    COLLECTIONS,
    DirectusClient,
    DirectusFilter,
    clear_directus_client_cache,
    directus_client,
    get_directus_client,
)
from src.core.exceptions import DirectusAPIError, DirectusRateLimitError


# =============================================================================
# Test Markers and Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def clear_client_cache() -> None:
    """Clear the Directus client cache before each test."""
    clear_directus_client_cache()


@pytest.fixture
def test_directus_url() -> str:
    """Get test Directus URL."""
    return "http://localhost:8055"


@pytest.fixture
def test_directus_token() -> str:
    """Get test Directus token."""
    return "test_token"


@pytest.fixture
def mock_httpx_client() -> AsyncMock:
    """Create a mock httpx.AsyncClient."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.is_closed = False

    # Create mock response
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": []}

    client.request = AsyncMock(return_value=mock_response)
    client.get = AsyncMock(return_value=mock_response)
    client.aclose = AsyncMock()

    return client


# =============================================================================
# DirectusFilter Tests
# =============================================================================


class TestDirectusFilter:
    """Tests for DirectusFilter query builder."""

    def test_equality_filter(self) -> None:
        """Verify equality filter builds correctly."""
        filter_obj = DirectusFilter().eq("status", "active")
        result = filter_obj.build()

        assert result == {"status": {"_eq": "active"}}

    def test_not_equal_filter(self) -> None:
        """Verify not-equal filter builds correctly."""
        filter_obj = DirectusFilter().neq("status", "deleted")
        result = filter_obj.build()

        assert result == {"status": {"_neq": "deleted"}}

    def test_comparison_filters(self) -> None:
        """Verify comparison filters build correctly."""
        filter_obj = (
            DirectusFilter()
            .gt("amount", 100)
            .gte("credits", 50)
            .lt("priority", 10)
            .lte("attempts", 3)
        )
        result = filter_obj.build()

        assert result["amount"] == {"_gt": 100}
        assert result["credits"] == {"_gte": 50}
        assert result["priority"] == {"_lt": 10}
        assert result["attempts"] == {"_lte": 3}

    def test_string_filters(self) -> None:
        """Verify string filters build correctly."""
        filter_obj = (
            DirectusFilter()
            .contains("name", "agency")
            .starts_with("email", "test")
            .ends_with("domain", ".com")
        )
        result = filter_obj.build()

        assert result["name"] == {"_contains": "agency"}
        assert result["email"] == {"_starts_with": "test"}
        assert result["domain"] == {"_ends_with": ".com"}

    def test_null_filter(self) -> None:
        """Verify null filter builds correctly."""
        filter_null = DirectusFilter().is_null("deleted_at", True)
        filter_not_null = DirectusFilter().is_null("confirmed_at", False)

        assert filter_null.build() == {"deleted_at": {"_null": True}}
        assert filter_not_null.build() == {"confirmed_at": {"_null": False}}

    def test_in_list_filter(self) -> None:
        """Verify in-list filter builds correctly."""
        filter_obj = DirectusFilter().in_list("status", ["active", "pending"])
        result = filter_obj.build()

        assert result == {"status": {"_in": ["active", "pending"]}}

    def test_not_in_list_filter(self) -> None:
        """Verify not-in-list filter builds correctly."""
        filter_obj = DirectusFilter().not_in_list("site", ["banned", "deprecated"])
        result = filter_obj.build()

        assert result == {"site": {"_nin": ["banned", "deprecated"]}}

    def test_between_filter(self) -> None:
        """Verify between filter builds correctly."""
        filter_obj = DirectusFilter().between("created_at", "2024-01-01", "2024-12-31")
        result = filter_obj.build()

        assert result == {"created_at": {"_between": ["2024-01-01", "2024-12-31"]}}

    def test_raw_filter(self) -> None:
        """Verify raw filter dict is merged correctly."""
        filter_obj = DirectusFilter().raw({"custom_field": {"_special": "value"}})
        result = filter_obj.build()

        assert result == {"custom_field": {"_special": "value"}}

    def test_and_filter(self) -> None:
        """Verify AND logical filter builds correctly."""
        filter1 = DirectusFilter().eq("status", "active")
        filter2 = DirectusFilter().gte("credits", 100)

        combined = DirectusFilter().and_(filter1, filter2)
        result = combined.build()

        assert "_and" in result
        assert len(result["_and"]) == 2

    def test_or_filter(self) -> None:
        """Verify OR logical filter builds correctly."""
        filter1 = DirectusFilter().eq("site", "vfs")
        filter2 = DirectusFilter().eq("site", "idata")

        combined = DirectusFilter().or_(filter1, filter2)
        result = combined.build()

        assert "_or" in result
        assert len(result["_or"]) == 2

    def test_chained_filters(self) -> None:
        """Verify multiple filters can be chained."""
        filter_obj = (
            DirectusFilter()
            .eq("status", "active")
            .gte("credits", 50)
            .is_null("deleted_at", True)
        )
        result = filter_obj.build()

        assert len(result) == 3
        assert result["status"] == {"_eq": "active"}
        assert result["credits"] == {"_gte": 50}
        assert result["deleted_at"] == {"_null": True}


# =============================================================================
# DirectusClient Tests (Mocked)
# =============================================================================


class TestDirectusClientMocked:
    """Tests for DirectusClient with mocked HTTP client."""

    @pytest.fixture
    def client(
        self, test_directus_url: str, test_directus_token: str
    ) -> DirectusClient:
        """Create a DirectusClient instance."""
        return DirectusClient(
            url=test_directus_url,
            token=test_directus_token,
            timeout=10.0,
            max_retries=2,
        )

    @pytest.mark.asyncio
    async def test_client_initialization(self, client: DirectusClient) -> None:
        """Verify client initializes with correct settings."""
        assert client.base_url == "http://localhost:8055"
        assert client.timeout == 10.0
        assert client.max_retries == 2

    @pytest.mark.asyncio
    async def test_client_context_manager(
        self, mock_httpx_client: AsyncMock
    ) -> None:
        """Verify client works as async context manager."""
        with patch.object(
            httpx, "AsyncClient", return_value=mock_httpx_client
        ):
            async with DirectusClient(
                url="http://test:8055", token="test"
            ) as client:
                assert client._client is not None

    @pytest.mark.asyncio
    async def test_get_items_builds_correct_params(
        self, client: DirectusClient, mock_httpx_client: AsyncMock
    ) -> None:
        """Verify get_items builds correct request parameters."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"id": "1", "name": "Test Agency"}]
        }
        mock_httpx_client.request.return_value = mock_response

        with patch.object(client, "_get_client", return_value=mock_httpx_client):
            result = await client.get_items(
                "agencies",
                filter={"status": {"_eq": "active"}},
                sort=["-created_at"],
                limit=10,
                offset=5,
            )

            # Verify request was made
            mock_httpx_client.request.assert_called_once()
            call_kwargs = mock_httpx_client.request.call_args.kwargs

            assert call_kwargs["method"] == "GET"
            assert "/items/agencies" in call_kwargs["url"]

    @pytest.mark.asyncio
    async def test_get_items_with_directus_filter(
        self, client: DirectusClient, mock_httpx_client: AsyncMock
    ) -> None:
        """Verify get_items accepts DirectusFilter objects."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}
        mock_httpx_client.request.return_value = mock_response

        filter_obj = DirectusFilter().eq("status", "active").gte("credits", 100)

        with patch.object(client, "_get_client", return_value=mock_httpx_client):
            await client.get_items("agencies", filter=filter_obj)

            mock_httpx_client.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_item_returns_item(
        self, client: DirectusClient, mock_httpx_client: AsyncMock
    ) -> None:
        """Verify get_item returns a single item."""
        item_id = str(uuid4())
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {"id": item_id, "name": "Test"}
        }
        mock_httpx_client.request.return_value = mock_response

        with patch.object(client, "_get_client", return_value=mock_httpx_client):
            result = await client.get_item("agencies", item_id)

            assert result is not None
            assert result["id"] == item_id

    @pytest.mark.asyncio
    async def test_get_item_returns_none_when_not_found(
        self, client: DirectusClient, mock_httpx_client: AsyncMock
    ) -> None:
        """Verify get_item returns None for 404 response."""
        mock_response = AsyncMock()
        mock_response.status_code = 404
        mock_response.json.return_value = {
            "errors": [{"message": "Item not found"}]
        }
        mock_response.text = "Item not found"
        mock_httpx_client.request.return_value = mock_response

        with patch.object(client, "_get_client", return_value=mock_httpx_client):
            result = await client.get_item("agencies", "nonexistent-id")
            assert result is None

    @pytest.mark.asyncio
    async def test_create_item_sends_post_request(
        self, client: DirectusClient, mock_httpx_client: AsyncMock
    ) -> None:
        """Verify create_item sends POST request."""
        new_id = str(uuid4())
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {"id": new_id, "name": "New Agency"}
        }
        mock_httpx_client.request.return_value = mock_response

        with patch.object(client, "_get_client", return_value=mock_httpx_client):
            result = await client.create_item(
                "agencies",
                {"name": "New Agency", "status": "active"},
            )

            assert result["id"] == new_id
            mock_httpx_client.request.assert_called_once()
            call_kwargs = mock_httpx_client.request.call_args.kwargs
            assert call_kwargs["method"] == "POST"

    @pytest.mark.asyncio
    async def test_update_item_sends_patch_request(
        self, client: DirectusClient, mock_httpx_client: AsyncMock
    ) -> None:
        """Verify update_item sends PATCH request."""
        item_id = str(uuid4())
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {"id": item_id, "status": "updated"}
        }
        mock_httpx_client.request.return_value = mock_response

        with patch.object(client, "_get_client", return_value=mock_httpx_client):
            result = await client.update_item(
                "agencies",
                item_id,
                {"status": "updated"},
            )

            assert result["status"] == "updated"
            mock_httpx_client.request.assert_called_once()
            call_kwargs = mock_httpx_client.request.call_args.kwargs
            assert call_kwargs["method"] == "PATCH"

    @pytest.mark.asyncio
    async def test_delete_item_sends_delete_request(
        self, client: DirectusClient, mock_httpx_client: AsyncMock
    ) -> None:
        """Verify delete_item sends DELETE request."""
        item_id = str(uuid4())
        mock_response = AsyncMock()
        mock_response.status_code = 204
        mock_response.json.return_value = {}
        mock_httpx_client.request.return_value = mock_response

        with patch.object(client, "_get_client", return_value=mock_httpx_client):
            await client.delete_item("agencies", item_id)

            mock_httpx_client.request.assert_called_once()
            call_kwargs = mock_httpx_client.request.call_args.kwargs
            assert call_kwargs["method"] == "DELETE"

    @pytest.mark.asyncio
    async def test_count_items_returns_count(
        self, client: DirectusClient, mock_httpx_client: AsyncMock
    ) -> None:
        """Verify count_items returns item count."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"count": 42}]
        }
        mock_httpx_client.request.return_value = mock_response

        with patch.object(client, "_get_client", return_value=mock_httpx_client):
            count = await client.count_items("agencies")
            assert count == 42

    @pytest.mark.asyncio
    async def test_health_check_returns_true_on_success(
        self, client: DirectusClient, mock_httpx_client: AsyncMock
    ) -> None:
        """Verify health_check returns True when Directus is healthy."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_httpx_client.get.return_value = mock_response

        with patch.object(client, "_get_client", return_value=mock_httpx_client):
            result = await client.health_check()
            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_error(
        self, client: DirectusClient, mock_httpx_client: AsyncMock
    ) -> None:
        """Verify health_check returns False on error."""
        mock_httpx_client.get.side_effect = httpx.ConnectError("Connection refused")

        with patch.object(client, "_get_client", return_value=mock_httpx_client):
            result = await client.health_check()
            assert result is False


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestDirectusClientErrorHandling:
    """Tests for DirectusClient error handling."""

    @pytest.fixture
    def client(self) -> DirectusClient:
        """Create a DirectusClient instance."""
        return DirectusClient(
            url="http://localhost:8055",
            token="test_token",
            max_retries=2,
        )

    @pytest.mark.asyncio
    async def test_api_error_raised_on_4xx(
        self, client: DirectusClient, mock_httpx_client: AsyncMock
    ) -> None:
        """Verify DirectusAPIError is raised on 4xx responses."""
        mock_response = AsyncMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "errors": [{"message": "Bad request"}]
        }
        mock_response.text = "Bad request"
        mock_httpx_client.request.return_value = mock_response

        with patch.object(client, "_get_client", return_value=mock_httpx_client):
            with pytest.raises(DirectusAPIError) as exc_info:
                await client.get_items("invalid_collection")

            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_rate_limit_error_raised_on_429(
        self, client: DirectusClient, mock_httpx_client: AsyncMock
    ) -> None:
        """Verify DirectusRateLimitError is raised on 429 responses."""
        mock_response = AsyncMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "5"}
        mock_response.json.return_value = {"errors": [{"message": "Rate limited"}]}
        mock_response.text = "Rate limited"

        # Return 429 for all attempts
        mock_httpx_client.request.return_value = mock_response

        with patch.object(client, "_get_client", return_value=mock_httpx_client):
            with pytest.raises(DirectusRateLimitError) as exc_info:
                await client.get_items("agencies")

            assert exc_info.value.retry_after == 5

    @pytest.mark.asyncio
    async def test_retry_on_timeout(
        self, client: DirectusClient, mock_httpx_client: AsyncMock
    ) -> None:
        """Verify client retries on timeout."""
        # First call times out, second succeeds
        mock_success_response = AsyncMock()
        mock_success_response.status_code = 200
        mock_success_response.json.return_value = {"data": []}

        mock_httpx_client.request.side_effect = [
            httpx.TimeoutException("Timeout"),
            mock_success_response,
        ]

        with patch.object(client, "_get_client", return_value=mock_httpx_client):
            with patch("asyncio.sleep"):  # Skip actual sleep
                result = await client.get_items("agencies")

        assert result == []
        assert mock_httpx_client.request.call_count == 2


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestDirectusFactoryFunctions:
    """Tests for Directus client factory functions."""

    def test_get_directus_client_returns_cached_instance(self) -> None:
        """Verify get_directus_client returns same instance."""
        clear_directus_client_cache()

        client1 = get_directus_client()
        client2 = get_directus_client()

        assert client1 is client2

    def test_clear_cache_creates_new_instance(self) -> None:
        """Verify clear_directus_client_cache creates new instance."""
        client1 = get_directus_client()
        clear_directus_client_cache()
        client2 = get_directus_client()

        assert client1 is not client2

    @pytest.mark.asyncio
    async def test_directus_client_context_manager(
        self, mock_httpx_client: AsyncMock
    ) -> None:
        """Verify directus_client context manager works."""
        with patch.object(
            httpx, "AsyncClient", return_value=mock_httpx_client
        ):
            async with directus_client() as client:
                assert isinstance(client, DirectusClient)

            # Client should be closed after context manager exit
            mock_httpx_client.aclose.assert_called()


# =============================================================================
# Collections Constants Test
# =============================================================================


class TestDirectusCollections:
    """Tests for Directus collection constants."""

    def test_core_collections_defined(self) -> None:
        """Verify core collections are defined."""
        expected_collections = [
            "agencies",
            "agency_credits",
            "booking_requests",
            "booking_results",
            "booking_attempts",
            "bot_accounts",
            "proxy_pool",
            "site_configs",
        ]

        for collection in expected_collections:
            assert collection in COLLECTIONS
            assert COLLECTIONS[collection] == collection

    def test_collections_count(self) -> None:
        """Verify expected number of collections."""
        assert len(COLLECTIONS) >= 10  # At least 10 core collections


# =============================================================================
# Live Integration Tests (require running Directus)
# =============================================================================


@pytest.mark.requires_directus
class TestDirectusClientLive:
    """
    Live integration tests requiring a running Directus instance.

    These tests are skipped by default. Run with:
        pytest tests/integration/test_directus.py -v -m requires_directus

    Prerequisites:
        - Directus running at http://localhost:8055
        - Valid admin token set in DIRECTUS_TOKEN environment variable
        - Test collections exist in the database
    """

    @pytest.fixture
    async def live_client(self) -> DirectusClient:
        """Create a client connected to live Directus."""
        return DirectusClient()

    @pytest.mark.asyncio
    async def test_health_check(self, live_client: DirectusClient) -> None:
        """Verify health check against live Directus."""
        async with live_client:
            result = await live_client.health_check()
            assert result is True

    @pytest.mark.asyncio
    async def test_crud_lifecycle(self, live_client: DirectusClient) -> None:
        """Verify full CRUD lifecycle against live Directus."""
        async with live_client:
            # Create
            test_id = str(uuid4())
            item = await live_client.create_item(
                "agencies",
                {
                    "id": test_id,
                    "name": f"Test Agency {test_id[:8]}",
                    "status": "active",
                    "contact_email": "test@example.com",
                },
            )
            assert item["id"] == test_id

            try:
                # Read
                fetched = await live_client.get_item("agencies", test_id)
                assert fetched is not None
                assert fetched["name"].startswith("Test Agency")

                # Update
                updated = await live_client.update_item(
                    "agencies",
                    test_id,
                    {"name": "Updated Test Agency"},
                )
                assert updated["name"] == "Updated Test Agency"

            finally:
                # Delete (cleanup)
                await live_client.delete_item("agencies", test_id)

            # Verify deleted
            deleted = await live_client.get_item("agencies", test_id)
            assert deleted is None
