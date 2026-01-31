# 006 - CAPTCHA Solver Specification

## Amaç

VFS Global'in reCAPTCHA v2/v3, hCaptcha ve Cloudflare Turnstile challenge'larını %95+ başarı oranıyla çözen multi-provider CAPTCHA solving chain. Failover, cost optimization ve site-specific stratejiler.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 004-STEALTH-ENGINE | CAPTCHA detection ve injection |
| 005-PROXY-MANAGER | CAPTCHA çözümü için IP consistency |
| 008-VFS-ADAPTER | VFS-specific challenge handling |
| 012-AI-DECISION-ENGINE | Solver strategy selection |

---

## CAPTCHA Tipleri ve Karşılaşma Sıklığı

| Site | CAPTCHA Tipi | Sıklık | Zorluk |
|------|--------------|--------|--------|
| VFS Global | reCAPTCHA v2 + Cloudflare Turnstile | Her login, her page | Yüksek |
| iDATA | reCAPTCHA v2 | İlk login | Düşük |
| BLS Spain | reCAPTCHA v2 | Form submit | Orta |
| KKOSMOS | reCAPTCHA v2 | Her işlem | Orta |

---

## Provider Hierarchy

```
┌─────────────────────────────────────────────────────────────┐
│                    CAPTCHA SOLVER CHAIN                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   TIER 1 (Hız)              TIER 2 (Güvenilirlik)           │
│   ┌──────────────┐          ┌──────────────┐                │
│   │   CapSolver  │          │   2Captcha   │                │
│   │              │   ───►   │              │                │
│   │  AI-powered  │  fail    │ Human-based  │                │
│   │  ~10-20 sec  │          │  ~30-60 sec  │                │
│   └──────────────┘          └──────────────┘                │
│          │                         │                         │
│          │ fail                    │ fail                    │
│          ▼                         ▼                         │
│   TIER 3 (Backup)                                            │
│   ┌──────────────────────────────────────┐                  │
│   │            Anti-Captcha              │                  │
│   │           ~20-40 sec                 │                  │
│   └──────────────────────────────────────┘                  │
│                      │                                       │
│                      │ fail (rare)                           │
│                      ▼                                       │
│   ┌──────────────────────────────────────┐                  │
│   │       Manual Escalation Queue        │                  │
│   │      (Human operator fallback)       │                  │
│   └──────────────────────────────────────┘                  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Provider Configurations

### CapSolver (Primary - AI-Powered)

```python
import httpx
from typing import Optional, Dict, Any

