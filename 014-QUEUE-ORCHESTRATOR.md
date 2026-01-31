# 014 - Queue Orchestrator Specification

## Amaç

200 paralel browser session için Celery/Redis tabanlı dağıtık iş kuyruğu. Priority-based routing, worker coordination, rate limiting ve graceful scaling. Günlük 1000+ booking kapasitesi.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 001-ARCHITECTURE-OVERVIEW | Orchestration layer |
| 002-DIRECTUS-SCHEMA | booking_requests source |
| 013-STATE-MACHINE | State-based routing |
| 008-011 | Site adapters - task execution |

---

## Queue Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           QUEUE ORCHESTRATOR                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐         │
│  │  Task Producer  │    │  Celery Beat    │    │  Priority       │         │
│  │  (API/Sheets)   │    │  (Scheduler)    │    │  Router         │         │
│  └────────┬────────┘    └────────┬────────┘    └────────┬────────┘         │
│           │                      │                      │                   │
│           ▼                      ▼                      ▼                   │
│  ┌──────────────────────────────────────────────────────────────┐          │
│  │                      REDIS QUEUES                             │          │
│  ├──────────────────────────────────────────────────────────────┤          │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │          │
│  │  │ HIGH_PRIO   │  │  NORMAL     │  │   RETRY     │          │          │
│  │  │ (premium)   │  │  (default)  │  │  (backoff)  │          │          │
│  │  └─────────────┘  └─────────────┘  └─────────────┘          │          │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │          │
│  │  │ SCHEDULED   │  │  NIGHT_OPS  │  │   DEAD      │          │          │
│  │  │ (delayed)   │  │  (02:00-06) │  │  (failed)   │          │          │
│  │  └─────────────┘  └─────────────┘  └─────────────┘          │          │
│  └──────────────────────────────────────────────────────────────┘          │
│                                │                                            │
│                                ▼                                            │
│  ┌──────────────────────────────────────────────────────────────┐          │
│  │                     WORKER POOLS                              │          │
│  ├──────────────────────────────────────────────────────────────┤          │
│  │  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐ │          │
│  │  │ VFS Pool  │  │iDATA Pool │  │ BLS Pool  │  │ Generic   │ │          │
│  │  │ (10 wrk)  │  │ (5 wrk)   │  │ (3 wrk)   │  │ (5 wrk)   │ │          │
│  │  └───────────┘  └───────────┘  └───────────┘  └───────────┘ │          │
│  └──────────────────────────────────────────────────────────────┘          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Redis Queue Configuration

```python
from celery import Celery
from kombu import Queue, Exchange
from typing import Dict, Any

# Queue definitions
QUEUE_CONFIG = {
    "high_priority": {
        "exchange": "booking",
        "routing_key": "booking.high",
        "queue_arguments": {
            "x-max-priority": 10,
        },
    },
    "normal": {
        "exchange": "booking",
        "routing_key": "booking.normal",
        "queue_arguments": {
            "x-max-priority": 5,
        },
    },
    "retry": {
        "exchange": "booking",
        "routing_key": "booking.retry",
        "queue_arguments": {
            "x-message-ttl": 3600000,  # 1 hour max
        },
    },
    "scheduled": {
        "exchange": "booking",
        "routing_key": "booking.scheduled",
    },
    "night_ops": {
        "exchange": "booking",
        "routing_key": "booking.night",
    },
    "dead_letter": {
        "exchange": "booking",
        "routing_key": "booking.dead",
    },
}

# Site-specific queues
SITE_QUEUES = {
    "vfs": {
        "exchange": "sites",
        "routing_key": "sites.vfs",
    },
    "idata": {
        "exchange": "sites",
        "routing_key": "sites.idata",
    },
    "bls": {
        "exchange": "sites",
        "routing_key": "sites.bls",
    },
    "kkosmos": {
        "exchange": "sites",
        "routing_key": "sites.kkosmos",
    },
}

def create_celery_app(redis_url: str) -> Celery:
    """Celery app oluştur"""
    
    app = Celery(
        "vise_os",
        broker=redis_url,
        backend=redis_url,
    )
    
    # Celery configuration
    app.conf.update(
        # Task settings
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="Europe/Istanbul",
        enable_utc=True,
        
        # Worker settings
        worker_prefetch_multiplier=1,  # Fair distribution
        worker_concurrency=10,         # Per worker
        worker_max_tasks_per_child=100,  # Memory leak prevention
        
        # Task execution
        task_acks_late=True,           # Ack after completion
        task_reject_on_worker_lost=True,
        task_time_limit=600,           # 10 minute hard limit
        task_soft_time_limit=540,      # 9 minute soft limit
        
        # Result backend
        result_expires=3600,           # 1 hour
        
        # Queue definitions
        task_queues=(
            Queue("high_priority", Exchange("booking"), routing_key="booking.high"),
            Queue("normal", Exchange("booking"), routing_key="booking.normal"),
            Queue("retry", Exchange("booking"), routing_key="booking.retry"),
            Queue("scheduled", Exchange("booking"), routing_key="booking.scheduled"),
            Queue("night_ops", Exchange("booking"), routing_key="booking.night"),
            Queue("dead_letter", Exchange("booking"), routing_key="booking.dead"),
            Queue("vfs", Exchange("sites"), routing_key="sites.vfs"),
            Queue("idata", Exchange("sites"), routing_key="sites.idata"),
            Queue("bls", Exchange("sites"), routing_key="sites.bls"),
            Queue("kkosmos", Exchange("sites"), routing_key="sites.kkosmos"),
        ),
        
        # Default queue
        task_default_queue="normal",
        task_default_exchange="booking",
        task_default_routing_key="booking.normal",
        
        # Priority support
        task_queue_max_priority=10,
        task_default_priority=5,
    )
    
    return app
```

