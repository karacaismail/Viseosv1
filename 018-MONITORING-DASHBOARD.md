# 018 - Monitoring Dashboard Specification

## Amaç

VISE OS platform için real-time monitoring dashboard. Prometheus metrics collection, Grafana visualization, booking flow tracking, system health monitoring ve capacity planning. Operatör ve agency admin için farklı view'lar.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 001-ARCHITECTURE-OVERVIEW | Infrastructure layer |
| 014-QUEUE-ORCHESTRATOR | Queue metrics |
| 013-STATE-MACHINE | Booking state metrics |
| 019-ALERT-SYSTEM | Alert integration |

---

## Monitoring Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        MONITORING ARCHITECTURE                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────────────────────────────────────────────┐              │
│  │                    DATA SOURCES                           │              │
│  ├──────────────────────────────────────────────────────────┤              │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐    │              │
│  │  │ App     │  │ Queue   │  │ Browser │  │ External │    │              │
│  │  │ Metrics │  │ Metrics │  │ Sessions│  │ Services │    │              │
│  │  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘    │              │
│  └───────┼────────────┼────────────┼────────────┼──────────┘              │
│          │            │            │            │                          │
│          ▼            ▼            ▼            ▼                          │
│  ┌──────────────────────────────────────────────────────────┐              │
│  │                    PROMETHEUS                             │              │
│  │  • Scrape endpoints  • Store time-series                  │              │
│  │  • PromQL queries    • Alert rules                        │              │
│  └──────────────────────────────────────────────────────────┘              │
│                              │                                              │
│          ┌───────────────────┼───────────────────┐                         │
│          ▼                   ▼                   ▼                         │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                 │
│  │   GRAFANA    │    │    LOKI      │    │ ALERTMANAGER │                 │
│  │  Dashboards  │    │    Logs      │    │   Alerts     │                 │
│  └──────────────┘    └──────────────┘    └──────────────┘                 │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Prometheus Metrics Definition

