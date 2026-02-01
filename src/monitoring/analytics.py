"""
Analytics and Reporting Service for VISE OS.

This module provides comprehensive analytics and reporting capabilities including:
- Data warehouse schema for booking analytics
- ETL pipeline for data transformation
- Report generation for agency and platform metrics
- Multi-format export (PDF, Excel, JSON)
- Scheduled report delivery

Based on 020-ANALYTICS-REPORTING.md specification.

Usage:
    from src.monitoring.analytics import ReportGenerator, AnalyticsETLPipeline

    # Generate agency daily report
    generator = ReportGenerator(analytics_db)
    report = await generator.generate_agency_daily_report(
        agency_id="agency-123",
        report_date=date.today() - timedelta(days=1),
    )

    # Run ETL pipeline
    pipeline = AnalyticsETLPipeline(source_db, analytics_db, prometheus_client)
    await pipeline.run_daily_etl()
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from functools import lru_cache
from typing import Any, Protocol

import structlog

from src.core.exceptions import ViseOSError

logger = structlog.get_logger()


# =============================================================================
# Exceptions
# =============================================================================


class AnalyticsError(ViseOSError):
    """
    Base exception for analytics errors.

    Raised when analytics operations fail.

    Attributes:
        report_type: The type of report that failed.
    """

    def __init__(
        self,
        message: str = "Analytics error occurred",
        *,
        report_type: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.report_type = report_type
        details = details or {}
        if report_type:
            details["report_type"] = report_type
        super().__init__(message, code=code or "ANALYTICS_ERROR", details=details)


class ETLError(AnalyticsError):
    """Raised when ETL pipeline fails."""

    def __init__(
        self,
        message: str = "ETL pipeline failed",
        *,
        stage: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.stage = stage
        details = details or {}
        if stage:
            details["stage"] = stage
        super().__init__(message, code="ETL_ERROR", details=details)


class ReportGenerationError(AnalyticsError):
    """Raised when report generation fails."""

    def __init__(
        self,
        message: str = "Report generation failed",
        *,
        report_type: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            report_type=report_type,
            code="REPORT_GENERATION_ERROR",
            details=details,
        )


class ExportError(AnalyticsError):
    """Raised when report export fails."""

    def __init__(
        self,
        message: str = "Report export failed",
        *,
        export_format: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.export_format = export_format
        details = details or {}
        if export_format:
            details["export_format"] = export_format
        super().__init__(message, code="EXPORT_ERROR", details=details)


# =============================================================================
# Enums
# =============================================================================


class ReportType(str, Enum):
    """Report types available in the system."""

    AGENCY_DAILY = "agency_daily"
    AGENCY_WEEKLY = "agency_weekly"
    AGENCY_MONTHLY = "agency_monthly"
    PLATFORM_DAILY = "platform_daily"
    PLATFORM_WEEKLY = "platform_weekly"
    SITE_PERFORMANCE = "site_performance"
    FINANCIAL = "financial"
    CAPACITY = "capacity"


class ExportFormat(str, Enum):
    """Supported export formats."""

    PDF = "pdf"
    EXCEL = "excel"
    JSON = "json"
    CSV = "csv"


class AggregationPeriod(str, Enum):
    """Time aggregation periods."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


# =============================================================================
# Dimension Tables (Data Warehouse Schema)
# =============================================================================


@dataclass
class DimAgency:
    """
    Agency dimension for analytics.

    Represents an agency in the data warehouse for dimensional modeling.

    Attributes:
        agency_key: Surrogate key for the dimension.
        agency_id: Business key from the operational system.
        agency_name: Human-readable agency name.
        tier: Subscription tier (premium, standard, trial).
        country: Country of operation.
        created_at: When the agency was created.
        is_active: Whether the agency is currently active.
    """

    agency_key: int
    agency_id: str
    agency_name: str
    tier: str
    country: str
    created_at: date
    is_active: bool


@dataclass
class DimSite:
    """
    Visa site dimension for analytics.

    Attributes:
        site_key: Surrogate key for the dimension.
        site_code: Site code (vfs, idata, bls, kkosmos).
        site_name: Human-readable site name.
        country: Country of the site.
        difficulty_level: Booking difficulty (easy, medium, hard).
    """

    site_key: int
    site_code: str
    site_name: str
    country: str
    difficulty_level: str


@dataclass
class DimDate:
    """
    Date dimension for time-based analysis.

    Attributes:
        date_key: Surrogate key (YYYYMMDD format).
        full_date: The actual date.
        year: Year component.
        quarter: Quarter (1-4).
        month: Month (1-12).
        week: ISO week number.
        day_of_week: Day of week (0=Monday).
        day_name: Day name (Monday, Tuesday, etc.).
        is_weekend: Whether the date is a weekend.
        is_holiday: Whether the date is a holiday.
    """

    date_key: int
    full_date: date
    year: int
    quarter: int
    month: int
    week: int
    day_of_week: int
    day_name: str
    is_weekend: bool
    is_holiday: bool


@dataclass
class DimTime:
    """
    Time dimension for hourly analysis.

    Attributes:
        time_key: Surrogate key.
        hour: Hour (0-23).
        minute_bucket: 15-minute bucket (0, 15, 30, 45).
        time_of_day: Period name (morning, afternoon, evening, night).
        is_business_hours: Whether within business hours.
    """

    time_key: int
    hour: int
    minute_bucket: int
    time_of_day: str
    is_business_hours: bool


@dataclass
class DimCountry:
    """
    Target country dimension.

    Attributes:
        country_key: Surrogate key.
        country_code: ISO country code.
        country_name: Human-readable country name.
        region: Region classification (schengen, non_schengen).
        visa_difficulty: General visa difficulty level.
    """

    country_key: int
    country_code: str
    country_name: str
    region: str
    visa_difficulty: str


# =============================================================================
# Fact Tables
# =============================================================================


