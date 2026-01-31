# 017 - SMS Verification Specification

## Amaç

Vize randevu sistemlerinde SMS/telefon doğrulama otomasyonu. Virtual phone number provider entegrasyonu, SMS code extraction, voice call handling ve OTP entry. Özellikle KKOSMOS (Yunanistan) için kritik.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 011-KKOSMOS-ADAPTER | Primary consumer (SMS/Voice zorunlu) |
| 007-ACCOUNT-POOL-MANAGER | Phone-account linking |
| 013-STATE-MACHINE | VERIFYING state handling |
| 002-DIRECTUS-SCHEMA | Phone pool storage |

---

## SMS Verification Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       SMS VERIFICATION FLOW                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │
│  │  Phone Pool  │    │   Provider   │    │     SMS      │                  │
│  │   Manager    │───►│    Router    │───►│   Receiver   │                  │
│  └──────────────┘    └──────────────┘    └──────────────┘                  │
│         │                   │                   │                           │
│         │                   ▼                   ▼                           │
│         │            ┌──────────────┐    ┌──────────────┐                  │
│         │            │  5sim        │    │   Code       │                  │
│         │            │  SMSHub      │    │  Extractor   │                  │
│         │            │  SMSActivate │    │              │                  │
│         │            └──────────────┘    └──────────────┘                  │
│         │                                       │                           │
│         ▼                                       ▼                           │
│  ┌──────────────────────────────────────────────────────────┐              │
│  │                    CODE ENTRY                             │              │
│  ├──────────────────────────────────────────────────────────┤              │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐      │              │
│  │  │  SMS OTP    │  │ Voice Call  │  │   IVR       │      │              │
│  │  │  Entry      │  │ Handling    │  │   DTMF      │      │              │
│  │  └─────────────┘  └─────────────┘  └─────────────┘      │              │
│  └──────────────────────────────────────────────────────────┘              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Phone Number Provider Configuration

```python
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, List, Any
from abc import ABC, abstractmethod
import httpx
import asyncio

class SMSProvider(Enum):
    """SMS provider'lar"""
    FIVE_SIM = "5sim"
    SMS_HUB = "smshub"
    SMS_ACTIVATE = "smsactivate"
    SMS_PVA = "smspva"
    ONLINESIM = "onlinesim"

class PhoneStatus(Enum):
    """Telefon durumları"""
    AVAILABLE = "available"
    WAITING_SMS = "waiting_sms"
    WAITING_CALL = "waiting_call"
    CODE_RECEIVED = "code_received"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

@dataclass
class VirtualPhone:
    """Sanal telefon numarası"""
    id: str
    provider: SMSProvider
    number: str
    country: str
    
    # Status
    status: PhoneStatus = PhoneStatus.AVAILABLE
    
    # Received data
    sms_code: Optional[str] = None
    sms_text: Optional[str] = None
    voice_code: Optional[str] = None
    
    # Timestamps
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    code_received_at: Optional[datetime] = None
    
    # Metadata
    service: str = ""  # Service name (e.g., "kkosmos", "vfs")
    cost: float = 0.0

@dataclass
class SMSProviderConfig:
    """Provider configuration"""
    provider: SMSProvider
    api_key: str
    base_url: str
    
    # Pricing (per number)
    price_sms: float = 0.50
    price_voice: float = 1.00
    
    # Limits
    timeout_seconds: int = 300
    max_retries: int = 3
```

---

## Provider API Clients

