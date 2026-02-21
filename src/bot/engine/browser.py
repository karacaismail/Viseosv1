"""
VISE OS Browser Launchers.

Multi-tier browser launcher implementations for stealth automation:
- Tier 1: Camoufox (C++ native fingerprint injection)
- Tier 2: Patchright (Playwright undetected fork)
- Tier 3: SeleniumBase UC (Undetected ChromeDriver)

Each launcher implements a common interface for session creation
with browser-specific stealth configurations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

import structlog

from src.core.exceptions import BrowserError

logger = structlog.get_logger(__name__)


# =============================================================================
# Enums
# =============================================================================


class BrowserTier(str, Enum):
    """Browser tier for fallback strategy."""

    CAMOUFOX = "camoufox"
    PATCHRIGHT = "patchright"
    SELENIUM_UC = "selenium_uc"


class BrowserType(str, Enum):
    """Underlying browser type."""

    FIREFOX = "firefox"
    CHROMIUM = "chromium"
    WEBKIT = "webkit"


# =============================================================================
# Base Launcher
# =============================================================================


class BaseBrowserLauncher(ABC):
    """
    Abstract base class for browser launchers.

    Defines the common interface for all browser tier implementations.
    Subclasses must implement launch() and close() methods.

    Attributes:
        tier: The browser tier this launcher implements.
        browser_type: Underlying browser engine type.
        browser: Browser instance after launch.
        context: Browser context with fingerprint.
        page: Active browser page.
    """

    tier: BrowserTier
    browser_type: BrowserType

    def __init__(self) -> None:
        """Initialize launcher."""
        self.browser: Any = None
        self.context: Any = None
        self.page: Any = None
        self._playwright: Any = None

    @abstractmethod
    async def launch(
        self,
        config: dict[str, Any],
        profile: dict[str, Any],
    ) -> tuple[Any, Any, Any]:
        """
        Launch browser with stealth configuration.

        Args:
            config: Browser configuration from StealthBrowserConfig.
            profile: Fingerprint profile from BrowserProfileGenerator.

        Returns:
            Tuple of (browser, context, page) instances.

        Raises:
            BrowserError: If launch fails.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close browser and release resources."""
        pass

    async def inject_stealth_scripts(self, context: Any, profile: dict[str, Any]) -> None:
        """
        Inject stealth scripts into browser context.

        Args:
            context: Browser context to inject scripts into.
            profile: Profile containing fingerprint data.
        """
        # WebGL spoofing
        webgl_vendor = profile.get("webgl", {}).get("vendor", "Intel Inc.")
        webgl_renderer = profile.get("webgl", {}).get(
            "renderer", "Intel(R) UHD Graphics 620"
        )

        await context.add_init_script(f"""
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {{
                if (parameter === 37445) return '{webgl_vendor}';
                if (parameter === 37446) return '{webgl_renderer}';
                return getParameter.call(this, parameter);
            }};
        """)

        # Canvas noise
        canvas_noise = profile.get("canvas_noise", 0.0005)
        await context.add_init_script(f"""
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(type) {{
                if (type === 'image/png') {{
                    const context = this.getContext('2d');
                    if (context) {{
                        try {{
                            const imageData = context.getImageData(0, 0, this.width, this.height);
                            for (let i = 0; i < imageData.data.length; i += 4) {{
                                imageData.data[i] += Math.floor(Math.random() * {canvas_noise} * 255);
                            }}
                            context.putImageData(imageData, 0, 0);
                        }} catch(e) {{}}
                    }}
                }}
                return originalToDataURL.apply(this, arguments);
            }};
        """)

        # Navigator properties
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['tr-TR', 'tr', 'en-US', 'en']
            });

            // Chrome detection bypass
            window.chrome = { runtime: {} };

            // Permissions API
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );

            // Remove automation indicators
            delete navigator.__proto__.webdriver;
        """)


# =============================================================================
# Camoufox Launcher (Tier 1)
# =============================================================================


