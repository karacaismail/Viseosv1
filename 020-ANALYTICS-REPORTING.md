# 020 - Analytics & Reporting Specification

## Amaç

VISE OS için kapsamlı analytics ve raporlama sistemi. Business intelligence, agency performance reports, booking analytics, cost analysis ve trend forecasting. Agency admin ve platform admin için farklı rapor setleri.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 002-DIRECTUS-SCHEMA | Data source |
| 018-MONITORING-DASHBOARD | Real-time metrics |
| 019-ALERT-SYSTEM | Alert statistics |
| 014-QUEUE-ORCHESTRATOR | Queue analytics |

---

## Analytics Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       ANALYTICS & REPORTING                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────────────────────────────────────────────┐              │
│  │                    DATA SOURCES                           │              │
│  ├──────────────────────────────────────────────────────────┤              │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐    │              │
│  │  │Bookings │  │Payments │  │ Metrics │  │  Logs   │    │              │
│  │  │   DB    │  │   DB    │  │ (Prom)  │  │ (Loki)  │    │              │
│  │  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘    │              │
│  └───────┼────────────┼────────────┼────────────┼──────────┘              │
│          │            │            │            │                          │
│          └────────────┼────────────┼────────────┘                          │
│                       ▼            ▼                                        │
│              ┌──────────────────────────────┐                              │
│              │      ETL PIPELINE            │                              │
│              │   (Data Transformation)      │                              │
│              └─────────────┬────────────────┘                              │
│                            │                                                │
│                            ▼                                                │
│              ┌──────────────────────────────┐                              │
│              │     DATA WAREHOUSE           │                              │
│              │     (Analytics DB)           │                              │
│              └─────────────┬────────────────┘                              │
│                            │                                                │
│          ┌─────────────────┼─────────────────┐                             │
│          ▼                 ▼                 ▼                             │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐                       │
│  │   Agency     │ │   Platform   │ │   Export     │                       │
│  │   Reports    │ │   Reports    │ │   Service    │                       │
│  └──────────────┘ └──────────────┘ └──────────────┘                       │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Data Warehouse Schema

```python
from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional
from enum import Enum

# ============================================
# DIMENSION TABLES
# ============================================

@dataclass
class DimAgency:
    """Agency dimension"""
    agency_key: int
    agency_id: str
    agency_name: str
    tier: str  # premium, standard, trial
    country: str
    created_at: date
    is_active: bool

@dataclass
class DimSite:
    """Visa site dimension"""
    site_key: int
    site_code: str  # vfs, idata, bls, kkosmos
    site_name: str
    country: str
    difficulty_level: str  # easy, medium, hard

@dataclass
class DimDate:
    """Date dimension"""
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
    """Time dimension (for hourly analysis)"""
    time_key: int
    hour: int
    minute_bucket: int  # 0, 15, 30, 45
    time_of_day: str  # morning, afternoon, evening, night
    is_business_hours: bool

@dataclass
class DimCountry:
    """Target country dimension"""
    country_key: int
    country_code: str
    country_name: str
    region: str  # schengen, non_schengen
    visa_difficulty: str

# ============================================
# FACT TABLES
# ============================================

@dataclass
class FactBooking:
    """Booking fact table"""
    booking_key: int
    
    # Dimensions
    agency_key: int
    site_key: int
    date_key: int
    time_key: int
    target_country_key: int
    
    # Measures
    booking_id: str
    status: str
    duration_seconds: int
    attempt_count: int
    
    # Financial
    credit_cost: float
    payment_amount: float
    payment_currency: str
    
    # Timing
    queued_at: datetime
    started_at: datetime
    completed_at: datetime
    
    # Result
    success: bool
    failure_reason: Optional[str]

@dataclass
class FactDailyAgencyStats:
    """Daily agency statistics (aggregated)"""
    stats_key: int
    
    # Dimensions
    agency_key: int
    site_key: int
    date_key: int
    
    # Measures
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
    """Daily site statistics"""
    stats_key: int
    
    # Dimensions
    site_key: int
    date_key: int
    
    # Measures
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
    """Hourly queue statistics"""
    stats_key: int
    
    # Dimensions
    date_key: int
    time_key: int
    
    # Measures
    queue_depth_avg: int
    queue_depth_max: int
    
    tasks_enqueued: int
    tasks_completed: int
    tasks_failed: int
    
    worker_count_avg: int
    worker_utilization: float
```

