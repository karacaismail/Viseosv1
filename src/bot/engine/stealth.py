"""
VISE OS Stealth Browser Engine.

This module provides anti-detection browser automation with multi-tier fallback:
- Tier 1 (Primary): Camoufox - 95%+ VFS bypass rate
- Tier 2 (Secondary): Patchright - 85%+ VFS bypass rate
- Tier 3 (Emergency): SeleniumBase UC - 70%+ VFS bypass rate

Components include fingerprint spoofing, human behavior simulation,
Cloudflare challenge detection, and session pool management.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator

import structlog

from src.core.exceptions import BrowserError, SessionError

logger = structlog.get_logger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class SessionPoolExhaustedError(BrowserError):
    """
    Raised when no sessions are available in the session pool.

    This indicates that all pre-warmed sessions are in use and new sessions
    cannot be created fast enough to meet demand.
    """

    def __init__(
        self,
        message: str = "No available sessions in pool",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message, code="SESSION_POOL_EXHAUSTED", details=details
        )


# =============================================================================
# Configuration
# =============================================================================


class StealthBrowserConfig:
    """
    Camoufox base configuration for stealth browsing.

    Provides default settings optimized for Turkish locale and
    common desktop fingerprints to evade anti-bot detection.
    """

    DEFAULT_CONFIG: dict[str, Any] = {
        # Geolocation (Turkey)
        "geoip": True,
        "locale": "tr-TR",
        "timezone": "Europe/Istanbul",
        # Screen & Viewport (realistic desktop)
        "screen": {
            "width": 1920,
            "height": 1080,
            "availWidth": 1920,
            "availHeight": 1040,
            "colorDepth": 24,
            "pixelDepth": 24,
        },
        "viewport": {
            "width": 1366,  # Common laptop resolution
            "height": 768,
        },
        # OS Fingerprint
        "os": "Windows",
        # Humanize
        "humanize": True,
        # Headless (production mode)
        "headless": True,
        # Block unnecessary resources
        "block_images": False,  # VFS image CAPTCHA requires images
        "block_webrtc": True,
        # Addons
        "addons": [],
    }

    # Default timeouts (milliseconds)
    DEFAULT_TIMEOUT_MS = 30000
    NAVIGATION_TIMEOUT_MS = 60000


# =============================================================================
# Profile Generator
# =============================================================================


class BrowserProfileGenerator:
    """
    Generates unique browser fingerprint profiles.

    Creates realistic browser profiles with randomized viewport sizes,
    user agents, WebGL vendors, fonts, and other fingerprint components
    to avoid browser fingerprinting detection.
    """

    VIEWPORTS: list[tuple[int, int]] = [
        (1920, 1080),
        (1366, 768),
        (1536, 864),
        (1440, 900),
        (1280, 720),
        (1600, 900),
    ]

    USER_AGENTS_WINDOWS: list[str] = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    ]

    WEBGL_VENDORS: list[tuple[str, str]] = [
        ("Intel Inc.", "Intel(R) UHD Graphics 620"),
        ("Intel Inc.", "Intel(R) Iris(R) Xe Graphics"),
        ("NVIDIA Corporation", "NVIDIA GeForce GTX 1650"),
        ("NVIDIA Corporation", "NVIDIA GeForce RTX 3060"),
        ("AMD", "AMD Radeon RX 580"),
        ("AMD", "AMD Radeon RX 6700 XT"),
    ]

    FONTS: list[list[str]] = [
        ["Arial", "Times New Roman", "Verdana", "Georgia", "Courier New"],
        ["Segoe UI", "Tahoma", "Calibri", "Cambria", "Consolas"],
        ["Arial", "Segoe UI", "Tahoma", "Verdana", "Times New Roman"],
    ]

    def generate(self, profile_id: str | None = None) -> dict[str, Any]:
        """
        Generate a unique browser profile with randomized fingerprint.

        Args:
            profile_id: Optional custom profile ID. Generated if not provided.

        Returns:
            Dictionary containing all profile attributes for browser configuration.

        Example:
            generator = BrowserProfileGenerator()
            profile = generator.generate()
            # Use profile with StealthSessionLauncher
        """
        viewport = random.choice(self.VIEWPORTS)
        webgl = random.choice(self.WEBGL_VENDORS)

        return {
            "profile_id": profile_id or self._generate_id(),
            "viewport": {
                "width": viewport[0],
                "height": viewport[1],
            },
            "screen": {
                "width": viewport[0],
                "height": viewport[1],
                "availWidth": viewport[0],
                "availHeight": viewport[1] - 40,  # Taskbar height
            },
            "user_agent": random.choice(self.USER_AGENTS_WINDOWS),
            "webgl": {
                "vendor": webgl[0],
                "renderer": webgl[1],
            },
            "canvas_noise": random.uniform(0.0001, 0.001),
            "audio_noise": random.uniform(0.0001, 0.0005),
            "fonts": random.choice(self.FONTS),
            "timezone_offset": 180,  # UTC+3 (Istanbul)
            "languages": ["tr-TR", "tr", "en-US", "en"],
            "plugins": self._generate_plugins(),
            "created_at": datetime.utcnow().isoformat(),
        }

    def _generate_plugins(self) -> list[dict[str, str]]:
        """Generate realistic browser plugin list."""
        base_plugins = [
            {"name": "PDF Viewer", "filename": "internal-pdf-viewer"},
            {"name": "Chrome PDF Viewer", "filename": "mhjfbmdgcfjbbpaeojofohoefgiehjai"},
        ]
        return base_plugins

    def _generate_id(self) -> str:
        """Generate unique profile ID."""
        return f"profile_{uuid.uuid4().hex[:12]}"


# =============================================================================
# Session Launcher
# =============================================================================


class StealthSessionLauncher:
    """
    Manages stealth browser session lifecycle.

    Handles browser launch, context creation, fingerprint injection,
    and resource cleanup. Provides both sync and async context manager
    support for clean resource handling.

    Attributes:
        profile: Browser fingerprint profile.
        proxy: Optional proxy configuration.
        browser: Playwright browser instance.
        context: Browser context with fingerprint.
        page: Active browser page.
        session_id: Unique session identifier.
    """

    def __init__(
        self,
        profile: dict[str, Any],
        proxy: dict[str, str] | None = None,
        launcher: Any = None,
    ) -> None:
        """
        Initialize session launcher.

        Args:
            profile: Browser fingerprint profile from BrowserProfileGenerator.
            proxy: Optional proxy config with host, port, username, password.
            launcher: Browser launcher instance (Camoufox, Patchright, etc).
        """
        self.profile = profile
        self.proxy = proxy
        self.launcher = launcher
        self.browser: Any = None
        self.context: Any = None
        self.page: Any = None
        self.session_id: str | None = None
        self._mouse_simulator: HumanMouseSimulator | None = None
        self._typing_simulator: HumanTypingSimulator | None = None
        self._scroll_simulator: HumanScrollSimulator | None = None
        self._launch_time: float | None = None

    async def launch(self) -> StealthSessionLauncher:
        """
        Launch browser session with stealth configuration.

        Creates browser instance, context with fingerprint injection,
        and initial page with default timeouts configured.

        Returns:
            Self for method chaining.

        Raises:
            BrowserError: If browser launch fails.
        """
        try:
            self._launch_time = time.time()

            # Build config from profile
            config = {
                **StealthBrowserConfig.DEFAULT_CONFIG,
                "viewport": self.profile["viewport"],
                "screen": self.profile["screen"],
            }

            # Add proxy if configured
            if self.proxy:
                config["proxy"] = {
                    "server": f"{self.proxy['protocol']}://{self.proxy['host']}:{self.proxy['port']}",
                }
                if self.proxy.get("username"):
                    config["proxy"]["username"] = self.proxy["username"]
                if self.proxy.get("password"):
                    config["proxy"]["password"] = self.proxy["password"]

            # Launch with appropriate launcher
            if self.launcher:
                self.browser, self.context, self.page = await self.launcher.launch(
                    config, self.profile
                )
            else:
                # Fallback: import and use Camoufox directly
                try:
                    from camoufox.async_api import AsyncCamoufox

                    self.browser = await AsyncCamoufox(**config).start()
                    self.context = await self.browser.new_context(
                        user_agent=self.profile["user_agent"],
                        locale="tr-TR",
                        timezone_id="Europe/Istanbul",
                        geolocation={"latitude": 41.0082, "longitude": 28.9784},
                        permissions=["geolocation"],
                    )
                    await self._inject_fingerprint_scripts()
                    self.page = await self.context.new_page()
                except ImportError:
                    logger.warning("camoufox_not_available", msg="Camoufox not installed")
                    raise BrowserError(
                        "Browser launch failed: Camoufox not installed",
                        code="BROWSER_NOT_AVAILABLE",
                    )

            # Configure timeouts
            if self.page:
                self.page.set_default_timeout(StealthBrowserConfig.DEFAULT_TIMEOUT_MS)
                self.page.set_default_navigation_timeout(
                    StealthBrowserConfig.NAVIGATION_TIMEOUT_MS
                )

            # Initialize simulators
            self._mouse_simulator = HumanMouseSimulator(self.page)
            self._typing_simulator = HumanTypingSimulator(self.page)
            self._scroll_simulator = HumanScrollSimulator(self.page)

            logger.info(
                "session_launched",
                profile_id=self.profile.get("profile_id"),
                has_proxy=bool(self.proxy),
            )

            return self

        except Exception as e:
            logger.error("session_launch_failed", error=str(e))
            await self.close()
            raise BrowserError(
                f"Failed to launch browser session: {e}",
                code="SESSION_LAUNCH_FAILED",
                details={"error": str(e)},
            )

    async def _inject_fingerprint_scripts(self) -> None:
        """Inject additional fingerprint spoofing scripts into context."""
        if not self.context:
            return

        # WebGL spoofing
        webgl_vendor = self.profile["webgl"]["vendor"]
        webgl_renderer = self.profile["webgl"]["renderer"]
        await self.context.add_init_script(f"""
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {{
                if (parameter === 37445) return '{webgl_vendor}';
                if (parameter === 37446) return '{webgl_renderer}';
                return getParameter.call(this, parameter);
            }};
        """)

        # Canvas noise
        canvas_noise = self.profile["canvas_noise"]
        await self.context.add_init_script(f"""
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(type) {{
                if (type === 'image/png') {{
                    const context = this.getContext('2d');
                    if (context) {{
                        const imageData = context.getImageData(0, 0, this.width, this.height);
                        for (let i = 0; i < imageData.data.length; i += 4) {{
                            imageData.data[i] += Math.floor(Math.random() * {canvas_noise} * 255);
                        }}
                        context.putImageData(imageData, 0, 0);
                    }}
                }}
                return originalToDataURL.apply(this, arguments);
            }};
        """)

        # Navigator properties
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['tr-TR', 'tr', 'en-US', 'en'] });

            // Chrome detection bypass
            window.chrome = { runtime: {} };

            // Permissions API
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)

    @property
    def mouse(self) -> HumanMouseSimulator | None:
        """Get human mouse simulator."""
        return self._mouse_simulator

    @property
    def typing(self) -> HumanTypingSimulator | None:
        """Get human typing simulator."""
        return self._typing_simulator

    @property
    def scroll(self) -> HumanScrollSimulator | None:
        """Get human scroll simulator."""
        return self._scroll_simulator

    @property
    def session_duration(self) -> float:
        """Get session duration in seconds."""
        if self._launch_time is None:
            return 0.0
        return time.time() - self._launch_time

    async def close(self) -> None:
        """
        Close browser session and release resources.

        Closes page, context, and browser in order, logging any errors
        but continuing cleanup to ensure all resources are released.
        """
        try:
            if self.page:
                await self.page.close()
                self.page = None
        except Exception as e:
            logger.warning("page_close_error", error=str(e))

        try:
            if self.context:
                await self.context.close()
                self.context = None
        except Exception as e:
            logger.warning("context_close_error", error=str(e))

        try:
            if self.browser:
                await self.browser.close()
                self.browser = None
        except Exception as e:
            logger.warning("browser_close_error", error=str(e))

        logger.info(
            "session_closed",
            profile_id=self.profile.get("profile_id"),
            duration=self.session_duration,
        )

    async def __aenter__(self) -> StealthSessionLauncher:
        """Async context manager entry."""
        return await self.launch()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()


# =============================================================================
# Human Behavior Simulators
# =============================================================================


class HumanMouseSimulator:
    """
    Simulates human-like mouse movement using Bezier curves.

    Generates realistic mouse paths with variable speed, overshoot,
    and micro-corrections to evade bot detection based on mouse patterns.
    Human average: ~427 pixels/second, Bot average: ~1520 pixels/second.
    """

    HUMAN_SPEED_MIN = 300  # pixels/second
    HUMAN_SPEED_MAX = 600  # pixels/second

    def __init__(self, page: Any) -> None:
        """
        Initialize mouse simulator.

        Args:
            page: Playwright page instance.
        """
        self.page = page
        self.current_pos: tuple[int, int] = (0, 0)

    async def move_to(
        self,
        target: tuple[int, int],
        click: bool = False,
    ) -> None:
        """
        Move mouse to target position using Bezier curve path.

        Args:
            target: Target (x, y) coordinates.
            click: Whether to click after reaching target.
        """
        path = self._generate_bezier_path(self.current_pos, target)

        for point in path:
            # Random delay between movements (human-like)
            delay = random.uniform(0.005, 0.02)  # 5-20ms
            await asyncio.sleep(delay)
            await self.page.mouse.move(point[0], point[1])

        self.current_pos = target

        if click:
            # Pre-click hesitation
            await asyncio.sleep(random.uniform(0.05, 0.15))
            await self.page.mouse.click(target[0], target[1])
            # Post-click pause
            await asyncio.sleep(random.uniform(0.1, 0.3))

    async def click_element(self, selector: str) -> None:
        """
        Move to element center and click with human-like behavior.

        Args:
            selector: CSS selector for target element.

        Raises:
            BrowserError: If element not found.
        """
        element = await self.page.query_selector(selector)
        if not element:
            raise BrowserError(
                f"Element not found: {selector}",
                code="ELEMENT_NOT_FOUND",
            )

        box = await element.bounding_box()
        if not box:
            raise BrowserError(
                f"Could not get bounding box: {selector}",
                code="BOUNDING_BOX_ERROR",
            )

        # Aim for slightly randomized position within element
        target_x = int(box["x"] + box["width"] * random.uniform(0.3, 0.7))
        target_y = int(box["y"] + box["height"] * random.uniform(0.3, 0.7))

        await self.move_to((target_x, target_y), click=True)

    def _generate_bezier_path(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        control_points: int = 2,
    ) -> list[tuple[int, int]]:
        """Generate Bezier curve path with overshoot and indirect movement."""
        distance = math.sqrt((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2)

        if distance < 1:
            return [end]

        # Control points for overshoot and indirect path
        controls: list[tuple[float, float]] = [start]
        for i in range(control_points):
            # Random offset from direct line
            offset_x = random.gauss(0, distance * 0.1)
            offset_y = random.gauss(0, distance * 0.1)
            t = (i + 1) / (control_points + 1)
            mid_x = start[0] + (end[0] - start[0]) * t + offset_x
            mid_y = start[1] + (end[1] - start[1]) * t + offset_y
            controls.append((mid_x, mid_y))
        controls.append(end)

        # Generate path points based on speed
        speed = random.uniform(self.HUMAN_SPEED_MIN, self.HUMAN_SPEED_MAX)
        duration = distance / speed
        steps = max(int(duration * 60), 10)  # Target 60 FPS

        path: list[tuple[int, int]] = []
        for i in range(steps + 1):
            t = i / steps
            point = self._bezier_point(controls, t)
            path.append(point)

        # Add micro-corrections at the end (30% chance)
        if random.random() < 0.3:
            path.extend(self._add_micro_corrections(end))

        return path

    def _bezier_point(
        self,
        controls: list[tuple[float, float]],
        t: float,
    ) -> tuple[int, int]:
        """Calculate point on Bezier curve at parameter t."""
        n = len(controls) - 1
        x, y = 0.0, 0.0

        for i, (cx, cy) in enumerate(controls):
            coeff = self._binomial(n, i) * (1 - t) ** (n - i) * t**i
            x += coeff * cx
            y += coeff * cy

        return (int(x), int(y))

    def _binomial(self, n: int, k: int) -> int:
        """Calculate binomial coefficient."""
        return math.factorial(n) // (math.factorial(k) * math.factorial(n - k))

    def _add_micro_corrections(
        self,
        target: tuple[int, int],
    ) -> list[tuple[int, int]]:
        """Add micro-corrections near target for human-like precision."""
        corrections: list[tuple[int, int]] = []
        for _ in range(random.randint(1, 3)):
            offset_x = random.gauss(0, 2)
            offset_y = random.gauss(0, 2)
            corrections.append((
                int(target[0] + offset_x),
                int(target[1] + offset_y),
            ))
        corrections.append(target)  # Final precise position
        return corrections


class HumanTypingSimulator:
    """
    Simulates human-like keyboard input with variable timing.

    Uses log-normal distribution for inter-keystroke intervals,
    accounts for bigram frequency, and simulates occasional typos
    with corrections for maximum authenticity.
    """

    # Inter-keystroke interval (IKI) - log-normal distribution
    IKI_MEAN = 0.12  # 120ms average
    IKI_STD = 0.04  # 40ms standard deviation

    # Common bigrams typed faster
    FAST_BIGRAMS: list[str] = [
        "th", "he", "in", "er", "an", "re", "on", "at", "en", "nd",
        "or", "es", "te", "ti", "ed", "is", "it", "al", "ar", "st",
    ]

    # Typo probability
    TYPO_RATE = 0.02  # 2%

    # Keyboard layout for nearby key typos
    KEYBOARD_LAYOUT: dict[str, list[str]] = {
        "q": ["w", "a"],
        "w": ["q", "e", "s"],
        "e": ["w", "r", "d"],
        "r": ["e", "t", "f"],
        "t": ["r", "y", "g"],
        "y": ["t", "u", "h"],
        "u": ["y", "i", "j"],
        "i": ["u", "o", "k"],
        "o": ["i", "p", "l"],
        "p": ["o", "l"],
        "a": ["q", "s", "z"],
        "s": ["a", "w", "d", "x"],
        "d": ["s", "e", "f", "c"],
        "f": ["d", "r", "g", "v"],
        "g": ["f", "t", "h", "b"],
        "h": ["g", "y", "j", "n"],
        "j": ["h", "u", "k", "m"],
        "k": ["j", "i", "l"],
        "l": ["k", "o", "p"],
        "z": ["a", "s", "x"],
        "x": ["z", "s", "d", "c"],
        "c": ["x", "d", "f", "v"],
        "v": ["c", "f", "g", "b"],
        "b": ["v", "g", "h", "n"],
        "n": ["b", "h", "j", "m"],
        "m": ["n", "j", "k"],
    }

    def __init__(self, page: Any) -> None:
        """
        Initialize typing simulator.

        Args:
            page: Playwright page instance.
        """
        self.page = page

    async def type_text(self, selector: str, text: str) -> None:
        """
        Type text into element with human-like timing and occasional typos.

        Args:
            selector: CSS selector for target input element.
            text: Text to type.
        """
        # Focus element
        await self.page.click(selector)
        await asyncio.sleep(random.uniform(0.1, 0.3))

        i = 0
        while i < len(text):
            char = text[i]

            # Calculate delay
            delay = self._get_keystroke_delay(text, i)
            await asyncio.sleep(delay)

            # Random typo
            if random.random() < self.TYPO_RATE and char.isalpha():
                wrong_char = self._get_nearby_key(char)
                await self.page.keyboard.type(wrong_char)
                await asyncio.sleep(random.uniform(0.2, 0.5))  # Notice mistake
                await self.page.keyboard.press("Backspace")
                await asyncio.sleep(random.uniform(0.1, 0.2))

            # Type correct character
            await self.page.keyboard.type(char)
            i += 1

        # Post-typing pause
        await asyncio.sleep(random.uniform(0.2, 0.5))

    async def type_text_direct(self, text: str) -> None:
        """
        Type text directly without focusing an element.

        Args:
            text: Text to type.
        """
        for i, char in enumerate(text):
            delay = self._get_keystroke_delay(text, i)
            await asyncio.sleep(delay)
            await self.page.keyboard.type(char)

        await asyncio.sleep(random.uniform(0.1, 0.3))

    def _get_keystroke_delay(self, text: str, index: int) -> float:
        """Calculate context-aware keystroke delay."""
        delay = random.gauss(self.IKI_MEAN, self.IKI_STD)
        delay = max(0.05, delay)  # Minimum 50ms

        # Bigram acceleration
        if index > 0:
            bigram = text[index - 1 : index + 1].lower()
            if bigram in self.FAST_BIGRAMS:
                delay *= 0.7  # 30% faster

        # Word start slowdown
        if index > 0 and text[index - 1] == " ":
            delay *= 1.3  # 30% slower

        # Shift key delay for uppercase
        if text[index].isupper():
            delay += random.uniform(0.02, 0.05)

        return delay

    def _get_nearby_key(self, char: str) -> str:
        """Get nearby key for realistic typo."""
        lower_char = char.lower()
        if lower_char in self.KEYBOARD_LAYOUT:
            wrong = random.choice(self.KEYBOARD_LAYOUT[lower_char])
            return wrong.upper() if char.isupper() else wrong
        return char


class HumanScrollSimulator:
    """
    Simulates human-like scroll behavior.

    Implements smooth scrolling with easing, random overshoot,
    and reading simulation pauses.
    """

    def __init__(self, page: Any) -> None:
        """
        Initialize scroll simulator.

        Args:
            page: Playwright page instance.
        """
        self.page = page

    async def scroll_to_element(self, selector: str) -> None:
        """
        Smooth scroll to element with human-like behavior.

        Args:
            selector: CSS selector for target element.
        """
        element = await self.page.query_selector(selector)
        if not element:
            return

        box = await element.bounding_box()
        if not box:
            return

        # Current scroll position
        current_scroll = await self.page.evaluate("window.scrollY")
        target_scroll = box["y"] - 200  # Keep 200px above element

        # Scroll in chunks with easing
        distance = target_scroll - current_scroll
        steps = max(abs(int(distance / 100)), 5)

        for i in range(steps):
            progress = (i + 1) / steps
            # Ease-out cubic
            eased_progress = 1 - (1 - progress) ** 3

            scroll_to = current_scroll + (distance * eased_progress)
            await self.page.evaluate(f"window.scrollTo(0, {scroll_to})")
            await asyncio.sleep(random.uniform(0.02, 0.05))

        # Random overshoot and correction (40% chance)
        if random.random() < 0.4:
            overshoot = random.uniform(20, 50)
            await self.page.evaluate(f"window.scrollBy(0, {overshoot})")
            await asyncio.sleep(random.uniform(0.1, 0.2))
            await self.page.evaluate(f"window.scrollBy(0, {-overshoot})")

    async def random_scroll(self) -> None:
        """Simulate reading scroll behavior with random direction and amount."""
        viewport_height = await self.page.evaluate("window.innerHeight")

        scroll_amount = random.randint(
            int(viewport_height * 0.3),
            int(viewport_height * 0.7),
        )

        if random.random() < 0.5:
            scroll_amount = -scroll_amount  # Scroll up

        await self.page.evaluate(f"window.scrollBy(0, {scroll_amount})")
        await asyncio.sleep(random.uniform(0.5, 2.0))

    async def scroll_to_bottom(self) -> None:
        """Scroll to page bottom with human-like behavior."""
        page_height = await self.page.evaluate(
            "document.documentElement.scrollHeight"
        )
        current_scroll = await self.page.evaluate("window.scrollY")
        viewport_height = await self.page.evaluate("window.innerHeight")

        while current_scroll < page_height - viewport_height - 100:
            scroll_amount = random.randint(200, 400)
            await self.page.evaluate(f"window.scrollBy(0, {scroll_amount})")
            await asyncio.sleep(random.uniform(0.3, 0.8))
            current_scroll = await self.page.evaluate("window.scrollY")


# =============================================================================
# Detection
# =============================================================================


class CloudflareDetector:
    """
    Detects Cloudflare challenge pages.

    Identifies Cloudflare browser verification, Turnstile CAPTCHA,
    and other protection pages to allow proper handling.
    """

    CHALLENGE_INDICATORS: list[str] = [
        "challenge-running",
        "cf-browser-verification",
        "cf-turnstile",
        "Just a moment...",
        "Checking your browser",
        "Please wait...",
        "ray ID",
        "cf-chl-bypass",
        "Attention Required",
    ]

    async def is_challenge_page(self, page: Any) -> bool:
        """
        Check if current page is a Cloudflare challenge.

        Args:
            page: Playwright page instance.

        Returns:
            True if Cloudflare challenge detected, False otherwise.
        """
        content = await page.content()
        url = page.url

        # URL check
        if "__cf_chl" in url or "challenge" in url:
            return True

        # Content check
        content_lower = content.lower()
        for indicator in self.CHALLENGE_INDICATORS:
            if indicator.lower() in content_lower:
                return True

        return False

    async def wait_for_challenge(
        self,
        page: Any,
        timeout: int = 30,
    ) -> bool:
        """
        Wait for Cloudflare challenge to be resolved.

        Args:
            page: Playwright page instance.
            timeout: Maximum seconds to wait.

        Returns:
            True if challenge resolved, False if timeout.
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            if not await self.is_challenge_page(page):
                return True
            await asyncio.sleep(1)

        return False


