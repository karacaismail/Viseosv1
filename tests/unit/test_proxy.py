"""
Unit Tests for Proxy Manager.

This module provides comprehensive tests for the proxy management system,
including provider configurations, pool management, rotation strategies,
failover chains, cost optimization, and health monitoring.

Test Categories:
- Provider Configurations: BrightData, Oxylabs, Smartproxy URL building
- ProxyHealth: Health data class initialization and attributes
- ProxyPoolManager: Pool operations, selection, health tracking
- Rotation Strategies: Rotating, Sticky, Geo, ASN diversity
- Failover Chain: Provider-level failover logic
- Cost Optimizer: Budget tracking and cost calculations
- Health Monitor: Pool status and alerts
- ProxyManager: High-level interface integration

Usage:
    pytest tests/unit/test_proxy.py -v
    pytest tests/unit/test_proxy.py -k "test_brightdata" -v
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.bot.services.proxy import (
    ASNDiversityStrategy,
    BrightDataConfig,
    GeoRotationStrategy,
    OxylabsConfig,
    ProxyCostOptimizer,
    ProxyFailoverChain,
    ProxyHealth,
    ProxyHealthMonitor,
    ProxyManager,
    ProxyPoolManager,
    ProxyStatus,
    ProxyType,
    RotatingProxyStrategy,
    SiteProxyConfig,
    SmartproxyConfig,
    StickySessionStrategy,
)
from src.core.exceptions import ProxyPoolExhaustedError


# =============================================================================
# BrightData Configuration Tests
# =============================================================================


class TestBrightDataConfig:
    """Tests for BrightData proxy provider configuration."""

    def test_endpoint_constants(self) -> None:
        """Verify BrightData endpoint constants are correct."""
        assert BrightDataConfig.RESIDENTIAL_ENDPOINT == "brd.superproxy.io:22225"
        assert BrightDataConfig.MOBILE_ENDPOINT == "brd.superproxy.io:22226"
        assert BrightDataConfig.ISP_ENDPOINT == "brd.superproxy.io:22227"

    def test_country_codes(self) -> None:
        """Verify BrightData country code mappings."""
        assert BrightDataConfig.COUNTRY_CODES["turkey"] == "tr"
        assert BrightDataConfig.COUNTRY_CODES["germany"] == "de"
        assert "netherlands" in BrightDataConfig.COUNTRY_CODES

    def test_session_types(self) -> None:
        """Verify BrightData session type configurations."""
        assert BrightDataConfig.SESSION_TYPES["rotating"] == ""
        assert BrightDataConfig.SESSION_TYPES["sticky"] == "session-"

    def test_build_proxy_url_residential(self) -> None:
        """Verify residential proxy URL building."""
        result = BrightDataConfig.build_proxy_url(
            customer_id="test_customer",
            zone="residential_zone",
            password="test_pass",
            country="tr",
        )

        assert result["protocol"] == "http"
        assert result["host"] == "brd.superproxy.io"
        assert result["port"] == 22225
        assert "test_customer" in result["username"]
        assert "residential_zone" in result["username"]
        assert "-country-tr" in result["username"]
        assert result["password"] == "test_pass"

    def test_build_proxy_url_mobile(self) -> None:
        """Verify mobile proxy URL building."""
        result = BrightDataConfig.build_proxy_url(
            customer_id="test_customer",
            zone="mobile_zone",
            password="test_pass",
            country="de",
            proxy_type="mobile",
        )

        assert result["port"] == 22226
        assert "-country-de" in result["username"]

    def test_build_proxy_url_with_session(self) -> None:
        """Verify proxy URL building with session ID."""
        result = BrightDataConfig.build_proxy_url(
            customer_id="test_customer",
            zone="test_zone",
            password="test_pass",
            session_id="session_123",
        )

        assert "-session-session_123" in result["username"]

    def test_build_proxy_url_isp(self) -> None:
        """Verify ISP proxy URL building."""
        result = BrightDataConfig.build_proxy_url(
            customer_id="test_customer",
            zone="isp_zone",
            password="test_pass",
            proxy_type="isp",
        )

        assert result["port"] == 22227


# =============================================================================
# Oxylabs Configuration Tests
# =============================================================================


class TestOxylabsConfig:
    """Tests for Oxylabs proxy provider configuration."""

    def test_endpoint_constant(self) -> None:
        """Verify Oxylabs endpoint constant."""
        assert OxylabsConfig.RESIDENTIAL_ENDPOINT == "pr.oxylabs.io:7777"

    def test_build_proxy_url_basic(self) -> None:
        """Verify basic Oxylabs proxy URL building."""
        result = OxylabsConfig.build_proxy_url(
            username="test_user",
            password="test_pass",
            country="tr",
        )

        assert result["protocol"] == "http"
        assert result["host"] == "pr.oxylabs.io"
        assert result["port"] == 7777
        assert "customer-test_user-cc-tr" == result["username"]
        assert result["password"] == "test_pass"

    def test_build_proxy_url_with_session(self) -> None:
        """Verify Oxylabs proxy URL with session ID."""
        result = OxylabsConfig.build_proxy_url(
            username="test_user",
            password="test_pass",
            country="de",
            session_id="sess_456",
        )

        assert "-sessid-sess_456" in result["username"]


# =============================================================================
# Smartproxy Configuration Tests
# =============================================================================


class TestSmartproxyConfig:
    """Tests for Smartproxy provider configuration."""

    def test_endpoint_constant(self) -> None:
        """Verify Smartproxy endpoint constant."""
        assert SmartproxyConfig.RESIDENTIAL_ENDPOINT == "gate.smartproxy.com:7000"

    def test_build_proxy_url_basic(self) -> None:
        """Verify basic Smartproxy proxy URL building."""
        result = SmartproxyConfig.build_proxy_url(
            username="test_user",
            password="test_pass",
            country="tr",
        )

        assert result["host"] == "gate.smartproxy.com"
        assert result["port"] == 7000
        assert "user-test_user-country-tr" == result["username"]

    def test_build_proxy_url_with_session(self) -> None:
        """Verify Smartproxy proxy URL with session ID."""
        result = SmartproxyConfig.build_proxy_url(
            username="test_user",
            password="test_pass",
            session_id="sess_789",
        )

        assert "-session-sess_789" in result["username"]


# =============================================================================
# ProxyHealth Tests
# =============================================================================


class TestProxyHealth:
    """Tests for ProxyHealth data class."""

    def test_default_initialization(self) -> None:
        """Verify ProxyHealth default values."""
        health = ProxyHealth(
            proxy_id="test_proxy",
            provider="brightdata",
            proxy_type=ProxyType.RESIDENTIAL,
            country="tr",
        )

        assert health.proxy_id == "test_proxy"
        assert health.provider == "brightdata"
        assert health.proxy_type == ProxyType.RESIDENTIAL
        assert health.country == "tr"
        assert health.status == ProxyStatus.ACTIVE
        assert health.health_score == 100
        assert health.total_requests == 0
        assert health.successful_requests == 0
        assert health.failed_requests == 0
        assert health.consecutive_failures == 0

    def test_custom_initialization(self) -> None:
        """Verify ProxyHealth with custom values."""
        health = ProxyHealth(
            proxy_id="test_proxy",
            provider="oxylabs",
            proxy_type=ProxyType.MOBILE,
            country="de",
            status=ProxyStatus.SLOW,
            health_score=75,
            total_requests=100,
        )

        assert health.status == ProxyStatus.SLOW
        assert health.health_score == 75
        assert health.total_requests == 100


# =============================================================================
# ProxyPoolManager Tests
# =============================================================================


class TestProxyPoolManager:
    """Tests for ProxyPoolManager core functionality."""

    @pytest.fixture
    def pool_manager(self) -> ProxyPoolManager:
        """Create a ProxyPoolManager instance."""
        return ProxyPoolManager()

    @pytest.fixture
    def test_configs(self) -> dict:
        """Create test provider configurations."""
        return {
            "brightdata": {
                "customer_id": "test_customer",
                "zone": "test_zone",
                "password": "test_pass",
                "countries": ["tr", "de"],
                "proxy_types": ["residential"],
                "pool_size": 5,
            },
            "oxylabs": {
                "username": "test_user",
                "password": "test_pass",
                "countries": ["tr"],
                "proxy_types": ["residential"],
                "pool_size": 3,
            },
        }

    @pytest.mark.asyncio
    async def test_initialize_creates_proxies(
        self,
        pool_manager: ProxyPoolManager,
        test_configs: dict,
    ) -> None:
        """Verify initialize creates proxy entries."""
        await pool_manager.initialize(test_configs)

        assert len(pool_manager.proxies) > 0
        assert pool_manager.provider_configs == test_configs

    @pytest.mark.asyncio
    async def test_get_proxy_returns_valid_proxy(
        self,
        pool_manager: ProxyPoolManager,
        test_configs: dict,
    ) -> None:
        """Verify get_proxy returns valid proxy details."""
        await pool_manager.initialize(test_configs)

        proxy = await pool_manager.get_proxy(
            target_site="vfs",
            country="tr",
        )

        assert "proxy_id" in proxy
        assert "protocol" in proxy
        assert "host" in proxy
        assert "port" in proxy

    @pytest.mark.asyncio
    async def test_get_proxy_filters_by_country(
        self,
        pool_manager: ProxyPoolManager,
        test_configs: dict,
    ) -> None:
        """Verify get_proxy filters by country correctly."""
        await pool_manager.initialize(test_configs)

        # Both should work
        proxy_tr = await pool_manager.get_proxy(
            target_site="vfs",
            country="tr",
        )
        assert proxy_tr is not None

    @pytest.mark.asyncio
    async def test_get_proxy_raises_when_exhausted(
        self,
        pool_manager: ProxyPoolManager,
    ) -> None:
        """Verify ProxyPoolExhaustedError when no proxies available."""
        # No initialization means no proxies
        with pytest.raises(ProxyPoolExhaustedError):
            await pool_manager.get_proxy(
                target_site="vfs",
                country="nonexistent",
            )

    @pytest.mark.asyncio
    async def test_report_success_updates_metrics(
        self,
        pool_manager: ProxyPoolManager,
        test_configs: dict,
    ) -> None:
        """Verify report_success updates proxy metrics."""
        await pool_manager.initialize(test_configs)

        proxy = await pool_manager.get_proxy(
            target_site="vfs",
            country="tr",
        )
        proxy_id = proxy["proxy_id"]

        await pool_manager.report_success(proxy_id, response_time_ms=250.0)

        health = pool_manager.proxies[proxy_id]
        assert health.total_requests == 1
        assert health.successful_requests == 1
        assert health.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_report_failure_updates_metrics(
        self,
        pool_manager: ProxyPoolManager,
        test_configs: dict,
    ) -> None:
        """Verify report_failure updates proxy metrics."""
        await pool_manager.initialize(test_configs)

        proxy = await pool_manager.get_proxy(
            target_site="vfs",
            country="tr",
        )
        proxy_id = proxy["proxy_id"]

        await pool_manager.report_failure(
            proxy_id, error_type="timeout", is_blocked=False
        )

        health = pool_manager.proxies[proxy_id]
        assert health.total_requests == 1
        assert health.failed_requests == 1
        assert health.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_report_failure_blocked_applies_penalty(
        self,
        pool_manager: ProxyPoolManager,
        test_configs: dict,
    ) -> None:
        """Verify blocked status applies larger health penalty."""
        await pool_manager.initialize(test_configs)

        proxy = await pool_manager.get_proxy(
            target_site="vfs",
            country="tr",
        )
        proxy_id = proxy["proxy_id"]
        initial_health = pool_manager.proxies[proxy_id].health_score

        await pool_manager.report_failure(
            proxy_id, error_type="blocked", is_blocked=True
        )

        health = pool_manager.proxies[proxy_id]
        # Blocked applies 30 point penalty vs 10 for non-blocked
        assert health.health_score <= initial_health - 25

    @pytest.mark.asyncio
    async def test_consecutive_failures_trigger_cooldown(
        self,
        pool_manager: ProxyPoolManager,
        test_configs: dict,
    ) -> None:
        """Verify consecutive failures trigger cooldown status."""
        await pool_manager.initialize(test_configs)

        proxy = await pool_manager.get_proxy(
            target_site="vfs",
            country="tr",
        )
        proxy_id = proxy["proxy_id"]

        # Simulate multiple failures
        for _ in range(pool_manager.CONSECUTIVE_FAILURE_COOLDOWN):
            await pool_manager.report_failure(proxy_id, error_type="error")

        health = pool_manager.proxies[proxy_id]
        assert health.status == ProxyStatus.COOLDOWN
        assert health.cooldown_until is not None


# =============================================================================
# Rotation Strategy Tests
# =============================================================================


class TestRotatingProxyStrategy:
    """Tests for RotatingProxyStrategy."""

    @pytest.mark.asyncio
    async def test_get_proxy_uses_non_sticky(self) -> None:
        """Verify rotating strategy uses non-sticky sessions."""
        pool = AsyncMock(spec=ProxyPoolManager)
        pool.get_proxy = AsyncMock(
            return_value={
                "proxy_id": "test",
                "host": "proxy.example.com",
            }
        )

        strategy = RotatingProxyStrategy(pool)
        await strategy.get_proxy(target_site="vfs", country="tr")

        pool.get_proxy.assert_called_once()
        call_kwargs = pool.get_proxy.call_args.kwargs
        assert call_kwargs.get("sticky_session") is False


class TestStickySessionStrategy:
    """Tests for StickySessionStrategy."""

    @pytest.mark.asyncio
    async def test_get_proxy_creates_session(self) -> None:
        """Verify sticky strategy creates and stores sessions."""
        pool = AsyncMock(spec=ProxyPoolManager)
        pool.get_proxy = AsyncMock(
            return_value={
                "proxy_id": "test",
                "session_id": "sess_123",
            }
        )

        strategy = StickySessionStrategy(pool)
        proxy = await strategy.get_proxy(
            session_key="session_1",
            target_site="vfs",
            country="tr",
        )

        assert "session_1" in strategy.active_sessions
        assert proxy["proxy_id"] == "test"

    @pytest.mark.asyncio
    async def test_get_proxy_reuses_session(self) -> None:
        """Verify sticky strategy reuses existing sessions."""
        pool = AsyncMock(spec=ProxyPoolManager)
        pool.get_proxy = AsyncMock(
            return_value={
                "proxy_id": "test",
                "session_id": "sess_123",
            }
        )

        strategy = StickySessionStrategy(pool)

        # First call
        await strategy.get_proxy(
            session_key="session_1",
            target_site="vfs",
            country="tr",
        )

        # Second call with same session key
        await strategy.get_proxy(
            session_key="session_1",
            target_site="vfs",
            country="tr",
        )

        # Should only call pool once
        assert pool.get_proxy.call_count == 1

    @pytest.mark.asyncio
    async def test_release_session_removes_entry(self) -> None:
        """Verify release_session removes session entry."""
        pool = AsyncMock(spec=ProxyPoolManager)
        pool.get_proxy = AsyncMock(return_value={"proxy_id": "test"})

        strategy = StickySessionStrategy(pool)
        await strategy.get_proxy(
            session_key="session_1",
            target_site="vfs",
            country="tr",
        )

        assert "session_1" in strategy.active_sessions

        await strategy.release_session("session_1")

        assert "session_1" not in strategy.active_sessions


class TestGeoRotationStrategy:
    """Tests for GeoRotationStrategy."""

    @pytest.mark.asyncio
    async def test_rotates_through_countries(self) -> None:
        """Verify geo strategy rotates through country sequence."""
        pool = AsyncMock(spec=ProxyPoolManager)
        pool.get_proxy = AsyncMock(return_value={"proxy_id": "test"})

        strategy = GeoRotationStrategy(pool)

        # Make multiple calls
        await strategy.get_proxy(target_site="vfs")
        await strategy.get_proxy(target_site="vfs")

        # Should have called with different countries
        calls = pool.get_proxy.call_args_list
        country1 = calls[0].kwargs["country"]
        country2 = calls[1].kwargs["country"]

        # Countries should be from the sequence
        assert country1 in GeoRotationStrategy.COUNTRY_SEQUENCE
        assert country2 in GeoRotationStrategy.COUNTRY_SEQUENCE


class TestASNDiversityStrategy:
    """Tests for ASNDiversityStrategy."""

    @pytest.mark.asyncio
    async def test_tracks_used_asns(self) -> None:
        """Verify ASN strategy tracks used ASNs."""
        pool = AsyncMock(spec=ProxyPoolManager)
        pool.get_proxy = AsyncMock(
            return_value={
                "proxy_id": "test",
                "asn": "AS12345",
            }
        )

        strategy = ASNDiversityStrategy(pool)
        await strategy.get_proxy(target_site="vfs", country="tr")

        assert "AS12345" in strategy.used_asns

    @pytest.mark.asyncio
    async def test_limits_asn_history(self) -> None:
        """Verify ASN history is limited."""
        pool = AsyncMock(spec=ProxyPoolManager)

        strategy = ASNDiversityStrategy(pool)
        strategy.max_history = 3

        # Add ASNs directly
        for i in range(5):
            pool.get_proxy = AsyncMock(
                return_value={"proxy_id": f"test_{i}", "asn": f"AS{i}"}
            )
            await strategy.get_proxy(target_site="vfs", country="tr")

        assert len(strategy.used_asns) <= strategy.max_history


# =============================================================================
# Site Proxy Config Tests
# =============================================================================


class TestSiteProxyConfig:
    """Tests for SiteProxyConfig."""

    def test_get_config_vfs(self) -> None:
        """Verify VFS site configuration."""
        config = SiteProxyConfig.get_config("vfs")

        assert config["proxy_type"] == ProxyType.RESIDENTIAL
        assert config["sticky_session"] is True
        assert config["min_health"] == 80

    def test_get_config_idata(self) -> None:
        """Verify iDATA site configuration."""
        config = SiteProxyConfig.get_config("idata")

        assert config["min_health"] == 60  # More tolerant
        assert ProxyType.DATACENTER in config["failover_types"]

    def test_get_config_unknown_returns_vfs_default(self) -> None:
        """Verify unknown site returns VFS config as default."""
        config = SiteProxyConfig.get_config("unknown")
        vfs_config = SiteProxyConfig.get_config("vfs")

        assert config == vfs_config


# =============================================================================
# Proxy Failover Chain Tests
# =============================================================================


class TestProxyFailoverChain:
    """Tests for ProxyFailoverChain."""

    @pytest.mark.asyncio
    async def test_uses_first_available_provider(self) -> None:
        """Verify failover chain uses first available provider."""
        pool = AsyncMock(spec=ProxyPoolManager)
        pool.get_proxy = AsyncMock(return_value={"proxy_id": "test"})

        chain = ProxyFailoverChain(pool)
        proxy = await chain.get_proxy_with_failover(
            target_site="vfs",
            country="tr",
        )

        assert proxy["proxy_id"] == "test"
        # Should have called with first provider
        call_kwargs = pool.get_proxy.call_args.kwargs
        assert call_kwargs.get("provider") == "brightdata"

    @pytest.mark.asyncio
    async def test_falls_back_on_exhaustion(self) -> None:
        """Verify failover to next provider on exhaustion."""
        pool = AsyncMock(spec=ProxyPoolManager)

        call_count = [0]

        async def mock_get_proxy(**kwargs):
            call_count[0] += 1
            if kwargs.get("provider") == "brightdata":
                raise ProxyPoolExhaustedError("No proxies")
            return {"proxy_id": "test", "provider": kwargs.get("provider")}

        pool.get_proxy = mock_get_proxy

        chain = ProxyFailoverChain(pool)
        proxy = await chain.get_proxy_with_failover(
            target_site="vfs",
            country="tr",
        )

        # Should have tried brightdata first, then oxylabs
        assert call_count[0] >= 2
        assert proxy["provider"] == "oxylabs"

    @pytest.mark.asyncio
    async def test_raises_when_all_exhausted(self) -> None:
        """Verify error when all providers exhausted."""
        pool = AsyncMock(spec=ProxyPoolManager)
        pool.get_proxy = AsyncMock(side_effect=ProxyPoolExhaustedError("No proxies"))

        chain = ProxyFailoverChain(pool)

        with pytest.raises(ProxyPoolExhaustedError):
            await chain.get_proxy_with_failover(target_site="vfs", country="tr")


# =============================================================================
# Proxy Cost Optimizer Tests
# =============================================================================


class TestProxyCostOptimizer:
    """Tests for ProxyCostOptimizer."""

    @pytest.fixture
    def optimizer(self) -> ProxyCostOptimizer:
        """Create a ProxyCostOptimizer instance."""
        return ProxyCostOptimizer(budget_limit=500.0)

    def test_track_usage_accumulates(self, optimizer: ProxyCostOptimizer) -> None:
        """Verify usage tracking accumulates correctly."""
        optimizer.track_usage("brightdata", "residential", 1024**3)  # 1 GB

        assert optimizer.usage.get("brightdata_residential", 0) > 0

    def test_get_estimated_cost_calculates(self, optimizer: ProxyCostOptimizer) -> None:
        """Verify estimated cost calculation."""
        # Track 1 GB of brightdata residential
        optimizer.track_usage("brightdata", "residential", 1024**3)

        cost = optimizer.get_estimated_cost()

        # BrightData residential is $12/GB
        assert cost == pytest.approx(12.0, rel=0.01)

    def test_should_downgrade_below_threshold(
        self, optimizer: ProxyCostOptimizer
    ) -> None:
        """Verify should_downgrade returns False below threshold."""
        assert optimizer.should_downgrade() is False

    def test_should_downgrade_above_threshold(
        self, optimizer: ProxyCostOptimizer
    ) -> None:
        """Verify should_downgrade returns True above 80% threshold."""
        # Track enough usage to exceed 80% of $500 budget
        # Need > $400 in cost, so > 33 GB at $12/GB
        optimizer.track_usage("brightdata", "residential", 35 * 1024**3)

        assert optimizer.should_downgrade() is True

    def test_get_cost_effective_proxy_type_normal(
        self, optimizer: ProxyCostOptimizer
    ) -> None:
        """Verify cost-effective type under budget returns residential."""
        proxy_type = optimizer.get_cost_effective_proxy_type("vfs")

        assert proxy_type == ProxyType.RESIDENTIAL

    def test_get_cost_effective_proxy_type_over_budget(
        self, optimizer: ProxyCostOptimizer
    ) -> None:
        """Verify cost-effective type over budget downgrades."""
        # Exceed budget
        optimizer.track_usage("brightdata", "residential", 40 * 1024**3)

        # iDATA can use datacenter
        proxy_type = optimizer.get_cost_effective_proxy_type("idata")
        assert proxy_type == ProxyType.DATACENTER

        # VFS needs at least ISP
        proxy_type = optimizer.get_cost_effective_proxy_type("vfs")
        assert proxy_type == ProxyType.ISP


# =============================================================================
# Proxy Health Monitor Tests
# =============================================================================


class TestProxyHealthMonitor:
    """Tests for ProxyHealthMonitor."""

    @pytest.fixture
    def pool_with_proxies(self) -> ProxyPoolManager:
        """Create pool manager with test proxies."""
        pool = ProxyPoolManager()

        # Add test proxies
        pool.proxies["proxy_1"] = ProxyHealth(
            proxy_id="proxy_1",
            provider="brightdata",
            proxy_type=ProxyType.RESIDENTIAL,
            country="tr",
            status=ProxyStatus.ACTIVE,
            health_score=90,
        )
        pool.proxies["proxy_2"] = ProxyHealth(
            proxy_id="proxy_2",
            provider="brightdata",
            proxy_type=ProxyType.RESIDENTIAL,
            country="tr",
            status=ProxyStatus.BLOCKED,
            health_score=30,
        )
        pool.proxies["proxy_3"] = ProxyHealth(
            proxy_id="proxy_3",
            provider="oxylabs",
            proxy_type=ProxyType.RESIDENTIAL,
            country="de",
            status=ProxyStatus.ACTIVE,
            health_score=80,
        )

        return pool

    @pytest.mark.asyncio
    async def test_get_pool_status_returns_counts(
        self, pool_with_proxies: ProxyPoolManager
    ) -> None:
        """Verify get_pool_status returns correct counts."""
        monitor = ProxyHealthMonitor(pool_with_proxies)
        status = await monitor.get_pool_status()

        assert status["total_proxies"] == 3
        assert status["status_breakdown"]["active"] == 2
        assert status["status_breakdown"]["blocked"] == 1

    @pytest.mark.asyncio
    async def test_get_pool_status_includes_provider_stats(
        self, pool_with_proxies: ProxyPoolManager
    ) -> None:
        """Verify get_pool_status includes provider breakdown."""
        monitor = ProxyHealthMonitor(pool_with_proxies)
        status = await monitor.get_pool_status()

        assert "brightdata" in status["by_provider"]
        assert "oxylabs" in status["by_provider"]
        assert status["by_provider"]["brightdata"]["total"] == 2

    @pytest.mark.asyncio
    async def test_get_pool_status_calculates_avg_health(
        self, pool_with_proxies: ProxyPoolManager
    ) -> None:
        """Verify average health calculation."""
        monitor = ProxyHealthMonitor(pool_with_proxies)
        status = await monitor.get_pool_status()

        # (90 + 30 + 80) / 3 = 66.67
        assert status["health_avg"] == pytest.approx(66.67, rel=0.01)

    @pytest.mark.asyncio
    async def test_get_pool_status_generates_alerts(self) -> None:
        """Verify alerts are generated for low active ratio."""
        pool = ProxyPoolManager()

        # Add mostly blocked proxies
        for i in range(10):
            pool.proxies[f"proxy_{i}"] = ProxyHealth(
                proxy_id=f"proxy_{i}",
                provider="brightdata",
                proxy_type=ProxyType.RESIDENTIAL,
                country="tr",
                status=ProxyStatus.BLOCKED if i < 8 else ProxyStatus.ACTIVE,
                health_score=50,
            )

        monitor = ProxyHealthMonitor(pool)
        status = await monitor.get_pool_status()

        assert len(status["alerts"]) > 0
        assert "CRITICAL" in status["alerts"][0]


# =============================================================================
# ProxyManager Integration Tests
# =============================================================================


class TestProxyManager:
    """Tests for high-level ProxyManager interface."""

    @pytest.fixture
    def test_configs(self) -> dict:
        """Create test provider configurations."""
        return {
            "brightdata": {
                "customer_id": "test_customer",
                "zone": "test_zone",
                "password": "test_pass",
                "countries": ["tr"],
                "proxy_types": ["residential"],
                "pool_size": 5,
            },
        }

    @pytest.mark.asyncio
    async def test_initialize_sets_up_components(self, test_configs: dict) -> None:
        """Verify initialize sets up all components."""
        manager = ProxyManager()
        await manager.initialize(test_configs)

        assert manager._initialized is True
        assert len(manager.pool.proxies) > 0

    @pytest.mark.asyncio
    async def test_get_proxy_returns_valid_proxy(self, test_configs: dict) -> None:
        """Verify get_proxy returns valid proxy details."""
        manager = ProxyManager()
        await manager.initialize(test_configs)

        proxy = await manager.get_proxy(
            target_site="vfs",
            country="tr",
        )

        assert "proxy_id" in proxy
        assert "host" in proxy

    @pytest.mark.asyncio
    async def test_report_success_updates_pool(self, test_configs: dict) -> None:
        """Verify report_success updates pool metrics."""
        manager = ProxyManager()
        await manager.initialize(test_configs)

        proxy = await manager.get_proxy(target_site="vfs", country="tr")
        await manager.report_success(proxy["proxy_id"], 100.0)

        health = manager.pool.proxies[proxy["proxy_id"]]
        assert health.successful_requests == 1

    @pytest.mark.asyncio
    async def test_report_failure_updates_pool(self, test_configs: dict) -> None:
        """Verify report_failure updates pool metrics."""
        manager = ProxyManager()
        await manager.initialize(test_configs)

        proxy = await manager.get_proxy(target_site="vfs", country="tr")
        await manager.report_failure(proxy["proxy_id"], "timeout")

        health = manager.pool.proxies[proxy["proxy_id"]]
        assert health.failed_requests == 1

    @pytest.mark.asyncio
    async def test_get_status_returns_comprehensive_status(
        self, test_configs: dict
    ) -> None:
        """Verify get_status returns comprehensive status."""
        manager = ProxyManager()
        await manager.initialize(test_configs)

        status = await manager.get_status()

        assert "total_proxies" in status
        assert "estimated_cost_usd" in status
        assert "should_downgrade" in status
        assert "active_sticky_sessions" in status


# =============================================================================
# ProxyStatus Enum Tests
# =============================================================================


class TestProxyStatusEnum:
    """Tests for ProxyStatus enum."""

    def test_all_statuses_defined(self) -> None:
        """Verify all expected statuses are defined."""
        expected = ["active", "slow", "blocked", "cooldown", "retired"]

        for status in expected:
            assert hasattr(ProxyStatus, status.upper())

    def test_status_values(self) -> None:
        """Verify status enum values."""
        assert ProxyStatus.ACTIVE.value == "active"
        assert ProxyStatus.BLOCKED.value == "blocked"


class TestProxyTypeEnum:
    """Tests for ProxyType enum."""

    def test_all_types_defined(self) -> None:
        """Verify all expected types are defined."""
        expected = ["residential", "mobile", "isp", "datacenter"]

        for proxy_type in expected:
            assert hasattr(ProxyType, proxy_type.upper())

    def test_type_values(self) -> None:
        """Verify type enum values."""
        assert ProxyType.RESIDENTIAL.value == "residential"
        assert ProxyType.MOBILE.value == "mobile"