---

## Task Definitions

```python
from celery import shared_task, Task
from typing import Dict, Any, Optional
from datetime import datetime
import asyncio

class BookingTask(Task):
    """Base booking task with error handling"""
    
    autoretry_for = (Exception,)
    retry_backoff = True
    retry_backoff_max = 600  # 10 minutes max
    retry_jitter = True
    max_retries = 3
    
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Failure callback"""
        booking_id = kwargs.get("booking_id") or args[0] if args else None
        
        if booking_id:
            # Move to dead letter queue
            self.app.send_task(
                "tasks.handle_dead_letter",
                args=[booking_id, str(exc)],
                queue="dead_letter",
            )
    
    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """Retry callback"""
        booking_id = kwargs.get("booking_id") or args[0] if args else None
        
        # Log retry
        print(f"Retrying task {task_id} for booking {booking_id}: {exc}")


# Main booking task
@shared_task(
    bind=True,
    base=BookingTask,
    name="tasks.process_booking",
    queue="normal",
)
def process_booking(
    self,
    booking_id: str,
    site: str,
    priority: int = 5,
) -> Dict[str, Any]:
    """Ana booking task"""
    
    # Run async code in sync task
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        result = loop.run_until_complete(
            _async_process_booking(booking_id, site)
        )
        return result
    finally:
        loop.close()


async def _async_process_booking(
    booking_id: str,
    site: str,
) -> Dict[str, Any]:
    """Async booking işlemi"""
    
    from adapters import get_adapter
    from state_machine import BookingStateMachine, StatePersistenceService
    
    # Load booking context
    persistence = StatePersistenceService()
    context = await persistence.load(booking_id)
    
    if not context:
        return {"success": False, "error": "Booking not found"}
    
    # Get site adapter
    adapter = get_adapter(site)
    
    # State machine
    state_machine = BookingStateMachine()
    
    # Transition to processing
    await state_machine.transition(
        context,
        BookingState.PROCESSING,
        trigger="task_started",
    )
    await persistence.save(context)
    
    try:
        # Execute booking
        result = await adapter.book_appointment(
            target_country=context.target_country,
            visa_category=context.visa_category,
            applicant_data=context.applicant_data,
            preferred_dates=context.preferred_dates,
            payment_card=context.payment_card,
        )
        
        if result.success:
            # Transition to completed
            context.confirmation_number = result.confirmation_number
            context.appointment_date = str(result.appointment_date)
            context.appointment_time = result.appointment_time
            
            await state_machine.transition(
                context,
                BookingState.COMPLETED,
                trigger="booking_success",
            )
        else:
            # Transition to failed
            context.last_error = result.error_message
            context.failure_reason = FailureReason.SYSTEM_ERROR
            
            await state_machine.transition(
                context,
                BookingState.FAILED,
                trigger="booking_failed",
            )
        
        await persistence.save(context)
        return {"success": result.success, "booking_id": booking_id}
        
    except Exception as e:
        context.last_error = str(e)
        await persistence.save(context)
        raise


# Site-specific tasks
@shared_task(
    bind=True,
    base=BookingTask,
    name="tasks.vfs_booking",
    queue="vfs",
)
def vfs_booking(self, booking_id: str) -> Dict[str, Any]:
    """VFS-specific booking task"""
    return process_booking.apply(args=[booking_id, "vfs"]).get()


@shared_task(
    bind=True,
    base=BookingTask,
    name="tasks.idata_booking",
    queue="idata",
)
def idata_booking(self, booking_id: str) -> Dict[str, Any]:
    """iDATA-specific booking task"""
    return process_booking.apply(args=[booking_id, "idata"]).get()


@shared_task(
    bind=True,
    base=BookingTask,
    name="tasks.bls_booking",
    queue="bls",
)
def bls_booking(self, booking_id: str) -> Dict[str, Any]:
    """BLS-specific booking task"""
    return process_booking.apply(args=[booking_id, "bls"]).get()


# Scheduled tasks
@shared_task(name="tasks.sync_google_sheets")
def sync_google_sheets(agency_id: str = None) -> Dict[str, Any]:
    """Google Sheets senkronizasyonu"""
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        return loop.run_until_complete(_async_sync_sheets(agency_id))
    finally:
        loop.close()


@shared_task(name="tasks.cleanup_pii")
def cleanup_pii() -> Dict[str, Any]:
    """PII temizleme görevi"""
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        return loop.run_until_complete(_async_cleanup_pii())
    finally:
        loop.close()


@shared_task(name="tasks.health_check_accounts")
def health_check_accounts() -> Dict[str, Any]:
    """Account pool sağlık kontrolü"""
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        return loop.run_until_complete(_async_health_check())
    finally:
        loop.close()
```

