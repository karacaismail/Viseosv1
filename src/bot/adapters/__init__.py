"""
VISE OS Site Adapters Package.

Provides abstract base class and concrete implementations for
visa appointment booking systems (VFS Global, iDATA, BLS Spain, KKOSMOS).

Concrete adapters are lazy-loaded to prevent import failures when
only a specific adapter is needed.

Usage:
    from src.bot.adapters import VFSAdapter, Slot, SlotSearchCriteria
    from src.bot.adapters import get_adapter_class

    # Get adapter class by site code
    adapter_cls = get_adapter_class("vfs")
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from src.bot.adapters.bls import BLSAdapter
    from src.bot.adapters.idata import IDataAdapter
    from src.bot.adapters.kkosmos import KKOSMOSAdapter
    from src.bot.adapters.vfs import VFSAdapter


# Lazy-load mapping: attribute name -> (module_path, class_name)
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # VFS
    "VFSAdapter": ("src.bot.adapters.vfs", "VFSAdapter"),
    "VFSFlowState": ("src.bot.adapters.vfs", "VFSFlowState"),
    "VFSURLBuilder": ("src.bot.adapters.vfs", "VFSURLBuilder"),
    "VFSSelectors": ("src.bot.adapters.vfs", "VFSSelectors"),
    "VFSErrorClassifier": ("src.bot.adapters.vfs", "VFSErrorClassifier"),
    "VFSErrorClassification": ("src.bot.adapters.vfs", "VFSErrorClassification"),
    "VFSErrorPatterns": ("src.bot.adapters.vfs", "VFSErrorPatterns"),
    "HumanBehaviorSimulator": ("src.bot.adapters.vfs", "HumanBehaviorSimulator"),
    # iDATA
    "IDataAdapter": ("src.bot.adapters.idata", "IDataAdapter"),
    "IDataFlowState": ("src.bot.adapters.idata", "IDataFlowState"),
    "IDataEndpoints": ("src.bot.adapters.idata", "IDataEndpoints"),
    "IDataSelectors": ("src.bot.adapters.idata", "IDataSelectors"),
    "IDataErrorClassifier": ("src.bot.adapters.idata", "IDataErrorClassifier"),
    "IDataErrorClassification": ("src.bot.adapters.idata", "IDataErrorClassification"),
    "IDataErrorPatterns": ("src.bot.adapters.idata", "IDataErrorPatterns"),
    "IDataAPIClient": ("src.bot.adapters.idata", "IDataAPIClient"),
    "IDataSlotMonitor": ("src.bot.adapters.idata", "IDataSlotMonitor"),
    # BLS
    "BLSAdapter": ("src.bot.adapters.bls", "BLSAdapter"),
    "BLSFlowState": ("src.bot.adapters.bls", "BLSFlowState"),
    "BLSURLBuilder": ("src.bot.adapters.bls", "BLSURLBuilder"),
    "BLSSelectors": ("src.bot.adapters.bls", "BLSSelectors"),
    "BLSKeyboardTyper": ("src.bot.adapters.bls", "BLSKeyboardTyper"),
    "BLSErrorPatterns": ("src.bot.adapters.bls", "BLSErrorPatterns"),
    "BLSErrorClassifier": ("src.bot.adapters.bls", "BLSErrorClassifier"),
    "BLSErrorClassification": ("src.bot.adapters.bls", "BLSErrorClassification"),
    # KKOSMOS
    "KKOSMOSAdapter": ("src.bot.adapters.kkosmos", "KKOSMOSAdapter"),
    "KKOSMOSFlowState": ("src.bot.adapters.kkosmos", "KKOSMOSFlowState"),
    "KKOSMOSURLBuilder": ("src.bot.adapters.kkosmos", "KKOSMOSURLBuilder"),
    "KKOSMOSSelectors": ("src.bot.adapters.kkosmos", "KKOSMOSSelectors"),
    "KKOSMOSVerificationHandler": ("src.bot.adapters.kkosmos", "KKOSMOSVerificationHandler"),
    "KKOSMOSErrorPatterns": ("src.bot.adapters.kkosmos", "KKOSMOSErrorPatterns"),
    "KKOSMOSErrorClassifier": ("src.bot.adapters.kkosmos", "KKOSMOSErrorClassifier"),
    "KKOSMOSErrorClassification": ("src.bot.adapters.kkosmos", "KKOSMOSErrorClassification"),
    "PhonePoolManager": ("src.bot.adapters.kkosmos", "PhonePoolManager"),
    "PhoneNumber": ("src.bot.adapters.kkosmos", "PhoneNumber"),
    "PhoneStatus": ("src.bot.adapters.kkosmos", "PhoneStatus"),
    "FiveSimClient": ("src.bot.adapters.kkosmos", "FiveSimClient"),
    "SMSHubClient": ("src.bot.adapters.kkosmos", "SMSHubClient"),
    "SMSActivateClient": ("src.bot.adapters.kkosmos", "SMSActivateClient"),
}

# Adapter registry: site_code -> (module_path, class_name)
_ADAPTER_REGISTRY: dict[str, tuple[str, str]] = {
    "vfs": ("src.bot.adapters.vfs", "VFSAdapter"),
    "idata": ("src.bot.adapters.idata", "IDataAdapter"),
    "bls": ("src.bot.adapters.bls", "BLSAdapter"),
    "kkosmos": ("src.bot.adapters.kkosmos", "KKOSMOSAdapter"),
}


def __getattr__(name: str) -> Any:
    """Lazy-load adapter classes on first access."""
    if name in _LAZY_IMPORTS:
        module_path, class_name = _LAZY_IMPORTS[name]
        module = importlib.import_module(module_path)
        return getattr(module, class_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_adapter_class(site_code: str) -> type[BaseSiteAdapter]:
    """
    Get adapter class by site code.

    Args:
        site_code: Site identifier (vfs, idata, bls, kkosmos).

    Returns:
        Adapter class (not instantiated).

    Raises:
        ValueError: If site_code is not recognized.
    """
    site_code = site_code.lower()
    if site_code not in _ADAPTER_REGISTRY:
        raise ValueError(
            f"Unknown site code: {site_code!r}. "
            f"Available: {list(_ADAPTER_REGISTRY.keys())}"
        )
    module_path, class_name = _ADAPTER_REGISTRY[site_code]
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


__all__ = [
    # Factory
    "get_adapter_class",
    # Main adapter class
    "BaseSiteAdapter",
    # Concrete adapters (lazy)
    "VFSAdapter",
    "IDataAdapter",
    "BLSAdapter",
    "KKOSMOSAdapter",
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