---

## ETL Pipeline

```python
from typing import Dict, List, Any
from datetime import datetime, timedelta
import asyncio

class AnalyticsETLPipeline:
    """Analytics ETL pipeline"""
    
    def __init__(
        self,
        source_db,
        analytics_db,
        prometheus_client,
    ):
        self.source = source_db
        self.analytics = analytics_db
        self.prometheus = prometheus_client
    
    async def run_daily_etl(self, target_date: date = None):
        """Günlük ETL çalıştır"""
        
        target_date = target_date or (datetime.utcnow().date() - timedelta(days=1))
        
        print(f"Running ETL for {target_date}")
        
        # Extract
        bookings = await self._extract_bookings(target_date)
        payments = await self._extract_payments(target_date)
        metrics = await self._extract_metrics(target_date)
        
        # Transform
        fact_bookings = self._transform_bookings(bookings, payments)
        agency_stats = self._aggregate_agency_stats(fact_bookings)
        site_stats = self._aggregate_site_stats(fact_bookings, metrics)
        
        # Load
        await self._load_fact_bookings(fact_bookings)
        await self._load_agency_stats(agency_stats)
        await self._load_site_stats(site_stats)
        
        print(f"ETL completed for {target_date}")
    
    async def run_hourly_etl(self, target_hour: datetime = None):
        """Saatlik ETL (queue stats)"""
        
        target_hour = target_hour or (datetime.utcnow() - timedelta(hours=1))
        target_hour = target_hour.replace(minute=0, second=0, microsecond=0)
        
        # Extract queue metrics from Prometheus
        queue_metrics = await self._extract_queue_metrics(target_hour)
        
        # Transform
        hourly_stats = self._transform_queue_stats(queue_metrics, target_hour)
        
        # Load
        await self._load_hourly_queue_stats(hourly_stats)
    
    async def _extract_bookings(self, target_date: date) -> List[Dict]:
        """Booking verilerini çek"""
        
        return await self.source.items("booking_requests").read(
            filter={
                "_and": [
                    {"created_at": {"_gte": target_date.isoformat()}},
                    {"created_at": {"_lt": (target_date + timedelta(days=1)).isoformat()}},
                ]
            },
            fields=[
                "id", "agency_id", "site", "status",
                "target_country", "created_at", "updated_at",
                "attempt_count", "confirmation_number",
                "failure_reason",
            ],
        )
    
    async def _extract_payments(self, target_date: date) -> List[Dict]:
        """Payment verilerini çek"""
        
        return await self.source.items("payment_transactions").read(
            filter={
                "_and": [
                    {"created_at": {"_gte": target_date.isoformat()}},
                    {"created_at": {"_lt": (target_date + timedelta(days=1)).isoformat()}},
                ]
            },
            fields=[
                "booking_id", "amount", "currency", "status",
            ],
        )
    
    async def _extract_metrics(self, target_date: date) -> Dict:
        """Prometheus metrics çek"""
        
        # Query Prometheus for daily aggregates
        queries = {
            "captcha_success_rate": f'sum(increase(vise_captcha_attempts_total{{result="success"}}[24h])) / sum(increase(vise_captcha_attempts_total[24h]))',
            "account_bans": f'sum(increase(vise_account_usage_total{{result="banned"}}[24h]))',
            "proxy_failures": f'sum(increase(vise_external_requests_total{{service="proxy",status="failed"}}[24h]))',
        }
        
        results = {}
        for name, query in queries.items():
            result = await self.prometheus.query(query)
            results[name] = result
        
        return results
    
    def _transform_bookings(
        self,
        bookings: List[Dict],
        payments: List[Dict],
    ) -> List[FactBooking]:
        """Booking verilerini transform et"""
        
        # Create payment lookup
        payment_map = {p["booking_id"]: p for p in payments}
        
        facts = []
        for booking in bookings:
            payment = payment_map.get(booking["id"], {})
            
            # Calculate duration
            created = datetime.fromisoformat(booking["created_at"])
            updated = datetime.fromisoformat(booking["updated_at"])
            duration = int((updated - created).total_seconds())
            
            fact = FactBooking(
                booking_key=0,  # Auto-generated
                agency_key=self._get_agency_key(booking["agency_id"]),
                site_key=self._get_site_key(booking["site"]),
                date_key=self._get_date_key(created.date()),
                time_key=self._get_time_key(created.hour),
                target_country_key=self._get_country_key(booking["target_country"]),
                booking_id=booking["id"],
                status=booking["status"],
                duration_seconds=duration,
                attempt_count=booking.get("attempt_count", 1),
                credit_cost=1.0,  # Base credit cost
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
        bookings: List[FactBooking],
    ) -> List[FactDailyAgencyStats]:
        """Agency stats aggregate"""
        
        # Group by agency + site + date
        groups = {}
        
        for booking in bookings:
            key = (booking.agency_key, booking.site_key, booking.date_key)
            
            if key not in groups:
                groups[key] = {
                    "bookings": [],
                    "successful": 0,
                    "failed": 0,
                    "durations": [],
                    "credits": 0,
                    "payments": 0,
                }
            
            groups[key]["bookings"].append(booking)
            groups[key]["durations"].append(booking.duration_seconds)
            groups[key]["credits"] += booking.credit_cost
            groups[key]["payments"] += booking.payment_amount
            
            if booking.success:
                groups[key]["successful"] += 1
            else:
                groups[key]["failed"] += 1
        
        # Create stats
        stats = []
        for (agency_key, site_key, date_key), data in groups.items():
            total = len(data["bookings"])
            
            stat = FactDailyAgencyStats(
                stats_key=0,
                agency_key=agency_key,
                site_key=site_key,
                date_key=date_key,
                total_bookings=total,
                successful_bookings=data["successful"],
                failed_bookings=data["failed"],
                avg_duration_seconds=sum(data["durations"]) / len(data["durations"]),
                min_duration_seconds=min(data["durations"]),
                max_duration_seconds=max(data["durations"]),
                total_credits_used=data["credits"],
                total_payment_amount=data["payments"],
                success_rate=data["successful"] / total if total > 0 else 0,
            )
            stats.append(stat)
        
        return stats
    
    def _aggregate_site_stats(
        self,
        bookings: List[FactBooking],
        metrics: Dict,
    ) -> List[FactDailySiteStats]:
        """Site stats aggregate"""
        
        # Group by site + date
        groups = {}
        
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
        for (site_key, date_key), data in groups.items():
            stat = FactDailySiteStats(
                stats_key=0,
                site_key=site_key,
                date_key=date_key,
                total_bookings=data["total"],
                successful_bookings=data["successful"],
                failed_bookings=data["failed"],
                avg_duration_seconds=sum(data["durations"]) / len(data["durations"]),
                avg_slot_search_seconds=0,  # From metrics
                captcha_count=0,  # From metrics
                captcha_success_rate=metrics.get("captcha_success_rate", 0),
                account_ban_count=int(metrics.get("account_bans", 0)),
                proxy_failure_count=int(metrics.get("proxy_failures", 0)),
            )
            stats.append(stat)
        
        return stats
    
    # Dimension key lookups
    def _get_agency_key(self, agency_id: str) -> int:
        # Lookup from dimension table
        return hash(agency_id) % 1000000
    
    def _get_site_key(self, site_code: str) -> int:
        sites = {"vfs": 1, "idata": 2, "bls": 3, "kkosmos": 4}
        return sites.get(site_code, 0)
    
    def _get_date_key(self, d: date) -> int:
        return int(d.strftime("%Y%m%d"))
    
    def _get_time_key(self, hour: int) -> int:
        return hour
    
    def _get_country_key(self, country_code: str) -> int:
        return hash(country_code) % 1000
```

