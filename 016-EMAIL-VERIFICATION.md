# 016 - Email Verification Specification

## Amaç

Vize randevu sistemlerinde email doğrulama otomasyonu. IMAP/POP3 ile email okuma, OTP/verification code extraction, confirmation link tıklama ve account activation. Multi-provider email pool yönetimi.

---

## Bağımlılıklar

| Modül | İlişki |
|-------|--------|
| 007-ACCOUNT-POOL-MANAGER | Account email credentials |
| 004-STEALTH-ENGINE | Link click için browser |
| 008-011 | Site adapters - verification trigger |
| 013-STATE-MACHINE | VERIFYING state handling |

---

## Email Verification Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       EMAIL VERIFICATION FLOW                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │
│  │  Email Pool  │    │    Email     │    │   Content    │                  │
│  │   Manager    │───►│   Fetcher    │───►│   Extractor  │                  │
│  └──────────────┘    └──────────────┘    └──────────────┘                  │
│         │                   │                   │                           │
│         │                   ▼                   ▼                           │
│         │            ┌──────────────┐    ┌──────────────┐                  │
│         │            │  IMAP/POP3   │    │  Code/Link   │                  │
│         │            │  Providers   │    │   Parser     │                  │
│         │            └──────────────┘    └──────────────┘                  │
│         │                                       │                           │
│         ▼                                       ▼                           │
│  ┌──────────────────────────────────────────────────────────┐              │
│  │                    ACTION EXECUTOR                        │              │
│  ├──────────────────────────────────────────────────────────┤              │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐      │              │
│  │  │  OTP Code   │  │ Confirm     │  │  Account    │      │              │
│  │  │  Entry      │  │ Link Click  │  │ Activation  │      │              │
│  │  └─────────────┘  └─────────────┘  └─────────────┘      │              │
│  └──────────────────────────────────────────────────────────┘              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Email Provider Configuration

```python
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, List
import imaplib
import poplib
import email
from email.header import decode_header
import asyncio

class EmailProtocol(Enum):
    IMAP = "imap"
    POP3 = "pop3"

class EmailProvider(Enum):
    """Desteklenen email provider'lar"""
    GMAIL = "gmail"
    OUTLOOK = "outlook"
    YANDEX = "yandex"
    CUSTOM = "custom"
    TEMP_MAIL = "temp_mail"  # Disposable email services

@dataclass
class EmailConfig:
    """Email account configuration"""
    provider: EmailProvider
    protocol: EmailProtocol
    
    # Server settings
    host: str
    port: int
    use_ssl: bool = True
    
    # Credentials
    email_address: str
    password: str
    
    # OAuth (for Gmail/Outlook)
    oauth_token: Optional[str] = None
    
# Provider presets
PROVIDER_CONFIGS = {
    EmailProvider.GMAIL: {
        "imap": {"host": "imap.gmail.com", "port": 993, "ssl": True},
        "pop3": {"host": "pop.gmail.com", "port": 995, "ssl": True},
    },
    EmailProvider.OUTLOOK: {
        "imap": {"host": "outlook.office365.com", "port": 993, "ssl": True},
        "pop3": {"host": "outlook.office365.com", "port": 995, "ssl": True},
    },
    EmailProvider.YANDEX: {
        "imap": {"host": "imap.yandex.com", "port": 993, "ssl": True},
        "pop3": {"host": "pop.yandex.com", "port": 995, "ssl": True},
    },
}
```

---

## Email Pool Manager

