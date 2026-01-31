# 019 - Alert System Specification

## Amaç

VISE OS için proaktif alert sistemi. Prometheus Alertmanager, multi-channel notifications (Telegram, Slack, SMS), escalation policies ve on-call routing. Kritik sorunlarda otomatik response ve human escalation.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 018-MONITORING-DASHBOARD | Metrics source |
| 014-QUEUE-ORCHESTRATOR | Queue alerts |
| 012-AI-DECISION-ENGINE | AI escalation decisions |
| 020-ANALYTICS-REPORTING | Alert statistics |

---

## Alert Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          ALERT SYSTEM                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │
│  │  Prometheus  │    │   Custom     │    │   External   │                  │
│  │   Alerts     │───►│   Rules      │───►│   Triggers   │                  │
│  └──────────────┘    └──────────────┘    └──────────────┘                  │
│          │                  │                   │                           │
│          └──────────────────┼───────────────────┘                           │
│                             ▼                                               │
│                  ┌──────────────────┐                                       │
│                  │  ALERTMANAGER    │                                       │
│                  │  • Deduplication │                                       │
│                  │  • Grouping      │                                       │
│                  │  • Silencing     │                                       │
│                  │  • Routing       │                                       │
│                  └────────┬─────────┘                                       │
│                           │                                                  │
│          ┌────────────────┼────────────────┐                                │
│          ▼                ▼                ▼                                │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐                        │
│  │   Telegram   │ │    Slack     │ │    SMS       │                        │
│  │   Channel    │ │   Channel    │ │   Gateway    │                        │
│  └──────────────┘ └──────────────┘ └──────────────┘                        │
│          │                │                │                                │
│          └────────────────┼────────────────┘                                │
│                           ▼                                                  │
│                  ┌──────────────────┐                                       │
│                  │  ON-CALL ROUTER  │                                       │
│                  │  • Schedule      │                                       │
│                  │  • Escalation    │                                       │
│                  │  • Acknowledgment│                                       │
│                  └──────────────────┘                                       │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Alert Severity Levels

```python
from enum import Enum
from dataclasses import dataclass
from typing import List, Optional
from datetime import timedelta

class AlertSeverity(Enum):
    """Alert önem seviyeleri"""
    CRITICAL = "critical"    # Immediate action required
    HIGH = "high"            # Action within 15 minutes
    MEDIUM = "medium"        # Action within 1 hour
    LOW = "low"              # Action within 24 hours
    INFO = "info"            # No action required

class AlertCategory(Enum):
    """Alert kategorileri"""
    BOOKING = "booking"
    SYSTEM = "system"
    SECURITY = "security"
    PAYMENT = "payment"
    EXTERNAL = "external"
    CAPACITY = "capacity"

@dataclass
class AlertConfig:
    """Alert configuration"""
    severity: AlertSeverity
    category: AlertCategory
    
    # Timing
    evaluation_interval: timedelta = timedelta(minutes=1)
    for_duration: timedelta = timedelta(minutes=5)
    
    # Routing
    channels: List[str] = None  # telegram, slack, sms
    escalation_delay: timedelta = timedelta(minutes=15)
    
    # Auto-response
    auto_remediation: bool = False
    remediation_action: Optional[str] = None

# Alert configurations
ALERT_CONFIGS = {
    "booking_success_rate_critical": AlertConfig(
        severity=AlertSeverity.CRITICAL,
        category=AlertCategory.BOOKING,
        channels=["telegram", "slack", "sms"],
        escalation_delay=timedelta(minutes=5),
    ),
    "queue_backlog_high": AlertConfig(
        severity=AlertSeverity.HIGH,
        category=AlertCategory.CAPACITY,
        channels=["telegram", "slack"],
        escalation_delay=timedelta(minutes=10),
        auto_remediation=True,
        remediation_action="scale_workers_up",
    ),
    "account_ban_rate_elevated": AlertConfig(
        severity=AlertSeverity.MEDIUM,
        category=AlertCategory.SECURITY,
        channels=["telegram"],
        escalation_delay=timedelta(minutes=30),
    ),
    "external_service_down": AlertConfig(
        severity=AlertSeverity.HIGH,
        category=AlertCategory.EXTERNAL,
        channels=["telegram", "slack"],
        escalation_delay=timedelta(minutes=15),
    ),
}
```

