# 007 - Account Pool Manager Specification

## Amaç

VFS Global, iDATA, BLS Spain ve KKOSMOS sistemlerindeki bot hesaplarının yaşam döngüsü yönetimi. Health scoring, ban detection/recovery, intelligent rotation ve credential management. 200 paralel session için scale edilebilir hesap havuzu.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 002-DIRECTUS-SCHEMA | bot_accounts collection |
| 004-STEALTH-ENGINE | Session başlatma için credential |
| 005-PROXY-MANAGER | Account-proxy pairing |
| 013-STATE-MACHINE | Account state transitions |
| 016-EMAIL-VERIFICATION | Account email handling |

---

## Account Lifecycle States

```
┌─────────────────────────────────────────────────────────────┐
│                    ACCOUNT LIFECYCLE                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   ┌──────────┐                                               │
│   │  CREATED │                                               │
│   └────┬─────┘                                               │
│        │ verification complete                               │
│        ▼                                                     │
│   ┌──────────┐      success      ┌──────────┐               │
│   │  ACTIVE  │ ◄───────────────► │  IN_USE  │               │
│   └────┬─────┘      release      └────┬─────┘               │
│        │                              │                      │
│        │ failure detected             │ ban detected         │
│        ▼                              ▼                      │
│   ┌──────────┐      timeout      ┌──────────┐               │
│   │ COOLDOWN │ ─────────────────►│  BANNED  │               │
│   └────┬─────┘                   └────┬─────┘               │
│        │                              │                      │
│        │ recovery                     │ permanent            │
│        ▼                              ▼                      │
│   ┌──────────┐                   ┌──────────┐               │
│   │  ACTIVE  │                   │ RETIRED  │               │
│   └──────────┘                   └──────────┘               │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Account Status Definitions

| Status | Açıklama | Duration | Recovery |
|--------|----------|----------|----------|
| CREATED | Hesap oluşturuldu, doğrulama bekleniyor | - | Manual |
| ACTIVE | Kullanıma hazır | Indefinite | - |
| IN_USE | Aktif session'da kullanılıyor | Session duration | Auto release |
| COOLDOWN | Geçici hata, bekleme | 10-120 dakika | Auto |
| BANNED | Kalıcı/uzun süreli ban | Hours-days | Manual veya timeout |
| RETIRED | Artık kullanılmıyor | Permanent | None |

---

## Account Data Model

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, List
import uuid

class AccountStatus(Enum):
    CREATED = "created"
    ACTIVE = "active"
    IN_USE = "in_use"
    COOLDOWN = "cooldown"
    BANNED = "banned"
    RETIRED = "retired"

class TargetSystem(Enum):
    VFS = "vfs"
    IDATA = "idata"
    BLS = "bls"
    KKOSMOS = "kkosmos"

@dataclass
class BotAccount:
    """Bot hesap entity"""
    
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    # Target system
    system: TargetSystem = TargetSystem.VFS
    country: str = "de"  # Target country code
    
    # Credentials (encrypted in storage)
    email: str = ""
    password: str = ""
    
    # Status
    status: AccountStatus = AccountStatus.CREATED
    
    # Health metrics
    health_score: int = 100  # 0-100
    success_count: int = 0
    failure_count: int = 0
    consecutive_failures: int = 0
    
    # Timestamps
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_used_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None
    banned_at: Optional[datetime] = None
    
    # Session tracking
    current_session_id: Optional[str] = None
    current_proxy_id: Optional[str] = None
    
    # Usage limits
    daily_usage_count: int = 0
    daily_usage_limit: int = 50  # Max bookings per day
    
    # Metadata
    notes: str = ""
    last_error: Optional[str] = None
    ban_reason: Optional[str] = None

@dataclass
class AccountPoolStats:
    """Havuz istatistikleri"""
    total: int = 0
    active: int = 0
    in_use: int = 0
    cooldown: int = 0
    banned: int = 0
    retired: int = 0
    avg_health: float = 0.0
```

---

## Account Pool Manager