---

## Report Definitions

```python
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from enum import Enum
from datetime import date, datetime

class ReportType(Enum):
    """Rapor tipleri"""
    AGENCY_DAILY = "agency_daily"
    AGENCY_WEEKLY = "agency_weekly"
    AGENCY_MONTHLY = "agency_monthly"
    PLATFORM_DAILY = "platform_daily"
    PLATFORM_WEEKLY = "platform_weekly"
    SITE_PERFORMANCE = "site_performance"
    FINANCIAL = "financial"
    CAPACITY = "capacity"

@dataclass
class ReportConfig:
    """Rapor konfigürasyonu"""
    report_type: ReportType
    title: str
    description: str
    
    # Schedule
    schedule: str  # cron expression
    
    # Filters
    agency_filter: Optional[str] = None
    site_filter: Optional[str] = None
    date_range_days: int = 1
    
    # Output
    output_formats: List[str] = None  # pdf, excel, json
    recipients: List[str] = None

# Report configurations
REPORT_CONFIGS = {
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
    ReportType.PLATFORM_DAILY: ReportConfig(
        report_type=ReportType.PLATFORM_DAILY,
        title="Platform Daily Overview",
        description="Platform-wide daily statistics",
        schedule="0 7 * * *",
        date_range_days=1,
        output_formats=["pdf"],
    ),
    ReportType.FINANCIAL: ReportConfig(
        report_type=ReportType.FINANCIAL,
        title="Financial Report",
        description="Revenue, credits, and payment statistics",
        schedule="0 10 1 * *",  # 1st of month
        date_range_days=30,
        output_formats=["pdf", "excel"],
    ),
}
```