class CapSolverProvider:
    """CapSolver - AI-based CAPTCHA solving"""
    
    BASE_URL = "https://api.capsolver.com"
    
    # Desteklenen CAPTCHA tipleri
    SUPPORTED_TYPES = [
        "ReCaptchaV2Task",
        "ReCaptchaV2TaskProxyless",
        "ReCaptchaV3Task",
        "ReCaptchaV3TaskProxyless",
        "HCaptchaTask",
        "HCaptchaTaskProxyless",
        "FunCaptchaTask",
        "TurnstileTask",
        "TurnstileTaskProxyless",
    ]
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=120)
    
    async def solve_recaptcha_v2(
        self,
        site_key: str,
        page_url: str,
        proxy: Optional[Dict] = None,
        invisible: bool = False,
    ) -> str:
        """reCAPTCHA v2 çöz"""
        
        task_type = "ReCaptchaV2Task" if proxy else "ReCaptchaV2TaskProxyless"
        
        task = {
            "type": task_type,
            "websiteURL": page_url,
            "websiteKey": site_key,
            "isInvisible": invisible,
        }
        
        if proxy:
            task.update({
                "proxyType": proxy.get("protocol", "http"),
                "proxyAddress": proxy["host"],
                "proxyPort": proxy["port"],
                "proxyLogin": proxy.get("username"),
                "proxyPassword": proxy.get("password"),
            })
        
        return await self._solve(task)
    
    async def solve_recaptcha_v3(
        self,
        site_key: str,
        page_url: str,
        action: str = "verify",
        min_score: float = 0.7,
        proxy: Optional[Dict] = None,
    ) -> str:
        """reCAPTCHA v3 çöz"""
        
        task_type = "ReCaptchaV3Task" if proxy else "ReCaptchaV3TaskProxyless"
        
        task = {
            "type": task_type,
            "websiteURL": page_url,
            "websiteKey": site_key,
            "pageAction": action,
            "minScore": min_score,
        }
        
        if proxy:
            task.update({
                "proxyType": proxy.get("protocol", "http"),
                "proxyAddress": proxy["host"],
                "proxyPort": proxy["port"],
                "proxyLogin": proxy.get("username"),
                "proxyPassword": proxy.get("password"),
            })
        
        return await self._solve(task)
    
    async def solve_turnstile(
        self,
        site_key: str,
        page_url: str,
        proxy: Optional[Dict] = None,
    ) -> str:
        """Cloudflare Turnstile çöz"""
        
        task_type = "TurnstileTask" if proxy else "TurnstileTaskProxyless"
        
        task = {
            "type": task_type,
            "websiteURL": page_url,
            "websiteKey": site_key,
        }
        
        if proxy:
            task.update({
                "proxyType": proxy.get("protocol", "http"),
                "proxyAddress": proxy["host"],
                "proxyPort": proxy["port"],
                "proxyLogin": proxy.get("username"),
                "proxyPassword": proxy.get("password"),
            })
        
        return await self._solve(task)
    
    async def solve_hcaptcha(
        self,
        site_key: str,
        page_url: str,
        proxy: Optional[Dict] = None,
    ) -> str:
        """hCaptcha çöz"""
        
        task_type = "HCaptchaTask" if proxy else "HCaptchaTaskProxyless"
        
        task = {
            "type": task_type,
            "websiteURL": page_url,
            "websiteKey": site_key,
        }
        
        if proxy:
            task.update({
                "proxyType": proxy.get("protocol", "http"),
                "proxyAddress": proxy["host"],
                "proxyPort": proxy["port"],
                "proxyLogin": proxy.get("username"),
                "proxyPassword": proxy.get("password"),
            })
        
        return await self._solve(task)
    
    async def _solve(self, task: Dict) -> str:
        """Task oluştur ve sonuç bekle"""
        
        # Create task
        create_response = await self.client.post(
            f"{self.BASE_URL}/createTask",
            json={
                "clientKey": self.api_key,
                "task": task,
            }
        )
        
        create_data = create_response.json()
        
        if create_data.get("errorId"):
            raise CaptchaSolveError(
                f"CapSolver create error: {create_data.get('errorDescription')}"
            )
        
        task_id = create_data["taskId"]
        
        # Poll for result
        for _ in range(60):  # Max 120 saniye (60 * 2)
            await asyncio.sleep(2)
            
            result_response = await self.client.post(
                f"{self.BASE_URL}/getTaskResult",
                json={
                    "clientKey": self.api_key,
                    "taskId": task_id,
                }
            )
            
            result_data = result_response.json()
            
            if result_data.get("errorId"):
                raise CaptchaSolveError(
                    f"CapSolver result error: {result_data.get('errorDescription')}"
                )
            
            if result_data.get("status") == "ready":
                return result_data["solution"]["gRecaptchaResponse"]
        
        raise CaptchaSolveError("CapSolver timeout")
    
    async def get_balance(self) -> float:
        """Bakiye sorgula"""
        response = await self.client.post(
            f"{self.BASE_URL}/getBalance",
            json={"clientKey": self.api_key}
        )
        return response.json().get("balance", 0)