```python
from prometheus_client import (
    Counter, Gauge, Histogram, Summary,
    CollectorRegistry, generate_latest,
)
from typing import Dict, Any
import time

# Custom registry
REGISTRY = CollectorRegistry()

# ============================================
# BOOKING METRICS
# ============================================

# Booking request counters
booking_requests_total = Counter(
    'vise_booking_requests_total',
    'Total booking requests',
    ['site', 'agency_id', 'status'],
    registry=REGISTRY,
)

booking_state_transitions = Counter(
    'vise_booking_state_transitions_total',
    'Booking state transitions',
    ['site', 'from_state', 'to_state'],
    registry=REGISTRY,
)

# Active bookings gauge
active_bookings = Gauge(
    'vise_active_bookings',
    'Currently active bookings',
    ['site', 'state'],
    registry=REGISTRY,
)

# Booking duration histogram
booking_duration_seconds = Histogram(
    'vise_booking_duration_seconds',
    'Booking completion time',
    ['site', 'result'],
    buckets=[30, 60, 120, 180, 300, 600, 900, 1800],
    registry=REGISTRY,
)

# Slot search duration
slot_search_duration_seconds = Histogram(
    'vise_slot_search_duration_seconds',
    'Time to find available slot',
    ['site'],
    buckets=[5, 10, 30, 60, 120, 300, 600],
    registry=REGISTRY,
)

# ============================================
# QUEUE METRICS
# ============================================

queue_size = Gauge(
    'vise_queue_size',
    'Queue size by queue name',
    ['queue_name'],
    registry=REGISTRY,
)

queue_processing_time = Histogram(
    'vise_queue_processing_seconds',
    'Task processing time',
    ['queue_name', 'task_type'],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600],
    registry=REGISTRY,
)

worker_count = Gauge(
    'vise_worker_count',
    'Active workers by pool',
    ['pool_name'],
    registry=REGISTRY,
)

task_retries = Counter(
    'vise_task_retries_total',
    'Task retry count',
    ['task_type', 'reason'],
    registry=REGISTRY,
)

# ============================================
# BROWSER SESSION METRICS
# ============================================

browser_sessions_active = Gauge(
    'vise_browser_sessions_active',
    'Active browser sessions',
    ['site'],
    registry=REGISTRY,
)

browser_session_duration = Histogram(
    'vise_browser_session_duration_seconds',
    'Browser session duration',
    ['site'],
    buckets=[60, 120, 300, 600, 900, 1800],
    registry=REGISTRY,
)

page_load_time = Histogram(
    'vise_page_load_seconds',
    'Page load time',
    ['site', 'page_type'],
    buckets=[1, 2, 5, 10, 20, 30, 60],
    registry=REGISTRY,
)

# ============================================
# PROXY METRICS
# ============================================

proxy_pool_size = Gauge(
    'vise_proxy_pool_size',
    'Proxy pool size by status',
    ['provider', 'status'],
    registry=REGISTRY,
)

proxy_success_rate = Gauge(
    'vise_proxy_success_rate',
    'Proxy success rate',
    ['provider'],
    registry=REGISTRY,
)

proxy_response_time = Histogram(
    'vise_proxy_response_seconds',
    'Proxy response time',
    ['provider'],
    buckets=[0.5, 1, 2, 5, 10, 20],
    registry=REGISTRY,
)

# ============================================
# CAPTCHA METRICS
# ============================================

captcha_attempts = Counter(
    'vise_captcha_attempts_total',
    'CAPTCHA solve attempts',
    ['provider', 'type', 'result'],
    registry=REGISTRY,
)

captcha_solve_time = Histogram(
    'vise_captcha_solve_seconds',
    'CAPTCHA solve time',
    ['provider', 'type'],
    buckets=[5, 10, 20, 30, 45, 60, 90, 120],
    registry=REGISTRY,
)

captcha_cost = Counter(
    'vise_captcha_cost_cents',
    'CAPTCHA cost in cents',
    ['provider', 'type'],
    registry=REGISTRY,
)

# ============================================
# ACCOUNT METRICS
# ============================================

account_pool_size = Gauge(
    'vise_account_pool_size',
    'Account pool size by status',
    ['site', 'status'],
    registry=REGISTRY,
)

account_usage = Counter(
    'vise_account_usage_total',
    'Account usage count',
    ['site', 'result'],
    registry=REGISTRY,
)

account_ban_rate = Gauge(
    'vise_account_ban_rate',
    'Account ban rate (24h rolling)',
    ['site'],
    registry=REGISTRY,
)

# ============================================
# PAYMENT METRICS
# ============================================

payment_attempts = Counter(
    'vise_payment_attempts_total',
    'Payment attempts',
    ['gateway', 'result'],
    registry=REGISTRY,
)

payment_amount = Counter(
    'vise_payment_amount_total',
    'Total payment amount',
    ['gateway', 'currency'],
    registry=REGISTRY,
)

three_ds_challenges = Counter(
    'vise_3ds_challenges_total',
    '3DS challenge count',
    ['version', 'result'],
    registry=REGISTRY,
)

# ============================================
# AI DECISION METRICS
# ============================================

ai_decisions = Counter(
    'vise_ai_decisions_total',
    'AI decision count',
    ['decision_type', 'action'],
    registry=REGISTRY,
)

ai_decision_confidence = Histogram(
    'vise_ai_decision_confidence',
    'AI decision confidence distribution',
    ['decision_type'],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    registry=REGISTRY,
)

ai_latency = Histogram(
    'vise_ai_latency_seconds',
    'AI decision latency',
    ['provider'],
    buckets=[0.5, 1, 2, 3, 5, 10],
    registry=REGISTRY,
)

# ============================================
# EXTERNAL SERVICE METRICS
# ============================================

external_service_requests = Counter(
    'vise_external_requests_total',
    'External service requests',
    ['service', 'endpoint', 'status'],
    registry=REGISTRY,
)

external_service_latency = Histogram(
    'vise_external_latency_seconds',
    'External service latency',
    ['service'],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30],
    registry=REGISTRY,
)

# ============================================
# SYSTEM METRICS
# ============================================

system_cpu_usage = Gauge(
    'vise_system_cpu_percent',
    'System CPU usage',
    ['host'],
    registry=REGISTRY,
)

system_memory_usage = Gauge(
    'vise_system_memory_percent',
    'System memory usage',
    ['host'],
    registry=REGISTRY,
)

system_disk_usage = Gauge(
    'vise_system_disk_percent',
    'System disk usage',
    ['host', 'mount'],
    registry=REGISTRY,
)
```

---

## Metrics Collector Service

