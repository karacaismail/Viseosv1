"""
VISE OS Proxy Manager.

Multi-provider proxy pool management with rotation, health tracking,
and intelligent failover for 200+ concurrent sessions.

Features:
- Multi-provider support: Bright Data (primary), Oxylabs, Smartproxy
- Health scoring with automatic cooldown/retirement
- ASN diversity tracking for anti-pattern detection
- Sticky session support for authenticated flows
- Site-specific proxy configuration
- Bandwidth and cost optimization
- Real-time health monitoring with alerts

Usage:
    from src.bot.services.proxy import ProxyManager

    manager = ProxyManager()
    await manager.initialize(configs)

    # Get proxy for booking session
    proxy = await manager.get_proxy(
        target_site="vfs",
        country="tr",
        sticky_session=True,
    )

    # Report success/failure
    await manager.report_success(proxy["proxy_id"], response_time_ms=250)
    await manager.report_failure(proxy["proxy_id"], "timeout", is_blocked=False)
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from src.core.exceptions import ProxyPoolExhaustedError

logger = structlog.get_logger(__name__)


# =============================================================================
# Enums
# =============================================================================


class ProxyStatus(str, Enum):
    """Proxy health status."""

    ACTIVE = "active"
    SLOW = "slow"
    BLOCKED = "blocked"
    COOLDOWN = "cooldown"
    RETIRED = "retired"


class ProxyType(str, Enum):
    """Proxy type classification."""

    RESIDENTIAL = "residential"
    MOBILE = "mobile"
    ISP = "isp"
    DATACENTER = "datacenter"


# =============================================================================
# Provider Configurations
# =============================================================================


class BrightDataConfig:
    """
    Bright Data residential proxy configuration.

    Primary provider with largest pool and best geo-coverage.
    Supports residential, mobile, ISP, and datacenter proxies.
    """

    # Endpoint patterns
    RESIDENTIAL_ENDPOINT = "brd.superproxy.io:22225"
    MOBILE_ENDPOINT = "brd.superproxy.io:22226"
    ISP_ENDPOINT = "brd.superproxy.io:22227"

    # Geo-targeting country codes
    COUNTRY_CODES: dict[str, str] = {
        "turkey": "tr",
        "germany": "de",
        "netherlands": "nl",
        "france": "fr",
        "spain": "es",
        "greece": "gr",
    }

    # Session types
    SESSION_TYPES: dict[str, str] = {
        "rotating": "",  # Each request gets different IP
        "sticky": "session-",  # Same IP for 30 minutes
    }

    @classmethod
    def build_proxy_url(
        cls,
        customer_id: str,
        zone: str,
        password: str,
        country: str = "tr",
        session_id: str | None = None,
        proxy_type: str = "residential",
    ) -> dict[str, Any]:
        """
        Build Bright Data proxy URL.

        Args:
            customer_id: Bright Data customer ID.
            zone: Proxy zone name.
            password: Zone password.
            country: Target country code.
            session_id: Optional session ID for sticky sessions.
            proxy_type: Type of proxy (residential, mobile, isp).

        Returns:
            Dictionary with proxy connection details.
        """
        endpoint = cls.RESIDENTIAL_ENDPOINT
        if proxy_type == "mobile":
            endpoint = cls.MOBILE_ENDPOINT
        elif proxy_type == "isp":
            endpoint = cls.ISP_ENDPOINT

        username = f"brd-customer-{customer_id}-zone-{zone}"

        # Country targeting
        username += f"-country-{country}"

        # Session (sticky IP)
        if session_id:
            username += f"-session-{session_id}"

        host, port = endpoint.split(":")

        return {
            "protocol": "http",
            "host": host,
            "port": int(port),
            "username": username,
            "password": password,
        }


class OxylabsConfig:
    """
    Oxylabs backup provider configuration.

    Secondary provider with good residential pool quality.
    """

    RESIDENTIAL_ENDPOINT = "pr.oxylabs.io:7777"

    @classmethod
    def build_proxy_url(
        cls,
        username: str,
        password: str,
        country: str = "tr",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Build Oxylabs proxy URL.

        Args:
            username: Oxylabs username.
            password: Account password.
            country: Target country code.
            session_id: Optional session ID for sticky sessions.

        Returns:
            Dictionary with proxy connection details.
        """
        user = f"customer-{username}-cc-{country}"

        if session_id:
            user += f"-sessid-{session_id}"

        return {
            "protocol": "http",
            "host": "pr.oxylabs.io",
            "port": 7777,
            "username": user,
            "password": password,
        }


