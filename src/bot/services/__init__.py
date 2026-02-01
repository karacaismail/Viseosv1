"""
VISE OS Bot Services Module.

Core services for browser automation including proxy management,
CAPTCHA solving, and account pool management.

Components:
- proxy: ProxyManager with rotation, health tracking, and multi-provider failover
- captcha: CAPTCHA solving with multi-provider chain (CapSolver, 2Captcha, Anti-Captcha)
- account: Bot account pool management with usage limits and cooldown handling
"""

from src.bot.services.captcha import (
    CaptchaSolver,
    CaptchaType,
    ProviderStatus,
    CaptchaChallenge,
    SolveResult,
    ProviderStats,
    CapSolverProvider,
    TwoCaptchaProvider,
    AntiCaptchaProvider,
    CaptchaSolverChain,
    CaptchaDetector,
    CaptchaTokenInjector,
    CaptchaCostTracker,
    VFSCaptchaHandler,
)
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
    # Proxy - Main manager
    "ProxyManager",
    "ProxyPoolManager",
    # Proxy - Data classes
    "ProxyHealth",
    # Proxy - Enums
    "ProxyStatus",
    "ProxyType",
    # Proxy - Provider configs
    "BrightDataConfig",
    "OxylabsConfig",
    "SmartproxyConfig",
    # Proxy - Site config
    "SiteProxyConfig",
    # Proxy - Rotation strategies
    "RotatingProxyStrategy",
    "StickySessionStrategy",
    "GeoRotationStrategy",
    "ASNDiversityStrategy",
    # Proxy - Failover and optimization
    "ProxyFailoverChain",
    "ProxyCostOptimizer",
    "ProxyHealthMonitor",
    # CAPTCHA - Main interface
    "CaptchaSolver",
    # CAPTCHA - Enums
    "CaptchaType",
    "ProviderStatus",
    # CAPTCHA - Data classes
    "CaptchaChallenge",
    "SolveResult",
    "ProviderStats",
    # CAPTCHA - Providers
    "CapSolverProvider",
    "TwoCaptchaProvider",
    "AntiCaptchaProvider",
    # CAPTCHA - Solver chain
    "CaptchaSolverChain",
    # CAPTCHA - Detection and injection
    "CaptchaDetector",
    "CaptchaTokenInjector",
    # CAPTCHA - Cost tracking
    "CaptchaCostTracker",
    # CAPTCHA - Site handlers
    "VFSCaptchaHandler",
]