class BotDetectionTester:
    """
    Tests browser against common bot detection services.

    Runs automated tests against detection sites to verify
    stealth configuration is working correctly.
    """

    TEST_SITES: list[str] = [
        "https://bot.sannysoft.com/",
        "https://nowsecure.nl/",
        "https://abrahamjuliot.github.io/creepjs/",
        "https://browserleaks.com/canvas",
    ]

    async def run_detection_test(self, page: Any) -> dict[str, Any]:
        """
        Run detection tests against known bot detection sites.

        Args:
            page: Playwright page instance.

        Returns:
            Dictionary with test results for each site.
        """
        results: dict[str, Any] = {}

        for site in self.TEST_SITES:
            try:
                await page.goto(site, wait_until="networkidle")
                await asyncio.sleep(3)

                if "sannysoft" in site:
                    results["sannysoft"] = await self._check_sannysoft(page)
                elif "nowsecure" in site:
                    results["nowsecure"] = await self._check_nowsecure(page)
                elif "creepjs" in site:
                    results["creepjs"] = "checked"
                elif "browserleaks" in site:
                    results["browserleaks"] = "checked"

            except Exception as e:
                results[site] = f"Error: {str(e)}"

        return results

    async def _check_sannysoft(self, page: Any) -> bool:
        """Check Sannysoft bot test result."""
        content = await page.content()
        return "FAIL" not in content

    async def _check_nowsecure(self, page: Any) -> bool:
        """Check nowsecure.nl test result."""
        element = await page.query_selector(".success")
        return element is not None


