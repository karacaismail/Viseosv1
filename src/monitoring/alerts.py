"""
Alert System with Escalation for VISE OS.

This module provides a comprehensive alert management system with:
- Multi-severity alert handling (Critical, High, Medium, Low, Info)
- Multi-channel notifications (Telegram, Slack, SMS)
- Auto-remediation engine for common issues
- On-call scheduling and escalation policies
- Alert deduplication and grouping

Based on 019-ALERT-SYSTEM.md specification.

Usage:
    from src.monitoring.alerts import AlertManager, get_alert_manager

    # Using context manager (recommended)
    async with AlertManager() as manager:
        await manager.fire_alert(
            alert_name="booking_success_rate_critical",
            message="Success rate dropped below 50%",
            labels={"site": "vfs"},
        )

    # Or with factory function
    manager = get_alert_manager()
    await manager.process_prometheus_alert(webhook_data)
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import lru_cache
from typing import Any, Callable, Coroutine, Protocol

import structlog

from src.api.config import get_settings
from src.core.exceptions import ViseOSError

logger = structlog.get_logger()


# =============================================================================
# Exceptions
# =============================================================================


class AlertError(ViseOSError):
    """
    Base exception for alert system errors.

    Raised when alert operations fail.

    Attributes:
        alert_id: The alert ID that caused the error.
    """

    def __init__(
        self,
        message: str = "Alert system error occurred",
        *,
        alert_id: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.alert_id = alert_id
        details = details or {}
        if alert_id:
            details["alert_id"] = alert_id
        super().__init__(message, code=code or "ALERT_ERROR", details=details)


class EscalationError(AlertError):
    """Raised when alert escalation fails."""

    def __init__(
        self,
        message: str = "Alert escalation failed",
        *,
        alert_id: str | None = None,
        tier: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        details = details or {}
        if tier:
            details["tier"] = tier
        super().__init__(
            message,
            alert_id=alert_id,
            code="ESCALATION_ERROR",
            details=details,
        )


class RemediationError(AlertError):
    """Raised when auto-remediation fails."""

    def __init__(
        self,
        message: str = "Auto-remediation failed",
        *,
        alert_id: str | None = None,
        action: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        details = details or {}
        if action:
            details["action"] = action
        super().__init__(
            message,
            alert_id=alert_id,
            code="REMEDIATION_ERROR",
            details=details,
        )


# =============================================================================
# Enums
# =============================================================================


class AlertSeverity(str, Enum):
    """
    Alert severity levels as per 019-ALERT-SYSTEM.md.

    Severity determines notification channels and escalation timing.
    """

    CRITICAL = "critical"  # Immediate action required
    HIGH = "high"  # Action within 15 minutes
    MEDIUM = "medium"  # Action within 1 hour
    LOW = "low"  # Action within 24 hours
    INFO = "info"  # No action required


class AlertCategory(str, Enum):
    """
    Alert categories for routing and grouping.

    Categories help route alerts to appropriate teams.
    """

    BOOKING = "booking"
    SYSTEM = "system"
    SECURITY = "security"
    PAYMENT = "payment"
    EXTERNAL = "external"
    CAPACITY = "capacity"


class AlertStatus(str, Enum):
    """Alert lifecycle status."""

    FIRING = "firing"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    SILENCED = "silenced"


class OnCallTier(str, Enum):
    """On-call escalation tiers."""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    MANAGEMENT = "management"


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class AlertConfig:
    """
    Configuration for a specific alert type.

    Defines severity, notification channels, escalation timing,
    and auto-remediation options.

    Attributes:
        severity: Alert severity level.
        category: Alert category for routing.
        evaluation_interval: How often to evaluate the alert condition.
        for_duration: Duration condition must be true before firing.
        channels: Notification channels (telegram, slack, sms).
        escalation_delay: Time before escalating to next tier.
        auto_remediation: Whether to attempt auto-remediation.
        remediation_action: Action to execute for auto-remediation.
    """

    severity: AlertSeverity
    category: AlertCategory
    evaluation_interval: timedelta = field(default_factory=lambda: timedelta(minutes=1))
    for_duration: timedelta = field(default_factory=lambda: timedelta(minutes=5))
    channels: list[str] = field(default_factory=lambda: ["telegram"])
    escalation_delay: timedelta = field(default_factory=lambda: timedelta(minutes=15))
    auto_remediation: bool = False
    remediation_action: str | None = None


@dataclass
class Alert:
    """
    Represents an active alert instance.

    Attributes:
        id: Unique alert instance ID.
        name: Alert rule name.
        severity: Alert severity level.
        category: Alert category.
        status: Current alert status.
        message: Alert summary message.
        description: Detailed alert description.
        labels: Key-value labels for grouping.
        annotations: Additional metadata.
        fired_at: When the alert started firing.
        acknowledged_at: When the alert was acknowledged.
        acknowledged_by: Who acknowledged the alert.
        resolved_at: When the alert was resolved.
        escalation_level: Current escalation tier.
        notification_count: Number of notifications sent.
        fingerprint: Hash for deduplication.
    """

    id: str
    name: str
    severity: AlertSeverity
    category: AlertCategory
    status: AlertStatus
    message: str
    description: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    fired_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None
    resolved_at: datetime | None = None
    escalation_level: OnCallTier = OnCallTier.PRIMARY
    notification_count: int = 0
    fingerprint: str = ""

    def __post_init__(self) -> None:
        """Generate fingerprint if not provided."""
        if not self.fingerprint:
            self.fingerprint = self._generate_fingerprint()

    def _generate_fingerprint(self) -> str:
        """Generate unique fingerprint for deduplication."""
        data = f"{self.name}:{sorted(self.labels.items())}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    @property
    def duration(self) -> timedelta:
        """Get alert duration."""
        end_time = self.resolved_at or datetime.now(timezone.utc)
        return end_time - self.fired_at

    def to_dict(self) -> dict[str, Any]:
        """Convert alert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "severity": self.severity.value,
            "category": self.category.value,
            "status": self.status.value,
            "message": self.message,
            "description": self.description,
            "labels": self.labels,
            "annotations": self.annotations,
            "fired_at": self.fired_at.isoformat(),
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "acknowledged_by": self.acknowledged_by,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "escalation_level": self.escalation_level.value,
            "notification_count": self.notification_count,
            "fingerprint": self.fingerprint,
            "duration_seconds": self.duration.total_seconds(),
        }