```python
import asyncio
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import random

class AccountPoolManager:
    """Bot hesap havuzu yöneticisi"""
    
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
    
    # Cooldown indicators (geçici, recovery mümkün)
    COOLDOWN_INDICATORS = [
        "try again later",
        "rate limit",
        "too many requests",
        "please wait",
        "exceeded.*attempts",
    ]
    
    def __init__(self):
        self.accounts: Dict[str, BotAccount] = {}
        self.lock = asyncio.Lock()
        
        # System-country specific pools
        self.pools: Dict[str, List[str]] = {}  # "vfs_de" -> [account_ids]
    
    async def load_from_database(self, db_accounts: List[Dict]):
        """Veritabanından hesapları yükle"""
        
        async with self.lock:
            for data in db_accounts:
                account = BotAccount(
                    id=data["id"],
                    system=TargetSystem(data["system"]),
                    country=data["country"],
                    email=data["email"],  # Decrypted
                    password=data["password"],  # Decrypted
                    status=AccountStatus(data["status"]),
                    health_score=data["health_score"],
                    success_count=data["success_count"],
                    failure_count=data["failure_count"],
                    consecutive_failures=data["consecutive_failures"],
                )
                
                self.accounts[account.id] = account
                
                # Pool indexing
                pool_key = f"{account.system.value}_{account.country}"
                if pool_key not in self.pools:
                    self.pools[pool_key] = []
                self.pools[pool_key].append(account.id)
    
    async def acquire(
        self,
        system: TargetSystem,
        country: str,
        session_id: str,
        exclude_ids: List[str] = None,
    ) -> Optional[BotAccount]:
        """Havuzdan uygun hesap al"""
        
        async with self.lock:
            pool_key = f"{system.value}_{country}"
            
            if pool_key not in self.pools:
                raise NoAccountAvailableError(f"No pool for {pool_key}")
            
            # Filter eligible accounts
            eligible = []
            now = datetime.utcnow()
            
            for account_id in self.pools[pool_key]:
                account = self.accounts[account_id]
                
                # Status check
                if account.status not in [AccountStatus.ACTIVE]:
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
                raise NoAccountAvailableError(f"No eligible accounts for {pool_key}")
            
            # Weighted selection based on health
            account = self._weighted_select(eligible)
            
            # Mark as in use
            account.status = AccountStatus.IN_USE
            account.current_session_id = session_id
            account.last_used_at = now
            
            return account
    
    def _weighted_select(self, accounts: List[BotAccount]) -> BotAccount:
        """Health score'a göre weighted selection"""
        
        weights = []
        now = datetime.utcnow()
        
        for account in accounts:
            weight = account.health_score
            
            # Recency bonus: daha az kullanılan tercih edilir
            if account.last_used_at:
                idle_minutes = (now - account.last_used_at).total_seconds() / 60
                weight += min(idle_minutes, 30)  # Max 30 bonus
            else:
                weight += 30  # Hiç kullanılmamış
            
            # Success streak bonus
            if account.consecutive_failures == 0:
                weight += 10
            
            weights.append(max(weight, 1))
        
        return random.choices(accounts, weights=weights, k=1)[0]
    
    async def release(
        self,
        account_id: str,
        success: bool,
        error_message: Optional[str] = None,
    ):
        """Hesabı serbest bırak"""
        
        async with self.lock:
            if account_id not in self.accounts:
                return
            
            account = self.accounts[account_id]
            account.current_session_id = None
            
            if success:
                await self._handle_success(account)
            else:
                await self._handle_failure(account, error_message)
    
    async def _handle_success(self, account: BotAccount):
        """Başarılı işlem sonrası"""
        
        account.success_count += 1
        account.consecutive_failures = 0
        account.last_success_at = datetime.utcnow()
        account.daily_usage_count += 1
        account.status = AccountStatus.ACTIVE
        
        # Health boost
        account.health_score = min(100, account.health_score + 5)
        
        # Clear cooldown
        account.cooldown_until = None
    
    async def _handle_failure(self, account: BotAccount, error_message: Optional[str]):
        """Başarısız işlem sonrası"""
        
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
    
    def _is_ban_error(self, error_message: Optional[str]) -> bool:
        """Ban error mi?"""
        if not error_message:
            return False
        
        error_lower = error_message.lower()
        return any(indicator in error_lower for indicator in self.BAN_INDICATORS)
    
    def _is_cooldown_error(self, error_message: Optional[str]) -> bool:
        """Cooldown gerektiren error mi?"""
        if not error_message:
            return False
        
        import re
        error_lower = error_message.lower()
        return any(
            re.search(indicator, error_lower) 
            for indicator in self.COOLDOWN_INDICATORS
        )
    
    async def _handle_ban(self, account: BotAccount, reason: Optional[str]):
        """Ban durumu işle"""
        
        account.status = AccountStatus.BANNED
        account.banned_at = datetime.utcnow()
        account.ban_reason = reason
        account.health_score = max(0, account.health_score - 50)
        
        # VFS için 10 dakikalık geçici ban olabilir
        if account.system == TargetSystem.VFS and "10 minute" in (reason or "").lower():
            # Geçici ban, 15 dakika sonra recovery dene
            account.cooldown_until = datetime.utcnow() + timedelta(minutes=15)
    
    async def _handle_cooldown(self, account: BotAccount):
        """Cooldown durumu işle"""
        
        account.status = AccountStatus.COOLDOWN
        account.health_score = max(0, account.health_score - 20)
        
        # Exponential backoff
        cooldown_minutes = min(
            self.COOLDOWN_INITIAL_MINUTES * (self.COOLDOWN_MULTIPLIER ** account.consecutive_failures),
            self.COOLDOWN_MAX_MINUTES
        )
        
        account.cooldown_until = datetime.utcnow() + timedelta(minutes=cooldown_minutes)
    
    async def check_recovery(self, account_id: str) -> bool:
        """Cooldown/ban sonrası recovery kontrolü"""
        
        async with self.lock:
            if account_id not in self.accounts:
                return False
            
            account = self.accounts[account_id]
            now = datetime.utcnow()
            
            # Cooldown expired?
            if account.status == AccountStatus.COOLDOWN:
                if account.cooldown_until and account.cooldown_until <= now:
                    account.status = AccountStatus.ACTIVE
                    account.cooldown_until = None
                    return True
            
            # Banned but might be temporary
            if account.status == AccountStatus.BANNED:
                if account.cooldown_until and account.cooldown_until <= now:
                    # Try recovery
                    account.status = AccountStatus.ACTIVE
                    account.cooldown_until = None
                    account.consecutive_failures = 0  # Reset
                    return True
            
            return False
    
    async def get_pool_stats(self) -> Dict[str, AccountPoolStats]:
        """Havuz istatistikleri"""
        
        stats = {}
        
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
            
            pool_stats.avg_health = health_sum / pool_stats.total if pool_stats.total > 0 else 0
            stats[pool_key] = pool_stats
        
        return stats
    
    async def reset_daily_limits(self):
        """Günlük limitleri sıfırla (gece yarısı çalışır)"""
        
        async with self.lock:
            for account in self.accounts.values():
                account.daily_usage_count = 0
```

