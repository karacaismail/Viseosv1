"""
PII Field-Level Encryption Service.

This module provides Fernet-based encryption for Personally Identifiable Information (PII)
stored in the applicants collection. All PII fields are encrypted at rest and decrypted
only when needed for booking operations.

Encrypted Fields (per 002-DIRECTUS-SCHEMA.md):
- first_name
- last_name
- passport_number
- phone
- email

Security Features:
- AES-128-CBC encryption with HMAC-SHA256 authentication (Fernet)
- URL-safe base64 encoding for storage compatibility
- Per-encryption unique IV (initialization vector)
- Key loaded from ENCRYPTION_KEY environment variable

Usage:
    from src.core.applicant.encryption import PIIEncryptor, get_encryptor

    # Using global encryptor (recommended)
    encryptor = get_encryptor()
    encrypted = encryptor.encrypt("John Doe")
    original = encryptor.decrypt(encrypted)

    # Using custom key
    key = b"your_32_byte_encryption_key_here"
    encryptor = PIIEncryptor(key)
    encrypted = encryptor.encrypt("secret data")
"""

import base64
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from src.core.exceptions import ViseOSError


class EncryptionError(ViseOSError):
    """
    Base exception for encryption-related errors.

    Raised when encryption or decryption operations fail.

    Attributes:
        operation: The operation that failed (encrypt, decrypt).
    """

    def __init__(
        self,
        message: str = "Encryption error occurred",
        *,
        operation: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.operation = operation
        details = details or {}
        if operation:
            details["operation"] = operation
        super().__init__(message, code=code or "ENCRYPTION_ERROR", details=details)


class InvalidKeyError(EncryptionError):
    """
    Raised when an invalid encryption key is provided.

    The key must be exactly 32 bytes for Fernet compatibility.
    """

    def __init__(
        self,
        message: str = "Invalid encryption key",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code="INVALID_KEY", details=details)


class DecryptionError(EncryptionError):
    """
    Raised when decryption fails.

    This may indicate tampered data, wrong key, or corrupted ciphertext.
    """

    def __init__(
        self,
        message: str = "Failed to decrypt data",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message, operation="decrypt", code="DECRYPTION_ERROR", details=details
        )


class PIIEncryptor:
    """
    PII field-level encryptor using Fernet symmetric encryption.

    Fernet provides authenticated encryption using AES-128-CBC with
    HMAC-SHA256 for integrity verification. Each encryption operation
    uses a unique IV, ensuring identical plaintexts produce different
    ciphertexts.

    Attributes:
        _fernet: The underlying Fernet instance.

    Example:
        encryptor = PIIEncryptor(b"32_byte_key_for_encryption_here")

        # Encrypt PII field
        encrypted_name = encryptor.encrypt("John Doe")

        # Decrypt when needed
        name = encryptor.decrypt(encrypted_name)
        assert name == "John Doe"
    """

    def __init__(self, key: bytes) -> None:
        """
        Initialize the encryptor with a 32-byte key.

        The key is base64-encoded internally for Fernet compatibility.
        Fernet requires a URL-safe base64-encoded 32-byte key.

        Args:
            key: A 32-byte encryption key.

        Raises:
            InvalidKeyError: If the key is not exactly 32 bytes.

        Example:
            # Generate a key
            key = b"my_super_secret_32_byte_key_1234"
            encryptor = PIIEncryptor(key)
        """
        if len(key) != 32:
            raise InvalidKeyError(
                f"Key must be exactly 32 bytes, got {len(key)} bytes",
                details={"provided_length": len(key), "required_length": 32},
            )

        # Fernet expects a URL-safe base64-encoded key
        # We encode the raw 32-byte key to create a valid Fernet key
        fernet_key = base64.urlsafe_b64encode(key)
        self._fernet = Fernet(fernet_key)

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext string.

        Uses Fernet encryption which includes:
        - AES-128-CBC encryption
        - HMAC-SHA256 authentication
        - Timestamp for optional TTL enforcement
        - Unique IV per encryption

        Args:
            plaintext: The string to encrypt.

        Returns:
            URL-safe base64-encoded ciphertext string.

        Raises:
            EncryptionError: If encryption fails.

        Example:
            encrypted = encryptor.encrypt("John Doe")
            # Returns something like "gAAAAABl..."
        """
        if not isinstance(plaintext, str):
            raise EncryptionError(
                "Plaintext must be a string",
                operation="encrypt",
                details={"provided_type": type(plaintext).__name__},
            )

        try:
            # Encode to bytes, encrypt, decode to string for storage
            plaintext_bytes = plaintext.encode("utf-8")
            ciphertext_bytes = self._fernet.encrypt(plaintext_bytes)
            return ciphertext_bytes.decode("utf-8")
        except Exception as e:
            raise EncryptionError(
                f"Failed to encrypt data: {e}",
                operation="encrypt",
                details={"error": str(e)},
            ) from e

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt a ciphertext string.

        Verifies the HMAC signature before decryption to ensure
        data integrity and authenticity.

        Args:
            ciphertext: The URL-safe base64-encoded ciphertext to decrypt.

        Returns:
            The original plaintext string.

        Raises:
            DecryptionError: If decryption fails due to invalid token,
                wrong key, or tampered data.

        Example:
            decrypted = encryptor.decrypt(encrypted)
            assert decrypted == "John Doe"
        """
        if not isinstance(ciphertext, str):
            raise DecryptionError(
                "Ciphertext must be a string",
                details={"provided_type": type(ciphertext).__name__},
            )

        try:
            # Encode to bytes, decrypt, decode to string
            ciphertext_bytes = ciphertext.encode("utf-8")
            plaintext_bytes = self._fernet.decrypt(ciphertext_bytes)
            return plaintext_bytes.decode("utf-8")
        except InvalidToken as e:
            raise DecryptionError(
                "Invalid token: data may be corrupted or using wrong key",
                details={"error": "InvalidToken"},
            ) from e
        except Exception as e:
            raise DecryptionError(
                f"Failed to decrypt data: {e}",
                details={"error": str(e)},
            ) from e

    def encrypt_dict(self, data: dict[str, Any], fields: list[str]) -> dict[str, Any]:
        """
        Encrypt specified fields in a dictionary.

        Useful for encrypting multiple PII fields in an applicant record.
        Only encrypts fields that exist in the dictionary and are non-None strings.

        Args:
            data: Dictionary containing fields to encrypt.
            fields: List of field names to encrypt.

        Returns:
            New dictionary with specified fields encrypted.

        Example:
            applicant = {
                "first_name": "John",
                "last_name": "Doe",
                "nationality": "TR"  # Not PII, not encrypted
            }
            encrypted = encryptor.encrypt_dict(
                applicant,
                fields=["first_name", "last_name"]
            )
        """
        result = data.copy()
        for field in fields:
            if field in result and result[field] is not None:
                value = result[field]
                if isinstance(value, str):
                    result[field] = self.encrypt(value)
        return result

    def decrypt_dict(self, data: dict[str, Any], fields: list[str]) -> dict[str, Any]:
        """
        Decrypt specified fields in a dictionary.

        Args:
            data: Dictionary containing encrypted fields.
            fields: List of field names to decrypt.

        Returns:
            New dictionary with specified fields decrypted.

        Example:
            decrypted = encryptor.decrypt_dict(
                encrypted_applicant,
                fields=["first_name", "last_name"]
            )
        """
        result = data.copy()
        for field in fields:
            if field in result and result[field] is not None:
                value = result[field]
                if isinstance(value, str):
                    result[field] = self.decrypt(value)
        return result


# PII fields that must be encrypted per 002-DIRECTUS-SCHEMA.md
PII_FIELDS = [
    "first_name",
    "last_name",
    "passport_number",
    "phone",
    "email",
]


@lru_cache
def get_encryptor() -> PIIEncryptor:
    """
    Get the global PII encryptor instance.

    Loads the encryption key from settings (ENCRYPTION_KEY environment variable)
    and creates a cached encryptor instance.

    Uses lru_cache to avoid creating multiple encryptor instances.

    Returns:
        PIIEncryptor: The global encryptor instance.

    Raises:
        InvalidKeyError: If ENCRYPTION_KEY is not set or invalid.

    Example:
        from src.core.applicant.encryption import get_encryptor

        encryptor = get_encryptor()
        encrypted = encryptor.encrypt("sensitive data")
    """
    from src.api.config import get_settings

    settings = get_settings()
    key_value = settings.ENCRYPTION_KEY.get_secret_value()

    if not key_value:
        raise InvalidKeyError(
            "ENCRYPTION_KEY environment variable is not set",
            details={"env_var": "ENCRYPTION_KEY"},
        )

    # Convert string key to bytes
    key_bytes = key_value.encode("utf-8")

    # Ensure key is exactly 32 bytes
    if len(key_bytes) < 32:
        raise InvalidKeyError(
            f"ENCRYPTION_KEY must be at least 32 bytes, got {len(key_bytes)}",
            details={"provided_length": len(key_bytes), "required_length": 32},
        )

    # Use first 32 bytes if key is longer
    return PIIEncryptor(key_bytes[:32])


def clear_encryptor_cache() -> None:
    """
    Clear the encryptor cache.

    Useful for testing or when the encryption key changes.
    """
    get_encryptor.cache_clear()


__all__ = [
    "PIIEncryptor",
    "EncryptionError",
    "InvalidKeyError",
    "DecryptionError",
    "PII_FIELDS",
    "get_encryptor",
    "clear_encryptor_cache",
]
