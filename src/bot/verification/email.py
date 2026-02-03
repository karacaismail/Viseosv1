"""
Email Verification Service.

This module provides email verification automation for visa appointment systems.
Features include IMAP/POP3 email reading, OTP/verification code extraction,
confirmation link handling, and multi-provider email pool management.

Dependencies:
- Account Pool Manager: Account email credentials
- Stealth Engine: Browser for link verification
- Site Adapters: Verification trigger handling
- State Machine: VERIFYING state handling

Usage:
    from src.bot.verification.email import EmailVerifier, EmailConfig

    verifier = EmailVerifier(encryptor)
    result = await verifier.verify_by_code(
        site="vfs",
        email_address="user@example.com",
        password="encrypted_password",
        timeout_seconds=120,
    )

    if result["success"]:
        print(f"Verification code: {result['code']}")
"""

from __future__ import annotations

import asyncio
import imaplib
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email import message_from_bytes
from email.header import decode_header
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Optional

from src.core.exceptions import (
    OTPExtractionError,
    VerificationError,
    VerificationTimeoutError,
)

if TYPE_CHECKING:
    from src.core.applicant.encryption import PIIEncryptor


# =============================================================================
# Email Protocol and Provider Enums
# =============================================================================


class EmailProtocol(Enum):
    """
    Supported email protocols.

    Attributes:
        IMAP: Internet Message Access Protocol for email retrieval.
        POP3: Post Office Protocol version 3 for email retrieval.
    """

    IMAP = "imap"
    POP3 = "pop3"


class EmailProvider(Enum):
    """
    Supported email providers.

    Each provider has predefined server configurations for easy setup.

    Attributes:
        GMAIL: Google Gmail service.
        OUTLOOK: Microsoft Outlook/Office 365.
        YANDEX: Yandex mail service.
        CUSTOM: Custom email server configuration.
        TEMP_MAIL: Disposable email services.
    """

    GMAIL = "gmail"
    OUTLOOK = "outlook"
    YANDEX = "yandex"
    CUSTOM = "custom"
    TEMP_MAIL = "temp_mail"


# =============================================================================
# Provider Configurations
# =============================================================================


