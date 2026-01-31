# 004 - Stealth Browser Engine Specification

## Amaç

VFS Global'in Cloudflare Enterprise korumasını, iDATA'nın session kontrollerini ve diğer anti-bot sistemlerini bypass edebilen, %95+ başarı oranlı stealth browser engine. Camoufox primary, Patchright secondary, SeleniumBase emergency fallback.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 001-ARCHITECTURE-OVERVIEW | Browser automation layer |
| 005-PROXY-MANAGER | Her session'a proxy assignment |
| 006-CAPTCHA-SOLVER | Challenge detection ve çözüm |
| 007-ACCOUNT-POOL-MANAGER | Hesap credential injection |
| 012-AI-DECISION-ENGINE | Strategy switching kararları |

---

## Browser Engine Hierarchy

```
┌─────────────────────────────────────────────────────────────┐
│                    STEALTH ENGINE                            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   TIER 1 (Primary)          TIER 2 (Secondary)              │
│   ┌──────────────┐          ┌──────────────┐                │
│   │   Camoufox   │          │  Patchright  │                │
│   │              │   ───►   │              │                │
│   │  %95+ VFS    │  fail    │  %85+ VFS    │                │
│   └──────────────┘          └──────────────┘                │
│          │                         │                         │
│          │ fail                    │ fail                    │
│          ▼                         ▼                         │
│   TIER 3 (Emergency)                                         │
│   ┌──────────────────────────────────────┐                  │
│   │         SeleniumBase UC Mode          │                  │
│   │              %70+ VFS                 │                  │
│   └──────────────────────────────────────┘                  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Camoufox Configuration (Primary)

### Neden Camoufox?

| Özellik | Camoufox | Standart Playwright |
|---------|----------|---------------------|
| Fingerprint injection | C++ native | JavaScript hooks |
| Hook detection riski | İmkansız | Tespit edilebilir |
| TLS fingerprint | Authentic Firefox | Automation signature |
| WebGL spoofing | Hardware-level | Software emulation |
| Canvas noise | Native | Detectable patterns |

### Installation

```bash
# Camoufox + Playwright
pip install camoufox[geoip]
playwright install firefox
```

### Base Configuration

```python
from camoufox.sync_api import Camoufox

class StealthBrowserConfig:
    """Camoufox temel konfigürasyonu"""
    
    DEFAULT_CONFIG = {
        # Geolocation (Türkiye)
        "geoip": True,
        "locale": "tr-TR",
        "timezone": "Europe/Istanbul",
        
        # Screen & Viewport (realistic desktop)
        "screen": {
            "width": 1920,
            "height": 1080,
            "availWidth": 1920,
            "availHeight": 1040,
            "colorDepth": 24,
            "pixelDepth": 24,
        },
        "viewport": {
            "width": 1366,  # Common laptop resolution
            "height": 768,
        },
        
        # OS Fingerprint
        "os": "Windows",  # veya "MacOS", "Linux"
        
        # Humanize
        "humanize": True,
        
        # Headless (production'da True)
        "headless": True,
        
        # Block unnecessary resources
        "block_images": False,  # VFS image CAPTCHA için gerekli
        "block_webrtc": True,
        
        # Addons (uBlock Origin vb.)
        "addons": [],
    }
```

### Browser Profile Generation

```python
import random
from typing import Dict, Any