---

## Priority Router

```python
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from datetime import datetime, timedelta

class BookingPriority(Enum):
    """Booking öncelik seviyeleri"""
    CRITICAL = 10    # Premium müşteri, acil randevu
    HIGH = 8         # Premium müşteri, normal
    ELEVATED = 6     # Standart müşteri, yakın tarih
    NORMAL = 5       # Standart müşteri
    LOW = 3          # Toplu randevu, esnek tarih
    BACKGROUND = 1   # Gece işlemi, en düşük

@dataclass
class PriorityFactors:
    """Öncelik hesaplama faktörleri"""
    is_premium_agency: bool = False
    date_urgency_days: int = 30
    retry_count: int = 0
    slot_rarity: float = 1.0  # 0-1, 1=very rare
    payment_value: float = 0.0

class PriorityRouter:
    """Booking öncelik router"""
    
    def calculate_priority(
        self,
        factors: PriorityFactors,
    ) -> BookingPriority:
        """Öncelik hesapla"""
        
        score = 5  # Base score (NORMAL)
        
        # Premium agency bonus
        if factors.is_premium_agency:
            score += 2
        
        # Date urgency
        if factors.date_urgency_days <= 7:
            score += 3
        elif factors.date_urgency_days <= 14:
            score += 2
        elif factors.date_urgency_days <= 21:
            score += 1
        
        # Slot rarity
        if factors.slot_rarity >= 0.8:  # Rare slot
            score += 2
        elif factors.slot_rarity >= 0.5:
            score += 1
        
        # Retry penalty (daha önce denendi, belki sorunlu)
        if factors.retry_count > 0:
            score -= min(factors.retry_count, 2)
        
        # Payment value bonus
        if factors.payment_value >= 200:  # EUR
            score += 1
        
        # Clamp and convert to enum
        score = max(1, min(10, score))
        
        if score >= 10:
            return BookingPriority.CRITICAL
        elif score >= 8:
            return BookingPriority.HIGH
        elif score >= 6:
            return BookingPriority.ELEVATED
        elif score >= 4:
            return BookingPriority.NORMAL
        elif score >= 2:
            return BookingPriority.LOW
        else:
            return BookingPriority.BACKGROUND
    
    def select_queue(
        self,
        site: str,
        priority: BookingPriority,
        scheduled_time: Optional[datetime] = None,
    ) -> str:
        """Uygun queue seç"""
        
        now = datetime.utcnow()
        
        # Night operations (02:00-06:00 Istanbul)
        istanbul_hour = (now.hour + 3) % 24  # UTC+3
        if 2 <= istanbul_hour < 6:
            return "night_ops"
        
        # Scheduled task
        if scheduled_time and scheduled_time > now:
            return "scheduled"
        
        # Priority-based selection
        if priority in [BookingPriority.CRITICAL, BookingPriority.HIGH]:
            return "high_priority"
        
        # Site-specific for better load balancing
        if site in ["vfs", "idata", "bls", "kkosmos"]:
            return site
        
        return "normal"
    
    def get_task_options(
        self,
        priority: BookingPriority,
        scheduled_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Task options oluştur"""
        
        options = {
            "priority": priority.value,
        }
        
        if scheduled_time:
            options["eta"] = scheduled_time
        
        # Time limits based on priority
        if priority == BookingPriority.CRITICAL:
            options["time_limit"] = 300  # 5 minutes max
            options["soft_time_limit"] = 240
        elif priority in [BookingPriority.HIGH, BookingPriority.ELEVATED]:
            options["time_limit"] = 600  # 10 minutes
            options["soft_time_limit"] = 540
        else:
            options["time_limit"] = 900  # 15 minutes
            options["soft_time_limit"] = 840
        
        return options
```