---

## Account-Proxy Pairing

```python
class AccountProxyPairing:
    """Hesap-proxy eşleştirme stratejisi"""
    
    def __init__(
        self,
        account_pool: AccountPoolManager,
        proxy_pool,  # ProxyPoolManager
    ):
        self.account_pool = account_pool
        self.proxy_pool = proxy_pool
        
        # Pairing memory (hesap hangi proxy ile başarılı oldu)
        self.successful_pairs: Dict[str, List[str]] = {}  # account_id -> [proxy_ids]
    
    async def get_paired_resources(
        self,
        system: TargetSystem,
        country: str,
        session_id: str,
    ) -> tuple:
        """Hesap ve proxy çifti al"""
        
        # First, get account
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
            await self.account_pool.release(account.id, success=False, error_message=str(e))
            raise
    
    async def _get_preferred_proxy(self, account_id: str) -> Optional[Dict]:
        """Hesap için daha önce başarılı olan proxy"""
        
        if account_id not in self.successful_pairs:
            return None
        
        successful_proxies = self.successful_pairs[account_id]
        
        if not successful_proxies:
            return None
        
        # Son başarılı proxy'yi tercih et
        last_proxy_id = successful_proxies[-1]
        
        try:
            # Proxy hala aktif mi?
            proxy = await self.proxy_pool.get_proxy_by_id(last_proxy_id)
            if proxy and proxy.status == "active":
                return proxy
        except:
            pass
        
        return None
    
    async def record_success(self, account_id: str, proxy_id: str):
        """Başarılı eşleşmeyi kaydet"""
        
        if account_id not in self.successful_pairs:
            self.successful_pairs[account_id] = []
        
        # Son 5 başarılı proxy'yi tut
        self.successful_pairs[account_id].append(proxy_id)
        if len(self.successful_pairs[account_id]) > 5:
            self.successful_pairs[account_id].pop(0)
```

---

## Account Creation Service