```python
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import asyncio

@dataclass
class EmailAccount:
    """Email account in pool"""
    id: str
    email_address: str
    provider: EmailProvider
    
    # Credentials (encrypted)
    encrypted_password: bytes
    oauth_token: Optional[str] = None
    
    # Status
    status: str = "available"  # available, in_use, cooldown, disabled
    
    # Usage tracking
    usage_count: int = 0
    last_used_at: Optional[datetime] = None
    cooldown_until: Optional[datetime] = None
    
    # Associated account (if linked to bot account)
    bot_account_id: Optional[str] = None

class EmailPoolManager:
    """Email account pool yönetimi"""
    
    # Cooldown settings
    COOLDOWN_MINUTES = 5
    MAX_USAGE_PER_HOUR = 20
    
    def __init__(self, encryptor):
        self.encryptor = encryptor
        self.accounts: Dict[str, EmailAccount] = {}
        self.lock = asyncio.Lock()
    
    async def acquire(
        self,
        provider: Optional[EmailProvider] = None,
        bot_account_id: Optional[str] = None,
    ) -> Optional[EmailAccount]:
        """Email account al"""
        
        async with self.lock:
            # If linked to bot account, use that
            if bot_account_id:
                for account in self.accounts.values():
                    if account.bot_account_id == bot_account_id:
                        if account.status == "available":
                            account.status = "in_use"
                            account.last_used_at = datetime.utcnow()
                            return account
            
            # Find available account
            now = datetime.utcnow()
            
            for account in self.accounts.values():
                if account.status != "available":
                    continue
                
                if provider and account.provider != provider:
                    continue
                
                # Check cooldown
                if account.cooldown_until and account.cooldown_until > now:
                    continue
                
                # Check rate limit
                if account.last_used_at:
                    hour_ago = now - timedelta(hours=1)
                    if account.last_used_at > hour_ago and account.usage_count >= self.MAX_USAGE_PER_HOUR:
                        continue
                
                # Use this account
                account.status = "in_use"
                account.last_used_at = now
                account.usage_count += 1
                
                return account
            
            return None
    
    async def release(
        self,
        account_id: str,
        success: bool = True,
    ):
        """Email account serbest bırak"""
        
        async with self.lock:
            if account_id not in self.accounts:
                return
            
            account = self.accounts[account_id]
            
            if success:
                account.cooldown_until = datetime.utcnow() + timedelta(
                    minutes=self.COOLDOWN_MINUTES
                )
                account.status = "cooldown"
            else:
                account.status = "available"
    
    async def add_account(
        self,
        email_address: str,
        password: str,
        provider: EmailProvider,
        bot_account_id: Optional[str] = None,
    ) -> EmailAccount:
        """Yeni email account ekle"""
        
        encrypted_password = self.encryptor.encrypt(password.encode())
        
        account = EmailAccount(
            id=str(uuid.uuid4()),
            email_address=email_address,
            provider=provider,
            encrypted_password=encrypted_password,
            bot_account_id=bot_account_id,
        )
        
        self.accounts[account.id] = account
        return account
```

---

## IMAP Email Fetcher