---

## Worker Pool Manager

```python
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import asyncio

@dataclass
class WorkerInfo:
    """Worker bilgisi"""
    worker_id: str
    hostname: str
    pool: str  # vfs, idata, bls, generic
    
    active_tasks: int = 0
    max_tasks: int = 10
    
    started_at: datetime = field(default_factory=datetime.utcnow)
    last_heartbeat: datetime = field(default_factory=datetime.utcnow)
    
    total_tasks_completed: int = 0
    total_tasks_failed: int = 0

class WorkerPoolManager:
    """Worker pool yönetimi"""
    
    # Pool configuration
    POOL_CONFIG = {
        "vfs": {
            "min_workers": 5,
            "max_workers": 15,
            "target_workers": 10,
            "queues": ["vfs", "high_priority"],
            "concurrency": 8,  # Browser sessions per worker
        },
        "idata": {
            "min_workers": 3,
            "max_workers": 10,
            "target_workers": 5,
            "queues": ["idata", "normal"],
            "concurrency": 12,  # Less resource intensive
        },
        "bls": {
            "min_workers": 2,
            "max_workers": 5,
            "target_workers": 3,
            "queues": ["bls", "normal"],
            "concurrency": 6,
        },
        "generic": {
            "min_workers": 3,
            "max_workers": 10,
            "target_workers": 5,
            "queues": ["normal", "retry", "scheduled"],
            "concurrency": 10,
        },
        "night": {
            "min_workers": 2,
            "max_workers": 5,
            "target_workers": 3,
            "queues": ["night_ops"],
            "concurrency": 20,  # Aggressive night operations
        },
    }
    
    def __init__(self, celery_app):
        self.app = celery_app
        self.workers: Dict[str, WorkerInfo] = {}
        self.lock = asyncio.Lock()
    
    async def get_pool_status(self) -> Dict[str, Any]:
        """Pool durumu"""
        
        inspect = self.app.control.inspect()
        
        # Active workers
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}
        stats = inspect.stats() or {}
        
        pool_status = {}
        
        for pool_name, config in self.POOL_CONFIG.items():
            pool_workers = [
                w for w in stats.keys()
                if pool_name in w or "celery" in w
            ]
            
            active_tasks = sum(
                len(active.get(w, []))
                for w in pool_workers
            )
            
            reserved_tasks = sum(
                len(reserved.get(w, []))
                for w in pool_workers
            )
            
            pool_status[pool_name] = {
                "workers": len(pool_workers),
                "min_workers": config["min_workers"],
                "max_workers": config["max_workers"],
                "target_workers": config["target_workers"],
                "active_tasks": active_tasks,
                "reserved_tasks": reserved_tasks,
                "capacity": len(pool_workers) * config["concurrency"],
                "utilization": active_tasks / max(1, len(pool_workers) * config["concurrency"]),
            }
        
        return pool_status
    
    async def scale_pool(
        self,
        pool_name: str,
        target_workers: int,
    ):
        """Pool worker sayısını ayarla"""
        
        config = self.POOL_CONFIG.get(pool_name)
        if not config:
            return
        
        # Clamp to limits
        target = max(config["min_workers"], min(config["max_workers"], target_workers))
        
        # In production: use container orchestration (Docker Swarm, K8s)
        # Here: signal to external scaler
        
        return {
            "pool": pool_name,
            "target": target,
            "queues": config["queues"],
            "concurrency": config["concurrency"],
        }
    
    async def auto_scale(self):
        """Otomatik scaling"""
        
        status = await self.get_pool_status()
        
        scale_actions = []
        
        for pool_name, pool_status in status.items():
            config = self.POOL_CONFIG[pool_name]
            current = pool_status["workers"]
            utilization = pool_status["utilization"]
            
            if utilization > 0.8 and current < config["max_workers"]:
                # Scale up
                scale_actions.append({
                    "pool": pool_name,
                    "action": "scale_up",
                    "from": current,
                    "to": min(current + 2, config["max_workers"]),
                    "reason": f"High utilization: {utilization:.1%}",
                })
            
            elif utilization < 0.3 and current > config["min_workers"]:
                # Scale down
                scale_actions.append({
                    "pool": pool_name,
                    "action": "scale_down",
                    "from": current,
                    "to": max(current - 1, config["min_workers"]),
                    "reason": f"Low utilization: {utilization:.1%}",
                })
        
        return scale_actions
```