```python
class SMSProviderClient(ABC):
    """Abstract SMS provider client"""
    
    @abstractmethod
    async def buy_number(
        self,
        country: str,
        service: str,
    ) -> Optional[VirtualPhone]:
        pass
    
    @abstractmethod
    async def get_sms(
        self,
        phone_id: str,
        timeout: int = 120,
    ) -> Optional[str]:
        pass
    
    @abstractmethod
    async def get_voice_code(
        self,
        phone_id: str,
        timeout: int = 180,
    ) -> Optional[str]:
        pass
    
    @abstractmethod
    async def cancel_number(self, phone_id: str):
        pass
    
    @abstractmethod
    async def finish_number(self, phone_id: str):
        pass
    
    @abstractmethod
    async def get_balance(self) -> float:
        pass


class FiveSimClient(SMSProviderClient):
    """5sim.net API client"""
    
    BASE_URL = "https://5sim.net/v1"
    
    # Country code mapping
    COUNTRY_CODES = {
        "tr": "turkey",
        "de": "germany",
        "nl": "netherlands",
        "fr": "france",
        "gr": "greece",
        "ru": "russia",
    }
    
    # Service mapping
    SERVICE_CODES = {
        "kkosmos": "other",
        "vfs": "other",
        "idata": "other",
        "bls": "other",
    }
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            timeout=30,
        )
    
    async def buy_number(
        self,
        country: str,
        service: str,
    ) -> Optional[VirtualPhone]:
        """Numara satın al"""
        
        country_code = self.COUNTRY_CODES.get(country, country)
        service_code = self.SERVICE_CODES.get(service, "other")
        
        try:
            response = await self.client.get(
                f"{self.BASE_URL}/user/buy/activation/{country_code}/any/{service_code}"
            )
            
            if response.status_code == 200:
                data = response.json()
                
                return VirtualPhone(
                    id=str(data["id"]),
                    provider=SMSProvider.FIVE_SIM,
                    number=data["phone"],
                    country=country,
                    status=PhoneStatus.WAITING_SMS,
                    service=service,
                    cost=data.get("price", 0),
                    created_at=datetime.utcnow(),
                    expires_at=datetime.utcnow() + timedelta(minutes=20),
                )
            
            return None
            
        except Exception as e:
            print(f"5sim buy error: {e}")
            return None
    
    async def get_sms(
        self,
        phone_id: str,
        timeout: int = 120,
    ) -> Optional[str]:
        """SMS bekle ve al"""
        
        start_time = datetime.utcnow()
        
        while (datetime.utcnow() - start_time).total_seconds() < timeout:
            try:
                response = await self.client.get(
                    f"{self.BASE_URL}/user/check/{phone_id}"
                )
                
                if response.status_code == 200:
                    data = response.json()
                    status = data.get("status")
                    
                    if status == "RECEIVED" and data.get("sms"):
                        # Extract code from SMS
                        sms_list = data["sms"]
                        if sms_list:
                            sms_text = sms_list[0].get("text", "")
                            code = self._extract_code(sms_text)
                            if code:
                                return code
                    
                    elif status in ["TIMEOUT", "BANNED", "CANCELED"]:
                        return None
                
            except Exception as e:
                print(f"5sim check error: {e}")
            
            await asyncio.sleep(5)
        
        return None
    
    async def get_voice_code(
        self,
        phone_id: str,
        timeout: int = 180,
    ) -> Optional[str]:
        """Voice call bekle (5sim voice desteği varsa)"""
        
        # 5sim primarily SMS, voice limited
        # Fallback to SMS
        return await self.get_sms(phone_id, timeout)
    
    async def cancel_number(self, phone_id: str):
        """Numara iptal (refund için)"""
        
        await self.client.get(
            f"{self.BASE_URL}/user/cancel/{phone_id}"
        )
    
    async def finish_number(self, phone_id: str):
        """İşlemi tamamla"""
        
        await self.client.get(
            f"{self.BASE_URL}/user/finish/{phone_id}"
        )
    
    async def get_balance(self) -> float:
        """Bakiye sorgula"""
        
        response = await self.client.get(
            f"{self.BASE_URL}/user/profile"
        )
        
        if response.status_code == 200:
            data = response.json()
            return float(data.get("balance", 0))
        
        return 0.0
    
    def _extract_code(self, sms_text: str) -> Optional[str]:
        """SMS'ten kod çıkar"""
        
        import re
        
        patterns = [
            r'(\d{6})',  # 6 digit
            r'(\d{4})',  # 4 digit
            r'(?:code|kod)[:\s]*(\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, sms_text, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None


class SMSHubClient(SMSProviderClient):
    """SMSHub API client"""
    
    BASE_URL = "https://smshub.org/stubs/handler_api.php"
    
    # Country codes
    COUNTRY_CODES = {
        "tr": "62",
        "de": "43",
        "nl": "48",
        "fr": "78",
        "gr": "33",
        "ru": "0",
    }
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30)
    
    async def buy_number(
        self,
        country: str,
        service: str,
    ) -> Optional[VirtualPhone]:
        """Numara satın al"""
        
        country_code = self.COUNTRY_CODES.get(country, "0")
        
        try:
            response = await self.client.get(
                self.BASE_URL,
                params={
                    "api_key": self.api_key,
                    "action": "getNumber",
                    "service": "ot",  # Other
                    "country": country_code,
                }
            )
            
            text = response.text
            
            if text.startswith("ACCESS_NUMBER"):
                parts = text.split(":")
                return VirtualPhone(
                    id=parts[1],
                    provider=SMSProvider.SMS_HUB,
                    number=parts[2],
                    country=country,
                    status=PhoneStatus.WAITING_SMS,
                    service=service,
                    created_at=datetime.utcnow(),
                    expires_at=datetime.utcnow() + timedelta(minutes=20),
                )
            
            return None
            
        except Exception as e:
            print(f"SMSHub buy error: {e}")
            return None
    
    async def get_sms(
        self,
        phone_id: str,
        timeout: int = 120,
    ) -> Optional[str]:
        """SMS bekle ve al"""
        
        # Set status to waiting
        await self._set_status(phone_id, "1")
        
        start_time = datetime.utcnow()
        
        while (datetime.utcnow() - start_time).total_seconds() < timeout:
            try:
                response = await self.client.get(
                    self.BASE_URL,
                    params={
                        "api_key": self.api_key,
                        "action": "getStatus",
                        "id": phone_id,
                    }
                )
                
                text = response.text
                
                if text.startswith("STATUS_OK"):
                    code = text.split(":")[1]
                    return code
                
                elif text in ["STATUS_CANCEL", "STATUS_ERROR"]:
                    return None
                
            except Exception as e:
                print(f"SMSHub check error: {e}")
            
            await asyncio.sleep(5)
        
        return None
    
    async def get_voice_code(
        self,
        phone_id: str,
        timeout: int = 180,
    ) -> Optional[str]:
        """Voice call (SMSHub voice desteği sınırlı)"""
        return await self.get_sms(phone_id, timeout)
    
    async def cancel_number(self, phone_id: str):
        """İptal"""
        await self._set_status(phone_id, "8")
    
    async def finish_number(self, phone_id: str):
        """Tamamla"""
        await self._set_status(phone_id, "6")
    
    async def get_balance(self) -> float:
        """Bakiye"""
        
        response = await self.client.get(
            self.BASE_URL,
            params={
                "api_key": self.api_key,
                "action": "getBalance",
            }
        )
        
        text = response.text
        
        if text.startswith("ACCESS_BALANCE"):
            return float(text.split(":")[1])
        
        return 0.0
    
    async def _set_status(self, phone_id: str, status: str):
        """Durum güncelle"""
        
        await self.client.get(
            self.BASE_URL,
            params={
                "api_key": self.api_key,
                "action": "setStatus",
                "id": phone_id,
                "status": status,
            }
        )


class SMSActivateClient(SMSProviderClient):
    """SMS-Activate API client"""
    
    BASE_URL = "https://api.sms-activate.org/stubs/handler_api.php"
    
    COUNTRY_CODES = {
        "tr": "62",
        "de": "43",
        "nl": "48",
        "fr": "78",
        "gr": "33",
        "ru": "0",
    }
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30)
    
    async def buy_number(
        self,
        country: str,
        service: str,
    ) -> Optional[VirtualPhone]:
        """Numara satın al"""
        
        country_code = self.COUNTRY_CODES.get(country, "0")
        
        try:
            response = await self.client.get(
                self.BASE_URL,
                params={
                    "api_key": self.api_key,
                    "action": "getNumber",
                    "service": "ot",
                    "country": country_code,
                }
            )
            
            text = response.text
            
            if text.startswith("ACCESS_NUMBER"):
                parts = text.split(":")
                return VirtualPhone(
                    id=parts[1],
                    provider=SMSProvider.SMS_ACTIVATE,
                    number=parts[2],
                    country=country,
                    status=PhoneStatus.WAITING_SMS,
                    service=service,
                    created_at=datetime.utcnow(),
                    expires_at=datetime.utcnow() + timedelta(minutes=20),
                )
            
            return None
            
        except Exception as e:
            print(f"SMS-Activate buy error: {e}")
            return None
    
    async def get_sms(
        self,
        phone_id: str,
        timeout: int = 120,
    ) -> Optional[str]:
        """SMS bekle"""
        
        start_time = datetime.utcnow()
        
        while (datetime.utcnow() - start_time).total_seconds() < timeout:
            try:
                response = await self.client.get(
                    self.BASE_URL,
                    params={
                        "api_key": self.api_key,
                        "action": "getStatus",
                        "id": phone_id,
                    }
                )
                
                text = response.text
                
                if text.startswith("STATUS_OK"):
                    code = text.split(":")[1]
                    return code
                
                elif text in ["STATUS_CANCEL"]:
                    return None
                
            except Exception as e:
                print(f"SMS-Activate check error: {e}")
            
            await asyncio.sleep(5)
        
        return None
    
    async def get_voice_code(
        self,
        phone_id: str,
        timeout: int = 180,
    ) -> Optional[str]:
        """Voice call"""
        return await self.get_sms(phone_id, timeout)
    
    async def cancel_number(self, phone_id: str):
        """İptal"""
        await self.client.get(
            self.BASE_URL,
            params={
                "api_key": self.api_key,
                "action": "setStatus",
                "id": phone_id,
                "status": "8",
            }
        )
    
    async def finish_number(self, phone_id: str):
        """Tamamla"""
        await self.client.get(
            self.BASE_URL,
            params={
                "api_key": self.api_key,
                "action": "setStatus",
                "id": phone_id,
                "status": "6",
            }
        )
    
    async def get_balance(self) -> float:
        """Bakiye"""
        
        response = await self.client.get(
            self.BASE_URL,
            params={
                "api_key": self.api_key,
                "action": "getBalance",
            }
        )
        
        text = response.text
        
        if text.startswith("ACCESS_BALANCE"):
            return float(text.split(":")[1])
        
        return 0.0
```