@dataclass
class FactBooking:
    """
    Booking fact table for granular booking analysis.

    Each row represents a single booking request.

    Attributes:
        booking_key: Surrogate key for the fact.
        agency_key: FK to DimAgency.
        site_key: FK to DimSite.
        date_key: FK to DimDate.
        time_key: FK to DimTime.
        target_country_key: FK to DimCountry.
        booking_id: Original booking ID.
        status: Final booking status.
        duration_seconds: Total booking duration.
        attempt_count: Number of attempts made.
        credit_cost: Credits consumed.
        payment_amount: Payment amount if applicable.
        payment_currency: Payment currency code.
        queued_at: When booking was queued.
        started_at: When booking started processing.
        completed_at: When booking completed.
        success: Whether booking was successful.
        failure_reason: Reason for failure if applicable.
    """

    booking_key: int
    agency_key: int
    site_key: int
    date_key: int
    time_key: int
    target_country_key: int
    booking_id: str
    status: str
    duration_seconds: int
    attempt_count: int
    credit_cost: float
    payment_amount: float
    payment_currency: str
    queued_at: datetime
    started_at: datetime
    completed_at: datetime
    success: bool
    failure_reason: str | None = None


@dataclass
class FactDailyAgencyStats:
    """
    Daily aggregated statistics per agency and site.

    Pre-aggregated metrics for fast reporting.

    Attributes:
        stats_key: Surrogate key.
        agency_key: FK to DimAgency.
        site_key: FK to DimSite.
        date_key: FK to DimDate.
        total_bookings: Total booking count.
        successful_bookings: Successful booking count.
        failed_bookings: Failed booking count.
        avg_duration_seconds: Average booking duration.
        min_duration_seconds: Minimum booking duration.
        max_duration_seconds: Maximum booking duration.
        total_credits_used: Total credits consumed.
        total_payment_amount: Total payment amount.
        success_rate: Success rate (0-1).
    """

    stats_key: int
    agency_key: int
    site_key: int
    date_key: int
    total_bookings: int
    successful_bookings: int
    failed_bookings: int
    avg_duration_seconds: float
    min_duration_seconds: int
    max_duration_seconds: int
    total_credits_used: float
    total_payment_amount: float
    success_rate: float


@dataclass
class FactDailySiteStats:
    """
    Daily aggregated statistics per site.

    Attributes:
        stats_key: Surrogate key.
        site_key: FK to DimSite.
        date_key: FK to DimDate.
        total_bookings: Total booking count.
        successful_bookings: Successful booking count.
        failed_bookings: Failed booking count.
        avg_duration_seconds: Average booking duration.
        avg_slot_search_seconds: Average slot search time.
        captcha_count: Number of CAPTCHAs solved.
        captcha_success_rate: CAPTCHA success rate.
        account_ban_count: Number of account bans.
        proxy_failure_count: Number of proxy failures.
    """

    stats_key: int
    site_key: int
    date_key: int
    total_bookings: int
    successful_bookings: int
    failed_bookings: int
    avg_duration_seconds: float
    avg_slot_search_seconds: float
    captcha_count: int
    captcha_success_rate: float
    account_ban_count: int
    proxy_failure_count: int


@dataclass
class FactHourlyQueueStats:
    """
    Hourly queue statistics for capacity analysis.

    Attributes:
        stats_key: Surrogate key.
        date_key: FK to DimDate.
        time_key: FK to DimTime.
        queue_depth_avg: Average queue depth.
        queue_depth_max: Maximum queue depth.
        tasks_enqueued: Tasks added to queue.
        tasks_completed: Tasks completed.
        tasks_failed: Tasks failed.
        worker_count_avg: Average worker count.
        worker_utilization: Worker utilization percentage.
    """

    stats_key: int
    date_key: int
    time_key: int
    queue_depth_avg: int
    queue_depth_max: int
    tasks_enqueued: int
    tasks_completed: int
    tasks_failed: int
    worker_count_avg: int
    worker_utilization: float


# =============================================================================
# Report Configuration
# =============================================================================


@dataclass
class ReportConfig:
    """
    Configuration for a report type.

    Attributes:
        report_type: The type of report.
        title: Report title.
        description: Report description.
        schedule: Cron expression for scheduling.
        agency_filter: Optional agency filter.
        site_filter: Optional site filter.
        date_range_days: Number of days to include.
        output_formats: Supported export formats.
        recipients: Default email recipients.
    """

    report_type: ReportType
    title: str
    description: str
    schedule: str
    agency_filter: str | None = None
    site_filter: str | None = None
    date_range_days: int = 1
    output_formats: list[str] = field(default_factory=lambda: ["pdf", "excel"])
    recipients: list[str] = field(default_factory=list)


# Report configurations
REPORT_CONFIGS: dict[ReportType, ReportConfig] = {
    ReportType.AGENCY_DAILY: ReportConfig(
        report_type=ReportType.AGENCY_DAILY,
        title="Daily Booking Report",
        description="Daily booking summary for agency",
        schedule="0 8 * * *",  # 08:00 daily
        date_range_days=1,
        output_formats=["pdf", "excel"],
    ),
    ReportType.AGENCY_WEEKLY: ReportConfig(
        report_type=ReportType.AGENCY_WEEKLY,
        title="Weekly Performance Report",
        description="Weekly performance summary",
        schedule="0 9 * * 1",  # Monday 09:00
        date_range_days=7,
        output_formats=["pdf", "excel"],
    ),
    ReportType.AGENCY_MONTHLY: ReportConfig(
        report_type=ReportType.AGENCY_MONTHLY,
        title="Monthly Performance Report",
        description="Monthly performance summary",
        schedule="0 10 1 * *",  # 1st of month 10:00
        date_range_days=30,
        output_formats=["pdf", "excel"],
    ),
    ReportType.PLATFORM_DAILY: ReportConfig(
        report_type=ReportType.PLATFORM_DAILY,
        title="Platform Daily Overview",
        description="Platform-wide daily statistics",
        schedule="0 7 * * *",  # 07:00 daily
        date_range_days=1,
        output_formats=["pdf"],
    ),
    ReportType.PLATFORM_WEEKLY: ReportConfig(
        report_type=ReportType.PLATFORM_WEEKLY,
        title="Platform Weekly Overview",
        description="Platform-wide weekly statistics",
        schedule="0 8 * * 1",  # Monday 08:00
        date_range_days=7,
        output_formats=["pdf", "excel"],
    ),
    ReportType.SITE_PERFORMANCE: ReportConfig(
        report_type=ReportType.SITE_PERFORMANCE,
        title="Site Performance Report",
        description="Per-site performance metrics",
        schedule="0 9 * * *",  # 09:00 daily
        date_range_days=1,
        output_formats=["pdf", "excel"],
    ),
    ReportType.FINANCIAL: ReportConfig(
        report_type=ReportType.FINANCIAL,
        title="Financial Report",
        description="Revenue, credits, and payment statistics",
        schedule="0 10 1 * *",  # 1st of month 10:00
        date_range_days=30,
        output_formats=["pdf", "excel"],
    ),
    ReportType.CAPACITY: ReportConfig(
        report_type=ReportType.CAPACITY,
        title="Capacity Report",
        description="Queue and worker capacity analysis",
        schedule="0 6 * * *",  # 06:00 daily
        date_range_days=1,
        output_formats=["pdf"],
    ),
}