```

### 2Captcha (Secondary - Human-Based)

```python
class TwoCaptchaProvider:
    """2Captcha - Human workforce CAPTCHA solving"""
    
    BASE_URL = "https://2captcha.com"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=180)
    
    async def solve_recaptcha_v2(
        self,
        site_key: str,
        page_url: str,
        proxy: Optional[Dict] = None,
        invisible: bool = False,
    ) -> str:
        """reCAPTCHA v2 çöz"""
        
        params = {
            "key": self.api_key,
            "method": "userrecaptcha",
            "googlekey": site_key,
            "pageurl": page_url,
            "json": 1,
        }
        
        if invisible:
            params["invisible"] = 1
        
        if proxy:
            params["proxy"] = f"{proxy['username']}:{proxy['password']}@{proxy['host']}:{proxy['port']}"
            params["proxytype"] = proxy.get("protocol", "HTTP").upper()
        
        return await self._solve(params)
    
    async def solve_recaptcha_v3(
        self,
        site_key: str,
        page_url: str,
        action: str = "verify",
        min_score: float = 0.7,
        proxy: Optional[Dict] = None,
    ) -> str:
        """reCAPTCHA v3 çöz"""
        
        params = {
            "key": self.api_key,
            "method": "userrecaptcha",
            "googlekey": site_key,
            "pageurl": page_url,
            "version": "v3",
            "action": action,
            "min_score": min_score,
            "json": 1,
        }
        
        if proxy:
            params["proxy"] = f"{proxy['username']}:{proxy['password']}@{proxy['host']}:{proxy['port']}"
            params["proxytype"] = proxy.get("protocol", "HTTP").upper()
        
        return await self._solve(params)
    
    async def solve_turnstile(
        self,
        site_key: str,
        page_url: str,
        proxy: Optional[Dict] = None,
    ) -> str:
        """Cloudflare Turnstile çöz"""
        
        params = {
            "key": self.api_key,
            "method": "turnstile",
            "sitekey": site_key,
            "pageurl": page_url,
            "json": 1,
        }
        
        if proxy:
            params["proxy"] = f"{proxy['username']}:{proxy['password']}@{proxy['host']}:{proxy['port']}"
            params["proxytype"] = proxy.get("protocol", "HTTP").upper()
        
        return await self._solve(params)
    
    async def solve_hcaptcha(
        self,
        site_key: str,
        page_url: str,
        proxy: Optional[Dict] = None,
    ) -> str:
        """hCaptcha çöz"""
        
        params = {
            "key": self.api_key,
            "method": "hcaptcha",
            "sitekey": site_key,
            "pageurl": page_url,
            "json": 1,
        }
        
        if proxy:
            params["proxy"] = f"{proxy['username']}:{proxy['password']}@{proxy['host']}:{proxy['port']}"
            params["proxytype"] = proxy.get("protocol", "HTTP").upper()
        
        return await self._solve(params)
    
    async def _solve(self, params: Dict) -> str:
        """Task submit ve sonuç bekle"""
        
        # Submit task
        submit_response = await self.client.get(
            f"{self.BASE_URL}/in.php",
            params=params,
        )
        
        submit_data = submit_response.json()
        
        if submit_data.get("status") != 1:
            raise CaptchaSolveError(f"2Captcha submit error: {submit_data.get('request')}")
        
        task_id = submit_data["request"]
        
        # Poll for result (2Captcha daha yavaş, 5 saniye interval)
        for _ in range(36):  # Max 180 saniye
            await asyncio.sleep(5)
            
            result_response = await self.client.get(
                f"{self.BASE_URL}/res.php",
                params={
                    "key": self.api_key,
                    "action": "get",
                    "id": task_id,
                    "json": 1,
                }
            )
            
            result_data = result_response.json()
            
            if result_data.get("status") == 1:
                return result_data["request"]
            
            if result_data.get("request") not in ["CAPCHA_NOT_READY"]:
                raise CaptchaSolveError(f"2Captcha error: {result_data.get('request')}")
        
        raise CaptchaSolveError("2Captcha timeout")
    
    async def get_balance(self) -> float:
        """Bakiye sorgula"""
        response = await self.client.get(
            f"{self.BASE_URL}/res.php",
            params={
                "key": self.api_key,
                "action": "getbalance",
                "json": 1,
            }
        )
        return float(response.json().get("request", 0))
    
    async def report_bad(self, task_id: str):
        """Yanlış çözümü raporla (refund için)"""
        await self.client.get(
            f"{self.BASE_URL}/res.php",
            params={
                "key": self.api_key,
                "action": "reportbad",
                "id": task_id,
            }
        )