@dataclass
class OnCallPerson:
    """
    Represents an on-call team member.

    Attributes:
        id: Unique person identifier.
        name: Display name.
        telegram_id: Telegram chat ID for notifications.
        phone: Phone number for SMS alerts.
        email: Email address.
        tier: On-call tier assignment.
    """

    id: str
    name: str
    telegram_id: str
    phone: str
    email: str
    tier: OnCallTier


@dataclass
class OnCallSchedule:
    """
    On-call schedule entry.

    Attributes:
        person: The on-call person.
        start_time: Schedule start time.
        end_time: Schedule end time.
    """

    person: OnCallPerson
    start_time: datetime
    end_time: datetime


@dataclass
class RemediationAction:
    """
    Auto-remediation action definition.

    Attributes:
        name: Human-readable action name.
        handler: Async handler function.
        timeout_seconds: Maximum execution time.
        requires_approval: Whether manual approval is needed.
    """

    name: str
    handler: Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]
    timeout_seconds: int = 60
    requires_approval: bool = False


@dataclass
class RemediationResult:
    """Result of a remediation action."""

    success: bool
    action: str
    result: dict[str, Any] | None = None
    error: str | None = None
    requires_approval: bool = False
    duration_seconds: float = 0.0


# =============================================================================
# Protocol Definitions
# =============================================================================


class NotificationChannel(Protocol):
    """Protocol for notification channels."""

    async def send_alert(
        self,
        alert: Alert,
        *,
        chat_id: str | None = None,
    ) -> bool:
        """Send alert notification."""
        ...


class WorkerManager(Protocol):
    """Protocol for worker management."""

    async def get_count(self) -> int:
        """Get current worker count."""
        ...

    async def scale_to(self, count: int) -> None:
        """Scale to specified worker count."""
        ...

    async def restart_all(self) -> None:
        """Restart all workers."""
        ...


class QueueManager(Protocol):
    """Protocol for queue management."""

    async def pause_queues(self, sites: list[str] | None = None) -> None:
        """Pause queue processing."""
        ...


class ProxyManager(Protocol):
    """Protocol for proxy management."""

    async def rotate_all(self) -> int:
        """Rotate all proxies."""
        ...


class AccountManager(Protocol):
    """Protocol for account management."""

    async def clear_cooldowns(self, site: str | None = None) -> int:
        """Clear account cooldowns."""
        ...


# =============================================================================
# Alert Configurations
# =============================================================================