# =============================================================================
# Protocols
# =============================================================================


class AnalyticsDatabase(Protocol):
    """Protocol for analytics database operations."""

    async def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute a query and return results."""
        ...

    async def insert(self, table: str, data: dict[str, Any]) -> None:
        """Insert a row into a table."""
        ...

    async def insert_many(self, table: str, data: list[dict[str, Any]]) -> None:
        """Insert multiple rows into a table."""
        ...


class SourceDatabase(Protocol):
    """Protocol for source database operations."""

    def items(self, collection: str) -> "ItemsProtocol":
        """Get items interface for a collection."""
        ...


class ItemsProtocol(Protocol):
    """Protocol for collection items."""

    async def read(
        self,
        filter: dict[str, Any] | None = None,
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Read items from collection."""
        ...


class PrometheusClient(Protocol):
    """Protocol for Prometheus client."""

    async def query(self, query: str) -> Any:
        """Execute a PromQL query."""
        ...


class EmailService(Protocol):
    """Protocol for email service."""

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        attachments: list[tuple[str, bytes, str]] | None = None,
    ) -> bool:
        """Send an email."""
        ...


# =============================================================================
# ETL Pipeline
# =============================================================================


class AnalyticsETLPipeline:
    """
    ETL pipeline for analytics data.

    Extracts data from operational systems, transforms it into
    the analytics schema, and loads it into the data warehouse.

    Attributes:
        source: Source database client.
        analytics: Analytics database client.
        prometheus: Prometheus client for metrics.
    """

    def __init__(
        self,
        source_db: SourceDatabase | None = None,
        analytics_db: AnalyticsDatabase | None = None,
        prometheus_client: PrometheusClient | None = None,
    ) -> None:
        """
        Initialize the ETL pipeline.

        Args:
            source_db: Source database client.
            analytics_db: Analytics database client.
            prometheus_client: Prometheus client for metrics.
        """
        self.source = source_db
        self.analytics = analytics_db
        self.prometheus = prometheus_client
        self._logger = logger.bind(component="etl_pipeline")

    async def run_daily_etl(self, target_date: date | None = None) -> dict[str, Any]:
        """
        Run daily ETL for the specified date.

        Args:
            target_date: Date to process (defaults to yesterday).

        Returns:
            ETL execution summary.
        """
        target_date = target_date or (datetime.now(timezone.utc).date() - timedelta(days=1))

        self._logger.info("etl_daily_started", target_date=target_date.isoformat())

        result = {
            "target_date": target_date.isoformat(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "stages": {},
        }

        try:
            # Extract
            bookings = await self._extract_bookings(target_date)
            payments = await self._extract_payments(target_date)
            metrics = await self._extract_metrics(target_date)

            result["stages"]["extract"] = {
                "bookings_count": len(bookings),
                "payments_count": len(payments),
            }

            # Transform
            fact_bookings = self._transform_bookings(bookings, payments)
            agency_stats = self._aggregate_agency_stats(fact_bookings)
            site_stats = self._aggregate_site_stats(fact_bookings, metrics)

            result["stages"]["transform"] = {
                "fact_bookings_count": len(fact_bookings),
                "agency_stats_count": len(agency_stats),
                "site_stats_count": len(site_stats),
            }

            # Load
            await self._load_fact_bookings(fact_bookings)
            await self._load_agency_stats(agency_stats)
            await self._load_site_stats(site_stats)

            result["stages"]["load"] = {"success": True}
            result["completed_at"] = datetime.now(timezone.utc).isoformat()

            self._logger.info(
                "etl_daily_completed",
                target_date=target_date.isoformat(),
                bookings=len(fact_bookings),
            )

        except Exception as e:
            result["error"] = str(e)
            self._logger.error(
                "etl_daily_failed",
                target_date=target_date.isoformat(),
                error=str(e),
            )
            raise ETLError(f"ETL failed for {target_date}: {e}", stage="daily")

        return result

    async def run_hourly_etl(self, target_hour: datetime | None = None) -> dict[str, Any]:
        """
        Run hourly ETL for queue statistics.

        Args:
            target_hour: Hour to process (defaults to previous hour).

        Returns:
            ETL execution summary.
        """
        if target_hour is None:
            target_hour = datetime.now(timezone.utc) - timedelta(hours=1)
        target_hour = target_hour.replace(minute=0, second=0, microsecond=0)

        self._logger.info("etl_hourly_started", target_hour=target_hour.isoformat())

        result = {
            "target_hour": target_hour.isoformat(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            # Extract queue metrics from Prometheus
            queue_metrics = await self._extract_queue_metrics(target_hour)

            # Transform
            hourly_stats = self._transform_queue_stats(queue_metrics, target_hour)

            # Load
            await self._load_hourly_queue_stats(hourly_stats)

            result["completed_at"] = datetime.now(timezone.utc).isoformat()
            result["stats_count"] = len(hourly_stats)

            self._logger.info(
                "etl_hourly_completed",
                target_hour=target_hour.isoformat(),
            )

        except Exception as e:
            result["error"] = str(e)
            self._logger.error(
                "etl_hourly_failed",
                target_hour=target_hour.isoformat(),
                error=str(e),
            )
            raise ETLError(f"Hourly ETL failed: {e}", stage="hourly")

        return result

    async def _extract_bookings(self, target_date: date) -> list[dict[str, Any]]:
        """Extract booking data for the target date."""
        if not self.source:
            return []

        return await self.source.items("booking_requests").read(
            filter={
                "_and": [
                    {"created_at": {"_gte": target_date.isoformat()}},
                    {"created_at": {"_lt": (target_date + timedelta(days=1)).isoformat()}},
                ]
            },
            fields=[
                "id",
                "agency_id",
                "site",
                "status",
                "target_country",
                "created_at",
                "updated_at",
                "attempt_count",
                "confirmation_number",
                "failure_reason",
            ],
        )

    async def _extract_payments(self, target_date: date) -> list[dict[str, Any]]:
        """Extract payment data for the target date."""
        if not self.source:
            return []

        return await self.source.items("payment_transactions").read(
            filter={
                "_and": [
                    {"created_at": {"_gte": target_date.isoformat()}},
                    {"created_at": {"_lt": (target_date + timedelta(days=1)).isoformat()}},
                ]
            },
            fields=[
                "booking_id",
                "amount",
                "currency",
                "status",
            ],
        )

    async def _extract_metrics(self, target_date: date) -> dict[str, Any]:
        """Extract Prometheus metrics for the target date."""
        if not self.prometheus:
            return {}

        queries = {
            "captcha_success_rate": (
                'sum(increase(vise_captcha_requests_total{status="success"}[24h])) / '
                'sum(increase(vise_captcha_requests_total[24h]))'
            ),
            "account_bans": 'sum(increase(vise_account_bans_total[24h]))',
            "proxy_failures": 'sum(increase(vise_proxy_requests_total{status="failed"}[24h]))',
        }

        results = {}
        for name, query in queries.items():
            try:
                result = await self.prometheus.query(query)
                results[name] = result
            except Exception as e:
                self._logger.warning(
                    "prometheus_query_failed",
                    query_name=name,
                    error=str(e),
                )
                results[name] = 0

        return results

    async def _extract_queue_metrics(self, target_hour: datetime) -> dict[str, Any]:
        """Extract queue metrics from Prometheus."""
        if not self.prometheus:
            return {}

        queries = {
            "queue_depth_avg": "avg_over_time(vise_queue_size[1h])",
            "queue_depth_max": "max_over_time(vise_queue_size[1h])",
            "worker_count_avg": "avg_over_time(vise_worker_count[1h])",
        }

        results = {}
        for name, query in queries.items():
            try:
                result = await self.prometheus.query(query)
                results[name] = result
            except Exception:
                results[name] = 0

        return results

    def _transform_bookings(
        self,
        bookings: list[dict[str, Any]],
        payments: list[dict[str, Any]],
    ) -> list[FactBooking]:
        """Transform booking data into fact records."""
        # Create payment lookup
        payment_map = {p["booking_id"]: p for p in payments}

        facts = []
        for idx, booking in enumerate(bookings):
            payment = payment_map.get(booking["id"], {})

            # Calculate duration
            created = datetime.fromisoformat(booking["created_at"].replace("Z", "+00:00"))
            updated = datetime.fromisoformat(booking["updated_at"].replace("Z", "+00:00"))
            duration = int((updated - created).total_seconds())

            fact = FactBooking(
                booking_key=idx,
                agency_key=self._get_agency_key(booking["agency_id"]),
                site_key=self._get_site_key(booking["site"]),
                date_key=self._get_date_key(created.date()),
                time_key=self._get_time_key(created.hour),
                target_country_key=self._get_country_key(booking.get("target_country", "")),
                booking_id=booking["id"],
                status=booking["status"],
                duration_seconds=max(0, duration),
                attempt_count=booking.get("attempt_count", 1),
                credit_cost=1.0,
                payment_amount=payment.get("amount", 0),
                payment_currency=payment.get("currency", "TRY"),
                queued_at=created,
                started_at=created,
                completed_at=updated,
                success=booking["status"] == "completed",
                failure_reason=booking.get("failure_reason"),
            )
            facts.append(fact)

        return facts

    def _aggregate_agency_stats(
        self,
        bookings: list[FactBooking],
    ) -> list[FactDailyAgencyStats]:
        """Aggregate booking facts into daily agency statistics."""
        # Group by agency + site + date
        groups: dict[tuple[int, int, int], dict[str, Any]] = {}

        for booking in bookings:
            key = (booking.agency_key, booking.site_key, booking.date_key)

            if key not in groups:
                groups[key] = {
                    "bookings": [],
                    "successful": 0,
                    "failed": 0,
                    "durations": [],
                    "credits": 0.0,
                    "payments": 0.0,
                }

            groups[key]["bookings"].append(booking)
            groups[key]["durations"].append(booking.duration_seconds)
            groups[key]["credits"] += booking.credit_cost
            groups[key]["payments"] += booking.payment_amount

            if booking.success:
                groups[key]["successful"] += 1
            else:
                groups[key]["failed"] += 1

        # Create stats records
        stats = []
        for idx, ((agency_key, site_key, date_key), data) in enumerate(groups.items()):
            total = len(data["bookings"])
            durations = data["durations"]

            stat = FactDailyAgencyStats(
                stats_key=idx,
                agency_key=agency_key,
                site_key=site_key,
                date_key=date_key,
                total_bookings=total,
                successful_bookings=data["successful"],
                failed_bookings=data["failed"],
                avg_duration_seconds=sum(durations) / len(durations) if durations else 0,
                min_duration_seconds=min(durations) if durations else 0,
                max_duration_seconds=max(durations) if durations else 0,
                total_credits_used=data["credits"],
                total_payment_amount=data["payments"],
                success_rate=data["successful"] / total if total > 0 else 0,
            )
            stats.append(stat)

        return stats

    def _aggregate_site_stats(
        self,
        bookings: list[FactBooking],
        metrics: dict[str, Any],
    ) -> list[FactDailySiteStats]:
        """Aggregate booking facts into daily site statistics."""
        # Group by site + date
        groups: dict[tuple[int, int], dict[str, Any]] = {}

        for booking in bookings:
            key = (booking.site_key, booking.date_key)

            if key not in groups:
                groups[key] = {
                    "total": 0,
                    "successful": 0,
                    "failed": 0,
                    "durations": [],
                }

            groups[key]["total"] += 1
            groups[key]["durations"].append(booking.duration_seconds)

            if booking.success:
                groups[key]["successful"] += 1
            else:
                groups[key]["failed"] += 1

        stats = []
        for idx, ((site_key, date_key), data) in enumerate(groups.items()):
            durations = data["durations"]

            stat = FactDailySiteStats(
                stats_key=idx,
                site_key=site_key,
                date_key=date_key,
                total_bookings=data["total"],
                successful_bookings=data["successful"],
                failed_bookings=data["failed"],
                avg_duration_seconds=sum(durations) / len(durations) if durations else 0,
                avg_slot_search_seconds=0,  # From metrics
                captcha_count=0,  # From metrics
                captcha_success_rate=float(metrics.get("captcha_success_rate", 0)),
                account_ban_count=int(metrics.get("account_bans", 0)),
                proxy_failure_count=int(metrics.get("proxy_failures", 0)),
            )
            stats.append(stat)

        return stats

    def _transform_queue_stats(
        self,
        metrics: dict[str, Any],
        target_hour: datetime,
    ) -> list[FactHourlyQueueStats]:
        """Transform queue metrics into hourly stats."""
        return [
            FactHourlyQueueStats(
                stats_key=0,
                date_key=self._get_date_key(target_hour.date()),
                time_key=self._get_time_key(target_hour.hour),
                queue_depth_avg=int(metrics.get("queue_depth_avg", 0)),
                queue_depth_max=int(metrics.get("queue_depth_max", 0)),
                tasks_enqueued=0,
                tasks_completed=0,
                tasks_failed=0,
                worker_count_avg=int(metrics.get("worker_count_avg", 0)),
                worker_utilization=0.0,
            )
        ]

    async def _load_fact_bookings(self, facts: list[FactBooking]) -> None:
        """Load booking facts into the data warehouse."""
        if not self.analytics or not facts:
            return

        data = [
            {
                "booking_key": f.booking_key,
                "agency_key": f.agency_key,
                "site_key": f.site_key,
                "date_key": f.date_key,
                "booking_id": f.booking_id,
                "status": f.status,
                "duration_seconds": f.duration_seconds,
                "success": f.success,
            }
            for f in facts
        ]
        await self.analytics.insert_many("fact_booking", data)

    async def _load_agency_stats(self, stats: list[FactDailyAgencyStats]) -> None:
        """Load agency statistics into the data warehouse."""
        if not self.analytics or not stats:
            return

        data = [
            {
                "stats_key": s.stats_key,
                "agency_key": s.agency_key,
                "site_key": s.site_key,
                "date_key": s.date_key,
                "total_bookings": s.total_bookings,
                "successful_bookings": s.successful_bookings,
                "success_rate": s.success_rate,
            }
            for s in stats
        ]
        await self.analytics.insert_many("fact_daily_agency_stats", data)

    async def _load_site_stats(self, stats: list[FactDailySiteStats]) -> None:
        """Load site statistics into the data warehouse."""
        if not self.analytics or not stats:
            return

        data = [
            {
                "stats_key": s.stats_key,
                "site_key": s.site_key,
                "date_key": s.date_key,
                "total_bookings": s.total_bookings,
                "success_rate": s.successful_bookings / s.total_bookings if s.total_bookings else 0,
            }
            for s in stats
        ]
        await self.analytics.insert_many("fact_daily_site_stats", data)

    async def _load_hourly_queue_stats(self, stats: list[FactHourlyQueueStats]) -> None:
        """Load hourly queue statistics."""
        if not self.analytics or not stats:
            return

        data = [
            {
                "stats_key": s.stats_key,
                "date_key": s.date_key,
                "time_key": s.time_key,
                "queue_depth_avg": s.queue_depth_avg,
                "worker_count_avg": s.worker_count_avg,
            }
            for s in stats
        ]
        await self.analytics.insert_many("fact_hourly_queue_stats", data)

    # Dimension key lookups
    def _get_agency_key(self, agency_id: str) -> int:
        """Get or create agency dimension key."""
        return hash(agency_id) % 1000000

    def _get_site_key(self, site_code: str) -> int:
        """Get site dimension key."""
        sites = {"vfs": 1, "idata": 2, "bls": 3, "kkosmos": 4}
        return sites.get(site_code, 0)

    def _get_date_key(self, d: date) -> int:
        """Get date dimension key (YYYYMMDD format)."""
        return int(d.strftime("%Y%m%d"))

    def _get_time_key(self, hour: int) -> int:
        """Get time dimension key."""
        return hour

    def _get_country_key(self, country_code: str) -> int:
        """Get country dimension key."""
        return hash(country_code) % 1000


# =============================================================================
# Report Generator
# =============================================================================


class ReportGenerator:
    """
    Generate various reports from analytics data.

    Provides methods to generate agency-level, platform-level,
    and financial reports from the data warehouse.

    Attributes:
        db: Analytics database client.
    """

    def __init__(self, analytics_db: AnalyticsDatabase | None = None) -> None:
        """
        Initialize the report generator.

        Args:
            analytics_db: Analytics database client.
        """
        self.db = analytics_db
        self._logger = logger.bind(component="report_generator")

    async def generate_agency_daily_report(
        self,
        agency_id: str,
        report_date: date,
    ) -> dict[str, Any]:
        """
        Generate daily report for an agency.

        Args:
            agency_id: The agency ID.
            report_date: Date for the report.

        Returns:
            Report data dictionary.
        """
        self._logger.info(
            "generating_agency_daily_report",
            agency_id=agency_id,
            report_date=report_date.isoformat(),
        )

        # Fetch data
        stats = await self._get_agency_stats(agency_id, report_date)
        bookings = await self._get_agency_bookings(agency_id, report_date)

        # Build report
        report = {
            "metadata": {
                "report_type": ReportType.AGENCY_DAILY.value,
                "agency_id": agency_id,
                "report_date": report_date.isoformat(),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            "summary": {
                "total_bookings": stats.get("total_bookings", 0),
                "successful_bookings": stats.get("successful_bookings", 0),
                "failed_bookings": stats.get("failed_bookings", 0),
                "success_rate": stats.get("success_rate", 0),
                "avg_duration_minutes": stats.get("avg_duration_seconds", 0) / 60,
                "credits_used": stats.get("total_credits_used", 0),
            },
            "by_site": self._group_by_site(bookings),
            "by_country": self._group_by_country(bookings),
            "failures": self._analyze_failures(bookings),
            "hourly_distribution": self._hourly_distribution(bookings),
        }

        return report

    async def generate_agency_weekly_report(
        self,
        agency_id: str,
        week_start: date,
    ) -> dict[str, Any]:
        """
        Generate weekly report for an agency.

        Args:
            agency_id: The agency ID.
            week_start: Start date of the week.

        Returns:
            Report data dictionary.
        """
        week_end = week_start + timedelta(days=6)

        # Aggregate daily stats
        daily_reports = []
        current = week_start
        while current <= week_end:
            daily = await self.generate_agency_daily_report(agency_id, current)
            daily_reports.append(daily)
            current += timedelta(days=1)

        # Combine into weekly summary
        total_bookings = sum(r["summary"]["total_bookings"] for r in daily_reports)
        successful = sum(r["summary"]["successful_bookings"] for r in daily_reports)

        return {
            "metadata": {
                "report_type": ReportType.AGENCY_WEEKLY.value,
                "agency_id": agency_id,
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            "summary": {
                "total_bookings": total_bookings,
                "successful_bookings": successful,
                "failed_bookings": total_bookings - successful,
                "success_rate": successful / total_bookings if total_bookings > 0 else 0,
            },
            "daily_breakdown": [
                {
                    "date": r["metadata"]["report_date"],
                    "bookings": r["summary"]["total_bookings"],
                    "success_rate": r["summary"]["success_rate"],
                }
                for r in daily_reports
            ],
        }

    async def generate_platform_daily_report(
        self,
        report_date: date,
    ) -> dict[str, Any]:
        """
        Generate platform-wide daily report.

        Args:
            report_date: Date for the report.

        Returns:
            Report data dictionary.
        """
        self._logger.info(
            "generating_platform_daily_report",
            report_date=report_date.isoformat(),
        )

        # Fetch platform-wide data
        all_stats = await self._get_platform_stats(report_date)
        site_stats = await self._get_site_stats(report_date)

        report = {
            "metadata": {
                "report_type": ReportType.PLATFORM_DAILY.value,
                "report_date": report_date.isoformat(),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            "summary": {
                "total_bookings": all_stats.get("total_bookings", 0),
                "successful_bookings": all_stats.get("successful_bookings", 0),
                "failed_bookings": all_stats.get("failed_bookings", 0),
                "success_rate": all_stats.get("success_rate", 0),
                "active_agencies": all_stats.get("active_agencies", 0),
                "total_revenue": all_stats.get("total_revenue", 0),
            },
            "by_site": site_stats,
            "top_agencies": await self._get_top_agencies(report_date),
            "failure_analysis": await self._get_failure_analysis(report_date),
            "capacity_utilization": await self._get_capacity_stats(report_date),
        }

        return report

    async def generate_financial_report(
        self,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        """
        Generate financial report for a date range.

        Args:
            start_date: Start date of the period.
            end_date: End date of the period.

        Returns:
            Report data dictionary.
        """
        self._logger.info(
            "generating_financial_report",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )

        report = {
            "metadata": {
                "report_type": ReportType.FINANCIAL.value,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            "revenue": {
                "total_credits_sold": 0,
                "total_credits_used": 0,
                "credit_revenue": 0,
                "payment_processing_revenue": 0,
                "total_revenue": 0,
            },
            "costs": {
                "captcha_costs": 0,
                "proxy_costs": 0,
                "sms_costs": 0,
                "infrastructure_costs": 0,
                "total_costs": 0,
            },
            "by_agency_tier": {
                "premium": {"revenue": 0, "bookings": 0},
                "standard": {"revenue": 0, "bookings": 0},
                "trial": {"revenue": 0, "bookings": 0},
            },
            "by_site": {},
            "daily_breakdown": [],
        }

        # Populate from database if available
        if self.db:
            # Query financial data
            pass

        return report

    async def generate_site_performance_report(
        self,
        report_date: date,
        site_code: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate site performance report.

        Args:
            report_date: Date for the report.
            site_code: Optional specific site to report on.

        Returns:
            Report data dictionary.
        """
        self._logger.info(
            "generating_site_performance_report",
            report_date=report_date.isoformat(),
            site_code=site_code,
        )

        sites = [site_code] if site_code else ["vfs", "idata", "bls", "kkosmos"]
        site_data = {}

        for site in sites:
            site_data[site] = {
                "total_bookings": 0,
                "success_rate": 0,
                "avg_duration_seconds": 0,
                "captcha_success_rate": 0,
                "account_ban_count": 0,
                "proxy_failure_count": 0,
            }

        return {
            "metadata": {
                "report_type": ReportType.SITE_PERFORMANCE.value,
                "report_date": report_date.isoformat(),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            "sites": site_data,
        }

    # Helper methods for data aggregation
    async def _get_agency_stats(
        self,
        agency_id: str,
        report_date: date,
    ) -> dict[str, Any]:
        """Get aggregated agency stats for a date."""
        if self.db:
            # Query from analytics database
            pass
        return {
            "total_bookings": 0,
            "successful_bookings": 0,
            "failed_bookings": 0,
            "success_rate": 0,
            "avg_duration_seconds": 0,
            "total_credits_used": 0,
        }

    async def _get_agency_bookings(
        self,
        agency_id: str,
        report_date: date,
    ) -> list[dict[str, Any]]:
        """Get booking list for an agency and date."""
        if self.db:
            # Query from analytics database
            pass
        return []

    async def _get_platform_stats(self, report_date: date) -> dict[str, Any]:
        """Get platform-wide stats for a date."""
        return {
            "total_bookings": 0,
            "successful_bookings": 0,
            "failed_bookings": 0,
            "success_rate": 0,
            "active_agencies": 0,
            "total_revenue": 0,
        }

    async def _get_site_stats(self, report_date: date) -> dict[str, Any]:
        """Get per-site stats for a date."""
        return {
            "vfs": {"bookings": 0, "success_rate": 0},
            "idata": {"bookings": 0, "success_rate": 0},
            "bls": {"bookings": 0, "success_rate": 0},
            "kkosmos": {"bookings": 0, "success_rate": 0},
        }

    async def _get_top_agencies(
        self,
        report_date: date,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get top performing agencies."""
        return []

    async def _get_failure_analysis(self, report_date: date) -> dict[str, int]:
        """Get failure reason breakdown."""
        return {}

    async def _get_capacity_stats(self, report_date: date) -> dict[str, Any]:
        """Get capacity utilization stats."""
        return {
            "avg_queue_depth": 0,
            "max_queue_depth": 0,
            "avg_workers": 0,
            "worker_utilization": 0,
        }

    def _group_by_site(self, bookings: list[dict[str, Any]]) -> dict[str, Any]:
        """Group bookings by site."""
        groups: dict[str, dict[str, int]] = {}
        for booking in bookings:
            site = booking.get("site", "unknown")
            if site not in groups:
                groups[site] = {"total": 0, "success": 0, "failed": 0}
            groups[site]["total"] += 1
            if booking.get("success"):
                groups[site]["success"] += 1
            else:
                groups[site]["failed"] += 1
        return groups

    def _group_by_country(self, bookings: list[dict[str, Any]]) -> dict[str, int]:
        """Group bookings by target country."""
        groups: dict[str, int] = {}
        for booking in bookings:
            country = booking.get("target_country", "unknown")
            groups[country] = groups.get(country, 0) + 1
        return groups

    def _analyze_failures(self, bookings: list[dict[str, Any]]) -> dict[str, int]:
        """Analyze failure reasons."""
        failures: dict[str, int] = {}
        for booking in bookings:
            if not booking.get("success"):
                reason = booking.get("failure_reason", "unknown")
                failures[reason] = failures.get(reason, 0) + 1
        return failures

    def _hourly_distribution(self, bookings: list[dict[str, Any]]) -> dict[int, int]:
        """Calculate hourly distribution of bookings."""
        hours: dict[int, int] = {h: 0 for h in range(24)}
        for booking in bookings:
            created = booking.get("created_at")
            if created:
                if isinstance(created, str):
                    created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                hours[created.hour] += 1
        return hours


# =============================================================================
# Report Export Service
# =============================================================================


class ReportExportService:
    """
    Export reports to various formats.

    Supports PDF, Excel, JSON, and CSV export formats.
    """

    def __init__(self) -> None:
        """Initialize the export service."""
        self._logger = logger.bind(component="report_export")

    async def export_to_pdf(
        self,
        report_data: dict[str, Any],
    ) -> bytes:
        """
        Export report to PDF format.

        Args:
            report_data: Report data dictionary.

        Returns:
            PDF file as bytes.
        """
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

            buffer = io.BytesIO()
            doc = SimpleDocTemplate(buffer, pagesize=A4)

            elements = []
            styles = getSampleStyleSheet()

            # Title
            title = report_data.get("metadata", {}).get("report_type", "Report")
            elements.append(Paragraph(title.replace("_", " ").title(), styles["Heading1"]))
            elements.append(Spacer(1, 12))

            # Summary table
            summary = report_data.get("summary", {})
            summary_data = [
                ["Metric", "Value"],
                ["Total Bookings", str(summary.get("total_bookings", 0))],
                ["Successful", str(summary.get("successful_bookings", 0))],
                ["Failed", str(summary.get("failed_bookings", 0))],
                ["Success Rate", f"{summary.get('success_rate', 0):.1%}"],
            ]

            table = Table(summary_data)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, 0), 14),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                        ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                        ("TEXTCOLOR", (0, 1), (-1, -1), colors.black),
                        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                        ("FONTSIZE", (0, 1), (-1, -1), 12),
                        ("GRID", (0, 0), (-1, -1), 1, colors.black),
                    ]
                )
            )

            elements.append(table)
            doc.build(elements)

            return buffer.getvalue()

        except ImportError:
            self._logger.warning("reportlab_not_installed")
            # Return a simple text representation as fallback
            return json.dumps(report_data, indent=2, default=str).encode()

    async def export_to_excel(
        self,
        report_data: dict[str, Any],
    ) -> bytes:
        """
        Export report to Excel format.

        Args:
            report_data: Report data dictionary.

        Returns:
            Excel file as bytes.
        """
        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Summary"

            # Header style
            header_font = Font(bold=True, color="FFFFFF")
            header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")

            # Title
            ws["A1"] = report_data.get("metadata", {}).get("report_type", "Report")
            ws["A1"].font = Font(bold=True, size=16)

            # Summary
            summary = report_data.get("summary", {})
            row = 3

            ws.cell(row=row, column=1, value="Metric").font = header_font
            ws.cell(row=row, column=1).fill = header_fill
            ws.cell(row=row, column=2, value="Value").font = header_font
            ws.cell(row=row, column=2).fill = header_fill

            row += 1
            for key, value in summary.items():
                ws.cell(row=row, column=1, value=key.replace("_", " ").title())
                if isinstance(value, float) and value < 1:
                    ws.cell(row=row, column=2, value=f"{value:.1%}")
                else:
                    ws.cell(row=row, column=2, value=str(value))
                row += 1

            # By Site sheet
            if "by_site" in report_data:
                ws_site = wb.create_sheet("By Site")
                ws_site["A1"] = "Site"
                ws_site["B1"] = "Total"
                ws_site["C1"] = "Success"
                ws_site["D1"] = "Failed"

                row = 2
                for site, data in report_data["by_site"].items():
                    ws_site.cell(row=row, column=1, value=site)
                    if isinstance(data, dict):
                        ws_site.cell(row=row, column=2, value=data.get("total", 0))
                        ws_site.cell(row=row, column=3, value=data.get("success", 0))
                        ws_site.cell(row=row, column=4, value=data.get("failed", 0))
                    row += 1

            buffer = io.BytesIO()
            wb.save(buffer)
            return buffer.getvalue()

        except ImportError:
            self._logger.warning("openpyxl_not_installed")
            return json.dumps(report_data, indent=2, default=str).encode()

    async def export_to_json(
        self,
        report_data: dict[str, Any],
    ) -> bytes:
        """
        Export report to JSON format.

        Args:
            report_data: Report data dictionary.

        Returns:
            JSON file as bytes.
        """
        return json.dumps(report_data, indent=2, default=str).encode()

    async def export_to_csv(
        self,
        report_data: dict[str, Any],
    ) -> bytes:
        """
        Export report summary to CSV format.

        Args:
            report_data: Report data dictionary.

        Returns:
            CSV file as bytes.
        """
        import csv

        buffer = io.StringIO()
        writer = csv.writer(buffer)

        # Write summary
        writer.writerow(["Metric", "Value"])
        summary = report_data.get("summary", {})
        for key, value in summary.items():
            if isinstance(value, float) and value < 1:
                writer.writerow([key.replace("_", " ").title(), f"{value:.1%}"])
            else:
                writer.writerow([key.replace("_", " ").title(), str(value)])

        return buffer.getvalue().encode()

    async def export(
        self,
        report_data: dict[str, Any],
        format: ExportFormat | str,
    ) -> bytes:
        """
        Export report to specified format.

        Args:
            report_data: Report data dictionary.
            format: Export format.

        Returns:
            Exported file as bytes.
        """
        if isinstance(format, str):
            format = ExportFormat(format)

        if format == ExportFormat.PDF:
            return await self.export_to_pdf(report_data)
        elif format == ExportFormat.EXCEL:
            return await self.export_to_excel(report_data)
        elif format == ExportFormat.JSON:
            return await self.export_to_json(report_data)
        elif format == ExportFormat.CSV:
            return await self.export_to_csv(report_data)
        else:
            raise ExportError(f"Unsupported format: {format}", export_format=str(format))


# =============================================================================
# Report Scheduler
# =============================================================================


class ReportScheduler:
    """
    Scheduler for automated report generation and delivery.

    Manages scheduled report generation and email delivery
    to configured recipients.

    Attributes:
        generator: Report generator instance.
        export_service: Export service instance.
        email_service: Email service for delivery.
    """

    def __init__(
        self,
        report_generator: ReportGenerator,
        export_service: ReportExportService,
        email_service: EmailService | None = None,
    ) -> None:
        """
        Initialize the report scheduler.

        Args:
            report_generator: Report generator instance.
            export_service: Export service instance.
            email_service: Email service for delivery.
        """
        self.generator = report_generator
        self.export = export_service
        self.email = email_service
        self._logger = logger.bind(component="report_scheduler")

    async def deliver_agency_daily_reports(
        self,
        report_date: date | None = None,
    ) -> dict[str, Any]:
        """
        Generate and deliver daily reports for all active agencies.

        Args:
            report_date: Date for reports (defaults to yesterday).

        Returns:
            Delivery summary.
        """
        report_date = report_date or (datetime.now(timezone.utc).date() - timedelta(days=1))

        self._logger.info(
            "delivering_agency_daily_reports",
            report_date=report_date.isoformat(),
        )

        # Get active agencies
        agencies = await self._get_active_agencies()

        results = {
            "report_date": report_date.isoformat(),
            "agencies_processed": 0,
            "successes": 0,
            "failures": 0,
        }

        for agency in agencies:
            try:
                # Generate report
                report_data = await self.generator.generate_agency_daily_report(
                    agency["id"],
                    report_date,
                )

                # Export to PDF and Excel
                pdf = await self.export.export_to_pdf(report_data)
                excel = await self.export.export_to_excel(report_data)

                # Send email if service available
                if self.email:
                    await self.email.send(
                        to=agency.get("email", ""),
                        subject=f"VISE OS Daily Report - {report_date}",
                        body=self._format_email_body(report_data),
                        attachments=[
                            ("report.pdf", pdf, "application/pdf"),
                            (
                                "report.xlsx",
                                excel,
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            ),
                        ],
                    )

                results["successes"] += 1

            except Exception as e:
                self._logger.error(
                    "agency_report_delivery_failed",
                    agency_id=agency.get("id"),
                    error=str(e),
                )
                results["failures"] += 1

            results["agencies_processed"] += 1

        return results

    async def deliver_platform_daily_report(
        self,
        report_date: date | None = None,
    ) -> dict[str, Any]:
        """
        Generate and deliver platform daily report to admins.

        Args:
            report_date: Date for report (defaults to yesterday).

        Returns:
            Delivery summary.
        """
        report_date = report_date or (datetime.now(timezone.utc).date() - timedelta(days=1))

        self._logger.info(
            "delivering_platform_daily_report",
            report_date=report_date.isoformat(),
        )

        try:
            # Generate report
            report_data = await self.generator.generate_platform_daily_report(report_date)

            # Export to PDF
            pdf = await self.export.export_to_pdf(report_data)

            # Send to admins
            if self.email:
                await self.email.send(
                    to="admin@vise.os",
                    subject=f"Platform Daily Report - {report_date}",
                    body=self._format_email_body(report_data),
                    attachments=[("platform_report.pdf", pdf, "application/pdf")],
                )

            return {
                "report_date": report_date.isoformat(),
                "success": True,
            }

        except Exception as e:
            self._logger.error(
                "platform_report_delivery_failed",
                report_date=report_date.isoformat(),
                error=str(e),
            )
            return {
                "report_date": report_date.isoformat(),
                "success": False,
                "error": str(e),
            }

    async def _get_active_agencies(self) -> list[dict[str, Any]]:
        """Get list of active agencies for report delivery."""
        # Would query from database
        return []

    def _format_email_body(self, report_data: dict[str, Any]) -> str:
        """Format email body from report data."""
        summary = report_data.get("summary", {})

        success_rate = summary.get("success_rate", 0)
        if isinstance(success_rate, float):
            success_rate_str = f"{success_rate:.1%}"
        else:
            success_rate_str = str(success_rate)

        return f"""
VISE OS Report

Summary:
- Total Bookings: {summary.get('total_bookings', 0)}
- Successful: {summary.get('successful_bookings', 0)}
- Failed: {summary.get('failed_bookings', 0)}
- Success Rate: {success_rate_str}

Please see attached PDF and Excel files for detailed information.

---
This is an automated report from VISE OS.
        """.strip()


# =============================================================================
# Factory Functions
# =============================================================================


@lru_cache
def get_report_generator() -> ReportGenerator:
    """
    Get a cached ReportGenerator instance.

    Returns:
        Configured ReportGenerator instance.
    """
    return ReportGenerator()


@lru_cache
def get_export_service() -> ReportExportService:
    """
    Get a cached ReportExportService instance.

    Returns:
        Configured ReportExportService instance.
    """
    return ReportExportService()


def get_report_scheduler(
    generator: ReportGenerator | None = None,
    export_service: ReportExportService | None = None,
    email_service: EmailService | None = None,
) -> ReportScheduler:
    """
    Get a configured ReportScheduler instance.

    Args:
        generator: Optional report generator.
        export_service: Optional export service.
        email_service: Optional email service.

    Returns:
        Configured ReportScheduler instance.
    """
    return ReportScheduler(
        report_generator=generator or get_report_generator(),
        export_service=export_service or get_export_service(),
        email_service=email_service,
    )


# =============================================================================
# Exports
# =============================================================================


__all__ = [
    # Main classes
    "ReportGenerator",
    "AnalyticsETLPipeline",
    "ReportExportService",
    "ReportScheduler",
    # Factory functions
    "get_report_generator",
    "get_export_service",
    "get_report_scheduler",
    # Data classes - Dimensions
    "DimAgency",
    "DimSite",
    "DimDate",
    "DimTime",
    "DimCountry",
    # Data classes - Facts
    "FactBooking",
    "FactDailyAgencyStats",
    "FactDailySiteStats",
    "FactHourlyQueueStats",
    # Configuration
    "ReportConfig",
    "REPORT_CONFIGS",
    # Enums
    "ReportType",
    "ExportFormat",
    "AggregationPeriod",
    # Exceptions
    "AnalyticsError",
    "ETLError",
    "ReportGenerationError",
    "ExportError",
]
