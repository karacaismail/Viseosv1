# 013 - Booking State Machine Specification

## Amaç

Tüm booking flow'ları için deterministic state machine. State transitions, error handling, recovery flows ve compensating transactions. Idempotent operations ve eventual consistency garantisi.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 002-DIRECTUS-SCHEMA | booking_requests state storage |
| 012-AI-DECISION-ENGINE | Recovery strategy decisions |
| 014-QUEUE-ORCHESTRATOR | State-based job routing |
| 008-011 | Site adapters - state reporting |

---

## State Diagram

```
                                    ┌─────────────────────────────────────────────────┐
                                    │                                                 │
                                    ▼                                                 │
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐        │
│ PENDING  │───►│  QUEUED  │───►│PROCESSING│───►│SLOT_FOUND│───►│ BOOKING  │        │
└──────────┘    └────┬─────┘    └────┬─────┘    └────┬─────┘    └────┬─────┘        │
     │               │               │               │               │               │
     │               │               │               │               │               │
     ▼               ▼               ▼               ▼               ▼               │
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐        │
│ EXPIRED  │    │ EXPIRED  │    │  FAILED  │    │  FAILED  │    │ PAYMENT  │        │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └────┬─────┘        │
                                     │               │               │               │
                                     │               │               ▼               │
                                     │               │          ┌──────────┐         │
                                     │               │          │VERIFYING │         │
                                     │               │          └────┬─────┘         │
                                     │               │               │               │
                                     ▼               ▼               ▼               │
                                ┌─────────────────────────────────────────┐          │
                                │              COMPLETED                   │          │
                                └─────────────────────────────────────────┘          │
                                     ▲               ▲               │               │
                                     │               │               │               │
                                ┌────┴───────────────┴───────────────┴───┐           │
                                │              CANCELLED                  │◄──────────┘
                                └─────────────────────────────────────────┘
```

---

## State Definitions

```python
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import uuid

class BookingState(Enum):
    """Booking durumları"""
    
    # Initial states
    PENDING = "pending"           # Sheets'ten alındı, validation bekliyor
    QUEUED = "queued"             # Queue'ya alındı, işlem bekliyor
    
    # Processing states
    PROCESSING = "processing"     # Aktif işleniyor
    SLOT_FOUND = "slot_found"     # Slot bulundu, seçim bekliyor
    BOOKING = "booking"           # Form dolduruluyor
    PAYMENT = "payment"           # Ödeme işleniyor
    VERIFYING = "verifying"       # Email/SMS doğrulama
    
    # Terminal states
    COMPLETED = "completed"       # Başarılı
    FAILED = "failed"             # Başarısız (kalıcı)
    EXPIRED = "expired"           # Süre doldu
    CANCELLED = "cancelled"       # İptal edildi

class FailureReason(Enum):
    """Başarısızlık nedenleri"""
    
    # Slot related
    NO_SLOT_AVAILABLE = "no_slot_available"
    SLOT_TAKEN = "slot_taken"
    
    # Account related
    ACCOUNT_BANNED = "account_banned"
    ACCOUNT_LOCKED = "account_locked"
    
    # Payment related
    PAYMENT_FAILED = "payment_failed"
    PAYMENT_TIMEOUT = "payment_timeout"
    CARD_DECLINED = "card_declined"
    
    # Verification related
    VERIFICATION_FAILED = "verification_failed"
    SMS_NOT_RECEIVED = "sms_not_received"
    
    # System related
    MAX_RETRIES_EXCEEDED = "max_retries_exceeded"
    TIMEOUT = "timeout"
    SYSTEM_ERROR = "system_error"
    
    # External
    SITE_UNAVAILABLE = "site_unavailable"
    CAPTCHA_FAILED = "captcha_failed"
    
    # User related
    INVALID_DATA = "invalid_data"
    CANCELLED_BY_USER = "cancelled_by_user"

@dataclass
class StateTransition:
    """State geçiş kaydı"""
    from_state: BookingState
    to_state: BookingState
    timestamp: datetime
    trigger: str
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class BookingContext:
    """Booking işlem context'i"""
    
    # Identity
    booking_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agency_id: str = ""
    applicant_id: str = ""
    
    # Current state
    state: BookingState = BookingState.PENDING
    sub_state: Optional[str] = None
    
    # Timing
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    
    # Attempt tracking
    attempt_count: int = 0
    max_attempts: int = 3
    last_attempt_at: Optional[datetime] = None
    
    # Error tracking
    last_error: Optional[str] = None
    failure_reason: Optional[FailureReason] = None
    error_history: List[Dict] = field(default_factory=list)
    
    # Progress tracking
    slot_found_at: Optional[datetime] = None
    booking_started_at: Optional[datetime] = None
    payment_started_at: Optional[datetime] = None
    
    # Result
    confirmation_number: Optional[str] = None
    appointment_date: Optional[str] = None
    appointment_time: Optional[str] = None
    
    # Resources
    assigned_account_id: Optional[str] = None
    assigned_proxy_id: Optional[str] = None
    assigned_session_id: Optional[str] = None
    
    # Credit tracking
    credit_reserved: float = 0.0
    credit_charged: float = 0.0
    
    # State history
    state_history: List[StateTransition] = field(default_factory=list)
```