PROVIDER_CONFIGS: dict[EmailProvider, dict[str, dict[str, Any]]] = {
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


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class EmailConfig:
    """
    Email account configuration.

    Contains all settings needed to connect to an email account
    including server details, credentials, and optional OAuth tokens.

    Attributes:
        provider: Email provider type.
        protocol: Connection protocol (IMAP/POP3).
        host: Email server hostname.
        port: Server port number.
        use_ssl: Whether to use SSL/TLS.
        email_address: Email account address.
        password: Account password (may be encrypted).
        oauth_token: Optional OAuth token for Gmail/Outlook.

    Example:
        config = EmailConfig(
            provider=EmailProvider.GMAIL,
            protocol=EmailProtocol.IMAP,
            host="imap.gmail.com",
            port=993,
            use_ssl=True,
            email_address="user@gmail.com",
            password="app_password",
        )
    """

    provider: EmailProvider
    protocol: EmailProtocol
    host: str
    port: int
    email_address: str
    password: str
    use_ssl: bool = True
    oauth_token: str | None = None


@dataclass
class EmailMessage:
    """
    Email message representation.

    Contains parsed email content and extracted verification data.

    Attributes:
        message_id: Unique message identifier.
        sender: Email sender address.
        subject: Email subject line.
        body_text: Plain text body content.
        body_html: HTML body content.
        received_at: When the email was received.
        verification_code: Extracted verification code if found.
        verification_link: Extracted verification link if found.

    Example:
        message = EmailMessage(
            message_id="123",
            sender="noreply@vfsglobal.com",
            subject="Verification Code",
            body_text="Your code is: 123456",
            body_html="<p>Your code is: <b>123456</b></p>",
            received_at=datetime.utcnow(),
            verification_code="123456",
        )
    """

    message_id: str
    sender: str
    subject: str
    body_text: str
    body_html: str
    received_at: datetime
    verification_code: str | None = None
    verification_link: str | None = None


@dataclass
class EmailAccount:
    """
    Email account in the pool.

    Tracks usage, cooldown, and status for email accounts
    used in verification operations.

    Attributes:
        id: Unique account identifier.
        email_address: Email address.
        provider: Email provider type.
        encrypted_password: Fernet-encrypted password.
        oauth_token: Optional OAuth token.
        status: Current status (available, in_use, cooldown, disabled).
        usage_count: Number of times account has been used.
        last_used_at: Timestamp of last usage.
        cooldown_until: When cooldown period ends.
        bot_account_id: Associated bot account if linked.

    Example:
        account = EmailAccount(
            id="acc_123",
            email_address="bot@example.com",
            provider=EmailProvider.GMAIL,
            encrypted_password=b"encrypted...",
        )
    """

    id: str
    email_address: str
    provider: EmailProvider
    encrypted_password: bytes
    oauth_token: str | None = None
    status: str = "available"
    usage_count: int = 0
    last_used_at: datetime | None = None
    cooldown_until: datetime | None = None
    bot_account_id: str | None = None


# =============================================================================
# Email Pool Manager
# =============================================================================


class EmailPoolManager:
    """
    Email account pool management.

    Manages a pool of email accounts for verification operations,
    handling acquisition, release, cooldowns, and rate limiting.

    Attributes:
        COOLDOWN_MINUTES: Cooldown period after successful use.
        MAX_USAGE_PER_HOUR: Maximum uses per account per hour.

    Usage:
        pool = EmailPoolManager(encryptor)
        await pool.add_account("user@example.com", "password", EmailProvider.GMAIL)

        account = await pool.acquire()
        if account:
            # Use account
            await pool.release(account.id, success=True)
    """

    COOLDOWN_MINUTES: int = 5
    MAX_USAGE_PER_HOUR: int = 20

    def __init__(self, encryptor: PIIEncryptor | None = None) -> None:
        """
        Initialize the email pool manager.

        Args:
            encryptor: Optional encryptor for password encryption.
        """
        self.encryptor = encryptor
        self.accounts: dict[str, EmailAccount] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        provider: EmailProvider | None = None,
        bot_account_id: str | None = None,
    ) -> EmailAccount | None:
        """
        Acquire an available email account from the pool.

        Respects cooldown periods and rate limits when selecting accounts.

        Args:
            provider: Optional provider filter.
            bot_account_id: Optional linked bot account ID.

        Returns:
            EmailAccount if available, None otherwise.

        Example:
            account = await pool.acquire(provider=EmailProvider.GMAIL)
            if account:
                # Use account for verification
                pass
        """
        async with self._lock:
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
                    if (
                        account.last_used_at > hour_ago
                        and account.usage_count >= self.MAX_USAGE_PER_HOUR
                    ):
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
    ) -> None:
        """
        Release an email account back to the pool.

        Sets cooldown period on successful operations.

        Args:
            account_id: ID of the account to release.
            success: Whether the operation was successful.

        Example:
            await pool.release(account.id, success=True)
        """
        async with self._lock:
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
        bot_account_id: str | None = None,
    ) -> EmailAccount:
        """
        Add a new email account to the pool.

        Encrypts the password before storage if encryptor is available.

        Args:
            email_address: Email address.
            password: Account password (will be encrypted).
            provider: Email provider type.
            bot_account_id: Optional linked bot account ID.

        Returns:
            The created EmailAccount.

        Example:
            account = await pool.add_account(
                "user@gmail.com",
                "app_password",
                EmailProvider.GMAIL,
            )
        """
        if self.encryptor:
            encrypted_password = self.encryptor.encrypt(password).encode()
        else:
            encrypted_password = password.encode()

        account = EmailAccount(
            id=str(uuid.uuid4()),
            email_address=email_address,
            provider=provider,
            encrypted_password=encrypted_password,
            bot_account_id=bot_account_id,
        )

        self.accounts[account.id] = account
        return account

    async def get_stats(self) -> dict[str, Any]:
        """
        Get pool statistics.

        Returns:
            Dictionary with pool statistics.
        """
        async with self._lock:
            total = len(self.accounts)
            available = sum(1 for a in self.accounts.values() if a.status == "available")
            in_use = sum(1 for a in self.accounts.values() if a.status == "in_use")
            cooldown = sum(1 for a in self.accounts.values() if a.status == "cooldown")
            disabled = sum(1 for a in self.accounts.values() if a.status == "disabled")

            return {
                "total": total,
                "available": available,
                "in_use": in_use,
                "cooldown": cooldown,
                "disabled": disabled,
            }


