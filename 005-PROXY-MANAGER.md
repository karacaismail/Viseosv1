# 005 - Proxy Manager Specification

## Amaç

200 paralel session için residential proxy pool yönetimi. Multi-provider failover, health scoring, intelligent rotation, ASN diversity ve session pinning. VFS için %95+, iDATA için %98+ başarı oranı hedefi.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 001-ARCHITECTURE-OVERVIEW | Resource pool layer |
| 004-STEALTH-ENGINE | Her session'a proxy assignment |
| 007-ACCOUNT-POOL-MANAGER | Account-proxy pairing |
| 013-STATE-MACHINE | Proxy failure handling |

---

## Proxy Tipi Hierarchy

```
┌─────────────────────────────────────────────────────────────┐
│                    PROXY SELECTION                           │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   TIER 1 (Default)          TIER 2 (Fallback)               │
│   ┌──────────────┐          ┌──────────────┐                │
│   │  Residential │          │    Mobile    │                │
│   │    Proxy     │   ───►   │   (4G/5G)    │                │
│   │              │  detect  │              │                │
│   │  %95+ trust  │          │  %99+ trust  │                │
│   └──────────────┘          └──────────────┘                │
│          │                         │                         │
│          │ fallback                │ fallback                │
│          ▼                         ▼                         │
│   TIER 3 (Emergency)                                         │
│   ┌──────────────────────────────────────┐                  │
│   │         ISP/Static Residential        │                  │
│   │              %85+ trust               │                  │
│   └──────────────────────────────────────┘                  │
│                      │                                       │
│                      │ last resort                           │
│                      ▼                                       │
│   ┌──────────────────────────────────────┐                  │
│   │    Datacenter (sadece iDATA için)     │                  │
│   │              %60+ trust               │                  │
│   └──────────────────────────────────────┘                  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Provider Configuration

### Primary: Bright Data

```python
class BrightDataConfig:
    """Bright Data residential proxy configuration"""
    
    # Endpoint patterns
    RESIDENTIAL_ENDPOINT = "brd.superproxy.io:22225"
    MOBILE_ENDPOINT = "brd.superproxy.io:22226"
    ISP_ENDPOINT = "brd.superproxy.io:22227"
    
    # Zone credentials (from Directus api_configurations)
    # Format: brd-customer-{CUSTOMER_ID}-zone-{ZONE_NAME}
    
    # Geo-targeting
    COUNTRY_CODES = {
        "turkey": "tr",
        "germany": "de",
        "netherlands": "nl",
        "france": "fr",
    }
    
    # Session types
    SESSION_TYPES = {
        "rotating": "",           # Her request farklı IP
        "sticky": "session-",     # Aynı IP (30 dakika)
    }
    
    @classmethod
    def build_proxy_url(
        cls,
        customer_id: str,
        zone: str,
        password: str,
        country: str = "tr",
        session_id: str = None,
        proxy_type: str = "residential",
    ) -> Dict[str, str]:
        """Proxy URL oluştur"""
        
        endpoint = cls.RESIDENTIAL_ENDPOINT
        if proxy_type == "mobile":
            endpoint = cls.MOBILE_ENDPOINT
        elif proxy_type == "isp":
            endpoint = cls.ISP_ENDPOINT
        
        username = f"brd-customer-{customer_id}-zone-{zone}"
        
        # Country targeting
        username += f"-country-{country}"
        
        # Session (sticky IP)
        if session_id:
            username += f"-session-{session_id}"
        
        return {
            "protocol": "http",
            "host": endpoint.split(":")[0],
            "port": int(endpoint.split(":")[1]),
            "username": username,
            "password": password,
        }