```python
import imaplib
import email
from email.header import decode_header
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import asyncio
from concurrent.futures import ThreadPoolExecutor

@dataclass
class EmailMessage:
    """Email mesajı"""
    message_id: str
    sender: str
    subject: str
    body_text: str
    body_html: str
    received_at: datetime
    
    # Extracted data
    verification_code: Optional[str] = None
    verification_link: Optional[str] = None

class IMAPEmailFetcher:
    """IMAP ile email okuma"""
    
    def __init__(self, config: EmailConfig, encryptor):
        self.config = config
        self.encryptor = encryptor
        self.executor = ThreadPoolExecutor(max_workers=5)
    
    async def fetch_verification_email(
        self,
        sender_patterns: List[str],
        subject_patterns: List[str],
        timeout_seconds: int = 120,
        poll_interval: int = 5,
    ) -> Optional[EmailMessage]:
        """Verification email'i bekle ve al"""
        
        start_time = datetime.utcnow()
        
        while (datetime.utcnow() - start_time).total_seconds() < timeout_seconds:
            # Fetch recent emails
            messages = await self._fetch_recent_emails(
                since_minutes=5,
                sender_patterns=sender_patterns,
                subject_patterns=subject_patterns,
            )
            
            if messages:
                # Return most recent matching
                return messages[0]
            
            await asyncio.sleep(poll_interval)
        
        return None
    
    async def _fetch_recent_emails(
        self,
        since_minutes: int,
        sender_patterns: List[str],
        subject_patterns: List[str],
    ) -> List[EmailMessage]:
        """Son X dakikadaki email'leri al"""
        
        # Run in thread pool (imaplib is sync)
        loop = asyncio.get_event_loop()
        
        return await loop.run_in_executor(
            self.executor,
            self._sync_fetch_emails,
            since_minutes,
            sender_patterns,
            subject_patterns,
        )
    
    def _sync_fetch_emails(
        self,
        since_minutes: int,
        sender_patterns: List[str],
        subject_patterns: List[str],
    ) -> List[EmailMessage]:
        """Sync email fetch (thread'de çalışır)"""
        
        messages = []
        
        try:
            # Connect
            if self.config.use_ssl:
                mail = imaplib.IMAP4_SSL(self.config.host, self.config.port)
            else:
                mail = imaplib.IMAP4(self.config.host, self.config.port)
            
            # Decrypt password
            password = self.encryptor.decrypt(self.config.password)
            
            # Login
            mail.login(self.config.email_address, password)
            
            # Select inbox
            mail.select("INBOX")
            
            # Search for recent emails
            since_date = (datetime.utcnow() - timedelta(minutes=since_minutes)).strftime("%d-%b-%Y")
            
            search_criteria = f'(SINCE "{since_date}")'
            
            status, message_ids = mail.search(None, search_criteria)
            
            if status != "OK":
                return messages
            
            # Process each message
            for msg_id in message_ids[0].split()[-10:]:  # Last 10
                status, msg_data = mail.fetch(msg_id, "(RFC822)")
                
                if status != "OK":
                    continue
                
                # Parse email
                raw_email = msg_data[0][1]
                email_message = email.message_from_bytes(raw_email)
                
                # Extract headers
                sender = self._decode_header(email_message["From"])
                subject = self._decode_header(email_message["Subject"])
                
                # Check patterns
                if not self._matches_patterns(sender, sender_patterns):
                    continue
                
                if not self._matches_patterns(subject, subject_patterns):
                    continue
                
                # Extract body
                body_text, body_html = self._extract_body(email_message)
                
                # Create message object
                msg = EmailMessage(
                    message_id=msg_id.decode(),
                    sender=sender,
                    subject=subject,
                    body_text=body_text,
                    body_html=body_html,
                    received_at=datetime.utcnow(),
                )
                
                messages.append(msg)
            
            mail.logout()
            
        except Exception as e:
            print(f"IMAP error: {e}")
        
        return messages
    
    def _decode_header(self, header: str) -> str:
        """Email header decode"""
        
        if not header:
            return ""
        
        decoded_parts = decode_header(header)
        result = ""
        
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                result += part.decode(encoding or "utf-8", errors="ignore")
            else:
                result += part
        
        return result
    
    def _matches_patterns(self, text: str, patterns: List[str]) -> bool:
        """Pattern eşleşme kontrolü"""
        
        if not patterns:
            return True
        
        text_lower = text.lower()
        
        for pattern in patterns:
            if pattern.lower() in text_lower:
                return True
        
        return False
    
    def _extract_body(self, email_message) -> tuple:
        """Email body'yi çıkar"""
        
        body_text = ""
        body_html = ""
        
        if email_message.is_multipart():
            for part in email_message.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                
                if "attachment" in content_disposition:
                    continue
                
                payload = part.get_payload(decode=True)
                
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="ignore")
                    
                    if content_type == "text/plain":
                        body_text = text
                    elif content_type == "text/html":
                        body_html = text
        else:
            content_type = email_message.get_content_type()
            payload = email_message.get_payload(decode=True)
            
            if payload:
                charset = email_message.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="ignore")
                
                if content_type == "text/plain":
                    body_text = text
                else:
                    body_html = text
        
        return body_text, body_html
```

---

## Verification Code/Link Extractor