ALERT_CONFIGS: dict[str, AlertConfig] = {
    # Booking alerts
    "booking_success_rate_critical": AlertConfig(
        severity=AlertSeverity.CRITICAL,
        category=AlertCategory.BOOKING,
        channels=["telegram", "slack", "sms"],
        escalation_delay=timedelta(minutes=5),
    ),
    "booking_success_rate_high": AlertConfig(
        severity=AlertSeverity.HIGH,
        category=AlertCategory.BOOKING,
        channels=["telegram", "slack"],
        escalation_delay=timedelta(minutes=10),
    ),
    "site_failure_spike": AlertConfig(
        severity=AlertSeverity.HIGH,
        category=AlertCategory.BOOKING,
        channels=["telegram", "slack"],
        escalation_delay=timedelta(minutes=10),
    ),
    # System alerts
    "queue_backlog_critical": AlertConfig(
        severity=AlertSeverity.CRITICAL,
        category=AlertCategory.CAPACITY,
        channels=["telegram", "slack", "sms"],
        escalation_delay=timedelta(minutes=5),
        auto_remediation=True,
        remediation_action="scale_workers_up",
    ),
    "queue_backlog_high": AlertConfig(
        severity=AlertSeverity.HIGH,
        category=AlertCategory.CAPACITY,
        channels=["telegram", "slack"],
        escalation_delay=timedelta(minutes=10),
        auto_remediation=True,
        remediation_action="scale_workers_up",
    ),
    "no_active_workers": AlertConfig(
        severity=AlertSeverity.CRITICAL,
        category=AlertCategory.SYSTEM,
        channels=["telegram", "slack", "sms"],
        escalation_delay=timedelta(minutes=2),
        auto_remediation=True,
        remediation_action="restart_workers",
    ),
    "worker_count_low": AlertConfig(
        severity=AlertSeverity.HIGH,
        category=AlertCategory.SYSTEM,
        channels=["telegram", "slack"],
        escalation_delay=timedelta(minutes=10),
        auto_remediation=True,
        remediation_action="restart_workers",
    ),
    # Resource alerts
    "proxy_pool_exhausted": AlertConfig(
        severity=AlertSeverity.HIGH,
        category=AlertCategory.CAPACITY,
        channels=["telegram", "slack"],
        escalation_delay=timedelta(minutes=10),
        auto_remediation=True,
        remediation_action="rotate_proxies",
    ),
    "account_pool_exhausted": AlertConfig(
        severity=AlertSeverity.HIGH,
        category=AlertCategory.CAPACITY,
        channels=["telegram", "slack"],
        escalation_delay=timedelta(minutes=10),
    ),
    "account_ban_rate_elevated": AlertConfig(
        severity=AlertSeverity.MEDIUM,
        category=AlertCategory.SECURITY,
        channels=["telegram"],
        escalation_delay=timedelta(minutes=30),
    ),
    "account_ban_rate_high": AlertConfig(
        severity=AlertSeverity.HIGH,
        category=AlertCategory.SECURITY,
        channels=["telegram", "slack"],
        escalation_delay=timedelta(minutes=15),
        auto_remediation=True,
        remediation_action="pause_site",
    ),
    # External service alerts
    "external_service_down": AlertConfig(
        severity=AlertSeverity.HIGH,
        category=AlertCategory.EXTERNAL,
        channels=["telegram", "slack"],
        escalation_delay=timedelta(minutes=15),
    ),
    "captcha_solve_rate_low": AlertConfig(
        severity=AlertSeverity.MEDIUM,
        category=AlertCategory.EXTERNAL,
        channels=["telegram"],
        escalation_delay=timedelta(minutes=30),
    ),
    # Payment alerts
    "payment_failure_rate_high": AlertConfig(
        severity=AlertSeverity.HIGH,
        category=AlertCategory.PAYMENT,
        channels=["telegram", "slack"],
        escalation_delay=timedelta(minutes=15),
    ),
    # System resource alerts
    "high_cpu_usage": AlertConfig(
        severity=AlertSeverity.HIGH,
        category=AlertCategory.SYSTEM,
        channels=["telegram", "slack"],
        escalation_delay=timedelta(minutes=15),
    ),
    "high_memory_usage": AlertConfig(
        severity=AlertSeverity.HIGH,
        category=AlertCategory.SYSTEM,
        channels=["telegram", "slack"],
        escalation_delay=timedelta(minutes=15),
    ),
    "disk_space_low": AlertConfig(
        severity=AlertSeverity.MEDIUM,
        category=AlertCategory.SYSTEM,
        channels=["telegram"],
        escalation_delay=timedelta(minutes=30),
    ),
}


# =============================================================================
# Escalation Configuration
# =============================================================================


ESCALATION_TIMEOUTS: dict[OnCallTier, timedelta] = {
    OnCallTier.PRIMARY: timedelta(minutes=15),
    OnCallTier.SECONDARY: timedelta(minutes=30),
    OnCallTier.MANAGEMENT: timedelta(minutes=60),
}


# =============================================================================
# Auto-Remediation Engine
# =============================================================================