```

### Secondary: Oxylabs

```python
class OxylabsConfig:
    """Oxylabs backup provider"""
    
    RESIDENTIAL_ENDPOINT = "pr.oxylabs.io:7777"
    
    @classmethod
    def build_proxy_url(
        cls,
        username: str,
        password: str,
        country: str = "tr",
        session_id: str = None,
    ) -> Dict[str, str]:
        """Oxylabs proxy URL"""
        
        user = f"customer-{username}-cc-{country}"
        
        if session_id:
            user += f"-sessid-{session_id}"
        
        return {
            "protocol": "http",
            "host": "pr.oxylabs.io",
            "port": 7777,
            "username": user,
            "password": password,
        }
```

### Tertiary: Smartproxy

```python
class SmartproxyConfig:
    """Smartproxy tertiary provider"""
    
    RESIDENTIAL_ENDPOINT = "gate.smartproxy.com:7000"
    
    @classmethod
    def build_proxy_url(
        cls,
        username: str,
        password: str,
        country: str = "tr",
        session_id: str = None,
    ) -> Dict[str, str]:
        """Smartproxy URL"""
        
        user = f"user-{username}-country-{country}"
        
        if session_id:
            user += f"-session-{session_id}"
        
        return {
            "protocol": "http",
            "host": "gate.smartproxy.com",
            "port": 7000,
            "username": user,
            "password": password,
        }
```

---

## Proxy Pool Manager

```python
from dataclasses import dataclass, field
from typing import Optional, Dict, List
from enum import Enum
import asyncio
import time
import random

class ProxyStatus(Enum):
    ACTIVE = "active"
    SLOW = "slow"
    BLOCKED = "blocked"
    COOLDOWN = "cooldown"
    RETIRED = "retired"

class ProxyType(Enum):
    RESIDENTIAL = "residential"
    MOBILE = "mobile"
    ISP = "isp"
    DATACENTER = "datacenter"

@dataclass
class ProxyHealth:
    """Proxy sağlık metrikleri"""
    proxy_id: str
    provider: str
    proxy_type: ProxyType
    country: str
    
    status: ProxyStatus = ProxyStatus.ACTIVE
    health_score: int = 100  # 0-100
    
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    consecutive_failures: int = 0
    
    avg_response_ms: float = 0.0
    last_used_at: Optional[float] = None
    last_success_at: Optional[float] = None
    cooldown_until: Optional[float] = None
    
    # ASN tracking (anti-pattern detection)
    asn: Optional[str] = None
    subnet: Optional[str] = None