---

## State Machine Implementation

```python
from typing import Callable, Dict, Tuple, Optional
import asyncio
from datetime import datetime

class BookingStateMachine:
    """Booking state machine"""
    
    # Valid state transitions
    TRANSITIONS: Dict[BookingState, List[BookingState]] = {
        BookingState.PENDING: [
            BookingState.QUEUED,
            BookingState.EXPIRED,
            BookingState.CANCELLED,
        ],
        BookingState.QUEUED: [
            BookingState.PROCESSING,
            BookingState.EXPIRED,
            BookingState.CANCELLED,
        ],
        BookingState.PROCESSING: [
            BookingState.SLOT_FOUND,
            BookingState.FAILED,
            BookingState.QUEUED,  # Retry için geri
            BookingState.CANCELLED,
        ],
        BookingState.SLOT_FOUND: [
            BookingState.BOOKING,
            BookingState.FAILED,
            BookingState.PROCESSING,  # Slot kaybedildi, tekrar ara
            BookingState.CANCELLED,
        ],
        BookingState.BOOKING: [
            BookingState.PAYMENT,
            BookingState.FAILED,
            BookingState.SLOT_FOUND,  # Form hatası, slot hala var
            BookingState.CANCELLED,
        ],
        BookingState.PAYMENT: [
            BookingState.VERIFYING,
            BookingState.COMPLETED,  # Verification gerektirmeyen durumlar
            BookingState.FAILED,
            BookingState.CANCELLED,
        ],
        BookingState.VERIFYING: [
            BookingState.COMPLETED,
            BookingState.FAILED,
            BookingState.CANCELLED,
        ],
        # Terminal states - no outgoing transitions
        BookingState.COMPLETED: [],
        BookingState.FAILED: [
            BookingState.QUEUED,  # Manual retry
        ],
        BookingState.EXPIRED: [],
        BookingState.CANCELLED: [],
    }
    
    # State timeout configuration (seconds)
    STATE_TIMEOUTS: Dict[BookingState, int] = {
        BookingState.PENDING: 3600,      # 1 saat
        BookingState.QUEUED: 86400,      # 24 saat
        BookingState.PROCESSING: 300,     # 5 dakika
        BookingState.SLOT_FOUND: 60,      # 1 dakika (slot kaybolabilir)
        BookingState.BOOKING: 180,        # 3 dakika
        BookingState.PAYMENT: 300,        # 5 dakika (3DS dahil)
        BookingState.VERIFYING: 180,      # 3 dakika
    }
    
    def __init__(self):
        # State handlers
        self.entry_handlers: Dict[BookingState, Callable] = {}
        self.exit_handlers: Dict[BookingState, Callable] = {}
        self.transition_validators: Dict[Tuple[BookingState, BookingState], Callable] = {}
    
    def can_transition(
        self,
        context: BookingContext,
        target_state: BookingState,
    ) -> Tuple[bool, str]:
        """Geçiş yapılabilir mi?"""
        
        current = context.state
        
        # Check valid transitions
        if target_state not in self.TRANSITIONS.get(current, []):
            return False, f"Invalid transition: {current.value} -> {target_state.value}"
        
        # Check custom validator
        validator_key = (current, target_state)
        if validator_key in self.transition_validators:
            validator = self.transition_validators[validator_key]
            is_valid, reason = validator(context)
            if not is_valid:
                return False, reason
        
        # Check timeout (eğer timeout olmuşsa sadece failed/expired'a geçebilir)
        if self._is_timed_out(context):
            if target_state not in [BookingState.FAILED, BookingState.EXPIRED]:
                return False, "State timed out"
        
        return True, "OK"
    
    async def transition(
        self,
        context: BookingContext,
        target_state: BookingState,
        trigger: str,
        metadata: Dict = None,
    ) -> BookingContext:
        """State geçişi yap"""
        
        # Validate transition
        can_do, reason = self.can_transition(context, target_state)
        if not can_do:
            raise InvalidTransitionError(
                f"Cannot transition from {context.state.value} to {target_state.value}: {reason}"
            )
        
        # Exit handler
        if context.state in self.exit_handlers:
            await self.exit_handlers[context.state](context)
        
        # Record transition
        transition = StateTransition(
            from_state=context.state,
            to_state=target_state,
            timestamp=datetime.utcnow(),
            trigger=trigger,
            metadata=metadata or {},
        )
        context.state_history.append(transition)
        
        # Update state
        old_state = context.state
        context.state = target_state
        context.updated_at = datetime.utcnow()
        
        # State-specific updates
        if target_state == BookingState.PROCESSING:
            context.attempt_count += 1
            context.last_attempt_at = datetime.utcnow()
        elif target_state == BookingState.SLOT_FOUND:
            context.slot_found_at = datetime.utcnow()
        elif target_state == BookingState.BOOKING:
            context.booking_started_at = datetime.utcnow()
        elif target_state == BookingState.PAYMENT:
            context.payment_started_at = datetime.utcnow()
        
        # Entry handler
        if target_state in self.entry_handlers:
            await self.entry_handlers[target_state](context)
        
        return context
    
    def _is_timed_out(self, context: BookingContext) -> bool:
        """State timeout kontrolü"""
        
        timeout = self.STATE_TIMEOUTS.get(context.state)
        if not timeout:
            return False
        
        elapsed = (datetime.utcnow() - context.updated_at).total_seconds()
        return elapsed > timeout
    
    def register_entry_handler(
        self,
        state: BookingState,
        handler: Callable,
    ):
        """State giriş handler'ı kaydet"""
        self.entry_handlers[state] = handler
    
    def register_exit_handler(
        self,
        state: BookingState,
        handler: Callable,
    ):
        """State çıkış handler'ı kaydet"""
        self.exit_handlers[state] = handler
    
    def register_transition_validator(
        self,
        from_state: BookingState,
        to_state: BookingState,
        validator: Callable,
    ):
        """Geçiş validator'ı kaydet"""
        self.transition_validators[(from_state, to_state)] = validator
```

