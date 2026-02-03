"""
VISE OS Stealth Browser Engine.

Anti-detection browser automation engine with multi-tier fallback strategy.
Provides stealth browsing capabilities with Camoufox (primary), Patchright
(secondary), and SeleniumBase UC mode (emergency fallback).

Components:
- StealthEngine: Main engine with tiered browser strategy
- StealthBrowserConfig: Browser configuration defaults
- BrowserProfileGenerator: Unique fingerprint profile generation
- StealthSessionLauncher: Session management and lifecycle
- SessionManager: High-level session management with health monitoring
- ManagedSession: Session wrapper with metrics tracking
- HumanMouseSimulator: Human-like mouse movement
- HumanTypingSimulator: Human-like keyboard input
- HumanScrollSimulator: Human-like scroll behavior
- CloudflareDetector: Cloudflare challenge detection
- BotDetectionTester: Bot detection test utilities
- BrowserSessionPool: Pre-warmed session pool management

Usage:
    from src.bot.engine.stealth import StealthEngine
    from src.bot.engine.session import SessionManager

    # Option 1: Using StealthEngine directly
    engine = StealthEngine()
    async with engine.create_session(proxy=proxy_config) as session:
        await session.page.goto("https://example.com")

    # Option 2: Using SessionManager for high-level management
    manager = SessionManager()
    await manager.initialize()
    async with manager.acquire_session() as session:
        await session.page.goto("https://example.com")
"""

from src.bot.engine.stealth import (
    StealthEngine,
    StealthBrowserConfig,
    BrowserProfileGenerator,
    StealthSessionLauncher,
    HumanMouseSimulator,
    HumanTypingSimulator,
    HumanScrollSimulator,
    CloudflareDetector,
    BotDetectionTester,
    BrowserSessionPool,
    SessionPoolExhaustedError,
)

from src.bot.engine.browser import (
    BaseBrowserLauncher,
    CamoufoxLauncher,
    PatchrightLauncher,
    SeleniumBaseUCLauncher,
    BrowserTier,
)

from src.bot.engine.session import (
    SessionManager,
    ManagedSession,
    SessionMetrics,
    SessionConfig,
    SessionStatus,
    SessionHealthStatus,
)

__all__ = [
    # Stealth Engine
    "StealthEngine",
    "StealthBrowserConfig",
    "BrowserProfileGenerator",
    "StealthSessionLauncher",
    "HumanMouseSimulator",
    "HumanTypingSimulator",
    "HumanScrollSimulator",
    "CloudflareDetector",
    "BotDetectionTester",
    "BrowserSessionPool",
    "SessionPoolExhaustedError",
    # Session Management
    "SessionManager",
    "ManagedSession",
    "SessionMetrics",
    "SessionConfig",
    "SessionStatus",
    "SessionHealthStatus",
    # Browser Launchers
    "BaseBrowserLauncher",
    "CamoufoxLauncher",
    "PatchrightLauncher",
    "SeleniumBaseUCLauncher",
    "BrowserTier",
]