class ProxyPoolManager:
    """Proxy havuz yöneticisi"""
    
    # Thresholds
    HEALTH_THRESHOLD_SLOW = 70
    HEALTH_THRESHOLD_BLOCKED = 40
    HEALTH_THRESHOLD_RETIRE = 20
    
    CONSECUTIVE_FAILURE_COOLDOWN = 3
    COOLDOWN_DURATION_SECONDS = 300  # 5 dakika
    
    MAX_REQUESTS_PER_PROXY = 1000
    
    def __init__(self):
        self.proxies: Dict[str, ProxyHealth] = {}
        self.provider_configs: Dict[str, Any] = {}
        self.lock = asyncio.Lock()
        
        # ASN diversity tracking
        self.recent_asns: List[str] = []
        self.max_asn_history = 10
    
    async def initialize(self, configs: Dict[str, Any]):
        """Provider config'lerini yükle"""
        self.provider_configs = configs
        
        # Pre-warm proxy pool
        for provider in ["brightdata", "oxylabs", "smartproxy"]:
            if provider in configs:
                await self._register_provider_proxies(provider, configs[provider])
    
    async def get_proxy(
        self,
        target_site: str,
        country: str = "tr",
        sticky_session: bool = True,
        proxy_type: ProxyType = ProxyType.RESIDENTIAL,
        exclude_asns: List[str] = None,
    ) -> Dict[str, str]:
        """En uygun proxy'yi seç"""
        
        async with self.lock:
            # Filter eligible proxies
            eligible = self._filter_eligible_proxies(
                country=country,
                proxy_type=proxy_type,
                exclude_asns=exclude_asns or self.recent_asns[-5:],
            )
            
            if not eligible:
                # Fallback to different proxy type
                eligible = self._filter_eligible_proxies(
                    country=country,
                    proxy_type=ProxyType.MOBILE if proxy_type == ProxyType.RESIDENTIAL else ProxyType.ISP,
                )
            
            if not eligible:
                raise NoProxyAvailableError(f"No proxy available for {country}")
            
            # Weighted random selection based on health score
            proxy = self._weighted_select(eligible)
            
            # Generate session ID for sticky
            session_id = None
            if sticky_session:
                session_id = f"{target_site}_{int(time.time())}_{random.randint(1000, 9999)}"
            
            # Build proxy URL
            proxy_url = self._build_proxy_url(proxy, session_id)
            
            # Track ASN
            if proxy.asn:
                self.recent_asns.append(proxy.asn)
                if len(self.recent_asns) > self.max_asn_history:
                    self.recent_asns.pop(0)
            
            # Update last used
            proxy.last_used_at = time.time()
            
            return {
                "proxy_id": proxy.proxy_id,
                "session_id": session_id,
                **proxy_url,
            }
    
    def _filter_eligible_proxies(
        self,
        country: str,
        proxy_type: ProxyType,
        exclude_asns: List[str] = None,
    ) -> List[ProxyHealth]:
        """Uygun proxy'leri filtrele"""
        
        eligible = []
        now = time.time()
        
        for proxy in self.proxies.values():
            # Status check
            if proxy.status in [ProxyStatus.BLOCKED, ProxyStatus.RETIRED]:
                continue
            
            # Cooldown check
            if proxy.cooldown_until and proxy.cooldown_until > now:
                continue
            
            # Country check
            if proxy.country != country:
                continue
            
            # Type check
            if proxy.proxy_type != proxy_type:
                continue
            
            # ASN diversity (anti-pattern)
            if exclude_asns and proxy.asn in exclude_asns:
                continue
            
            # Usage limit check
            if proxy.total_requests >= self.MAX_REQUESTS_PER_PROXY:
                continue
            
            eligible.append(proxy)
        
        return eligible
    
    def _weighted_select(self, proxies: List[ProxyHealth]) -> ProxyHealth:
        """Sağlık skoruna göre weighted selection"""
        
        # Weight = health_score + recency_bonus
        weights = []
        now = time.time()
        
        for proxy in proxies:
            weight = proxy.health_score
            
            # Recency bonus: daha az kullanılan proxy'ler tercih edilir
            if proxy.last_used_at:
                idle_time = now - proxy.last_used_at
                recency_bonus = min(idle_time / 60, 20)  # Max 20 bonus
                weight += recency_bonus
            else:
                weight += 20  # Hiç kullanılmamış, bonus ver
            
            weights.append(max(weight, 1))
        
        return random.choices(proxies, weights=weights, k=1)[0]
    
    def _build_proxy_url(
        self, 
        proxy: ProxyHealth, 
        session_id: str = None
    ) -> Dict[str, str]:
        """Provider'a göre proxy URL oluştur"""
        
        config = self.provider_configs.get(proxy.provider)
        
        if proxy.provider == "brightdata":
            return BrightDataConfig.build_proxy_url(
                customer_id=config["customer_id"],
                zone=config["zone"],
                password=config["password"],
                country=proxy.country,
                session_id=session_id,
                proxy_type=proxy.proxy_type.value,
            )
        elif proxy.provider == "oxylabs":
            return OxylabsConfig.build_proxy_url(
                username=config["username"],
                password=config["password"],
                country=proxy.country,
                session_id=session_id,
            )
        elif proxy.provider == "smartproxy":
            return SmartproxyConfig.build_proxy_url(
                username=config["username"],
                password=config["password"],
                country=proxy.country,
                session_id=session_id,
            )
        
        raise ValueError(f"Unknown provider: {proxy.provider}")
    
    async def report_success(self, proxy_id: str, response_time_ms: float):
        """Başarılı request raporla"""
        
        async with self.lock:
            if proxy_id not in self.proxies:
                return
            
            proxy = self.proxies[proxy_id]
            
            proxy.total_requests += 1
            proxy.successful_requests += 1
            proxy.consecutive_failures = 0
            proxy.last_success_at = time.time()
            
            # Update average response time
            proxy.avg_response_ms = (
                (proxy.avg_response_ms * (proxy.total_requests - 1) + response_time_ms)
                / proxy.total_requests
            )
            
            # Update health score
            self._recalculate_health(proxy)
            
            # Clear cooldown
            if proxy.status == ProxyStatus.COOLDOWN:
                proxy.status = ProxyStatus.ACTIVE
                proxy.cooldown_until = None
    
    async def report_failure(
        self, 
        proxy_id: str, 
        error_type: str,
        is_blocked: bool = False,
    ):
        """Başarısız request raporla"""
        
        async with self.lock:
            if proxy_id not in self.proxies:
                return
            
            proxy = self.proxies[proxy_id]
            
            proxy.total_requests += 1
            proxy.failed_requests += 1
            proxy.consecutive_failures += 1
            
            # Health penalty
            if is_blocked:
                proxy.health_score = max(0, proxy.health_score - 30)
            else:
                proxy.health_score = max(0, proxy.health_score - 10)
            
            # Status transitions
            if is_blocked or proxy.consecutive_failures >= self.CONSECUTIVE_FAILURE_COOLDOWN:
                proxy.status = ProxyStatus.COOLDOWN
                proxy.cooldown_until = time.time() + self.COOLDOWN_DURATION_SECONDS
            
            if proxy.health_score < self.HEALTH_THRESHOLD_BLOCKED:
                proxy.status = ProxyStatus.BLOCKED
            
            if proxy.health_score < self.HEALTH_THRESHOLD_RETIRE:
                proxy.status = ProxyStatus.RETIRED
            
            self._recalculate_health(proxy)
    
    def _recalculate_health(self, proxy: ProxyHealth):
        """Sağlık skorunu yeniden hesapla"""
        
        if proxy.total_requests == 0:
            proxy.health_score = 100
            return
        
        # Base score from success rate
        success_rate = proxy.successful_requests / proxy.total_requests
        base_score = success_rate * 100
        
        # Response time penalty
        if proxy.avg_response_ms > 5000:  # 5 saniyeden yavaş
            base_score -= 20
        elif proxy.avg_response_ms > 3000:
            base_score -= 10
        
        # Consecutive failure penalty
        failure_penalty = min(proxy.consecutive_failures * 10, 50)
        
        proxy.health_score = int(max(0, min(100, base_score - failure_penalty)))
        
        # Update status based on health
        if proxy.health_score < self.HEALTH_THRESHOLD_SLOW and proxy.status == ProxyStatus.ACTIVE:
            proxy.status = ProxyStatus.SLOW