```python
import re
from typing import Optional, List, Dict
from bs4 import BeautifulSoup

class VerificationExtractor:
    """Email'den verification code/link çıkarma"""
    
    # Common code patterns
    CODE_PATTERNS = [
        r'(?:code|kod|verification|doğrulama)[:\s]*(\d{4,8})',  # "Code: 123456"
        r'(?:OTP|otp)[:\s]*(\d{4,8})',
        r'\b(\d{6})\b',  # Standalone 6-digit
        r'(?:PIN|pin)[:\s]*(\d{4,6})',
        r'(?:confirmation|onay)[:\s#]*(\d{4,8})',
    ]
    
    # Common link patterns
    LINK_PATTERNS = [
        r'(https?://[^\s<>"]+(?:verify|confirm|activate|validate)[^\s<>"]*)',
        r'(https?://[^\s<>"]+\?[^\s<>"]*(?:token|code|key)=[^\s<>"]+)',
        r'<a[^>]+href=["\']([^"\']+(?:verify|confirm|activate)[^"\']*)["\']',
    ]
    
    # Site-specific patterns
    SITE_PATTERNS = {
        "vfs": {
            "sender": ["vfsglobal", "noreply@vfs"],
            "subject": ["verification", "confirm", "booking"],
            "code_pattern": r'(?:verification code|doğrulama kodu)[:\s]*(\d{6})',
            "link_pattern": r'(https://visa\.vfsglobal\.com/[^\s<>"]+(?:verify|confirm)[^\s<>"]*)',
        },
        "idata": {
            "sender": ["idata.com.tr", "noreply@idata"],
            "subject": ["randevu", "appointment", "confirm"],
            "code_pattern": r'(?:onay kodu|confirmation)[:\s]*(\d{6})',
            "link_pattern": r'(https://[^\s<>"]*idata[^\s<>"]+(?:confirm|verify)[^\s<>"]*)',
        },
        "bls": {
            "sender": ["blsspain", "bls"],
            "subject": ["verification", "booking"],
            "code_pattern": r'(?:code|código)[:\s]*(\d{6})',
            "link_pattern": r'(https://[^\s<>"]*bls[^\s<>"]+(?:verify|confirm)[^\s<>"]*)',
        },
    }
    
    def extract_code(
        self,
        message: EmailMessage,
        site: Optional[str] = None,
    ) -> Optional[str]:
        """Verification code çıkar"""
        
        # Combine text and HTML
        content = message.body_text + " " + self._html_to_text(message.body_html)
        
        # Site-specific pattern first
        if site and site in self.SITE_PATTERNS:
            pattern = self.SITE_PATTERNS[site].get("code_pattern")
            if pattern:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    return match.group(1)
        
        # Generic patterns
        for pattern in self.CODE_PATTERNS:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                code = match.group(1)
                # Validate: 4-8 digits
                if code.isdigit() and 4 <= len(code) <= 8:
                    return code
        
        return None
    
    def extract_link(
        self,
        message: EmailMessage,
        site: Optional[str] = None,
    ) -> Optional[str]:
        """Verification link çıkar"""
        
        # Prefer HTML for links
        content = message.body_html or message.body_text
        
        # Site-specific pattern first
        if site and site in self.SITE_PATTERNS:
            pattern = self.SITE_PATTERNS[site].get("link_pattern")
            if pattern:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    return self._clean_link(match.group(1))
        
        # Parse HTML for links
        if message.body_html:
            soup = BeautifulSoup(message.body_html, "html.parser")
            
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                text = a_tag.get_text().lower()
                
                # Check link text
                if any(kw in text for kw in ["verify", "confirm", "activate", "doğrula", "onayla"]):
                    return self._clean_link(href)
                
                # Check URL
                if any(kw in href.lower() for kw in ["verify", "confirm", "activate", "token"]):
                    return self._clean_link(href)
        
        # Generic patterns
        for pattern in self.LINK_PATTERNS:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                return self._clean_link(match.group(1))
        
        return None
    
    def extract_all(
        self,
        message: EmailMessage,
        site: Optional[str] = None,
    ) -> Dict[str, Optional[str]]:
        """Tüm verification bilgilerini çıkar"""
        
        return {
            "code": self.extract_code(message, site),
            "link": self.extract_link(message, site),
        }
    
    def _html_to_text(self, html: str) -> str:
        """HTML'i text'e çevir"""
        
        if not html:
            return ""
        
        soup = BeautifulSoup(html, "html.parser")
        
        # Remove script and style
        for tag in soup(["script", "style"]):
            tag.decompose()
        
        return soup.get_text(separator=" ")
    
    def _clean_link(self, link: str) -> str:
        """Link temizle"""
        
        # Remove trailing punctuation
        link = link.rstrip(".,;:>\"'")
        
        # Decode HTML entities
        link = link.replace("&amp;", "&")
        
        return link
```

---

## Email Verification Executor