---

## Phone Pool Manager

```python
class PhonePoolManager:
    """Virtual phone number pool yönetimi"""
    
    # Provider priority
    PROVIDER_PRIORITY = [
        SMSProvider.FIVE_SIM,
        SMSProvider.SMS_HUB,
        SMSProvider.SMS_ACTIVATE,
    ]
    
    # Pool settings
    MIN_BALANCE_ALERT = 5.0
    PHONE_COOLDOWN_MINUTES = 30
    MAX_USAGE_PER_PHONE = 3
    
    def __init__(self, provider_configs: Dict[SMSProvider, SMSProviderConfig]):
        self.configs = provider_configs
        self.clients: Dict[SMSProvider, SMSProviderClient] = {}
        self.active_phones: Dict[str, VirtualPhone] = {}
        self.phone_history: List[Dict] = []
        self.lock = asyncio.Lock()
        
        # Initialize clients
        for provider, config in provider_configs.items():
            if provider == SMSProvider.FIVE_SIM:
                self.clients[provider] = FiveSimClient(config.api_key)
            elif provider == SMSProvider.SMS_HUB:
                self.clients[provider] = SMSHubClient(config.api_key)
            elif provider == SMSProvider.SMS_ACTIVATE:
                self.clients[provider] = SMSActivateClient(config.api_key)
    
    async def acquire_phone(
        self,
        country: str,
        service: str,
        preferred_provider: Optional[SMSProvider] = None,
    ) -> Optional[VirtualPhone]:
        """Telefon numarası al"""
        
        async with self.lock:
            # Try providers in priority order
            providers = [preferred_provider] if preferred_provider else self.PROVIDER_PRIORITY
            
            for provider in providers:
                if provider not in self.clients:
                    continue
                
                client = self.clients[provider]
                
                # Check balance first
                balance = await client.get_balance()
                if balance < self.MIN_BALANCE_ALERT:
                    await self._send_balance_alert(provider, balance)
                    continue
                
                # Buy number
                phone = await client.buy_number(country, service)
                
                if phone:
                    self.active_phones[phone.id] = phone
                    return phone
            
            return None
    
    async def get_sms_code(
        self,
        phone_id: str,
        timeout: int = 120,
    ) -> Optional[str]:
        """SMS kodu bekle"""
        
        if phone_id not in self.active_phones:
            return None
        
        phone = self.active_phones[phone_id]
        client = self.clients.get(phone.provider)
        
        if not client:
            return None
        
        phone.status = PhoneStatus.WAITING_SMS
        
        code = await client.get_sms(phone_id, timeout)
        
        if code:
            phone.status = PhoneStatus.CODE_RECEIVED
            phone.sms_code = code
            phone.code_received_at = datetime.utcnow()
        else:
            phone.status = PhoneStatus.EXPIRED
        
        return code
    
    async def get_voice_code(
        self,
        phone_id: str,
        timeout: int = 180,
    ) -> Optional[str]:
        """Voice call kodu bekle"""
        
        if phone_id not in self.active_phones:
            return None
        
        phone = self.active_phones[phone_id]
        client = self.clients.get(phone.provider)
        
        if not client:
            return None
        
        phone.status = PhoneStatus.WAITING_CALL
        
        code = await client.get_voice_code(phone_id, timeout)
        
        if code:
            phone.status = PhoneStatus.CODE_RECEIVED
            phone.voice_code = code
            phone.code_received_at = datetime.utcnow()
        else:
            phone.status = PhoneStatus.EXPIRED
        
        return code
    
    async def release_phone(
        self,
        phone_id: str,
        success: bool = True,
    ):
        """Telefonu serbest bırak"""
        
        async with self.lock:
            if phone_id not in self.active_phones:
                return
            
            phone = self.active_phones[phone_id]
            client = self.clients.get(phone.provider)
            
            if not client:
                return
            
            if success:
                phone.status = PhoneStatus.COMPLETED
                await client.finish_number(phone_id)
            else:
                phone.status = PhoneStatus.CANCELLED
                await client.cancel_number(phone_id)
            
            # Track history
            self.phone_history.append({
                "phone_id": phone_id,
                "provider": phone.provider.value,
                "country": phone.country,
                "service": phone.service,
                "success": success,
                "code_received": phone.sms_code or phone.voice_code,
                "timestamp": datetime.utcnow().isoformat(),
            })
            
            # Remove from active
            del self.active_phones[phone_id]
    
    async def get_pool_stats(self) -> Dict:
        """Pool istatistikleri"""
        
        stats = {
            "active_phones": len(self.active_phones),
            "by_provider": {},
            "by_status": {},
            "balances": {},
        }
        
        for provider, client in self.clients.items():
            balance = await client.get_balance()
            stats["balances"][provider.value] = balance
            stats["by_provider"][provider.value] = len([
                p for p in self.active_phones.values()
                if p.provider == provider
            ])
        
        for phone in self.active_phones.values():
            status = phone.status.value
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1
        
        return stats
    
    async def _send_balance_alert(self, provider: SMSProvider, balance: float):
        """Düşük bakiye uyarısı"""
        # Telegram/Slack notification
        print(f"LOW BALANCE ALERT: {provider.value} balance is ${balance:.2f}")
```