```

---

## Rotation Strategies

### Per-Request Rotation

```python
class RotatingProxyStrategy:
    """Her request'te farklı IP"""
    
    def __init__(self, pool: ProxyPoolManager):
        self.pool = pool
    
    async def get_proxy(self, **kwargs) -> Dict:
        """Her çağrıda yeni proxy"""
        return await self.pool.get_proxy(
            sticky_session=False,
            **kwargs,
        )
```

### Sticky Session

```python
class StickySessionStrategy:
    """Aynı IP'yi koru (authenticated flows için)"""
    
    def __init__(self, pool: ProxyPoolManager):
        self.pool = pool
        self.active_sessions: Dict[str, Dict] = {}
    
    async def get_proxy(self, session_key: str, **kwargs) -> Dict:
        """Aynı session için aynı proxy"""
        
        if session_key in self.active_sessions:
            # Mevcut proxy'yi döndür (süresi dolmadıysa)
            session = self.active_sessions[session_key]
            if time.time() - session["created_at"] < 1800:  # 30 dakika
                return session["proxy"]
        
        # Yeni sticky session başlat
        proxy = await self.pool.get_proxy(
            sticky_session=True,
            **kwargs,
        )
        
        self.active_sessions[session_key] = {
            "proxy": proxy,
            "created_at": time.time(),
        }
        
        return proxy
    
    async def release_session(self, session_key: str):
        """Session'ı serbest bırak"""
        if session_key in self.active_sessions:
            del self.active_sessions[session_key]