```

### Anti-Captcha (Tertiary)

```python
class AntiCaptchaProvider:
    """Anti-Captcha backup provider"""
    
    BASE_URL = "https://api.anti-captcha.com"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=180)
    
    async def solve_recaptcha_v2(
        self,
        site_key: str,
        page_url: str,
        proxy: Optional[Dict] = None,
        invisible: bool = False,
    ) -> str:
        """reCAPTCHA v2 çöz"""
        
        task_type = "RecaptchaV2Task" if proxy else "RecaptchaV2TaskProxyless"
        
        task = {
            "type": task_type,
            "websiteURL": page_url,
            "websiteKey": site_key,
            "isInvisible": invisible,
        }
        
        if proxy:
            task.update({
                "proxyType": proxy.get("protocol", "http"),
                "proxyAddress": proxy["host"],
                "proxyPort": proxy["port"],
                "proxyLogin": proxy.get("username"),
                "proxyPassword": proxy.get("password"),
            })
        
        return await self._solve(task)
    
    async def _solve(self, task: Dict) -> str:
        """Task oluştur ve bekle"""
        
        # Create task
        create_response = await self.client.post(
            f"{self.BASE_URL}/createTask",
            json={
                "clientKey": self.api_key,
                "task": task,
            }
        )
        
        create_data = create_response.json()
        
        if create_data.get("errorId"):
            raise CaptchaSolveError(
                f"Anti-Captcha error: {create_data.get('errorDescription')}"
            )
        
        task_id = create_data["taskId"]
        
        # Poll
        for _ in range(36):
            await asyncio.sleep(5)
            
            result_response = await self.client.post(
                f"{self.BASE_URL}/getTaskResult",
                json={
                    "clientKey": self.api_key,
                    "taskId": task_id,
                }
            )
            
            result_data = result_response.json()
            
            if result_data.get("status") == "ready":
                return result_data["solution"]["gRecaptchaResponse"]
            
            if result_data.get("errorId"):
                raise CaptchaSolveError(
                    f"Anti-Captcha error: {result_data.get('errorDescription')}"
                )
        
        raise CaptchaSolveError("Anti-Captcha timeout")
```

---

## CAPTCHA Solver Chain

```python
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List
import asyncio
import time

class CaptchaType(Enum):
    RECAPTCHA_V2 = "recaptcha_v2"
    RECAPTCHA_V3 = "recaptcha_v3"
    HCAPTCHA = "hcaptcha"
    TURNSTILE = "turnstile"

@dataclass
class CaptchaChallenge:
    """CAPTCHA challenge bilgisi"""
    captcha_type: CaptchaType
    site_key: str
    page_url: str
    action: Optional[str] = None
    min_score: Optional[float] = None
    invisible: bool = False
    proxy: Optional[Dict] = None

@dataclass
class SolveResult:
    """Çözüm sonucu"""
    success: bool
    token: Optional[str] = None
    provider: Optional[str] = None
    solve_time_ms: int = 0
    error: Optional[str] = None