class CamoufoxLauncher(BaseBrowserLauncher):
    """
    Tier 1 browser launcher using Camoufox.

    Camoufox provides C++ native fingerprint injection that is
    impossible to detect via JavaScript hooks. Achieves 95%+
    success rate against VFS Global's Cloudflare protection.

    Features:
    - Hardware-level WebGL spoofing
    - Native canvas noise injection
    - Authentic Firefox TLS fingerprint
    - Built-in humanization
    """

    tier = BrowserTier.CAMOUFOX
    browser_type = BrowserType.FIREFOX

    async def launch(
        self,
        config: dict[str, Any],
        profile: dict[str, Any],
    ) -> tuple[Any, Any, Any]:
        """
        Launch Camoufox browser with stealth configuration.

        Args:
            config: Browser configuration with viewport, proxy, etc.
            profile: Fingerprint profile.

        Returns:
            Tuple of (browser, context, page).

        Raises:
            BrowserError: If Camoufox not installed or launch fails.
        """
        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError:
            raise BrowserError(
                "Camoufox not installed. Install with: pip install camoufox[geoip]",
                code="CAMOUFOX_NOT_INSTALLED",
            )

        try:
            # Prepare Camoufox config
            camoufox_config = {
                "geoip": config.get("geoip", True),
                "locale": config.get("locale", "tr-TR"),
                "os": config.get("os", "windows"),
                "humanize": config.get("humanize", True),
                "headless": config.get("headless", True),
                "block_webrtc": config.get("block_webrtc", True),
            }

            # Add viewport
            if "viewport" in config:
                camoufox_config["viewport"] = config["viewport"]

            # Add screen
            if "screen" in config:
                camoufox_config["screen"] = config["screen"]

            # Add proxy
            if "proxy" in config:
                camoufox_config["proxy"] = config["proxy"]

            # Launch browser
            self.browser = await AsyncCamoufox(**camoufox_config).start()

            # Create context
            self.context = await self.browser.new_context(
                user_agent=profile.get("user_agent"),
                locale="tr-TR",
                timezone_id="Europe/Istanbul",
                geolocation={"latitude": 41.0082, "longitude": 28.9784},
                permissions=["geolocation"],
            )

            # Inject additional stealth scripts
            await self.inject_stealth_scripts(self.context, profile)

            # Create page
            self.page = await self.context.new_page()

            logger.info("camoufox_launched", profile_id=profile.get("profile_id"))

            return self.browser, self.context, self.page

        except Exception as e:
            logger.error("camoufox_launch_failed", error=str(e))
            await self.close()
            raise BrowserError(
                f"Failed to launch Camoufox: {e}",
                code="CAMOUFOX_LAUNCH_FAILED",
                details={"error": str(e)},
            )

    async def close(self) -> None:
        """Close Camoufox browser and resources."""
        try:
            if self.page:
                await self.page.close()
                self.page = None
        except Exception as e:
            logger.warning("camoufox_page_close_error", error=str(e))

        try:
            if self.context:
                await self.context.close()
                self.context = None
        except Exception as e:
            logger.warning("camoufox_context_close_error", error=str(e))

        try:
            if self.browser:
                await self.browser.close()
                self.browser = None
        except Exception as e:
            logger.warning("camoufox_browser_close_error", error=str(e))


# =============================================================================
# Patchright Launcher (Tier 2)
# =============================================================================


class PatchrightLauncher(BaseBrowserLauncher):
    """
    Tier 2 browser launcher using Patchright.

    Patchright is an undetected fork of Playwright with patches
    to bypass common automation detection. Uses Chromium which
    has wider compatibility but slightly lower stealth than Camoufox.

    Features:
    - Automated webdriver property removal
    - Chrome runtime emulation
    - Disabled automation flags
    """

    tier = BrowserTier.PATCHRIGHT
    browser_type = BrowserType.CHROMIUM

    async def launch(
        self,
        config: dict[str, Any],
        profile: dict[str, Any],
    ) -> tuple[Any, Any, Any]:
        """
        Launch Patchright browser with stealth configuration.

        Args:
            config: Browser configuration.
            profile: Fingerprint profile.

        Returns:
            Tuple of (browser, context, page).

        Raises:
            BrowserError: If Patchright not installed or launch fails.
        """
        try:
            from patchright.async_api import async_playwright
        except ImportError:
            raise BrowserError(
                "Patchright not installed. Install with: pip install patchright",
                code="PATCHRIGHT_NOT_INSTALLED",
            )

        try:
            self._playwright = await async_playwright().start()

            # Browser arguments
            browser_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-extensions",
                "--disable-gpu",
                "--disable-setuid-sandbox",
                "--window-size=1920,1080",
            ]

            # Add proxy if configured
            proxy_config = None
            if "proxy" in config:
                proxy = config["proxy"]
                proxy_config = {
                    "server": proxy.get("server"),
                }
                if proxy.get("username"):
                    proxy_config["username"] = proxy["username"]
                if proxy.get("password"):
                    proxy_config["password"] = proxy["password"]

            # Launch browser
            self.browser = await self._playwright.chromium.launch(
                headless=config.get("headless", True),
                args=browser_args,
                proxy=proxy_config,
            )

            # Create context
            viewport = config.get("viewport", {"width": 1920, "height": 1080})
            self.context = await self.browser.new_context(
                viewport=viewport,
                user_agent=profile.get("user_agent"),
                locale="tr-TR",
                timezone_id="Europe/Istanbul",
                geolocation={"latitude": 41.0082, "longitude": 28.9784},
                permissions=["geolocation"],
            )

            # Inject stealth scripts
            await self.inject_stealth_scripts(self.context, profile)

            # Create page
            self.page = await self.context.new_page()

            logger.info("patchright_launched", profile_id=profile.get("profile_id"))

            return self.browser, self.context, self.page

        except Exception as e:
            logger.error("patchright_launch_failed", error=str(e))
            await self.close()
            raise BrowserError(
                f"Failed to launch Patchright: {e}",
                code="PATCHRIGHT_LAUNCH_FAILED",
                details={"error": str(e)},
            )

    async def close(self) -> None:
        """Close Patchright browser and resources."""
        try:
            if self.page:
                await self.page.close()
                self.page = None
        except Exception as e:
            logger.warning("patchright_page_close_error", error=str(e))

        try:
            if self.context:
                await self.context.close()
                self.context = None
        except Exception as e:
            logger.warning("patchright_context_close_error", error=str(e))

        try:
            if self.browser:
                await self.browser.close()
                self.browser = None
        except Exception as e:
            logger.warning("patchright_browser_close_error", error=str(e))

        try:
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
        except Exception as e:
            logger.warning("patchright_playwright_close_error", error=str(e))