---

## Report Generator

```python
from typing import Dict, Any, List
from datetime import date, datetime, timedelta
import io

class ReportGenerator:
    """Rapor üretici"""
    
    def __init__(self, analytics_db):
        self.db = analytics_db
    
    async def generate_agency_daily_report(
        self,
        agency_id: str,
        report_date: date,
    ) -> Dict[str, Any]:
        """Agency günlük raporu"""
        
        # Fetch data
        stats = await self._get_agency_stats(agency_id, report_date)
        bookings = await self._get_agency_bookings(agency_id, report_date)
        
        # Build report
        report = {
            "metadata": {
                "report_type": "agency_daily",
                "agency_id": agency_id,
                "report_date": report_date.isoformat(),
                "generated_at": datetime.utcnow().isoformat(),
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
    
    async def generate_platform_daily_report(
        self,
        report_date: date,
    ) -> Dict[str, Any]:
        """Platform günlük raporu"""
        
        # Fetch data
        all_stats = await self._get_platform_stats(report_date)
        site_stats = await self._get_site_stats(report_date)
        
        report = {
            "metadata": {
                "report_type": "platform_daily",
                "report_date": report_date.isoformat(),
                "generated_at": datetime.utcnow().isoformat(),
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
    ) -> Dict[str, Any]:
        """Finansal rapor"""
        
        report = {
            "metadata": {
                "report_type": "financial",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "generated_at": datetime.utcnow().isoformat(),
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
        
        # Populate from database
        # ...
        
        return report
    
    def _group_by_site(self, bookings: List[Dict]) -> Dict:
        """Site'a göre grupla"""
        groups = {}
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
    
    def _group_by_country(self, bookings: List[Dict]) -> Dict:
        """Ülkeye göre grupla"""
        groups = {}
        for booking in bookings:
            country = booking.get("target_country", "unknown")
            if country not in groups:
                groups[country] = 0
            groups[country] += 1
        return groups
    
    def _analyze_failures(self, bookings: List[Dict]) -> Dict:
        """Failure analizi"""
        failures = {}
        for booking in bookings:
            if not booking.get("success"):
                reason = booking.get("failure_reason", "unknown")
                if reason not in failures:
                    failures[reason] = 0
                failures[reason] += 1
        return failures
    
    def _hourly_distribution(self, bookings: List[Dict]) -> Dict:
        """Saatlik dağılım"""
        hours = {h: 0 for h in range(24)}
        for booking in bookings:
            created = booking.get("created_at")
            if created:
                hour = datetime.fromisoformat(created).hour
                hours[hour] += 1
        return hours
```

---

## Report Export Service