---

## Rate Limiter

```python
from datetime import datetime, timedelta
from typing import Dict, Optional
import asyncio

class QueueRateLimiter:
    """Queue-level rate limiting"""
    
    # Site-specific rate limits
    RATE_LIMITS = {
        "vfs": {
            "requests_per_minute": 20,
            "concurrent_sessions": 10,
            "cooldown_after_ban": 7200,  # 2 hours
        },
        "idata": {
            "requests_per_minute": 60,
            "concurrent_sessions": 20,
            "cooldown_after_ban": 1800,  # 30 minutes
        },
        "bls": {
            "requests_per_minute": 30,
            "concurrent_sessions": 8,
            "cooldown_after_ban": 3600,  # 1 hour
        },
        "kkosmos": {
            "requests_per_minute": 15,
            "concurrent_sessions": 5,
            "cooldown_after_ban": 10800,  # 3 hours
        },
    }
    
    def __init__(self, redis_client):
        self.redis = redis_client
    
    async def check_rate_limit(
        self,
        site: str,
    ) -> tuple[bool, Optional[int]]:
        """Rate limit kontrolü
        
        Returns:
            (allowed, wait_seconds)
        """
        
        limits = self.RATE_LIMITS.get(site)
        if not limits:
            return True, None
        
        # Check requests per minute
        key = f"rate_limit:{site}:rpm"
        current = await self.redis.get(key)
        
        if current and int(current) >= limits["requests_per_minute"]:
            ttl = await self.redis.ttl(key)
            return False, max(1, ttl)
        
        # Check concurrent sessions
        session_key = f"rate_limit:{site}:sessions"
        sessions = await self.redis.scard(session_key)
        
        if sessions >= limits["concurrent_sessions"]:
            return False, 5  # Wait 5 seconds
        
        return True, None
    
    async def acquire(
        self,
        site: str,
        session_id: str,
    ) -> bool:
        """Rate limit acquire"""
        
        allowed, wait = await self.check_rate_limit(site)
        
        if not allowed:
            return False
        
        limits = self.RATE_LIMITS.get(site, {})
        
        # Increment request count
        key = f"rate_limit:{site}:rpm"
        pipe = self.redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, 60)  # Reset every minute
        await pipe.execute()
        
        # Add to concurrent sessions
        session_key = f"rate_limit:{site}:sessions"
        await self.redis.sadd(session_key, session_id)
        await self.redis.expire(session_key, 3600)  # 1 hour max
        
        return True
    
    async def release(
        self,
        site: str,
        session_id: str,
    ):
        """Session release"""
        session_key = f"rate_limit:{site}:sessions"
        await self.redis.srem(session_key, session_id)
    
    async def apply_cooldown(
        self,
        site: str,
        reason: str = "ban",
    ):
        """Site cooldown uygula"""
        
        limits = self.RATE_LIMITS.get(site, {})
        cooldown = limits.get("cooldown_after_ban", 3600)
        
        key = f"rate_limit:{site}:cooldown"
        await self.redis.setex(key, cooldown, reason)
    
    async def is_in_cooldown(self, site: str) -> tuple[bool, Optional[int]]:
        """Cooldown kontrolü"""
        
        key = f"rate_limit:{site}:cooldown"
        ttl = await self.redis.ttl(key)
        
        if ttl > 0:
            return True, ttl
        
        return False, None
```

