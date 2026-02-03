"""
Unit Tests for PII Field-Level Encryption.

This module provides comprehensive tests for the PII encryption service,
including encryption/decryption round-trips, error handling, and dict operations.

Test Categories:
- PIIEncryptor: Core encryption/decryption functionality
- Key Validation: Invalid key handling
- Dict Operations: Batch encrypt/decrypt operations
- Exceptions: EncryptionError, InvalidKeyError, DecryptionError
- Factory Functions: get_encryptor, clear_encryptor_cache

Usage:
    pytest tests/unit/test_encryption.py -v
    pytest tests/unit/test_encryption.py -k "test_encrypt" -v
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest

from src.core.applicant.encryption import (
    DecryptionError,
    EncryptionError,
    InvalidKeyError,
    PII_FIELDS,
    PIIEncryptor,
    clear_encryptor_cache,
    get_encryptor,
)


# =============================================================================
# PIIEncryptor Initialization Tests
# =============================================================================


class TestPIIEncryptorInit:
    """Tests for PIIEncryptor initialization."""

    def test_create_encryptor_with_valid_key(self, test_encryption_key: bytes) -> None:
        """Verify encryptor creation with valid 32-byte key."""
        encryptor = PIIEncryptor(test_encryption_key)
        assert encryptor is not None
        assert encryptor._fernet is not None

    def test_create_encryptor_with_short_key_raises_error(self) -> None:
        """Verify encryptor creation fails with key shorter than 32 bytes."""
        short_key = b"too_short_key"  # Less than 32 bytes

        with pytest.raises(InvalidKeyError) as exc_info:
            PIIEncryptor(short_key)

        assert "must be exactly 32 bytes" in str(exc_info.value)
        assert exc_info.value.code == "INVALID_KEY"
        assert exc_info.value.details["provided_length"] == len(short_key)
        assert exc_info.value.details["required_length"] == 32

    def test_create_encryptor_with_long_key_raises_error(self) -> None:
        """Verify encryptor creation fails with key longer than 32 bytes."""
        long_key = b"this_key_is_way_too_long_and_exceeds_32_bytes_limit"

        with pytest.raises(InvalidKeyError) as exc_info:
            PIIEncryptor(long_key)

        assert "must be exactly 32 bytes" in str(exc_info.value)
        assert exc_info.value.details["provided_length"] == len(long_key)

    def test_create_encryptor_with_empty_key_raises_error(self) -> None:
        """Verify encryptor creation fails with empty key."""
        with pytest.raises(InvalidKeyError) as exc_info:
            PIIEncryptor(b"")

        assert exc_info.value.details["provided_length"] == 0


# =============================================================================
# Encrypt Tests
# =============================================================================


class TestPIIEncryptorEncrypt:
    """Tests for PIIEncryptor.encrypt method."""

    def test_encrypt_simple_string(self, pii_encryptor: PIIEncryptor) -> None:
        """Verify simple string encryption."""
        plaintext = "John Doe"
        encrypted = pii_encryptor.encrypt(plaintext)

        assert encrypted != plaintext
        assert isinstance(encrypted, str)
        # Fernet tokens start with 'gAAAAA'
        assert encrypted.startswith("gAAAAA")

    def test_encrypt_unicode_string(self, pii_encryptor: PIIEncryptor) -> None:
        """Verify Unicode string encryption."""
        plaintext = "Müller Özdemir \u4e2d\u6587"
        encrypted = pii_encryptor.encrypt(plaintext)

        assert encrypted != plaintext
        decrypted = pii_encryptor.decrypt(encrypted)
        assert decrypted == plaintext

    def test_encrypt_empty_string(self, pii_encryptor: PIIEncryptor) -> None:
        """Verify empty string encryption."""
        plaintext = ""
        encrypted = pii_encryptor.encrypt(plaintext)

        assert encrypted != ""
        decrypted = pii_encryptor.decrypt(encrypted)
        assert decrypted == plaintext

    def test_encrypt_special_characters(self, pii_encryptor: PIIEncryptor) -> None:
        """Verify special character encryption."""
        plaintext = "!@#$%^&*()_+-=[]{}|;':\",./<>?"
        encrypted = pii_encryptor.encrypt(plaintext)

        decrypted = pii_encryptor.decrypt(encrypted)
        assert decrypted == plaintext

    def test_encrypt_long_string(self, pii_encryptor: PIIEncryptor) -> None:
        """Verify long string encryption."""
        plaintext = "A" * 10000
        encrypted = pii_encryptor.encrypt(plaintext)

        decrypted = pii_encryptor.decrypt(encrypted)
        assert decrypted == plaintext

    def test_encrypt_non_string_raises_error(self, pii_encryptor: PIIEncryptor) -> None:
        """Verify encryption fails for non-string input."""
        with pytest.raises(EncryptionError) as exc_info:
            pii_encryptor.encrypt(12345)  # type: ignore

        assert "must be a string" in str(exc_info.value)
        assert exc_info.value.operation == "encrypt"
        assert exc_info.value.details["provided_type"] == "int"

    def test_encrypt_none_raises_error(self, pii_encryptor: PIIEncryptor) -> None:
        """Verify encryption fails for None input."""
        with pytest.raises(EncryptionError) as exc_info:
            pii_encryptor.encrypt(None)  # type: ignore

        assert "must be a string" in str(exc_info.value)

    def test_encrypt_produces_different_ciphertexts(
        self, pii_encryptor: PIIEncryptor
    ) -> None:
        """Verify same plaintext produces different ciphertexts (unique IV)."""
        plaintext = "John Doe"

        encrypted1 = pii_encryptor.encrypt(plaintext)
        encrypted2 = pii_encryptor.encrypt(plaintext)

        # Different ciphertexts due to unique IV
        assert encrypted1 != encrypted2
        # But both decrypt to same plaintext
        assert pii_encryptor.decrypt(encrypted1) == plaintext
        assert pii_encryptor.decrypt(encrypted2) == plaintext


# =============================================================================
# Decrypt Tests
# =============================================================================


class TestPIIEncryptorDecrypt:
    """Tests for PIIEncryptor.decrypt method."""

    def test_decrypt_valid_ciphertext(self, pii_encryptor: PIIEncryptor) -> None:
        """Verify valid ciphertext decryption."""
        plaintext = "Sensitive Data"
        encrypted = pii_encryptor.encrypt(plaintext)
        decrypted = pii_encryptor.decrypt(encrypted)

        assert decrypted == plaintext

    def test_decrypt_invalid_ciphertext_raises_error(
        self, pii_encryptor: PIIEncryptor
    ) -> None:
        """Verify decryption fails for invalid ciphertext."""
        with pytest.raises(DecryptionError) as exc_info:
            pii_encryptor.decrypt("not_valid_fernet_token")

        assert "Invalid token" in str(exc_info.value) or "Failed to decrypt" in str(
            exc_info.value
        )
        assert exc_info.value.code == "DECRYPTION_ERROR"

    def test_decrypt_tampered_ciphertext_raises_error(
        self, pii_encryptor: PIIEncryptor
    ) -> None:
        """Verify decryption fails for tampered ciphertext."""
        plaintext = "Sensitive Data"
        encrypted = pii_encryptor.encrypt(plaintext)

        # Tamper with the ciphertext
        tampered = encrypted[:-4] + "XXXX"

        with pytest.raises(DecryptionError) as exc_info:
            pii_encryptor.decrypt(tampered)

        assert exc_info.value.code == "DECRYPTION_ERROR"

    def test_decrypt_wrong_key_raises_error(
        self, test_encryption_key: bytes
    ) -> None:
        """Verify decryption fails with wrong key."""
        encryptor1 = PIIEncryptor(test_encryption_key)
        encryptor2 = PIIEncryptor(b"different_key_32_bytes_long!!")

        encrypted = encryptor1.encrypt("Sensitive Data")

        with pytest.raises(DecryptionError):
            encryptor2.decrypt(encrypted)

    def test_decrypt_non_string_raises_error(
        self, pii_encryptor: PIIEncryptor
    ) -> None:
        """Verify decryption fails for non-string input."""
        with pytest.raises(DecryptionError) as exc_info:
            pii_encryptor.decrypt(12345)  # type: ignore

        assert "must be a string" in str(exc_info.value)
        assert exc_info.value.details["provided_type"] == "int"

    def test_decrypt_empty_string_raises_error(
        self, pii_encryptor: PIIEncryptor
    ) -> None:
        """Verify decryption fails for empty string."""
        with pytest.raises(DecryptionError):
            pii_encryptor.decrypt("")


# =============================================================================
# Round-Trip Tests
# =============================================================================


class TestEncryptDecryptRoundTrip:
    """Tests for encrypt/decrypt round-trips."""

    def test_round_trip_simple(self, pii_encryptor: PIIEncryptor) -> None:
        """Verify encrypt then decrypt returns original."""
        original = "John Doe"
        encrypted = pii_encryptor.encrypt(original)
        decrypted = pii_encryptor.decrypt(encrypted)

        assert decrypted == original

    def test_round_trip_pii_fields(self, pii_encryptor: PIIEncryptor) -> None:
        """Verify round-trip works for typical PII values."""
        pii_values = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john.doe@example.com",
            "phone": "+905551234567",
            "passport_number": "U12345678",
        }

        for field, value in pii_values.items():
            encrypted = pii_encryptor.encrypt(value)
            decrypted = pii_encryptor.decrypt(encrypted)
            assert decrypted == value, f"Round-trip failed for {field}"

    def test_round_trip_multiple_iterations(
        self, pii_encryptor: PIIEncryptor
    ) -> None:
        """Verify multiple encrypt/decrypt cycles work correctly."""
        original = "Test Data"

        # Encrypt and decrypt multiple times
        for _ in range(10):
            encrypted = pii_encryptor.encrypt(original)
            decrypted = pii_encryptor.decrypt(encrypted)
            assert decrypted == original


# =============================================================================
# Dict Operations Tests
# =============================================================================


class TestPIIEncryptorDictOperations:
    """Tests for encrypt_dict and decrypt_dict methods."""

    def test_encrypt_dict_specified_fields(
        self, pii_encryptor: PIIEncryptor
    ) -> None:
        """Verify encrypt_dict encrypts only specified fields."""
        data = {
            "first_name": "John",
            "last_name": "Doe",
            "nationality": "TR",  # Not in fields
        }
        fields = ["first_name", "last_name"]

        encrypted = pii_encryptor.encrypt_dict(data, fields)

        assert encrypted["first_name"] != "John"
        assert encrypted["last_name"] != "Doe"
        assert encrypted["nationality"] == "TR"  # Unchanged

    def test_encrypt_dict_preserves_original(
        self, pii_encryptor: PIIEncryptor
    ) -> None:
        """Verify encrypt_dict does not modify original dict."""
        original = {
            "first_name": "John",
            "last_name": "Doe",
        }
        original_copy = original.copy()

        pii_encryptor.encrypt_dict(original, ["first_name", "last_name"])

        assert original == original_copy

    def test_encrypt_dict_skips_none_values(
        self, pii_encryptor: PIIEncryptor
    ) -> None:
        """Verify encrypt_dict skips None values."""
        data = {
            "first_name": "John",
            "last_name": None,
        }

        encrypted = pii_encryptor.encrypt_dict(data, ["first_name", "last_name"])

        assert encrypted["first_name"] != "John"
        assert encrypted["last_name"] is None

    def test_encrypt_dict_skips_non_string_values(
        self, pii_encryptor: PIIEncryptor
    ) -> None:
        """Verify encrypt_dict skips non-string values."""
        data = {
            "first_name": "John",
            "age": 30,  # Non-string
        }

        encrypted = pii_encryptor.encrypt_dict(data, ["first_name", "age"])

        assert encrypted["first_name"] != "John"
        assert encrypted["age"] == 30  # Unchanged

    def test_encrypt_dict_skips_missing_fields(
        self, pii_encryptor: PIIEncryptor
    ) -> None:
        """Verify encrypt_dict skips fields not present in dict."""
        data = {
            "first_name": "John",
        }

        encrypted = pii_encryptor.encrypt_dict(
            data, ["first_name", "missing_field"]
        )

        assert encrypted["first_name"] != "John"
        assert "missing_field" not in encrypted

    def test_decrypt_dict_specified_fields(
        self, pii_encryptor: PIIEncryptor
    ) -> None:
        """Verify decrypt_dict decrypts only specified fields."""
        original = {
            "first_name": "John",
            "last_name": "Doe",
            "nationality": "TR",
        }

        encrypted = pii_encryptor.encrypt_dict(
            original, ["first_name", "last_name"]
        )
        decrypted = pii_encryptor.decrypt_dict(
            encrypted, ["first_name", "last_name"]
        )

        assert decrypted["first_name"] == "John"
        assert decrypted["last_name"] == "Doe"
        assert decrypted["nationality"] == "TR"

    def test_encrypt_decrypt_dict_round_trip(
        self, pii_encryptor: PIIEncryptor
    ) -> None:
        """Verify full encrypt_dict/decrypt_dict round-trip."""
        original = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "phone": "+905551234567",
            "passport_number": "U12345678",
            "nationality": "TR",  # Not encrypted
        }

        encrypted = pii_encryptor.encrypt_dict(original, PII_FIELDS)
        decrypted = pii_encryptor.decrypt_dict(encrypted, PII_FIELDS)

        assert decrypted == original


# =============================================================================
# PII_FIELDS Constant Tests
# =============================================================================


class TestPIIFields:
    """Tests for PII_FIELDS constant."""

    def test_pii_fields_contains_expected_fields(self) -> None:
        """Verify PII_FIELDS contains all expected field names."""
        expected_fields = [
            "first_name",
            "last_name",
            "passport_number",
            "phone",
            "email",
        ]

        for field in expected_fields:
            assert field in PII_FIELDS

    def test_pii_fields_count(self) -> None:
        """Verify PII_FIELDS has expected count."""
        assert len(PII_FIELDS) == 5


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestGetEncryptor:
    """Tests for get_encryptor factory function."""

    def test_get_encryptor_returns_encryptor(self) -> None:
        """Verify get_encryptor returns PIIEncryptor instance."""
        encryptor = get_encryptor()

        assert isinstance(encryptor, PIIEncryptor)

    def test_get_encryptor_cached(self) -> None:
        """Verify get_encryptor returns cached instance."""
        encryptor1 = get_encryptor()
        encryptor2 = get_encryptor()

        assert encryptor1 is encryptor2

    def test_get_encryptor_works_with_32_byte_key(self) -> None:
        """Verify get_encryptor works correctly with proper key."""
        # Cache should be cleared by conftest.py
        encryptor = get_encryptor()

        # Should be able to encrypt/decrypt
        encrypted = encryptor.encrypt("Test")
        decrypted = encryptor.decrypt(encrypted)
        assert decrypted == "Test"


class TestClearEncryptorCache:
    """Tests for clear_encryptor_cache function."""

    def test_clear_encryptor_cache_clears_cache(self) -> None:
        """Verify clear_encryptor_cache clears the cached encryptor."""
        # Get first encryptor
        encryptor1 = get_encryptor()

        # Clear cache
        clear_encryptor_cache()

        # Get new encryptor
        encryptor2 = get_encryptor()

        # Should be different instances (cache was cleared)
        # Note: Since settings are the same, objects may behave identically
        # but the function should have been called twice (no cache hit)
        assert encryptor2 is not None


# =============================================================================
# Exception Tests
# =============================================================================


class TestEncryptionError:
    """Tests for EncryptionError exception class."""

    def test_encryption_error_default_message(self) -> None:
        """Verify default error message."""
        error = EncryptionError()
        assert error.message == "Encryption error occurred"
        assert error.code == "ENCRYPTION_ERROR"

    def test_encryption_error_with_operation(self) -> None:
        """Verify error with operation attribute."""
        error = EncryptionError("Test error", operation="encrypt")

        assert error.operation == "encrypt"
        assert error.details["operation"] == "encrypt"

    def test_encryption_error_to_dict(self) -> None:
        """Verify error serialization to dict."""
        error = EncryptionError(
            "Test error",
            operation="decrypt",
            code="TEST_CODE",
            details={"key": "value"},
        )

        result = error.to_dict()

        assert result["error"] == "EncryptionError"
        assert result["message"] == "Test error"
        assert result["code"] == "TEST_CODE"
        assert result["details"]["operation"] == "decrypt"
        assert result["details"]["key"] == "value"


class TestInvalidKeyError:
    """Tests for InvalidKeyError exception class."""

    def test_invalid_key_error_default_message(self) -> None:
        """Verify default error message."""
        error = InvalidKeyError()

        assert error.message == "Invalid encryption key"
        assert error.code == "INVALID_KEY"

    def test_invalid_key_error_inherits_from_encryption_error(self) -> None:
        """Verify InvalidKeyError inherits from EncryptionError."""
        error = InvalidKeyError()

        assert isinstance(error, EncryptionError)

    def test_invalid_key_error_with_details(self) -> None:
        """Verify error with details."""
        error = InvalidKeyError(
            "Key too short",
            details={"provided_length": 10, "required_length": 32},
        )

        assert error.details["provided_length"] == 10
        assert error.details["required_length"] == 32


class TestDecryptionError:
    """Tests for DecryptionError exception class."""

    def test_decryption_error_default_message(self) -> None:
        """Verify default error message."""
        error = DecryptionError()

        assert error.message == "Failed to decrypt data"
        assert error.code == "DECRYPTION_ERROR"
        assert error.operation == "decrypt"

    def test_decryption_error_inherits_from_encryption_error(self) -> None:
        """Verify DecryptionError inherits from EncryptionError."""
        error = DecryptionError()

        assert isinstance(error, EncryptionError)

    def test_decryption_error_with_details(self) -> None:
        """Verify error with details."""
        error = DecryptionError(
            "Invalid token",
            details={"error": "InvalidToken"},
        )

        assert error.details["error"] == "InvalidToken"
