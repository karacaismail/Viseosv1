"""
Unit Tests for Account Pool Manager.

This module provides comprehensive tests for the account pool management system,
including account lifecycle, health scoring, ban detection, cooldown handling,
pool statistics, and account-proxy pairing.

Test Categories:
- AccountStatus/TargetSystem Enums: Enum value validation
- BotAccount: Data class initialization and defaults
- AccountPoolStats: Statistics data class
- SiteAccountConfig: Site-specific configurations
- AccountHealthCalculator: Health score calculations
- AccountPoolManager: Core pool operations
- AccountProxyPairing: Account-proxy combination tracking
- BanRecoveryScheduler: Automated recovery scheduling
- AccountPoolMonitor: Health reporting and alerts

Usage:
    pytest tests/unit/test_account_pool.py -v
    pytest tests/unit/test_account_pool.py -k "test_acquire" -v
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.services.account import (
    AccountHealthCalculator,
    AccountPoolManager,
    AccountPoolMonitor,
    AccountPoolStats,
    AccountProxyPairing,
    AccountStatus,
    BanRecoveryScheduler,
    BotAccount,
    SiteAccountConfig,
    TargetSystem,
)
from src.core.exceptions import AccountPoolExhaustedError


# =============================================================================
# Enum Tests
# =============================================================================


class TestAccountStatusEnum:
    """Tests for AccountStatus enum."""

    def test_all_statuses_defined(self) -> None:
        """Verify all expected statuses are defined."""
        expected = ["created", "active", "in_use", "cooldown", "banned", "retired"]

        for status in expected:
            assert hasattr(AccountStatus, status.upper())

    def test_status_values(self) -> None:
        """Verify status enum values match expected strings."""
        assert AccountStatus.CREATED.value == "created"
        assert AccountStatus.ACTIVE.value == "active"
        assert AccountStatus.IN_USE.value == "in_use"
        assert AccountStatus.COOLDOWN.value == "cooldown"
        assert AccountStatus.BANNED.value == "banned"
        assert AccountStatus.RETIRED.value == "retired"

    def test_status_is_string_enum(self) -> None:
        """Verify AccountStatus is a string enum."""
        assert isinstance(AccountStatus.ACTIVE.value, str)
        assert str(AccountStatus.ACTIVE) == "AccountStatus.ACTIVE"


class TestTargetSystemEnum:
    """Tests for TargetSystem enum."""

    def test_all_systems_defined(self) -> None:
        """Verify all expected systems are defined."""
        expected = ["vfs", "idata", "bls", "kkosmos"]

        for system in expected:
            assert hasattr(TargetSystem, system.upper())

    def test_system_values(self) -> None:
        """Verify system enum values match expected strings."""
        assert TargetSystem.VFS.value == "vfs"
        assert TargetSystem.IDATA.value == "idata"
        assert TargetSystem.BLS.value == "bls"
        assert TargetSystem.KKOSMOS.value == "kkosmos"


# =============================================================================
# BotAccount Tests
# =============================================================================


class TestBotAccount:
    """Tests for BotAccount data class."""

    def test_default_initialization(self) -> None:
        """Verify BotAccount creates with correct defaults."""
        account = BotAccount()

        assert account.id is not None
        assert len(account.id) > 0
        assert account.system == TargetSystem.VFS
        assert account.country == "de"
        assert account.email == ""
        assert account.password == ""
        assert account.status == AccountStatus.CREATED
        assert account.health_score == 100
        assert account.success_count == 0
        assert account.failure_count == 0
        assert account.consecutive_failures == 0
        assert account.daily_usage_count == 0
        assert account.daily_usage_limit == 50

    def test_custom_initialization(self) -> None:
        """Verify BotAccount accepts custom values."""
        account = BotAccount(
            id="custom_id",
            system=TargetSystem.IDATA,
            country="it",
            email="test@example.com",
            password="secret",
            status=AccountStatus.ACTIVE,
            health_score=85,
        )

        assert account.id == "custom_id"
        assert account.system == TargetSystem.IDATA
        assert account.country == "it"
        assert account.email == "test@example.com"
        assert account.password == "secret"
        assert account.status == AccountStatus.ACTIVE
        assert account.health_score == 85

    def test_timestamps_default_to_none(self) -> None:
        """Verify optional timestamps default to None."""
        account = BotAccount()

        assert account.last_used_at is None
        assert account.last_success_at is None
        assert account.cooldown_until is None
        assert account.banned_at is None

    def test_session_tracking_defaults(self) -> None:
        """Verify session tracking fields default to None."""
        account = BotAccount()

        assert account.current_session_id is None
        assert account.current_proxy_id is None

    def test_metadata_defaults(self) -> None:
        """Verify metadata fields have correct defaults."""
        account = BotAccount()

        assert account.notes == ""
        assert account.last_error is None
        assert account.ban_reason is None


# =============================================================================
# AccountPoolStats Tests
# =============================================================================


class TestAccountPoolStats:
    """Tests for AccountPoolStats data class."""

    def test_default_initialization(self) -> None:
        """Verify AccountPoolStats creates with zero defaults."""
        stats = AccountPoolStats()

        assert stats.total == 0
        assert stats.active == 0
        assert stats.in_use == 0
        assert stats.cooldown == 0
        assert stats.banned == 0
        assert stats.retired == 0
        assert stats.avg_health == 0.0

    def test_custom_values(self) -> None:
        """Verify AccountPoolStats accepts custom values."""
        stats = AccountPoolStats(
            total=100,
            active=80,
            in_use=10,
            cooldown=5,
            banned=3,
            retired=2,
            avg_health=85.5,
        )

        assert stats.total == 100
        assert stats.active == 80
        assert stats.avg_health == 85.5


# =============================================================================
# SiteAccountConfig Tests
# =============================================================================


class TestSiteAccountConfig:
    """Tests for SiteAccountConfig."""

    def test_get_config_vfs(self) -> None:
        """Verify VFS site configuration values."""
        config = SiteAccountConfig.get_config("vfs")

        assert "de" in config["countries"]
        assert config["daily_limit"] == 50
        assert config["cooldown_initial"] == 10
        assert config["ban_recovery_time"] == 120

    def test_get_config_idata(self) -> None:
        """Verify iDATA site configuration values."""
        config = SiteAccountConfig.get_config("idata")

        assert config["daily_limit"] == 100  # More tolerant
        assert config["cooldown_initial"] == 5

    def test_get_config_bls(self) -> None:
        """Verify BLS site configuration values."""
        config = SiteAccountConfig.get_config("bls")

        assert "es" in config["countries"]
        assert config["daily_limit"] == 30

    def test_get_config_kkosmos(self) -> None:
        """Verify KKOSMOS site configuration values."""
        config = SiteAccountConfig.get_config("kkosmos")

        assert "gr" in config["countries"]
        assert config["ban_recovery_time"] == 180  # 3 hours

    def test_get_config_unknown_returns_vfs(self) -> None:
        """Verify unknown system returns VFS config as default."""
        config = SiteAccountConfig.get_config("unknown")
        vfs_config = SiteAccountConfig.get_config("vfs")

        assert config == vfs_config


# =============================================================================
# AccountHealthCalculator Tests
# =============================================================================


class TestAccountHealthCalculator:
    """Tests for AccountHealthCalculator."""

    def test_calculate_perfect_health(self) -> None:
        """Verify perfect health for new account."""
        account = BotAccount(
            status=AccountStatus.ACTIVE,
            success_count=0,
            failure_count=0,
        )

        health = AccountHealthCalculator.calculate(account)

        assert health == 100

    def test_calculate_with_failures(self) -> None:
        """Verify health decreases with failure rate."""
        account = BotAccount(
            success_count=50,
            failure_count=50,  # 50% success rate
        )

        health = AccountHealthCalculator.calculate(account)

        # Should be less than 100 due to 50% success rate
        assert health < 100
        assert health > 0

    def test_calculate_consecutive_failure_penalty(self) -> None:
        """Verify consecutive failures apply penalty."""
        account = BotAccount(
            success_count=100,
            failure_count=10,
            consecutive_failures=3,
        )

        health = AccountHealthCalculator.calculate(account)

        # Should have penalty for consecutive failures
        assert health < 100

    def test_calculate_usage_factor(self) -> None:
        """Verify high usage applies penalty."""
        account = BotAccount(
            success_count=100,
            failure_count=0,
            daily_usage_count=45,  # 90% of limit
            daily_usage_limit=50,
        )

        health = AccountHealthCalculator.calculate(account)

        # Should have penalty for high usage
        assert health < 100

    def test_should_cooldown_low_health(self) -> None:
        """Verify should_cooldown returns True for low health."""
        account = BotAccount(
            success_count=10,
            failure_count=90,  # Very low success rate
        )

        # Force low health
        account.consecutive_failures = 5

        result = AccountHealthCalculator.should_cooldown(account)

        assert result is True

    def test_should_cooldown_consecutive_failures(self) -> None:
        """Verify should_cooldown returns True for many failures."""
        account = BotAccount(consecutive_failures=3)

        result = AccountHealthCalculator.should_cooldown(account)

        assert result is True

    def test_should_cooldown_healthy_account(self) -> None:
        """Verify should_cooldown returns False for healthy account."""
        account = BotAccount(
            success_count=100,
            failure_count=0,
            consecutive_failures=0,
        )

        result = AccountHealthCalculator.should_cooldown(account)

        assert result is False

    def test_should_retire_very_low_health(self) -> None:
        """Verify should_retire returns True for very low health."""
        account = BotAccount(
            success_count=5,
            failure_count=95,
            consecutive_failures=10,
        )

        result = AccountHealthCalculator.should_retire(account)

        assert result is True

    def test_should_retire_many_consecutive_failures(self) -> None:
        """Verify should_retire returns True for 10+ consecutive failures."""
        account = BotAccount(consecutive_failures=10)

        result = AccountHealthCalculator.should_retire(account)

        assert result is True

    def test_should_retire_healthy_account(self) -> None:
        """Verify should_retire returns False for healthy account."""
        account = BotAccount(
            success_count=100,
            failure_count=5,
            consecutive_failures=0,
        )

        result = AccountHealthCalculator.should_retire(account)

        assert result is False


# =============================================================================
# AccountPoolManager Tests
# =============================================================================


class TestAccountPoolManager:
    """Tests for AccountPoolManager core functionality."""

    @pytest.fixture
    def pool_manager(self) -> AccountPoolManager:
        """Create an AccountPoolManager instance."""
        return AccountPoolManager()

    @pytest.fixture
    def sample_db_accounts(self) -> list[dict]:
        """Create sample database account records."""
        return [
            {
                "id": "acc_1",
                "system": "vfs",
                "country": "de",
                "email": "bot1@example.com",
                "password": "pass1",
                "status": "active",
                "health_score": 100,
            },
            {
                "id": "acc_2",
                "system": "vfs",
                "country": "de",
                "email": "bot2@example.com",
                "password": "pass2",
                "status": "active",
                "health_score": 90,
            },
            {
                "id": "acc_3",
                "system": "idata",
                "country": "it",
                "email": "bot3@example.com",
                "password": "pass3",
                "status": "active",
                "health_score": 85,
            },
        ]

    @pytest.mark.asyncio
    async def test_load_from_database(
        self,
        pool_manager: AccountPoolManager,
        sample_db_accounts: list[dict],
    ) -> None:
        """Verify load_from_database populates accounts."""
        await pool_manager.load_from_database(sample_db_accounts)

        assert len(pool_manager.accounts) == 3
        assert "acc_1" in pool_manager.accounts
        assert "vfs_de" in pool_manager.pools
        assert "idata_it" in pool_manager.pools

    @pytest.mark.asyncio
    async def test_add_account(self, pool_manager: AccountPoolManager) -> None:
        """Verify add_account adds to pool."""
        account = BotAccount(
            id="new_account",
            system=TargetSystem.VFS,
            country="fr",
            email="new@example.com",
        )

        await pool_manager.add_account(account)

        assert "new_account" in pool_manager.accounts
        assert "vfs_fr" in pool_manager.pools

    @pytest.mark.asyncio
    async def test_acquire_returns_account(
        self,
        pool_manager: AccountPoolManager,
        sample_db_accounts: list[dict],
    ) -> None:
        """Verify acquire returns an available account."""
        await pool_manager.load_from_database(sample_db_accounts)

        account = await pool_manager.acquire(
            system=TargetSystem.VFS,
            country="de",
            session_id="session_123",
        )

        assert account is not None
        assert account.status == AccountStatus.IN_USE
        assert account.current_session_id == "session_123"

    @pytest.mark.asyncio
    async def test_acquire_updates_last_used(
        self,
        pool_manager: AccountPoolManager,
        sample_db_accounts: list[dict],
    ) -> None:
        """Verify acquire updates last_used_at timestamp."""
        await pool_manager.load_from_database(sample_db_accounts)

        before = datetime.utcnow()
        account = await pool_manager.acquire(
            system=TargetSystem.VFS,
            country="de",
            session_id="session_123",
        )
        after = datetime.utcnow()

        assert account.last_used_at is not None
        assert before <= account.last_used_at <= after

    @pytest.mark.asyncio
    async def test_acquire_excludes_specified_ids(
        self,
        pool_manager: AccountPoolManager,
        sample_db_accounts: list[dict],
    ) -> None:
        """Verify acquire excludes specified account IDs."""
        await pool_manager.load_from_database(sample_db_accounts)

        # Exclude both VFS accounts except one
        account = await pool_manager.acquire(
            system=TargetSystem.VFS,
            country="de",
            session_id="session_123",
            exclude_ids=["acc_1"],
        )

        assert account.id == "acc_2"

    @pytest.mark.asyncio
    async def test_acquire_raises_when_pool_empty(
        self, pool_manager: AccountPoolManager
    ) -> None:
        """Verify AccountPoolExhaustedError when no accounts available."""
        with pytest.raises(AccountPoolExhaustedError):
            await pool_manager.acquire(
                system=TargetSystem.VFS,
                country="de",
                session_id="session_123",
            )

    @pytest.mark.asyncio
    async def test_acquire_skips_cooldown_accounts(
        self,
        pool_manager: AccountPoolManager,
        sample_db_accounts: list[dict],
    ) -> None:
        """Verify acquire skips accounts in cooldown."""
        await pool_manager.load_from_database(sample_db_accounts)

        # Put first account in cooldown
        pool_manager.accounts["acc_1"].status = AccountStatus.COOLDOWN
        pool_manager.accounts["acc_1"].cooldown_until = datetime.utcnow() + timedelta(
            hours=1
        )

        account = await pool_manager.acquire(
            system=TargetSystem.VFS,
            country="de",
            session_id="session_123",
        )

        assert account.id == "acc_2"

    @pytest.mark.asyncio
    async def test_acquire_skips_daily_limit_exceeded(
        self,
        pool_manager: AccountPoolManager,
        sample_db_accounts: list[dict],
    ) -> None:
        """Verify acquire skips accounts that exceeded daily limit."""
        await pool_manager.load_from_database(sample_db_accounts)

        # Set first account at daily limit
        pool_manager.accounts["acc_1"].daily_usage_count = 50
        pool_manager.accounts["acc_1"].daily_usage_limit = 50

        account = await pool_manager.acquire(
            system=TargetSystem.VFS,
            country="de",
            session_id="session_123",
        )

        assert account.id == "acc_2"

    @pytest.mark.asyncio
    async def test_release_success_updates_metrics(
        self,
        pool_manager: AccountPoolManager,
        sample_db_accounts: list[dict],
    ) -> None:
        """Verify release with success updates metrics correctly."""
        await pool_manager.load_from_database(sample_db_accounts)

        account = await pool_manager.acquire(
            system=TargetSystem.VFS,
            country="de",
            session_id="session_123",
        )
        account_id = account.id

        await pool_manager.release(account_id, success=True)

        updated = pool_manager.accounts[account_id]
        assert updated.status == AccountStatus.ACTIVE
        assert updated.success_count == 1
        assert updated.consecutive_failures == 0
        assert updated.daily_usage_count == 1
        assert updated.current_session_id is None

    @pytest.mark.asyncio
    async def test_release_failure_updates_metrics(
        self,
        pool_manager: AccountPoolManager,
        sample_db_accounts: list[dict],
    ) -> None:
        """Verify release with failure updates metrics correctly."""
        await pool_manager.load_from_database(sample_db_accounts)

        account = await pool_manager.acquire(
            system=TargetSystem.VFS,
            country="de",
            session_id="session_123",
        )
        account_id = account.id

        await pool_manager.release(
            account_id, success=False, error_message="Connection timeout"
        )

        updated = pool_manager.accounts[account_id]
        assert updated.failure_count == 1
        assert updated.consecutive_failures == 1
        assert updated.last_error == "Connection timeout"

    @pytest.mark.asyncio
    async def test_release_detects_ban(
        self,
        pool_manager: AccountPoolManager,
        sample_db_accounts: list[dict],
    ) -> None:
        """Verify release detects ban indicators."""
        await pool_manager.load_from_database(sample_db_accounts)

        account = await pool_manager.acquire(
            system=TargetSystem.VFS,
            country="de",
            session_id="session_123",
        )
        account_id = account.id

        await pool_manager.release(
            account_id, success=False, error_message="Your account has been suspended"
        )

        updated = pool_manager.accounts[account_id]
        assert updated.status == AccountStatus.BANNED
        assert updated.banned_at is not None

    @pytest.mark.asyncio
    async def test_release_triggers_cooldown(
        self,
        pool_manager: AccountPoolManager,
        sample_db_accounts: list[dict],
    ) -> None:
        """Verify release triggers cooldown on rate limit error."""
        await pool_manager.load_from_database(sample_db_accounts)

        account = await pool_manager.acquire(
            system=TargetSystem.VFS,
            country="de",
            session_id="session_123",
        )
        account_id = account.id

        await pool_manager.release(
            account_id, success=False, error_message="Rate limit exceeded"
        )

        updated = pool_manager.accounts[account_id]
        assert updated.status == AccountStatus.COOLDOWN
        assert updated.cooldown_until is not None

    @pytest.mark.asyncio
    async def test_check_recovery_cooldown_expired(
        self,
        pool_manager: AccountPoolManager,
        sample_db_accounts: list[dict],
    ) -> None:
        """Verify check_recovery recovers expired cooldown."""
        await pool_manager.load_from_database(sample_db_accounts)

        # Set account in cooldown with expired time
        pool_manager.accounts["acc_1"].status = AccountStatus.COOLDOWN
        pool_manager.accounts["acc_1"].cooldown_until = datetime.utcnow() - timedelta(
            minutes=1
        )

        recovered = await pool_manager.check_recovery("acc_1")

        assert recovered is True
        assert pool_manager.accounts["acc_1"].status == AccountStatus.ACTIVE
        assert pool_manager.accounts["acc_1"].cooldown_until is None

    @pytest.mark.asyncio
    async def test_check_recovery_cooldown_not_expired(
        self,
        pool_manager: AccountPoolManager,
        sample_db_accounts: list[dict],
    ) -> None:
        """Verify check_recovery does not recover active cooldown."""
        await pool_manager.load_from_database(sample_db_accounts)

        # Set account in cooldown with future time
        pool_manager.accounts["acc_1"].status = AccountStatus.COOLDOWN
        pool_manager.accounts["acc_1"].cooldown_until = datetime.utcnow() + timedelta(
            hours=1
        )

        recovered = await pool_manager.check_recovery("acc_1")

        assert recovered is False
        assert pool_manager.accounts["acc_1"].status == AccountStatus.COOLDOWN

    @pytest.mark.asyncio
    async def test_get_pool_stats(
        self,
        pool_manager: AccountPoolManager,
        sample_db_accounts: list[dict],
    ) -> None:
        """Verify get_pool_stats returns correct statistics."""
        await pool_manager.load_from_database(sample_db_accounts)

        stats = await pool_manager.get_pool_stats()

        assert "vfs_de" in stats
        assert stats["vfs_de"].total == 2
        assert stats["vfs_de"].active == 2

        assert "idata_it" in stats
        assert stats["idata_it"].total == 1

    @pytest.mark.asyncio
    async def test_reset_daily_limits(
        self,
        pool_manager: AccountPoolManager,
        sample_db_accounts: list[dict],
    ) -> None:
        """Verify reset_daily_limits resets all usage counts."""
        await pool_manager.load_from_database(sample_db_accounts)

        # Set some usage
        pool_manager.accounts["acc_1"].daily_usage_count = 25
        pool_manager.accounts["acc_2"].daily_usage_count = 30

        await pool_manager.reset_daily_limits()

        assert pool_manager.accounts["acc_1"].daily_usage_count == 0
        assert pool_manager.accounts["acc_2"].daily_usage_count == 0

    @pytest.mark.asyncio
    async def test_get_account(
        self,
        pool_manager: AccountPoolManager,
        sample_db_accounts: list[dict],
    ) -> None:
        """Verify get_account returns account by ID."""
        await pool_manager.load_from_database(sample_db_accounts)

        account = await pool_manager.get_account("acc_1")

        assert account is not None
        assert account.id == "acc_1"

    @pytest.mark.asyncio
    async def test_get_account_not_found(
        self, pool_manager: AccountPoolManager
    ) -> None:
        """Verify get_account returns None for unknown ID."""
        account = await pool_manager.get_account("nonexistent")

        assert account is None


# =============================================================================
# AccountProxyPairing Tests
# =============================================================================


class TestAccountProxyPairing:
    """Tests for AccountProxyPairing."""

    @pytest.fixture
    def account_pool(self) -> AccountPoolManager:
        """Create account pool with test data."""
        pool = AccountPoolManager()
        pool.accounts["acc_1"] = BotAccount(
            id="acc_1",
            system=TargetSystem.VFS,
            country="de",
            status=AccountStatus.ACTIVE,
        )
        pool.pools["vfs_de"] = ["acc_1"]
        return pool

    @pytest.fixture
    def mock_proxy_pool(self) -> AsyncMock:
        """Create mock proxy pool."""
        mock = AsyncMock()
        mock.get_proxy = AsyncMock(
            return_value={"proxy_id": "proxy_1", "host": "proxy.example.com"}
        )
        mock.get_proxy_by_id = AsyncMock(return_value={"status": "active"})
        return mock

    @pytest.mark.asyncio
    async def test_get_paired_resources_returns_account_and_proxy(
        self,
        account_pool: AccountPoolManager,
        mock_proxy_pool: AsyncMock,
    ) -> None:
        """Verify get_paired_resources returns both account and proxy."""
        pairing = AccountProxyPairing(account_pool, mock_proxy_pool)

        account, proxy = await pairing.get_paired_resources(
            system=TargetSystem.VFS,
            country="de",
            session_id="session_123",
        )

        assert account is not None
        assert account.id == "acc_1"
        assert proxy is not None
        assert "proxy_id" in proxy

    @pytest.mark.asyncio
    async def test_record_success_stores_pairing(
        self,
        account_pool: AccountPoolManager,
        mock_proxy_pool: AsyncMock,
    ) -> None:
        """Verify record_success stores successful pairing."""
        pairing = AccountProxyPairing(account_pool, mock_proxy_pool)

        await pairing.record_success("acc_1", "proxy_1")

        assert "acc_1" in pairing.successful_pairs
        assert "proxy_1" in pairing.successful_pairs["acc_1"]

    @pytest.mark.asyncio
    async def test_record_success_limits_history(
        self,
        account_pool: AccountPoolManager,
        mock_proxy_pool: AsyncMock,
    ) -> None:
        """Verify record_success limits pairing history to 5."""
        pairing = AccountProxyPairing(account_pool, mock_proxy_pool)

        # Record more than 5 pairings
        for i in range(7):
            await pairing.record_success("acc_1", f"proxy_{i}")

        assert len(pairing.successful_pairs["acc_1"]) == 5
        # Should keep most recent
        assert "proxy_6" in pairing.successful_pairs["acc_1"]
        assert "proxy_0" not in pairing.successful_pairs["acc_1"]


# =============================================================================
# BanRecoveryScheduler Tests
# =============================================================================


class TestBanRecoveryScheduler:
    """Tests for BanRecoveryScheduler."""

    @pytest.fixture
    def pool_with_banned(self) -> AccountPoolManager:
        """Create pool with banned account."""
        pool = AccountPoolManager()
        pool.accounts["acc_1"] = BotAccount(
            id="acc_1",
            status=AccountStatus.BANNED,
            cooldown_until=datetime.utcnow() - timedelta(minutes=1),  # Expired
        )
        pool.accounts["acc_2"] = BotAccount(
            id="acc_2",
            status=AccountStatus.COOLDOWN,
            cooldown_until=datetime.utcnow() - timedelta(minutes=1),  # Expired
        )
        return pool

    @pytest.mark.asyncio
    async def test_start_stop(self, pool_with_banned: AccountPoolManager) -> None:
        """Verify scheduler starts and stops cleanly."""
        scheduler = BanRecoveryScheduler(pool_with_banned)

        await scheduler.start()
        assert scheduler.running is True

        await scheduler.stop()
        assert scheduler.running is False

    @pytest.mark.asyncio
    async def test_check_recoveries_processes_cooldown(
        self, pool_with_banned: AccountPoolManager
    ) -> None:
        """Verify scheduler recovers expired cooldown accounts."""
        scheduler = BanRecoveryScheduler(pool_with_banned)

        await scheduler._check_recoveries()

        # Cooldown account should be recovered
        assert pool_with_banned.accounts["acc_2"].status == AccountStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_check_recoveries_processes_ban(
        self, pool_with_banned: AccountPoolManager
    ) -> None:
        """Verify scheduler recovers expired ban accounts."""
        scheduler = BanRecoveryScheduler(pool_with_banned)

        await scheduler._check_recoveries()

        # Banned account with expired cooldown should be recovered
        acc = pool_with_banned.accounts["acc_1"]
        assert acc.status == AccountStatus.ACTIVE
        assert acc.health_score == 50  # Reduced health after ban recovery


# =============================================================================
# AccountPoolMonitor Tests
# =============================================================================


class TestAccountPoolMonitor:
    """Tests for AccountPoolMonitor."""

    @pytest.fixture
    def pool_with_accounts(self) -> AccountPoolManager:
        """Create pool with test accounts."""
        pool = AccountPoolManager()
        pool.accounts["acc_1"] = BotAccount(
            id="acc_1",
            system=TargetSystem.VFS,
            country="de",
            status=AccountStatus.ACTIVE,
            health_score=90,
        )
        pool.accounts["acc_2"] = BotAccount(
            id="acc_2",
            system=TargetSystem.VFS,
            country="de",
            status=AccountStatus.BANNED,
            health_score=20,
        )
        pool.accounts["acc_3"] = BotAccount(
            id="acc_3",
            system=TargetSystem.VFS,
            country="de",
            status=AccountStatus.ACTIVE,
            health_score=80,
        )
        pool.pools["vfs_de"] = ["acc_1", "acc_2", "acc_3"]
        return pool

    @pytest.mark.asyncio
    async def test_get_health_report_includes_stats(
        self, pool_with_accounts: AccountPoolManager
    ) -> None:
        """Verify get_health_report includes pool statistics."""
        monitor = AccountPoolMonitor(pool_with_accounts)

        report = await monitor.get_health_report()

        assert "stats" in report
        assert "vfs_de" in report["stats"]
        assert report["stats"]["vfs_de"]["total"] == 3
        assert report["stats"]["vfs_de"]["active"] == 2
        assert report["stats"]["vfs_de"]["banned"] == 1

    @pytest.mark.asyncio
    async def test_get_health_report_includes_alerts(
        self, pool_with_accounts: AccountPoolManager
    ) -> None:
        """Verify get_health_report includes alerts."""
        monitor = AccountPoolMonitor(pool_with_accounts)

        report = await monitor.get_health_report()

        assert "alerts" in report

    @pytest.mark.asyncio
    async def test_get_health_report_generates_ban_alert(self) -> None:
        """Verify alert is generated when many accounts banned."""
        pool = AccountPoolManager()

        # Add mostly banned accounts (>20%)
        for i in range(10):
            pool.accounts[f"acc_{i}"] = BotAccount(
                id=f"acc_{i}",
                system=TargetSystem.VFS,
                country="de",
                status=AccountStatus.BANNED if i < 3 else AccountStatus.ACTIVE,
                health_score=50,
            )
        pool.pools["vfs_de"] = [f"acc_{i}" for i in range(10)]

        monitor = AccountPoolMonitor(pool)
        report = await monitor.get_health_report()

        ban_alerts = [a for a in report["alerts"] if "banned" in a["message"]]
        assert len(ban_alerts) > 0

    @pytest.mark.asyncio
    async def test_get_account_details(
        self, pool_with_accounts: AccountPoolManager
    ) -> None:
        """Verify get_account_details returns account information."""
        monitor = AccountPoolMonitor(pool_with_accounts)

        details = await monitor.get_account_details("acc_1")

        assert details is not None
        assert details["id"] == "acc_1"
        assert details["status"] == "active"
        assert details["health_score"] == 90

    @pytest.mark.asyncio
    async def test_get_account_details_not_found(
        self, pool_with_accounts: AccountPoolManager
    ) -> None:
        """Verify get_account_details returns None for unknown ID."""
        monitor = AccountPoolMonitor(pool_with_accounts)

        details = await monitor.get_account_details("nonexistent")

        assert details is None


# =============================================================================
# Ban Detection Pattern Tests
# =============================================================================


class TestBanDetectionPatterns:
    """Tests for ban detection in AccountPoolManager."""

    @pytest.fixture
    def pool_manager(self) -> AccountPoolManager:
        """Create an AccountPoolManager instance."""
        return AccountPoolManager()

    def test_is_ban_error_detects_suspension(
        self, pool_manager: AccountPoolManager
    ) -> None:
        """Verify suspension messages are detected as ban."""
        result = pool_manager._is_ban_error("Your account has been suspended")
        assert result is True

    def test_is_ban_error_detects_lock(
        self, pool_manager: AccountPoolManager
    ) -> None:
        """Verify lock messages are detected as ban."""
        result = pool_manager._is_ban_error("Account is locked for security reasons")
        assert result is True

    def test_is_ban_error_detects_blocked(
        self, pool_manager: AccountPoolManager
    ) -> None:
        """Verify blocked messages are detected as ban."""
        result = pool_manager._is_ban_error("Access denied due to unusual activity")
        assert result is True

    def test_is_ban_error_returns_false_for_normal(
        self, pool_manager: AccountPoolManager
    ) -> None:
        """Verify normal errors are not detected as ban."""
        result = pool_manager._is_ban_error("Connection timeout")
        assert result is False

    def test_is_ban_error_handles_none(
        self, pool_manager: AccountPoolManager
    ) -> None:
        """Verify None error message returns False."""
        result = pool_manager._is_ban_error(None)
        assert result is False

    def test_is_cooldown_error_detects_rate_limit(
        self, pool_manager: AccountPoolManager
    ) -> None:
        """Verify rate limit messages trigger cooldown."""
        result = pool_manager._is_cooldown_error("Rate limit exceeded")
        assert result is True

    def test_is_cooldown_error_detects_try_again(
        self, pool_manager: AccountPoolManager
    ) -> None:
        """Verify try again messages trigger cooldown."""
        result = pool_manager._is_cooldown_error("Please try again later")
        assert result is True

    def test_is_cooldown_error_returns_false_for_normal(
        self, pool_manager: AccountPoolManager
    ) -> None:
        """Verify normal errors don't trigger cooldown."""
        result = pool_manager._is_cooldown_error("Connection timeout")
        assert result is False


