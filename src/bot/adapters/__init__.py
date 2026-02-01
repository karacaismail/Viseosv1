"""
VISE OS Site Adapters Package.

Provides abstract base class and concrete implementations for
visa appointment booking systems (VFS Global, iDATA, BLS Spain, KKOSMOS).

Available Adapters:
- BaseSiteAdapter: Abstract base class for all site adapters
- VFSAdapter: VFS Global implementation (TODO)
- IDataAdapter: iDATA implementation (TODO)
- BLSAdapter: BLS Spain implementation (TODO)
- KKOSMOSAdapter: KKOSMOS implementation (TODO)

Usage:
    from src.bot.adapters.base import BaseSiteAdapter
    from src.bot.adapters import (
        Slot,
        SlotSearchCriteria,
        BookingResult,
        BookingResultStatus,
        AdapterConfig,
    )

    # Create a concrete adapter
    adapter = VFSAdapter(
        stealth_engine=stealth_engine,
        proxy_manager=proxy_manager,
        captcha_solver=captcha_solver,
    )

    # Use the adapter
    async with adapter.create_session(account) as session:
        slots = await adapter.search_slots(session, criteria)
        result = await adapter.book_slot(session, slots[0], applicant)
"""

from src.bot.adapters.base import (
    # Main adapter class
    BaseSiteAdapter,
    # Data classes
    AdapterConfig,
    BookingResult,
    BookingResultStatus,
    Slot,
    SlotSearchCriteria,
    # Enums
    SiteCode,
    SlotStatus,
    # Session management
    AdapterSession,
    AdapterSessionContext,
    # Retry and timeout
    AdapterRetryConfig,
    AdapterTimeouts,
    # Metrics
    AdapterMetrics,
)

__all__ = [
    # Main adapter class
    "BaseSiteAdapter",
    # Data classes
    "AdapterConfig",
    "BookingResult",
    "BookingResultStatus",
    "Slot",
    "SlotSearchCriteria",
    # Enums
    "SiteCode",
    "SlotStatus",
    # Session management
    "AdapterSession",
    "AdapterSessionContext",
    # Retry and timeout
    "AdapterRetryConfig",
    "AdapterTimeouts",
    # Metrics
    "AdapterMetrics",
]