```python
class EmailVerificationExecutor:
    """Email verification işlemlerini uygula"""
    
    def __init__(
        self,
        email_pool: EmailPoolManager,
        email_fetcher_factory,
        extractor: VerificationExtractor,
        browser_session_factory,
    ):
        self.pool = email_pool
        self.fetcher_factory = email_fetcher_factory
        self.extractor = extractor
        self.browser_factory = browser_session_factory
    
    async def verify_by_code(
        self,
        site: str,
        bot_account_id: str,
        code_entry_callback,
        timeout_seconds: int = 120,
    ) -> Dict[str, Any]:
        """Code-based verification"""
        
        # Get email account
        email_account = await self.pool.acquire(bot_account_id=bot_account_id)
        
        if not email_account:
            return {"success": False, "error": "No email account available"}
        
        try:
            # Create fetcher
            config = self._get_email_config(email_account)
            fetcher = self.fetcher_factory(config)
            
            # Get site patterns
            patterns = VerificationExtractor.SITE_PATTERNS.get(site, {})
            
            # Wait for email
            message = await fetcher.fetch_verification_email(
                sender_patterns=patterns.get("sender", []),
                subject_patterns=patterns.get("subject", []),
                timeout_seconds=timeout_seconds,
            )
            
            if not message:
                return {"success": False, "error": "Verification email not received"}
            
            # Extract code
            code = self.extractor.extract_code(message, site)
            
            if not code:
                return {"success": False, "error": "Could not extract verification code"}
            
            # Enter code via callback
            entry_success = await code_entry_callback(code)
            
            return {
                "success": entry_success,
                "code": code,
                "email_subject": message.subject,
            }
            
        finally:
            await self.pool.release(email_account.id)
    
    async def verify_by_link(
        self,
        site: str,
        bot_account_id: str,
        timeout_seconds: int = 120,
    ) -> Dict[str, Any]:
        """Link-based verification"""
        
        # Get email account
        email_account = await self.pool.acquire(bot_account_id=bot_account_id)
        
        if not email_account:
            return {"success": False, "error": "No email account available"}
        
        try:
            # Create fetcher
            config = self._get_email_config(email_account)
            fetcher = self.fetcher_factory(config)
            
            # Get site patterns
            patterns = VerificationExtractor.SITE_PATTERNS.get(site, {})
            
            # Wait for email
            message = await fetcher.fetch_verification_email(
                sender_patterns=patterns.get("sender", []),
                subject_patterns=patterns.get("subject", []),
                timeout_seconds=timeout_seconds,
            )
            
            if not message:
                return {"success": False, "error": "Verification email not received"}
            
            # Extract link
            link = self.extractor.extract_link(message, site)
            
            if not link:
                return {"success": False, "error": "Could not extract verification link"}
            
            # Click link in browser
            click_success = await self._click_verification_link(link)
            
            return {
                "success": click_success,
                "link": link,
                "email_subject": message.subject,
            }
            
        finally:
            await self.pool.release(email_account.id)
    
    async def _click_verification_link(self, link: str) -> bool:
        """Verification link'i browser'da tıkla"""
        
        browser = await self.browser_factory()
        
        try:
            page = browser.page
            
            # Navigate to link
            await page.goto(link, wait_until="networkidle")
            
            # Check for success indicators
            content = await page.content()
            url = page.url
            
            success_indicators = [
                "verified",
                "confirmed",
                "success",
                "activated",
                "doğrulandı",
                "onaylandı",
            ]
            
            for indicator in success_indicators:
                if indicator in content.lower() or indicator in url.lower():
                    return True
            
            # Check for error indicators
            error_indicators = [
                "expired",
                "invalid",
                "error",
                "failed",
            ]
            
            for indicator in error_indicators:
                if indicator in content.lower():
                    return False
            
            # Ambiguous - assume success if no error
            return True
            
        finally:
            await browser.close()
    
    def _get_email_config(self, account: EmailAccount) -> EmailConfig:
        """Email config oluştur"""
        
        preset = PROVIDER_CONFIGS.get(account.provider, {})
        imap_settings = preset.get("imap", {})
        
        return EmailConfig(
            provider=account.provider,
            protocol=EmailProtocol.IMAP,
            host=imap_settings.get("host", ""),
            port=imap_settings.get("port", 993),
            use_ssl=imap_settings.get("ssl", True),
            email_address=account.email_address,
            password=account.encrypted_password,
            oauth_token=account.oauth_token,
        )
```

---

## Temporary Email Service Integration

