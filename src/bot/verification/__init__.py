"""
VISE OS Verification Module.

Provides email and SMS verification automation for visa appointment systems.
Handles IMAP/POP3 email reading, OTP extraction, confirmation link handling,
virtual phone number management, and multi-provider pool management.

Components:
- email: Email verification via IMAP/POP3
- sms: SMS verification via virtual phone providers (5sim, SMSHub, SMS-Activate)

Usage:
    # Email verification
    from src.bot.verification import EmailVerifier, EmailProvider

    verifier = EmailVerifier()
    result = await verifier.verify_by_code(
        site="vfs",
        email_address="user@gmail.com",
        password="app_password",
    )

    # SMS verification
    from src.bot.verification import SMSVerifier, SMSProvider, SMSProviderConfig

    verifier = SMSVerifier()
    await verifier.initialize({
        SMSProvider.FIVE_SIM: SMSProviderConfig(
            provider=SMSProvider.FIVE_SIM,
            api_key="your_api_key",
            base_url="https://5sim.net/v1",
        )
    })

    result = await verifier.verify_by_sms(
        country="tr",
        service="vfs",
        phone_entry_callback=enter_phone,
        code_entry_callback=enter_code,
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
from src.bot.verification.sms import (
    FiveSimClient,
    HumanEscalationService,
    PhonePoolManager,
    PhoneStatus,
    SMSActivateClient,
    SMSCodeExtractor,
    SMSHubClient,
    SMSProvider,
    SMSProviderClient,
    SMSProviderConfig,
    SMSVerificationExecutor,
    SMSVerifier,
    VirtualPhone,
)

__all__ = [
    # Email Enums
    "EmailProtocol",
    "EmailProvider",
    # Email Data Classes
    "EmailConfig",
    "EmailMessage",
    "EmailAccount",
    # Email Provider Configs
    "PROVIDER_CONFIGS",
    # Email Classes
    "EmailPoolManager",
    "IMAPEmailFetcher",
    "VerificationExtractor",
    "EmailVerifier",
    # SMS Enums
    "SMSProvider",
    "PhoneStatus",
    # SMS Data Classes
    "VirtualPhone",
    "SMSProviderConfig",
    # SMS Code Extractor
    "SMSCodeExtractor",
    # SMS Provider Clients
    "SMSProviderClient",
    "FiveSimClient",
    "SMSHubClient",
    "SMSActivateClient",
    # SMS Pool Manager
    "PhonePoolManager",
    # SMS Verification Executor
    "SMSVerificationExecutor",
    # SMS Human Escalation
    "HumanEscalationService",
    # SMS Main Interface
    "SMSVerifier",
]