---

## Prometheus Alert Rules

```yaml
# prometheus/alerts/booking.yml
groups:
  - name: booking_alerts
    interval: 30s
    rules:
      # Critical: Success rate dropped below 50%
      - alert: BookingSuccessRateCritical
        expr: |
          (
            sum(rate(vise_booking_requests_total{status="completed"}[10m])) 
            / 
            sum(rate(vise_booking_requests_total[10m]))
          ) * 100 < 50
        for: 5m
        labels:
          severity: critical
          category: booking
        annotations:
          summary: "Booking success rate critical: {{ $value | printf \"%.1f\" }}%"
          description: "Booking success rate has dropped below 50% for 5 minutes"
          runbook: "https://docs.vise.os/runbooks/booking-success-rate"
          
      # High: Success rate below 70%
      - alert: BookingSuccessRateHigh
        expr: |
          (
            sum(rate(vise_booking_requests_total{status="completed"}[10m])) 
            / 
            sum(rate(vise_booking_requests_total[10m]))
          ) * 100 < 70
        for: 10m
        labels:
          severity: high
          category: booking
        annotations:
          summary: "Booking success rate degraded: {{ $value | printf \"%.1f\" }}%"
          description: "Booking success rate is below 70%"
          
      # Site-specific failure spike
      - alert: SiteFailureSpike
        expr: |
          sum by (site) (
            increase(vise_booking_requests_total{status="failed"}[5m])
          ) > 10
        for: 5m
        labels:
          severity: high
          category: booking
        annotations:
          summary: "Failure spike on {{ $labels.site }}"
          description: "{{ $value }} failures in last 5 minutes"
          
      # Booking duration anomaly
      - alert: BookingDurationAnomaly
        expr: |
          histogram_quantile(0.95, 
            sum by (site, le) (rate(vise_booking_duration_seconds_bucket[15m]))
          ) > 600
        for: 10m
        labels:
          severity: medium
          category: booking
        annotations:
          summary: "Booking duration elevated on {{ $labels.site }}"
          description: "P95 booking duration is {{ $value | printf \"%.0f\" }} seconds"
```

```yaml
# prometheus/alerts/system.yml
groups:
  - name: system_alerts
    interval: 30s
    rules:
      # Queue backlog
      - alert: QueueBacklogCritical
        expr: sum(vise_queue_size) > 1000
        for: 5m
        labels:
          severity: critical
          category: capacity
        annotations:
          summary: "Queue backlog critical: {{ $value }} tasks"
          description: "Queue has more than 1000 pending tasks"
          remediation: "auto:scale_workers_up"
          
      - alert: QueueBacklogHigh
        expr: sum(vise_queue_size) > 500
        for: 10m
        labels:
          severity: high
          category: capacity
        annotations:
          summary: "Queue backlog elevated: {{ $value }} tasks"
          
      # Worker health
      - alert: WorkerCountLow
        expr: sum(vise_worker_count) < 5
        for: 5m
        labels:
          severity: high
          category: system
        annotations:
          summary: "Worker count low: {{ $value }} workers"
          remediation: "auto:restart_workers"
          
      - alert: NoActiveWorkers
        expr: sum(vise_worker_count) == 0
        for: 2m
        labels:
          severity: critical
          category: system
        annotations:
          summary: "No active workers!"
          description: "All workers are down"
          
      # System resources
      - alert: HighCPUUsage
        expr: avg(vise_system_cpu_percent) > 90
        for: 10m
        labels:
          severity: high
          category: system
        annotations:
          summary: "High CPU usage: {{ $value | printf \"%.1f\" }}%"
          
      - alert: HighMemoryUsage
        expr: avg(vise_system_memory_percent) > 90
        for: 10m
        labels:
          severity: high
          category: system
        annotations:
          summary: "High memory usage: {{ $value | printf \"%.1f\" }}%"
          
      - alert: DiskSpaceLow
        expr: max(vise_system_disk_percent) > 85
        for: 30m
        labels:
          severity: medium
          category: system
        annotations:
          summary: "Disk space low: {{ $value | printf \"%.1f\" }}%"
```