class AutoRemediationEngine:
    """
    Engine for executing auto-remediation actions.

    Provides automatic recovery from common issues like queue backlogs,
    worker failures, and proxy exhaustion.

    Attributes:
        queue_manager: Queue management interface.
        worker_manager: Worker management interface.
        proxy_manager: Proxy management interface.
        account_manager: Account management interface.
    """

    def __init__(
        self,
        queue_manager: QueueManager | None = None,
        worker_manager: WorkerManager | None = None,
        proxy_manager: ProxyManager | None = None,
        account_manager: AccountManager | None = None,
    ) -> None:
        """
        Initialize the remediation engine.

        Args:
            queue_manager: Queue management interface.
            worker_manager: Worker management interface.
            proxy_manager: Proxy management interface.
            account_manager: Account management interface.
        """
        self.queue = queue_manager
        self.workers = worker_manager
        self.proxies = proxy_manager
        self.accounts = account_manager
        self._logger = logger.bind(component="remediation")

        # Register remediation handlers
        self.actions: dict[str, RemediationAction] = {
            "scale_workers_up": RemediationAction(
                name="Scale Workers Up",
                handler=self._scale_workers_up,
                timeout_seconds=120,
            ),
            "scale_workers_down": RemediationAction(
                name="Scale Workers Down",
                handler=self._scale_workers_down,
                timeout_seconds=60,
            ),
            "restart_workers": RemediationAction(
                name="Restart Workers",
                handler=self._restart_workers,
                timeout_seconds=180,
                requires_approval=True,
            ),
            "rotate_proxies": RemediationAction(
                name="Rotate Proxy Pool",
                handler=self._rotate_proxies,
                timeout_seconds=60,
            ),
            "pause_site": RemediationAction(
                name="Pause Site Operations",
                handler=self._pause_site,
                timeout_seconds=30,
                requires_approval=True,
            ),
            "clear_cooldowns": RemediationAction(
                name="Clear Account Cooldowns",
                handler=self._clear_cooldowns,
                timeout_seconds=30,
            ),
        }

    async def execute_remediation(
        self,
        action_name: str,
        alert_context: dict[str, Any],
        approved: bool = False,
    ) -> RemediationResult:
        """
        Execute a remediation action.

        Args:
            action_name: Name of the remediation action.
            alert_context: Context from the triggering alert.
            approved: Whether manual approval has been given.

        Returns:
            RemediationResult with success status and details.
        """
        if action_name not in self.actions:
            return RemediationResult(
                success=False,
                action=action_name,
                error=f"Unknown action: {action_name}",
            )

        action = self.actions[action_name]

        # Check approval requirement
        if action.requires_approval and not approved:
            self._logger.info(
                "remediation_requires_approval",
                action=action_name,
            )
            return RemediationResult(
                success=False,
                action=action_name,
                error="Action requires manual approval",
                requires_approval=True,
            )

        start_time = datetime.now(timezone.utc)

        try:
            self._logger.info(
                "remediation_starting",
                action=action_name,
                context=alert_context,
            )

            # Execute with timeout
            result = await asyncio.wait_for(
                action.handler(alert_context),
                timeout=action.timeout_seconds,
            )

            duration = (datetime.now(timezone.utc) - start_time).total_seconds()

            self._logger.info(
                "remediation_completed",
                action=action_name,
                result=result,
                duration_seconds=duration,
            )

            return RemediationResult(
                success=True,
                action=action_name,
                result=result,
                duration_seconds=duration,
            )

        except asyncio.TimeoutError:
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            self._logger.error(
                "remediation_timeout",
                action=action_name,
                timeout_seconds=action.timeout_seconds,
            )
            return RemediationResult(
                success=False,
                action=action_name,
                error="Remediation timed out",
                duration_seconds=duration,
            )
        except Exception as e:
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            self._logger.error(
                "remediation_failed",
                action=action_name,
                error=str(e),
            )
            return RemediationResult(
                success=False,
                action=action_name,
                error=str(e),
                duration_seconds=duration,
            )

    async def _scale_workers_up(self, context: dict[str, Any]) -> dict[str, Any]:
        """Scale workers up by 5, max 50."""
        if not self.workers:
            return {"action": "skipped", "reason": "worker_manager_not_configured"}

        current = await self.workers.get_count()
        target = min(current + 5, 50)
        await self.workers.scale_to(target)

        return {
            "previous": current,
            "current": target,
            "action": "scaled_up",
        }

    async def _scale_workers_down(self, context: dict[str, Any]) -> dict[str, Any]:
        """Scale workers down by 3, min 5."""
        if not self.workers:
            return {"action": "skipped", "reason": "worker_manager_not_configured"}

        current = await self.workers.get_count()
        target = max(current - 3, 5)
        await self.workers.scale_to(target)

        return {
            "previous": current,
            "current": target,
            "action": "scaled_down",
        }

    async def _restart_workers(self, context: dict[str, Any]) -> dict[str, Any]:
        """Restart all workers."""
        if not self.workers:
            return {"action": "skipped", "reason": "worker_manager_not_configured"}

        await self.workers.restart_all()
        return {"action": "restarted"}

    async def _rotate_proxies(self, context: dict[str, Any]) -> dict[str, Any]:
        """Rotate all proxies in the pool."""
        if not self.proxies:
            return {"action": "skipped", "reason": "proxy_manager_not_configured"}

        rotated = await self.proxies.rotate_all()
        return {
            "rotated_count": rotated,
            "action": "proxies_rotated",
        }

    async def _pause_site(self, context: dict[str, Any]) -> dict[str, Any]:
        """Pause site operations."""
        if not self.queue:
            return {"action": "skipped", "reason": "queue_manager_not_configured"}

        site = context.get("site", "all")
        sites = [site] if site != "all" else None
        await self.queue.pause_queues(sites)

        return {
            "site": site,
            "action": "paused",
        }

    async def _clear_cooldowns(self, context: dict[str, Any]) -> dict[str, Any]:
        """Clear account cooldowns."""
        if not self.accounts:
            return {"action": "skipped", "reason": "account_manager_not_configured"}

        site = context.get("site")
        cleared = await self.accounts.clear_cooldowns(site)

        return {
            "cleared_count": cleared,
            "action": "cooldowns_cleared",
        }


# =============================================================================
# On-Call Router
# =============================================================================