class BrowserProfileGenerator:
    """Unique fingerprint profilleri oluşturur"""
    
    VIEWPORTS = [
        (1920, 1080), (1366, 768), (1536, 864),
        (1440, 900), (1280, 720), (1600, 900),
    ]
    
    USER_AGENTS_WINDOWS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    ]
    
    WEBGL_VENDORS = [
        ("Intel Inc.", "Intel(R) UHD Graphics 620"),
        ("Intel Inc.", "Intel(R) Iris(R) Xe Graphics"),
        ("NVIDIA Corporation", "NVIDIA GeForce GTX 1650"),
        ("NVIDIA Corporation", "NVIDIA GeForce RTX 3060"),
        ("AMD", "AMD Radeon RX 580"),
    ]
    
    FONTS = [
        ["Arial", "Times New Roman", "Verdana", "Georgia", "Courier New"],
        ["Segoe UI", "Tahoma", "Calibri", "Cambria", "Consolas"],
    ]
    
    def generate(self, profile_id: str = None) -> Dict[str, Any]:
        """Unique browser profile oluştur"""
        
        viewport = random.choice(self.VIEWPORTS)
        webgl = random.choice(self.WEBGL_VENDORS)
        
        return {
            "profile_id": profile_id or self._generate_id(),
            "viewport": {
                "width": viewport[0],
                "height": viewport[1],
            },
            "screen": {
                "width": viewport[0],
                "height": viewport[1],
                "availWidth": viewport[0],
                "availHeight": viewport[1] - 40,  # Taskbar
            },
            "user_agent": random.choice(self.USER_AGENTS_WINDOWS),
            "webgl": {
                "vendor": webgl[0],
                "renderer": webgl[1],
            },
            "canvas_noise": random.uniform(0.0001, 0.001),
            "audio_noise": random.uniform(0.0001, 0.0005),
            "fonts": random.choice(self.FONTS),
            "timezone_offset": 180,  # UTC+3 (Istanbul)
            "languages": ["tr-TR", "tr", "en-US", "en"],
            "plugins": self._generate_plugins(),
            "created_at": datetime.utcnow().isoformat(),
        }
    
    def _generate_plugins(self) -> list:
        """Realistic plugin list"""
        base_plugins = [
            {"name": "PDF Viewer", "filename": "internal-pdf-viewer"},
            {"name": "Chrome PDF Viewer", "filename": "mhjfbmdgcfjbbpaeojofohoefgiehjai"},
        ]
        return base_plugins
    
    def _generate_id(self) -> str:
        return f"profile_{uuid.uuid4().hex[:12]}"
```

### Session Launcher

```python
from camoufox.sync_api import Camoufox
from typing import Optional
import asyncio

class StealthSessionLauncher:
    """Stealth browser session başlatıcı"""
    
    def __init__(
        self,
        profile: Dict[str, Any],
        proxy: Optional[Dict[str, str]] = None,
    ):
        self.profile = profile
        self.proxy = proxy
        self.browser = None
        self.context = None
        self.page = None
    
    async def launch(self) -> "StealthSessionLauncher":
        """Browser session başlat"""
        
        # Camoufox config
        config = {
            **StealthBrowserConfig.DEFAULT_CONFIG,
            "viewport": self.profile["viewport"],
            "screen": self.profile["screen"],
        }
        
        # Proxy configuration
        if self.proxy:
            config["proxy"] = {
                "server": f"{self.proxy['protocol']}://{self.proxy['host']}:{self.proxy['port']}",
                "username": self.proxy.get("username"),
                "password": self.proxy.get("password"),
            }
        
        # Launch browser
        self.browser = await Camoufox(config).start()
        
        # Create context with fingerprint
        self.context = await self.browser.new_context(
            user_agent=self.profile["user_agent"],
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
            geolocation={"latitude": 41.0082, "longitude": 28.9784},  # Istanbul
            permissions=["geolocation"],
        )
        
        # Inject additional fingerprint spoofing
        await self._inject_fingerprint_scripts()
        
        # Create page
        self.page = await self.context.new_page()
        
        # Set default timeouts
        self.page.set_default_timeout(30000)
        self.page.set_default_navigation_timeout(60000)
        
        return self
    
    async def _inject_fingerprint_scripts(self):
        """Additional fingerprint spoofing scripts"""
        
        # WebGL spoofing
        await self.context.add_init_script(f"""
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {{
                if (parameter === 37445) return '{self.profile["webgl"]["vendor"]}';
                if (parameter === 37446) return '{self.profile["webgl"]["renderer"]}';
                return getParameter.call(this, parameter);
            }};
        """)
        
        # Canvas noise
        await self.context.add_init_script(f"""
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(type) {{
                if (type === 'image/png') {{
                    const context = this.getContext('2d');
                    const imageData = context.getImageData(0, 0, this.width, this.height);
                    for (let i = 0; i < imageData.data.length; i += 4) {{
                        imageData.data[i] += Math.floor(Math.random() * {self.profile["canvas_noise"]} * 255);
                    }}
                    context.putImageData(imageData, 0, 0);
                }}
                return originalToDataURL.apply(this, arguments);
            }};
        """)
        
        # Navigator properties
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['tr-TR', 'tr', 'en-US', 'en'] });
            
            // Chrome detection bypass
            window.chrome = { runtime: {} };
            
            // Permissions API
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)
    
    async def close(self):
        """Session kapat"""
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
```

---

## Human Behavior Simulation

### Mouse Movement

```python
import math
import random
from typing import Tuple, List

