"""
VISE OS Account Pool Manager.

Bot account lifecycle management for VFS Global, iDATA, BLS Spain,
and KKOSMOS booking systems. Provides health scoring, ban detection/recovery,
intelligent rotation, and credential management for 200+ concurrent sessions.

Features:
- Multi-system support: VFS, iDATA, BLS, KKOSMOS
- Account health scoring with automatic cooldown/retirement
- Ban detection and recovery with exponential backoff
- Daily usage limit enforcement
- Weighted random selection based on health
- Account-proxy pairing for improved success rates
- Real-time health monitoring with alerts

Usage:
    from src.bot.services.account import AccountPoolManager

    manager = AccountPoolManager()
    await manager.load_from_database(db_accounts)

    # Acquire account for session
    account = await manager.acquire(
        system=TargetSystem.VFS,
        country="de",
        session_id="session_123",
    )

    # Release with status
    await manager.release(account.id, success=True)
"""

from __future__ import annotations

import asyncio
import random
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import structlog

from src.core.exceptions import AccountPoolExhaustedError

logger = structlog.get_logger(__name__)


# =============================================================================
# Enums
# =============================================================================


class AccountStatus(str, Enum):
    """Bot account status."""

    CREATED = "created"
    ACTIVE = "active"
    IN_USE = "in_use"
    COOLDOWN = "cooldown"
    BANNED = "banned"
    RETIRED = "retired"


class TargetSystem(str, Enum):
    """Target booking system."""

    VFS = "vfs"
    IDATA = "idata"
    BLS = "bls"
    KKOSMOS = "kkosmos"


# =============================================================================
# Site-Specific Configuration
# =============================================================================


