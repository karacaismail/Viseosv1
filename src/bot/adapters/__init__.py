"""
VISE OS Site Adapters Package.

Provides abstract base class and concrete implementations for
visa appointment booking systems (VFS Global, iDATA, BLS Spain, KKOSMOS).

Available Adapters:
- BaseSiteAdapter: Abstract base class for all site adapters
- VFSAdapter: VFS Global implementation
- IDataAdapter: iDATA implementation (Germany/Italy Schengen)
- BLSAdapter: BLS Spain implementation (TODO)
- KKOSMOSAdapter: KKOSMOS implementation (TODO)

Usage:
    from src.bot.adapters import (
        VFSAdapter,
        IDataAdapter,
        Slot,
        SlotSearchCriteria,
        BookingResult,
        BookingResultStatus,
        AdapterConfig,
    )

    # Create VFS adapter
    adapter = VFSAdapter(
        stealth_engine=stealth_engine,
        proxy_manager=proxy_manager,
        captcha_solver=captcha_solver,
    )

    # Create iDATA adapter
    idata_adapter = IDataAdapter(
        stealth_engine=stealth_engine,
        proxy_manager=proxy_manager,
        captcha_solver=captcha_solver,
    )

    # Use the adapter
    async with adapter.create_session(account) as session:
        await adapter.login(session, account)
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

# Concrete adapter implementations
from src.bot.adapters.vfs import (
    VFSAdapter,
    VFSFlowState,
    VFSURLBuilder,
    VFSSelectors,
    VFSErrorClassifier,
    VFSErrorClassification,
    VFSErrorPatterns,
    HumanBehaviorSimulator,
)

from src.bot.adapters.idata import (
    IDataAdapter,
    IDataFlowState,
    IDataEndpoints,
    IDataSelectors,
    IDataErrorClassifier,
    IDataErrorClassification,
    IDataErrorPatterns,
    IDataAPIClient,
    IDataSlotMonitor,
)

__all__ = [
    # Main adapter class
    "BaseSiteAdapter",
    # Concrete adapters
    "VFSAdapter",
    "IDataAdapter",
    # VFS-specific exports
    "VFSFlowState",
    "VFSURLBuilder",
    "VFSSelectors",
    "VFSErrorClassifier",
    "VFSErrorClassification",
    "VFSErrorPatterns",
    "HumanBehaviorSimulator",
    # iDATA-specific exports
    "IDataFlowState",
    "IDataEndpoints",
    "IDataSelectors",
    "IDataErrorClassifier",
    "IDataErrorClassification",
    "IDataErrorPatterns",
    "IDataAPIClient",
    "IDataSlotMonitor",
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