class HumanMouseSimulator:
    """İnsan benzeri mouse hareketi simülasyonu"""
    
    # İnsan ortalaması: ~427 pixel/saniye
    # Bot ortalaması: ~1520 pixel/saniye
    HUMAN_SPEED_MIN = 300  # pixel/saniye
    HUMAN_SPEED_MAX = 600  # pixel/saniye
    
    def __init__(self, page):
        self.page = page
        self.current_pos = (0, 0)
    
    async def move_to(self, target: Tuple[int, int], click: bool = False):
        """Bezier curve ile hedefe git"""
        
        path = self._generate_bezier_path(self.current_pos, target)
        
        for point in path:
            # Random delay between movements (human-like)
            delay = random.uniform(0.005, 0.02)  # 5-20ms
            await asyncio.sleep(delay)
            
            await self.page.mouse.move(point[0], point[1])
        
        self.current_pos = target
        
        if click:
            # Pre-click hesitation
            await asyncio.sleep(random.uniform(0.05, 0.15))
            await self.page.mouse.click(target[0], target[1])
            # Post-click pause
            await asyncio.sleep(random.uniform(0.1, 0.3))
    
    def _generate_bezier_path(
        self, 
        start: Tuple[int, int], 
        end: Tuple[int, int],
        control_points: int = 2,
    ) -> List[Tuple[int, int]]:
        """Bezier curve ile path oluştur"""
        
        distance = math.sqrt((end[0] - start[0])**2 + (end[1] - start[1])**2)
        
        # Control points (overshoot ve indirect path için)
        controls = [start]
        for _ in range(control_points):
            # Random offset from direct line
            offset_x = random.gauss(0, distance * 0.1)
            offset_y = random.gauss(0, distance * 0.1)
            
            mid_x = (start[0] + end[0]) / 2 + offset_x
            mid_y = (start[1] + end[1]) / 2 + offset_y
            controls.append((mid_x, mid_y))
        controls.append(end)
        
        # Generate path points
        speed = random.uniform(self.HUMAN_SPEED_MIN, self.HUMAN_SPEED_MAX)
        duration = distance / speed
        steps = max(int(duration * 60), 10)  # 60 FPS hedef
        
        path = []
        for i in range(steps + 1):
            t = i / steps
            point = self._bezier_point(controls, t)
            path.append(point)
        
        # Add micro-corrections at the end
        if random.random() < 0.3:  # %30 ihtimalle
            path.extend(self._add_micro_corrections(end))
        
        return path
    
    def _bezier_point(
        self, 
        controls: List[Tuple[float, float]], 
        t: float
    ) -> Tuple[int, int]:
        """Bezier curve üzerinde nokta hesapla"""
        n = len(controls) - 1
        x, y = 0, 0
        
        for i, (cx, cy) in enumerate(controls):
            coeff = self._binomial(n, i) * (1 - t)**(n - i) * t**i
            x += coeff * cx
            y += coeff * cy
        
        return (int(x), int(y))
    
    def _binomial(self, n: int, k: int) -> int:
        """Binomial katsayı"""
        return math.factorial(n) // (math.factorial(k) * math.factorial(n - k))
    
    def _add_micro_corrections(
        self, 
        target: Tuple[int, int]
    ) -> List[Tuple[int, int]]:
        """Hedefe yaklaşırken micro-correction ekle"""
        corrections = []
        for _ in range(random.randint(1, 3)):
            offset_x = random.gauss(0, 2)
            offset_y = random.gauss(0, 2)
            corrections.append((
                int(target[0] + offset_x),
                int(target[1] + offset_y)
            ))
        corrections.append(target)  # Final position
        return corrections