class CaptchaSolverChain:
    """Multi-provider CAPTCHA solver with failover"""
    
    def __init__(self, config: Dict[str, str]):
        self.providers = []
        
        # Initialize providers in priority order
        if config.get("capsolver_api_key"):
            self.providers.append(
                ("capsolver", CapSolverProvider(config["capsolver_api_key"]))
            )
        
        if config.get("2captcha_api_key"):
            self.providers.append(
                ("2captcha", TwoCaptchaProvider(config["2captcha_api_key"]))
            )
        
        if config.get("anticaptcha_api_key"):
            self.providers.append(
                ("anticaptcha", AntiCaptchaProvider(config["anticaptcha_api_key"]))
            )
        
        # Provider health tracking
        self.provider_stats: Dict[str, Dict] = {
            name: {
                "success": 0,
                "failure": 0,
                "total_time_ms": 0,
                "avg_time_ms": 0,
                "consecutive_failures": 0,
                "disabled_until": None,
            }
            for name, _ in self.providers
        }
    
    async def solve(
        self,
        challenge: CaptchaChallenge,
        timeout: int = 120,
    ) -> SolveResult:
        """CAPTCHA'yı çöz (failover ile)"""
        
        start_time = time.time()
        errors = []
        
        for provider_name, provider in self._get_active_providers():
            try:
                # Provider timeout = total / remaining providers
                remaining_time = timeout - (time.time() - start_time)
                if remaining_time <= 0:
                    break
                
                provider_timeout = min(remaining_time, 60)  # Max 60s per provider
                
                token = await asyncio.wait_for(
                    self._solve_with_provider(provider, challenge),
                    timeout=provider_timeout,
                )
                
                # Success
                solve_time = int((time.time() - start_time) * 1000)
                self._record_success(provider_name, solve_time)
                
                return SolveResult(
                    success=True,
                    token=token,
                    provider=provider_name,
                    solve_time_ms=solve_time,
                )
                
            except asyncio.TimeoutError:
                errors.append(f"{provider_name}: timeout")
                self._record_failure(provider_name)
                
            except CaptchaSolveError as e:
                errors.append(f"{provider_name}: {str(e)}")
                self._record_failure(provider_name)
                
            except Exception as e:
                errors.append(f"{provider_name}: unexpected error - {str(e)}")
                self._record_failure(provider_name)
        
        # All providers failed
        return SolveResult(
            success=False,
            error=f"All providers failed: {'; '.join(errors)}",
            solve_time_ms=int((time.time() - start_time) * 1000),
        )
    
    async def _solve_with_provider(
        self,
        provider,
        challenge: CaptchaChallenge,
    ) -> str:
        """Provider ile çöz"""
        
        if challenge.captcha_type == CaptchaType.RECAPTCHA_V2:
            return await provider.solve_recaptcha_v2(
                site_key=challenge.site_key,
                page_url=challenge.page_url,
                proxy=challenge.proxy,
                invisible=challenge.invisible,
            )
        
        elif challenge.captcha_type == CaptchaType.RECAPTCHA_V3:
            return await provider.solve_recaptcha_v3(
                site_key=challenge.site_key,
                page_url=challenge.page_url,
                action=challenge.action or "verify",
                min_score=challenge.min_score or 0.7,
                proxy=challenge.proxy,
            )
        
        elif challenge.captcha_type == CaptchaType.TURNSTILE:
            return await provider.solve_turnstile(
                site_key=challenge.site_key,
                page_url=challenge.page_url,
                proxy=challenge.proxy,
            )
        
        elif challenge.captcha_type == CaptchaType.HCAPTCHA:
            return await provider.solve_hcaptcha(
                site_key=challenge.site_key,
                page_url=challenge.page_url,
                proxy=challenge.proxy,
            )
        
        raise ValueError(f"Unsupported CAPTCHA type: {challenge.captcha_type}")
    
    def _get_active_providers(self):
        """Aktif provider'ları döndür (health-based sorting)"""
        
        now = time.time()
        active = []
        
        for name, provider in self.providers:
            stats = self.provider_stats[name]
            
            # Disabled check
            if stats["disabled_until"] and stats["disabled_until"] > now:
                continue
            
            active.append((name, provider, stats))
        
        # Sort by success rate and average time
        def score(item):
            name, provider, stats = item
            total = stats["success"] + stats["failure"]
            if total == 0:
                return 0.5  # Unknown, neutral score
            
            success_rate = stats["success"] / total
            time_penalty = min(stats["avg_time_ms"] / 60000, 0.3)  # Max 30% penalty
            
            return success_rate - time_penalty
        
        active.sort(key=score, reverse=True)
        
        return [(name, provider) for name, provider, _ in active]
    
    def _record_success(self, provider_name: str, solve_time_ms: int):
        """Başarı kaydet"""
        stats = self.provider_stats[provider_name]
        
        stats["success"] += 1
        stats["consecutive_failures"] = 0
        stats["total_time_ms"] += solve_time_ms
        
        total = stats["success"] + stats["failure"]
        stats["avg_time_ms"] = stats["total_time_ms"] / total
        
        # Clear disable
        stats["disabled_until"] = None
    
    def _record_failure(self, provider_name: str):
        """Başarısızlık kaydet"""
        stats = self.provider_stats[provider_name]
        
        stats["failure"] += 1
        stats["consecutive_failures"] += 1
        
        # 5 ardışık hata: 5 dakika disable
        if stats["consecutive_failures"] >= 5:
            stats["disabled_until"] = time.time() + 300
    
    async def get_balances(self) -> Dict[str, float]:
        """Tüm provider bakiyeleri"""
        balances = {}
        
        for name, provider in self.providers:
            try:
                balances[name] = await provider.get_balance()
            except:
                balances[name] = -1  # Error
        
        return balances