```yaml
# prometheus/alerts/resources.yml
groups:
  - name: resource_alerts
    interval: 30s
    rules:
      # Proxy pool
      - alert: ProxyPoolExhausted
        expr: |
          sum(vise_proxy_pool_size{status="available"}) < 10
        for: 5m
        labels:
          severity: high
          category: capacity
        annotations:
          summary: "Proxy pool nearly exhausted: {{ $value }} available"
          
      - alert: ProxySuccessRateLow
        expr: |
          avg(vise_proxy_success_rate) < 0.7
        for: 10m
        labels:
          severity: medium
          category: external
        annotations:
          summary: "Proxy success rate low: {{ $value | printf \"%.1f\" }}%"
          
      # Account pool
      - alert: AccountPoolExhausted
        expr: |
          sum by (site) (vise_account_pool_size{status="available"}) < 5
        for: 5m
        labels:
          severity: high
          category: capacity
        annotations:
          summary: "Account pool low for {{ $labels.site }}: {{ $value }} available"
          
      - alert: AccountBanRateHigh
        expr: |
          sum by (site) (vise_account_ban_rate) > 0.1
        for: 15m
        labels:
          severity: high
          category: security
        annotations:
          summary: "High ban rate on {{ $labels.site }}: {{ $value | printf \"%.1f\" }}%"
          description: "Account ban rate exceeds 10%"
          
      # CAPTCHA
      - alert: CaptchaSolveRateLow
        expr: |
          (
            sum(rate(vise_captcha_attempts_total{result="success"}[10m]))
            /
            sum(rate(vise_captcha_attempts_total[10m]))
          ) < 0.8
        for: 10m
        labels:
          severity: medium
          category: external
        annotations:
          summary: "CAPTCHA solve rate low: {{ $value | printf \"%.1f\" }}%"
          
      # Payment
      - alert: PaymentFailureRateHigh
        expr: |
          (
            sum(rate(vise_payment_attempts_total{result="failed"}[15m]))
            /
            sum(rate(vise_payment_attempts_total[15m]))
          ) > 0.2
        for: 10m
        labels:
          severity: high
          category: payment
        annotations:
          summary: "Payment failure rate high: {{ $value | printf \"%.1f\" }}%"
```

---

## Alertmanager Configuration