```python
from fastapi import FastAPI, Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
import asyncio
from datetime import datetime, timedelta

class MetricsCollector:
    """Metrics toplama servisi"""
    
    def __init__(
        self,
        db_client,
        redis_client,
        queue_manager,
    ):
        self.db = db_client
        self.redis = redis_client
        self.queue = queue_manager
        
        # Collection interval
        self.collection_interval = 15  # seconds
    
    async def start_collection(self):
        """Metric collection loop başlat"""
        
        while True:
            try:
                await self._collect_all_metrics()
            except Exception as e:
                print(f"Metrics collection error: {e}")
            
            await asyncio.sleep(self.collection_interval)
    
    async def _collect_all_metrics(self):
        """Tüm metrikleri topla"""
        
        await asyncio.gather(
            self._collect_booking_metrics(),
            self._collect_queue_metrics(),
            self._collect_browser_metrics(),
            self._collect_proxy_metrics(),
            self._collect_account_metrics(),
            self._collect_system_metrics(),
        )
    
    async def _collect_booking_metrics(self):
        """Booking metrikleri"""
        
        # Active bookings by site and state
        sites = ["vfs", "idata", "bls", "kkosmos"]
        states = ["pending", "queued", "processing", "slot_found", "booking", "payment"]
        
        for site in sites:
            for state in states:
                count = await self.db.items("booking_requests").read(
                    filter={
                        "site": {"_eq": site},
                        "status": {"_eq": state},
                    },
                    aggregate={"count": "*"},
                )
                active_bookings.labels(site=site, state=state).set(
                    count[0]["count"] if count else 0
                )
    
    async def _collect_queue_metrics(self):
        """Queue metrikleri"""
        
        queues = [
            "high_priority", "normal", "retry", 
            "scheduled", "vfs", "idata", "bls"
        ]
        
        for queue_name in queues:
            size = await self.redis.llen(queue_name)
            queue_size.labels(queue_name=queue_name).set(size)
        
        # Worker count
        pool_status = await self.queue.get_pool_status()
        for pool_name, status in pool_status.items():
            worker_count.labels(pool_name=pool_name).set(
                status.get("workers", 0)
            )
    
    async def _collect_browser_metrics(self):
        """Browser session metrikleri"""
        
        # Active sessions from Redis
        for site in ["vfs", "idata", "bls", "kkosmos"]:
            sessions = await self.redis.scard(f"browser_sessions:{site}")
            browser_sessions_active.labels(site=site).set(sessions)
    
    async def _collect_proxy_metrics(self):
        """Proxy metrikleri"""
        
        providers = ["brightdata", "oxylabs"]
        statuses = ["available", "in_use", "cooldown", "failed"]
        
        for provider in providers:
            for status in statuses:
                count = await self.redis.hget(
                    f"proxy_pool:{provider}",
                    status,
                ) or 0
                proxy_pool_size.labels(
                    provider=provider,
                    status=status,
                ).set(int(count))
    
    async def _collect_account_metrics(self):
        """Account metrikleri"""
        
        for site in ["vfs", "idata", "bls", "kkosmos"]:
            for status in ["available", "in_use", "cooldown", "banned"]:
                count = await self.db.items("bot_accounts").read(
                    filter={
                        "site": {"_eq": site},
                        "status": {"_eq": status},
                    },
                    aggregate={"count": "*"},
                )
                account_pool_size.labels(
                    site=site,
                    status=status,
                ).set(count[0]["count"] if count else 0)
    
    async def _collect_system_metrics(self):
        """Sistem metrikleri"""
        
        import psutil
        
        hostname = "vise-worker-1"  # Get from env
        
        system_cpu_usage.labels(host=hostname).set(psutil.cpu_percent())
        system_memory_usage.labels(host=hostname).set(psutil.virtual_memory().percent)
        
        for partition in psutil.disk_partitions():
            usage = psutil.disk_usage(partition.mountpoint)
            system_disk_usage.labels(
                host=hostname,
                mount=partition.mountpoint,
            ).set(usage.percent)


# FastAPI metrics endpoint
app = FastAPI()

@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )
```

---

## Grafana Dashboard Configuration