```

### Geographic Rotation

```python
class GeoRotationStrategy:
    """Ülke bazlı rotasyon (detection pattern kırma)"""
    
    COUNTRY_SEQUENCE = ["tr", "de", "nl", "fr", "tr"]
    
    def __init__(self, pool: ProxyPoolManager):
        self.pool = pool
        self.country_index = 0
    
    async def get_proxy(self, target_site: str, **kwargs) -> Dict:
        """Sırayla farklı ülkelerden proxy"""
        
        country = self.COUNTRY_SEQUENCE[self.country_index]
        self.country_index = (self.country_index + 1) % len(self.COUNTRY_SEQUENCE)
        
        return await self.pool.get_proxy(
            target_site=target_site,
            country=country,
            **kwargs,
        )
```

### ASN Diversity Strategy

```python
class ASNDiversityStrategy:
    """Farklı ASN'lerden proxy seç (pattern detection'ı zorlaştır)"""
    
    def __init__(self, pool: ProxyPoolManager):
        self.pool = pool
        self.used_asns: List[str] = []
        self.max_history = 10
    
    async def get_proxy(self, **kwargs) -> Dict:
        """Farklı ASN'den proxy"""
        
        proxy = await self.pool.get_proxy(
            exclude_asns=self.used_asns[-5:],  # Son 5 ASN'i hariç tut
            **kwargs,
        )
        
        # Track ASN (proxy response'dan alınacak)
        if proxy.get("asn"):
            self.used_asns.append(proxy["asn"])
            if len(self.used_asns) > self.max_history:
                self.used_asns.pop(0)
        
        return proxy
```

---

## Target Site Specific Configuration

```python
class SiteProxyConfig:
    """Site bazlı proxy konfigürasyonu"""
    
    SITE_CONFIGS = {
        "vfs": {
            "proxy_type": ProxyType.RESIDENTIAL,
            "sticky_session": True,
            "countries": ["tr"],
            "rotation": "sticky",  # Login sonrası aynı IP
            "min_health": 80,
            "failover_types": [ProxyType.MOBILE, ProxyType.ISP],
        },
        "idata": {
            "proxy_type": ProxyType.RESIDENTIAL,
            "sticky_session": True,
            "countries": ["tr"],
            "rotation": "sticky",
            "min_health": 60,  # Daha toleranslı
            "failover_types": [ProxyType.DATACENTER],  # iDATA datacenter kabul eder
        },
        "bls": {
            "proxy_type": ProxyType.RESIDENTIAL,
            "sticky_session": True,
            "countries": ["tr", "es"],
            "rotation": "sticky",
            "min_health": 70,
            "failover_types": [ProxyType.ISP],
        },
        "kkosmos": {
            "proxy_type": ProxyType.RESIDENTIAL,
            "sticky_session": True,
            "countries": ["tr", "gr"],
            "rotation": "sticky",
            "min_health": 75,
            "failover_types": [ProxyType.MOBILE],
        },
    }
    
    @classmethod
    def get_config(cls, site: str) -> Dict:
        return cls.SITE_CONFIGS.get(site, cls.SITE_CONFIGS["vfs"])