# =============================================================================
# SeleniumBase UC Launcher (Tier 3)
# =============================================================================


class SeleniumBaseUCLauncher(BaseBrowserLauncher):
    """
    Tier 3 browser launcher using SeleniumBase Undetected Chrome.

    SeleniumBase UC mode provides a fallback option using patched
    ChromeDriver. Lower success rate but wider browser compatibility.

    Features:
    - Undetected ChromeDriver
    - Automatic driver management
    - Stealth mode enabled
    """

    tier = BrowserTier.SELENIUM_UC
    browser_type = BrowserType.CHROMIUM

    def __init__(self) -> None:
        """Initialize SeleniumBase launcher."""
        super().__init__()
        self._sb: Any = None

    async def launch(
        self,
        config: dict[str, Any],
        profile: dict[str, Any],
    ) -> tuple[Any, Any, Any]:
        """
        Launch SeleniumBase UC browser.

        Note: SeleniumBase is synchronous, so this wraps sync operations.

        Args:
            config: Browser configuration.
            profile: Fingerprint profile.

        Returns:
            Tuple of (sb_instance, None, driver) - context is None for Selenium.

        Raises:
            BrowserError: If SeleniumBase not installed or launch fails.
        """
        try:
            from seleniumbase import SB
        except ImportError:
            raise BrowserError(
                "SeleniumBase not installed. Install with: pip install seleniumbase",
                code="SELENIUMBASE_NOT_INSTALLED",
            )

        try:
            # Build proxy string
            proxy_string = None
            if "proxy" in config:
                proxy = config["proxy"]
                server = proxy.get("server", "")
                if server:
                    # Extract host:port from server URL
                    if "://" in server:
                        server = server.split("://")[1]
                    if proxy.get("username"):
                        proxy_string = (
                            f"{proxy['username']}:{proxy['password']}@{server}"
                        )
                    else:
                        proxy_string = server

            # Create SeleniumBase instance
            self._sb = SB(
                uc=True,  # Undetected Chrome mode
                headless=config.get("headless", True),
                proxy=proxy_string,
                locale_code="tr-TR",
                agent=profile.get("user_agent"),
            )

            # Enter context
            self._sb.__enter__()
            self.browser = self._sb
            self.page = self._sb.driver

            logger.info("seleniumbase_launched", profile_id=profile.get("profile_id"))

            # Return (browser, context=None, page)
            return self._sb, None, self._sb.driver

        except Exception as e:
            logger.error("seleniumbase_launch_failed", error=str(e))
            await self.close()
            raise BrowserError(
                f"Failed to launch SeleniumBase UC: {e}",
                code="SELENIUMBASE_LAUNCH_FAILED",
                details={"error": str(e)},
            )

    async def close(self) -> None:
        """Close SeleniumBase browser."""
        try:
            if self._sb:
                self._sb.__exit__(None, None, None)
                self._sb = None
                self.browser = None
                self.page = None
        except Exception as e:
            logger.warning("seleniumbase_close_error", error=str(e))


# =============================================================================
# Launcher Factory
# =============================================================================


def get_launcher(tier: BrowserTier) -> BaseBrowserLauncher:
    """
    Get browser launcher for specified tier.

    Args:
        tier: Browser tier to get launcher for.

    Returns:
        Browser launcher instance.

    Raises:
        ValueError: If unknown tier specified.

    Example:
        launcher = get_launcher(BrowserTier.CAMOUFOX)
        browser, context, page = await launcher.launch(config, profile)
    """
    launchers: dict[BrowserTier, type[BaseBrowserLauncher]] = {
        BrowserTier.CAMOUFOX: CamoufoxLauncher,
        BrowserTier.PATCHRIGHT: PatchrightLauncher,
        BrowserTier.SELENIUM_UC: SeleniumBaseUCLauncher,
    }

    if tier not in launchers:
        raise ValueError(f"Unknown browser tier: {tier}")

    return launchers[tier]()


def get_all_launchers() -> list[BaseBrowserLauncher]:
    """
    Get all browser launchers in tier order.

    Returns:
        List of launcher instances from Tier 1 to Tier 3.
    """
    return [
        CamoufoxLauncher(),
        PatchrightLauncher(),
        SeleniumBaseUCLauncher(),
    ]


__all__ = [
    "BaseBrowserLauncher",
    "CamoufoxLauncher",
    "PatchrightLauncher",
    "SeleniumBaseUCLauncher",
    "BrowserTier",
    "BrowserType",
    "get_launcher",
    "get_all_launchers",
]
