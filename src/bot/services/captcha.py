"""
VISE OS CAPTCHA Solver Service.

Multi-provider CAPTCHA solving chain with failover, health tracking,
and cost optimization for high-volume bot operations.

Features:
- Multi-provider support: CapSolver (primary), 2Captcha, Anti-Captcha
- Provider health scoring with automatic failover
- CAPTCHA type detection: reCAPTCHA v2/v3, Turnstile, hCaptcha
- Token injection for solved CAPTCHAs
- Cost tracking and balance monitoring
- Site-specific handlers (VFS, iDATA, etc.)

Usage:
    from src.bot.services.captcha import CaptchaSolver

    solver = CaptchaSolver()
    await solver.initialize(configs)

    # Solve CAPTCHA
    result = await solver.solve(challenge)

    # Inject token into page
    if result.success:
        await solver.inject_token(page, result.token, challenge.captcha_type)
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

import httpx
import structlog

from src.core.exceptions import (
    CaptchaError,
    CaptchaProviderError,
    CaptchaSolveTimeoutError,
)

logger = structlog.get_logger(__name__)


# =============================================================================
# Enums
# =============================================================================


class CaptchaType(str, Enum):
    """Supported CAPTCHA types."""

    RECAPTCHA_V2 = "recaptcha_v2"
    RECAPTCHA_V3 = "recaptcha_v3"
    HCAPTCHA = "hcaptcha"
    TURNSTILE = "turnstile"


class ProviderStatus(str, Enum):
    """Provider health status."""

    ACTIVE = "active"
    DEGRADED = "degraded"
    DISABLED = "disabled"


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class CaptchaChallenge:
    """
    CAPTCHA challenge information.

    Contains all data needed to solve a CAPTCHA challenge.

    Attributes:
        captcha_type: Type of CAPTCHA (reCAPTCHA, Turnstile, etc.).
        site_key: The CAPTCHA site key from the page.
        page_url: URL of the page containing the CAPTCHA.
        action: Action parameter for reCAPTCHA v3.
        min_score: Minimum score for reCAPTCHA v3.
        invisible: Whether the CAPTCHA is invisible.
        proxy: Optional proxy configuration for solving.
    """

    captcha_type: CaptchaType
    site_key: str
    page_url: str
    action: str | None = None
    min_score: float | None = None
    invisible: bool = False
    proxy: dict[str, Any] | None = None


@dataclass
class SolveResult:
    """
    CAPTCHA solve result.

    Attributes:
        success: Whether the solve was successful.
        token: The solved CAPTCHA token.
        provider: Provider that solved the CAPTCHA.
        solve_time_ms: Time taken to solve in milliseconds.
        error: Error message if solve failed.
        task_id: Provider task ID for reporting.
    """

    success: bool
    token: str | None = None
    provider: str | None = None
    solve_time_ms: int = 0
    error: str | None = None
    task_id: str | None = None


@dataclass
class ProviderStats:
    """
    Provider performance statistics.

    Tracks success/failure rates and response times for
    intelligent provider selection and failover.

    Attributes:
        success: Number of successful solves.
        failure: Number of failed solves.
        total_time_ms: Total time spent solving.
        avg_time_ms: Average solve time.
        consecutive_failures: Count of consecutive failures.
        disabled_until: Timestamp when provider can be re-enabled.
        last_balance: Last known balance.
        last_balance_check: Timestamp of last balance check.
    """

    success: int = 0
    failure: int = 0
    total_time_ms: int = 0
    avg_time_ms: float = 0.0
    consecutive_failures: int = 0
    disabled_until: float | None = None
    last_balance: float | None = None
    last_balance_check: float | None = None


# =============================================================================
# Provider Protocol
# =============================================================================


class CaptchaProvider(Protocol):
    """Protocol for CAPTCHA solving providers."""

    async def solve_recaptcha_v2(
        self,
        site_key: str,
        page_url: str,
        proxy: dict[str, Any] | None = None,
        invisible: bool = False,
    ) -> tuple[str, str | None]:
        """Solve reCAPTCHA v2. Returns (token, task_id)."""
        ...

    async def solve_recaptcha_v3(
        self,
        site_key: str,
        page_url: str,
        action: str = "verify",
        min_score: float = 0.7,
        proxy: dict[str, Any] | None = None,
    ) -> tuple[str, str | None]:
        """Solve reCAPTCHA v3. Returns (token, task_id)."""
        ...

    async def solve_turnstile(
        self,
        site_key: str,
        page_url: str,
        proxy: dict[str, Any] | None = None,
    ) -> tuple[str, str | None]:
        """Solve Cloudflare Turnstile. Returns (token, task_id)."""
        ...

    async def solve_hcaptcha(
        self,
        site_key: str,
        page_url: str,
        proxy: dict[str, Any] | None = None,
    ) -> tuple[str, str | None]:
        """Solve hCaptcha. Returns (token, task_id)."""
        ...

    async def get_balance(self) -> float:
        """Get provider account balance."""
        ...

    async def report_bad(self, task_id: str) -> None:
        """Report incorrect solution for refund."""
        ...


# =============================================================================
# CapSolver Provider (Primary - AI-Powered)
# =============================================================================


class CapSolverProvider:
    """
    CapSolver - AI-based CAPTCHA solving.

    Primary provider with fast AI-powered solving.
    Typically 10-20 second solve times.

    Attributes:
        api_key: CapSolver API key.
        client: HTTP client for API requests.
    """

    BASE_URL = "https://api.capsolver.com"

    # Supported task types
    SUPPORTED_TYPES = [
        "ReCaptchaV2Task",
        "ReCaptchaV2TaskProxyless",
        "ReCaptchaV3Task",
        "ReCaptchaV3TaskProxyless",
        "HCaptchaTask",
        "HCaptchaTaskProxyless",
        "FunCaptchaTask",
        "TurnstileTask",
        "TurnstileTaskProxyless",
    ]

    def __init__(self, api_key: str) -> None:
        """Initialize CapSolver provider."""
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=120)

    async def solve_recaptcha_v2(
        self,
        site_key: str,
        page_url: str,
        proxy: dict[str, Any] | None = None,
        invisible: bool = False,
    ) -> tuple[str, str | None]:
        """Solve reCAPTCHA v2."""
        task_type = "ReCaptchaV2Task" if proxy else "ReCaptchaV2TaskProxyless"

        task: dict[str, Any] = {
            "type": task_type,
            "websiteURL": page_url,
            "websiteKey": site_key,
            "isInvisible": invisible,
        }

        if proxy:
            task.update(self._build_proxy_config(proxy))

        return await self._solve(task)

    async def solve_recaptcha_v3(
        self,
        site_key: str,
        page_url: str,
        action: str = "verify",
        min_score: float = 0.7,
        proxy: dict[str, Any] | None = None,
    ) -> tuple[str, str | None]:
        """Solve reCAPTCHA v3."""
        task_type = "ReCaptchaV3Task" if proxy else "ReCaptchaV3TaskProxyless"

        task: dict[str, Any] = {
            "type": task_type,
            "websiteURL": page_url,
            "websiteKey": site_key,
            "pageAction": action,
            "minScore": min_score,
        }

        if proxy:
            task.update(self._build_proxy_config(proxy))

        return await self._solve(task)

    async def solve_turnstile(
        self,
        site_key: str,
        page_url: str,
        proxy: dict[str, Any] | None = None,
    ) -> tuple[str, str | None]:
        """Solve Cloudflare Turnstile."""
        task_type = "TurnstileTask" if proxy else "TurnstileTaskProxyless"

        task: dict[str, Any] = {
            "type": task_type,
            "websiteURL": page_url,
            "websiteKey": site_key,
        }

        if proxy:
            task.update(self._build_proxy_config(proxy))

        return await self._solve(task)

    async def solve_hcaptcha(
        self,
        site_key: str,
        page_url: str,
        proxy: dict[str, Any] | None = None,
    ) -> tuple[str, str | None]:
        """Solve hCaptcha."""
        task_type = "HCaptchaTask" if proxy else "HCaptchaTaskProxyless"

        task: dict[str, Any] = {
            "type": task_type,
            "websiteURL": page_url,
            "websiteKey": site_key,
        }

        if proxy:
            task.update(self._build_proxy_config(proxy))

        return await self._solve(task)

    async def _solve(self, task: dict[str, Any]) -> tuple[str, str | None]:
        """Create task and wait for solution."""
        # Create task
        create_response = await self.client.post(
            f"{self.BASE_URL}/createTask",
            json={
                "clientKey": self.api_key,
                "task": task,
            },
        )

        create_data = create_response.json()

        if create_data.get("errorId"):
            raise CaptchaProviderError(
                f"CapSolver create error: {create_data.get('errorDescription')}",
                provider="capsolver",
                provider_error=create_data.get("errorDescription"),
            )

        task_id = create_data.get("taskId")
        if not task_id:
            raise CaptchaProviderError(
                "CapSolver did not return task ID",
                provider="capsolver",
            )

        # Poll for result (max 120 seconds, 2 second intervals)
        for _ in range(60):
            await asyncio.sleep(2)

            result_response = await self.client.post(
                f"{self.BASE_URL}/getTaskResult",
                json={
                    "clientKey": self.api_key,
                    "taskId": task_id,
                },
            )

            result_data = result_response.json()

            if result_data.get("errorId"):
                raise CaptchaProviderError(
                    f"CapSolver result error: {result_data.get('errorDescription')}",
                    provider="capsolver",
                    provider_error=result_data.get("errorDescription"),
                )

            if result_data.get("status") == "ready":
                solution = result_data.get("solution", {})
                token = solution.get("gRecaptchaResponse") or solution.get("token")
                if not token:
                    raise CaptchaProviderError(
                        "CapSolver returned empty token",
                        provider="capsolver",
                    )
                return token, task_id

        raise CaptchaSolveTimeoutError(
            "CapSolver timeout",
            provider="capsolver",
            timeout_seconds=120,
        )

    def _build_proxy_config(self, proxy: dict[str, Any]) -> dict[str, Any]:
        """Build proxy configuration for task."""
        return {
            "proxyType": proxy.get("protocol", "http"),
            "proxyAddress": proxy["host"],
            "proxyPort": proxy["port"],
            "proxyLogin": proxy.get("username"),
            "proxyPassword": proxy.get("password"),
        }

    async def get_balance(self) -> float:
        """Get account balance."""
        response = await self.client.post(
            f"{self.BASE_URL}/getBalance",
            json={"clientKey": self.api_key},
        )
        return response.json().get("balance", 0)

    async def report_bad(self, task_id: str) -> None:
        """Report bad solution (not supported by CapSolver)."""
        # CapSolver doesn't support bad reports
        pass

    async def close(self) -> None:
        """Close HTTP client."""
        await self.client.aclose()


# =============================================================================
# 2Captcha Provider (Secondary - Human-Based)
# =============================================================================


class TwoCaptchaProvider:
    """
    2Captcha - Human workforce CAPTCHA solving.

    Secondary provider with high reliability.
    Typically 30-60 second solve times.

    Attributes:
        api_key: 2Captcha API key.
        client: HTTP client for API requests.
    """

    BASE_URL = "https://2captcha.com"

    def __init__(self, api_key: str) -> None:
        """Initialize 2Captcha provider."""
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=180)

    async def solve_recaptcha_v2(
        self,
        site_key: str,
        page_url: str,
        proxy: dict[str, Any] | None = None,
        invisible: bool = False,
    ) -> tuple[str, str | None]:
        """Solve reCAPTCHA v2."""
        params: dict[str, Any] = {
            "key": self.api_key,
            "method": "userrecaptcha",
            "googlekey": site_key,
            "pageurl": page_url,
            "json": 1,
        }

        if invisible:
            params["invisible"] = 1

        if proxy:
            params.update(self._build_proxy_params(proxy))

        return await self._solve(params)

    async def solve_recaptcha_v3(
        self,
        site_key: str,
        page_url: str,
        action: str = "verify",
        min_score: float = 0.7,
        proxy: dict[str, Any] | None = None,
    ) -> tuple[str, str | None]:
        """Solve reCAPTCHA v3."""
        params: dict[str, Any] = {
            "key": self.api_key,
            "method": "userrecaptcha",
            "googlekey": site_key,
            "pageurl": page_url,
            "version": "v3",
            "action": action,
            "min_score": min_score,
            "json": 1,
        }

        if proxy:
            params.update(self._build_proxy_params(proxy))

        return await self._solve(params)

    async def solve_turnstile(
        self,
        site_key: str,
        page_url: str,
        proxy: dict[str, Any] | None = None,
    ) -> tuple[str, str | None]:
        """Solve Cloudflare Turnstile."""
        params: dict[str, Any] = {
            "key": self.api_key,
            "method": "turnstile",
            "sitekey": site_key,
            "pageurl": page_url,
            "json": 1,
        }

        if proxy:
            params.update(self._build_proxy_params(proxy))

        return await self._solve(params)

    async def solve_hcaptcha(
        self,
        site_key: str,
        page_url: str,
        proxy: dict[str, Any] | None = None,
    ) -> tuple[str, str | None]:
        """Solve hCaptcha."""
        params: dict[str, Any] = {
            "key": self.api_key,
            "method": "hcaptcha",
            "sitekey": site_key,
            "pageurl": page_url,
            "json": 1,
        }

        if proxy:
            params.update(self._build_proxy_params(proxy))

        return await self._solve(params)

    async def _solve(self, params: dict[str, Any]) -> tuple[str, str | None]:
        """Submit task and wait for solution."""
        # Submit task
        submit_response = await self.client.get(
            f"{self.BASE_URL}/in.php",
            params=params,
        )

        submit_data = submit_response.json()

        if submit_data.get("status") != 1:
            raise CaptchaProviderError(
                f"2Captcha submit error: {submit_data.get('request')}",
                provider="2captcha",
                provider_error=str(submit_data.get("request")),
            )

        task_id = submit_data["request"]

        # Poll for result (max 180 seconds, 5 second intervals)
        for _ in range(36):
            await asyncio.sleep(5)

            result_response = await self.client.get(
                f"{self.BASE_URL}/res.php",
                params={
                    "key": self.api_key,
                    "action": "get",
                    "id": task_id,
                    "json": 1,
                },
            )

            result_data = result_response.json()

            if result_data.get("status") == 1:
                token = result_data.get("request")
                if not token:
                    raise CaptchaProviderError(
                        "2Captcha returned empty token",
                        provider="2captcha",
                    )
                return token, task_id

            if result_data.get("request") not in ["CAPCHA_NOT_READY"]:
                raise CaptchaProviderError(
                    f"2Captcha error: {result_data.get('request')}",
                    provider="2captcha",
                    provider_error=str(result_data.get("request")),
                )

        raise CaptchaSolveTimeoutError(
            "2Captcha timeout",
            provider="2captcha",
            timeout_seconds=180,
        )

    def _build_proxy_params(self, proxy: dict[str, Any]) -> dict[str, Any]:
        """Build proxy parameters."""
        username = proxy.get("username", "")
        password = proxy.get("password", "")
        host = proxy["host"]
        port = proxy["port"]

        proxy_str = f"{host}:{port}"
        if username and password:
            proxy_str = f"{username}:{password}@{host}:{port}"

        return {
            "proxy": proxy_str,
            "proxytype": proxy.get("protocol", "HTTP").upper(),
        }

    async def get_balance(self) -> float:
        """Get account balance."""
        response = await self.client.get(
            f"{self.BASE_URL}/res.php",
            params={
                "key": self.api_key,
                "action": "getbalance",
                "json": 1,
            },
        )
        return float(response.json().get("request", 0))

    async def report_bad(self, task_id: str) -> None:
        """Report bad solution for refund."""
        await self.client.get(
            f"{self.BASE_URL}/res.php",
            params={
                "key": self.api_key,
                "action": "reportbad",
                "id": task_id,
            },
        )

    async def close(self) -> None:
        """Close HTTP client."""
        await self.client.aclose()


# =============================================================================
# Anti-Captcha Provider (Tertiary)
# =============================================================================


class AntiCaptchaProvider:
    """
    Anti-Captcha backup provider.

    Tertiary provider for additional redundancy.
    Typically 20-40 second solve times.

    Attributes:
        api_key: Anti-Captcha API key.
        client: HTTP client for API requests.
    """

    BASE_URL = "https://api.anti-captcha.com"

    def __init__(self, api_key: str) -> None:
        """Initialize Anti-Captcha provider."""
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=180)

    async def solve_recaptcha_v2(
        self,
        site_key: str,
        page_url: str,
        proxy: dict[str, Any] | None = None,
        invisible: bool = False,
    ) -> tuple[str, str | None]:
        """Solve reCAPTCHA v2."""
        task_type = "RecaptchaV2Task" if proxy else "RecaptchaV2TaskProxyless"

        task: dict[str, Any] = {
            "type": task_type,
            "websiteURL": page_url,
            "websiteKey": site_key,
            "isInvisible": invisible,
        }

        if proxy:
            task.update(self._build_proxy_config(proxy))

        return await self._solve(task)

    async def solve_recaptcha_v3(
        self,
        site_key: str,
        page_url: str,
        action: str = "verify",
        min_score: float = 0.7,
        proxy: dict[str, Any] | None = None,
    ) -> tuple[str, str | None]:
        """Solve reCAPTCHA v3."""
        task_type = "RecaptchaV3Task" if proxy else "RecaptchaV3TaskProxyless"

        task: dict[str, Any] = {
            "type": task_type,
            "websiteURL": page_url,
            "websiteKey": site_key,
            "pageAction": action,
            "minScore": min_score,
        }

        if proxy:
            task.update(self._build_proxy_config(proxy))

        return await self._solve(task)

    async def solve_turnstile(
        self,
        site_key: str,
        page_url: str,
        proxy: dict[str, Any] | None = None,
    ) -> tuple[str, str | None]:
        """Solve Cloudflare Turnstile."""
        task_type = "TurnstileTask" if proxy else "TurnstileTaskProxyless"

        task: dict[str, Any] = {
            "type": task_type,
            "websiteURL": page_url,
            "websiteKey": site_key,
        }

        if proxy:
            task.update(self._build_proxy_config(proxy))

        return await self._solve(task)

    async def solve_hcaptcha(
        self,
        site_key: str,
        page_url: str,
        proxy: dict[str, Any] | None = None,
    ) -> tuple[str, str | None]:
        """Solve hCaptcha."""
        task_type = "HCaptchaTask" if proxy else "HCaptchaTaskProxyless"

        task: dict[str, Any] = {
            "type": task_type,
            "websiteURL": page_url,
            "websiteKey": site_key,
        }

        if proxy:
            task.update(self._build_proxy_config(proxy))

        return await self._solve(task)

    async def _solve(self, task: dict[str, Any]) -> tuple[str, str | None]:
        """Create task and wait for solution."""
        # Create task
        create_response = await self.client.post(
            f"{self.BASE_URL}/createTask",
            json={
                "clientKey": self.api_key,
                "task": task,
            },
        )

        create_data = create_response.json()

        if create_data.get("errorId"):
            raise CaptchaProviderError(
                f"Anti-Captcha error: {create_data.get('errorDescription')}",
                provider="anticaptcha",
                provider_error=create_data.get("errorDescription"),
            )

        task_id = create_data.get("taskId")
        if not task_id:
            raise CaptchaProviderError(
                "Anti-Captcha did not return task ID",
                provider="anticaptcha",
            )

        # Poll for result (max 180 seconds, 5 second intervals)
        for _ in range(36):
            await asyncio.sleep(5)

            result_response = await self.client.post(
                f"{self.BASE_URL}/getTaskResult",
                json={
                    "clientKey": self.api_key,
                    "taskId": task_id,
                },
            )

            result_data = result_response.json()

            if result_data.get("status") == "ready":
                solution = result_data.get("solution", {})
                token = solution.get("gRecaptchaResponse") or solution.get("token")
                if not token:
                    raise CaptchaProviderError(
                        "Anti-Captcha returned empty token",
                        provider="anticaptcha",
                    )
                return token, str(task_id)

            if result_data.get("errorId"):
                raise CaptchaProviderError(
                    f"Anti-Captcha error: {result_data.get('errorDescription')}",
                    provider="anticaptcha",
                    provider_error=result_data.get("errorDescription"),
                )

        raise CaptchaSolveTimeoutError(
            "Anti-Captcha timeout",
            provider="anticaptcha",
            timeout_seconds=180,
        )

    def _build_proxy_config(self, proxy: dict[str, Any]) -> dict[str, Any]:
        """Build proxy configuration for task."""
        return {
            "proxyType": proxy.get("protocol", "http"),
            "proxyAddress": proxy["host"],
            "proxyPort": proxy["port"],
            "proxyLogin": proxy.get("username"),
            "proxyPassword": proxy.get("password"),
        }

    async def get_balance(self) -> float:
        """Get account balance."""
        response = await self.client.post(
            f"{self.BASE_URL}/getBalance",
            json={"clientKey": self.api_key},
        )
        return response.json().get("balance", 0)

    async def report_bad(self, task_id: str) -> None:
        """Report bad solution."""
        await self.client.post(
            f"{self.BASE_URL}/reportIncorrectRecaptcha",
            json={
                "clientKey": self.api_key,
                "taskId": int(task_id),
            },
        )

    async def close(self) -> None:
        """Close HTTP client."""
        await self.client.aclose()


# =============================================================================
# CAPTCHA Detector
# =============================================================================


class CaptchaDetector:
    """
    Detect CAPTCHA presence and type on a page.

    Uses regex patterns to identify CAPTCHA site keys
    and determine the CAPTCHA type.
    """

    # Site key detection patterns
    RECAPTCHA_PATTERNS = [
        r'data-sitekey="([^"]+)"',
        r"grecaptcha\.render\([^,]+,\s*\{[^}]*sitekey:\s*['\"]([^'\"]+)['\"]",
        r'src="https://www\.google\.com/recaptcha/[^"]*\?.*?k=([^"&]+)',
        r"sitekey\s*[:=]\s*['\"]([^'\"]+)['\"]",
    ]

    HCAPTCHA_PATTERNS = [
        r'data-sitekey="([^"]+)"[^>]*class="[^"]*h-captcha',
        r'class="[^"]*h-captcha[^"]*"[^>]*data-sitekey="([^"]+)"',
        r'hcaptcha\.render\([^,]+,\s*\{[^}]*sitekey:\s*["\']([^"\']+)["\']',
    ]

    TURNSTILE_PATTERNS = [
        r'data-sitekey="([^"]+)"[^>]*class="[^"]*cf-turnstile',
        r'class="[^"]*cf-turnstile[^"]*"[^>]*data-sitekey="([^"]+)"',
        r'turnstile\.render\([^,]+,\s*\{[^}]*sitekey:\s*["\']([^"\']+)["\']',
    ]

    CHALLENGE_INDICATORS = [
        "challenge-running",
        "cf-browser-verification",
        "cf-turnstile",
        "g-recaptcha",
        "h-captcha",
        "Please verify you are a human",
        "complete the security check",
        "Verify you are human",
        "checking your browser",
    ]

    async def detect(self, page: Any) -> CaptchaChallenge | None:
        """
        Detect CAPTCHA on a page.

        Args:
            page: Playwright page object.

        Returns:
            CaptchaChallenge if CAPTCHA found, None otherwise.
        """
        content = await page.content()
        url = page.url

        # Check Turnstile first (Cloudflare)
        for pattern in self.TURNSTILE_PATTERNS:
            match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
            if match:
                logger.debug("captcha_detected", type="turnstile", url=url)
                return CaptchaChallenge(
                    captcha_type=CaptchaType.TURNSTILE,
                    site_key=match.group(1),
                    page_url=url,
                )

        # Check hCaptcha
        if "h-captcha" in content.lower() or "hcaptcha" in content.lower():
            for pattern in self.HCAPTCHA_PATTERNS:
                match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
                if match:
                    logger.debug("captcha_detected", type="hcaptcha", url=url)
                    return CaptchaChallenge(
                        captcha_type=CaptchaType.HCAPTCHA,
                        site_key=match.group(1),
                        page_url=url,
                    )

        # Check reCAPTCHA
        for pattern in self.RECAPTCHA_PATTERNS:
            match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
            if match:
                # Determine v2 vs v3
                captcha_type = CaptchaType.RECAPTCHA_V2
                if (
                    "recaptcha/api.js?render=" in content
                    or "grecaptcha.execute" in content
                ):
                    captcha_type = CaptchaType.RECAPTCHA_V3

                invisible = (
                    "invisible" in content.lower() or "size: 'invisible'" in content
                )

                logger.debug(
                    "captcha_detected",
                    type=captcha_type.value,
                    invisible=invisible,
                    url=url,
                )

                return CaptchaChallenge(
                    captcha_type=captcha_type,
                    site_key=match.group(1),
                    page_url=url,
                    invisible=invisible,
                )

        return None

    async def is_challenge_page(self, page: Any) -> bool:
        """
        Check if page is a challenge/verification page.

        Args:
            page: Playwright page object.

        Returns:
            True if page appears to be a CAPTCHA challenge.
        """
        content = await page.content()
        content_lower = content.lower()

        return any(
            indicator.lower() in content_lower for indicator in self.CHALLENGE_INDICATORS
        )


# =============================================================================
# Token Injector
# =============================================================================


class CaptchaTokenInjector:
    """
    Inject solved CAPTCHA tokens into pages.

    Handles different CAPTCHA types and their specific
    injection requirements.
    """

    async def inject_recaptcha_token(self, page: Any, token: str) -> None:
        """
        Inject reCAPTCHA token into page.

        Args:
            page: Playwright page object.
            token: Solved CAPTCHA token.
        """
        # Escape token for JavaScript
        escaped_token = token.replace("'", "\\'").replace('"', '\\"')

        await page.evaluate(
            f"""
            () => {{
                // Find the response textarea
                const responseField = document.querySelector('[name="g-recaptcha-response"]')
                    || document.getElementById('g-recaptcha-response');

                if (responseField) {{
                    responseField.value = '{escaped_token}';
                    responseField.style.display = 'none';
                }}

                // Also set in potential hidden inputs
                document.querySelectorAll('textarea[name="g-recaptcha-response"]').forEach(el => {{
                    el.value = '{escaped_token}';
                }});

                // Trigger callback if exists
                if (typeof window.grecaptchaCallback === 'function') {{
                    window.grecaptchaCallback('{escaped_token}');
                }}

                // Check for explicit callback name
                const callbackMatch = document.body.innerHTML.match(/data-callback="([^"]+)"/);
                if (callbackMatch && typeof window[callbackMatch[1]] === 'function') {{
                    window[callbackMatch[1]]('{escaped_token}');
                }}

                // Try grecaptcha object
                if (typeof grecaptcha !== 'undefined' && grecaptcha.getResponse) {{
                    // Can't set directly, but callback should work
                }}
            }}
        """
        )

        logger.debug("token_injected", captcha_type="recaptcha")

    async def inject_turnstile_token(self, page: Any, token: str) -> None:
        """
        Inject Cloudflare Turnstile token into page.

        Args:
            page: Playwright page object.
            token: Solved CAPTCHA token.
        """
        escaped_token = token.replace("'", "\\'").replace('"', '\\"')

        await page.evaluate(
            f"""
            () => {{
                // Find turnstile response field
                const responseField = document.querySelector('[name="cf-turnstile-response"]')
                    || document.querySelector('input[name*="turnstile"]');

                if (responseField) {{
                    responseField.value = '{escaped_token}';
                }}

                // Trigger turnstile callback
                if (window.turnstile && typeof window.turnstile.callback === 'function') {{
                    window.turnstile.callback('{escaped_token}');
                }}

                // Try widget callbacks
                const widgets = document.querySelectorAll('.cf-turnstile');
                widgets.forEach(widget => {{
                    const callback = widget.getAttribute('data-callback');
                    if (callback && typeof window[callback] === 'function') {{
                        window[callback]('{escaped_token}');
                    }}
                }});
            }}
        """
        )

        logger.debug("token_injected", captcha_type="turnstile")

    async def inject_hcaptcha_token(self, page: Any, token: str) -> None:
        """
        Inject hCaptcha token into page.

        Args:
            page: Playwright page object.
            token: Solved CAPTCHA token.
        """
        escaped_token = token.replace("'", "\\'").replace('"', '\\"')

        await page.evaluate(
            f"""
            () => {{
                const responseField = document.querySelector('[name="h-captcha-response"]')
                    || document.querySelector('[name="g-recaptcha-response"]');

                if (responseField) {{
                    responseField.value = '{escaped_token}';
                }}

                // Trigger callback
                const widgets = document.querySelectorAll('.h-captcha');
                widgets.forEach(widget => {{
                    const callback = widget.getAttribute('data-callback');
                    if (callback && typeof window[callback] === 'function') {{
                        window[callback]('{escaped_token}');
                    }}
                }});
            }}
        """
        )

        logger.debug("token_injected", captcha_type="hcaptcha")

    async def inject_token(
        self,
        page: Any,
        token: str,
        captcha_type: CaptchaType,
    ) -> None:
        """
        Inject token based on CAPTCHA type.

        Args:
            page: Playwright page object.
            token: Solved CAPTCHA token.
            captcha_type: Type of CAPTCHA.
        """
        if captcha_type == CaptchaType.TURNSTILE:
            await self.inject_turnstile_token(page, token)
        elif captcha_type == CaptchaType.HCAPTCHA:
            await self.inject_hcaptcha_token(page, token)
        else:
            await self.inject_recaptcha_token(page, token)


# =============================================================================
# Cost Tracker
# =============================================================================


class CaptchaCostTracker:
    """
    Track CAPTCHA solving costs.

    Maintains usage statistics and calculates estimated costs
    based on provider pricing.
    """

    # Pricing per 1000 solves (USD)
    PRICING: dict[str, dict[str, float]] = {
        "capsolver": {
            "recaptcha_v2": 1.0,
            "recaptcha_v3": 1.2,
            "turnstile": 1.0,
            "hcaptcha": 0.8,
        },
        "2captcha": {
            "recaptcha_v2": 2.99,
            "recaptcha_v3": 2.99,
            "turnstile": 2.99,
            "hcaptcha": 2.99,
        },
        "anticaptcha": {
            "recaptcha_v2": 2.0,
            "recaptcha_v3": 2.5,
            "turnstile": 2.0,
            "hcaptcha": 2.0,
        },
    }

    def __init__(self) -> None:
        """Initialize cost tracker."""
        self.usage: dict[str, dict[str, int]] = {}
        self._lock = asyncio.Lock()

    async def track(self, provider: str, captcha_type: str) -> None:
        """
        Track CAPTCHA solve usage.

        Args:
            provider: Provider name.
            captcha_type: Type of CAPTCHA solved.
        """
        async with self._lock:
            if provider not in self.usage:
                self.usage[provider] = {}

            if captcha_type not in self.usage[provider]:
                self.usage[provider][captcha_type] = 0

            self.usage[provider][captcha_type] += 1

    def get_estimated_cost(self) -> float:
        """
        Calculate estimated total cost.

        Returns:
            Estimated cost in USD.
        """
        total = 0.0

        for provider, types in self.usage.items():
            for captcha_type, count in types.items():
                price_per_1000 = self.PRICING.get(provider, {}).get(captcha_type, 3.0)
                total += (count / 1000) * price_per_1000

        return total

    def get_report(self) -> dict[str, Any]:
        """
        Get detailed usage report.

        Returns:
            Dictionary with usage statistics and costs.
        """
        return {
            "usage": dict(self.usage),
            "estimated_cost_usd": round(self.get_estimated_cost(), 4),
            "by_provider": {
                provider: sum(types.values())
                for provider, types in self.usage.items()
            },
            "total_solves": sum(
                sum(types.values()) for types in self.usage.values()
            ),
        }

    def reset(self) -> None:
        """Reset usage statistics."""
        self.usage = {}


# =============================================================================
# CAPTCHA Solver Chain
# =============================================================================


class CaptchaSolverChain:
    """
    Multi-provider CAPTCHA solver with failover.

    Manages multiple CAPTCHA solving providers with automatic
    failover, health tracking, and cost optimization.

    Attributes:
        providers: List of (name, provider) tuples in priority order.
        provider_stats: Performance statistics for each provider.
        cost_tracker: Cost tracking instance.
    """

    # Failover configuration
    CONSECUTIVE_FAILURE_THRESHOLD = 5
    DISABLE_DURATION_SECONDS = 300  # 5 minutes
    LOW_BALANCE_THRESHOLD = 5.0  # USD

    def __init__(self) -> None:
        """Initialize CAPTCHA solver chain."""
        self.providers: list[tuple[str, Any]] = []
        self.provider_stats: dict[str, ProviderStats] = {}
        self.cost_tracker = CaptchaCostTracker()
        self._lock = asyncio.Lock()

    async def initialize(self, config: dict[str, str]) -> None:
        """
        Initialize with provider configurations.

        Args:
            config: Dictionary with provider API keys.
                   Keys: capsolver_api_key, 2captcha_api_key, anticaptcha_api_key
        """
        # Initialize providers in priority order
        if config.get("capsolver_api_key"):
            provider = CapSolverProvider(config["capsolver_api_key"])
            self.providers.append(("capsolver", provider))
            self.provider_stats["capsolver"] = ProviderStats()

        if config.get("2captcha_api_key"):
            provider = TwoCaptchaProvider(config["2captcha_api_key"])
            self.providers.append(("2captcha", provider))
            self.provider_stats["2captcha"] = ProviderStats()

        if config.get("anticaptcha_api_key"):
            provider = AntiCaptchaProvider(config["anticaptcha_api_key"])
            self.providers.append(("anticaptcha", provider))
            self.provider_stats["anticaptcha"] = ProviderStats()

        logger.info(
            "captcha_solver_chain_initialized",
            providers=[name for name, _ in self.providers],
        )

    async def solve(
        self,
        challenge: CaptchaChallenge,
        timeout: int = 120,
    ) -> SolveResult:
        """
        Solve CAPTCHA with provider failover.

        Args:
            challenge: CAPTCHA challenge to solve.
            timeout: Maximum total timeout in seconds.

        Returns:
            SolveResult with token or error.
        """
        start_time = time.time()
        errors: list[str] = []

        for provider_name, provider in self._get_active_providers():
            try:
                # Calculate remaining time
                remaining_time = timeout - (time.time() - start_time)
                if remaining_time <= 0:
                    break

                provider_timeout = min(remaining_time, 60)  # Max 60s per provider

                token, task_id = await asyncio.wait_for(
                    self._solve_with_provider(provider, challenge),
                    timeout=provider_timeout,
                )

                # Success
                solve_time = int((time.time() - start_time) * 1000)
                self._record_success(provider_name, solve_time)

                # Track cost
                await self.cost_tracker.track(
                    provider_name, challenge.captcha_type.value
                )

                logger.info(
                    "captcha_solved",
                    provider=provider_name,
                    captcha_type=challenge.captcha_type.value,
                    solve_time_ms=solve_time,
                )

                return SolveResult(
                    success=True,
                    token=token,
                    provider=provider_name,
                    solve_time_ms=solve_time,
                    task_id=task_id,
                )

            except asyncio.TimeoutError:
                errors.append(f"{provider_name}: timeout")
                self._record_failure(provider_name)
                logger.warning(
                    "captcha_provider_timeout",
                    provider=provider_name,
                )

            except CaptchaProviderError as e:
                errors.append(f"{provider_name}: {e.message}")
                self._record_failure(provider_name)
                logger.warning(
                    "captcha_provider_error",
                    provider=provider_name,
                    error=str(e),
                )

            except Exception as e:
                errors.append(f"{provider_name}: unexpected - {str(e)}")
                self._record_failure(provider_name)
                logger.error(
                    "captcha_unexpected_error",
                    provider=provider_name,
                    error=str(e),
                    exc_info=True,
                )

        # All providers failed
        solve_time = int((time.time() - start_time) * 1000)
        error_msg = f"All providers failed: {'; '.join(errors)}"

        logger.error(
            "captcha_solve_failed",
            errors=errors,
            solve_time_ms=solve_time,
        )

        return SolveResult(
            success=False,
            error=error_msg,
            solve_time_ms=solve_time,
        )

    async def _solve_with_provider(
        self,
        provider: Any,
        challenge: CaptchaChallenge,
    ) -> tuple[str, str | None]:
        """Solve using specific provider."""
        if challenge.captcha_type == CaptchaType.RECAPTCHA_V2:
            return await provider.solve_recaptcha_v2(
                site_key=challenge.site_key,
                page_url=challenge.page_url,
                proxy=challenge.proxy,
                invisible=challenge.invisible,
            )

        elif challenge.captcha_type == CaptchaType.RECAPTCHA_V3:
            return await provider.solve_recaptcha_v3(
                site_key=challenge.site_key,
                page_url=challenge.page_url,
                action=challenge.action or "verify",
                min_score=challenge.min_score or 0.7,
                proxy=challenge.proxy,
            )

        elif challenge.captcha_type == CaptchaType.TURNSTILE:
            return await provider.solve_turnstile(
                site_key=challenge.site_key,
                page_url=challenge.page_url,
                proxy=challenge.proxy,
            )

        elif challenge.captcha_type == CaptchaType.HCAPTCHA:
            return await provider.solve_hcaptcha(
                site_key=challenge.site_key,
                page_url=challenge.page_url,
                proxy=challenge.proxy,
            )

        raise CaptchaError(f"Unsupported CAPTCHA type: {challenge.captcha_type}")

    def _get_active_providers(self) -> list[tuple[str, Any]]:
        """Get active providers sorted by health score."""
        now = time.time()
        active: list[tuple[str, Any, ProviderStats]] = []

        for name, provider in self.providers:
            stats = self.provider_stats[name]

            # Check if disabled
            if stats.disabled_until and stats.disabled_until > now:
                continue

            active.append((name, provider, stats))

        # Sort by success rate and average time
        def score(item: tuple[str, Any, ProviderStats]) -> float:
            _, _, stats = item
            total = stats.success + stats.failure

            if total == 0:
                return 0.5  # Neutral score for unknown

            success_rate = stats.success / total
            time_penalty = min(stats.avg_time_ms / 60000, 0.3)  # Max 30% penalty

            return success_rate - time_penalty

        active.sort(key=score, reverse=True)

        return [(name, provider) for name, provider, _ in active]

    def _record_success(self, provider_name: str, solve_time_ms: int) -> None:
        """Record successful solve."""
        stats = self.provider_stats[provider_name]

        stats.success += 1
        stats.consecutive_failures = 0
        stats.total_time_ms += solve_time_ms

        total = stats.success + stats.failure
        stats.avg_time_ms = stats.total_time_ms / total

        # Clear disable
        stats.disabled_until = None

    def _record_failure(self, provider_name: str) -> None:
        """Record failed solve."""
        stats = self.provider_stats[provider_name]

        stats.failure += 1
        stats.consecutive_failures += 1

        # Disable after consecutive failures
        if stats.consecutive_failures >= self.CONSECUTIVE_FAILURE_THRESHOLD:
            stats.disabled_until = time.time() + self.DISABLE_DURATION_SECONDS
            logger.warning(
                "captcha_provider_disabled",
                provider=provider_name,
                consecutive_failures=stats.consecutive_failures,
                disabled_for_seconds=self.DISABLE_DURATION_SECONDS,
            )

    async def report_bad_solution(self, provider_name: str, task_id: str) -> None:
        """Report incorrect solution to provider."""
        for name, provider in self.providers:
            if name == provider_name:
                try:
                    await provider.report_bad(task_id)
                    logger.info(
                        "bad_solution_reported",
                        provider=provider_name,
                        task_id=task_id,
                    )
                except Exception as e:
                    logger.error(
                        "bad_solution_report_failed",
                        provider=provider_name,
                        error=str(e),
                    )
                break

    async def get_balances(self) -> dict[str, float]:
        """Get balances for all providers."""
        balances: dict[str, float] = {}

        for name, provider in self.providers:
            try:
                balance = await provider.get_balance()
                balances[name] = balance

                # Update stats
                self.provider_stats[name].last_balance = balance
                self.provider_stats[name].last_balance_check = time.time()

                # Log low balance warning
                if balance < self.LOW_BALANCE_THRESHOLD:
                    logger.warning(
                        "captcha_provider_low_balance",
                        provider=name,
                        balance=balance,
                        threshold=self.LOW_BALANCE_THRESHOLD,
                    )

            except Exception as e:
                balances[name] = -1  # Error indicator
                logger.error(
                    "balance_check_failed",
                    provider=name,
                    error=str(e),
                )

        return balances

    def get_stats(self) -> dict[str, Any]:
        """Get provider statistics."""
        return {
            name: {
                "success": stats.success,
                "failure": stats.failure,
                "success_rate": (
                    stats.success / (stats.success + stats.failure)
                    if (stats.success + stats.failure) > 0
                    else 0
                ),
                "avg_time_ms": round(stats.avg_time_ms, 2),
                "consecutive_failures": stats.consecutive_failures,
                "disabled": (
                    stats.disabled_until is not None
                    and stats.disabled_until > time.time()
                ),
                "last_balance": stats.last_balance,
            }
            for name, stats in self.provider_stats.items()
        }

    async def close(self) -> None:
        """Close all provider connections."""
        for name, provider in self.providers:
            try:
                await provider.close()
            except Exception as e:
                logger.error(
                    "provider_close_error",
                    provider=name,
                    error=str(e),
                )


# =============================================================================
# VFS CAPTCHA Handler
# =============================================================================


class VFSCaptchaHandler:
    """
    VFS Global specific CAPTCHA handler.

    Handles the specific CAPTCHA patterns and flows used by
    VFS Global booking systems.

    Attributes:
        solver: CAPTCHA solver chain.
        detector: CAPTCHA detector.
        injector: Token injector.
    """

    # VFS-specific configuration
    MAX_CAPTCHA_ATTEMPTS = 3
    SOLVE_TIMEOUT = 90

    def __init__(
        self,
        solver_chain: CaptchaSolverChain,
        detector: CaptchaDetector | None = None,
    ) -> None:
        """Initialize VFS CAPTCHA handler."""
        self.solver = solver_chain
        self.detector = detector or CaptchaDetector()
        self.injector = CaptchaTokenInjector()

    async def handle_captcha(
        self,
        page: Any,
        proxy: dict[str, Any] | None = None,
        max_attempts: int | None = None,
    ) -> bool:
        """
        Handle CAPTCHA on VFS page.

        Args:
            page: Playwright page object.
            proxy: Optional proxy configuration.
            max_attempts: Maximum solve attempts.

        Returns:
            True if CAPTCHA was handled successfully.
        """
        attempts = max_attempts or self.MAX_CAPTCHA_ATTEMPTS

        for attempt in range(attempts):
            # Detect CAPTCHA
            challenge = await self.detector.detect(page)

            if not challenge:
                # No CAPTCHA found
                logger.debug("no_captcha_detected")
                return True

            # Add proxy to challenge
            challenge.proxy = proxy

            # Solve
            result = await self.solver.solve(challenge, timeout=self.SOLVE_TIMEOUT)

            if not result.success:
                logger.warning(
                    "captcha_solve_failed",
                    attempt=attempt + 1,
                    max_attempts=attempts,
                    error=result.error,
                )
                if attempt < attempts - 1:
                    await asyncio.sleep(2)
                    continue
                return False

            # Inject token
            await self.injector.inject_token(
                page, result.token, challenge.captcha_type  # type: ignore
            )

            # Wait for challenge to clear
            await asyncio.sleep(2)

            # Verify challenge cleared
            if not await self.detector.is_challenge_page(page):
                logger.info(
                    "captcha_handled",
                    captcha_type=challenge.captcha_type.value,
                    attempt=attempt + 1,
                )
                return True

            # Try submitting form after injection
            try:
                submit_button = await page.query_selector(
                    'button[type="submit"], input[type="submit"]'
                )
                if submit_button:
                    await submit_button.click()
                    await asyncio.sleep(3)
            except Exception:
                pass

            # Check again
            if not await self.detector.is_challenge_page(page):
                return True

            # Solution might be invalid, report and retry
            if result.task_id and result.provider:
                await self.solver.report_bad_solution(result.provider, result.task_id)

        return False


# =============================================================================
# Main CaptchaSolver Interface
# =============================================================================


class CaptchaSolver:
    """
    Main CAPTCHA solver interface.

    Provides a unified interface for CAPTCHA detection, solving,
    and injection with multi-provider failover.

    Usage:
        solver = CaptchaSolver()
        await solver.initialize({"capsolver_api_key": "..."})

        challenge = await solver.detect(page)
        if challenge:
            result = await solver.solve(challenge)
            if result.success:
                await solver.inject_token(page, result.token, challenge.captcha_type)
    """

    def __init__(self) -> None:
        """Initialize CAPTCHA solver."""
        self.chain = CaptchaSolverChain()
        self.detector = CaptchaDetector()
        self.injector = CaptchaTokenInjector()
        self._initialized = False

    async def initialize(self, config: dict[str, str]) -> None:
        """
        Initialize with provider configuration.

        Args:
            config: Dictionary with provider API keys.
        """
        await self.chain.initialize(config)
        self._initialized = True

    async def detect(self, page: Any) -> CaptchaChallenge | None:
        """
        Detect CAPTCHA on page.

        Args:
            page: Playwright page object.

        Returns:
            CaptchaChallenge if found, None otherwise.
        """
        return await self.detector.detect(page)

    async def is_challenge_page(self, page: Any) -> bool:
        """
        Check if page is a challenge page.

        Args:
            page: Playwright page object.

        Returns:
            True if page appears to be a CAPTCHA challenge.
        """
        return await self.detector.is_challenge_page(page)

    async def solve(
        self,
        challenge: CaptchaChallenge,
        timeout: int = 120,
    ) -> SolveResult:
        """
        Solve CAPTCHA challenge.

        Args:
            challenge: CAPTCHA challenge to solve.
            timeout: Maximum timeout in seconds.

        Returns:
            SolveResult with token or error.
        """
        if not self._initialized:
            raise CaptchaError("CaptchaSolver not initialized")

        return await self.chain.solve(challenge, timeout)

    async def inject_token(
        self,
        page: Any,
        token: str,
        captcha_type: CaptchaType,
    ) -> None:
        """
        Inject solved token into page.

        Args:
            page: Playwright page object.
            token: Solved CAPTCHA token.
            captcha_type: Type of CAPTCHA.
        """
        await self.injector.inject_token(page, token, captcha_type)

    async def handle_page_captcha(
        self,
        page: Any,
        proxy: dict[str, Any] | None = None,
        max_attempts: int = 3,
    ) -> bool:
        """
        Detect, solve, and inject CAPTCHA on a page.

        Convenience method that handles the full CAPTCHA flow.

        Args:
            page: Playwright page object.
            proxy: Optional proxy configuration.
            max_attempts: Maximum attempts.

        Returns:
            True if CAPTCHA was handled or not present.
        """
        handler = VFSCaptchaHandler(self.chain, self.detector)
        return await handler.handle_captcha(page, proxy, max_attempts)

    async def get_balances(self) -> dict[str, float]:
        """Get provider balances."""
        return await self.chain.get_balances()

    def get_stats(self) -> dict[str, Any]:
        """Get solver statistics."""
        return {
            "providers": self.chain.get_stats(),
            "costs": self.chain.cost_tracker.get_report(),
        }

    async def report_bad_solution(self, provider: str, task_id: str) -> None:
        """Report incorrect solution."""
        await self.chain.report_bad_solution(provider, task_id)

    async def close(self) -> None:
        """Close all connections."""
        await self.chain.close()


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Main interface
    "CaptchaSolver",
    # Enums
    "CaptchaType",
    "ProviderStatus",
    # Data classes
    "CaptchaChallenge",
    "SolveResult",
    "ProviderStats",
    # Providers
    "CapSolverProvider",
    "TwoCaptchaProvider",
    "AntiCaptchaProvider",
    # Solver chain
    "CaptchaSolverChain",
    # Detection and injection
    "CaptchaDetector",
    "CaptchaTokenInjector",
    # Cost tracking
    "CaptchaCostTracker",
    # Site handlers
    "VFSCaptchaHandler",
]