class SmartproxyConfig:
    """
    Smartproxy tertiary provider configuration.

    Budget option with acceptable quality for backup scenarios.
    """

    RESIDENTIAL_ENDPOINT = "gate.smartproxy.com:7000"

    @classmethod
    def build_proxy_url(
        cls,
        username: str,
        password: str,
        country: str = "tr",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Build Smartproxy proxy URL.

        Args:
            username: Smartproxy username.
            password: Account password.
            country: Target country code.
            session_id: Optional session ID for sticky sessions.

        Returns:
            Dictionary with proxy connection details.
        """
        user = f"user-{username}-country-{country}"

        if session_id:
            user += f"-session-{session_id}"

        return {
            "protocol": "http",
            "host": "gate.smartproxy.com",
            "port": 7000,
            "username": user,
            "password": password,
        }


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ProxyHealth:
    """
    Proxy health metrics and status tracking.

    Tracks request statistics, response times, failures,
    and ASN information for intelligent proxy selection.

    Attributes:
        proxy_id: Unique proxy identifier.
        provider: Proxy provider name (brightdata, oxylabs, smartproxy).
        proxy_type: Type of proxy (residential, mobile, isp, datacenter).
        country: Target country code.
        status: Current health status.
        health_score: Health score from 0-100.
        total_requests: Total number of requests made.
        successful_requests: Number of successful requests.
        failed_requests: Number of failed requests.
        consecutive_failures: Count of consecutive failures.
        avg_response_ms: Average response time in milliseconds.
        last_used_at: Timestamp of last use.
        last_success_at: Timestamp of last successful request.
        cooldown_until: Cooldown end timestamp.
        asn: Autonomous System Number for diversity tracking.
        subnet: IP subnet for pattern detection.
    """

    proxy_id: str
    provider: str
    proxy_type: ProxyType
    country: str

    status: ProxyStatus = ProxyStatus.ACTIVE
    health_score: int = 100

    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    consecutive_failures: int = 0

    avg_response_ms: float = 0.0
    last_used_at: float | None = None
    last_success_at: float | None = None
    cooldown_until: float | None = None

    # ASN tracking for anti-pattern detection
    asn: str | None = None
    subnet: str | None = None


# =============================================================================
# Proxy Pool Manager
# =============================================================================


class ProxyPoolManager:
    """
    Core proxy pool management with health tracking.

    Manages proxy selection, health scoring, rotation,
    and automatic cooldown/retirement based on performance.

    Attributes:
        proxies: Dictionary of ProxyHealth objects by proxy_id.
        provider_configs: Provider configuration dictionary.
        recent_asns: List of recently used ASNs for diversity.
    """

    # Health thresholds
    HEALTH_THRESHOLD_SLOW = 70
    HEALTH_THRESHOLD_BLOCKED = 40
    HEALTH_THRESHOLD_RETIRE = 20

    # Failure handling
    CONSECUTIVE_FAILURE_COOLDOWN = 3
    COOLDOWN_DURATION_SECONDS = 300  # 5 minutes

    # Usage limits
    MAX_REQUESTS_PER_PROXY = 1000

    def __init__(self) -> None:
        """Initialize proxy pool manager."""
        self.proxies: dict[str, ProxyHealth] = {}
        self.provider_configs: dict[str, Any] = {}
        self._lock = asyncio.Lock()

        # ASN diversity tracking
        self.recent_asns: list[str] = []
        self.max_asn_history = 10

    async def initialize(self, configs: dict[str, Any]) -> None:
        """
        Initialize with provider configurations.

        Args:
            configs: Dictionary with provider credentials.
                     Keys: brightdata, oxylabs, smartproxy
                     Values: Provider-specific config dicts
        """
        self.provider_configs = configs

        # Pre-warm proxy pool with virtual proxy entries
        for provider in ["brightdata", "oxylabs", "smartproxy"]:
            if provider in configs:
                await self._register_provider_proxies(provider, configs[provider])

        logger.info(
            "proxy_pool_initialized",
            total_proxies=len(self.proxies),
            providers=list(configs.keys()),
        )

    async def _register_provider_proxies(
        self,
        provider: str,
        config: dict[str, Any],
    ) -> None:
        """
        Register virtual proxy entries for a provider.

        Args:
            provider: Provider name.
            config: Provider configuration.
        """
        # Countries to support
        countries = config.get("countries", ["tr"])
        proxy_types = config.get("proxy_types", [ProxyType.RESIDENTIAL])

        # Create virtual proxy entries for each combination
        proxies_per_combo = config.get("pool_size", 10)

        for country in countries:
            for proxy_type in proxy_types:
                if isinstance(proxy_type, str):
                    proxy_type = ProxyType(proxy_type)

                for i in range(proxies_per_combo):
                    proxy_id = f"{provider}_{country}_{proxy_type.value}_{i}"

                    self.proxies[proxy_id] = ProxyHealth(
                        proxy_id=proxy_id,
                        provider=provider,
                        proxy_type=proxy_type,
                        country=country,
                    )

        logger.debug(
            "provider_proxies_registered",
            provider=provider,
            countries=countries,
            count=len([p for p in self.proxies.values() if p.provider == provider]),
        )

    async def get_proxy(
        self,
        target_site: str,
        country: str = "tr",
        sticky_session: bool = True,
        proxy_type: ProxyType = ProxyType.RESIDENTIAL,
        exclude_asns: list[str] | None = None,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """
        Select the best available proxy.

        Uses weighted random selection based on health score
        with ASN diversity consideration.

        Args:
            target_site: Target site code (vfs, idata, etc.).
            country: Target country code.
            sticky_session: Whether to use sticky session.
            proxy_type: Preferred proxy type.
            exclude_asns: ASNs to exclude for diversity.
            provider: Specific provider to use (optional).

        Returns:
            Dictionary with proxy connection details and metadata.

        Raises:
            ProxyPoolExhaustedError: If no eligible proxies available.
        """
        async with self._lock:
            # Filter eligible proxies
            eligible = self._filter_eligible_proxies(
                country=country,
                proxy_type=proxy_type,
                exclude_asns=exclude_asns or self.recent_asns[-5:],
                provider=provider,
            )

            if not eligible:
                # Fallback to different proxy type
                fallback_type = (
                    ProxyType.MOBILE
                    if proxy_type == ProxyType.RESIDENTIAL
                    else ProxyType.ISP
                )
                eligible = self._filter_eligible_proxies(
                    country=country,
                    proxy_type=fallback_type,
                    provider=provider,
                )

            if not eligible:
                raise ProxyPoolExhaustedError(
                    f"No proxy available for {country}",
                    country=country,
                    provider=provider,
                )

            # Weighted random selection based on health score
            proxy = self._weighted_select(eligible)

            # Generate session ID for sticky
            session_id = None
            if sticky_session:
                session_id = f"{target_site}_{int(time.time())}_{random.randint(1000, 9999)}"

            # Build proxy URL
            proxy_url = self._build_proxy_url(proxy, session_id)

            # Track ASN for diversity
            if proxy.asn:
                self.recent_asns.append(proxy.asn)
                if len(self.recent_asns) > self.max_asn_history:
                    self.recent_asns.pop(0)

            # Update last used
            proxy.last_used_at = time.time()

            logger.debug(
                "proxy_selected",
                proxy_id=proxy.proxy_id,
                provider=proxy.provider,
                health_score=proxy.health_score,
                target_site=target_site,
            )

            return {
                "proxy_id": proxy.proxy_id,
                "session_id": session_id,
                **proxy_url,
            }

    async def get_proxy_by_id(self, proxy_id: str) -> dict[str, Any] | None:
        """
        Get proxy connection details by its ID.

        Args:
            proxy_id: The proxy identifier.

        Returns:
            Dictionary with proxy connection details, or None if not found.
        """
        proxy_health = self.proxies.get(proxy_id)
        if not proxy_health:
            return None

        proxy_url = self._build_proxy_url(proxy_health, session_id=None)
        return {
            "proxy_id": proxy_health.proxy_id,
            "provider": proxy_health.provider,
            "status": proxy_health.status.value,
            "health_score": proxy_health.health_score,
            "country": proxy_health.country,
            **proxy_url,
        }

    def _filter_eligible_proxies(
        self,
        country: str,
        proxy_type: ProxyType,
        exclude_asns: list[str] | None = None,
        provider: str | None = None,
    ) -> list[ProxyHealth]:
        """
        Filter proxies by eligibility criteria.

        Args:
            country: Target country code.
            proxy_type: Required proxy type.
            exclude_asns: ASNs to exclude.
            provider: Specific provider filter.

        Returns:
            List of eligible ProxyHealth objects.
        """
        eligible = []
        now = time.time()

        for proxy in self.proxies.values():
            # Status check
            if proxy.status in [ProxyStatus.BLOCKED, ProxyStatus.RETIRED]:
                continue

            # Cooldown check
            if proxy.cooldown_until and proxy.cooldown_until > now:
                continue

            # Country check
            if proxy.country != country:
                continue

            # Type check
            if proxy.proxy_type != proxy_type:
                continue

            # Provider filter
            if provider and proxy.provider != provider:
                continue

            # ASN diversity (anti-pattern)
            if exclude_asns and proxy.asn in exclude_asns:
                continue

            # Usage limit check
            if proxy.total_requests >= self.MAX_REQUESTS_PER_PROXY:
                continue

            eligible.append(proxy)

        return eligible

    def _weighted_select(self, proxies: list[ProxyHealth]) -> ProxyHealth:
        """
        Select proxy using weighted random based on health score.

        Proxies with higher health scores and longer idle times
        have higher probability of selection.

        Args:
            proxies: List of eligible proxies.

        Returns:
            Selected ProxyHealth object.
        """
        weights = []
        now = time.time()

        for proxy in proxies:
            weight = proxy.health_score

            # Recency bonus: less recently used proxies preferred
            if proxy.last_used_at:
                idle_time = now - proxy.last_used_at
                recency_bonus = min(idle_time / 60, 20)  # Max 20 bonus
                weight += recency_bonus
            else:
                weight += 20  # Never used, give bonus

            weights.append(max(weight, 1))

        return random.choices(proxies, weights=weights, k=1)[0]

    def _build_proxy_url(
        self,
        proxy: ProxyHealth,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Build proxy URL for the given proxy and provider.

        Args:
            proxy: ProxyHealth object.
            session_id: Optional session ID for sticky sessions.

        Returns:
            Dictionary with proxy connection details.

        Raises:
            ValueError: If provider is unknown.
        """
        config = self.provider_configs.get(proxy.provider)

        if not config:
            raise ValueError(f"No config for provider: {proxy.provider}")

        if proxy.provider == "brightdata":
            return BrightDataConfig.build_proxy_url(
                customer_id=config["customer_id"],
                zone=config["zone"],
                password=config["password"],
                country=proxy.country,
                session_id=session_id,
                proxy_type=proxy.proxy_type.value,
            )
        elif proxy.provider == "oxylabs":
            return OxylabsConfig.build_proxy_url(
                username=config["username"],
                password=config["password"],
                country=proxy.country,
                session_id=session_id,
            )
        elif proxy.provider == "smartproxy":
            return SmartproxyConfig.build_proxy_url(
                username=config["username"],
                password=config["password"],
                country=proxy.country,
                session_id=session_id,
            )

        raise ValueError(f"Unknown provider: {proxy.provider}")

    async def report_success(
        self,
        proxy_id: str,
        response_time_ms: float,
    ) -> None:
        """
        Report successful request through proxy.

        Updates health metrics and clears cooldown if applicable.

        Args:
            proxy_id: Proxy identifier.
            response_time_ms: Response time in milliseconds.
        """
        async with self._lock:
            if proxy_id not in self.proxies:
                return

            proxy = self.proxies[proxy_id]

            proxy.total_requests += 1
            proxy.successful_requests += 1
            proxy.consecutive_failures = 0
            proxy.last_success_at = time.time()

            # Update average response time
            if proxy.total_requests > 1:
                proxy.avg_response_ms = (
                    (proxy.avg_response_ms * (proxy.total_requests - 1) + response_time_ms)
                    / proxy.total_requests
                )
            else:
                proxy.avg_response_ms = response_time_ms

            # Recalculate health
            self._recalculate_health(proxy)

            # Clear cooldown
            if proxy.status == ProxyStatus.COOLDOWN:
                proxy.status = ProxyStatus.ACTIVE
                proxy.cooldown_until = None

            logger.debug(
                "proxy_success_reported",
                proxy_id=proxy_id,
                response_ms=response_time_ms,
                health_score=proxy.health_score,
            )

    async def report_failure(
        self,
        proxy_id: str,
        error_type: str,
        is_blocked: bool = False,
    ) -> None:
        """
        Report failed request through proxy.

        Applies health penalty and may trigger cooldown/retirement.

        Args:
            proxy_id: Proxy identifier.
            error_type: Type of error that occurred.
            is_blocked: Whether the proxy was blocked by target.
        """
        async with self._lock:
            if proxy_id not in self.proxies:
                return

            proxy = self.proxies[proxy_id]

            proxy.total_requests += 1
            proxy.failed_requests += 1
            proxy.consecutive_failures += 1

            # Health penalty
            if is_blocked:
                proxy.health_score = max(0, proxy.health_score - 30)
            else:
                proxy.health_score = max(0, proxy.health_score - 10)

            # Status transitions
            if is_blocked or proxy.consecutive_failures >= self.CONSECUTIVE_FAILURE_COOLDOWN:
                proxy.status = ProxyStatus.COOLDOWN
                proxy.cooldown_until = time.time() + self.COOLDOWN_DURATION_SECONDS

            if proxy.health_score < self.HEALTH_THRESHOLD_BLOCKED:
                proxy.status = ProxyStatus.BLOCKED

            if proxy.health_score < self.HEALTH_THRESHOLD_RETIRE:
                proxy.status = ProxyStatus.RETIRED

            self._recalculate_health(proxy)

            logger.warning(
                "proxy_failure_reported",
                proxy_id=proxy_id,
                error_type=error_type,
                is_blocked=is_blocked,
                health_score=proxy.health_score,
                status=proxy.status.value,
            )

    def _recalculate_health(self, proxy: ProxyHealth) -> None:
        """
        Recalculate health score based on current metrics.

        Args:
            proxy: ProxyHealth object to update.
        """
        if proxy.total_requests == 0:
            proxy.health_score = 100
            return

        # Base score from success rate
        success_rate = proxy.successful_requests / proxy.total_requests
        base_score = success_rate * 100

        # Response time penalty
        if proxy.avg_response_ms > 5000:  # Over 5 seconds
            base_score -= 20
        elif proxy.avg_response_ms > 3000:
            base_score -= 10

        # Consecutive failure penalty
        failure_penalty = min(proxy.consecutive_failures * 10, 50)

        proxy.health_score = int(max(0, min(100, base_score - failure_penalty)))

        # Update status based on health
        if (
            proxy.health_score < self.HEALTH_THRESHOLD_SLOW
            and proxy.status == ProxyStatus.ACTIVE
        ):
            proxy.status = ProxyStatus.SLOW


# =============================================================================
# Rotation Strategies
# =============================================================================


class RotatingProxyStrategy:
    """
    Per-request rotation strategy.

    Each request gets a different IP address.
    Best for scraping and non-authenticated flows.
    """

    def __init__(self, pool: ProxyPoolManager) -> None:
        """
        Initialize rotating strategy.

        Args:
            pool: ProxyPoolManager instance.
        """
        self.pool = pool

    async def get_proxy(self, **kwargs: Any) -> dict[str, Any]:
        """
        Get a new proxy for each request.

        Args:
            **kwargs: Arguments passed to pool.get_proxy().

        Returns:
            Proxy connection details.
        """
        return await self.pool.get_proxy(
            sticky_session=False,
            **kwargs,
        )


class StickySessionStrategy:
    """
    Sticky session strategy for authenticated flows.

    Maintains the same IP for a session duration (up to 30 minutes).
    Essential for login flows and multi-step bookings.
    """

    def __init__(self, pool: ProxyPoolManager) -> None:
        """
        Initialize sticky session strategy.

        Args:
            pool: ProxyPoolManager instance.
        """
        self.pool = pool
        self.active_sessions: dict[str, dict[str, Any]] = {}

    async def get_proxy(self, session_key: str, **kwargs: Any) -> dict[str, Any]:
        """
        Get or reuse proxy for session.

        Args:
            session_key: Unique session identifier.
            **kwargs: Arguments passed to pool.get_proxy().

        Returns:
            Proxy connection details.
        """
        if session_key in self.active_sessions:
            # Return existing proxy if not expired
            session = self.active_sessions[session_key]
            if time.time() - session["created_at"] < 1800:  # 30 minutes
                return session["proxy"]

        # Get new sticky session
        proxy = await self.pool.get_proxy(
            sticky_session=True,
            **kwargs,
        )

        self.active_sessions[session_key] = {
            "proxy": proxy,
            "created_at": time.time(),
        }

        return proxy

    async def release_session(self, session_key: str) -> None:
        """
        Release a sticky session.

        Args:
            session_key: Session identifier to release.
        """
        if session_key in self.active_sessions:
            del self.active_sessions[session_key]
            logger.debug("sticky_session_released", session_key=session_key)


class GeoRotationStrategy:
    """
    Geographic rotation strategy for pattern detection evasion.

    Cycles through countries to avoid geographic fingerprinting.
    """

    COUNTRY_SEQUENCE = ["tr", "de", "nl", "fr", "tr"]

    def __init__(self, pool: ProxyPoolManager) -> None:
        """
        Initialize geo rotation strategy.

        Args:
            pool: ProxyPoolManager instance.
        """
        self.pool = pool
        self.country_index = 0

    async def get_proxy(self, target_site: str, **kwargs: Any) -> dict[str, Any]:
        """
        Get proxy from rotating countries.

        Args:
            target_site: Target site code.
            **kwargs: Additional arguments.

        Returns:
            Proxy connection details.
        """
        country = self.COUNTRY_SEQUENCE[self.country_index]
        self.country_index = (self.country_index + 1) % len(self.COUNTRY_SEQUENCE)

        return await self.pool.get_proxy(
            target_site=target_site,
            country=country,
            **kwargs,
        )


class ASNDiversityStrategy:
    """
    ASN diversity strategy for anti-pattern detection.

    Ensures consecutive requests use different ASNs to
    avoid behavioral fingerprinting.
    """

    def __init__(self, pool: ProxyPoolManager) -> None:
        """
        Initialize ASN diversity strategy.

        Args:
            pool: ProxyPoolManager instance.
        """
        self.pool = pool
        self.used_asns: list[str] = []
        self.max_history = 10

    async def get_proxy(self, **kwargs: Any) -> dict[str, Any]:
        """
        Get proxy from different ASN.

        Args:
            **kwargs: Arguments passed to pool.get_proxy().

        Returns:
            Proxy connection details.
        """
        proxy = await self.pool.get_proxy(
            exclude_asns=self.used_asns[-5:],  # Exclude last 5 ASNs
            **kwargs,
        )

        # Track ASN from proxy response
        if proxy.get("asn"):
            self.used_asns.append(proxy["asn"])
            if len(self.used_asns) > self.max_history:
                self.used_asns.pop(0)

        return proxy


# =============================================================================
# Site-Specific Configuration
# =============================================================================


class SiteProxyConfig:
    """
    Site-specific proxy configuration.

    Different sites have different detection capabilities
    and proxy requirements.
    """

    SITE_CONFIGS: dict[str, dict[str, Any]] = {
        "vfs": {
            "proxy_type": ProxyType.RESIDENTIAL,
            "sticky_session": True,
            "countries": ["tr"],
            "rotation": "sticky",
            "min_health": 80,
            "failover_types": [ProxyType.MOBILE, ProxyType.ISP],
        },
        "idata": {
            "proxy_type": ProxyType.RESIDENTIAL,
            "sticky_session": True,
            "countries": ["tr"],
            "rotation": "sticky",
            "min_health": 60,  # More tolerant
            "failover_types": [ProxyType.DATACENTER],  # iDATA accepts datacenter
        },
        "bls": {
            "proxy_type": ProxyType.RESIDENTIAL,
            "sticky_session": True,
            "countries": ["tr", "es"],
            "rotation": "sticky",
            "min_health": 70,
            "failover_types": [ProxyType.ISP],
        },
        "kkosmos": {
            "proxy_type": ProxyType.RESIDENTIAL,
            "sticky_session": True,
            "countries": ["tr", "gr"],
            "rotation": "sticky",
            "min_health": 75,
            "failover_types": [ProxyType.MOBILE],
        },
    }

    @classmethod
    def get_config(cls, site: str) -> dict[str, Any]:
        """
        Get proxy configuration for a site.

        Args:
            site: Site code (vfs, idata, bls, kkosmos).

        Returns:
            Site-specific proxy configuration.
        """
        return cls.SITE_CONFIGS.get(site, cls.SITE_CONFIGS["vfs"])


# =============================================================================
# Provider Failover Chain
# =============================================================================


class ProxyFailoverChain:
    """
    Provider-level failover management.

    Automatically switches between providers when one
    experiences issues or exhaustion.
    """

    PROVIDER_PRIORITY = [
        "brightdata",  # Primary - largest pool
        "oxylabs",  # Secondary - good quality
        "smartproxy",  # Tertiary - budget option
    ]

    def __init__(self, pool: ProxyPoolManager) -> None:
        """
        Initialize failover chain.

        Args:
            pool: ProxyPoolManager instance.
        """
        self.pool = pool
        self.provider_status: dict[str, bool] = {p: True for p in self.PROVIDER_PRIORITY}
        self.failure_counts: dict[str, int] = {p: 0 for p in self.PROVIDER_PRIORITY}

    async def get_proxy_with_failover(self, **kwargs: Any) -> dict[str, Any]:
        """
        Get proxy with automatic provider failover.

        Args:
            **kwargs: Arguments passed to pool.get_proxy().

        Returns:
            Proxy connection details.

        Raises:
            ProxyPoolExhaustedError: If all providers exhausted.
        """
        for provider in self.PROVIDER_PRIORITY:
            if not self.provider_status[provider]:
                continue

            try:
                proxy = await self.pool.get_proxy(
                    provider=provider,
                    **kwargs,
                )

                # Success, reset failure count
                self.failure_counts[provider] = 0
                return proxy

            except ProxyPoolExhaustedError:
                self.failure_counts[provider] += 1

                # Disable provider after 5 consecutive failures
                if self.failure_counts[provider] >= 5:
                    self.provider_status[provider] = False
                    asyncio.create_task(self._reactivate_provider(provider, delay=300))
                    logger.warning(
                        "provider_disabled",
                        provider=provider,
                        failure_count=self.failure_counts[provider],
                    )

                continue

        raise ProxyPoolExhaustedError("All providers exhausted")

    async def _reactivate_provider(self, provider: str, delay: int) -> None:
        """
        Reactivate a provider after delay.

        Args:
            provider: Provider name.
            delay: Seconds to wait before reactivation.
        """
        await asyncio.sleep(delay)
        self.provider_status[provider] = True
        self.failure_counts[provider] = 0
        logger.info("provider_reactivated", provider=provider)


# =============================================================================
# Cost Optimization
# =============================================================================


class ProxyCostOptimizer:
    """
    Proxy bandwidth and cost optimization.

    Tracks usage across providers and proxy types to
    stay within budget while maximizing success rates.
    """

    # Pricing per GB in USD
    PRICING: dict[str, dict[str, float]] = {
        "brightdata": {
            "residential": 12.0,
            "mobile": 25.0,
            "isp": 8.0,
            "datacenter": 0.5,
        },
        "oxylabs": {
            "residential": 10.0,
            "mobile": 20.0,
        },
        "smartproxy": {
            "residential": 8.0,
        },
    }

    def __init__(self, budget_limit: float = 500.0) -> None:
        """
        Initialize cost optimizer.

        Args:
            budget_limit: Monthly budget limit in USD.
        """
        self.usage: dict[str, float] = {}  # provider_type -> GB used
        self.budget_limit = budget_limit

    def track_usage(
        self,
        provider: str,
        proxy_type: str,
        bytes_transferred: int,
    ) -> None:
        """
        Track bandwidth usage.

        Args:
            provider: Provider name.
            proxy_type: Type of proxy used.
            bytes_transferred: Bytes transferred.
        """
        gb = bytes_transferred / (1024**3)

        key = f"{provider}_{proxy_type}"
        self.usage[key] = self.usage.get(key, 0) + gb

    def get_estimated_cost(self) -> float:
        """
        Calculate estimated monthly cost.

        Returns:
            Estimated cost in USD.
        """
        total = 0.0

        for key, gb in self.usage.items():
            provider, proxy_type = key.rsplit("_", 1)
            price_per_gb = self.PRICING.get(provider, {}).get(proxy_type, 10.0)
            total += gb * price_per_gb

        return total

    def should_downgrade(self) -> bool:
        """
        Check if proxy type should be downgraded for cost.

        Returns:
            True if budget threshold (80%) exceeded.
        """
        return self.get_estimated_cost() > self.budget_limit * 0.8

    def get_cost_effective_proxy_type(self, site: str) -> ProxyType:
        """
        Get cost-effective proxy type for site.

        Args:
            site: Target site code.

        Returns:
            Recommended proxy type.
        """
        if self.should_downgrade():
            # iDATA accepts datacenter
            if site == "idata":
                return ProxyType.DATACENTER
            # VFS needs at least ISP
            return ProxyType.ISP

        return ProxyType.RESIDENTIAL


# =============================================================================
# Health Monitoring
# =============================================================================


class ProxyHealthMonitor:
    """
    Proxy pool health monitoring and alerting.

    Provides real-time visibility into pool status,
    provider health, and alert generation.
    """

    def __init__(self, pool: ProxyPoolManager) -> None:
        """
        Initialize health monitor.

        Args:
            pool: ProxyPoolManager instance.
        """
        self.pool = pool

    async def get_pool_status(self) -> dict[str, Any]:
        """
        Get comprehensive pool status.

        Returns:
            Dictionary with pool statistics and alerts.
        """
        status_counts: dict[str, int] = {s.value: 0 for s in ProxyStatus}
        provider_stats: dict[str, dict[str, Any]] = {}
        type_stats: dict[str, dict[str, int]] = {}

        for proxy in self.pool.proxies.values():
            status_counts[proxy.status.value] += 1

            # Provider stats
            if proxy.provider not in provider_stats:
                provider_stats[proxy.provider] = {
                    "total": 0,
                    "active": 0,
                    "success_rate": 0.0,
                }
            provider_stats[proxy.provider]["total"] += 1
            if proxy.status == ProxyStatus.ACTIVE:
                provider_stats[proxy.provider]["active"] += 1

            # Type stats
            if proxy.proxy_type.value not in type_stats:
                type_stats[proxy.proxy_type.value] = {"total": 0, "active": 0}
            type_stats[proxy.proxy_type.value]["total"] += 1
            if proxy.status == ProxyStatus.ACTIVE:
                type_stats[proxy.proxy_type.value]["active"] += 1

        return {
            "total_proxies": len(self.pool.proxies),
            "status_breakdown": status_counts,
            "by_provider": provider_stats,
            "by_type": type_stats,
            "health_avg": self._calculate_avg_health(),
            "alerts": self._check_alerts(status_counts),
        }

    def _calculate_avg_health(self) -> float:
        """
        Calculate average health score.

        Returns:
            Average health score across all proxies.
        """
        if not self.pool.proxies:
            return 0.0

        total = sum(p.health_score for p in self.pool.proxies.values())
        return total / len(self.pool.proxies)

    def _check_alerts(self, status_counts: dict[str, int]) -> list[str]:
        """
        Check for alert conditions.

        Args:
            status_counts: Dictionary of status counts.

        Returns:
            List of alert messages.
        """
        alerts = []

        total = sum(status_counts.values())
        if total == 0:
            alerts.append("CRITICAL: No proxies in pool")
            return alerts

        active_rate = status_counts.get("active", 0) / total

        if active_rate < 0.3:
            alerts.append("CRITICAL: Less than 30% proxies active")
        elif active_rate < 0.5:
            alerts.append("WARNING: Less than 50% proxies active")

        if status_counts.get("blocked", 0) > total * 0.2:
            alerts.append("WARNING: More than 20% proxies blocked")

        return alerts


# =============================================================================
# High-Level Proxy Manager
# =============================================================================


class ProxyManager:
    """
    High-level proxy management interface.

    Combines pool management, rotation strategies, failover,
    and monitoring into a single unified interface.

    Usage:
        manager = ProxyManager()
        await manager.initialize(configs)

        proxy = await manager.get_proxy(
            target_site="vfs",
            country="tr",
        )

        await manager.report_success(proxy["proxy_id"], 250.0)
    """

    def __init__(self) -> None:
        """Initialize proxy manager."""
        self.pool = ProxyPoolManager()
        self.failover = ProxyFailoverChain(self.pool)
        self.cost_optimizer = ProxyCostOptimizer()
        self.monitor = ProxyHealthMonitor(self.pool)
        self.sticky_strategy = StickySessionStrategy(self.pool)
        self._initialized = False

    async def initialize(self, configs: dict[str, Any]) -> None:
        """
        Initialize proxy manager with provider configs.

        Args:
            configs: Provider configuration dictionary.
        """
        await self.pool.initialize(configs)
        self._initialized = True

        logger.info(
            "proxy_manager_initialized",
            providers=list(configs.keys()),
        )

    async def get_proxy(
        self,
        target_site: str,
        country: str = "tr",
        session_key: str | None = None,
        use_failover: bool = True,
    ) -> dict[str, Any]:
        """
        Get optimal proxy for target site.

        Args:
            target_site: Target site code (vfs, idata, etc.).
            country: Target country code.
            session_key: Optional session key for sticky sessions.
            use_failover: Whether to use provider failover.

        Returns:
            Proxy connection details.
        """
        # Get site-specific configuration
        site_config = SiteProxyConfig.get_config(target_site)

        # Check cost optimization
        proxy_type = self.cost_optimizer.get_cost_effective_proxy_type(target_site)

        kwargs = {
            "target_site": target_site,
            "country": country,
            "sticky_session": site_config["sticky_session"],
            "proxy_type": proxy_type,
        }

        # Use sticky strategy if session key provided
        if session_key:
            return await self.sticky_strategy.get_proxy(session_key, **kwargs)

        # Use failover chain
        if use_failover:
            return await self.failover.get_proxy_with_failover(**kwargs)

        return await self.pool.get_proxy(**kwargs)

    async def report_success(
        self,
        proxy_id: str,
        response_time_ms: float,
        bytes_transferred: int = 0,
    ) -> None:
        """
        Report successful proxy use.

        Args:
            proxy_id: Proxy identifier.
            response_time_ms: Response time in milliseconds.
            bytes_transferred: Bytes transferred (for cost tracking).
        """
        await self.pool.report_success(proxy_id, response_time_ms)

        # Track cost if bytes provided
        if bytes_transferred > 0 and proxy_id in self.pool.proxies:
            proxy = self.pool.proxies[proxy_id]
            self.cost_optimizer.track_usage(
                proxy.provider,
                proxy.proxy_type.value,
                bytes_transferred,
            )

    async def report_failure(
        self,
        proxy_id: str,
        error_type: str,
        is_blocked: bool = False,
    ) -> None:
        """
        Report proxy failure.

        Args:
            proxy_id: Proxy identifier.
            error_type: Type of error.
            is_blocked: Whether proxy was blocked.
        """
        await self.pool.report_failure(proxy_id, error_type, is_blocked)

    async def release_session(self, session_key: str) -> None:
        """
        Release a sticky session.

        Args:
            session_key: Session identifier.
        """
        await self.sticky_strategy.release_session(session_key)

    async def get_status(self) -> dict[str, Any]:
        """
        Get proxy manager status.

        Returns:
            Comprehensive status dictionary.
        """
        pool_status = await self.monitor.get_pool_status()

        return {
            **pool_status,
            "estimated_cost_usd": self.cost_optimizer.get_estimated_cost(),
            "should_downgrade": self.cost_optimizer.should_downgrade(),
            "active_sticky_sessions": len(self.sticky_strategy.active_sessions),
        }


# =============================================================================
# Exports
# =============================================================================

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