```yaml
# alertmanager/alertmanager.yml
global:
  resolve_timeout: 5m
  
  # Telegram config
  telegram_api_url: "https://api.telegram.org"
  
  # Slack config
  slack_api_url: "https://hooks.slack.com/services/xxx"

# Route tree
route:
  receiver: 'default'
  group_by: ['alertname', 'severity', 'category']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  
  routes:
    # Critical alerts - immediate, all channels
    - match:
        severity: critical
      receiver: 'critical-alerts'
      group_wait: 10s
      group_interval: 1m
      repeat_interval: 30m
      continue: true
      
    # High alerts - Telegram + Slack
    - match:
        severity: high
      receiver: 'high-alerts'
      group_wait: 30s
      group_interval: 5m
      repeat_interval: 2h
      
    # Payment alerts - special routing
    - match:
        category: payment
      receiver: 'payment-alerts'
      group_wait: 30s
      
    # Security alerts
    - match:
        category: security
      receiver: 'security-alerts'
      
    # Low/Info - just logging
    - match_re:
        severity: low|info
      receiver: 'logging-only'

# Receivers
receivers:
  - name: 'default'
    telegram_configs:
      - bot_token: '${TELEGRAM_BOT_TOKEN}'
        chat_id: ${TELEGRAM_CHAT_ID}
        parse_mode: 'HTML'
        message: |
          {{ template "telegram.default" . }}

  - name: 'critical-alerts'
    telegram_configs:
      - bot_token: '${TELEGRAM_BOT_TOKEN}'
        chat_id: ${TELEGRAM_CRITICAL_CHAT}
        parse_mode: 'HTML'
        message: |
          🚨 <b>CRITICAL ALERT</b> 🚨
          
          {{ range .Alerts }}
          <b>{{ .Labels.alertname }}</b>
          {{ .Annotations.summary }}
          
          {{ if .Annotations.description }}
          {{ .Annotations.description }}
          {{ end }}
          {{ end }}
    slack_configs:
      - channel: '#vise-critical'
        send_resolved: true
        title: '🚨 Critical Alert'
        text: |
          {{ range .Alerts }}
          *{{ .Labels.alertname }}*
          {{ .Annotations.summary }}
          {{ end }}
    # SMS for on-call
    webhook_configs:
      - url: 'http://sms-gateway:8080/send'
        send_resolved: true

  - name: 'high-alerts'
    telegram_configs:
      - bot_token: '${TELEGRAM_BOT_TOKEN}'
        chat_id: ${TELEGRAM_ALERTS_CHAT}
        parse_mode: 'HTML'
        message: |
          ⚠️ <b>HIGH ALERT</b>
          
          {{ range .Alerts }}
          <b>{{ .Labels.alertname }}</b>
          {{ .Annotations.summary }}
          {{ end }}
    slack_configs:
      - channel: '#vise-alerts'
        send_resolved: true

  - name: 'payment-alerts'
    telegram_configs:
      - bot_token: '${TELEGRAM_BOT_TOKEN}'
        chat_id: ${TELEGRAM_PAYMENT_CHAT}
        parse_mode: 'HTML'
        message: |
          💳 <b>PAYMENT ALERT</b>
          
          {{ range .Alerts }}
          {{ .Annotations.summary }}
          {{ end }}

  - name: 'security-alerts'
    telegram_configs:
      - bot_token: '${TELEGRAM_BOT_TOKEN}'
        chat_id: ${TELEGRAM_SECURITY_CHAT}
        parse_mode: 'HTML'
        message: |
          🔒 <b>SECURITY ALERT</b>
          
          {{ range .Alerts }}
          {{ .Annotations.summary }}
          {{ end }}

  - name: 'logging-only'
    # Just webhook for logging, no notifications
    webhook_configs:
      - url: 'http://alert-logger:8080/log'

# Inhibition rules
inhibit_rules:
  # If critical firing, suppress high
  - source_match:
      severity: 'critical'
    target_match:
      severity: 'high'
    equal: ['alertname', 'site']
    
  # If system down, suppress component alerts
  - source_match:
      alertname: 'NoActiveWorkers'
    target_match_re:
      alertname: 'Queue.*|Booking.*'

# Templates
templates:
  - '/etc/alertmanager/templates/*.tmpl'
```

---

## Notification Templates

```go
// alertmanager/templates/telegram.tmpl
{{ define "telegram.default" }}
{{ if eq .Status "firing" }}🔴{{ else }}✅{{ end }} <b>{{ .Status | toUpper }}</b>

{{ range .Alerts }}
<b>Alert:</b> {{ .Labels.alertname }}
<b>Severity:</b> {{ .Labels.severity }}
<b>Category:</b> {{ .Labels.category }}

{{ if .Annotations.summary }}
<b>Summary:</b> {{ .Annotations.summary }}
{{ end }}

{{ if .Annotations.description }}
{{ .Annotations.description }}
{{ end }}

{{ if .Annotations.runbook }}
📖 <a href="{{ .Annotations.runbook }}">Runbook</a>
{{ end }}

{{ if .Annotations.remediation }}
🔧 Auto-remediation: {{ .Annotations.remediation }}
{{ end }}

<i>Started: {{ .StartsAt.Format "2006-01-02 15:04:05" }}</i>
---
{{ end }}
{{ end }}
```

---

## Auto-Remediation Engine

