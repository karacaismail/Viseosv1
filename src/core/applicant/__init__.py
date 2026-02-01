"""
VISE OS Applicant Module

Handles applicant profile management with PII encryption for GDPR/offshore compliance.
Applicant data is encrypted at rest and automatically redacted after 24 hours.

Components:
- encryption: Field-level PII encryption using Fernet
- repository: ApplicantRepository for data access with automatic encryption

Usage:
    from src.core.applicant import PIIEncryptor, get_encryptor, ApplicantRepository

    # Use the global encryptor (loads key from settings)
    encryptor = get_encryptor()
    encrypted = encryptor.encrypt("John Doe")
    decrypted = encryptor.decrypt(encrypted)

    # Use the repository (handles encryption automatically)
    repo = ApplicantRepository()
    applicant = await repo.create({...})  # PII encrypted automatically
"""

from src.core.applicant.encryption import PIIEncryptor, get_encryptor
from src.core.applicant.repository import ApplicantRepository

__all__ = [
    "PIIEncryptor",
    "get_encryptor",
    "ApplicantRepository",
]