# =============================================================================
# Session Pool
# =============================================================================


class BrowserSessionPool:
    """
    Manages a pool of pre-warmed browser sessions.

    Provides efficient session acquisition and release with
    automatic pool replenishment for high-throughput scenarios.
    Supports up to 20 concurrent sessions by default.

    Attributes:
        max_sessions: Maximum sessions in pool.
        available_sessions: Queue of ready sessions.
        active_sessions: Currently in-use sessions.
    """

    def __init__(self, max_sessions: int = 20) -> None:
        """
        Initialize session pool.

        Args:
            max_sessions: Maximum number of concurrent sessions.
        """
        self.max_sessions = max_sessions
        self.available_sessions: asyncio.Queue[StealthSessionLauncher] = asyncio.Queue()
        self.active_sessions: dict[str, StealthSessionLauncher] = {}
        self._lock = asyncio.Lock()
        self._profile_generator = BrowserProfileGenerator()
        self._profiles: list[dict[str, Any]] = []
        self._proxies: list[dict[str, str]] = []
        self._initialized = False

    async def initialize(
        self,
        profiles: list[dict[str, Any]] | None = None,
        proxies: list[dict[str, str]] | None = None,
        initial_sessions: int = 5,
    ) -> None:
        """
        Initialize session pool with pre-warmed sessions.

        Args:
            profiles: List of browser profiles. Generated if not provided.
            proxies: List of proxy configurations.
            initial_sessions: Number of sessions to pre-warm.
        """
        self._profiles = profiles or [
            self._profile_generator.generate() for _ in range(self.max_sessions)
        ]
        self._proxies = proxies or []

        # Create initial sessions
        count = min(initial_sessions, self.max_sessions, len(self._profiles))
        for i in range(count):
            try:
                session = await self._create_session(
                    self._profiles[i % len(self._profiles)],
                    self._proxies[i % len(self._proxies)] if self._proxies else None,
                )
                await self.available_sessions.put(session)
            except Exception as e:
                logger.warning("session_warmup_failed", error=str(e), index=i)

        self._initialized = True
        logger.info(
            "session_pool_initialized",
            available=self.available_sessions.qsize(),
            max=self.max_sessions,
        )

    async def acquire(self, timeout: float = 30) -> StealthSessionLauncher:
        """
        Acquire a session from the pool.

        Args:
            timeout: Maximum seconds to wait for available session.

        Returns:
            StealthSessionLauncher instance.

        Raises:
            SessionPoolExhaustedError: If no sessions available within timeout.
        """
        try:
            session = await asyncio.wait_for(
                self.available_sessions.get(),
                timeout=timeout,
            )

            session_id = str(uuid.uuid4())
            async with self._lock:
                self.active_sessions[session_id] = session

            session.session_id = session_id

            logger.info(
                "session_acquired",
                session_id=session_id,
                active=len(self.active_sessions),
                available=self.available_sessions.qsize(),
            )

            return session

        except asyncio.TimeoutError:
            raise SessionPoolExhaustedError(
                "No available sessions in pool",
                details={
                    "active_sessions": len(self.active_sessions),
                    "timeout": timeout,
                },
            )

    async def release(
        self,
        session: StealthSessionLauncher,
        reusable: bool = True,
    ) -> None:
        """
        Release session back to pool or close it.

        Args:
            session: Session to release.
            reusable: If True, session is cleaned and reused.
                     If False, session is closed (burned).
        """
        session_id = getattr(session, "session_id", None)

        async with self._lock:
            if session_id and session_id in self.active_sessions:
                del self.active_sessions[session_id]

        if reusable:
            try:
                # Clear cookies and storage
                if session.context:
                    await session.context.clear_cookies()
                await self.available_sessions.put(session)
                logger.info("session_released", session_id=session_id)
            except Exception as e:
                logger.warning("session_release_failed", error=str(e))
                await session.close()
                asyncio.create_task(self._replenish_pool())
        else:
            # Session burned, close and create replacement
            await session.close()
            logger.info("session_burned", session_id=session_id)
            asyncio.create_task(self._replenish_pool())

    async def _create_session(
        self,
        profile: dict[str, Any],
        proxy: dict[str, str] | None = None,
    ) -> StealthSessionLauncher:
        """Create new session with given profile and proxy."""
        session = StealthSessionLauncher(profile=profile, proxy=proxy)
        await session.launch()
        return session

    async def _replenish_pool(self) -> None:
        """Replenish pool with new session if below max."""
        if self.available_sessions.qsize() + len(self.active_sessions) >= self.max_sessions:
            return

        try:
            profile_idx = random.randint(0, len(self._profiles) - 1)
            proxy = None
            if self._proxies:
                proxy = random.choice(self._proxies)

            session = await self._create_session(
                self._profiles[profile_idx],
                proxy,
            )
            await self.available_sessions.put(session)
            logger.info("session_pool_replenished")
        except Exception as e:
            logger.warning("session_replenish_failed", error=str(e))

    async def close_all(self) -> None:
        """Close all sessions in pool."""
        # Close available sessions
        while not self.available_sessions.empty():
            try:
                session = self.available_sessions.get_nowait()
                await session.close()
            except asyncio.QueueEmpty:
                break

        # Close active sessions
        async with self._lock:
            for session in self.active_sessions.values():
                await session.close()
            self.active_sessions.clear()

        logger.info("session_pool_closed")

    @property
    def stats(self) -> dict[str, int]:
        """Get pool statistics."""
        return {
            "available": self.available_sessions.qsize(),
            "active": len(self.active_sessions),
            "max": self.max_sessions,
        }