# =============================================================================
# Weighted Selection Tests
# =============================================================================


class TestWeightedSelection:
    """Tests for weighted account selection."""

    @pytest.mark.asyncio
    async def test_prefers_higher_health(self) -> None:
        """Verify accounts with higher health are preferred."""
        pool = AccountPoolManager()

        # Create accounts with different health scores
        accounts = [
            BotAccount(id=f"acc_{i}", health_score=score, status=AccountStatus.ACTIVE)
            for i, score in enumerate([100, 50, 30])
        ]

        # Run multiple selections and check distribution
        selections: dict[str, int] = {"acc_0": 0, "acc_1": 0, "acc_2": 0}

        for _ in range(100):
            selected = pool._weighted_select(accounts)
            selections[selected.id] += 1

        # Higher health should be selected more often
        assert selections["acc_0"] > selections["acc_2"]

    @pytest.mark.asyncio
    async def test_prefers_less_recent(self) -> None:
        """Verify less recently used accounts are preferred."""
        pool = AccountPoolManager()

        now = datetime.utcnow()

        # Create accounts with different last_used times
        accounts = [
            BotAccount(
                id="acc_0",
                health_score=80,
                status=AccountStatus.ACTIVE,
                last_used_at=now - timedelta(hours=1),  # Older
            ),
            BotAccount(
                id="acc_1",
                health_score=80,
                status=AccountStatus.ACTIVE,
                last_used_at=now - timedelta(minutes=1),  # Recent
            ),
        ]

        # Run multiple selections
        selections: dict[str, int] = {"acc_0": 0, "acc_1": 0}

        for _ in range(100):
            selected = pool._weighted_select(accounts)
            selections[selected.id] += 1

        # Older (less recently used) should be selected more often
        assert selections["acc_0"] > selections["acc_1"]