```yaml
# grafana/dashboards/vise-overview.json
{
  "dashboard": {
    "title": "VISE OS Overview",
    "uid": "vise-overview",
    "panels": [
      # Row 1: Key Metrics
      {
        "title": "Active Bookings",
        "type": "stat",
        "gridPos": {"x": 0, "y": 0, "w": 6, "h": 4},
        "targets": [
          {
            "expr": "sum(vise_active_bookings)",
            "legendFormat": "Active"
          }
        ]
      },
      {
        "title": "Success Rate (24h)",
        "type": "gauge",
        "gridPos": {"x": 6, "y": 0, "w": 6, "h": 4},
        "targets": [
          {
            "expr": "sum(rate(vise_booking_requests_total{status='completed'}[24h])) / sum(rate(vise_booking_requests_total[24h])) * 100"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "min": 0,
            "max": 100,
            "thresholds": {
              "steps": [
                {"color": "red", "value": 0},
                {"color": "yellow", "value": 70},
                {"color": "green", "value": 90}
              ]
            }
          }
        }
      },
      {
        "title": "Queue Depth",
        "type": "stat",
        "gridPos": {"x": 12, "y": 0, "w": 6, "h": 4},
        "targets": [
          {
            "expr": "sum(vise_queue_size)"
          }
        ]
      },
      {
        "title": "Active Workers",
        "type": "stat",
        "gridPos": {"x": 18, "y": 0, "w": 6, "h": 4},
        "targets": [
          {
            "expr": "sum(vise_worker_count)"
          }
        ]
      },
      
      # Row 2: Booking Flow
      {
        "title": "Bookings by State",
        "type": "piechart",
        "gridPos": {"x": 0, "y": 4, "w": 8, "h": 8},
        "targets": [
          {
            "expr": "sum by (state) (vise_active_bookings)",
            "legendFormat": "{{state}}"
          }
        ]
      },
      {
        "title": "Booking Rate (per minute)",
        "type": "timeseries",
        "gridPos": {"x": 8, "y": 4, "w": 16, "h": 8},
        "targets": [
          {
            "expr": "sum(rate(vise_booking_requests_total[5m])) * 60",
            "legendFormat": "Total"
          },
          {
            "expr": "sum(rate(vise_booking_requests_total{status='completed'}[5m])) * 60",
            "legendFormat": "Completed"
          },
          {
            "expr": "sum(rate(vise_booking_requests_total{status='failed'}[5m])) * 60",
            "legendFormat": "Failed"
          }
        ]
      },
      
      # Row 3: Site Performance
      {
        "title": "Success Rate by Site",
        "type": "bargauge",
        "gridPos": {"x": 0, "y": 12, "w": 12, "h": 6},
        "targets": [
          {
            "expr": "sum by (site) (rate(vise_booking_requests_total{status='completed'}[1h])) / sum by (site) (rate(vise_booking_requests_total[1h])) * 100",
            "legendFormat": "{{site}}"
          }
        ]
      },
      {
        "title": "Booking Duration by Site (p95)",
        "type": "timeseries",
        "gridPos": {"x": 12, "y": 12, "w": 12, "h": 6},
        "targets": [
          {
            "expr": "histogram_quantile(0.95, sum by (site, le) (rate(vise_booking_duration_seconds_bucket[1h])))",
            "legendFormat": "{{site}}"
          }
        ]
      },
      
      # Row 4: Resources
      {
        "title": "Proxy Pool Status",
        "type": "timeseries",
        "gridPos": {"x": 0, "y": 18, "w": 8, "h": 6},
        "targets": [
          {
            "expr": "sum by (status) (vise_proxy_pool_size)",
            "legendFormat": "{{status}}"
          }
        ]
      },
      {
        "title": "Account Pool Status",
        "type": "timeseries",
        "gridPos": {"x": 8, "y": 18, "w": 8, "h": 6},
        "targets": [
          {
            "expr": "sum by (status) (vise_account_pool_size)",
            "legendFormat": "{{status}}"
          }
        ]
      },
      {
        "title": "CAPTCHA Solve Rate",
        "type": "timeseries",
        "gridPos": {"x": 16, "y": 18, "w": 8, "h": 6},
        "targets": [
          {
            "expr": "sum(rate(vise_captcha_attempts_total{result='success'}[5m])) / sum(rate(vise_captcha_attempts_total[5m])) * 100",
            "legendFormat": "Success %"
          }
        ]
      }
    ]
  }
}
```

---

## Agency Dashboard