---

## SMS Verification Executor

```python
class SMSVerificationExecutor:
    """SMS verification işlemlerini uygula"""
    
    def __init__(
        self,
        phone_pool: PhonePoolManager,
    ):
        self.pool = phone_pool
    
    async def verify_by_sms(
        self,
        country: str,
        service: str,
        phone_entry_callback,
        code_entry_callback,
        timeout_seconds: int = 120,
    ) -> Dict[str, Any]:
        """SMS-based verification"""
        
        # Acquire phone
        phone = await self.pool.acquire_phone(country, service)
        
        if not phone:
            return {
                "success": False,
                "error": "No phone number available",
            }
        
        try:
            # Enter phone number in form
            entry_success = await phone_entry_callback(phone.number)
            
            if not entry_success:
                return {
                    "success": False,
                    "error": "Failed to enter phone number",
                }
            
            # Wait for SMS
            code = await self.pool.get_sms_code(
                phone.id,
                timeout=timeout_seconds,
            )
            
            if not code:
                return {
                    "success": False,
                    "error": "SMS code not received",
                    "phone": phone.number,
                }
            
            # Enter code
            code_success = await code_entry_callback(code)
            
            if not code_success:
                return {
                    "success": False,
                    "error": "Failed to enter verification code",
                    "code": code,
                }
            
            return {
                "success": True,
                "phone": phone.number,
                "code": code,
                "method": "sms",
            }
            
        finally:
            # Release phone
            await self.pool.release_phone(
                phone.id,
                success=code is not None,
            )
    
    async def verify_by_voice(
        self,
        country: str,
        service: str,
        phone_entry_callback,
        code_entry_callback,
        timeout_seconds: int = 180,
    ) -> Dict[str, Any]:
        """Voice call verification"""
        
        # Acquire phone
        phone = await self.pool.acquire_phone(country, service)
        
        if not phone:
            return {
                "success": False,
                "error": "No phone number available",
            }
        
        try:
            # Enter phone number
            entry_success = await phone_entry_callback(phone.number)
            
            if not entry_success:
                return {
                    "success": False,
                    "error": "Failed to enter phone number",
                }
            
            # Wait for voice call
            code = await self.pool.get_voice_code(
                phone.id,
                timeout=timeout_seconds,
            )
            
            if not code:
                return {
                    "success": False,
                    "error": "Voice code not received",
                    "phone": phone.number,
                }
            
            # Enter code
            code_success = await code_entry_callback(code)
            
            return {
                "success": code_success,
                "phone": phone.number,
                "code": code,
                "method": "voice",
            }
            
        finally:
            await self.pool.release_phone(
                phone.id,
                success=code is not None,
            )
    
    async def verify_with_fallback(
        self,
        country: str,
        service: str,
        phone_entry_callback,
        code_entry_callback,
        primary_method: str = "sms",
        timeout_seconds: int = 120,
    ) -> Dict[str, Any]:
        """SMS → Voice fallback ile verification"""
        
        # Try primary method
        if primary_method == "sms":
            result = await self.verify_by_sms(
                country, service,
                phone_entry_callback, code_entry_callback,
                timeout_seconds,
            )
            
            if result["success"]:
                return result
            
            # Fallback to voice
            return await self.verify_by_voice(
                country, service,
                phone_entry_callback, code_entry_callback,
                timeout_seconds + 60,
            )
        else:
            result = await self.verify_by_voice(
                country, service,
                phone_entry_callback, code_entry_callback,
                timeout_seconds,
            )
            
            if result["success"]:
                return result
            
            # Fallback to SMS
            return await self.verify_by_sms(
                country, service,
                phone_entry_callback, code_entry_callback,
                timeout_seconds,
            )
```