---

## Celery Beat Schedule

```python
from celery.schedules import crontab

# Celery Beat schedule configuration
CELERY_BEAT_SCHEDULE = {
    # Google Sheets sync - every 5 minutes
    "sync-sheets-every-5-minutes": {
        "task": "tasks.sync_google_sheets",
        "schedule": 300.0,  # 5 minutes
        "options": {"queue": "scheduled"},
    },
    
    # PII cleanup - hourly
    "cleanup-pii-hourly": {
        "task": "tasks.cleanup_pii",
        "schedule": crontab(minute=0),  # Every hour at :00
        "options": {"queue": "scheduled"},
    },
    
    # Account health check - every 15 minutes
    "health-check-accounts": {
        "task": "tasks.health_check_accounts",
        "schedule": 900.0,  # 15 minutes
        "options": {"queue": "scheduled"},
    },
    
    # Daily credit reset - midnight Istanbul
    "reset-daily-limits": {
        "task": "tasks.reset_daily_limits",
        "schedule": crontab(hour=21, minute=0),  # 00:00 Istanbul (21:00 UTC)
        "options": {"queue": "scheduled"},
    },
    
    # Orphan task recovery - every 10 minutes
    "recover-orphan-tasks": {
        "task": "tasks.recover_orphan_tasks",
        "schedule": 600.0,
        "options": {"queue": "retry"},
    },
    
    # Pool auto-scaling - every 2 minutes
    "auto-scale-pools": {
        "task": "tasks.auto_scale_worker_pools",
        "schedule": 120.0,
        "options": {"queue": "scheduled"},
    },
    
    # Night operations - aggressive slot check (02:00-06:00 Istanbul)
    "night-slot-check": {
        "task": "tasks.night_slot_scanning",
        "schedule": crontab(hour="23,0,1,2,3", minute="*/10"),  # UTC hours
        "options": {"queue": "night_ops"},
    },
    
    # Dead letter processing - every 30 minutes
    "process-dead-letters": {
        "task": "tasks.process_dead_letter_queue",
        "schedule": 1800.0,
        "options": {"queue": "scheduled"},
    },
}
```

---

## Task Producer

```python
from typing import Dict, Any, Optional
from datetime import datetime

class BookingTaskProducer:
    """Booking task üretici"""
    
    def __init__(
        self,
        celery_app,
        priority_router: PriorityRouter,
        rate_limiter: QueueRateLimiter,
    ):
        self.app = celery_app
        self.router = priority_router
        self.limiter = rate_limiter
    
    async def submit_booking(
        self,
        booking_id: str,
        site: str,
        agency_id: str,
        is_premium: bool = False,
        preferred_date: Optional[datetime] = None,
        scheduled_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Booking task submit"""
        
        # Check rate limit
        in_cooldown, cooldown_ttl = await self.limiter.is_in_cooldown(site)
        if in_cooldown:
            return {
                "success": False,
                "error": f"Site {site} is in cooldown",
                "retry_after": cooldown_ttl,
            }
        
        # Calculate priority
        urgency_days = 30
        if preferred_date:
            urgency_days = (preferred_date - datetime.utcnow()).days
        
        factors = PriorityFactors(
            is_premium_agency=is_premium,
            date_urgency_days=urgency_days,
        )
        priority = self.router.calculate_priority(factors)
        
        # Select queue
        queue = self.router.select_queue(site, priority, scheduled_time)
        
        # Get task options
        options = self.router.get_task_options(priority, scheduled_time)
        options["queue"] = queue
        
        # Submit task
        task = self.app.send_task(
            f"tasks.{site}_booking" if site in ["vfs", "idata", "bls"] else "tasks.process_booking",
            args=[booking_id] if site in ["vfs", "idata", "bls"] else [booking_id, site],
            **options,
        )
        
        return {
            "success": True,
            "task_id": task.id,
            "queue": queue,
            "priority": priority.value,
            "booking_id": booking_id,
        }
    
    async def submit_batch(
        self,
        bookings: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Toplu booking submit"""
        
        results = []
        
        for booking in bookings:
            result = await self.submit_booking(**booking)
            results.append(result)
            
            # Small delay to prevent burst
            await asyncio.sleep(0.1)
        
        return results
    
    async def cancel_booking(
        self,
        task_id: str,
    ) -> bool:
        """Task iptal"""
        
        self.app.control.revoke(task_id, terminate=True)
        return True
```