```

### Typing Simulation

```python
class HumanTypingSimulator:
    """İnsan benzeri klavye girişi simülasyonu"""
    
    # Inter-keystroke interval (IKI) - log-normal dağılım
    IKI_MEAN = 0.12  # 120ms ortalama
    IKI_STD = 0.04   # 40ms std
    
    # Familiar bigrams - daha hızlı yazılır
    FAST_BIGRAMS = ["th", "he", "in", "er", "an", "re", "on", "at", "en", "nd"]
    
    # Typo probability
    TYPO_RATE = 0.02  # %2
    
    def __init__(self, page):
        self.page = page
    
    async def type_text(self, selector: str, text: str):
        """İnsan benzeri text girişi"""
        
        # Focus element
        await self.page.click(selector)
        await asyncio.sleep(random.uniform(0.1, 0.3))
        
        i = 0
        while i < len(text):
            char = text[i]
            
            # Calculate delay
            delay = self._get_keystroke_delay(text, i)
            await asyncio.sleep(delay)
            
            # Random typo
            if random.random() < self.TYPO_RATE and char.isalpha():
                # Make typo
                wrong_char = self._get_nearby_key(char)
                await self.page.keyboard.type(wrong_char)
                await asyncio.sleep(random.uniform(0.2, 0.5))  # Notice mistake
                await self.page.keyboard.press("Backspace")
                await asyncio.sleep(random.uniform(0.1, 0.2))
            
            # Type correct character
            await self.page.keyboard.type(char)
            i += 1
        
        # Post-typing pause
        await asyncio.sleep(random.uniform(0.2, 0.5))
    
    def _get_keystroke_delay(self, text: str, index: int) -> float:
        """Context-aware keystroke delay"""
        
        delay = random.gauss(self.IKI_MEAN, self.IKI_STD)
        delay = max(0.05, delay)  # Minimum 50ms
        
        # Bigram hızlandırma
        if index > 0:
            bigram = text[index-1:index+1].lower()
            if bigram in self.FAST_BIGRAMS:
                delay *= 0.7  # %30 daha hızlı
        
        # Kelime başı yavaşlama
        if index > 0 and text[index-1] == " ":
            delay *= 1.3  # %30 daha yavaş
        
        # Shift key için ek delay
        if text[index].isupper():
            delay += random.uniform(0.02, 0.05)
        
        return delay
    
    def _get_nearby_key(self, char: str) -> str:
        """Klavyede yakın tuş (typo için)"""
        keyboard_layout = {
            'q': ['w', 'a'], 'w': ['q', 'e', 's'], 'e': ['w', 'r', 'd'],
            'r': ['e', 't', 'f'], 't': ['r', 'y', 'g'], 'y': ['t', 'u', 'h'],
            'u': ['y', 'i', 'j'], 'i': ['u', 'o', 'k'], 'o': ['i', 'p', 'l'],
            'p': ['o', 'l'], 'a': ['q', 's', 'z'], 's': ['a', 'w', 'd', 'x'],
            'd': ['s', 'e', 'f', 'c'], 'f': ['d', 'r', 'g', 'v'],
            'g': ['f', 't', 'h', 'b'], 'h': ['g', 'y', 'j', 'n'],
            'j': ['h', 'u', 'k', 'm'], 'k': ['j', 'i', 'l'],
            'l': ['k', 'o', 'p'], 'z': ['a', 's', 'x'],
            'x': ['z', 's', 'd', 'c'], 'c': ['x', 'd', 'f', 'v'],
            'v': ['c', 'f', 'g', 'b'], 'b': ['v', 'g', 'h', 'n'],
            'n': ['b', 'h', 'j', 'm'], 'm': ['n', 'j', 'k'],
        }
        
        lower_char = char.lower()
        if lower_char in keyboard_layout:
            wrong = random.choice(keyboard_layout[lower_char])
            return wrong.upper() if char.isupper() else wrong
        return char
```

### Scroll Behavior

```python
class HumanScrollSimulator:
    """İnsan benzeri scroll davranışı"""
    
    def __init__(self, page):
        self.page = page
    
    async def scroll_to_element(self, selector: str):
        """Element'e smooth scroll"""
        
        element = await self.page.query_selector(selector)
        if not element:
            return
        
        # Get element position
        box = await element.bounding_box()
        if not box:
            return
        
        # Current scroll position
        current_scroll = await self.page.evaluate("window.scrollY")
        target_scroll = box["y"] - 200  # 200px yukarıda bırak
        
        # Scroll in chunks
        distance = target_scroll - current_scroll
        steps = max(abs(int(distance / 100)), 5)
        
        for i in range(steps):
            progress = (i + 1) / steps
            # Ease-out curve
            eased_progress = 1 - (1 - progress) ** 3
            
            scroll_to = current_scroll + (distance * eased_progress)
            await self.page.evaluate(f"window.scrollTo(0, {scroll_to})")
            
            await asyncio.sleep(random.uniform(0.02, 0.05))
        
        # Small random overshoot and correction
        if random.random() < 0.4:
            overshoot = random.uniform(20, 50)
            await self.page.evaluate(f"window.scrollBy(0, {overshoot})")
            await asyncio.sleep(random.uniform(0.1, 0.2))
            await self.page.evaluate(f"window.scrollBy(0, {-overshoot})")
    
    async def random_scroll(self):
        """Sayfa okuma simülasyonu için random scroll"""
        
        viewport_height = await self.page.evaluate("window.innerHeight")
        
        # Random scroll amount
        scroll_amount = random.randint(
            int(viewport_height * 0.3),
            int(viewport_height * 0.7)
        )
        
        if random.random() < 0.5:
            scroll_amount = -scroll_amount  # Yukarı scroll
        
        await self.page.evaluate(f"window.scrollBy(0, {scroll_amount})")
        await asyncio.sleep(random.uniform(0.5, 2.0))