```

---

## Provider Failover Chain

```python
class ProxyFailoverChain:
    """Provider bazlı failover"""
    
    PROVIDER_PRIORITY = [
        "brightdata",   # Primary - en geniş pool
        "oxylabs",      # Secondary - iyi kalite
        "smartproxy",   # Tertiary - budget option
    ]
    
    def __init__(self, pool: ProxyPoolManager):
        self.pool = pool
        self.provider_status: Dict[str, bool] = {p: True for p in self.PROVIDER_PRIORITY}
        self.failure_counts: Dict[str, int] = {p: 0 for p in self.PROVIDER_PRIORITY}
    
    async def get_proxy_with_failover(self, **kwargs) -> Dict:
        """Provider failover ile proxy al"""
        
        for provider in self.PROVIDER_PRIORITY:
            if not self.provider_status[provider]:
                continue
            
            try:
                proxy = await self.pool.get_proxy(
                    provider=provider,
                    **kwargs,
                )
                
                # Success, reset failure count
                self.failure_counts[provider] = 0
                return proxy
                
            except NoProxyAvailableError:
                self.failure_counts[provider] += 1
                
                # 5 ardışık başarısızlık, provider'ı devre dışı bırak (5 dk)
                if self.failure_counts[provider] >= 5:
                    self.provider_status[provider] = False
                    asyncio.create_task(self._reactivate_provider(provider, delay=300))
                
                continue
        
        raise NoProxyAvailableError("All providers exhausted")
    
    async def _reactivate_provider(self, provider: str, delay: int):
        """Provider'ı yeniden aktif et"""
        await asyncio.sleep(delay)
        self.provider_status[provider] = True
        self.failure_counts[provider] = 0
```

---

## Bandwidth ve Maliyet Optimizasyonu

```python
class ProxyCostOptimizer:
    """Proxy maliyetini optimize et"""
    
    # Fiyatlandırma ($/GB)
    PRICING = {
        "brightdata": {
            "residential": 12.0,
            "mobile": 25.0,
            "isp": 8.0,
            "datacenter": 0.5,
        },
        "oxylabs": {
            "residential": 10.0,
            "mobile": 20.0,
        },
        "smartproxy": {
            "residential": 8.0,
        },
    }
    
    def __init__(self):
        self.usage: Dict[str, float] = {}  # provider -> GB used
        self.budget_limit: float = 500.0   # Monthly budget USD
    
    def track_usage(self, provider: str, proxy_type: str, bytes_transferred: int):
        """Bandwidth kullanımını takip et"""
        gb = bytes_transferred / (1024 ** 3)
        
        key = f"{provider}_{proxy_type}"
        self.usage[key] = self.usage.get(key, 0) + gb
    
    def get_estimated_cost(self) -> float:
        """Tahmini aylık maliyet"""
        total = 0.0
        
        for key, gb in self.usage.items():
            provider, proxy_type = key.rsplit("_", 1)
            price_per_gb = self.PRICING.get(provider, {}).get(proxy_type, 10.0)
            total += gb * price_per_gb
        
        return total
    
    def should_downgrade(self) -> bool:
        """Bütçe aşıldıysa downgrade öner"""
        return self.get_estimated_cost() > self.budget_limit * 0.8
    
    def get_cost_effective_proxy_type(self, site: str) -> ProxyType:
        """Maliyet-etkin proxy tipi öner"""
        
        if self.should_downgrade():
            # iDATA datacenter kabul eder
            if site == "idata":
                return ProxyType.DATACENTER
            # VFS için ISP dene
            return ProxyType.ISP
        
        return ProxyType.RESIDENTIAL
