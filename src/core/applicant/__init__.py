"""
VISE OS Applicant Module

Handles applicant profile management with PII encryption for GDPR/offshore compliance.
Applicant data is encrypted at rest and automatically redacted after 24 hours.

Components:
- encryption: Field-level PII encryption using Fernet
- repository: ApplicantRepository for data access with automatic encryption
- service: ApplicantService for high-level business logic

Usage:
    from src.core.applicant import (
        PIIEncryptor,
        get_encryptor,
        ApplicantRepository,
        ApplicantService,
        get_applicant_service,
    )

    # Use the global encryptor (loads key from settings)
    encryptor = get_encryptor()
    encrypted = encryptor.encrypt("John Doe")
    decrypted = encryptor.decrypt(encrypted)

    # Use the repository (handles encryption automatically)
    repo = ApplicantRepository()
    applicant = await repo.create({...})  # PII encrypted automatically

    # Use the service (recommended for business logic)
    service = get_applicant_service()
    applicant = await service.create_applicant(...)
"""

from src.core.applicant.encryption import PIIEncryptor, get_encryptor
from src.core.applicant.repository import ApplicantRepository
from src.core.applicant.service import (
    ApplicantError,
    ApplicantEvent,
    ApplicantEventType,
    ApplicantExpiredError,
    ApplicantNotEligibleError,
    ApplicantNotFoundError,
    ApplicantService,
    ApplicantSummary,
    FamilyGroup,
    InvalidApplicantDataError,
    clear_applicant_service_cache,
    get_applicant_service,
)

__all__ = [
    # Encryption
    "PIIEncryptor",
    "get_encryptor",
    # Repository
    "ApplicantRepository",
    # Service
    "ApplicantService",
    "get_applicant_service",
    "clear_applicant_service_cache",
    # Data classes
    "ApplicantSummary",
    "FamilyGroup",
    "ApplicantEvent",
    "ApplicantEventType",
    # Exceptions
    "ApplicantError",
    "ApplicantNotFoundError",
    "ApplicantExpiredError",
    "ApplicantNotEligibleError",
    "InvalidApplicantDataError",
]
