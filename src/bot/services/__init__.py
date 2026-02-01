"""
VISE OS Bot Services Module.

Core services for browser automation including proxy management,
CAPTCHA solving, and account pool management.

Components:
- proxy: ProxyManager with rotation, health tracking, and multi-provider failover
- captcha: CAPTCHA solving with multi-provider chain (2Captcha, Anti-Captcha, CapSolver)
- account: Bot account pool management with usage limits and cooldown handling
"""

from src.bot.services.proxy import (
    ProxyManager,
    ProxyPoolManager,
    ProxyHealth,
    ProxyStatus,
    ProxyType,
    BrightDataConfig,
    OxylabsConfig,
    SmartproxyConfig,
    SiteProxyConfig,
    RotatingProxyStrategy,
    StickySessionStrategy,
    GeoRotationStrategy,
    ASNDiversityStrategy,
    ProxyFailoverChain,
    ProxyCostOptimizer,
    ProxyHealthMonitor,
)

__all__ = [
    # Main manager
    "ProxyManager",
    "ProxyPoolManager",
    # Data classes
    "ProxyHealth",
    # Enums
    "ProxyStatus",
    "ProxyType",
    # Provider configs
    "BrightDataConfig",
    "OxylabsConfig",
    "SmartproxyConfig",
    # Site config
    "SiteProxyConfig",
    # Rotation strategies
    "RotatingProxyStrategy",
    "StickySessionStrategy",
    "GeoRotationStrategy",
    "ASNDiversityStrategy",
    # Failover and optimization
    "ProxyFailoverChain",
    "ProxyCostOptimizer",
    "ProxyHealthMonitor",
]