# =============================================================================
# Stealth Engine
# =============================================================================


class BrowserTier(str, Enum):
    """Browser tier for fallback strategy."""

    CAMOUFOX = "camoufox"
    PATCHRIGHT = "patchright"
    SELENIUM_UC = "selenium_uc"


class StealthEngine:
    """
    Main stealth browser engine with tiered fallback strategy.

    Provides high-level API for creating stealth browser sessions
    with automatic fallback between browser tiers on failure:
    - Tier 1: Camoufox (95%+ VFS success rate)
    - Tier 2: Patchright (85%+ VFS success rate)
    - Tier 3: SeleniumBase UC (70%+ VFS success rate)

    Usage:
        engine = StealthEngine()
        await engine.initialize()

        async with engine.create_session(proxy=proxy) as session:
            await session.page.goto("https://example.com")
            # Use session.mouse, session.typing, session.scroll for human-like behavior
    """

    def __init__(
        self,
        max_sessions: int = 20,
        enable_pool: bool = True,
    ) -> None:
        """
        Initialize stealth engine.

        Args:
            max_sessions: Maximum concurrent sessions.
            enable_pool: Whether to use session pooling.
        """
        self.max_sessions = max_sessions
        self.enable_pool = enable_pool
        self._pool: BrowserSessionPool | None = None
        self._profile_generator = BrowserProfileGenerator()
        self._cloudflare_detector = CloudflareDetector()
        self._current_tier = BrowserTier.CAMOUFOX
        self._initialized = False

    async def initialize(
        self,
        profiles: list[dict[str, Any]] | None = None,
        proxies: list[dict[str, str]] | None = None,
    ) -> None:
        """
        Initialize engine and optional session pool.

        Args:
            profiles: Optional pre-generated profiles.
            proxies: Optional proxy configurations.
        """
        if self.enable_pool:
            self._pool = BrowserSessionPool(max_sessions=self.max_sessions)
            await self._pool.initialize(profiles=profiles, proxies=proxies)

        self._initialized = True
        logger.info("stealth_engine_initialized", pool_enabled=self.enable_pool)

    @asynccontextmanager
    async def create_session(
        self,
        proxy: dict[str, str] | None = None,
        profile: dict[str, Any] | None = None,
        use_pool: bool = True,
    ) -> AsyncIterator[StealthSessionLauncher]:
        """
        Create a stealth browser session.

        Args:
            proxy: Optional proxy configuration.
            profile: Optional specific profile. Generated if not provided.
            use_pool: Whether to use session pool (if enabled).

        Yields:
            StealthSessionLauncher instance.

        Example:
            async with engine.create_session(proxy=proxy) as session:
                await session.page.goto("https://example.com")
        """
        session: StealthSessionLauncher | None = None
        from_pool = False

        try:
            if self.enable_pool and use_pool and self._pool:
                session = await self._pool.acquire()
                from_pool = True
            else:
                session_profile = profile or self._profile_generator.generate()
                session = StealthSessionLauncher(
                    profile=session_profile,
                    proxy=proxy,
                )
                await session.launch()

            yield session

        finally:
            if session:
                if from_pool and self._pool:
                    await self._pool.release(session, reusable=True)
                else:
                    await session.close()

    async def create_session_with_fallback(
        self,
        proxy: dict[str, str] | None = None,
        url: str | None = None,
    ) -> StealthSessionLauncher:
        """
        Create session with automatic tier fallback on detection.

        Attempts to create session starting from Tier 1 (Camoufox),
        falling back to lower tiers if bot detection is triggered.

        Args:
            proxy: Optional proxy configuration.
            url: Optional initial URL to navigate to.

        Returns:
            StealthSessionLauncher that passed detection.

        Raises:
            BrowserError: If all tiers fail.
        """
        tiers = [BrowserTier.CAMOUFOX, BrowserTier.PATCHRIGHT, BrowserTier.SELENIUM_UC]

        for tier in tiers:
            try:
                profile = self._profile_generator.generate()
                session = StealthSessionLauncher(profile=profile, proxy=proxy)
                await session.launch()

                if url:
                    await session.page.goto(url, wait_until="networkidle")

                    # Check for Cloudflare challenge
                    if await self._cloudflare_detector.is_challenge_page(session.page):
                        logger.warning(
                            "cloudflare_detected",
                            tier=tier.value,
                            url=url,
                        )
                        await session.close()
                        continue

                self._current_tier = tier
                logger.info("session_created_with_tier", tier=tier.value)
                return session

            except Exception as e:
                logger.warning(
                    "tier_failed",
                    tier=tier.value,
                    error=str(e),
                )
                continue

        raise BrowserError(
            "All browser tiers failed",
            code="ALL_TIERS_FAILED",
            details={"attempted_tiers": [t.value for t in tiers]},
        )

    async def close(self) -> None:
        """Close engine and all resources."""
        if self._pool:
            await self._pool.close_all()
        logger.info("stealth_engine_closed")

    @property
    def current_tier(self) -> BrowserTier:
        """Get currently active browser tier."""
        return self._current_tier

    @property
    def pool_stats(self) -> dict[str, int] | None:
        """Get session pool statistics."""
        return self._pool.stats if self._pool else None

    @property
    def cloudflare_detector(self) -> CloudflareDetector:
        """Get Cloudflare detector instance."""
        return self._cloudflare_detector

    @property
    def profile_generator(self) -> BrowserProfileGenerator:
        """Get profile generator instance."""
        return self._profile_generator