```

---

## Patchright Configuration (Secondary)

```python
# Patchright - Playwright undetected fork
from patchright.async_api import async_playwright

class PatchrightLauncher:
    """Patchright fallback launcher"""
    
    async def launch(self, profile: Dict, proxy: Dict = None):
        self.playwright = await async_playwright().start()
        
        browser_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ]
        
        if proxy:
            browser_args.append(f"--proxy-server={proxy['host']}:{proxy['port']}")
        
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=browser_args,
        )
        
        self.context = await self.browser.new_context(
            viewport=profile["viewport"],
            user_agent=profile["user_agent"],
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
        )
        
        # Stealth scripts
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            delete navigator.__proto__.webdriver;
        """)
        
        self.page = await self.context.new_page()
        return self
```

---

## SeleniumBase UC Mode (Emergency)

```python
from seleniumbase import SB

class SeleniumBaseUCLauncher:
    """SeleniumBase Undetected Chrome - emergency fallback"""
    
    def launch(self, profile: Dict, proxy: Dict = None):
        proxy_string = None
        if proxy:
            proxy_string = f"{proxy['host']}:{proxy['port']}"
            if proxy.get('username'):
                proxy_string = f"{proxy['username']}:{proxy['password']}@{proxy_string}"
        
        self.sb = SB(
            uc=True,  # Undetected Chrome mode
            headless=True,
            proxy=proxy_string,
            locale_code="tr-TR",
        )
        
        self.sb.__enter__()
        return self
    
    def close(self):
        self.sb.__exit__(None, None, None)
```

---

## Detection Evasion Tests

### Cloudflare Check

```python
class CloudflareDetector:
    """Cloudflare challenge detection"""
    
    CHALLENGE_INDICATORS = [
        "challenge-running",
        "cf-browser-verification",
        "cf-turnstile",
        "Just a moment...",
        "Checking your browser",
        "Please wait...",
        "ray ID",
    ]
    
    async def is_challenge_page(self, page) -> bool:
        """Cloudflare challenge sayfası mı?"""
        
        content = await page.content()
        url = page.url
        
        # URL check
        if "__cf_chl" in url or "challenge" in url:
            return True
        
        # Content check
        for indicator in self.CHALLENGE_INDICATORS:
            if indicator.lower() in content.lower():
                return True
        
        return False
    
    async def wait_for_challenge(self, page, timeout: int = 30):
        """Challenge çözülmesini bekle"""
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if not await self.is_challenge_page(page):
                return True
            await asyncio.sleep(1)
        
        return False
```

### Bot Detection Test

```python
class BotDetectionTester:
    """Browser'ın tespit edilip edilmediğini kontrol et"""
    
    TEST_SITES = [
        "https://bot.sannysoft.com/",
        "https://nowsecure.nl/",
        "https://abrahamjuliot.github.io/creepjs/",
        "https://browserleaks.com/canvas",
    ]
    
    async def run_detection_test(self, page) -> Dict[str, bool]:
        """Tüm detection testlerini çalıştır"""
        
        results = {}
        
        for site in self.TEST_SITES:
            try:
                await page.goto(site, wait_until="networkidle")
                await asyncio.sleep(3)
                
                # Site-specific checks
                if "sannysoft" in site:
                    results["sannysoft"] = await self._check_sannysoft(page)
                elif "nowsecure" in site:
                    results["nowsecure"] = await self._check_nowsecure(page)
                    
            except Exception as e:
                results[site] = f"Error: {str(e)}"
        
        return results
    
    async def _check_sannysoft(self, page) -> bool:
        """Sannysoft bot test sonucu"""
        # "FAIL" kelimesi sayfada görünmemeli
        content = await page.content()
        return "FAIL" not in content
    
    async def _check_nowsecure(self, page) -> bool:
        """Nowsecure.nl test sonucu"""
        # Yeşil checkmark görünmeli
        element = await page.query_selector(".success")
        return element is not None