```

---

## Monitoring ve Alerting

```python
class ProxyHealthMonitor:
    """Proxy pool sağlık monitoring"""
    
    def __init__(self, pool: ProxyPoolManager):
        self.pool = pool
    
    async def get_pool_status(self) -> Dict:
        """Pool durumu özeti"""
        
        status_counts = {s.value: 0 for s in ProxyStatus}
        provider_stats = {}
        type_stats = {}
        
        for proxy in self.pool.proxies.values():
            status_counts[proxy.status.value] += 1
            
            # Provider stats
            if proxy.provider not in provider_stats:
                provider_stats[proxy.provider] = {
                    "total": 0, "active": 0, "success_rate": 0.0
                }
            provider_stats[proxy.provider]["total"] += 1
            if proxy.status == ProxyStatus.ACTIVE:
                provider_stats[proxy.provider]["active"] += 1
            
            # Type stats
            if proxy.proxy_type.value not in type_stats:
                type_stats[proxy.proxy_type.value] = {"total": 0, "active": 0}
            type_stats[proxy.proxy_type.value]["total"] += 1
            if proxy.status == ProxyStatus.ACTIVE:
                type_stats[proxy.proxy_type.value]["active"] += 1
        
        return {
            "total_proxies": len(self.pool.proxies),
            "status_breakdown": status_counts,
            "by_provider": provider_stats,
            "by_type": type_stats,
            "health_avg": self._calculate_avg_health(),
            "alerts": self._check_alerts(status_counts),
        }
    
    def _calculate_avg_health(self) -> float:
        """Ortalama sağlık skoru"""
        if not self.pool.proxies:
            return 0.0
        
        total = sum(p.health_score for p in self.pool.proxies.values())
        return total / len(self.pool.proxies)
    
    def _check_alerts(self, status_counts: Dict) -> List[str]:
        """Alert koşullarını kontrol et"""
        alerts = []
        
        total = sum(status_counts.values())
        if total == 0:
            alerts.append("CRITICAL: No proxies in pool")
            return alerts
        
        active_rate = status_counts.get("active", 0) / total
        
        if active_rate < 0.3:
            alerts.append("CRITICAL: Less than 30% proxies active")
        elif active_rate < 0.5:
            alerts.append("WARNING: Less than 50% proxies active")
        
        if status_counts.get("blocked", 0) > total * 0.2:
            alerts.append("WARNING: More than 20% proxies blocked")
        
        return alerts
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | 200 concurrent session için proxy sağlanmalı | Load test |
| AC-002 | Bright Data primary, failover çalışmalı | Failover test |
| AC-003 | Sticky session 30 dakika aynı IP tutmalı | Session test |
| AC-004 | Health score doğru hesaplanmalı | Unit test |
| AC-005 | ASN diversity farklı subnet'ler seçmeli | Diversity test |
| AC-006 | Budget tracking doğru maliyet hesaplamalı | Integration test |
| AC-007 | VFS %95+, iDATA %98+ başarı oranı | Production test |

---

## Edge Cases

### Provider Outage

```
Detection: Tüm proxy'ler 5 dk içinde fail
Action:
  1. Provider'ı devre dışı bırak
  2. Sonraki provider'a geç
  3. Alert gönder
  4. 15 dk sonra tekrar dene
```

### IP Exhaustion

```
Detection: Belirli ülke için proxy kalmadı
Action:
  1. Komşu ülke proxy'si dene (tr → de → nl)
  2. Proxy tipi downgrade (residential → isp)
  3. Cooldown'daki proxy'leri aggressive recovery
  4. Alert: "Proxy pool expansion needed"
```

### Mass Block Event

```
Detection: %50+ proxy 10 dk içinde blocked
Action:
  1. Tüm aktif session'ları pause
  2. 30 dakika cooldown
  3. Farklı provider'dan fresh proxy'ler al
  4. Gradual ramp-up
```

---

## Güvenlik Notları

1. **Credential Storage:** Provider password'leri Directus'ta encrypted
2. **No IP Logging:** Kullanılan IP'ler loglanmaz (privacy)
3. **Session Isolation:** Her booking request izole session
4. **Rate Limiting:** Provider rate limit'lerine uyum
5. **Geo-Compliance:** Sadece izin verilen ülkelerden proxy

---

## Sonraki Adımlar

1. **006-CAPTCHA-SOLVER.md** - CAPTCHA çözüm chain
2. **007-ACCOUNT-POOL-MANAGER.md** - Bot hesap yönetimi
3. Provider API entegrasyonları