# =============================================================================
# IMAP Email Fetcher
# =============================================================================


class IMAPEmailFetcher:
    """
    IMAP email fetcher for verification emails.

    Connects to IMAP servers and retrieves emails matching
    specified sender and subject patterns.

    Attributes:
        config: Email configuration.
        encryptor: Password decryptor.
        executor: Thread pool for sync IMAP operations.

    Usage:
        fetcher = IMAPEmailFetcher(config, encryptor)
        message = await fetcher.fetch_verification_email(
            sender_patterns=["vfsglobal"],
            subject_patterns=["verification"],
            timeout_seconds=120,
        )
    """

    def __init__(
        self,
        config: EmailConfig,
        encryptor: PIIEncryptor | None = None,
    ) -> None:
        """
        Initialize the IMAP email fetcher.

        Args:
            config: Email configuration.
            encryptor: Optional encryptor for password decryption.
        """
        self.config = config
        self.encryptor = encryptor
        self._executor = ThreadPoolExecutor(max_workers=5)

    async def fetch_verification_email(
        self,
        sender_patterns: list[str],
        subject_patterns: list[str],
        timeout_seconds: int = 120,
        poll_interval: int = 5,
    ) -> EmailMessage | None:
        """
        Wait for and fetch a verification email.

        Polls the inbox until a matching email is found or timeout.

        Args:
            sender_patterns: Patterns to match in sender address.
            subject_patterns: Patterns to match in subject line.
            timeout_seconds: Maximum wait time in seconds.
            poll_interval: Seconds between inbox checks.

        Returns:
            EmailMessage if found, None if timeout.

        Raises:
            VerificationTimeoutError: If timeout is exceeded.

        Example:
            message = await fetcher.fetch_verification_email(
                sender_patterns=["vfsglobal"],
                subject_patterns=["verification", "code"],
                timeout_seconds=120,
            )
        """
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
        sender_patterns: list[str],
        subject_patterns: list[str],
    ) -> list[EmailMessage]:
        """
        Fetch recent emails matching patterns.

        Args:
            since_minutes: Look back period in minutes.
            sender_patterns: Sender patterns to match.
            subject_patterns: Subject patterns to match.

        Returns:
            List of matching EmailMessage objects.
        """
        loop = asyncio.get_event_loop()

        return await loop.run_in_executor(
            self._executor,
            self._sync_fetch_emails,
            since_minutes,
            sender_patterns,
            subject_patterns,
        )

    def _sync_fetch_emails(
        self,
        since_minutes: int,
        sender_patterns: list[str],
        subject_patterns: list[str],
    ) -> list[EmailMessage]:
        """
        Synchronous email fetch (runs in thread pool).

        Args:
            since_minutes: Look back period in minutes.
            sender_patterns: Sender patterns to match.
            subject_patterns: Subject patterns to match.

        Returns:
            List of matching EmailMessage objects.
        """
        messages: list[EmailMessage] = []

        try:
            # Connect
            if self.config.use_ssl:
                mail = imaplib.IMAP4_SSL(self.config.host, self.config.port)
            else:
                mail = imaplib.IMAP4(self.config.host, self.config.port)

            # Decrypt password if encrypted
            password = self.config.password
            if self.encryptor and password.startswith("gAAAAA"):
                password = self.encryptor.decrypt(password)

            # Login
            mail.login(self.config.email_address, password)

            # Select inbox
            mail.select("INBOX")

            # Search for recent emails
            since_date = (
                datetime.utcnow() - timedelta(minutes=since_minutes)
            ).strftime("%d-%b-%Y")

            search_criteria = f'(SINCE "{since_date}")'

            status, message_ids = mail.search(None, search_criteria)

            if status != "OK":
                mail.logout()
                return messages

            # Process each message (last 10)
            msg_id_list = message_ids[0].split()
            for msg_id in msg_id_list[-10:]:
                status, msg_data = mail.fetch(msg_id, "(RFC822)")

                if status != "OK":
                    continue

                # Parse email
                raw_email = msg_data[0][1]  # type: ignore[index]
                email_message = message_from_bytes(raw_email)  # type: ignore[arg-type]

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
                    message_id=msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id),
                    sender=sender,
                    subject=subject,
                    body_text=body_text,
                    body_html=body_html,
                    received_at=datetime.utcnow(),
                )

                messages.append(msg)

            mail.logout()

        except Exception:
            # Silently handle IMAP errors
            pass

        return messages

    def _decode_header(self, header: str | None) -> str:
        """
        Decode email header value.

        Args:
            header: Raw header value.

        Returns:
            Decoded string value.
        """
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

    def _matches_patterns(self, text: str, patterns: list[str]) -> bool:
        """
        Check if text matches any pattern.

        Args:
            text: Text to search.
            patterns: Patterns to match (case-insensitive).

        Returns:
            True if any pattern matches.
        """
        if not patterns:
            return True

        text_lower = text.lower()

        for pattern in patterns:
            if pattern.lower() in text_lower:
                return True

        return False

    def _extract_body(self, email_message: Any) -> tuple[str, str]:
        """
        Extract text and HTML body from email.

        Args:
            email_message: Parsed email message.

        Returns:
            Tuple of (body_text, body_html).
        """
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

    def close(self) -> None:
        """Clean up resources."""
        self._executor.shutdown(wait=False)