```python
class AccountCreationService:
    """Yeni bot hesabı oluşturma servisi"""
    
    def __init__(
        self,
        stealth_engine,  # StealthSessionLauncher
        email_service,   # EmailVerificationService
        pool_manager: AccountPoolManager,
    ):
        self.stealth = stealth_engine
        self.email = email_service
        self.pool = pool_manager
    
    async def create_vfs_account(
        self,
        email: str,
        password: str,
        country: str,
        proxy: Dict = None,
    ) -> BotAccount:
        """VFS Global hesabı oluştur"""
        
        account = BotAccount(
            system=TargetSystem.VFS,
            country=country,
            email=email,
            password=password,
            status=AccountStatus.CREATED,
        )
        
        # Launch browser
        session = await self.stealth.launch(proxy=proxy)
        
        try:
            # Navigate to VFS registration
            url = f"https://visa.vfsglobal.com/{country}/en/deu/register"
            await session.page.goto(url)
            
            # Wait for page load
            await session.page.wait_for_load_state("networkidle")
            
            # Fill registration form
            await self._fill_registration_form(session.page, email, password)
            
            # Submit
            await session.page.click('button[type="submit"]')
            
            # Wait for email verification
            verification_result = await self.email.wait_for_verification(
                email=email,
                timeout=300,  # 5 dakika
            )
            
            if verification_result.success:
                account.status = AccountStatus.ACTIVE
            else:
                account.status = AccountStatus.CREATED
                account.notes = "Email verification pending"
            
            # Save to pool
            await self.pool.add_account(account)
            
            return account
            
        finally:
            await session.close()
    
    async def _fill_registration_form(self, page, email: str, password: str):
        """VFS kayıt formunu doldur"""
        
        # Human-like typing
        await page.fill('input[name="email"]', email, delay=100)
        await asyncio.sleep(random.uniform(0.5, 1.0))
        
        await page.fill('input[name="password"]', password, delay=100)
        await asyncio.sleep(random.uniform(0.5, 1.0))
        
        await page.fill('input[name="confirmPassword"]', password, delay=100)
        await asyncio.sleep(random.uniform(0.5, 1.0))
        
        # Terms checkbox
        await page.click('input[type="checkbox"]')
```

---

## Health Score Calculation

```python
class AccountHealthCalculator:
    """Hesap sağlık skoru hesaplama"""
    
    @staticmethod
    def calculate(account: BotAccount) -> int:
        """Sağlık skoru hesapla (0-100)"""
        
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
            hours_since_success = (datetime.utcnow() - account.last_success_at).total_seconds() / 3600
            if hours_since_success > 24:
                score -= min((hours_since_success - 24) * 0.5, 15)
        
        # Usage factor (15%)
        usage_ratio = account.daily_usage_count / account.daily_usage_limit
        if usage_ratio > 0.8:
            score -= (usage_ratio - 0.8) * 75  # 80%+ kullanımda penalty
        
        return int(max(0, min(100, score)))
    
    @staticmethod
    def should_cooldown(account: BotAccount) -> bool:
        """Cooldown gerekli mi?"""
        health = AccountHealthCalculator.calculate(account)
        return health < 50 or account.consecutive_failures >= 3
    
    @staticmethod
    def should_retire(account: BotAccount) -> bool:
        """Hesap retire edilmeli mi?"""
        health = AccountHealthCalculator.calculate(account)
        return health < 20 or account.consecutive_failures >= 10
```

---

## Ban Recovery Scheduler

```python
class BanRecoveryScheduler:
    """Ban recovery otomasyonu"""
    
    def __init__(self, pool_manager: AccountPoolManager):
        self.pool = pool_manager
        self.running = False
    
    async def start(self):
        """Recovery scheduler başlat"""
        self.running = True
        
        while self.running:
            await self._check_recoveries()
            await asyncio.sleep(60)  # Her dakika kontrol
    
    async def stop(self):
        """Scheduler durdur"""
        self.running = False
    
    async def _check_recoveries(self):
        """Recovery bekleyen hesapları kontrol et"""
        
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
            
            # Check temporary ban expiry (VFS 10 dakika ban)
            if account.status == AccountStatus.BANNED:
                if account.cooldown_until and account.cooldown_until <= now:
                    # Attempt recovery - mark as active but watch closely
                    account.status = AccountStatus.ACTIVE
                    account.cooldown_until = None
                    account.health_score = 50  # Start with lower health
```

---

## Monitoring ve Alerting

