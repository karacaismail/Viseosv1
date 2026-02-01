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
    from src.integrations.google_sheets import GoogleSheetsClient, get_sheets_client

    # Using factory function (recommended for FastAPI)
    client = get_directus_client()
    agencies = await client.get_items("agencies")

    # Using context manager
    async with DirectusClient() as client:
        await client.create_item("bookings", {...})

    # Google Sheets sync
    async with GoogleSheetsClient() as sheets:
        rows = await sheets.get_pending_applications(sheet_id)
"""

from src.integrations.directus import (
    COLLECTIONS,
    DirectusClient,
    DirectusFilter,
    clear_directus_client_cache,
    directus_client,
    get_directus_client,
)
from src.integrations.google_sheets import (
    COLUMN_MAPPING,
    COUNTRY_CITIES,
    COUNTRY_VISA_TYPES,
    FIELD_TO_COLUMN,
    REQUIRED_FIELDS,
    SUPPORTED_COUNTRIES,
    GoogleSheetsClient,
    GoogleSheetsError,
    RowStatus,
    SheetNotFoundError,
    SheetRateLimitError,
    SheetRow,
    SheetValidationError,
    SyncResult,
    clear_sheets_client_cache,
    get_sheets_client,
    sheets_client,
)

__all__ = [
    # Directus
    "DirectusClient",
    "DirectusFilter",
    "get_directus_client",
    "clear_directus_client_cache",
    "directus_client",
    "COLLECTIONS",
    # Google Sheets
    "GoogleSheetsClient",
    "get_sheets_client",
    "clear_sheets_client_cache",
    "sheets_client",
    "SheetRow",
    "SyncResult",
    "RowStatus",
    "GoogleSheetsError",
    "SheetNotFoundError",
    "SheetValidationError",
    "SheetRateLimitError",
    "COLUMN_MAPPING",
    "FIELD_TO_COLUMN",
    "REQUIRED_FIELDS",
    "SUPPORTED_COUNTRIES",
    "COUNTRY_CITIES",
    "COUNTRY_VISA_TYPES",
]