```

---

## CAPTCHA Detection

```python
class CaptchaDetector:
    """Sayfada CAPTCHA tespit et"""
    
    # Site key patterns
    RECAPTCHA_PATTERNS = [
        r'data-sitekey="([^"]+)"',
        r"grecaptcha.render\([^,]+,\s*{[^}]*sitekey:\s*['\"]([^'\"]+)['\"]",
        r'src="https://www.google.com/recaptcha/[^"]*\?.*?k=([^"&]+)',
    ]
    
    HCAPTCHA_PATTERNS = [
        r'data-sitekey="([^"]+)".*?class="h-captcha"',
        r'class="h-captcha".*?data-sitekey="([^"]+)"',
    ]
    
    TURNSTILE_PATTERNS = [
        r'data-sitekey="([^"]+)".*?class="cf-turnstile"',
        r'class="cf-turnstile".*?data-sitekey="([^"]+)"',
        r'turnstile.render\([^,]+,\s*{[^}]*sitekey:\s*["\']([^"\']+)',
    ]
    
    async def detect(self, page) -> Optional[CaptchaChallenge]:
        """Sayfada CAPTCHA tespit et"""
        
        content = await page.content()
        url = page.url
        
        # Check Turnstile first (Cloudflare)
        for pattern in self.TURNSTILE_PATTERNS:
            match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
            if match:
                return CaptchaChallenge(
                    captcha_type=CaptchaType.TURNSTILE,
                    site_key=match.group(1),
                    page_url=url,
                )
        
        # Check reCAPTCHA
        for pattern in self.RECAPTCHA_PATTERNS:
            match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
            if match:
                # v2 vs v3 detection
                captcha_type = CaptchaType.RECAPTCHA_V2
                if "recaptcha/api.js?render=" in content or "grecaptcha.execute" in content:
                    captcha_type = CaptchaType.RECAPTCHA_V3
                
                invisible = "invisible" in content.lower() or "size: 'invisible'" in content
                
                return CaptchaChallenge(
                    captcha_type=captcha_type,
                    site_key=match.group(1),
                    page_url=url,
                    invisible=invisible,
                )
        
        # Check hCaptcha
        for pattern in self.HCAPTCHA_PATTERNS:
            match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
            if match:
                return CaptchaChallenge(
                    captcha_type=CaptchaType.HCAPTCHA,
                    site_key=match.group(1),
                    page_url=url,
                )
        
        return None
    
    async def is_challenge_page(self, page) -> bool:
        """Sayfa challenge sayfası mı?"""
        
        content = await page.content()
        
        indicators = [
            "challenge-running",
            "cf-browser-verification",
            "cf-turnstile",
            "g-recaptcha",
            "h-captcha",
            "Please verify you are a human",
            "complete the security check",
        ]
        
        return any(indicator.lower() in content.lower() for indicator in indicators)