---

## Code Extraction Patterns

```python
class SMSCodeExtractor:
    """SMS'ten verification code çıkarma"""
    
    # Site-specific patterns
    SITE_PATTERNS = {
        "kkosmos": {
            "patterns": [
                r'(?:code|κωδικός)[:\s]*(\d{6})',
                r'(?:verification|επαλήθευση)[:\s]*(\d{6})',
                r'\b(\d{6})\b',
            ],
            "length": 6,
        },
        "vfs": {
            "patterns": [
                r'(?:verification code|doğrulama kodu)[:\s]*(\d{6})',
                r'(?:OTP)[:\s]*(\d{6})',
                r'\b(\d{6})\b',
            ],
            "length": 6,
        },
        "generic": {
            "patterns": [
                r'(?:code|kod|OTP)[:\s]*(\d{4,8})',
                r'\b(\d{6})\b',
                r'\b(\d{4})\b',
            ],
            "length": None,
        },
    }
    
    @classmethod
    def extract(
        cls,
        sms_text: str,
        site: Optional[str] = None,
    ) -> Optional[str]:
        """SMS'ten kod çıkar"""
        
        import re
        
        # Get patterns for site
        config = cls.SITE_PATTERNS.get(site, cls.SITE_PATTERNS["generic"])
        expected_length = config.get("length")
        
        for pattern in config["patterns"]:
            match = re.search(pattern, sms_text, re.IGNORECASE)
            if match:
                code = match.group(1)
                
                # Validate length if specified
                if expected_length and len(code) != expected_length:
                    continue
                
                return code
        
        return None
    
    @classmethod
    def validate_code(
        cls,
        code: str,
        site: Optional[str] = None,
    ) -> bool:
        """Kod geçerli mi?"""
        
        if not code or not code.isdigit():
            return False
        
        config = cls.SITE_PATTERNS.get(site, cls.SITE_PATTERNS["generic"])
        expected_length = config.get("length")
        
        if expected_length:
            return len(code) == expected_length
        
        return 4 <= len(code) <= 8
```