---

## Monitoring Dashboard Data

```python
class QueueMonitor:
    """Queue monitoring"""
    
    def __init__(self, celery_app, redis_client):
        self.app = celery_app
        self.redis = redis_client
    
    async def get_dashboard_data(self) -> Dict[str, Any]:
        """Dashboard için monitoring data"""
        
        inspect = self.app.control.inspect()
        
        # Queue lengths
        queue_lengths = {}
        for queue in ["high_priority", "normal", "retry", "scheduled", "vfs", "idata", "bls"]:
            length = await self.redis.llen(queue)
            queue_lengths[queue] = length
        
        # Active tasks
        active = inspect.active() or {}
        total_active = sum(len(tasks) for tasks in active.values())
        
        # Reserved tasks
        reserved = inspect.reserved() or {}
        total_reserved = sum(len(tasks) for tasks in reserved.values())
        
        # Worker stats
        stats = inspect.stats() or {}
        worker_count = len(stats)
        
        # Task throughput (last hour)
        throughput = await self._calculate_throughput()
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "queues": queue_lengths,
            "total_queued": sum(queue_lengths.values()),
            "active_tasks": total_active,
            "reserved_tasks": total_reserved,
            "workers": {
                "count": worker_count,
                "stats": stats,
            },
            "throughput": throughput,
            "health": self._calculate_health(queue_lengths, total_active, worker_count),
        }
    
    async def _calculate_throughput(self) -> Dict[str, int]:
        """Son saat task throughput"""
        
        # Redis'te completed/failed sayaçları
        completed = await self.redis.get("metrics:completed:hourly") or 0
        failed = await self.redis.get("metrics:failed:hourly") or 0
        
        return {
            "completed_last_hour": int(completed),
            "failed_last_hour": int(failed),
            "success_rate": int(completed) / max(1, int(completed) + int(failed)),
        }
    
    def _calculate_health(
        self,
        queue_lengths: Dict[str, int],
        active_tasks: int,
        worker_count: int,
    ) -> str:
        """Sistem sağlık durumu"""
        
        total_queued = sum(queue_lengths.values())
        
        if worker_count == 0:
            return "critical"
        
        if total_queued > 1000 or queue_lengths.get("dead_letter", 0) > 50:
            return "degraded"
        
        if total_queued > 500:
            return "warning"
        
        return "healthy"
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | 200 concurrent task desteklemeli | Load test |
| AC-002 | Priority routing doğru çalışmalı | Priority test |
| AC-003 | Rate limiting site-specific olmalı | Rate limit test |
| AC-004 | Failed task retry ile queue'ya dönmeli | Failure test |
| AC-005 | Dead letter queue'ya düşen task alert vermeli | Monitoring test |
| AC-006 | Auto-scaling utilization'a göre çalışmalı | Scale test |
| AC-007 | Celery Beat schedule doğru çalışmalı | Schedule test |

---

## Edge Cases

### Queue Backlog

```
Scenario: Queue'da 1000+ task birikti
Action:
  1. Auto-scale workers up
  2. Reduce per-task timeout
  3. Prioritize high_priority queue
  4. Alert: "Queue backlog critical"
```

### Worker Crash

```
Scenario: Worker crash, task yarıda kaldı
Action:
  1. task_acks_late ile task otomatik retry
  2. State machine orphan recovery
  3. 5 dakika timeout sonrası FAILED
```

### Redis Connection Loss

```
Scenario: Redis bağlantısı kesildi
Action:
  1. Exponential backoff reconnect
  2. In-flight tasks memory'de tutulur
  3. Reconnect sonrası resume
  4. Alert: "Queue broker disconnected"
```

---

## Sonraki Adımlar

Grup 4 (AI ve Orchestration) tamamlandı.

Sonraki: **Grup 5 - Payment ve Verification** (015, 016, 017)