```python
from typing import Dict, Callable, Any
from dataclasses import dataclass
import asyncio

@dataclass
class RemediationAction:
    """Remediation action tanımı"""
    name: str
    handler: Callable
    timeout_seconds: int = 60
    requires_approval: bool = False

class AutoRemediationEngine:
    """Otomatik düzeltme motoru"""
    
    def __init__(
        self,
        queue_manager,
        worker_manager,
        proxy_manager,
        account_manager,
    ):
        self.queue = queue_manager
        self.workers = worker_manager
        self.proxies = proxy_manager
        self.accounts = account_manager
        
        # Remediation handlers
        self.actions: Dict[str, RemediationAction] = {
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
        alert_context: Dict,
        approved: bool = False,
    ) -> Dict[str, Any]:
        """Remediation çalıştır"""
        
        if action_name not in self.actions:
            return {
                "success": False,
                "error": f"Unknown action: {action_name}",
            }
        
        action = self.actions[action_name]
        
        # Check approval
        if action.requires_approval and not approved:
            return {
                "success": False,
                "error": "Action requires manual approval",
                "requires_approval": True,
            }
        
        try:
            # Execute with timeout
            result = await asyncio.wait_for(
                action.handler(alert_context),
                timeout=action.timeout_seconds,
            )
            
            return {
                "success": True,
                "action": action_name,
                "result": result,
            }
            
        except asyncio.TimeoutError:
            return {
                "success": False,
                "error": "Remediation timed out",
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }
    
    async def _scale_workers_up(self, context: Dict) -> Dict:
        """Worker'ları scale up"""
        
        current_workers = await self.workers.get_count()
        target = min(current_workers + 5, 50)  # Max 50
        
        await self.workers.scale_to(target)
        
        return {
            "previous": current_workers,
            "current": target,
            "action": "scaled_up",
        }
    
    async def _scale_workers_down(self, context: Dict) -> Dict:
        """Worker'ları scale down"""
        
        current_workers = await self.workers.get_count()
        target = max(current_workers - 3, 5)  # Min 5
        
        await self.workers.scale_to(target)
        
        return {
            "previous": current_workers,
            "current": target,
            "action": "scaled_down",
        }
    
    async def _restart_workers(self, context: Dict) -> Dict:
        """Worker'ları restart"""
        
        await self.workers.restart_all()
        
        return {"action": "restarted"}
    
    async def _rotate_proxies(self, context: Dict) -> Dict:
        """Proxy pool rotate"""
        
        rotated = await self.proxies.rotate_all()
        
        return {
            "rotated_count": rotated,
            "action": "proxies_rotated",
        }
    
    async def _pause_site(self, context: Dict) -> Dict:
        """Site operasyonlarını durdur"""
        
        site = context.get("site", "all")
        
        await self.queue.pause_queues([site] if site != "all" else None)
        
        return {
            "site": site,
            "action": "paused",
        }
    
    async def _clear_cooldowns(self, context: Dict) -> Dict:
        """Account cooldown'larını temizle"""
        
        site = context.get("site")
        cleared = await self.accounts.clear_cooldowns(site)
        
        return {
            "cleared_count": cleared,
            "action": "cooldowns_cleared",
        }
```

---

## On-Call Schedule