---

## State Handlers

```python
class BookingStateHandlers:
    """State giriş/çıkış handler'ları"""
    
    def __init__(
        self,
        credit_service,
        notification_service,
        pii_cleanup_service,
    ):
        self.credits = credit_service
        self.notifications = notification_service
        self.pii_cleanup = pii_cleanup_service
    
    # Entry Handlers
    
    async def on_enter_queued(self, context: BookingContext):
        """QUEUED state'e girerken"""
        
        # Reserve credit
        credit_amount = self._calculate_credit_cost(context)
        reserved = await self.credits.reserve(
            agency_id=context.agency_id,
            amount=credit_amount,
            booking_id=context.booking_id,
        )
        context.credit_reserved = reserved
    
    async def on_enter_processing(self, context: BookingContext):
        """PROCESSING state'e girerken"""
        
        # Log attempt
        context.error_history.append({
            "attempt": context.attempt_count,
            "started_at": datetime.utcnow().isoformat(),
        })
    
    async def on_enter_completed(self, context: BookingContext):
        """COMPLETED state'e girerken"""
        
        # Charge credit (convert reservation to actual charge)
        await self.credits.charge(
            agency_id=context.agency_id,
            amount=context.credit_reserved,
            booking_id=context.booking_id,
            confirmation=context.confirmation_number,
        )
        context.credit_charged = context.credit_reserved
        context.credit_reserved = 0
        
        # Send success notification
        await self.notifications.send_booking_success(
            agency_id=context.agency_id,
            booking_id=context.booking_id,
            confirmation=context.confirmation_number,
            appointment_date=context.appointment_date,
            appointment_time=context.appointment_time,
        )
        
        # Schedule PII cleanup (24 saat sonra)
        await self.pii_cleanup.schedule(
            applicant_id=context.applicant_id,
            cleanup_at=datetime.utcnow() + timedelta(hours=24),
        )
    
    async def on_enter_failed(self, context: BookingContext):
        """FAILED state'e girerken"""
        
        # Release reserved credit
        if context.credit_reserved > 0:
            await self.credits.release(
                agency_id=context.agency_id,
                amount=context.credit_reserved,
                booking_id=context.booking_id,
            )
            context.credit_reserved = 0
        
        # Send failure notification
        await self.notifications.send_booking_failed(
            agency_id=context.agency_id,
            booking_id=context.booking_id,
            reason=context.failure_reason.value if context.failure_reason else "unknown",
            error=context.last_error,
        )
    
    async def on_enter_cancelled(self, context: BookingContext):
        """CANCELLED state'e girerken"""
        
        # Release reserved credit
        if context.credit_reserved > 0:
            await self.credits.release(
                agency_id=context.agency_id,
                amount=context.credit_reserved,
                booking_id=context.booking_id,
            )
            context.credit_reserved = 0
        
        # Send cancellation notification
        await self.notifications.send_booking_cancelled(
            agency_id=context.agency_id,
            booking_id=context.booking_id,
        )
    
    # Exit Handlers
    
    async def on_exit_processing(self, context: BookingContext):
        """PROCESSING state'ten çıkarken"""
        
        # Update attempt record
        if context.error_history:
            context.error_history[-1]["ended_at"] = datetime.utcnow().isoformat()
            context.error_history[-1]["result"] = context.state.value
    
    async def on_exit_slot_found(self, context: BookingContext):
        """SLOT_FOUND state'ten çıkarken"""
        
        # Log slot timing
        if context.slot_found_at:
            duration = (datetime.utcnow() - context.slot_found_at).total_seconds()
            context.error_history.append({
                "event": "slot_selection_duration",
                "seconds": duration,
            })
    
    # Helpers
    
    def _calculate_credit_cost(self, context: BookingContext) -> float:
        """Booking credit maliyeti hesapla"""
        # Base cost: 1 credit per booking
        # Premium: additional based on urgency
        return 1.0
```