```python
class TempMailService:
    """Temporary/disposable email service entegrasyonu"""
    
    # Supported services
    SERVICES = {
        "guerrillamail": {
            "api_url": "https://api.guerrillamail.com/ajax.php",
            "domain": "@guerrillamailblock.com",
        },
        "tempmail": {
            "api_url": "https://api.temp-mail.org",
            "domain": "@temp-mail.org",
        },
        "mailnator": {
            "api_url": "https://api.mailinator.com/api",
            "domain": "@mailinator.com",
        },
    }
    
    def __init__(self, service: str = "guerrillamail"):
        self.service = service
        self.config = self.SERVICES.get(service, {})
        self.client = httpx.AsyncClient()
        
        self.current_email: Optional[str] = None
        self.session_id: Optional[str] = None
    
    async def create_email(self) -> str:
        """Yeni temp email oluştur"""
        
        if self.service == "guerrillamail":
            response = await self.client.get(
                self.config["api_url"],
                params={"f": "get_email_address"}
            )
            
            data = response.json()
            self.current_email = data["email_addr"]
            self.session_id = data["sid_token"]
            
            return self.current_email
        
        # Add other service implementations
        raise NotImplementedError(f"Service {self.service} not implemented")
    
    async def check_inbox(
        self,
        sender_filter: Optional[str] = None,
        subject_filter: Optional[str] = None,
    ) -> List[EmailMessage]:
        """Inbox kontrol et"""
        
        if self.service == "guerrillamail":
            response = await self.client.get(
                self.config["api_url"],
                params={
                    "f": "check_email",
                    "sid_token": self.session_id,
                    "seq": 0,
                }
            )
            
            data = response.json()
            messages = []
            
            for email_data in data.get("list", []):
                # Apply filters
                if sender_filter and sender_filter.lower() not in email_data["mail_from"].lower():
                    continue
                
                if subject_filter and subject_filter.lower() not in email_data["mail_subject"].lower():
                    continue
                
                messages.append(EmailMessage(
                    message_id=email_data["mail_id"],
                    sender=email_data["mail_from"],
                    subject=email_data["mail_subject"],
                    body_text=email_data.get("mail_excerpt", ""),
                    body_html="",
                    received_at=datetime.utcnow(),
                ))
            
            return messages
        
        raise NotImplementedError(f"Service {self.service} not implemented")
    
    async def get_email_content(self, message_id: str) -> EmailMessage:
        """Email içeriğini al"""
        
        if self.service == "guerrillamail":
            response = await self.client.get(
                self.config["api_url"],
                params={
                    "f": "fetch_email",
                    "sid_token": self.session_id,
                    "email_id": message_id,
                }
            )
            
            data = response.json()
            
            return EmailMessage(
                message_id=message_id,
                sender=data["mail_from"],
                subject=data["mail_subject"],
                body_text=data.get("mail_body", ""),
                body_html=data.get("mail_body", ""),
                received_at=datetime.utcnow(),
            )
        
        raise NotImplementedError(f"Service {self.service} not implemented")
    
    async def close(self):
        """Cleanup"""
        await self.client.aclose()
```

---

## Kabul Kriterleri

| ID | Kriter | Test Yöntemi |
|----|--------|--------------|
| AC-001 | IMAP email fetch çalışmalı | Integration test |
| AC-002 | VFS verification code extraction %95+ | Pattern test |
| AC-003 | iDATA verification code extraction %95+ | Pattern test |
| AC-004 | Verification link click çalışmalı | E2E test |
| AC-005 | Email timeout handling doğru olmalı | Timeout test |
| AC-006 | Email pool rotation çalışmalı | Pool test |
| AC-007 | Temp mail service çalışmalı | Service test |

---

## Site-Specific Verification Flows

| Site | Method | Pattern | Timeout |
|------|--------|---------|---------|
| VFS | Code | 6-digit in email body | 120s |
| iDATA | Link | Confirmation URL | 120s |
| BLS | Code | 6-digit in email body | 120s |
| KKOSMOS | Code + Link | Both options | 180s |

---

## Sonraki Adımlar

1. **017-SMS-VERIFICATION.md** - SMS doğrulama
2. OAuth setup (Gmail, Outlook)
3. Site-specific pattern tuning