```python
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import List, Optional, Dict
from enum import Enum

class OnCallTier(Enum):
    """On-call seviyeleri"""
    PRIMARY = "primary"
    SECONDARY = "secondary"
    MANAGEMENT = "management"

@dataclass
class OnCallPerson:
    """On-call kişisi"""
    id: str
    name: str
    telegram_id: str
    phone: str
    email: str
    tier: OnCallTier

@dataclass
class OnCallSchedule:
    """On-call programı"""
    person: OnCallPerson
    start_time: datetime
    end_time: datetime

class OnCallRouter:
    """On-call routing"""
    
    # Escalation timeouts
    ESCALATION_TIMEOUTS = {
        OnCallTier.PRIMARY: timedelta(minutes=15),
        OnCallTier.SECONDARY: timedelta(minutes=30),
        OnCallTier.MANAGEMENT: timedelta(minutes=60),
    }
    
    def __init__(self):
        self.schedules: List[OnCallSchedule] = []
        self.active_alerts: Dict[str, Dict] = {}
    
    def get_current_oncall(
        self,
        tier: OnCallTier = OnCallTier.PRIMARY,
    ) -> Optional[OnCallPerson]:
        """Şu anki on-call kişisini bul"""
        
        now = datetime.utcnow()
        
        for schedule in self.schedules:
            if (schedule.person.tier == tier and 
                schedule.start_time <= now <= schedule.end_time):
                return schedule.person
        
        return None
    
    async def route_alert(
        self,
        alert_id: str,
        severity: AlertSeverity,
        message: str,
    ) -> Dict:
        """Alert'i route et"""
        
        # Find primary on-call
        primary = self.get_current_oncall(OnCallTier.PRIMARY)
        
        if not primary:
            # Fallback to management
            primary = self.get_current_oncall(OnCallTier.MANAGEMENT)
        
        if not primary:
            return {
                "success": False,
                "error": "No on-call person available",
            }
        
        # Track alert
        self.active_alerts[alert_id] = {
            "severity": severity,
            "message": message,
            "routed_to": primary.id,
            "routed_at": datetime.utcnow(),
            "acknowledged": False,
        }
        
        # Send notification
        await self._notify_person(primary, alert_id, message)
        
        # Schedule escalation
        if severity in [AlertSeverity.CRITICAL, AlertSeverity.HIGH]:
            asyncio.create_task(
                self._schedule_escalation(alert_id, primary.tier)
            )
        
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
        """Alert'i acknowledge et"""
        
        if alert_id not in self.active_alerts:
            return False
        
        self.active_alerts[alert_id]["acknowledged"] = True
        self.active_alerts[alert_id]["acknowledged_by"] = person_id
        self.active_alerts[alert_id]["acknowledged_at"] = datetime.utcnow()
        
        return True
    
    async def _notify_person(
        self,
        person: OnCallPerson,
        alert_id: str,
        message: str,
    ):
        """Kişiye bildirim gönder"""
        
        # Telegram notification
        await self._send_telegram(
            person.telegram_id,
            f"🚨 Alert #{alert_id}\n\n{message}\n\n"
            f"Reply /ack {alert_id} to acknowledge",
        )
    
    async def _schedule_escalation(
        self,
        alert_id: str,
        current_tier: OnCallTier,
    ):
        """Escalation planla"""
        
        timeout = self.ESCALATION_TIMEOUTS[current_tier]
        
        await asyncio.sleep(timeout.total_seconds())
        
        # Check if acknowledged
        alert = self.active_alerts.get(alert_id)
        
        if alert and not alert.get("acknowledged"):
            # Escalate to next tier
            next_tier = self._get_next_tier(current_tier)
            
            if next_tier:
                next_person = self.get_current_oncall(next_tier)
                
                if next_person:
                    await self._notify_person(
                        next_person,
                        alert_id,
                        f"⬆️ ESCALATED from {current_tier.value}\n\n{alert['message']}",
                    )
                    
                    # Schedule next escalation
                    asyncio.create_task(
                        self._schedule_escalation(alert_id, next_tier)
                    )
    
    def _get_next_tier(self, current: OnCallTier) -> Optional[OnCallTier]:
        """Sonraki tier"""
        
        order = [OnCallTier.PRIMARY, OnCallTier.SECONDARY, OnCallTier.MANAGEMENT]
        
        try:
            idx = order.index(current)
            if idx < len(order) - 1:
                return order[idx + 1]
        except ValueError:
            pass
        
        return None
    
    async def _send_telegram(self, chat_id: str, message: str):
        """Telegram mesajı gönder"""
        # Implementation
        pass
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | Alert rules Prometheus'ta yüklenmeli | Config test |
| AC-002 | Critical alerts <1 dakikada iletilmeli | Timing test |
| AC-003 | Telegram notifications çalışmalı | Integration test |
| AC-004 | Slack notifications çalışmalı | Integration test |
| AC-005 | Auto-remediation scale_workers çalışmalı | Action test |
| AC-006 | On-call escalation çalışmalı | Escalation test |
| AC-007 | Alert acknowledgment çalışmalı | Ack test |

---

## Alert Response Matrix

| Alert | Severity | Auto-Action | Manual Action |
|-------|----------|-------------|---------------|
| BookingSuccessRateCritical | Critical | - | Investigate immediately |
| QueueBacklogCritical | Critical | Scale up | Monitor |
| NoActiveWorkers | Critical | Restart | Check infrastructure |
| AccountBanRateHigh | High | Pause site | Review strategy |
| ProxyPoolExhausted | High | Rotate | Add more proxies |
| PaymentFailureRateHigh | High | - | Check gateway |

---

## Sonraki Adımlar

1. **020-ANALYTICS-REPORTING.md** - Analytics ve raporlama
2. Runbook dökümanları
3. Alert tuning ve threshold optimization