```python
from typing import Dict, Any, BinaryIO
from datetime import date
import io

class ReportExportService:
    """Rapor export servisi"""
    
    async def export_to_pdf(
        self,
        report_data: Dict[str, Any],
    ) -> bytes:
        """PDF export"""
        
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
        from reportlab.lib.styles import getSampleStyleSheet
        
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        
        elements = []
        styles = getSampleStyleSheet()
        
        # Title
        elements.append(Paragraph(
            report_data["metadata"].get("report_type", "Report"),
            styles["Heading1"],
        ))
        
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
        table.setStyle(TableStyle([
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
        ]))
        
        elements.append(table)
        
        doc.build(elements)
        return buffer.getvalue()
    
    async def export_to_excel(
        self,
        report_data: Dict[str, Any],
    ) -> bytes:
        """Excel export"""
        
        import openpyxl
        from openpyxl.styles import Font, Alignment, PatternFill
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Summary"
        
        # Header style
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        
        # Title
        ws["A1"] = report_data["metadata"].get("report_type", "Report")
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
            ws.cell(row=row, column=1, value=key)
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
                ws_site.cell(row=row, column=2, value=data.get("total", 0))
                ws_site.cell(row=row, column=3, value=data.get("success", 0))
                ws_site.cell(row=row, column=4, value=data.get("failed", 0))
                row += 1
        
        buffer = io.BytesIO()
        wb.save(buffer)
        return buffer.getvalue()
    
    async def export_to_json(
        self,
        report_data: Dict[str, Any],
    ) -> bytes:
        """JSON export"""
        import json
        return json.dumps(report_data, indent=2, default=str).encode()
```

---

## Scheduled Report Delivery

```python
from celery import shared_task
from datetime import date, datetime, timedelta
from typing import List

class ReportScheduler:
    """Rapor zamanlayıcı"""
    
    def __init__(
        self,
        report_generator: ReportGenerator,
        export_service: ReportExportService,
        email_service,
    ):
        self.generator = report_generator
        self.export = export_service
        self.email = email_service
    
    async def deliver_agency_daily_reports(self, report_date: date = None):
        """Agency günlük raporlarını gönder"""
        
        report_date = report_date or (datetime.utcnow().date() - timedelta(days=1))
        
        # Get active agencies
        agencies = await self._get_active_agencies()
        
        for agency in agencies:
            try:
                # Generate report
                report_data = await self.generator.generate_agency_daily_report(
                    agency["id"],
                    report_date,
                )
                
                # Export to PDF
                pdf = await self.export.export_to_pdf(report_data)
                
                # Export to Excel
                excel = await self.export.export_to_excel(report_data)
                
                # Send email
                await self.email.send(
                    to=agency["email"],
                    subject=f"VISE OS Daily Report - {report_date}",
                    body=self._format_email_body(report_data),
                    attachments=[
                        ("report.pdf", pdf, "application/pdf"),
                        ("report.xlsx", excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                    ],
                )
                
            except Exception as e:
                print(f"Failed to deliver report for agency {agency['id']}: {e}")
    
    async def deliver_platform_daily_report(self, report_date: date = None):
        """Platform günlük raporunu gönder"""
        
        report_date = report_date or (datetime.utcnow().date() - timedelta(days=1))
        
        # Generate report
        report_data = await self.generator.generate_platform_daily_report(report_date)
        
        # Export
        pdf = await self.export.export_to_pdf(report_data)
        
        # Send to admins
        await self.email.send(
            to="admin@vise.os",
            subject=f"Platform Daily Report - {report_date}",
            body=self._format_email_body(report_data),
            attachments=[("platform_report.pdf", pdf, "application/pdf")],
        )
    
    def _format_email_body(self, report_data: Dict) -> str:
        """Email body formatla"""
        
        summary = report_data.get("summary", {})
        
        return f"""
VISE OS Report

Summary:
- Total Bookings: {summary.get('total_bookings', 0)}
- Successful: {summary.get('successful_bookings', 0)}
- Failed: {summary.get('failed_bookings', 0)}
- Success Rate: {summary.get('success_rate', 0):.1%}

Please see attached PDF and Excel files for detailed information.

---
This is an automated report from VISE OS.
        """


# Celery tasks
@shared_task(name="tasks.deliver_agency_daily_reports")
def deliver_agency_daily_reports_task():
    """Agency günlük rapor task'ı"""
    import asyncio
    scheduler = ReportScheduler(...)
    asyncio.run(scheduler.deliver_agency_daily_reports())

@shared_task(name="tasks.deliver_platform_daily_report")
def deliver_platform_daily_report_task():
    """Platform günlük rapor task'ı"""
    import asyncio
    scheduler = ReportScheduler(...)
    asyncio.run(scheduler.deliver_platform_daily_report())
```