class OnCallRouter:
    """
    Routes alerts to on-call personnel with escalation support.

    Manages on-call schedules, alert routing, acknowledgment tracking,
    and automatic escalation when alerts are not acknowledged.

    Attributes:
        schedules: List of on-call schedules.
        active_alerts: Currently active alerts pending acknowledgment.
    """

    def __init__(self) -> None:
        """Initialize the on-call router."""
        self.schedules: list[OnCallSchedule] = []
        self.active_alerts: dict[str, dict[str, Any]] = {}
        self._escalation_tasks: dict[str, asyncio.Task[None]] = {}
        self._logger = logger.bind(component="oncall")

    def add_schedule(self, schedule: OnCallSchedule) -> None:
        """Add an on-call schedule."""
        self.schedules.append(schedule)
        self._logger.info(
            "oncall_schedule_added",
            person=schedule.person.name,
            tier=schedule.person.tier.value,
            start=schedule.start_time.isoformat(),
            end=schedule.end_time.isoformat(),
        )

    def get_current_oncall(
        self,
        tier: OnCallTier = OnCallTier.PRIMARY,
    ) -> OnCallPerson | None:
        """
        Get the current on-call person for a tier.

        Args:
            tier: The on-call tier to query.

        Returns:
            The on-call person or None if no one is scheduled.
        """
        now = datetime.now(timezone.utc)

        for schedule in self.schedules:
            if (
                schedule.person.tier == tier
                and schedule.start_time <= now <= schedule.end_time
            ):
                return schedule.person

        return None

    async def route_alert(
        self,
        alert: Alert,
        notification_callback: Callable[
            [OnCallPerson, Alert, str], Coroutine[Any, Any, bool]
        ],
    ) -> dict[str, Any]:
        """
        Route an alert to the appropriate on-call person.

        Args:
            alert: The alert to route.
            notification_callback: Async callback to send notification.

        Returns:
            Routing result with person and tier information.
        """
        # Find primary on-call
        primary = self.get_current_oncall(OnCallTier.PRIMARY)

        if not primary:
            # Fallback to management
            primary = self.get_current_oncall(OnCallTier.MANAGEMENT)

        if not primary:
            self._logger.warning(
                "oncall_no_person_available",
                alert_id=alert.id,
            )
            return {
                "success": False,
                "error": "No on-call person available",
            }

        # Track alert
        self.active_alerts[alert.id] = {
            "alert": alert,
            "routed_to": primary.id,
            "routed_at": datetime.now(timezone.utc),
            "acknowledged": False,
        }

        # Send notification
        message = self._format_alert_message(alert)
        await notification_callback(primary, alert, message)

        self._logger.info(
            "alert_routed",
            alert_id=alert.id,
            person=primary.name,
            tier=primary.tier.value,
        )

        # Schedule escalation for high-priority alerts
        if alert.severity in [AlertSeverity.CRITICAL, AlertSeverity.HIGH]:
            task = asyncio.create_task(
                self._schedule_escalation(
                    alert,
                    primary.tier,
                    notification_callback,
                )
            )
            self._escalation_tasks[alert.id] = task

        return {
            "success": True,
            "routed_to": primary.name,
            "tier": primary.tier.value,
        }

    async def acknowledge_alert(
        self,
        alert_id: str,
        person_id: str,
    ) -> bool:
        """
        Acknowledge an alert.

        Args:
            alert_id: The alert ID to acknowledge.
            person_id: The ID of the person acknowledging.

        Returns:
            True if acknowledgment was successful.
        """
        if alert_id not in self.active_alerts:
            return False

        self.active_alerts[alert_id]["acknowledged"] = True
        self.active_alerts[alert_id]["acknowledged_by"] = person_id
        self.active_alerts[alert_id]["acknowledged_at"] = datetime.now(timezone.utc)

        # Cancel escalation task
        if alert_id in self._escalation_tasks:
            self._escalation_tasks[alert_id].cancel()
            del self._escalation_tasks[alert_id]

        self._logger.info(
            "alert_acknowledged",
            alert_id=alert_id,
            acknowledged_by=person_id,
        )

        return True

    async def resolve_alert(self, alert_id: str) -> bool:
        """
        Resolve an alert and remove from active tracking.

        Args:
            alert_id: The alert ID to resolve.

        Returns:
            True if resolution was successful.
        """
        if alert_id not in self.active_alerts:
            return False

        # Cancel escalation task
        if alert_id in self._escalation_tasks:
            self._escalation_tasks[alert_id].cancel()
            del self._escalation_tasks[alert_id]

        del self.active_alerts[alert_id]

        self._logger.info("alert_resolved", alert_id=alert_id)
        return True

    async def _schedule_escalation(
        self,
        alert: Alert,
        current_tier: OnCallTier,
        notification_callback: Callable[
            [OnCallPerson, Alert, str], Coroutine[Any, Any, bool]
        ],
    ) -> None:
        """Schedule and execute escalation."""
        timeout = ESCALATION_TIMEOUTS[current_tier]

        try:
            await asyncio.sleep(timeout.total_seconds())

            # Check if acknowledged
            alert_data = self.active_alerts.get(alert.id)
            if not alert_data or alert_data.get("acknowledged"):
                return

            # Escalate to next tier
            next_tier = self._get_next_tier(current_tier)
            if not next_tier:
                return

            next_person = self.get_current_oncall(next_tier)
            if not next_person:
                return

            # Update alert escalation level
            alert.escalation_level = next_tier
            alert.notification_count += 1

            # Send escalated notification
            message = (
                f"ESCALATED from {current_tier.value}\n\n"
                f"{self._format_alert_message(alert)}"
            )
            await notification_callback(next_person, alert, message)

            self._logger.warning(
                "alert_escalated",
                alert_id=alert.id,
                from_tier=current_tier.value,
                to_tier=next_tier.value,
                to_person=next_person.name,
            )

            # Schedule next escalation
            await self._schedule_escalation(alert, next_tier, notification_callback)

        except asyncio.CancelledError:
            pass

    def _get_next_tier(self, current: OnCallTier) -> OnCallTier | None:
        """Get the next escalation tier."""
        order = [OnCallTier.PRIMARY, OnCallTier.SECONDARY, OnCallTier.MANAGEMENT]

        try:
            idx = order.index(current)
            if idx < len(order) - 1:
                return order[idx + 1]
        except ValueError:
            pass

        return None

    def _format_alert_message(self, alert: Alert) -> str:
        """Format alert for notification."""
        parts = [
            f"Alert #{alert.id[:8]}",
            f"Name: {alert.name}",
            f"Severity: {alert.severity.value.upper()}",
            f"Category: {alert.category.value}",
            "",
            alert.message,
        ]

        if alert.description:
            parts.append("")
            parts.append(alert.description)

        if alert.labels:
            parts.append("")
            parts.append("Labels:")
            for key, value in alert.labels.items():
                parts.append(f"  {key}: {value}")

        parts.append("")
        parts.append(f"Fired at: {alert.fired_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        parts.append("")
        parts.append(f"Reply /ack {alert.id} to acknowledge")

        return "\n".join(parts)


# =============================================================================
# Alert Manager
# =============================================================================


class AlertManager:
    """
    Central alert management system.

    Provides:
    - Alert firing and tracking
    - Deduplication and grouping
    - Multi-channel notifications
    - Auto-remediation
    - On-call routing and escalation

    Usage:
        async with AlertManager() as manager:
            await manager.fire_alert(
                alert_name="booking_success_rate_critical",
                message="Success rate dropped below 50%",
            )
    """

    def __init__(
        self,
        remediation_engine: AutoRemediationEngine | None = None,
        oncall_router: OnCallRouter | None = None,
    ) -> None:
        """
        Initialize the alert manager.

        Args:
            remediation_engine: Auto-remediation engine instance.
            oncall_router: On-call routing instance.
        """
        self._settings = get_settings()
        self._logger = logger.bind(component="alert_manager")

        # Alert tracking
        self._active_alerts: dict[str, Alert] = {}
        self._alert_history: list[Alert] = []
        self._silenced_fingerprints: set[str] = set()

        # Components
        self.remediation = remediation_engine or AutoRemediationEngine()
        self.oncall = oncall_router or OnCallRouter()

        # Notification channels (lazy loaded)
        self._telegram: Any = None
        self._initialized = False

    async def _init_channels(self) -> None:
        """Initialize notification channels."""
        if self._initialized:
            return

        try:
            from src.integrations.telegram import TelegramNotifier

            self._telegram = TelegramNotifier()
            await self._telegram._get_client()
        except ImportError:
            self._logger.warning("telegram_notifier_not_available")
        except Exception as e:
            self._logger.error("telegram_init_failed", error=str(e))

        self._initialized = True

    async def __aenter__(self) -> "AlertManager":
        """Async context manager entry."""
        await self._init_channels()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Async context manager exit."""
        if self._telegram:
            await self._telegram.close()

    async def fire_alert(
        self,
        alert_name: str,
        message: str,
        *,
        description: str = "",
        labels: dict[str, str] | None = None,
        annotations: dict[str, str] | None = None,
        skip_remediation: bool = False,
    ) -> Alert:
        """
        Fire a new alert.

        Args:
            alert_name: Name of the alert (must be in ALERT_CONFIGS).
            message: Alert summary message.
            description: Detailed description.
            labels: Key-value labels for grouping.
            annotations: Additional metadata.
            skip_remediation: Skip auto-remediation for this alert.

        Returns:
            The created Alert instance.

        Raises:
            AlertError: If alert configuration is not found.
        """
        await self._init_channels()

        config = ALERT_CONFIGS.get(alert_name)
        if not config:
            raise AlertError(
                f"Unknown alert: {alert_name}",
                code="UNKNOWN_ALERT",
            )

        # Create alert instance
        alert = Alert(
            id=str(uuid.uuid4()),
            name=alert_name,
            severity=config.severity,
            category=config.category,
            status=AlertStatus.FIRING,
            message=message,
            description=description,
            labels=labels or {},
            annotations=annotations or {},
        )

        # Check for deduplication
        if alert.fingerprint in self._silenced_fingerprints:
            self._logger.info(
                "alert_silenced",
                alert_name=alert_name,
                fingerprint=alert.fingerprint,
            )
            alert.status = AlertStatus.SILENCED
            return alert

        # Check for existing alert with same fingerprint
        existing = self._find_by_fingerprint(alert.fingerprint)
        if existing and existing.status == AlertStatus.FIRING:
            self._logger.info(
                "alert_deduplicated",
                alert_name=alert_name,
                existing_id=existing.id,
            )
            existing.notification_count += 1
            return existing

        # Track the alert
        self._active_alerts[alert.id] = alert
        self._alert_history.append(alert)

        self._logger.warning(
            "alert_fired",
            alert_id=alert.id,
            alert_name=alert_name,
            severity=config.severity.value,
            category=config.category.value,
        )

        # Send notifications
        await self._send_notifications(alert, config)

        # Attempt auto-remediation
        if config.auto_remediation and config.remediation_action and not skip_remediation:
            context = {
                "alert_id": alert.id,
                "alert_name": alert_name,
                **alert.labels,
            }
            result = await self.remediation.execute_remediation(
                config.remediation_action,
                context,
            )
            alert.annotations["remediation_result"] = (
                "success" if result.success else f"failed: {result.error}"
            )

        return alert

    async def resolve_alert(
        self,
        alert_id: str | None = None,
        fingerprint: str | None = None,
    ) -> bool:
        """
        Resolve an alert.

        Args:
            alert_id: The alert ID to resolve.
            fingerprint: The fingerprint to resolve (alternative to ID).

        Returns:
            True if the alert was resolved.
        """
        alert: Alert | None = None

        if alert_id:
            alert = self._active_alerts.get(alert_id)
        elif fingerprint:
            alert = self._find_by_fingerprint(fingerprint)

        if not alert:
            return False

        alert.status = AlertStatus.RESOLVED
        alert.resolved_at = datetime.now(timezone.utc)

        # Remove from active alerts
        if alert.id in self._active_alerts:
            del self._active_alerts[alert.id]

        # Resolve in on-call router
        await self.oncall.resolve_alert(alert.id)

        self._logger.info(
            "alert_resolved",
            alert_id=alert.id,
            duration_seconds=alert.duration.total_seconds(),
        )

        # Send resolution notification
        await self._send_resolution(alert)

        return True

    async def acknowledge_alert(
        self,
        alert_id: str,
        acknowledged_by: str,
    ) -> bool:
        """
        Acknowledge an alert.

        Args:
            alert_id: The alert ID to acknowledge.
            acknowledged_by: Identifier of who acknowledged.

        Returns:
            True if acknowledgment was successful.
        """
        alert = self._active_alerts.get(alert_id)
        if not alert:
            return False

        alert.status = AlertStatus.ACKNOWLEDGED
        alert.acknowledged_at = datetime.now(timezone.utc)
        alert.acknowledged_by = acknowledged_by

        # Acknowledge in on-call router
        await self.oncall.acknowledge_alert(alert_id, acknowledged_by)

        self._logger.info(
            "alert_acknowledged",
            alert_id=alert_id,
            acknowledged_by=acknowledged_by,
        )

        return True

    def silence_alert(
        self,
        fingerprint: str,
        duration: timedelta | None = None,
    ) -> None:
        """
        Silence alerts matching a fingerprint.

        Args:
            fingerprint: The fingerprint pattern to silence.
            duration: How long to silence (None = indefinite).
        """
        self._silenced_fingerprints.add(fingerprint)
        self._logger.info(
            "alert_silenced",
            fingerprint=fingerprint,
            duration_seconds=duration.total_seconds() if duration else None,
        )

        if duration:
            asyncio.create_task(self._unsilence_after(fingerprint, duration))

    async def _unsilence_after(
        self,
        fingerprint: str,
        duration: timedelta,
    ) -> None:
        """Remove silence after duration."""
        await asyncio.sleep(duration.total_seconds())
        self._silenced_fingerprints.discard(fingerprint)

    def get_active_alerts(
        self,
        severity: AlertSeverity | None = None,
        category: AlertCategory | None = None,
    ) -> list[Alert]:
        """
        Get active alerts with optional filtering.

        Args:
            severity: Filter by severity.
            category: Filter by category.

        Returns:
            List of matching active alerts.
        """
        alerts = list(self._active_alerts.values())

        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        if category:
            alerts = [a for a in alerts if a.category == category]

        return sorted(alerts, key=lambda a: a.fired_at, reverse=True)

    def get_alert_stats(self) -> dict[str, Any]:
        """Get alert statistics."""
        active = list(self._active_alerts.values())

        by_severity = {}
        for sev in AlertSeverity:
            by_severity[sev.value] = len([a for a in active if a.severity == sev])

        by_category = {}
        for cat in AlertCategory:
            by_category[cat.value] = len([a for a in active if a.category == cat])

        return {
            "total_active": len(active),
            "by_severity": by_severity,
            "by_category": by_category,
            "total_historical": len(self._alert_history),
            "silenced_patterns": len(self._silenced_fingerprints),
        }

    async def process_prometheus_alert(
        self,
        webhook_data: dict[str, Any],
    ) -> list[Alert]:
        """
        Process alerts from Prometheus Alertmanager webhook.

        Args:
            webhook_data: Alertmanager webhook payload.

        Returns:
            List of processed alerts.
        """
        alerts = []
        status = webhook_data.get("status", "firing")

        for alert_data in webhook_data.get("alerts", []):
            labels = alert_data.get("labels", {})
            annotations = alert_data.get("annotations", {})
            alert_name = labels.get("alertname", "unknown")

            # Convert Prometheus alert name to our format
            normalized_name = alert_name.lower().replace("-", "_")

            if status == "resolved":
                # Try to resolve by fingerprint
                fingerprint = alert_data.get("fingerprint", "")
                await self.resolve_alert(fingerprint=fingerprint)
            else:
                try:
                    alert = await self.fire_alert(
                        alert_name=normalized_name,
                        message=annotations.get("summary", alert_name),
                        description=annotations.get("description", ""),
                        labels=labels,
                        annotations=annotations,
                    )
                    alerts.append(alert)
                except AlertError:
                    # Unknown alert type, create with default config
                    self._logger.warning(
                        "prometheus_unknown_alert",
                        alert_name=normalized_name,
                    )

        return alerts

    def _find_by_fingerprint(self, fingerprint: str) -> Alert | None:
        """Find active alert by fingerprint."""
        for alert in self._active_alerts.values():
            if alert.fingerprint == fingerprint:
                return alert
        return None

    async def _send_notifications(
        self,
        alert: Alert,
        config: AlertConfig,
    ) -> None:
        """Send notifications through configured channels."""
        alert.notification_count += 1

        for channel in config.channels:
            if channel == "telegram" and self._telegram:
                try:
                    from src.integrations.telegram import (
                        AlertCategory as TelegramCategory,
                        AlertSeverity as TelegramSeverity,
                    )

                    # Map severity and category to telegram module enums
                    severity_map = {
                        AlertSeverity.CRITICAL: TelegramSeverity.CRITICAL,
                        AlertSeverity.HIGH: TelegramSeverity.HIGH,
                        AlertSeverity.MEDIUM: TelegramSeverity.MEDIUM,
                        AlertSeverity.LOW: TelegramSeverity.LOW,
                        AlertSeverity.INFO: TelegramSeverity.INFO,
                    }
                    category_map = {
                        AlertCategory.BOOKING: TelegramCategory.BOOKING,
                        AlertCategory.SYSTEM: TelegramCategory.SYSTEM,
                        AlertCategory.SECURITY: TelegramCategory.SECURITY,
                        AlertCategory.PAYMENT: TelegramCategory.PAYMENT,
                        AlertCategory.EXTERNAL: TelegramCategory.EXTERNAL,
                        AlertCategory.CAPACITY: TelegramCategory.CAPACITY,
                    }

                    await self._telegram.send_alert(
                        severity=severity_map[alert.severity],
                        category=category_map[alert.category],
                        title=alert.name.replace("_", " ").title(),
                        message=alert.message,
                        details=alert.labels if alert.labels else None,
                        runbook_url=alert.annotations.get("runbook"),
                    )
                except Exception as e:
                    self._logger.error(
                        "telegram_notification_failed",
                        alert_id=alert.id,
                        error=str(e),
                    )

            elif channel == "slack":
                self._logger.info(
                    "slack_notification_skipped",
                    alert_id=alert.id,
                    reason="not_implemented",
                )

            elif channel == "sms":
                self._logger.info(
                    "sms_notification_skipped",
                    alert_id=alert.id,
                    reason="not_implemented",
                )

    async def _send_resolution(self, alert: Alert) -> None:
        """Send resolution notification."""
        if self._telegram:
            try:
                message = (
                    f"Alert RESOLVED: {alert.name.replace('_', ' ').title()}\n\n"
                    f"Duration: {int(alert.duration.total_seconds())} seconds"
                )
                await self._telegram.send_message(text=message)
            except Exception as e:
                self._logger.error(
                    "resolution_notification_failed",
                    alert_id=alert.id,
                    error=str(e),
                )


# =============================================================================
# Factory Functions
# =============================================================================


@lru_cache
def get_alert_manager() -> AlertManager:
    """
    Get a cached AlertManager instance.

    Returns:
        Configured AlertManager instance.
    """
    return AlertManager()


def clear_alert_manager_cache() -> None:
    """Clear the cached alert manager."""
    get_alert_manager.cache_clear()


@asynccontextmanager
async def alert_manager():
    """
    Async context manager for AlertManager.

    Yields:
        Configured AlertManager instance.
    """
    manager = AlertManager()
    try:
        await manager._init_channels()
        yield manager
    finally:
        if manager._telegram:
            await manager._telegram.close()


# =============================================================================
# Exports
# =============================================================================


__all__ = [
    # Main class
    "AlertManager",
    "get_alert_manager",
    "clear_alert_manager_cache",
    "alert_manager",
    # Components
    "AutoRemediationEngine",
    "OnCallRouter",
    # Data classes
    "Alert",
    "AlertConfig",
    "OnCallPerson",
    "OnCallSchedule",
    "RemediationAction",
    "RemediationResult",
    # Enums
    "AlertSeverity",
    "AlertCategory",
    "AlertStatus",
    "OnCallTier",
    # Configurations
    "ALERT_CONFIGS",
    "ESCALATION_TIMEOUTS",
    # Exceptions
    "AlertError",
    "EscalationError",
    "RemediationError",
]
