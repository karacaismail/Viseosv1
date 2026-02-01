"""
VISE OS Applicant Module

Handles applicant profile management with PII encryption for GDPR/offshore compliance.
Applicant data is encrypted at rest and automatically redacted after 24 hours.

Components:
- encryption: Field-level PII encryption using Fernet
- service: Applicant management operations (future)

Usage:
    from src.core.applicant.encryption import PIIEncryptor, get_encryptor

    # Use the global encryptor (loads key from settings)
    encryptor = get_encryptor()
    encrypted = encryptor.encrypt("John Doe")
    decrypted = encryptor.decrypt(encrypted)

    # Or create a custom encryptor with a specific key
    custom_encryptor = PIIEncryptor(key=b"your_32_byte_key_here_12345678")
"""

from src.core.applicant.encryption import PIIEncryptor, get_encryptor

__all__ = [
    "PIIEncryptor",
    "get_encryptor",
]