```

---

## Token Injection

```python
class CaptchaTokenInjector:
    """Çözülen token'ı sayfaya enjekte et"""
    
    async def inject_recaptcha_token(self, page, token: str):
        """reCAPTCHA token'ı enjekte et"""
        
        await page.evaluate(f"""
            // Find the response textarea
            const responseField = document.querySelector('[name="g-recaptcha-response"]') 
                || document.getElementById('g-recaptcha-response');
            
            if (responseField) {{
                responseField.value = '{token}';
                responseField.style.display = 'none';
            }}
            
            // Also set in potential hidden inputs
            document.querySelectorAll('textarea[name="g-recaptcha-response"]').forEach(el => {{
                el.value = '{token}';
            }});
            
            // Trigger callback if exists
            if (typeof window.grecaptchaCallback === 'function') {{
                window.grecaptchaCallback('{token}');
            }}
            
            // Check for explicit callback name
            const callbackMatch = document.body.innerHTML.match(/data-callback="([^"]+)"/);
            if (callbackMatch && typeof window[callbackMatch[1]] === 'function') {{
                window[callbackMatch[1]]('{token}');
            }}
        """)
    
    async def inject_turnstile_token(self, page, token: str):
        """Cloudflare Turnstile token'ı enjekte et"""
        
        await page.evaluate(f"""
            // Find turnstile response field
            const responseField = document.querySelector('[name="cf-turnstile-response"]')
                || document.querySelector('input[name*="turnstile"]');
            
            if (responseField) {{
                responseField.value = '{token}';
            }}
            
            // Trigger turnstile callback
            if (window.turnstile && window.turnstile.callback) {{
                window.turnstile.callback('{token}');
            }}
        """)
    
    async def inject_hcaptcha_token(self, page, token: str):
        """hCaptcha token'ı enjekte et"""
        
        await page.evaluate(f"""
            const responseField = document.querySelector('[name="h-captcha-response"]')
                || document.querySelector('[name="g-recaptcha-response"]');
            
            if (responseField) {{
                responseField.value = '{token}';
            }}
            
            // Trigger callback
            if (typeof hcaptcha !== 'undefined') {{
                // Find widget id and trigger
                const widgetId = hcaptcha.getWidgetID();
                if (widgetId !== null) {{
                    hcaptcha.execute(widgetId);
                }}
            }}
        """)
```

---

## Site-Specific Handlers

### VFS CAPTCHA Handler

```python
class VFSCaptchaHandler:
    """VFS Global CAPTCHA handling"""
    
    # VFS site keys (ülkeye göre farklı olabilir)
    SITE_KEYS = {
        "default": "6LcxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxAA",  # Placeholder
    }
    
    def __init__(self, solver_chain: CaptchaSolverChain, detector: CaptchaDetector):
        self.solver = solver_chain
        self.detector = detector
        self.injector = CaptchaTokenInjector()
    
    async def handle_captcha(
        self,
        page,
        proxy: Optional[Dict] = None,
        max_attempts: int = 3,
    ) -> bool:
        """VFS CAPTCHA'yı handle et"""
        
        for attempt in range(max_attempts):
            # Detect CAPTCHA type
            challenge = await self.detector.detect(page)
            
            if not challenge:
                # No CAPTCHA found
                return True
            
            # Add proxy to challenge
            challenge.proxy = proxy
            
            # Solve
            result = await self.solver.solve(challenge, timeout=90)
            
            if not result.success:
                if attempt < max_attempts - 1:
                    await asyncio.sleep(2)
                    continue
                return False
            
            # Inject token
            if challenge.captcha_type == CaptchaType.TURNSTILE:
                await self.injector.inject_turnstile_token(page, result.token)
            elif challenge.captcha_type in [CaptchaType.RECAPTCHA_V2, CaptchaType.RECAPTCHA_V3]:
                await self.injector.inject_recaptcha_token(page, result.token)
            
            # Wait for challenge to clear
            await asyncio.sleep(2)
            
            # Verify challenge cleared
            if not await self.detector.is_challenge_page(page):
                return True
            
            # VFS might need form submit after injection
            submit_button = await page.query_selector('button[type="submit"], input[type="submit"]')
            if submit_button:
                await submit_button.click()
                await asyncio.sleep(3)
            
            if not await self.detector.is_challenge_page(page):
                return True
        
        return False