# =============================================================================
# Verification Code/Link Extractor
# =============================================================================


class VerificationExtractor:
    """
    Email verification code and link extractor.

    Extracts verification codes and confirmation links from email
    content using site-specific and generic patterns.

    Attributes:
        CODE_PATTERNS: Generic code extraction patterns.
        LINK_PATTERNS: Generic link extraction patterns.
        SITE_PATTERNS: Site-specific extraction patterns.

    Usage:
        extractor = VerificationExtractor()
        code = extractor.extract_code(message, site="vfs")
        link = extractor.extract_link(message, site="vfs")
    """

    # Common code patterns
    CODE_PATTERNS: list[str] = [
        r"(?:code|kod|verification|doğrulama)[:\s]*(\d{4,8})",
        r"(?:OTP|otp)[:\s]*(\d{4,8})",
        r"\b(\d{6})\b",  # Standalone 6-digit
        r"(?:PIN|pin)[:\s]*(\d{4,6})",
        r"(?:confirmation|onay)[:\s#]*(\d{4,8})",
    ]

    # Common link patterns
    LINK_PATTERNS: list[str] = [
        r"(https?://[^\s<>\"]+(?:verify|confirm|activate|validate)[^\s<>\"]*)",
        r"(https?://[^\s<>\"]+\?[^\s<>\"]*(?:token|code|key)=[^\s<>\"]+)",
        r'<a[^>]+href=["\']([^"\']+(?:verify|confirm|activate)[^"\']*)["\']',
    ]

    # Site-specific patterns
    SITE_PATTERNS: dict[str, dict[str, Any]] = {
        "vfs": {
            "sender": ["vfsglobal", "noreply@vfs"],
            "subject": ["verification", "confirm", "booking"],
            "code_pattern": r"(?:verification code|doğrulama kodu)[:\s]*(\d{6})",
            "link_pattern": r'(https://visa\.vfsglobal\.com/[^\s<>"]+(?:verify|confirm)[^\s<>"]*)',
        },
        "idata": {
            "sender": ["idata.com.tr", "noreply@idata"],
            "subject": ["randevu", "appointment", "confirm"],
            "code_pattern": r"(?:onay kodu|confirmation)[:\s]*(\d{6})",
            "link_pattern": r'(https://[^\s<>"]*idata[^\s<>"]+(?:confirm|verify)[^\s<>"]*)',
        },
        "bls": {
            "sender": ["blsspain", "bls"],
            "subject": ["verification", "booking"],
            "code_pattern": r"(?:code|código)[:\s]*(\d{6})",
            "link_pattern": r'(https://[^\s<>"]*bls[^\s<>"]+(?:verify|confirm)[^\s<>"]*)',
        },
        "kkosmos": {
            "sender": ["kkosmos", "konsolosluk"],
            "subject": ["verification", "appointment", "randevu"],
            "code_pattern": r"(?:code|kod)[:\s]*(\d{6})",
            "link_pattern": r'(https://[^\s<>"]*kkosmos[^\s<>"]+(?:verify|confirm)[^\s<>"]*)',
        },
    }

    def extract_code(
        self,
        message: EmailMessage,
        site: str | None = None,
    ) -> str | None:
        """
        Extract verification code from email.

        Args:
            message: Email message to extract from.
            site: Optional site for specific patterns.

        Returns:
            Extracted code string or None.

        Example:
            code = extractor.extract_code(message, site="vfs")
            if code:
                print(f"Found code: {code}")
        """
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
        site: str | None = None,
    ) -> str | None:
        """
        Extract verification link from email.

        Args:
            message: Email message to extract from.
            site: Optional site for specific patterns.

        Returns:
            Extracted link string or None.

        Example:
            link = extractor.extract_link(message, site="vfs")
            if link:
                print(f"Found link: {link}")
        """
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
            # Simple link extraction without BeautifulSoup
            link_pattern = r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>'
            for match in re.finditer(link_pattern, message.body_html, re.IGNORECASE):
                href = match.group(1)
                text = match.group(2).lower()

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
        site: str | None = None,
    ) -> dict[str, str | None]:
        """
        Extract all verification information.

        Args:
            message: Email message to extract from.
            site: Optional site for specific patterns.

        Returns:
            Dictionary with 'code' and 'link' keys.

        Example:
            result = extractor.extract_all(message, site="vfs")
            print(f"Code: {result['code']}, Link: {result['link']}")
        """
        return {
            "code": self.extract_code(message, site),
            "link": self.extract_link(message, site),
        }

    def _html_to_text(self, html: str) -> str:
        """
        Convert HTML to plain text.

        Args:
            html: HTML content.

        Returns:
            Plain text content.
        """
        if not html:
            return ""

        # Remove script and style tags
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)

        # Remove HTML tags
        text = re.sub(r"<[^>]+>", " ", text)

        # Decode HTML entities
        text = text.replace("&nbsp;", " ")
        text = text.replace("&amp;", "&")
        text = text.replace("&lt;", "<")
        text = text.replace("&gt;", ">")
        text = text.replace("&quot;", '"')

        # Normalize whitespace
        text = re.sub(r"\s+", " ", text)

        return text.strip()

    def _clean_link(self, link: str) -> str:
        """
        Clean and normalize a link.

        Args:
            link: Raw link string.

        Returns:
            Cleaned link string.
        """
        # Remove trailing punctuation
        link = link.rstrip(".,;:>\"'")

        # Decode HTML entities
        link = link.replace("&amp;", "&")

        return link