---

## Analytics API

```python
from fastapi import FastAPI, Query, Depends
from datetime import date, datetime, timedelta
from typing import Optional, List

app = FastAPI()

@app.get("/api/analytics/agency/{agency_id}/summary")
async def get_agency_summary(
    agency_id: str,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
):
    """Agency özet istatistikleri"""
    
    if not start_date:
        start_date = datetime.utcnow().date() - timedelta(days=7)
    if not end_date:
        end_date = datetime.utcnow().date()
    
    return {
        "agency_id": agency_id,
        "period": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "summary": {
            "total_bookings": 156,
            "successful_bookings": 142,
            "failed_bookings": 14,
            "success_rate": 0.91,
            "avg_duration_minutes": 4.5,
            "credits_used": 156,
        },
        "trends": {
            "booking_trend": "+12%",
            "success_rate_trend": "-2%",
        },
    }

@app.get("/api/analytics/agency/{agency_id}/bookings")
async def get_agency_bookings(
    agency_id: str,
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
    site: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
):
    """Agency booking listesi"""
    
    return {
        "agency_id": agency_id,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": 156,
        },
        "bookings": [
            # Booking list
        ],
    }

@app.get("/api/analytics/platform/overview")
async def get_platform_overview(
    start_date: date = Query(default=None),
    end_date: date = Query(default=None),
):
    """Platform genel bakış"""
    
    return {
        "period": {
            "start": start_date.isoformat() if start_date else None,
            "end": end_date.isoformat() if end_date else None,
        },
        "summary": {
            "total_bookings": 2456,
            "active_agencies": 45,
            "success_rate": 0.89,
            "total_revenue": 12500.00,
        },
        "by_site": {
            "vfs": {"bookings": 1200, "success_rate": 0.88},
            "idata": {"bookings": 800, "success_rate": 0.92},
            "bls": {"bookings": 300, "success_rate": 0.85},
            "kkosmos": {"bookings": 156, "success_rate": 0.78},
        },
    }

@app.get("/api/analytics/reports/{report_id}")
async def get_report(report_id: str):
    """Rapor detayı"""
    pass

@app.post("/api/analytics/reports/generate")
async def generate_report(
    report_type: str,
    agency_id: Optional[str] = None,
    start_date: date = None,
    end_date: date = None,
    output_format: str = "pdf",
):
    """On-demand rapor üret"""
    pass
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | ETL pipeline günlük çalışmalı | Schedule test |
| AC-002 | Agency daily report doğru üretilmeli | Report test |
| AC-003 | Platform daily report doğru üretilmeli | Report test |
| AC-004 | PDF export çalışmalı | Export test |
| AC-005 | Excel export çalışmalı | Export test |
| AC-006 | Email delivery çalışmalı | Email test |
| AC-007 | Analytics API response <2s olmalı | Performance test |

---

## Report Schedule Summary

| Rapor | Schedule | Recipients |
|-------|----------|------------|
| Agency Daily | 08:00 daily | Agency admin |
| Agency Weekly | Mon 09:00 | Agency admin |
| Agency Monthly | 1st 10:00 | Agency admin |
| Platform Daily | 07:00 daily | Platform admin |
| Platform Weekly | Mon 08:00 | Platform admin |
| Financial Monthly | 1st 10:00 | Finance team |

---

## VISE OS Specification Complete

Tüm 20 specification dosyası tamamlandı:

| Grup | Dosyalar |
|------|----------|
| Grup 1: Temel Mimari | 001, 002, 003 |
| Grup 2: Bot Core | 004, 005, 006, 007 |
| Grup 3: Site Adapters | 008, 009, 010, 011 |
| Grup 4: AI/Orchestration | 012, 013, 014 |
| Grup 5: Payment/Verification | 015, 016, 017 |
| Grup 6: Monitoring/Analytics | 018, 019, 020 |
