"""
VISE OS Browser Session Management.

High-level session management for browser automation with:
- Session lifecycle management (create, acquire, release, cleanup)
- Health monitoring (memory usage, duration limits)
- Session rotation with configurable usage limits
- Cooldown handling after detection/failure
- Integration with StealthSessionLauncher and BrowserSessionPool

Usage:
    from src.bot.engine.session import SessionManager

    manager = SessionManager()
    await manager.initialize()

    async with manager.acquire_session(proxy=proxy_config) as session:
        await session.page.goto("https://example.com")

    # Or with context tracking
    session = await manager.acquire("booking_123")
    try:
        await session.page.goto("https://example.com")
        await manager.release(session, success=True)
    except Exception:
        await manager.release(session, success=False)  # Marks for cooldown
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, AsyncIterator

import structlog

from src.core.exceptions import BrowserError, SessionError

logger = structlog.get_logger(__name__)


# =============================================================================
# Constants
# =============================================================================

# Session limits from spec
DEFAULT_MAX_MEMORY_MB = 500
DEFAULT_MAX_SESSION_DURATION_SECONDS = 1800  # 30 minutes
DEFAULT_MAX_PROFILE_USES = 50
DEFAULT_COOLDOWN_SECONDS = 3600  # 1 hour after failure

# Pool defaults
DEFAULT_MAX_SESSIONS = 20
DEFAULT_INITIAL_SESSIONS = 5
DEFAULT_ACQUIRE_TIMEOUT = 30.0


# =============================================================================
# Enums
# =============================================================================


class SessionStatus(str, Enum):
    """Session lifecycle status."""

    IDLE = "idle"  # Available in pool
    ACTIVE = "active"  # In use by a task
    COOLDOWN = "cooldown"  # Temporarily unavailable (failed/detected)
    BURNED = "burned"  # Permanently unusable (banned)
    CLOSED = "closed"  # Session closed


class SessionHealthStatus(str, Enum):
    """Session health check result."""

    HEALTHY = "healthy"
    MEMORY_HIGH = "memory_high"
    DURATION_EXCEEDED = "duration_exceeded"
    USAGE_LIMIT_REACHED = "usage_limit_reached"
    UNHEALTHY = "unhealthy"


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class SessionMetrics:
    """
    Tracks session usage and health metrics.

    Attributes:
        session_id: Unique session identifier.
        profile_id: Associated browser profile ID.
        created_at: When the session was created.
        last_used_at: Last time session was used.
        use_count: Number of times session has been acquired.
        success_count: Number of successful releases.
        failure_count: Number of failed releases.
        total_duration_seconds: Total active time.
        memory_usage_mb: Current memory usage estimate.
        status: Current session status.
        cooldown_until: When cooldown period ends (if applicable).
        context_id: Current task context (e.g., booking_id).
    """

    session_id: str
    profile_id: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_used_at: datetime | None = None
    use_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_duration_seconds: float = 0.0
    memory_usage_mb: float = 0.0
    status: SessionStatus = SessionStatus.IDLE
    cooldown_until: datetime | None = None
    context_id: str | None = None


@dataclass
class SessionConfig:
    """
    Session management configuration.

    Attributes:
        max_memory_mb: Maximum memory per session before recycling.
        max_duration_seconds: Maximum session duration before recycling.
        max_profile_uses: Maximum uses per profile before rotation.
        cooldown_seconds: Cooldown duration after failure.
        max_sessions: Maximum concurrent sessions.
        initial_sessions: Number of sessions to pre-warm.
        acquire_timeout: Timeout for acquiring a session.
        health_check_interval: Interval between health checks.
        auto_recycle: Whether to automatically recycle unhealthy sessions.
    """

    max_memory_mb: float = DEFAULT_MAX_MEMORY_MB
    max_duration_seconds: float = DEFAULT_MAX_SESSION_DURATION_SECONDS
    max_profile_uses: int = DEFAULT_MAX_PROFILE_USES
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS
    max_sessions: int = DEFAULT_MAX_SESSIONS
    initial_sessions: int = DEFAULT_INITIAL_SESSIONS
    acquire_timeout: float = DEFAULT_ACQUIRE_TIMEOUT
    health_check_interval: float = 60.0
    auto_recycle: bool = True


# =============================================================================
# Session Wrapper
# =============================================================================


class ManagedSession:
    """
    Wrapper around StealthSessionLauncher with management metadata.

    Provides session lifecycle tracking, health monitoring, and
    integration with the SessionManager.

    Attributes:
        session: The underlying StealthSessionLauncher instance.
        metrics: Session usage and health metrics.
        manager: Reference to the managing SessionManager.
    """

    def __init__(
        self,
        session: Any,  # StealthSessionLauncher
        metrics: SessionMetrics,
        manager: SessionManager | None = None,
    ) -> None:
        """
        Initialize managed session.

        Args:
            session: StealthSessionLauncher instance.
            metrics: Session metrics tracker.
            manager: Optional reference to SessionManager.
        """
        self._session = session
        self.metrics = metrics
        self._manager = manager
        self._acquire_time: float | None = None

    @property
    def session(self) -> Any:
        """Get underlying session launcher."""
        return self._session

    @property
    def page(self) -> Any:
        """Get browser page for convenience."""
        return self._session.page if self._session else None

    @property
    def context(self) -> Any:
        """Get browser context for convenience."""
        return self._session.context if self._session else None

    @property
    def session_id(self) -> str:
        """Get session ID."""
        return self.metrics.session_id

    @property
    def profile_id(self) -> str:
        """Get profile ID."""
        return self.metrics.profile_id

    @property
    def is_healthy(self) -> bool:
        """Quick health check."""
        return self.check_health() == SessionHealthStatus.HEALTHY

    @property
    def mouse(self) -> Any:
        """Get human mouse simulator."""
        return self._session.mouse if self._session else None

    @property
    def typing(self) -> Any:
        """Get human typing simulator."""
        return self._session.typing if self._session else None

    @property
    def scroll(self) -> Any:
        """Get human scroll simulator."""
        return self._session.scroll if self._session else None

    def check_health(self, config: SessionConfig | None = None) -> SessionHealthStatus:
        """
        Check session health against configured limits.

        Args:
            config: Optional config for limit values.

        Returns:
            SessionHealthStatus indicating health state.
        """
        cfg = config or SessionConfig()

        # Check memory
        if self.metrics.memory_usage_mb > cfg.max_memory_mb:
            return SessionHealthStatus.MEMORY_HIGH

        # Check duration
        if self.metrics.total_duration_seconds > cfg.max_duration_seconds:
            return SessionHealthStatus.DURATION_EXCEEDED

        # Check usage count
        if self.metrics.use_count >= cfg.max_profile_uses:
            return SessionHealthStatus.USAGE_LIMIT_REACHED

        # Check if in cooldown
        if self.metrics.status == SessionStatus.COOLDOWN:
            if self.metrics.cooldown_until and datetime.utcnow() < self.metrics.cooldown_until:
                return SessionHealthStatus.UNHEALTHY

        # Check if burned
        if self.metrics.status == SessionStatus.BURNED:
            return SessionHealthStatus.UNHEALTHY

        return SessionHealthStatus.HEALTHY

    def mark_acquired(self, context_id: str | None = None) -> None:
        """
        Mark session as acquired for use.

        Args:
            context_id: Optional context identifier (e.g., booking_id).
        """
        self._acquire_time = time.time()
        self.metrics.status = SessionStatus.ACTIVE
        self.metrics.use_count += 1
        self.metrics.last_used_at = datetime.utcnow()
        self.metrics.context_id = context_id

        logger.debug(
            "session_acquired",
            session_id=self.session_id,
            use_count=self.metrics.use_count,
            context_id=context_id,
        )

    def mark_released(self, success: bool = True, cooldown_seconds: int | None = None) -> None:
        """
        Mark session as released after use.

        Args:
            success: Whether the session use was successful.
            cooldown_seconds: Cooldown duration if failed.
        """
        # Update duration
        if self._acquire_time:
            duration = time.time() - self._acquire_time
            self.metrics.total_duration_seconds += duration
            self._acquire_time = None

        # Update counts
        if success:
            self.metrics.success_count += 1
            self.metrics.status = SessionStatus.IDLE
        else:
            self.metrics.failure_count += 1
            if cooldown_seconds:
                self.metrics.status = SessionStatus.COOLDOWN
                self.metrics.cooldown_until = datetime.utcnow() + timedelta(
                    seconds=cooldown_seconds
                )
            else:
                self.metrics.status = SessionStatus.IDLE

        self.metrics.context_id = None

        logger.debug(
            "session_released",
            session_id=self.session_id,
            success=success,
            total_uses=self.metrics.use_count,
        )

    def mark_burned(self, reason: str | None = None) -> None:
        """
        Mark session as permanently unusable.

        Args:
            reason: Reason for burning the session.
        """
        self.metrics.status = SessionStatus.BURNED
        logger.warning(
            "session_burned",
            session_id=self.session_id,
            reason=reason,
            total_uses=self.metrics.use_count,
        )

    async def close(self) -> None:
        """Close the underlying session."""
        if self._session:
            try:
                await self._session.close()
            except Exception as e:
                logger.warning("session_close_error", session_id=self.session_id, error=str(e))
            finally:
                self.metrics.status = SessionStatus.CLOSED

    async def __aenter__(self) -> ManagedSession:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit with auto-release."""
        if self._manager:
            success = exc_type is None
            await self._manager.release(self, success=success)


