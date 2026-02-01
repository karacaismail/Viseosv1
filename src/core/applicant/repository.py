"""
Applicant Repository for data access.

This module provides a repository class for applicant CRUD operations.
It encapsulates all Directus API interactions for applicant-related data,
including automatic PII encryption/decryption.

CRITICAL: Applicant data contains PII. Per offshore compliance,
PII has a 24-hour retention period and is automatically redacted.

Based on 002-DIRECTUS-SCHEMA.md collections:
- applicants: Applicant profiles with encrypted PII

Usage:
    from src.core.applicant.repository import ApplicantRepository

    repo = ApplicantRepository()

    # Create applicant (PII is automatically encrypted)
    applicant = await repo.create({
        "agency_id": "...",
        "first_name": "John",
        "last_name": "Doe",
        ...
    })

    # Get applicant (PII is automatically decrypted)
    applicant = await repo.get_by_id("uuid-here")
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import structlog

from src.api.schemas.applicant import ApplicantStatus
from src.core.applicant.encryption import PII_FIELDS, get_encryptor
from src.integrations.directus import (
    COLLECTIONS,
    DirectusClient,
    DirectusFilter,
    get_directus_client,
)

logger = structlog.get_logger()


class ApplicantRepository:
    """
    Repository for applicant data access with automatic PII encryption.

    Provides CRUD operations and query methods for the applicants
    Directus collection. Automatically encrypts PII fields on write
    and decrypts on read.

    Encrypted PII Fields:
    - first_name
    - last_name
    - passport_number
    - phone
    - email

    Attributes:
        _client: DirectusClient instance for API calls.
        _logger: Structured logger for this repository.
        _encrypt_pii: Whether to encrypt/decrypt PII (default: True).

    Example:
        repo = ApplicantRepository()

        # Create applicant (PII encrypted automatically)
        applicant = await repo.create({
            "agency_id": "uuid-here",
            "first_name": "John",
            "last_name": "Doe",
            "birth_date": "1990-01-15",
            "nationality": "TR",
            "passport_number": "U12345678",
            "passport_expiry": "2030-05-20",
            "phone": "+905551234567",
            "target_country": "DE",
            "visa_type": "tourist",
        })

        # Get applicant (PII decrypted automatically)
        applicant = await repo.get_by_id(applicant["id"])
    """

    COLLECTION = COLLECTIONS.get("applicants", "applicants")

    # PII retention period (24 hours per spec)
    PII_RETENTION_HOURS = 24

    def __init__(
        self,
        client: DirectusClient | None = None,
        *,
        encrypt_pii: bool = True,
    ) -> None:
        """
        Initialize the repository with a Directus client.

        Args:
            client: Optional DirectusClient instance. If not provided,
                uses the global cached client.
            encrypt_pii: Whether to encrypt/decrypt PII fields.
                Set to False for testing or when using pre-encrypted data.
        """
        self._client = client or get_directus_client()
        self._logger = logger.bind(repository="applicant")
        self._encrypt_pii = encrypt_pii

    def _encrypt_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Encrypt PII fields in data.

        Args:
            data: Data dictionary with potential PII fields.

        Returns:
            Data with PII fields encrypted.
        """
        if not self._encrypt_pii:
            return data

        encryptor = get_encryptor()
        return encryptor.encrypt_dict(data, PII_FIELDS)

    def _decrypt_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Decrypt PII fields in data.

        Handles [REDACTED] values gracefully (doesn't try to decrypt).

        Args:
            data: Data dictionary with encrypted PII fields.

        Returns:
            Data with PII fields decrypted.
        """
        if not self._encrypt_pii:
            return data

        # Skip decryption for redacted data
        if data.get("first_name") == "[REDACTED]":
            return data

        encryptor = get_encryptor()
        return encryptor.decrypt_dict(data, PII_FIELDS)

    # -------------------------------------------------------------------------
    # CRUD Operations
    # -------------------------------------------------------------------------

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a new applicant with encrypted PII.

        Sets default values:
        - status: "pending"
        - expires_at: created_at + 24 hours

        Args:
            data: Applicant data (PII will be encrypted).

        Returns:
            Created applicant with PII decrypted for response.
        """
        # Set defaults
        data.setdefault("status", ApplicantStatus.PENDING.value)
        data.setdefault("exclude_weekends", False)

        # Set expiration (24 hours from now)
        now = datetime.utcnow()
        expires_at = now + timedelta(hours=self.PII_RETENTION_HOURS)
        data.setdefault("expires_at", expires_at.isoformat())

        self._logger.info(
            "applicant_create",
            agency_id=data.get("agency_id"),
            target_country=data.get("target_country"),
            visa_type=data.get("visa_type"),
        )

        # Encrypt PII fields
        encrypted_data = self._encrypt_data(data)

        result = await self._client.create_item(self.COLLECTION, encrypted_data)

        # Return decrypted data for convenience
        return self._decrypt_data(result) if result else result

    async def get_by_id(
        self,
        applicant_id: str | UUID,
        fields: list[str] | None = None,
        *,
        decrypt: bool = True,
    ) -> dict[str, Any] | None:
        """
        Get an applicant by ID.

        Args:
            applicant_id: The applicant UUID.
            fields: Optional list of fields to return.
            decrypt: Whether to decrypt PII fields (default: True).

        Returns:
            Applicant data with PII decrypted (if requested) or None if not found.
        """
        self._logger.debug("applicant_get", applicant_id=str(applicant_id))
        result = await self._client.get_item(
            self.COLLECTION,
            applicant_id,
            fields=fields,
        )

        if result and decrypt:
            return self._decrypt_data(result)
        return result

    async def update(
        self,
        applicant_id: str | UUID,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Update an applicant.

        Note: PII fields should not be updated after creation
        (per audit requirements). Only non-PII fields are encrypted.

        Args:
            applicant_id: The applicant UUID.
            data: Fields to update (non-PII only recommended).

        Returns:
            Updated applicant data with PII decrypted.
        """
        self._logger.info(
            "applicant_update",
            applicant_id=str(applicant_id),
            fields=list(data.keys()),
        )

        # Only encrypt if updating PII fields (not recommended but supported)
        update_data = self._encrypt_data(data)

        result = await self._client.update_item(self.COLLECTION, applicant_id, update_data)
        return self._decrypt_data(result) if result else result

    async def update_status(
        self,
        applicant_id: str | UUID,
        status: ApplicantStatus | str,
    ) -> dict[str, Any]:
        """
        Update an applicant's status.

        Args:
            applicant_id: The applicant UUID.
            status: New status value.

        Returns:
            Updated applicant data.
        """
        status_value = status.value if isinstance(status, ApplicantStatus) else status

        self._logger.info(
            "applicant_status_update",
            applicant_id=str(applicant_id),
            status=status_value,
        )

        result = await self._client.update_item(
            self.COLLECTION,
            applicant_id,
            {"status": status_value},
        )
        return self._decrypt_data(result) if result else result

    async def delete(self, applicant_id: str | UUID) -> None:
        """
        Delete an applicant.

        Note: Consider using soft_delete() instead for audit compliance.

        Args:
            applicant_id: The applicant UUID.
        """
        self._logger.info("applicant_delete", applicant_id=str(applicant_id))
        await self._client.delete_item(self.COLLECTION, applicant_id)

    async def soft_delete(self, applicant_id: str | UUID) -> dict[str, Any]:
        """
        Soft delete an applicant by redacting PII.

        Per GDPR/offshore compliance, this redacts PII fields rather than
        deleting the record entirely, maintaining audit trail.

        Args:
            applicant_id: The applicant UUID.

        Returns:
            Updated applicant data with redacted PII.
        """
        self._logger.info("applicant_soft_delete", applicant_id=str(applicant_id))

        redacted_data = {
            "first_name": "[REDACTED]",
            "last_name": "[REDACTED]",
            "passport_number": "[REDACTED]",
            "phone": "[REDACTED]",
            "email": "[REDACTED]",
            "deleted_at": datetime.utcnow().isoformat(),
        }

        # Don't encrypt [REDACTED] values
        return await self._client.update_item(
            self.COLLECTION,
            applicant_id,
            redacted_data,
        )

    # -------------------------------------------------------------------------
    # Query Methods
    # -------------------------------------------------------------------------

    async def find_by_status(
        self,
        status: ApplicantStatus | str,
        *,
        agency_id: str | UUID | None = None,
        limit: int | None = None,
        offset: int | None = None,
        decrypt: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Find applicants by status.

        Args:
            status: The status to filter by.
            agency_id: Optional agency ID filter.
            limit: Maximum number of results.
            offset: Number of results to skip.
            decrypt: Whether to decrypt PII fields.

        Returns:
            List of applicants matching the filter.
        """
        status_value = status.value if isinstance(status, ApplicantStatus) else status

        filter_builder = DirectusFilter().eq("status", status_value)
        if agency_id:
            filter_builder = filter_builder.raw(
                {"agency_id": {"_eq": str(agency_id)}}
            )

        # Exclude soft-deleted records
        filter_builder = filter_builder.raw({"deleted_at": {"_null": True}})

        results = await self._client.get_items(
            self.COLLECTION,
            filter=filter_builder,
            sort=["-created_at"],
            limit=limit,
            offset=offset,
        )

        if decrypt:
            return [self._decrypt_data(r) for r in results]
        return results

    async def find_by_agency(
        self,
        agency_id: str | UUID,
        *,
        status: ApplicantStatus | str | None = None,
        include_deleted: bool = False,
        limit: int | None = None,
        offset: int | None = None,
        decrypt: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Find applicants for an agency.

        Args:
            agency_id: The agency UUID.
            status: Optional status filter.
            include_deleted: Include soft-deleted applicants.
            limit: Maximum number of results.
            offset: Number of results to skip.
            decrypt: Whether to decrypt PII fields.

        Returns:
            List of applicants for the agency.
        """
        filter_builder = DirectusFilter().eq("agency_id", str(agency_id))

        if status:
            status_value = status.value if isinstance(status, ApplicantStatus) else status
            filter_builder = filter_builder.raw({"status": {"_eq": status_value}})

        if not include_deleted:
            filter_builder = filter_builder.raw({"deleted_at": {"_null": True}})

        results = await self._client.get_items(
            self.COLLECTION,
            filter=filter_builder,
            sort=["-created_at"],
            limit=limit,
            offset=offset,
        )

        if decrypt:
            return [self._decrypt_data(r) for r in results]
        return results

    async def find_pending_for_booking(
        self,
        *,
        agency_id: str | UUID | None = None,
        target_country: str | None = None,
        limit: int = 10,
        decrypt: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Find pending applicants ready for booking.

        Returns applicants in PENDING status that have not expired.

        Args:
            agency_id: Optional agency ID filter.
            target_country: Optional country filter.
            limit: Maximum number of results.
            decrypt: Whether to decrypt PII fields.

        Returns:
            List of pending applicants.
        """
        now = datetime.utcnow().isoformat()

        filter_dict: dict[str, Any] = {
            "_and": [
                {"status": {"_eq": ApplicantStatus.PENDING.value}},
                {"deleted_at": {"_null": True}},
                {"expires_at": {"_gt": now}},
            ]
        }

        if agency_id:
            filter_dict["_and"].append({"agency_id": {"_eq": str(agency_id)}})
        if target_country:
            filter_dict["_and"].append({"target_country": {"_eq": target_country}})

        results = await self._client.get_items(
            self.COLLECTION,
            filter=filter_dict,
            sort=["created_at"],  # FIFO - oldest first
            limit=limit,
        )

        if decrypt:
            return [self._decrypt_data(r) for r in results]
        return results

    async def find_expired(
        self,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Find applicants with expired PII that need redaction.

        Returns applicants where:
        - expires_at < now
        - deleted_at is null (not yet redacted)

        Args:
            limit: Maximum number of results.

        Returns:
            List of expired applicants (encrypted, not decrypted).
        """
        now = datetime.utcnow().isoformat()

        filter_dict = {
            "_and": [
                {"expires_at": {"_lt": now}},
                {"deleted_at": {"_null": True}},
            ]
        }

        # Don't decrypt - we're going to redact these anyway
        return await self._client.get_items(
            self.COLLECTION,
            filter=filter_dict,
            limit=limit,
        )

    async def find_by_family_group(
        self,
        family_group_id: str | UUID,
        *,
        decrypt: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Find all applicants in a family group.

        Args:
            family_group_id: The family group UUID.
            decrypt: Whether to decrypt PII fields.

        Returns:
            List of applicants in the family group.
        """
        results = await self._client.get_items(
            self.COLLECTION,
            filter={"family_group_id": {"_eq": str(family_group_id)}},
            sort=["created_at"],
        )

        if decrypt:
            return [self._decrypt_data(r) for r in results]
        return results

    async def count_by_status(
        self,
        status: ApplicantStatus | str,
        *,
        agency_id: str | UUID | None = None,
    ) -> int:
        """
        Count applicants by status.

        Args:
            status: The status to count.
            agency_id: Optional agency ID filter.

        Returns:
            Number of applicants matching the filter.
        """
        status_value = status.value if isinstance(status, ApplicantStatus) else status

        filter_builder = DirectusFilter().eq("status", status_value)
        filter_builder = filter_builder.raw({"deleted_at": {"_null": True}})

        if agency_id:
            filter_builder = filter_builder.raw(
                {"agency_id": {"_eq": str(agency_id)}}
            )

        return await self._client.count_items(self.COLLECTION, filter=filter_builder)

    # -------------------------------------------------------------------------
    # PII Cleanup Operations
    # -------------------------------------------------------------------------

    async def redact_expired_pii(self, *, batch_size: int = 100) -> int:
        """
        Redact PII for expired applicants.

        This implements the auto-cleanup job from 002-DIRECTUS-SCHEMA.md.
        Should be called by a scheduled task (e.g., hourly).

        Args:
            batch_size: Number of records to process per batch.

        Returns:
            Number of applicants redacted.
        """
        expired = await self.find_expired(limit=batch_size)

        if not expired:
            return 0

        redacted_count = 0
        for applicant in expired:
            try:
                await self.soft_delete(applicant["id"])
                redacted_count += 1
            except Exception as e:
                self._logger.error(
                    "pii_redaction_error",
                    applicant_id=applicant.get("id"),
                    error=str(e),
                )

        self._logger.info(
            "pii_redaction_complete",
            redacted_count=redacted_count,
            batch_size=batch_size,
        )

        return redacted_count

    async def extend_expiry(
        self,
        applicant_id: str | UUID,
        hours: int = 1,
    ) -> dict[str, Any]:
        """
        Extend PII expiry for an applicant.

        Used when applicant is in processing and needs more time.

        Args:
            applicant_id: The applicant UUID.
            hours: Number of hours to extend.

        Returns:
            Updated applicant data.
        """
        new_expiry = datetime.utcnow() + timedelta(hours=hours)

        self._logger.info(
            "applicant_expiry_extended",
            applicant_id=str(applicant_id),
            hours=hours,
            new_expiry=new_expiry.isoformat(),
        )

        result = await self._client.update_item(
            self.COLLECTION,
            applicant_id,
            {"expires_at": new_expiry.isoformat()},
        )
        return self._decrypt_data(result) if result else result


__all__ = [
    "ApplicantRepository",
]