```python
class AccountPoolMonitor:
    """Hesap havuzu monitoring"""
    
    def __init__(self, pool_manager: AccountPoolManager):
        self.pool = pool_manager
    
    async def get_health_report(self) -> Dict:
        """Sağlık raporu"""
        
        stats = await self.pool.get_pool_stats()
        
        alerts = []
        
        for pool_key, pool_stats in stats.items():
            # Active account ratio check
            if pool_stats.total > 0:
                active_ratio = (pool_stats.active + pool_stats.in_use) / pool_stats.total
                
                if active_ratio < 0.3:
                    alerts.append({
                        "level": "critical",
                        "pool": pool_key,
                        "message": f"Only {active_ratio*100:.0f}% accounts active",
                    })
                elif active_ratio < 0.5:
                    alerts.append({
                        "level": "warning",
                        "pool": pool_key,
                        "message": f"Only {active_ratio*100:.0f}% accounts active",
                    })
            
            # Banned account check
            if pool_stats.total > 0:
                banned_ratio = pool_stats.banned / pool_stats.total
                if banned_ratio > 0.2:
                    alerts.append({
                        "level": "warning",
                        "pool": pool_key,
                        "message": f"{banned_ratio*100:.0f}% accounts banned",
                    })
            
            # Average health check
            if pool_stats.avg_health < 50:
                alerts.append({
                    "level": "warning",
                    "pool": pool_key,
                    "message": f"Average health score: {pool_stats.avg_health:.0f}",
                })
        
        return {
            "stats": {k: v.__dict__ for k, v in stats.items()},
            "alerts": alerts,
            "timestamp": datetime.utcnow().isoformat(),
        }
    
    async def get_account_details(self, account_id: str) -> Dict:
        """Tekil hesap detayları"""
        
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
            "last_used": account.last_used_at.isoformat() if account.last_used_at else None,
            "last_success": account.last_success_at.isoformat() if account.last_success_at else None,
            "cooldown_until": account.cooldown_until.isoformat() if account.cooldown_until else None,
            "last_error": account.last_error,
        }
```

---

## Site-Specific Account Requirements

### VFS Global

```python
VFS_ACCOUNT_CONFIG = {
    "countries": ["de", "fr", "nl", "no", "se"],
    "accounts_per_country": 20,  # Minimum
    "daily_limit": 50,
    "cooldown_initial": 10,  # dakika
    "ban_recovery_time": 120,  # dakika (2 saat)
    "notes": "Her ülke için ayrı hesap gerekli. Login sonrası Cloudflare check var.",
}
```

### iDATA

```python
IDATA_ACCOUNT_CONFIG = {
    "countries": ["de", "it"],
    "accounts_per_country": 15,
    "daily_limit": 100,  # Daha toleranslı
    "cooldown_initial": 5,
    "ban_recovery_time": 30,
    "notes": "Daha az agresif rate limiting. jQuery API kullanır.",
}
```

### BLS Spain

```python
BLS_ACCOUNT_CONFIG = {
    "countries": ["es"],
    "accounts_per_country": 10,
    "daily_limit": 30,
    "cooldown_initial": 15,
    "ban_recovery_time": 60,
    "notes": "Keyboard-only input. Paste çalışmıyor.",
}
```

### KKOSMOS

```python
KKOSMOS_ACCOUNT_CONFIG = {
    "countries": ["gr"],
    "accounts_per_country": 10,
    "daily_limit": 20,
    "cooldown_initial": 20,
    "ban_recovery_time": 180,  # 3 saat
    "notes": "SMS verification zorunlu. Telefon numarası pool gerekli.",
}
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | 200 concurrent session için hesap sağlanmalı | Load test |
| AC-002 | Health score doğru hesaplanmalı | Unit test |
| AC-003 | Ban detection %95+ accuracy | Pattern test |
| AC-004 | Cooldown exponential backoff çalışmalı | Timing test |
| AC-005 | Recovery scheduler otomatik aktif etmeli | Integration test |
| AC-006 | Daily limit enforcement çalışmalı | Unit test |
| AC-007 | Account-proxy pairing başarı oranını artırmalı | A/B test |

---

## Edge Cases

### Tüm Hesaplar Cooldown'da

```
Detection: Pool'da active hesap kalmadı
Action:
  1. Alert: "Pool exhausted"
  2. En kısa cooldown'lu hesabı force-active yap (risky)
  3. Veya: Session'ı queue'ya geri koy, beklet
```

### Cascade Ban

```
Detection: 5 dakika içinde 5+ hesap ban
Action:
  1. Alert: "Cascade ban detected"
  2. Tüm aktif session'ları pause
  3. Proxy pool'u rotate et
  4. 30 dakika global cooldown
```

### Credential Leak Şüphesi

```
Detection: Hesap dışarıdan login denemesi aldı
Action:
  1. Hesabı immediate retire
  2. Password değiştir (manuel)
  3. Alert: Security incident
```

---

## Güvenlik Notları

1. **Credential Encryption:** AES-256-GCM, key rotation monthly
2. **No Plaintext Logging:** Password/email asla log'a yazılmaz
3. **Memory Protection:** Credentials decrypted only when needed
4. **Access Control:** Sadece system_bot role erişebilir
5. **Audit Trail:** Her login/logout logged (credential olmadan)

---

## Sonraki Adımlar

Bu spec tamamlandığında Grup 2 (Bot Automation Core) complete olur.

Sonraki: **Grup 3 - Site Adapters** (008, 009, 010, 011)