```yaml
# grafana/dashboards/agency-dashboard.json
{
  "dashboard": {
    "title": "Agency Dashboard - ${agency_id}",
    "uid": "vise-agency",
    "templating": {
      "list": [
        {
          "name": "agency_id",
          "type": "custom",
          "query": "agency_001,agency_002,agency_003"
        }
      ]
    },
    "panels": [
      {
        "title": "Your Bookings Today",
        "type": "stat",
        "gridPos": {"x": 0, "y": 0, "w": 6, "h": 4},
        "targets": [
          {
            "expr": "sum(increase(vise_booking_requests_total{agency_id='${agency_id}'}[24h]))"
          }
        ]
      },
      {
        "title": "Success Rate",
        "type": "gauge",
        "gridPos": {"x": 6, "y": 0, "w": 6, "h": 4},
        "targets": [
          {
            "expr": "sum(rate(vise_booking_requests_total{agency_id='${agency_id}',status='completed'}[24h])) / sum(rate(vise_booking_requests_total{agency_id='${agency_id}'}[24h])) * 100"
          }
        ]
      },
      {
        "title": "Credits Used",
        "type": "stat",
        "gridPos": {"x": 12, "y": 0, "w": 6, "h": 4},
        "targets": [
          {
            "expr": "sum(increase(vise_credits_used_total{agency_id='${agency_id}'}[24h]))"
          }
        ]
      },
      {
        "title": "Avg Booking Time",
        "type": "stat",
        "gridPos": {"x": 18, "y": 0, "w": 6, "h": 4},
        "targets": [
          {
            "expr": "avg(vise_booking_duration_seconds{agency_id='${agency_id}',result='success'})"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "unit": "s"
          }
        }
      },
      {
        "title": "Booking History",
        "type": "timeseries",
        "gridPos": {"x": 0, "y": 4, "w": 24, "h": 8},
        "targets": [
          {
            "expr": "sum(increase(vise_booking_requests_total{agency_id='${agency_id}',status='completed'}[1h]))",
            "legendFormat": "Completed"
          },
          {
            "expr": "sum(increase(vise_booking_requests_total{agency_id='${agency_id}',status='failed'}[1h]))",
            "legendFormat": "Failed"
          }
        ]
      },
      {
        "title": "Bookings by Country",
        "type": "piechart",
        "gridPos": {"x": 0, "y": 12, "w": 12, "h": 8},
        "targets": [
          {
            "expr": "sum by (country) (increase(vise_booking_requests_total{agency_id='${agency_id}'}[24h]))",
            "legendFormat": "{{country}}"
          }
        ]
      },
      {
        "title": "Recent Failures",
        "type": "table",
        "gridPos": {"x": 12, "y": 12, "w": 12, "h": 8},
        "targets": [
          {
            "expr": "sum by (reason) (increase(vise_booking_requests_total{agency_id='${agency_id}',status='failed'}[24h]))",
            "format": "table"
          }
        ]
      }
    ]
  }
}
```

---

## Loki Log Configuration

```yaml
# loki/loki-config.yaml
auth_enabled: false

server:
  http_listen_port: 3100

ingester:
  lifecycler:
    ring:
      kvstore:
        store: inmemory
      replication_factor: 1
  chunk_idle_period: 5m
  chunk_retain_period: 30s

schema_config:
  configs:
    - from: 2024-01-01
      store: boltdb-shipper
      object_store: filesystem
      schema: v11
      index:
        prefix: index_
        period: 24h

storage_config:
  boltdb_shipper:
    active_index_directory: /loki/index
    cache_location: /loki/cache
    shared_store: filesystem
  filesystem:
    directory: /loki/chunks

limits_config:
  enforce_metric_name: false
  reject_old_samples: true
  reject_old_samples_max_age: 168h

# Retention
compactor:
  working_directory: /loki/compactor
  shared_store: filesystem
  retention_enabled: true
  retention_delete_delay: 2h
  retention_delete_worker_count: 150

# Log labels
chunk_store_config:
  max_look_back_period: 0s

table_manager:
  retention_deletes_enabled: true
  retention_period: 720h  # 30 days
```

---

## Promtail Log Collector