```

---

## Cost Tracking

```python
class CaptchaCostTracker:
    """CAPTCHA çözüm maliyetini takip et"""
    
    # Fiyatlandırma ($ per 1000 solves)
    PRICING = {
        "capsolver": {
            "recaptcha_v2": 1.0,
            "recaptcha_v3": 1.2,
            "turnstile": 1.0,
            "hcaptcha": 0.8,
        },
        "2captcha": {
            "recaptcha_v2": 2.99,
            "recaptcha_v3": 2.99,
            "turnstile": 2.99,
            "hcaptcha": 2.99,
        },
        "anticaptcha": {
            "recaptcha_v2": 2.0,
            "recaptcha_v3": 2.5,
            "turnstile": 2.0,
            "hcaptcha": 2.0,
        },
    }
    
    def __init__(self):
        self.usage: Dict[str, Dict[str, int]] = {}  # provider -> type -> count
    
    def track(self, provider: str, captcha_type: str):
        """Kullanımı kaydet"""
        if provider not in self.usage:
            self.usage[provider] = {}
        
        if captcha_type not in self.usage[provider]:
            self.usage[provider][captcha_type] = 0
        
        self.usage[provider][captcha_type] += 1
    
    def get_estimated_cost(self) -> float:
        """Tahmini maliyet"""
        total = 0.0
        
        for provider, types in self.usage.items():
            for captcha_type, count in types.items():
                price_per_1000 = self.PRICING.get(provider, {}).get(captcha_type, 3.0)
                total += (count / 1000) * price_per_1000
        
        return total
    
    def get_report(self) -> Dict:
        """Detaylı rapor"""
        return {
            "usage": self.usage,
            "estimated_cost_usd": self.get_estimated_cost(),
            "by_provider": {
                provider: sum(types.values())
                for provider, types in self.usage.items()
            },
        }
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | reCAPTCHA v2 %95+ çözüm başarısı | 100 challenge test |
| AC-002 | Turnstile %90+ çözüm başarısı | 50 challenge test |
| AC-003 | Provider failover 30 saniye içinde | Failover test |
| AC-004 | Token injection doğru çalışmalı | Integration test |
| AC-005 | Maliyet tracking doğru hesaplamalı | Unit test |
| AC-006 | VFS CAPTCHA loop handle edilmeli | E2E test |
| AC-007 | Balance uyarısı <$5'da tetiklenmeli | Threshold test |

---

## Edge Cases

### CAPTCHA Loop Detection

```
Trigger: 3+ ardışık CAPTCHA aynı sayfada
Action:
  1. Log "CAPTCHA loop detected"
  2. Browser profile rotate
  3. Proxy rotate
  4. 5 dakika cooldown
  5. Farklı provider dene
```

### Invalid Token

```
Trigger: Token enjekte edildi ama sayfa hala challenge gösteriyor
Action:
  1. Report bad token (refund)
  2. Fresh proxy ile yeni token al
  3. Max 3 deneme, sonra fail
```

### Provider Balance Low

```
Trigger: Herhangi bir provider < $5
Action:
  1. Telegram alert
  2. Provider'ı low-priority'ye al
  3. Diğer provider'ları tercih et
```

---

## Güvenlik Notları

1. **API Key Storage:** Directus'ta encrypted, runtime'da memory-only
2. **No Token Caching:** Token'lar cache'lenmez (kısa ömürlü)
3. **Provider Isolation:** Her provider ayrı client instance
4. **Rate Limiting:** Provider rate limit'lerine uyum
5. **Audit Trail:** Her çözüm logged (provider, time, success)

---

## Sonraki Adımlar

1. **007-ACCOUNT-POOL-MANAGER.md** - Bot hesap yönetimi
2. Provider hesapları oluştur ve test et
3. VFS/iDATA integration test