```

---

## Session Management

### Session Pool

```python
class BrowserSessionPool:
    """Pre-warmed browser session havuzu"""
    
    def __init__(self, max_sessions: int = 20):
        self.max_sessions = max_sessions
        self.available_sessions: asyncio.Queue = asyncio.Queue()
        self.active_sessions: Dict[str, StealthSessionLauncher] = {}
        self.lock = asyncio.Lock()
    
    async def initialize(self, profiles: List[Dict], proxies: List[Dict]):
        """Session havuzunu başlat"""
        
        for i in range(min(self.max_sessions, len(profiles))):
            session = await self._create_session(
                profiles[i % len(profiles)],
                proxies[i % len(proxies)] if proxies else None,
            )
            await self.available_sessions.put(session)
    
    async def acquire(self, timeout: float = 30) -> StealthSessionLauncher:
        """Havuzdan session al"""
        
        try:
            session = await asyncio.wait_for(
                self.available_sessions.get(),
                timeout=timeout
            )
            
            session_id = str(uuid.uuid4())
            async with self.lock:
                self.active_sessions[session_id] = session
            
            session.session_id = session_id
            return session
            
        except asyncio.TimeoutError:
            # Havuz boş, yeni session oluştur
            raise SessionPoolExhaustedError("No available sessions")
    
    async def release(self, session: StealthSessionLauncher, reusable: bool = True):
        """Session'ı havuza geri ver veya kapat"""
        
        session_id = getattr(session, 'session_id', None)
        
        async with self.lock:
            if session_id in self.active_sessions:
                del self.active_sessions[session_id]
        
        if reusable:
            # Clear cookies and state
            await session.context.clear_cookies()
            await self.available_sessions.put(session)
        else:
            # Session burned, close and create new
            await session.close()
            # Background task: create replacement
            asyncio.create_task(self._replenish_pool())
    
    async def _create_session(
        self, 
        profile: Dict, 
        proxy: Dict = None
    ) -> StealthSessionLauncher:
        """Yeni session oluştur"""
        session = StealthSessionLauncher(profile=profile, proxy=proxy)
        await session.launch()
        return session
    
    async def _replenish_pool(self):
        """Havuzu yeniden doldur"""
        # Implementation: create new session with fresh profile/proxy
        pass
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | Camoufox nowsecure.nl testini geçmeli | Automated test |
| AC-002 | VFS login %90+ başarı oranı | Load test (100 attempt) |
| AC-003 | Mouse movement human-like olmalı (~427 px/s) | Timing analysis |
| AC-004 | Typing pattern log-normal dağılım izlemeli | Statistical test |
| AC-005 | Cloudflare challenge otomatik tespit edilmeli | Integration test |
| AC-006 | Session pool 20 concurrent session desteklemeli | Stress test |
| AC-007 | Fallback (Camoufox → Patchright) çalışmalı | Failover test |

---

## Edge Cases

### Mid-Session Ban

```
Detection: HTTP 403 veya Cloudflare block mid-flow
Action:
  1. Screenshot capture
  2. Session terminate (don't reuse)
  3. Account cooldown trigger
  4. New session with different profile/proxy
  5. Resume from last successful state
```

### Browser Crash

```
Detection: Page/context/browser disconnected event
Action:
  1. Log crash details
  2. Release all resources
  3. Notify orchestrator
  4. Create replacement session
  5. Retry task from beginning
```

### Memory Leak

```
Detection: Session memory > 500MB
Action:
  1. Graceful session close
  2. Force garbage collection
  3. Create fresh session
  4. Log for monitoring
```

---

## Güvenlik Notları

1. **Profile Storage:** Fingerprint'ler Directus'ta, runtime'da load
2. **No Hardcoded Patterns:** Tüm parametreler configurable
3. **Rotation Policy:** Her profile max 50 kullanım
4. **Burned Detection:** Başarısız session'lar retire
5. **Resource Limits:** Max 500MB per session, 30 dakika timeout

---

## Sonraki Adımlar

Bu spec onaylandıktan sonra:
1. **005-PROXY-MANAGER.md** - Proxy rotation ve health
2. **006-CAPTCHA-SOLVER.md** - Multi-provider chain
3. Camoufox integration test environment