# =============================================================================
# Session Manager
# =============================================================================


class SessionManager:
    """
    High-level browser session management.

    Provides a centralized interface for managing browser sessions with:
    - Automatic session pool management
    - Health monitoring and auto-recycling
    - Usage tracking and rotation
    - Cooldown handling after failures
    - Context-aware session acquisition

    Usage:
        manager = SessionManager()
        await manager.initialize()

        # Simple usage with context manager
        async with manager.acquire_session() as session:
            await session.page.goto("https://example.com")

        # Advanced usage with explicit release
        session = await manager.acquire("booking_123")
        try:
            # Use session
            await manager.release(session, success=True)
        except BotDetectedError:
            await manager.release(session, success=False)

        # Cleanup
        await manager.shutdown()
    """

    def __init__(
        self,
        config: SessionConfig | None = None,
        pool: Any | None = None,  # BrowserSessionPool
        profile_generator: Any | None = None,  # BrowserProfileGenerator
        stealth_engine: Any | None = None,  # StealthEngine for tier-aware session creation
    ) -> None:
        """
        Initialize session manager.

        Args:
            config: Session management configuration.
            pool: Optional pre-existing session pool.
            profile_generator: Optional profile generator.
            stealth_engine: Optional StealthEngine for tier-aware session creation.
        """
        self.config = config or SessionConfig()
        self._pool = pool
        self._profile_generator = profile_generator
        self._stealth_engine = stealth_engine
        self._sessions: dict[str, ManagedSession] = {}
        self._active_sessions: dict[str, ManagedSession] = {}
        self._available_queue: asyncio.Queue[ManagedSession] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._initialized = False
        self._health_check_task: asyncio.Task | None = None
        self._profiles: list[dict[str, Any]] = []
        self._proxies: list[dict[str, str]] = []

    async def initialize(
        self,
        profiles: list[dict[str, Any]] | None = None,
        proxies: list[dict[str, str]] | None = None,
    ) -> None:
        """
        Initialize session manager and pre-warm sessions.

        Args:
            profiles: Optional pre-generated browser profiles.
            proxies: Optional proxy configurations.
        """
        # Import dependencies here to avoid circular imports
        from src.bot.engine.stealth import BrowserProfileGenerator, BrowserSessionPool

        # Setup profile generator
        if not self._profile_generator:
            self._profile_generator = BrowserProfileGenerator()

        # Generate or use provided profiles
        self._profiles = profiles or [
            self._profile_generator.generate()
            for _ in range(self.config.max_sessions)
        ]

        self._proxies = proxies or []

        # Setup session pool
        if not self._pool:
            self._pool = BrowserSessionPool(max_sessions=self.config.max_sessions)
            await self._pool.initialize(
                profiles=self._profiles,
                proxies=self._proxies,
                initial_sessions=self.config.initial_sessions,
            )

        # Wrap pooled sessions in ManagedSession
        await self._wrap_pool_sessions()

        # Start health check background task
        if self.config.auto_recycle:
            self._health_check_task = asyncio.create_task(self._health_check_loop())

        self._initialized = True

        logger.info(
            "session_manager_initialized",
            max_sessions=self.config.max_sessions,
            initial_sessions=self.config.initial_sessions,
            available=self._available_queue.qsize(),
        )

    async def _wrap_pool_sessions(self) -> None:
        """Wrap pool sessions in ManagedSession objects."""
        if not self._pool:
            return

        # Drain existing sessions from pool and wrap them
        while not self._pool.available_sessions.empty():
            try:
                session = self._pool.available_sessions.get_nowait()
                profile_id = session.profile.get("profile_id", "unknown")

                metrics = SessionMetrics(
                    session_id=str(uuid.uuid4()),
                    profile_id=profile_id,
                )

                managed = ManagedSession(session=session, metrics=metrics, manager=self)
                self._sessions[managed.session_id] = managed
                await self._available_queue.put(managed)
            except asyncio.QueueEmpty:
                break

    async def acquire(
        self,
        context_id: str | None = None,
        proxy: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ManagedSession:
        """
        Acquire a session for use.

        Args:
            context_id: Optional context identifier (e.g., booking_id).
            proxy: Optional specific proxy to use.
            timeout: Optional timeout override.

        Returns:
            ManagedSession ready for use.

        Raises:
            SessionError: If no sessions available within timeout.
        """
        if not self._initialized:
            raise SessionError(
                "SessionManager not initialized",
                details={"action": "call initialize() first"},
            )

        timeout = timeout or self.config.acquire_timeout

        try:
            # Try to get from available queue
            managed = await asyncio.wait_for(
                self._available_queue.get(),
                timeout=timeout,
            )

            # Verify health
            health = managed.check_health(self.config)
            if health != SessionHealthStatus.HEALTHY:
                logger.warning(
                    "session_unhealthy_on_acquire",
                    session_id=managed.session_id,
                    health=health.value,
                )
                # Recycle and try again
                await self._recycle_session(managed)
                return await self.acquire(context_id, proxy, timeout)

            # Mark as acquired
            managed.mark_acquired(context_id)

            # Track active session
            async with self._lock:
                self._active_sessions[managed.session_id] = managed

            logger.info(
                "session_acquired",
                session_id=managed.session_id,
                context_id=context_id,
                active_count=len(self._active_sessions),
            )

            return managed

        except asyncio.TimeoutError:
            # Try to create a new session if under limit
            async with self._lock:
                total = len(self._sessions)
                if total < self.config.max_sessions:
                    managed = await self._create_session(proxy)
                    managed.mark_acquired(context_id)
                    self._active_sessions[managed.session_id] = managed
                    return managed

            raise SessionError(
                "No sessions available within timeout",
                code="SESSION_POOL_EXHAUSTED",
                details={
                    "timeout": timeout,
                    "active_sessions": len(self._active_sessions),
                    "total_sessions": len(self._sessions),
                },
            )

    async def release(
        self,
        session: ManagedSession,
        success: bool = True,
        burned: bool = False,
    ) -> None:
        """
        Release a session after use.

        Args:
            session: The session to release.
            success: Whether the session use was successful.
            burned: Whether the session is permanently unusable.
        """
        session_id = session.session_id

        # Remove from active sessions
        async with self._lock:
            if session_id in self._active_sessions:
                del self._active_sessions[session_id]

        if burned:
            # Session is permanently unusable
            session.mark_burned()
            await self._recycle_session(session)
        elif not success:
            # Session failed, apply cooldown
            session.mark_released(success=False, cooldown_seconds=self.config.cooldown_seconds)
            # Put back in queue if not at limit
            if session.metrics.use_count < self.config.max_profile_uses:
                await self._available_queue.put(session)
            else:
                await self._recycle_session(session)
        else:
            # Successful release
            session.mark_released(success=True)

            # Check if session should be recycled
            health = session.check_health(self.config)
            if health != SessionHealthStatus.HEALTHY:
                await self._recycle_session(session)
            else:
                await self._available_queue.put(session)

        logger.info(
            "session_released",
            session_id=session_id,
            success=success,
            burned=burned,
            available=self._available_queue.qsize(),
        )

    @asynccontextmanager
    async def acquire_session(
        self,
        context_id: str | None = None,
        proxy: dict[str, str] | None = None,
    ) -> AsyncIterator[ManagedSession]:
        """
        Context manager for acquiring and auto-releasing a session.

        Args:
            context_id: Optional context identifier.
            proxy: Optional specific proxy to use.

        Yields:
            ManagedSession ready for use.

        Example:
            async with manager.acquire_session("booking_123") as session:
                await session.page.goto("https://example.com")
        """
        session = await self.acquire(context_id, proxy)
        try:
            yield session
        except Exception as e:
            # Check if session should be burned
            burned = self._should_burn_session(e)
            await self.release(session, success=False, burned=burned)
            raise
        else:
            await self.release(session, success=True)

    def _should_burn_session(self, error: Exception) -> bool:
        """
        Determine if error warrants burning the session.

        Args:
            error: The exception that occurred.

        Returns:
            True if session should be permanently discarded.
        """
        # Check for ban/block indicators
        error_str = str(error).lower()
        burn_keywords = ["banned", "blocked", "403", "captcha challenge failed"]

        for keyword in burn_keywords:
            if keyword in error_str:
                return True

        return False

    async def _create_session(
        self,
        proxy: dict[str, str] | None = None,
    ) -> ManagedSession:
        """
        Create a new managed session.

        Uses StealthEngine with tier-aware fallback when available,
        otherwise falls back to direct StealthSessionLauncher.

        Args:
            proxy: Optional proxy configuration.

        Returns:
            New ManagedSession instance.
        """
        from src.bot.engine.stealth import StealthSessionLauncher

        # Use provided proxy or rotate from list
        if not proxy and self._proxies:
            proxy = self._proxies[len(self._sessions) % len(self._proxies)]

        # Use StealthEngine tier fallback if available
        if self._stealth_engine:
            session = await self._stealth_engine.create_session_with_fallback(
                proxy=proxy,
            )
        else:
            # Fallback: direct session without tier-aware launchers
            profile_idx = len(self._sessions) % len(self._profiles)
            profile = self._profiles[profile_idx]
            session = StealthSessionLauncher(profile=profile, proxy=proxy)
            await session.launch()

        # Wrap in managed session
        metrics = SessionMetrics(
            session_id=str(uuid.uuid4()),
            profile_id=profile.get("profile_id", "unknown"),
        )

        managed = ManagedSession(session=session, metrics=metrics, manager=self)

        async with self._lock:
            self._sessions[managed.session_id] = managed

        logger.info(
            "session_created",
            session_id=managed.session_id,
            profile_id=metrics.profile_id,
            total_sessions=len(self._sessions),
        )

        return managed

    async def _recycle_session(self, session: ManagedSession) -> None:
        """
        Recycle a session by closing it and creating a replacement.

        Args:
            session: Session to recycle.
        """
        session_id = session.session_id

        # Close old session
        await session.close()

        # Remove from tracking
        async with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
            if session_id in self._active_sessions:
                del self._active_sessions[session_id]

        # Create replacement if under limit
        if len(self._sessions) < self.config.max_sessions:
            try:
                new_session = await self._create_session()
                await self._available_queue.put(new_session)
                logger.info(
                    "session_recycled",
                    old_session_id=session_id,
                    new_session_id=new_session.session_id,
                )
            except Exception as e:
                logger.warning("session_recycle_failed", error=str(e))

    async def _health_check_loop(self) -> None:
        """Background task for periodic health checks."""
        while True:
            try:
                await asyncio.sleep(self.config.health_check_interval)
                await self._run_health_check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("health_check_error", error=str(e))

    async def _run_health_check(self) -> None:
        """Run health check on all sessions."""
        now = datetime.utcnow()
        sessions_to_check: list[ManagedSession] = []

        # Get available sessions to check
        temp_queue: list[ManagedSession] = []
        while not self._available_queue.empty():
            try:
                session = self._available_queue.get_nowait()
                temp_queue.append(session)
            except asyncio.QueueEmpty:
                break

        for session in temp_queue:
            # Check if cooldown has expired
            if session.metrics.status == SessionStatus.COOLDOWN:
                if session.metrics.cooldown_until and now >= session.metrics.cooldown_until:
                    session.metrics.status = SessionStatus.IDLE
                    session.metrics.cooldown_until = None

            # Check health
            health = session.check_health(self.config)
            if health == SessionHealthStatus.HEALTHY:
                await self._available_queue.put(session)
            else:
                logger.info(
                    "session_health_check_failed",
                    session_id=session.session_id,
                    health=health.value,
                )
                if self.config.auto_recycle:
                    asyncio.create_task(self._recycle_session(session))
                else:
                    await self._available_queue.put(session)

    async def shutdown(self) -> None:
        """Shutdown session manager and close all sessions."""
        # Cancel health check task
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        # Close all sessions
        async with self._lock:
            for session in list(self._sessions.values()):
                await session.close()
            self._sessions.clear()
            self._active_sessions.clear()

        # Clear queue
        while not self._available_queue.empty():
            try:
                self._available_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Close pool
        if self._pool:
            await self._pool.close_all()

        self._initialized = False

        logger.info("session_manager_shutdown")

    @property
    def stats(self) -> dict[str, Any]:
        """
        Get session manager statistics.

        Returns:
            Dictionary with session stats.
        """
        active_count = len(self._active_sessions)
        available_count = self._available_queue.qsize()
        total_count = len(self._sessions)

        # Aggregate metrics
        total_uses = sum(s.metrics.use_count for s in self._sessions.values())
        total_successes = sum(s.metrics.success_count for s in self._sessions.values())
        total_failures = sum(s.metrics.failure_count for s in self._sessions.values())

        return {
            "active": active_count,
            "available": available_count,
            "total": total_count,
            "max": self.config.max_sessions,
            "total_uses": total_uses,
            "total_successes": total_successes,
            "total_failures": total_failures,
            "success_rate": (total_successes / total_uses * 100) if total_uses > 0 else 0.0,
            "initialized": self._initialized,
        }

    def get_session_metrics(self, session_id: str) -> SessionMetrics | None:
        """
        Get metrics for a specific session.

        Args:
            session_id: The session ID.

        Returns:
            SessionMetrics or None if not found.
        """
        session = self._sessions.get(session_id)
        return session.metrics if session else None

    def get_all_metrics(self) -> list[SessionMetrics]:
        """
        Get metrics for all sessions.

        Returns:
            List of SessionMetrics for all tracked sessions.
        """
        return [s.metrics for s in self._sessions.values()]


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "SessionManager",
    "ManagedSession",
    "SessionMetrics",
    "SessionConfig",
    "SessionStatus",
    "SessionHealthStatus",
]