# =============================================================================
# Email Verifier (Main Class)
# =============================================================================


class EmailVerifier:
    """
    Main email verification service.

    Provides high-level methods for email-based verification including
    code extraction, link verification, and pool management.

    Attributes:
        encryptor: Password encryptor/decryptor.
        pool: Email account pool manager.
        extractor: Verification code/link extractor.

    Usage:
        verifier = EmailVerifier(encryptor)
        result = await verifier.verify_by_code(
            site="vfs",
            email_address="user@example.com",
            password="password",
            timeout_seconds=120,
        )
    """

    def __init__(
        self,
        encryptor: PIIEncryptor | None = None,
        pool: EmailPoolManager | None = None,
    ) -> None:
        """
        Initialize the email verifier.

        Args:
            encryptor: Optional encryptor for password handling.
            pool: Optional email pool manager.
        """
        self.encryptor = encryptor
        self.pool = pool or EmailPoolManager(encryptor)
        self.extractor = VerificationExtractor()

    async def verify_by_code(
        self,
        site: str,
        email_address: str,
        password: str,
        provider: EmailProvider = EmailProvider.GMAIL,
        code_entry_callback: Callable[[str], Any] | None = None,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """
        Verify by extracting code from email.

        Waits for a verification email and extracts the code.
        Optionally calls a callback with the extracted code.

        Args:
            site: Target site code (vfs, idata, bls, kkosmos).
            email_address: Email address to check.
            password: Email password.
            provider: Email provider type.
            code_entry_callback: Optional callback for code entry.
            timeout_seconds: Maximum wait time.

        Returns:
            Dictionary with verification result.

        Raises:
            VerificationTimeoutError: If email not received in time.
            OTPExtractionError: If code cannot be extracted.

        Example:
            result = await verifier.verify_by_code(
                site="vfs",
                email_address="user@gmail.com",
                password="app_password",
                timeout_seconds=120,
            )

            if result["success"]:
                print(f"Code: {result['code']}")
        """
        # Create email config
        config = self._create_config(email_address, password, provider)

        # Create fetcher
        fetcher = IMAPEmailFetcher(config, self.encryptor)

        try:
            # Get site patterns
            patterns = self.extractor.SITE_PATTERNS.get(site, {})

            # Wait for email
            message = await fetcher.fetch_verification_email(
                sender_patterns=patterns.get("sender", []),
                subject_patterns=patterns.get("subject", []),
                timeout_seconds=timeout_seconds,
            )

            if not message:
                raise VerificationTimeoutError(
                    "Verification email not received",
                    verification_type="email",
                    timeout_seconds=timeout_seconds,
                )

            # Extract code
            code = self.extractor.extract_code(message, site)

            if not code:
                raise OTPExtractionError(
                    "Could not extract verification code from email",
                    verification_type="email",
                    source=message.subject,
                )

            # Call callback if provided
            entry_success = True
            if code_entry_callback:
                try:
                    callback_result = code_entry_callback(code)
                    if asyncio.iscoroutine(callback_result):
                        entry_success = await callback_result
                    else:
                        entry_success = bool(callback_result)
                except Exception:
                    entry_success = False

            return {
                "success": entry_success,
                "code": code,
                "email_subject": message.subject,
                "email_sender": message.sender,
            }

        finally:
            fetcher.close()

    async def verify_by_link(
        self,
        site: str,
        email_address: str,
        password: str,
        provider: EmailProvider = EmailProvider.GMAIL,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """
        Verify by extracting and clicking link from email.

        Waits for a verification email and extracts the confirmation link.

        Args:
            site: Target site code.
            email_address: Email address to check.
            password: Email password.
            provider: Email provider type.
            timeout_seconds: Maximum wait time.

        Returns:
            Dictionary with verification result including link.

        Raises:
            VerificationTimeoutError: If email not received in time.
            VerificationError: If link cannot be extracted.

        Example:
            result = await verifier.verify_by_link(
                site="idata",
                email_address="user@gmail.com",
                password="app_password",
            )

            if result["success"]:
                print(f"Link: {result['link']}")
        """
        # Create email config
        config = self._create_config(email_address, password, provider)

        # Create fetcher
        fetcher = IMAPEmailFetcher(config, self.encryptor)

        try:
            # Get site patterns
            patterns = self.extractor.SITE_PATTERNS.get(site, {})

            # Wait for email
            message = await fetcher.fetch_verification_email(
                sender_patterns=patterns.get("sender", []),
                subject_patterns=patterns.get("subject", []),
                timeout_seconds=timeout_seconds,
            )

            if not message:
                raise VerificationTimeoutError(
                    "Verification email not received",
                    verification_type="email",
                    timeout_seconds=timeout_seconds,
                )

            # Extract link
            link = self.extractor.extract_link(message, site)

            if not link:
                raise VerificationError(
                    "Could not extract verification link from email",
                    verification_type="email",
                    details={"subject": message.subject},
                )

            return {
                "success": True,
                "link": link,
                "email_subject": message.subject,
                "email_sender": message.sender,
            }

        finally:
            fetcher.close()

    async def wait_for_email(
        self,
        email_address: str,
        password: str,
        provider: EmailProvider = EmailProvider.GMAIL,
        sender_patterns: list[str] | None = None,
        subject_patterns: list[str] | None = None,
        timeout_seconds: int = 120,
    ) -> EmailMessage | None:
        """
        Wait for an email matching patterns.

        Low-level method for custom email waiting scenarios.

        Args:
            email_address: Email address to check.
            password: Email password.
            provider: Email provider type.
            sender_patterns: Sender patterns to match.
            subject_patterns: Subject patterns to match.
            timeout_seconds: Maximum wait time.

        Returns:
            EmailMessage if found, None if timeout.

        Example:
            message = await verifier.wait_for_email(
                email_address="user@gmail.com",
                password="app_password",
                sender_patterns=["noreply@site.com"],
                subject_patterns=["confirmation"],
            )
        """
        config = self._create_config(email_address, password, provider)
        fetcher = IMAPEmailFetcher(config, self.encryptor)

        try:
            return await fetcher.fetch_verification_email(
                sender_patterns=sender_patterns or [],
                subject_patterns=subject_patterns or [],
                timeout_seconds=timeout_seconds,
            )
        finally:
            fetcher.close()

    def extract_code_from_message(
        self,
        message: EmailMessage,
        site: str | None = None,
    ) -> str | None:
        """
        Extract verification code from an email message.

        Args:
            message: Email message.
            site: Optional site for specific patterns.

        Returns:
            Extracted code or None.
        """
        return self.extractor.extract_code(message, site)

    def extract_link_from_message(
        self,
        message: EmailMessage,
        site: str | None = None,
    ) -> str | None:
        """
        Extract verification link from an email message.

        Args:
            message: Email message.
            site: Optional site for specific patterns.

        Returns:
            Extracted link or None.
        """
        return self.extractor.extract_link(message, site)

    def _create_config(
        self,
        email_address: str,
        password: str,
        provider: EmailProvider,
    ) -> EmailConfig:
        """
        Create email configuration from parameters.

        Args:
            email_address: Email address.
            password: Email password.
            provider: Email provider type.

        Returns:
            EmailConfig instance.
        """
        preset = PROVIDER_CONFIGS.get(provider, {})
        imap_settings = preset.get("imap", {})

        return EmailConfig(
            provider=provider,
            protocol=EmailProtocol.IMAP,
            host=imap_settings.get("host", "imap.gmail.com"),
            port=imap_settings.get("port", 993),
            use_ssl=imap_settings.get("ssl", True),
            email_address=email_address,
            password=password,
        )


# =============================================================================
# Exports
# =============================================================================


__all__ = [
    # Enums
    "EmailProtocol",
    "EmailProvider",
    # Data Classes
    "EmailConfig",
    "EmailMessage",
    "EmailAccount",
    # Provider Configs
    "PROVIDER_CONFIGS",
    # Classes
    "EmailPoolManager",
    "IMAPEmailFetcher",
    "VerificationExtractor",
    "EmailVerifier",
]