---

## Human Escalation for Voice

```python
class HumanEscalationService:
    """Voice call için human-in-the-loop"""
    
    def __init__(
        self,
        telegram_bot_token: str,
        telegram_chat_id: str,
    ):
        self.telegram_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        self.client = httpx.AsyncClient()
        
        self.pending_requests: Dict[str, Dict] = {}
    
    async def request_voice_code(
        self,
        phone_number: str,
        booking_id: str,
        timeout_seconds: int = 300,
    ) -> Optional[str]:
        """Operatörden voice code iste"""
        
        request_id = str(uuid.uuid4())[:8]
        
        # Send Telegram message
        message = f"""
🔔 **Voice Verification Required**

Booking ID: `{booking_id}`
Phone: `{phone_number}`
Request ID: `{request_id}`

Reply with the code received in the phone call.
Format: `/code {request_id} XXXXXX`
        """
        
        await self._send_telegram(message)
        
        # Store pending request
        self.pending_requests[request_id] = {
            "booking_id": booking_id,
            "phone": phone_number,
            "created_at": datetime.utcnow(),
            "code": None,
        }
        
        # Wait for response
        start_time = datetime.utcnow()
        
        while (datetime.utcnow() - start_time).total_seconds() < timeout_seconds:
            request = self.pending_requests.get(request_id)
            
            if request and request.get("code"):
                code = request["code"]
                del self.pending_requests[request_id]
                return code
            
            await asyncio.sleep(5)
        
        # Timeout
        if request_id in self.pending_requests:
            del self.pending_requests[request_id]
        
        return None
    
    async def submit_code(self, request_id: str, code: str) -> bool:
        """Operatör kod gönderdi"""
        
        if request_id not in self.pending_requests:
            return False
        
        self.pending_requests[request_id]["code"] = code
        return True
    
    async def _send_telegram(self, message: str):
        """Telegram mesajı gönder"""
        
        await self.client.post(
            f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
            json={
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "Markdown",
            }
        )
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | 5sim SMS alımı çalışmalı | Integration test |
| AC-002 | SMSHub SMS alımı çalışmalı | Integration test |
| AC-003 | SMS code extraction %95+ | Pattern test |
| AC-004 | SMS → Voice fallback çalışmalı | Failover test |
| AC-005 | Provider balance tracking doğru olmalı | Balance test |
| AC-006 | Human escalation workflow çalışmalı | Manual test |
| AC-007 | Phone pool rotation çalışmalı | Pool test |

---

## Maliyet Karşılaştırması

| Provider | SMS Fiyat | Voice Fiyat | Country Support |
|----------|-----------|-------------|-----------------|
| 5sim | $0.10-0.50 | $0.50-1.00 | 100+ |
| SMSHub | $0.05-0.30 | $0.30-0.80 | 50+ |
| SMS-Activate | $0.08-0.40 | $0.40-0.90 | 80+ |

---

## Risk Yönetimi

1. **Provider Down:** Multi-provider fallback
2. **Number Blacklisted:** Automatic provider rotation
3. **High Cost:** Budget alerts, provider switching
4. **Voice Not Supported:** Human escalation

---

## Sonraki Adımlar

Grup 5 (Payment ve Verification) tamamlandı.

Sonraki: **Grup 6 - Monitoring ve Analytics** (018, 019, 020)