---

## Compensating Transactions

```python
class CompensatingTransactionManager:
    """Başarısız işlemler için telafi işlemleri"""
    
    def __init__(
        self,
        credit_service,
        account_pool,
        proxy_pool,
        session_manager,
    ):
        self.credits = credit_service
        self.accounts = account_pool
        self.proxies = proxy_pool
        self.sessions = session_manager
    
    async def compensate(
        self,
        context: BookingContext,
        failure_point: BookingState,
    ):
        """Başarısız booking için telafi işlemleri"""
        
        compensations = []
        
        # Credit release (her durumda)
        if context.credit_reserved > 0:
            compensations.append(
                self._release_credit(context)
            )
        
        # Account release
        if context.assigned_account_id:
            success = failure_point not in [
                BookingState.COMPLETED,
                BookingState.CANCELLED,
            ]
            compensations.append(
                self._release_account(context, success=success)
            )
        
        # Proxy release
        if context.assigned_proxy_id:
            compensations.append(
                self._release_proxy(context)
            )
        
        # Session cleanup
        if context.assigned_session_id:
            compensations.append(
                self._cleanup_session(context)
            )
        
        # Payment reversal (eğer partial charge olduysa)
        if context.credit_charged > 0 and failure_point == BookingState.VERIFYING:
            compensations.append(
                self._refund_payment(context)
            )
        
        # Execute all compensations
        await asyncio.gather(*compensations, return_exceptions=True)
    
    async def _release_credit(self, context: BookingContext):
        """Credit serbest bırak"""
        await self.credits.release(
            agency_id=context.agency_id,
            amount=context.credit_reserved,
            booking_id=context.booking_id,
        )
    
    async def _release_account(self, context: BookingContext, success: bool):
        """Account serbest bırak"""
        await self.accounts.release(
            account_id=context.assigned_account_id,
            success=success,
            error_message=context.last_error,
        )
    
    async def _release_proxy(self, context: BookingContext):
        """Proxy serbest bırak"""
        await self.proxies.report_usage(
            proxy_id=context.assigned_proxy_id,
            success=context.state == BookingState.COMPLETED,
        )
    
    async def _cleanup_session(self, context: BookingContext):
        """Browser session temizle"""
        await self.sessions.cleanup(
            session_id=context.assigned_session_id,
        )
    
    async def _refund_payment(self, context: BookingContext):
        """Ödeme iadesi"""
        # Payment reversal logic
        pass
```