class SiteAccountConfig:
    """
    Site-specific account configuration.

    Different sites have different rate limiting behaviors
    and account requirements.
    """

    SITE_CONFIGS: dict[str, dict[str, Any]] = {
        "vfs": {
            "countries": ["de", "fr", "nl", "no", "se"],
            "accounts_per_country": 20,
            "daily_limit": 50,
            "cooldown_initial": 10,  # minutes
            "ban_recovery_time": 120,  # minutes
            "notes": "Each country requires separate account. Cloudflare check after login.",
        },
        "idata": {
            "countries": ["de", "it"],
            "accounts_per_country": 15,
            "daily_limit": 100,  # More tolerant
            "cooldown_initial": 5,
            "ban_recovery_time": 30,
            "notes": "Less aggressive rate limiting. jQuery API.",
        },
        "bls": {
            "countries": ["es"],
            "accounts_per_country": 10,
            "daily_limit": 30,
            "cooldown_initial": 15,
            "ban_recovery_time": 60,
            "notes": "Keyboard-only input. Paste disabled.",
        },
        "kkosmos": {
            "countries": ["gr"],
            "accounts_per_country": 10,
            "daily_limit": 20,
            "cooldown_initial": 20,
            "ban_recovery_time": 180,  # 3 hours
            "notes": "SMS verification required. Phone number pool needed.",
        },
    }

    @classmethod
    def get_config(cls, system: str) -> dict[str, Any]:
        """
        Get account configuration for a system.

        Args:
            system: System code (vfs, idata, bls, kkosmos).

        Returns:
            Site-specific account configuration.
        """
        return cls.SITE_CONFIGS.get(system, cls.SITE_CONFIGS["vfs"])


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class BotAccount:
    """
    Bot account entity.

    Tracks account credentials, status, health metrics,
    and usage statistics for intelligent selection.

    Attributes:
        id: Unique account identifier.
        system: Target booking system.
        country: Target country code.
        email: Account email (encrypted in storage).
        password: Account password (encrypted in storage).
        status: Current account status.
        health_score: Health score from 0-100.
        success_count: Total successful operations.
        failure_count: Total failed operations.
        consecutive_failures: Count of consecutive failures.
        created_at: Account creation timestamp.
        last_used_at: Last usage timestamp.
        last_success_at: Last successful operation timestamp.
        cooldown_until: Cooldown end timestamp.
        banned_at: Ban detection timestamp.
        current_session_id: ID of current session using account.
        current_proxy_id: ID of proxy paired with account.
        daily_usage_count: Operations today.
        daily_usage_limit: Maximum daily operations.
        notes: Additional notes.
        last_error: Last error message.
        ban_reason: Reason for ban if applicable.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Target system
    system: TargetSystem = TargetSystem.VFS
    country: str = "de"

    # Credentials (encrypted in storage)
    email: str = ""
    password: str = ""

    # Status
    status: AccountStatus = AccountStatus.CREATED

    # Health metrics
    health_score: int = 100
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0

    # Timestamps
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_used_at: datetime | None = None
    last_success_at: datetime | None = None
    cooldown_until: datetime | None = None
    banned_at: datetime | None = None

    # Session tracking
    current_session_id: str | None = None
    current_proxy_id: str | None = None

    # Usage limits
    daily_usage_count: int = 0
    daily_usage_limit: int = 50

    # Metadata
    notes: str = ""
    last_error: str | None = None
    ban_reason: str | None = None


@dataclass
class AccountPoolStats:
    """
    Account pool statistics.

    Aggregates status counts and health metrics for monitoring.

    Attributes:
        total: Total accounts in pool.
        active: Active accounts available.
        in_use: Accounts currently in use.
        cooldown: Accounts in cooldown.
        banned: Banned accounts.
        retired: Retired accounts.
        avg_health: Average health score.
    """

    total: int = 0
    active: int = 0
    in_use: int = 0
    cooldown: int = 0
    banned: int = 0
    retired: int = 0
    avg_health: float = 0.0


# =============================================================================
# Account Health Calculator
# =============================================================================


class AccountHealthCalculator:
    """
    Calculate account health scores.

    Uses success rate, consecutive failures, recency,
    and usage factors to compute health.
    """

    @staticmethod
    def calculate(account: BotAccount) -> int:
        """
        Calculate health score (0-100).

        Args:
            account: BotAccount to evaluate.

        Returns:
            Health score from 0 to 100.
        """
        score = 100.0

        # Success rate factor (40%)
        total_ops = account.success_count + account.failure_count
        if total_ops > 0:
            success_rate = account.success_count / total_ops
            score -= (1 - success_rate) * 40

        # Consecutive failures penalty (30%)
        failure_penalty = min(account.consecutive_failures * 10, 30)
        score -= failure_penalty

        # Recency factor (15%)
        if account.last_success_at:
            hours_since_success = (
                datetime.utcnow() - account.last_success_at
            ).total_seconds() / 3600
            if hours_since_success > 24:
                score -= min((hours_since_success - 24) * 0.5, 15)

        # Usage factor (15%)
        if account.daily_usage_limit > 0:
            usage_ratio = account.daily_usage_count / account.daily_usage_limit
            if usage_ratio > 0.8:
                score -= (usage_ratio - 0.8) * 75  # 80%+ usage penalty

        return int(max(0, min(100, score)))

    @staticmethod
    def should_cooldown(account: BotAccount) -> bool:
        """
        Check if account should enter cooldown.

        Args:
            account: BotAccount to check.

        Returns:
            True if cooldown is warranted.
        """
        health = AccountHealthCalculator.calculate(account)
        return health < 50 or account.consecutive_failures >= 3

    @staticmethod
    def should_retire(account: BotAccount) -> bool:
        """
        Check if account should be retired.

        Args:
            account: BotAccount to check.

        Returns:
            True if retirement is warranted.
        """
        health = AccountHealthCalculator.calculate(account)
        return health < 20 or account.consecutive_failures >= 10


# =============================================================================
# Account Pool Manager
# =============================================================================


class AccountPoolManager:
    """
    Bot account pool management with lifecycle tracking.

    Manages account selection, health scoring, cooldown,
    ban detection/recovery, and automatic retirement.

    Attributes:
        accounts: Dictionary of BotAccount objects by ID.
        pools: System-country specific account lists.
    """

    # Cooldown configuration
    COOLDOWN_INITIAL_MINUTES = 10
    COOLDOWN_MAX_MINUTES = 120
    COOLDOWN_MULTIPLIER = 2

    # Health thresholds
    HEALTH_THRESHOLD_WARNING = 70
    HEALTH_THRESHOLD_COOLDOWN = 50
    HEALTH_THRESHOLD_RETIRE = 20

    # Ban detection patterns
    BAN_INDICATORS = [
        "account has been suspended",
        "account is locked",
        "too many attempts",
        "temporarily blocked",
        "access denied",
        "unusual activity",
        "security reasons",
    ]

    # Cooldown indicators (temporary, recovery possible)
    COOLDOWN_INDICATORS = [
        "try again later",
        "rate limit",
        "too many requests",
        "please wait",
        r"exceeded.*attempts",
    ]

    def __init__(self) -> None:
        """Initialize account pool manager."""
        self.accounts: dict[str, BotAccount] = {}
        self._lock = asyncio.Lock()

        # System-country specific pools
        self.pools: dict[str, list[str]] = {}  # "vfs_de" -> [account_ids]

    async def load_from_database(self, db_accounts: list[dict[str, Any]]) -> None:
        """
        Load accounts from database records.

        Args:
            db_accounts: List of account dictionaries from database.
        """
        async with self._lock:
            for data in db_accounts:
                account = BotAccount(
                    id=data["id"],
                    system=TargetSystem(data["system"]),
                    country=data["country"],
                    email=data["email"],  # Decrypted
                    password=data["password"],  # Decrypted
                    status=AccountStatus(data.get("status", "active")),
                    health_score=data.get("health_score", 100),
                    success_count=data.get("success_count", 0),
                    failure_count=data.get("failure_count", 0),
                    consecutive_failures=data.get("consecutive_failures", 0),
                    daily_usage_limit=data.get(
                        "daily_usage_limit",
                        SiteAccountConfig.get_config(data["system"])["daily_limit"],
                    ),
                )

                self.accounts[account.id] = account

                # Pool indexing
                pool_key = f"{account.system.value}_{account.country}"
                if pool_key not in self.pools:
                    self.pools[pool_key] = []
                self.pools[pool_key].append(account.id)

            logger.info(
                "accounts_loaded",
                total=len(self.accounts),
                pools=list(self.pools.keys()),
            )

    async def add_account(self, account: BotAccount) -> None:
        """
        Add a new account to the pool.

        Args:
            account: BotAccount to add.
        """
        async with self._lock:
            self.accounts[account.id] = account

            pool_key = f"{account.system.value}_{account.country}"
            if pool_key not in self.pools:
                self.pools[pool_key] = []

            if account.id not in self.pools[pool_key]:
                self.pools[pool_key].append(account.id)

            logger.debug(
                "account_added",
                account_id=account.id,
                pool=pool_key,
            )

    async def acquire(
        self,
        system: TargetSystem,
        country: str,
        session_id: str,
        exclude_ids: list[str] | None = None,
    ) -> BotAccount:
        """
        Acquire an account from the pool.

        Uses weighted random selection based on health score
        and idle time to select the best available account.

        Args:
            system: Target booking system.
            country: Target country code.
            session_id: ID of the session acquiring the account.
            exclude_ids: Account IDs to exclude from selection.

        Returns:
            Selected BotAccount marked as in use.

        Raises:
            AccountPoolExhaustedError: If no eligible accounts available.
        """
        async with self._lock:
            pool_key = f"{system.value}_{country}"

            if pool_key not in self.pools:
                raise AccountPoolExhaustedError(
                    f"No account pool for {pool_key}",
                    site=system.value,
                )

            # Filter eligible accounts
            eligible: list[BotAccount] = []
            now = datetime.utcnow()

            for account_id in self.pools[pool_key]:
                account = self.accounts[account_id]

                # Status check
                if account.status != AccountStatus.ACTIVE:
                    continue

                # Cooldown check
                if account.cooldown_until and account.cooldown_until > now:
                    continue

                # Daily limit check
                if account.daily_usage_count >= account.daily_usage_limit:
                    continue

                # Exclude list
                if exclude_ids and account.id in exclude_ids:
                    continue

                # Health check
                if account.health_score < self.HEALTH_THRESHOLD_COOLDOWN:
                    continue

                eligible.append(account)

            if not eligible:
                raise AccountPoolExhaustedError(
                    f"No eligible accounts for {pool_key}",
                    site=system.value,
                )

            # Weighted selection based on health
            account = self._weighted_select(eligible)

            # Mark as in use
            account.status = AccountStatus.IN_USE
            account.current_session_id = session_id
            account.last_used_at = now

            logger.debug(
                "account_acquired",
                account_id=account.id,
                session_id=session_id,
                health_score=account.health_score,
                pool=pool_key,
            )

            return account

    def _weighted_select(self, accounts: list[BotAccount]) -> BotAccount:
        """
        Select account using weighted random based on health.

        Accounts with higher health scores and longer idle times
        have higher probability of selection.

        Args:
            accounts: List of eligible accounts.

        Returns:
            Selected BotAccount.
        """
        weights: list[float] = []
        now = datetime.utcnow()

        for account in accounts:
            weight = float(account.health_score)

            # Recency bonus: less recently used accounts preferred
            if account.last_used_at:
                idle_minutes = (now - account.last_used_at).total_seconds() / 60
                weight += min(idle_minutes, 30)  # Max 30 bonus
            else:
                weight += 30  # Never used, give bonus

            # Success streak bonus
            if account.consecutive_failures == 0:
                weight += 10

            weights.append(max(weight, 1))

        return random.choices(accounts, weights=weights, k=1)[0]

    async def release(
        self,
        account_id: str,
        success: bool,
        error_message: str | None = None,
    ) -> None:
        """
        Release an account back to the pool.

        Updates health metrics based on operation success/failure.

        Args:
            account_id: ID of the account to release.
            success: Whether the operation was successful.
            error_message: Error message if operation failed.
        """
        async with self._lock:
            if account_id not in self.accounts:
                return

            account = self.accounts[account_id]
            account.current_session_id = None

            if success:
                await self._handle_success(account)
            else:
                await self._handle_failure(account, error_message)

            logger.debug(
                "account_released",
                account_id=account_id,
                success=success,
                new_status=account.status.value,
                health_score=account.health_score,
            )

    async def _handle_success(self, account: BotAccount) -> None:
        """
        Process successful operation.

        Args:
            account: Account that completed successfully.
        """
        account.success_count += 1
        account.consecutive_failures = 0
        account.last_success_at = datetime.utcnow()
        account.daily_usage_count += 1
        account.status = AccountStatus.ACTIVE

        # Health boost
        account.health_score = min(100, account.health_score + 5)

        # Clear cooldown
        account.cooldown_until = None

    async def _handle_failure(
        self,
        account: BotAccount,
        error_message: str | None,
    ) -> None:
        """
        Process failed operation.

        Args:
            account: Account that failed.
            error_message: Error message from failure.
        """
        account.failure_count += 1
        account.consecutive_failures += 1
        account.last_error = error_message

        # Classify error
        is_ban = self._is_ban_error(error_message)
        is_cooldown = self._is_cooldown_error(error_message)

        if is_ban:
            await self._handle_ban(account, error_message)
        elif is_cooldown or account.consecutive_failures >= 3:
            await self._handle_cooldown(account)
        else:
            # Minor failure, just reduce health
            account.health_score = max(0, account.health_score - 10)
            account.status = AccountStatus.ACTIVE

        # Check for retirement
        if account.health_score < self.HEALTH_THRESHOLD_RETIRE:
            account.status = AccountStatus.RETIRED
            logger.warning(
                "account_retired",
                account_id=account.id,
                health_score=account.health_score,
                consecutive_failures=account.consecutive_failures,
            )

    def _is_ban_error(self, error_message: str | None) -> bool:
        """
        Check if error indicates account ban.

        Args:
            error_message: Error message to check.

        Returns:
            True if error indicates ban.
        """
        if not error_message:
            return False

        error_lower = error_message.lower()
        return any(indicator in error_lower for indicator in self.BAN_INDICATORS)

    def _is_cooldown_error(self, error_message: str | None) -> bool:
        """
        Check if error indicates cooldown needed.

        Args:
            error_message: Error message to check.

        Returns:
            True if error indicates cooldown needed.
        """
        if not error_message:
            return False

        error_lower = error_message.lower()
        return any(
            re.search(indicator, error_lower) for indicator in self.COOLDOWN_INDICATORS
        )

    async def _handle_ban(
        self,
        account: BotAccount,
        reason: str | None,
    ) -> None:
        """
        Process account ban.

        Args:
            account: Banned account.
            reason: Ban reason if available.
        """
        account.status = AccountStatus.BANNED
        account.banned_at = datetime.utcnow()
        account.ban_reason = reason
        account.health_score = max(0, account.health_score - 50)

        # VFS temporary ban check (10-minute ban is recoverable)
        if account.system == TargetSystem.VFS and "10 minute" in (reason or "").lower():
            # Temporary ban, try recovery in 15 minutes
            account.cooldown_until = datetime.utcnow() + timedelta(minutes=15)

        logger.warning(
            "account_banned",
            account_id=account.id,
            system=account.system.value,
            reason=reason,
        )

    async def _handle_cooldown(self, account: BotAccount) -> None:
        """
        Put account into cooldown.

        Uses exponential backoff based on consecutive failures.

        Args:
            account: Account to cooldown.
        """
        account.status = AccountStatus.COOLDOWN
        account.health_score = max(0, account.health_score - 20)

        # Exponential backoff
        cooldown_minutes = min(
            self.COOLDOWN_INITIAL_MINUTES
            * (self.COOLDOWN_MULTIPLIER**account.consecutive_failures),
            self.COOLDOWN_MAX_MINUTES,
        )

        account.cooldown_until = datetime.utcnow() + timedelta(minutes=cooldown_minutes)

        logger.info(
            "account_cooldown",
            account_id=account.id,
            cooldown_minutes=cooldown_minutes,
            consecutive_failures=account.consecutive_failures,
        )

    async def check_recovery(self, account_id: str) -> bool:
        """
        Check and attempt account recovery.

        Args:
            account_id: ID of account to check.

        Returns:
            True if account was recovered.
        """
        async with self._lock:
            if account_id not in self.accounts:
                return False

            account = self.accounts[account_id]
            now = datetime.utcnow()

            # Cooldown expired?
            if account.status == AccountStatus.COOLDOWN:
                if account.cooldown_until and account.cooldown_until <= now:
                    account.status = AccountStatus.ACTIVE
                    account.cooldown_until = None
                    logger.info("account_recovered", account_id=account_id)
                    return True

            # Banned but might be temporary
            if account.status == AccountStatus.BANNED:
                if account.cooldown_until and account.cooldown_until <= now:
                    # Attempt recovery
                    account.status = AccountStatus.ACTIVE
                    account.cooldown_until = None
                    account.consecutive_failures = 0  # Reset
                    account.health_score = 50  # Start with lower health
                    logger.info(
                        "account_ban_recovered",
                        account_id=account_id,
                    )
                    return True

            return False

    async def get_pool_stats(self) -> dict[str, AccountPoolStats]:
        """
        Get statistics for all pools.

        Returns:
            Dictionary mapping pool keys to AccountPoolStats.
        """
        stats: dict[str, AccountPoolStats] = {}

        for pool_key, account_ids in self.pools.items():
            pool_stats = AccountPoolStats()
            pool_stats.total = len(account_ids)

            health_sum = 0

            for account_id in account_ids:
                account = self.accounts[account_id]
                health_sum += account.health_score

                if account.status == AccountStatus.ACTIVE:
                    pool_stats.active += 1
                elif account.status == AccountStatus.IN_USE:
                    pool_stats.in_use += 1
                elif account.status == AccountStatus.COOLDOWN:
                    pool_stats.cooldown += 1
                elif account.status == AccountStatus.BANNED:
                    pool_stats.banned += 1
                elif account.status == AccountStatus.RETIRED:
                    pool_stats.retired += 1

            pool_stats.avg_health = (
                health_sum / pool_stats.total if pool_stats.total > 0 else 0
            )
            stats[pool_key] = pool_stats

        return stats

    async def reset_daily_limits(self) -> None:
        """
        Reset daily usage limits.

        Should be called at midnight to reset daily counters.
        """
        async with self._lock:
            for account in self.accounts.values():
                account.daily_usage_count = 0

            logger.info(
                "daily_limits_reset",
                total_accounts=len(self.accounts),
            )

    async def get_account(self, account_id: str) -> BotAccount | None:
        """
        Get account by ID.

        Args:
            account_id: Account ID to retrieve.

        Returns:
            BotAccount if found, None otherwise.
        """
        return self.accounts.get(account_id)


# =============================================================================
# Account-Proxy Pairing
# =============================================================================


class AccountProxyPairing:
    """
    Account-proxy pairing strategy.

    Tracks successful account-proxy combinations for
    improved success rates.

    Attributes:
        account_pool: Account pool manager.
        proxy_pool: Proxy pool manager.
        successful_pairs: History of successful pairings.
    """

    def __init__(
        self,
        account_pool: AccountPoolManager,
        proxy_pool: Any,  # ProxyPoolManager
    ) -> None:
        """
        Initialize pairing strategy.

        Args:
            account_pool: AccountPoolManager instance.
            proxy_pool: ProxyPoolManager instance.
        """
        self.account_pool = account_pool
        self.proxy_pool = proxy_pool

        # Pairing memory (account_id -> [proxy_ids])
        self.successful_pairs: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    async def get_paired_resources(
        self,
        system: TargetSystem,
        country: str,
        session_id: str,
    ) -> tuple[BotAccount, dict[str, Any]]:
        """
        Get paired account and proxy.

        Attempts to use previously successful combinations.

        Args:
            system: Target booking system.
            country: Target country code.
            session_id: Session identifier.

        Returns:
            Tuple of (BotAccount, proxy_dict).

        Raises:
            AccountPoolExhaustedError: If no accounts available.
        """
        # Get account first
        account = await self.account_pool.acquire(
            system=system,
            country=country,
            session_id=session_id,
        )

        try:
            # Try to get a previously successful proxy for this account
            preferred_proxy = await self._get_preferred_proxy(account.id)

            if preferred_proxy:
                proxy = preferred_proxy
            else:
                # Get new proxy
                proxy = await self.proxy_pool.get_proxy(
                    target_site=system.value,
                    country=country,
                    sticky_session=True,
                )

            # Link proxy to account
            account.current_proxy_id = proxy.get("proxy_id")

            return account, proxy

        except Exception as e:
            # Release account if proxy acquisition fails
            await self.account_pool.release(
                account.id, success=False, error_message=str(e)
            )
            raise

    async def _get_preferred_proxy(self, account_id: str) -> dict[str, Any] | None:
        """
        Get preferred proxy for account.

        Args:
            account_id: Account ID to check.

        Returns:
            Proxy dict if available, None otherwise.
        """
        async with self._lock:
            if account_id not in self.successful_pairs:
                return None

            successful_proxies = self.successful_pairs[account_id]

            if not successful_proxies:
                return None

            # Last successful proxy preferred
            last_proxy_id = successful_proxies[-1]

            try:
                # Check if proxy is still active
                proxy = await self.proxy_pool.get_proxy_by_id(last_proxy_id)
                if proxy and proxy.get("status") == "active":
                    return proxy
            except Exception:
                pass

            return None

    async def record_success(self, account_id: str, proxy_id: str) -> None:
        """
        Record successful pairing.

        Args:
            account_id: Account ID.
            proxy_id: Proxy ID.
        """
        async with self._lock:
            if account_id not in self.successful_pairs:
                self.successful_pairs[account_id] = []

            # Keep last 5 successful proxies
            self.successful_pairs[account_id].append(proxy_id)
            if len(self.successful_pairs[account_id]) > 5:
                self.successful_pairs[account_id].pop(0)

            logger.debug(
                "pairing_success_recorded",
                account_id=account_id,
                proxy_id=proxy_id,
            )


# =============================================================================
# Ban Recovery Scheduler
# =============================================================================


class BanRecoveryScheduler:
    """
    Automated ban recovery scheduler.

    Periodically checks accounts in cooldown or banned status
    and attempts recovery when cooldown period expires.

    Attributes:
        pool: Account pool manager.
        running: Whether scheduler is running.
    """

    def __init__(self, pool_manager: AccountPoolManager) -> None:
        """
        Initialize recovery scheduler.

        Args:
            pool_manager: AccountPoolManager instance.
        """
        self.pool = pool_manager
        self.running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start recovery scheduler."""
        self.running = True
        self._task = asyncio.create_task(self._run())
        logger.info("ban_recovery_scheduler_started")

    async def stop(self) -> None:
        """Stop recovery scheduler."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ban_recovery_scheduler_stopped")

    async def _run(self) -> None:
        """Main scheduler loop."""
        while self.running:
            await self._check_recoveries()
            await asyncio.sleep(60)  # Check every minute

    async def _check_recoveries(self) -> None:
        """Check and process account recoveries."""
        now = datetime.utcnow()

        for account in self.pool.accounts.values():
            # Skip active accounts
            if account.status in [AccountStatus.ACTIVE, AccountStatus.IN_USE]:
                continue

            # Check cooldown expiry
            if account.status == AccountStatus.COOLDOWN:
                if account.cooldown_until and account.cooldown_until <= now:
                    account.status = AccountStatus.ACTIVE
                    account.cooldown_until = None
                    logger.info(
                        "account_auto_recovered",
                        account_id=account.id,
                        previous_status="cooldown",
                    )

            # Check temporary ban expiry
            if account.status == AccountStatus.BANNED:
                if account.cooldown_until and account.cooldown_until <= now:
                    # Attempt recovery with lower health
                    account.status = AccountStatus.ACTIVE
                    account.cooldown_until = None
                    account.health_score = 50
                    logger.info(
                        "account_ban_auto_recovered",
                        account_id=account.id,
                    )


# =============================================================================
# Account Pool Monitor
# =============================================================================


class AccountPoolMonitor:
    """
    Account pool health monitoring.

    Provides health reports and generates alerts
    for pool status issues.

    Attributes:
        pool: Account pool manager.
    """

    def __init__(self, pool_manager: AccountPoolManager) -> None:
        """
        Initialize pool monitor.

        Args:
            pool_manager: AccountPoolManager instance.
        """
        self.pool = pool_manager

    async def get_health_report(self) -> dict[str, Any]:
        """
        Generate pool health report.

        Returns:
            Dictionary with pool statistics and alerts.
        """
        stats = await self.pool.get_pool_stats()
        alerts: list[dict[str, Any]] = []

        for pool_key, pool_stats in stats.items():
            # Active account ratio check
            if pool_stats.total > 0:
                active_ratio = (pool_stats.active + pool_stats.in_use) / pool_stats.total

                if active_ratio < 0.3:
                    alerts.append(
                        {
                            "level": "critical",
                            "pool": pool_key,
                            "message": f"Only {active_ratio * 100:.0f}% accounts active",
                        }
                    )
                elif active_ratio < 0.5:
                    alerts.append(
                        {
                            "level": "warning",
                            "pool": pool_key,
                            "message": f"Only {active_ratio * 100:.0f}% accounts active",
                        }
                    )

            # Banned account check
            if pool_stats.total > 0:
                banned_ratio = pool_stats.banned / pool_stats.total
                if banned_ratio > 0.2:
                    alerts.append(
                        {
                            "level": "warning",
                            "pool": pool_key,
                            "message": f"{banned_ratio * 100:.0f}% accounts banned",
                        }
                    )

            # Average health check
            if pool_stats.avg_health < 50:
                alerts.append(
                    {
                        "level": "warning",
                        "pool": pool_key,
                        "message": f"Average health score: {pool_stats.avg_health:.0f}",
                    }
                )

        return {
            "stats": {
                k: {
                    "total": v.total,
                    "active": v.active,
                    "in_use": v.in_use,
                    "cooldown": v.cooldown,
                    "banned": v.banned,
                    "retired": v.retired,
                    "avg_health": round(v.avg_health, 2),
                }
                for k, v in stats.items()
            },
            "alerts": alerts,
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def get_account_details(self, account_id: str) -> dict[str, Any] | None:
        """
        Get detailed account information.

        Args:
            account_id: Account ID to query.

        Returns:
            Account details dictionary or None if not found.
        """
        if account_id not in self.pool.accounts:
            return None

        account = self.pool.accounts[account_id]

        return {
            "id": account.id,
            "system": account.system.value,
            "country": account.country,
            "status": account.status.value,
            "health_score": account.health_score,
            "success_count": account.success_count,
            "failure_count": account.failure_count,
            "consecutive_failures": account.consecutive_failures,
            "daily_usage": f"{account.daily_usage_count}/{account.daily_usage_limit}",
            "last_used": (
                account.last_used_at.isoformat() if account.last_used_at else None
            ),
            "last_success": (
                account.last_success_at.isoformat() if account.last_success_at else None
            ),
            "cooldown_until": (
                account.cooldown_until.isoformat() if account.cooldown_until else None
            ),
            "last_error": account.last_error,
        }


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Main manager
    "AccountPoolManager",
    # Data classes
    "BotAccount",
    "AccountPoolStats",
    # Enums
    "AccountStatus",
    "TargetSystem",
    # Site config
    "SiteAccountConfig",
    # Health calculation
    "AccountHealthCalculator",
    # Pairing
    "AccountProxyPairing",
    # Recovery
    "BanRecoveryScheduler",
    # Monitoring
    "AccountPoolMonitor",
]
