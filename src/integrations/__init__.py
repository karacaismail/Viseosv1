"""
VISE OS Integrations Module

External service integrations for the VISE OS platform.
Handles connections to Directus CMS, Google Sheets,
and notification services.

Components:
- directus: Directus CMS API client
- google_sheets: Google Sheets data sync
- telegram: Telegram notification service

Usage:
    from src.integrations.directus import DirectusClient, get_directus_client

    # Using factory function (recommended for FastAPI)
    client = get_directus_client()
    agencies = await client.get_items("agencies")

    # Using context manager
    async with DirectusClient() as client:
        await client.create_item("bookings", {...})
"""

from src.integrations.directus import (
    COLLECTIONS,
    DirectusClient,
    DirectusFilter,
    clear_directus_client_cache,
    directus_client,
    get_directus_client,
)

__all__ = [
    "DirectusClient",
    "DirectusFilter",
    "get_directus_client",
    "clear_directus_client_cache",
    "directus_client",
    "COLLECTIONS",
]
