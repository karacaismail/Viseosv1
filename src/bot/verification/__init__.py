"""
VISE OS Verification Module.

Provides email and SMS verification automation for visa appointment systems.
Handles IMAP/POP3 email reading, OTP extraction, confirmation link handling,
and multi-provider account pool management.

Components:
- email: Email verification via IMAP/POP3
- sms: SMS verification (future implementation)

Usage:
    from src.bot.verification import EmailVerifier, EmailProvider

    verifier = EmailVerifier()
    result = await verifier.verify_by_code(
        site="vfs",
        email_address="user@gmail.com",
        password="app_password",
    )
"""

from src.bot.verification.email import (
    EmailAccount,
    EmailConfig,
    EmailMessage,
    EmailPoolManager,
    EmailProtocol,
    EmailProvider,
    EmailVerifier,
    IMAPEmailFetcher,
    PROVIDER_CONFIGS,
    VerificationExtractor,
)

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