---

## Retry Policy

```python
from dataclasses import dataclass
from enum import Enum

class RetryPolicy(Enum):
    """Retry politikaları"""
    IMMEDIATE = "immediate"
    EXPONENTIAL_BACKOFF = "exponential_backoff"
    FIXED_DELAY = "fixed_delay"
    NO_RETRY = "no_retry"

@dataclass
class RetryConfig:
    """Retry konfigürasyonu"""
    policy: RetryPolicy
    max_attempts: int
    initial_delay_seconds: int
    max_delay_seconds: int
    multiplier: float = 2.0

class RetryManager:
    """Retry yönetimi"""
    
    # Failure reason -> retry config mapping
    RETRY_CONFIGS: Dict[FailureReason, RetryConfig] = {
        # Retriable with backoff
        FailureReason.TIMEOUT: RetryConfig(
            policy=RetryPolicy.EXPONENTIAL_BACKOFF,
            max_attempts=3,
            initial_delay_seconds=30,
            max_delay_seconds=300,
        ),
        FailureReason.SITE_UNAVAILABLE: RetryConfig(
            policy=RetryPolicy.EXPONENTIAL_BACKOFF,
            max_attempts=5,
            initial_delay_seconds=60,
            max_delay_seconds=600,
        ),
        FailureReason.SLOT_TAKEN: RetryConfig(
            policy=RetryPolicy.IMMEDIATE,
            max_attempts=5,
            initial_delay_seconds=5,
            max_delay_seconds=5,
        ),
        FailureReason.CAPTCHA_FAILED: RetryConfig(
            policy=RetryPolicy.FIXED_DELAY,
            max_attempts=3,
            initial_delay_seconds=60,
            max_delay_seconds=60,
        ),
        
        # Retriable with long delay (account related)
        FailureReason.ACCOUNT_BANNED: RetryConfig(
            policy=RetryPolicy.FIXED_DELAY,
            max_attempts=2,
            initial_delay_seconds=7200,  # 2 saat
            max_delay_seconds=7200,
        ),
        
        # Not retriable
        FailureReason.INVALID_DATA: RetryConfig(
            policy=RetryPolicy.NO_RETRY,
            max_attempts=0,
            initial_delay_seconds=0,
            max_delay_seconds=0,
        ),
        FailureReason.CANCELLED_BY_USER: RetryConfig(
            policy=RetryPolicy.NO_RETRY,
            max_attempts=0,
            initial_delay_seconds=0,
            max_delay_seconds=0,
        ),
        FailureReason.CARD_DECLINED: RetryConfig(
            policy=RetryPolicy.NO_RETRY,
            max_attempts=0,
            initial_delay_seconds=0,
            max_delay_seconds=0,
        ),
    }
    
    # Default config
    DEFAULT_CONFIG = RetryConfig(
        policy=RetryPolicy.EXPONENTIAL_BACKOFF,
        max_attempts=3,
        initial_delay_seconds=30,
        max_delay_seconds=300,
    )
    
    def should_retry(
        self,
        context: BookingContext,
    ) -> Tuple[bool, int]:
        """Retry yapılmalı mı? Kaç saniye sonra?"""
        
        reason = context.failure_reason
        config = self.RETRY_CONFIGS.get(reason, self.DEFAULT_CONFIG)
        
        # No retry policy
        if config.policy == RetryPolicy.NO_RETRY:
            return False, 0
        
        # Max attempts check
        if context.attempt_count >= config.max_attempts:
            return False, 0
        
        # Calculate delay
        delay = self._calculate_delay(config, context.attempt_count)
        
        return True, delay
    
    def _calculate_delay(self, config: RetryConfig, attempt: int) -> int:
        """Delay hesapla"""
        
        if config.policy == RetryPolicy.IMMEDIATE:
            return config.initial_delay_seconds
        
        if config.policy == RetryPolicy.FIXED_DELAY:
            return config.initial_delay_seconds
        
        if config.policy == RetryPolicy.EXPONENTIAL_BACKOFF:
            delay = config.initial_delay_seconds * (config.multiplier ** (attempt - 1))
            return min(int(delay), config.max_delay_seconds)
        
        return config.initial_delay_seconds
```

