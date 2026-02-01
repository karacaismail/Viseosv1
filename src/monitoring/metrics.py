"""
Prometheus Metrics Collection for VISE OS.

This module provides comprehensive metrics collection for monitoring the VISE OS platform.
Metrics are organized by domain: bookings, queue, browser sessions, proxy, captcha,
payment, and system health.

Usage:
    from src.monitoring.metrics import BookingMetrics, metrics

    # Record booking request
    BookingMetrics.record_request(site="vfs", agency_id="agency-1", status="success")

    # Observe booking duration
    BookingMetrics.observe_duration(site="vfs", result="completed", duration=120.5)

    # Generate metrics for Prometheus scraping
    from prometheus_client import generate_latest
    output = generate_latest(metrics.registry)
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from time import time
from typing import Any, Iterator, Optional

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Summary,
    generate_latest,
    multiprocess,
)


class MetricsRegistry:
    """
    Central registry for all Prometheus metrics.

    Handles multi-process mode for Gunicorn/Uvicorn deployments.
    Metrics are registered to a custom registry for isolation.
    """

    def __init__(self) -> None:
        """Initialize the metrics registry."""
        self._registry: Optional[CollectorRegistry] = None
        self._multiprocess_mode = bool(os.environ.get("prometheus_multiproc_dir"))

    @property
    def registry(self) -> CollectorRegistry:
        """
        Get the collector registry.

        Returns a multi-process registry if prometheus_multiproc_dir is set,
        otherwise returns a standard registry.
        """
        if self._registry is None:
            if self._multiprocess_mode:
                self._registry = CollectorRegistry()
                multiprocess.MultiProcessCollector(self._registry)
            else:
                self._registry = CollectorRegistry(auto_describe=True)
        return self._registry

    def generate_metrics(self) -> bytes:
        """
        Generate metrics output for Prometheus scraping.

        Returns:
            Prometheus-formatted metrics as bytes.
        """
        return generate_latest(self.registry)


# Global metrics registry
metrics = MetricsRegistry()
REGISTRY = metrics.registry


# ============================================
# BOOKING METRICS
# ============================================

booking_requests_total = Counter(
    "vise_booking_requests_total",
    "Total booking requests",
    ["site", "agency_id", "status"],
    registry=REGISTRY,
)

booking_state_transitions = Counter(
    "vise_booking_state_transitions_total",
    "Booking state transitions",
    ["site", "from_state", "to_state"],
    registry=REGISTRY,
)

active_bookings = Gauge(
    "vise_active_bookings",
    "Currently active bookings",
    ["site", "state"],
    registry=REGISTRY,
)

booking_duration_seconds = Histogram(
    "vise_booking_duration_seconds",
    "Booking completion time",
    ["site", "result"],
    buckets=[30, 60, 120, 180, 300, 600, 900, 1800],
    registry=REGISTRY,
)

slot_search_duration_seconds = Histogram(
    "vise_slot_search_duration_seconds",
    "Time to find available slot",
    ["site"],
    buckets=[5, 10, 30, 60, 120, 300, 600],
    registry=REGISTRY,
)

booking_attempts_total = Counter(
    "vise_booking_attempts_total",
    "Total booking attempts",
    ["site", "result"],
    registry=REGISTRY,
)


# ============================================
# QUEUE METRICS
# ============================================

queue_size = Gauge(
    "vise_queue_size",
    "Queue size by queue name",
    ["queue_name"],
    registry=REGISTRY,
)

queue_processing_time = Histogram(
    "vise_queue_processing_seconds",
    "Task processing time",
    ["queue_name", "task_type"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600],
    registry=REGISTRY,
)

worker_count = Gauge(
    "vise_worker_count",
    "Active workers by pool",
    ["pool_name"],
    registry=REGISTRY,
)

task_retries = Counter(
    "vise_task_retries_total",
    "Task retry count",
    ["task_type", "reason"],
    registry=REGISTRY,
)

task_failures = Counter(
    "vise_task_failures_total",
    "Task failure count",
    ["task_type", "reason"],
    registry=REGISTRY,
)


# ============================================
# BROWSER SESSION METRICS
# ============================================

browser_sessions_active = Gauge(
    "vise_browser_sessions_active",
    "Active browser sessions",
    ["site"],
    registry=REGISTRY,
)

browser_session_duration = Histogram(
    "vise_browser_session_duration_seconds",
    "Browser session duration",
    ["site"],
    buckets=[60, 120, 300, 600, 900, 1800],
    registry=REGISTRY,
)

page_load_time = Histogram(
    "vise_page_load_seconds",
    "Page load time",
    ["site", "page_type"],
    buckets=[1, 2, 5, 10, 20, 30, 60],
    registry=REGISTRY,
)

browser_errors = Counter(
    "vise_browser_errors_total",
    "Browser error count",
    ["site", "error_type"],
    registry=REGISTRY,
)


# ============================================
# PROXY METRICS
# ============================================

proxy_pool_size = Gauge(
    "vise_proxy_pool_size",
    "Proxy pool size by status",
    ["provider", "status"],
    registry=REGISTRY,
)

proxy_success_rate = Gauge(
    "vise_proxy_success_rate",
    "Proxy success rate",
    ["provider"],
    registry=REGISTRY,
)

proxy_response_time = Histogram(
    "vise_proxy_response_seconds",
    "Proxy response time",
    ["provider"],
    buckets=[0.5, 1, 2, 5, 10, 20],
    registry=REGISTRY,
)

proxy_requests = Counter(
    "vise_proxy_requests_total",
    "Proxy requests by outcome",
    ["provider", "status"],
    registry=REGISTRY,
)

proxy_rotations = Counter(
    "vise_proxy_rotations_total",
    "Proxy rotation events",
    ["provider", "reason"],
    registry=REGISTRY,
)


# ============================================
# CAPTCHA METRICS
# ============================================

captcha_requests = Counter(
    "vise_captcha_requests_total",
    "CAPTCHA solve requests",
    ["provider", "captcha_type", "status"],
    registry=REGISTRY,
)

captcha_solve_time = Histogram(
    "vise_captcha_solve_seconds",
    "CAPTCHA solve time",
    ["provider", "captcha_type"],
    buckets=[5, 10, 20, 30, 60, 90, 120],
    registry=REGISTRY,
)

captcha_balance = Gauge(
    "vise_captcha_balance_usd",
    "CAPTCHA service balance in USD",
    ["provider"],
    registry=REGISTRY,
)


# ============================================
# ACCOUNT POOL METRICS
# ============================================

account_pool_size = Gauge(
    "vise_account_pool_size",
    "Account pool size by status",
    ["site", "status"],
    registry=REGISTRY,
)

account_usage = Counter(
    "vise_account_usage_total",
    "Account usage count",
    ["site", "result"],
    registry=REGISTRY,
)

account_bans = Counter(
    "vise_account_bans_total",
    "Account ban events",
    ["site", "reason"],
    registry=REGISTRY,
)


# ============================================
# PAYMENT METRICS
# ============================================

payment_requests = Counter(
    "vise_payment_requests_total",
    "Payment requests by outcome",
    ["gateway", "status"],
    registry=REGISTRY,
)

payment_amount = Counter(
    "vise_payment_amount_total",
    "Total payment amount processed",
    ["gateway", "currency"],
    registry=REGISTRY,
)

payment_duration = Histogram(
    "vise_payment_duration_seconds",
    "Payment processing time",
    ["gateway"],
    buckets=[1, 2, 5, 10, 30, 60, 120],
    registry=REGISTRY,
)

three_ds_attempts = Counter(
    "vise_3ds_attempts_total",
    "3DS authentication attempts",
    ["gateway", "status"],
    registry=REGISTRY,
)


# ============================================
# CREDIT METRICS
# ============================================

credit_balance = Gauge(
    "vise_credit_balance",
    "Agency credit balance",
    ["agency_id"],
    registry=REGISTRY,
)

credit_transactions = Counter(
    "vise_credit_transactions_total",
    "Credit transactions",
    ["agency_id", "type"],
    registry=REGISTRY,
)


# ============================================
# SYSTEM HEALTH METRICS
# ============================================

api_requests = Counter(
    "vise_api_requests_total",
    "API requests by endpoint",
    ["method", "endpoint", "status_code"],
    registry=REGISTRY,
)

api_latency = Histogram(
    "vise_api_latency_seconds",
    "API request latency",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
    registry=REGISTRY,
)

directus_requests = Counter(
    "vise_directus_requests_total",
    "Directus API requests",
    ["collection", "operation", "status"],
    registry=REGISTRY,
)

directus_latency = Histogram(
    "vise_directus_latency_seconds",
    "Directus API latency",
    ["collection", "operation"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
    registry=REGISTRY,
)

redis_operations = Counter(
    "vise_redis_operations_total",
    "Redis operations",
    ["operation", "status"],
    registry=REGISTRY,
)


class BookingMetrics:
    """
    High-level interface for booking-related metrics.

    Provides convenient methods for recording booking metrics
    without needing to work directly with Prometheus primitives.
    """

    @staticmethod
    def record_request(
        site: str,
        agency_id: str,
        status: str,
    ) -> None:
        """
        Record a booking request.

        Args:
            site: The booking site (e.g., "vfs", "idata").
            agency_id: The agency making the request.
            status: Request status (e.g., "success", "failed").
        """
        booking_requests_total.labels(
            site=site,
            agency_id=agency_id,
            status=status,
        ).inc()

    @staticmethod
    def record_state_transition(
        site: str,
        from_state: str,
        to_state: str,
    ) -> None:
        """
        Record a booking state transition.

        Args:
            site: The booking site.
            from_state: Previous state.
            to_state: New state.
        """
        booking_state_transitions.labels(
            site=site,
            from_state=from_state,
            to_state=to_state,
        ).inc()

    @staticmethod
    def set_active_bookings(site: str, state: str, count: int) -> None:
        """
        Set the count of active bookings.

        Args:
            site: The booking site.
            state: Current booking state.
            count: Number of active bookings.
        """
        active_bookings.labels(site=site, state=state).set(count)

    @staticmethod
    def observe_duration(
        site: str,
        result: str,
        duration: float,
    ) -> None:
        """
        Record booking completion duration.

        Args:
            site: The booking site.
            result: Booking result (e.g., "completed", "failed").
            duration: Duration in seconds.
        """
        booking_duration_seconds.labels(
            site=site,
            result=result,
        ).observe(duration)

    @staticmethod
    def observe_slot_search(site: str, duration: float) -> None:
        """
        Record slot search duration.

        Args:
            site: The booking site.
            duration: Search duration in seconds.
        """
        slot_search_duration_seconds.labels(site=site).observe(duration)

    @staticmethod
    def record_attempt(site: str, result: str) -> None:
        """
        Record a booking attempt.

        Args:
            site: The booking site.
            result: Attempt result.
        """
        booking_attempts_total.labels(site=site, result=result).inc()

    @staticmethod
    @contextmanager
    def track_duration(site: str, result: str = "completed") -> Iterator[None]:
        """
        Context manager to track booking duration.

        Args:
            site: The booking site.
            result: Expected result (can be modified before exit).

        Yields:
            None

        Example:
            with BookingMetrics.track_duration("vfs"):
                await process_booking()
        """
        start = time()
        try:
            yield
        finally:
            duration = time() - start
            BookingMetrics.observe_duration(site, result, duration)


class QueueMetrics:
    """High-level interface for queue-related metrics."""

    @staticmethod
    def set_queue_size(queue_name: str, size: int) -> None:
        """Set the current queue size."""
        queue_size.labels(queue_name=queue_name).set(size)

    @staticmethod
    def observe_processing_time(
        queue_name: str,
        task_type: str,
        duration: float,
    ) -> None:
        """Record task processing time."""
        queue_processing_time.labels(
            queue_name=queue_name,
            task_type=task_type,
        ).observe(duration)

    @staticmethod
    def set_worker_count(pool_name: str, count: int) -> None:
        """Set the active worker count."""
        worker_count.labels(pool_name=pool_name).set(count)

    @staticmethod
    def record_retry(task_type: str, reason: str) -> None:
        """Record a task retry."""
        task_retries.labels(task_type=task_type, reason=reason).inc()

    @staticmethod
    def record_failure(task_type: str, reason: str) -> None:
        """Record a task failure."""
        task_failures.labels(task_type=task_type, reason=reason).inc()


class BrowserMetrics:
    """High-level interface for browser session metrics."""

    @staticmethod
    def set_active_sessions(site: str, count: int) -> None:
        """Set the count of active browser sessions."""
        browser_sessions_active.labels(site=site).set(count)

    @staticmethod
    def observe_session_duration(site: str, duration: float) -> None:
        """Record browser session duration."""
        browser_session_duration.labels(site=site).observe(duration)

    @staticmethod
    def observe_page_load(site: str, page_type: str, duration: float) -> None:
        """Record page load time."""
        page_load_time.labels(site=site, page_type=page_type).observe(duration)

    @staticmethod
    def record_error(site: str, error_type: str) -> None:
        """Record a browser error."""
        browser_errors.labels(site=site, error_type=error_type).inc()


class ProxyMetrics:
    """High-level interface for proxy metrics."""

    @staticmethod
    def set_pool_size(provider: str, status: str, size: int) -> None:
        """Set proxy pool size by status."""
        proxy_pool_size.labels(provider=provider, status=status).set(size)

    @staticmethod
    def set_success_rate(provider: str, rate: float) -> None:
        """Set proxy success rate (0-1)."""
        proxy_success_rate.labels(provider=provider).set(rate)

    @staticmethod
    def observe_response_time(provider: str, duration: float) -> None:
        """Record proxy response time."""
        proxy_response_time.labels(provider=provider).observe(duration)

    @staticmethod
    def record_request(provider: str, status: str) -> None:
        """Record a proxy request."""
        proxy_requests.labels(provider=provider, status=status).inc()

    @staticmethod
    def record_rotation(provider: str, reason: str) -> None:
        """Record a proxy rotation event."""
        proxy_rotations.labels(provider=provider, reason=reason).inc()


class CaptchaMetrics:
    """High-level interface for CAPTCHA metrics."""

    @staticmethod
    def record_request(
        provider: str,
        captcha_type: str,
        status: str,
    ) -> None:
        """Record a CAPTCHA solve request."""
        captcha_requests.labels(
            provider=provider,
            captcha_type=captcha_type,
            status=status,
        ).inc()

    @staticmethod
    def observe_solve_time(
        provider: str,
        captcha_type: str,
        duration: float,
    ) -> None:
        """Record CAPTCHA solve time."""
        captcha_solve_time.labels(
            provider=provider,
            captcha_type=captcha_type,
        ).observe(duration)

    @staticmethod
    def set_balance(provider: str, balance: float) -> None:
        """Set CAPTCHA service balance."""
        captcha_balance.labels(provider=provider).set(balance)


class AccountMetrics:
    """High-level interface for account pool metrics."""

    @staticmethod
    def set_pool_size(site: str, status: str, size: int) -> None:
        """Set account pool size by status."""
        account_pool_size.labels(site=site, status=status).set(size)

    @staticmethod
    def record_usage(site: str, result: str) -> None:
        """Record account usage."""
        account_usage.labels(site=site, result=result).inc()

    @staticmethod
    def record_ban(site: str, reason: str) -> None:
        """Record an account ban event."""
        account_bans.labels(site=site, reason=reason).inc()


class PaymentMetrics:
    """High-level interface for payment metrics."""

    @staticmethod
    def record_request(gateway: str, status: str) -> None:
        """Record a payment request."""
        payment_requests.labels(gateway=gateway, status=status).inc()

    @staticmethod
    def record_amount(gateway: str, currency: str, amount: float) -> None:
        """Record payment amount."""
        payment_amount.labels(gateway=gateway, currency=currency).inc(amount)

    @staticmethod
    def observe_duration(gateway: str, duration: float) -> None:
        """Record payment processing duration."""
        payment_duration.labels(gateway=gateway).observe(duration)

    @staticmethod
    def record_3ds_attempt(gateway: str, status: str) -> None:
        """Record a 3DS authentication attempt."""
        three_ds_attempts.labels(gateway=gateway, status=status).inc()


class APIMetrics:
    """High-level interface for API metrics."""

    @staticmethod
    def record_request(method: str, endpoint: str, status_code: int) -> None:
        """Record an API request."""
        api_requests.labels(
            method=method,
            endpoint=endpoint,
            status_code=str(status_code),
        ).inc()

    @staticmethod
    def observe_latency(method: str, endpoint: str, duration: float) -> None:
        """Record API request latency."""
        api_latency.labels(method=method, endpoint=endpoint).observe(duration)

    @staticmethod
    @contextmanager
    def track_request(
        method: str,
        endpoint: str,
    ) -> Iterator[dict[str, Any]]:
        """
        Context manager to track API request metrics.

        Yields a dict where you can set 'status_code' before exit.

        Example:
            with APIMetrics.track_request("GET", "/api/bookings") as ctx:
                response = await process_request()
                ctx["status_code"] = response.status_code
        """
        start = time()
        context: dict[str, Any] = {"status_code": 200}
        try:
            yield context
        finally:
            duration = time() - start
            APIMetrics.observe_latency(method, endpoint, duration)
            APIMetrics.record_request(method, endpoint, context["status_code"])


class DirectusMetrics:
    """High-level interface for Directus API metrics."""

    @staticmethod
    def record_request(collection: str, operation: str, status: str) -> None:
        """Record a Directus API request."""
        directus_requests.labels(
            collection=collection,
            operation=operation,
            status=status,
        ).inc()

    @staticmethod
    def observe_latency(collection: str, operation: str, duration: float) -> None:
        """Record Directus API latency."""
        directus_latency.labels(
            collection=collection,
            operation=operation,
        ).observe(duration)


class CreditMetrics:
    """High-level interface for credit metrics."""

    @staticmethod
    def set_balance(agency_id: str, balance: float) -> None:
        """Set agency credit balance."""
        credit_balance.labels(agency_id=agency_id).set(balance)

    @staticmethod
    def record_transaction(agency_id: str, transaction_type: str) -> None:
        """Record a credit transaction."""
        credit_transactions.labels(
            agency_id=agency_id,
            type=transaction_type,
        ).inc()


__all__ = [
    # Registry
    "metrics",
    "REGISTRY",
    # High-level interfaces
    "BookingMetrics",
    "QueueMetrics",
    "BrowserMetrics",
    "ProxyMetrics",
    "CaptchaMetrics",
    "AccountMetrics",
    "PaymentMetrics",
    "APIMetrics",
    "DirectusMetrics",
    "CreditMetrics",
    # Raw metrics
    "booking_requests_total",
    "booking_state_transitions",
    "active_bookings",
    "booking_duration_seconds",
    "slot_search_duration_seconds",
    "booking_attempts_total",
    "queue_size",
    "queue_processing_time",
    "worker_count",
    "task_retries",
    "task_failures",
    "browser_sessions_active",
    "browser_session_duration",
    "page_load_time",
    "browser_errors",
    "proxy_pool_size",
    "proxy_success_rate",
    "proxy_response_time",
    "proxy_requests",
    "proxy_rotations",
    "captcha_requests",
    "captcha_solve_time",
    "captcha_balance",
    "account_pool_size",
    "account_usage",
    "account_bans",
    "payment_requests",
    "payment_amount",
    "payment_duration",
    "three_ds_attempts",
    "credit_balance",
    "credit_transactions",
    "api_requests",
    "api_latency",
    "directus_requests",
    "directus_latency",
    "redis_operations",
]