```yaml
# promtail/promtail-config.yaml
server:
  http_listen_port: 9080
  grpc_listen_port: 0

positions:
  filename: /tmp/positions.yaml

clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:
  # Application logs
  - job_name: vise-app
    static_configs:
      - targets:
          - localhost
        labels:
          job: vise-app
          __path__: /var/log/vise/*.log
    pipeline_stages:
      - json:
          expressions:
            level: level
            message: message
            booking_id: booking_id
            agency_id: agency_id
            site: site
            timestamp: timestamp
      - labels:
          level:
          booking_id:
          agency_id:
          site:
      - timestamp:
          source: timestamp
          format: RFC3339

  # Worker logs
  - job_name: vise-workers
    static_configs:
      - targets:
          - localhost
        labels:
          job: vise-worker
          __path__: /var/log/vise/workers/*.log
    pipeline_stages:
      - json:
          expressions:
            level: level
            worker_id: worker_id
            task_id: task_id
            queue: queue
      - labels:
          level:
          worker_id:
          queue:

  # Browser session logs
  - job_name: vise-browser
    static_configs:
      - targets:
          - localhost
        labels:
          job: vise-browser
          __path__: /var/log/vise/browser/*.log
    pipeline_stages:
      - json:
          expressions:
            session_id: session_id
            site: site
            action: action
            url: url
      - labels:
          session_id:
          site:
          action:
```

---

## Real-time WebSocket Dashboard

```python
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import Dict, List, Set
import asyncio
import json

class RealtimeDashboard:
    """WebSocket-based real-time dashboard"""
    
    def __init__(self, metrics_collector):
        self.collector = metrics_collector
        self.connections: Dict[str, Set[WebSocket]] = {
            "overview": set(),
            "booking": set(),
            "queue": set(),
        }
    
    async def connect(self, websocket: WebSocket, channel: str):
        """Client connect"""
        await websocket.accept()
        self.connections[channel].add(websocket)
    
    def disconnect(self, websocket: WebSocket, channel: str):
        """Client disconnect"""
        self.connections[channel].discard(websocket)
    
    async def broadcast(self, channel: str, data: Dict):
        """Broadcast to channel"""
        message = json.dumps(data)
        
        dead_connections = set()
        
        for websocket in self.connections[channel]:
            try:
                await websocket.send_text(message)
            except:
                dead_connections.add(websocket)
        
        # Remove dead connections
        self.connections[channel] -= dead_connections
    
    async def start_broadcasting(self):
        """Broadcasting loop"""
        
        while True:
            # Overview metrics
            overview_data = await self._get_overview_metrics()
            await self.broadcast("overview", overview_data)
            
            # Booking updates
            booking_data = await self._get_booking_updates()
            await self.broadcast("booking", booking_data)
            
            # Queue status
            queue_data = await self._get_queue_status()
            await self.broadcast("queue", queue_data)
            
            await asyncio.sleep(2)  # 2 second refresh
    
    async def _get_overview_metrics(self) -> Dict:
        """Overview metrikleri"""
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "active_bookings": 42,
            "success_rate": 94.5,
            "queue_depth": 156,
            "active_workers": 23,
            "system_health": "healthy",
        }
    
    async def _get_booking_updates(self) -> Dict:
        """Son booking güncellemeleri"""
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "recent_completions": [
                {"booking_id": "ABC123", "site": "vfs", "duration": 145},
            ],
            "recent_failures": [
                {"booking_id": "XYZ789", "site": "bls", "reason": "slot_taken"},
            ],
        }
    
    async def _get_queue_status(self) -> Dict:
        """Queue durumu"""
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "queues": {
                "high_priority": 12,
                "normal": 89,
                "retry": 34,
                "vfs": 21,
            },
        }


# FastAPI WebSocket endpoints
app = FastAPI()
dashboard = RealtimeDashboard(None)

@app.websocket("/ws/overview")
async def ws_overview(websocket: WebSocket):
    await dashboard.connect(websocket, "overview")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        dashboard.disconnect(websocket, "overview")

@app.websocket("/ws/booking")
async def ws_booking(websocket: WebSocket):
    await dashboard.connect(websocket, "booking")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        dashboard.disconnect(websocket, "booking")
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | Prometheus metrics endpoint çalışmalı | HTTP test |
| AC-002 | Grafana dashboard'lar yüklenmeli | UI test |
| AC-003 | Loki log aggregation çalışmalı | Query test |
| AC-004 | WebSocket real-time update çalışmalı | WS test |
| AC-005 | Agency dashboard isolation çalışmalı | Auth test |
| AC-006 | Metrics retention 30 gün olmalı | Storage test |
| AC-007 | Dashboard response <2s olmalı | Performance test |

---

## Sonraki Adımlar

1. **019-ALERT-SYSTEM.md** - Alert tanımları
2. Grafana provisioning automation
3. Custom metrics fine-tuning