---

## State Persistence

```python
class StatePersistenceService:
    """State kalıcı storage"""
    
    def __init__(self, directus_client):
        self.db = directus_client
    
    async def save(self, context: BookingContext):
        """Context'i veritabanına kaydet"""
        
        data = {
            "id": context.booking_id,
            "agency_id": context.agency_id,
            "applicant_id": context.applicant_id,
            "status": context.state.value,
            "sub_status": context.sub_state,
            "attempt_count": context.attempt_count,
            "max_attempts": context.max_attempts,
            "last_error": context.last_error,
            "failure_reason": context.failure_reason.value if context.failure_reason else None,
            "confirmation_number": context.confirmation_number,
            "appointment_date": context.appointment_date,
            "appointment_time": context.appointment_time,
            "assigned_account_id": context.assigned_account_id,
            "credit_reserved": context.credit_reserved,
            "credit_charged": context.credit_charged,
            "state_history": [
                {
                    "from": t.from_state.value,
                    "to": t.to_state.value,
                    "timestamp": t.timestamp.isoformat(),
                    "trigger": t.trigger,
                }
                for t in context.state_history
            ],
            "error_history": context.error_history,
            "updated_at": datetime.utcnow().isoformat(),
        }
        
        await self.db.items("booking_requests").update(
            context.booking_id,
            data,
        )
    
    async def load(self, booking_id: str) -> Optional[BookingContext]:
        """Context'i veritabanından yükle"""
        
        result = await self.db.items("booking_requests").read_one(booking_id)
        
        if not result:
            return None
        
        context = BookingContext(
            booking_id=result["id"],
            agency_id=result["agency_id"],
            applicant_id=result["applicant_id"],
            state=BookingState(result["status"]),
            sub_state=result.get("sub_status"),
            attempt_count=result.get("attempt_count", 0),
            max_attempts=result.get("max_attempts", 3),
            last_error=result.get("last_error"),
            failure_reason=FailureReason(result["failure_reason"]) if result.get("failure_reason") else None,
            confirmation_number=result.get("confirmation_number"),
            appointment_date=result.get("appointment_date"),
            appointment_time=result.get("appointment_time"),
            assigned_account_id=result.get("assigned_account_id"),
            credit_reserved=result.get("credit_reserved", 0),
            credit_charged=result.get("credit_charged", 0),
        )
        
        # Restore state history
        for t in result.get("state_history", []):
            context.state_history.append(StateTransition(
                from_state=BookingState(t["from"]),
                to_state=BookingState(t["to"]),
                timestamp=datetime.fromisoformat(t["timestamp"]),
                trigger=t["trigger"],
            ))
        
        context.error_history = result.get("error_history", [])
        
        return context
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | Tüm valid transitions çalışmalı | Unit test |
| AC-002 | Invalid transitions reject edilmeli | Unit test |
| AC-003 | State timeout detection çalışmalı | Timing test |
| AC-004 | Credit reserve/release idempotent olmalı | Idempotency test |
| AC-005 | Compensating transactions çalışmalı | Failure injection test |
| AC-006 | State persistence doğru kaydetmeli | Integration test |
| AC-007 | Retry policy doğru delay hesaplamalı | Unit test |

---

## Edge Cases

### Concurrent State Updates

```
Scenario: İki worker aynı booking'i update etmeye çalışıyor
Solution: 
  - Optimistic locking (version field)
  - Database-level atomicity
  - Queue-based serialization
```

### State Recovery (Crash)

```
Scenario: Worker crash, booking PROCESSING state'te kaldı
Solution:
  - Heartbeat mechanism
  - Stale state detection (5 dakika timeout)
  - Orphan recovery job
```

### Split-Brain

```
Scenario: Network partition, iki worker farklı state görüyor
Solution:
  - Database as source of truth
  - Re-fetch before transition
  - Conflict resolution: latest timestamp wins
```

---

## Sonraki Adımlar

1. **014-QUEUE-ORCHESTRATOR.md** - Queue yönetimi
2. State machine integration tests
3. Monitoring dashboard
