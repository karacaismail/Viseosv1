"""
Directus CMS API Client.

This module provides an async client for interacting with Directus CMS.
It handles authentication, CRUD operations, filtering, pagination,
and error handling with automatic retries.

Usage:
    from src.integrations.directus import DirectusClient, get_directus_client

    # Using context manager (recommended)
    async with DirectusClient(url="http://localhost:8055", token="xxx") as client:
        agencies = await client.get_items("agencies", filter={"status": {"_eq": "active"}})

    # Or with dependency injection
    client = get_directus_client()
    await client.create_item("bookings", {"status": "pending", ...})
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, TypeVar
from uuid import UUID

import httpx
import structlog

from src.api.config import get_settings
from src.core.exceptions import (
    DirectusAPIError,
    DirectusRateLimitError,
)

logger = structlog.get_logger()

# Type alias for Directus items
T = TypeVar("T", bound=dict[str, Any])


class DirectusFilter:
    """
    Builder for Directus filter queries.

    Directus uses a specific filter syntax for querying items.
    This class provides a fluent interface for building filters.

    Example:
        filter = (
            DirectusFilter()
            .eq("status", "active")
            .gte("created_at", "2024-01-01")
            .contains("name", "agency")
            .build()
        )
    """

    def __init__(self) -> None:
        self._filters: dict[str, Any] = {}

    def eq(self, field: str, value: Any) -> "DirectusFilter":
        """Add equality filter: field == value."""
        self._filters[field] = {"_eq": value}
        return self

    def neq(self, field: str, value: Any) -> "DirectusFilter":
        """Add not-equal filter: field != value."""
        self._filters[field] = {"_neq": value}
        return self

    def gt(self, field: str, value: Any) -> "DirectusFilter":
        """Add greater-than filter: field > value."""
        self._filters[field] = {"_gt": value}
        return self

    def gte(self, field: str, value: Any) -> "DirectusFilter":
        """Add greater-than-or-equal filter: field >= value."""
        self._filters[field] = {"_gte": value}
        return self

    def lt(self, field: str, value: Any) -> "DirectusFilter":
        """Add less-than filter: field < value."""
        self._filters[field] = {"_lt": value}
        return self

    def lte(self, field: str, value: Any) -> "DirectusFilter":
        """Add less-than-or-equal filter: field <= value."""
        self._filters[field] = {"_lte": value}
        return self

    def contains(self, field: str, value: str) -> "DirectusFilter":
        """Add contains filter: field LIKE %value%."""
        self._filters[field] = {"_contains": value}
        return self

    def starts_with(self, field: str, value: str) -> "DirectusFilter":
        """Add starts-with filter: field LIKE value%."""
        self._filters[field] = {"_starts_with": value}
        return self

    def ends_with(self, field: str, value: str) -> "DirectusFilter":
        """Add ends-with filter: field LIKE %value."""
        self._filters[field] = {"_ends_with": value}
        return self

    def is_null(self, field: str, null: bool = True) -> "DirectusFilter":
        """Add null filter: field IS NULL or IS NOT NULL."""
        self._filters[field] = {"_null": null}
        return self

    def in_list(self, field: str, values: list[Any]) -> "DirectusFilter":
        """Add in-list filter: field IN (values)."""
        self._filters[field] = {"_in": values}
        return self

    def not_in_list(self, field: str, values: list[Any]) -> "DirectusFilter":
        """Add not-in-list filter: field NOT IN (values)."""
        self._filters[field] = {"_nin": values}
        return self

    def between(self, field: str, start: Any, end: Any) -> "DirectusFilter":
        """Add between filter: field BETWEEN start AND end."""
        self._filters[field] = {"_between": [start, end]}
        return self

    def raw(self, filter_dict: dict[str, Any]) -> "DirectusFilter":
        """Add raw Directus filter dict."""
        self._filters.update(filter_dict)
        return self

    def and_(self, *filters: "DirectusFilter") -> "DirectusFilter":
        """Combine filters with AND logic."""
        self._filters["_and"] = [f.build() for f in filters]
        return self

    def or_(self, *filters: "DirectusFilter") -> "DirectusFilter":
        """Combine filters with OR logic."""
        self._filters["_or"] = [f.build() for f in filters]
        return self

    def build(self) -> dict[str, Any]:
        """Build and return the filter dictionary."""
        return self._filters.copy()


class DirectusClient:
    """
    Async client for Directus CMS API.

    Provides methods for CRUD operations on Directus collections with
    support for filtering, sorting, pagination, and error handling.

    Attributes:
        base_url: Base URL of the Directus instance.
        timeout: Request timeout in seconds.
        max_retries: Maximum number of retries for failed requests.

    Example:
        async with DirectusClient(url="http://localhost:8055", token="xxx") as client:
            # Get items with filtering
            active_agencies = await client.get_items(
                "agencies",
                filter={"status": {"_eq": "active"}},
                sort=["-created_at"],
                limit=10
            )

            # Create an item
            new_booking = await client.create_item(
                "booking_requests",
                {"agency_id": "...", "status": "pending"}
            )

            # Update an item
            await client.update_item("booking_requests", booking_id, {"status": "processing"})
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        """
        Initialize the Directus client.

        Args:
            url: Directus instance URL. If not provided, uses DIRECTUS_URL from settings.
            token: API token. If not provided, uses DIRECTUS_TOKEN from settings.
            timeout: Request timeout in seconds (default: 30).
            max_retries: Maximum retries for failed requests (default: 3).
        """
        settings = get_settings()
        self.base_url = (url or settings.DIRECTUS_URL).rstrip("/")
        self._token = token or settings.DIRECTUS_TOKEN.get_secret_value()
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: httpx.AsyncClient | None = None
        self._logger = logger.bind(service="directus")

    @property
    def _headers(self) -> dict[str, str]:
        """Get request headers with authentication."""
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers,
                timeout=httpx.Timeout(self.timeout),
            )
        return self._client

    async def __aenter__(self) -> "DirectusClient":
        """Async context manager entry."""
        await self._get_client()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit - close the client."""
        await self.close()

    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json_data: dict[str, Any] | list[dict[str, Any]] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """
        Make an HTTP request to Directus with retry logic.

        Args:
            method: HTTP method (GET, POST, PATCH, DELETE).
            endpoint: API endpoint path.
            json_data: JSON body for POST/PATCH requests.
            params: Query parameters.

        Returns:
            Parsed JSON response data.

        Raises:
            DirectusAPIError: For API errors.
            DirectusRateLimitError: When rate limited (with retry).
        """
        client = await self._get_client()
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                self._logger.debug(
                    "directus_request",
                    method=method,
                    endpoint=endpoint,
                    attempt=attempt + 1,
                )

                response = await client.request(
                    method=method,
                    url=endpoint,
                    json=json_data,
                    params=self._serialize_params(params) if params else None,
                )

                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 5))
                    self._logger.warning(
                        "directus_rate_limited",
                        endpoint=endpoint,
                        retry_after=retry_after,
                    )
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(retry_after)
                        continue
                    raise DirectusRateLimitError(
                        message=f"Rate limit exceeded for {endpoint}",
                        endpoint=endpoint,
                        retry_after=retry_after,
                    )

                # Handle other errors
                if response.status_code >= 400:
                    error_data = self._parse_error_response(response)
                    raise DirectusAPIError(
                        message=error_data.get("message", f"API error: {response.status_code}"),
                        endpoint=endpoint,
                        status_code=response.status_code,
                        details=error_data,
                    )

                # Success
                result = response.json()
                return result.get("data", result)

            except httpx.TimeoutException as e:
                last_error = e
                self._logger.warning(
                    "directus_timeout",
                    endpoint=endpoint,
                    attempt=attempt + 1,
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)  # Exponential backoff
                    continue

            except httpx.HTTPError as e:
                last_error = e
                self._logger.error(
                    "directus_http_error",
                    endpoint=endpoint,
                    error=str(e),
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue

        # All retries exhausted
        raise DirectusAPIError(
            message=f"Request failed after {self.max_retries} attempts",
            endpoint=endpoint,
            details={"last_error": str(last_error)} if last_error else None,
        )

    def _serialize_params(self, params: dict[str, Any]) -> dict[str, str]:
        """
        Serialize query parameters for Directus API.

        Handles special cases like filter objects and lists.
        """
        serialized = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, dict):
                # Directus expects filter as JSON string
                import json
                serialized[key] = json.dumps(value)
            elif isinstance(value, list):
                if key == "sort":
                    # Sort is comma-separated
                    serialized[key] = ",".join(str(v) for v in value)
                elif key == "fields":
                    # Fields are comma-separated
                    serialized[key] = ",".join(str(v) for v in value)
                else:
                    import json
                    serialized[key] = json.dumps(value)
            elif isinstance(value, bool):
                serialized[key] = str(value).lower()
            elif isinstance(value, (UUID, datetime)):
                serialized[key] = str(value)
            else:
                serialized[key] = str(value)
        return serialized

    def _parse_error_response(self, response: httpx.Response) -> dict[str, Any]:
        """Parse error response from Directus."""
        try:
            data = response.json()
            errors = data.get("errors", [])
            if errors:
                return {
                    "message": errors[0].get("message", "Unknown error"),
                    "extensions": errors[0].get("extensions", {}),
                }
            return {"message": response.text}
        except Exception:
            return {"message": response.text}

    # -------------------------------------------------------------------------
    # CRUD Operations
    # -------------------------------------------------------------------------

    async def get_items(
        self,
        collection: str,
        *,
        filter: dict[str, Any] | DirectusFilter | None = None,
        fields: list[str] | None = None,
        sort: list[str] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        search: str | None = None,
        deep: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get items from a collection.

        Args:
            collection: Collection name (e.g., "agencies", "booking_requests").
            filter: Filter criteria (dict or DirectusFilter).
            fields: Fields to include in response.
            sort: Sort fields (prefix with - for descending).
            limit: Maximum number of items to return.
            offset: Number of items to skip.
            search: Full-text search query.
            deep: Deep filter for nested relations.

        Returns:
            List of items matching the criteria.

        Example:
            # Get active agencies sorted by name
            agencies = await client.get_items(
                "agencies",
                filter={"status": {"_eq": "active"}},
                sort=["name"],
                limit=10
            )

            # Using DirectusFilter builder
            filter = DirectusFilter().eq("status", "active").gte("credits", 100)
            agencies = await client.get_items("agencies", filter=filter)
        """
        params: dict[str, Any] = {}

        if filter is not None:
            if isinstance(filter, DirectusFilter):
                params["filter"] = filter.build()
            else:
                params["filter"] = filter

        if fields is not None:
            params["fields"] = fields
        if sort is not None:
            params["sort"] = sort
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        if search is not None:
            params["search"] = search
        if deep is not None:
            params["deep"] = deep

        self._logger.info(
            "directus_get_items",
            collection=collection,
            has_filter=filter is not None,
            limit=limit,
        )

        result = await self._request("GET", f"/items/{collection}", params=params)

        # Handle case where result might be a single item or list
        if isinstance(result, list):
            return result
        return [result] if result else []

    async def get_item(
        self,
        collection: str,
        item_id: str | UUID,
        *,
        fields: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """
        Get a single item by ID.

        Args:
            collection: Collection name.
            item_id: Item ID (UUID or string).
            fields: Fields to include in response.

        Returns:
            Item data or None if not found.
        """
        params: dict[str, Any] = {}
        if fields is not None:
            params["fields"] = fields

        self._logger.debug(
            "directus_get_item",
            collection=collection,
            item_id=str(item_id),
        )

        try:
            return await self._request(
                "GET",
                f"/items/{collection}/{item_id}",
                params=params if params else None,
            )
        except DirectusAPIError as e:
            if e.status_code == 404:
                return None
            raise

    async def create_item(
        self,
        collection: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Create a new item in a collection.

        Args:
            collection: Collection name.
            data: Item data to create.

        Returns:
            Created item with generated fields (id, created_at, etc.).

        Example:
            booking = await client.create_item(
                "booking_requests",
                {
                    "agency_id": "uuid-here",
                    "status": "pending",
                    "priority": 5,
                }
            )
        """
        self._logger.info(
            "directus_create_item",
            collection=collection,
        )

        return await self._request("POST", f"/items/{collection}", json_data=data)

    async def create_items(
        self,
        collection: str,
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Create multiple items in a collection.

        Args:
            collection: Collection name.
            items: List of items to create.

        Returns:
            List of created items.
        """
        self._logger.info(
            "directus_create_items",
            collection=collection,
            count=len(items),
        )

        result = await self._request("POST", f"/items/{collection}", json_data=items)
        return result if isinstance(result, list) else [result]

    async def update_item(
        self,
        collection: str,
        item_id: str | UUID,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Update an existing item.

        Args:
            collection: Collection name.
            item_id: Item ID to update.
            data: Fields to update.

        Returns:
            Updated item data.

        Example:
            await client.update_item(
                "booking_requests",
                booking_id,
                {"status": "processing", "attempts": 1}
            )
        """
        self._logger.info(
            "directus_update_item",
            collection=collection,
            item_id=str(item_id),
        )

        return await self._request(
            "PATCH",
            f"/items/{collection}/{item_id}",
            json_data=data,
        )

    async def update_items(
        self,
        collection: str,
        item_ids: list[str | UUID],
        data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Update multiple items with the same data.

        Args:
            collection: Collection name.
            item_ids: List of item IDs to update.
            data: Fields to update on all items.

        Returns:
            List of updated items.
        """
        self._logger.info(
            "directus_update_items",
            collection=collection,
            count=len(item_ids),
        )

        # Directus PATCH /items/{collection} with keys parameter
        params = {"keys": [str(id) for id in item_ids]}
        return await self._request(
            "PATCH",
            f"/items/{collection}",
            json_data=data,
            params=params,
        )

    async def delete_item(
        self,
        collection: str,
        item_id: str | UUID,
    ) -> None:
        """
        Delete an item from a collection.

        Args:
            collection: Collection name.
            item_id: Item ID to delete.
        """
        self._logger.info(
            "directus_delete_item",
            collection=collection,
            item_id=str(item_id),
        )

        await self._request("DELETE", f"/items/{collection}/{item_id}")

    async def delete_items(
        self,
        collection: str,
        item_ids: list[str | UUID],
    ) -> None:
        """
        Delete multiple items from a collection.

        Args:
            collection: Collection name.
            item_ids: List of item IDs to delete.
        """
        self._logger.info(
            "directus_delete_items",
            collection=collection,
            count=len(item_ids),
        )

        await self._request(
            "DELETE",
            f"/items/{collection}",
            json_data=[str(id) for id in item_ids],
        )

    # -------------------------------------------------------------------------
    # Aggregate Operations
    # -------------------------------------------------------------------------

    async def count_items(
        self,
        collection: str,
        *,
        filter: dict[str, Any] | DirectusFilter | None = None,
    ) -> int:
        """
        Count items in a collection.

        Args:
            collection: Collection name.
            filter: Optional filter criteria.

        Returns:
            Number of items matching the filter.
        """
        params: dict[str, Any] = {
            "aggregate": {"count": "*"},
        }

        if filter is not None:
            if isinstance(filter, DirectusFilter):
                params["filter"] = filter.build()
            else:
                params["filter"] = filter

        result = await self._request("GET", f"/items/{collection}", params=params)

        if isinstance(result, list) and len(result) > 0:
            return int(result[0].get("count", 0))
        return 0

    async def aggregate(
        self,
        collection: str,
        *,
        aggregate: dict[str, str | list[str]],
        filter: dict[str, Any] | DirectusFilter | None = None,
        group_by: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Perform aggregate operations on a collection.

        Args:
            collection: Collection name.
            aggregate: Aggregate operations (e.g., {"sum": "amount", "count": "*"}).
            filter: Optional filter criteria.
            group_by: Fields to group by.

        Returns:
            Aggregate results.

        Example:
            # Sum credits used per agency
            result = await client.aggregate(
                "credit_transactions",
                aggregate={"sum": "amount"},
                filter={"type": {"_eq": "usage"}},
                group_by=["agency_id"]
            )
        """
        params: dict[str, Any] = {"aggregate": aggregate}

        if filter is not None:
            if isinstance(filter, DirectusFilter):
                params["filter"] = filter.build()
            else:
                params["filter"] = filter

        if group_by is not None:
            params["groupBy"] = group_by

        return await self._request("GET", f"/items/{collection}", params=params)

    # -------------------------------------------------------------------------
    # Utility Methods
    # -------------------------------------------------------------------------

    async def health_check(self) -> bool:
        """
        Check if Directus is healthy and accessible.

        Returns:
            True if Directus is responding, False otherwise.
        """
        try:
            client = await self._get_client()
            response = await client.get("/server/health")
            return response.status_code == 200
        except Exception as e:
            self._logger.warning("directus_health_check_failed", error=str(e))
            return False

    async def get_server_info(self) -> dict[str, Any]:
        """
        Get Directus server information.

        Returns:
            Server info including version and project name.
        """
        return await self._request("GET", "/server/info")

    async def get_collection_fields(self, collection: str) -> list[dict[str, Any]]:
        """
        Get field definitions for a collection.

        Args:
            collection: Collection name.

        Returns:
            List of field definitions.
        """
        return await self._request("GET", f"/fields/{collection}")


# =============================================================================
# Factory Functions
# =============================================================================


@lru_cache
def get_directus_client() -> DirectusClient:
    """
    Get a cached Directus client instance.

    The client is cached and reused across requests.
    Use this for dependency injection in FastAPI.

    Returns:
        Configured DirectusClient instance.

    Example:
        from fastapi import Depends

        @router.get("/agencies")
        async def list_agencies(client: DirectusClient = Depends(get_directus_client)):
            return await client.get_items("agencies")
    """
    return DirectusClient()


def clear_directus_client_cache() -> None:
    """
    Clear the cached Directus client.

    Useful for testing or when connection settings change.
    """
    get_directus_client.cache_clear()


@asynccontextmanager
async def directus_client(
    url: str | None = None,
    token: str | None = None,
) -> DirectusClient:
    """
    Async context manager for a Directus client.

    Creates a new client instance that is automatically closed on exit.
    Use this when you need a client with custom settings.

    Args:
        url: Optional custom Directus URL.
        token: Optional custom API token.

    Yields:
        Configured DirectusClient instance.

    Example:
        async with directus_client() as client:
            await client.create_item("agencies", {...})
    """
    client = DirectusClient(url=url, token=token)
    try:
        await client._get_client()
        yield client
    finally:
        await client.close()


# =============================================================================
# Collection Constants
# =============================================================================

# Core collections from 002-DIRECTUS-SCHEMA.md
COLLECTIONS = {
    "agencies": "agencies",
    "agency_credits": "agency_credits",
    "credit_transactions": "credit_transactions",
    "applicants": "applicants",
    "booking_requests": "booking_requests",
    "booking_results": "booking_results",
    "booking_attempts": "booking_attempts",
    "bot_accounts": "bot_accounts",
    "account_sessions": "account_sessions",
    "proxy_pool": "proxy_pool",
    "proxy_health": "proxy_health",
    "site_configs": "site_configs",
    "selector_mappings": "selector_mappings",
    "system_metrics": "system_metrics",
    "alert_rules": "alert_rules",
    "alert_history": "alert_history",
}


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "DirectusClient",
    "DirectusFilter",
    "get_directus_client",
    "clear_directus_client_cache",
    "directus_client",
    "COLLECTIONS",
]
