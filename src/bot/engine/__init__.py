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
- HumanMouseSimulator: Human-like mouse movement
- HumanTypingSimulator: Human-like keyboard input
- HumanScrollSimulator: Human-like scroll behavior
- CloudflareDetector: Cloudflare challenge detection
- BotDetectionTester: Bot detection test utilities
- BrowserSessionPool: Pre-warmed session pool management

Usage:
    from src.bot.engine.stealth import StealthEngine

    engine = StealthEngine()
    async with engine.create_session(proxy=proxy_config) as session:
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
    # Browser Launchers
    "BaseBrowserLauncher",
    "CamoufoxLauncher",
    "PatchrightLauncher",
    "SeleniumBaseUCLauncher",
    "BrowserTier",
]
