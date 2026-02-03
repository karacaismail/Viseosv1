"""
Google Sheets Integration Client.

This module provides an async-compatible client for interacting with Google Sheets.
It handles service account authentication, reading/writing spreadsheet data,
field mapping, validation, and sync operations.

Based on 003-GOOGLE-SHEETS-TEMPLATE.md specification for agency data import.

Usage:
    from src.integrations.google_sheets import GoogleSheetsClient, get_sheets_client

    # Using context manager (recommended)
    async with GoogleSheetsClient() as client:
        rows = await client.get_pending_applications("sheet_id_here")

    # Or with dependency injection
    client = get_sheets_client()
    await client.update_row_status(sheet_id, row_num, status="queued")
"""

import asyncio
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from functools import lru_cache
from typing import Any

import gspread
import structlog
from google.oauth2.service_account import Credentials

from src.api.config import get_settings
from src.core.exceptions import ViseOSError

logger = structlog.get_logger()


# =============================================================================
# Exceptions
# =============================================================================


class GoogleSheetsError(ViseOSError):
    """
    Base exception for Google Sheets errors.

    Raised when Google Sheets operations fail.

    Attributes:
        sheet_id: The spreadsheet ID that caused the error.
        sheet_name: The sheet/tab name within the spreadsheet.
    """

    def __init__(
        self,
        message: str = "Google Sheets error occurred",
        *,
        sheet_id: str | None = None,
        sheet_name: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.sheet_id = sheet_id
        self.sheet_name = sheet_name
        details = details or {}
        if sheet_id:
            details["sheet_id"] = sheet_id
        if sheet_name:
            details["sheet_name"] = sheet_name
        super().__init__(message, code=code or "SHEETS_ERROR", details=details)


class SheetNotFoundError(GoogleSheetsError):
    """Raised when a spreadsheet or worksheet is not found."""

    def __init__(
        self,
        message: str = "Spreadsheet or worksheet not found",
        *,
        sheet_id: str | None = None,
        sheet_name: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message,
            sheet_id=sheet_id,
            sheet_name=sheet_name,
            code="SHEET_NOT_FOUND",
            details=details,
        )


class SheetValidationError(GoogleSheetsError):
    """Raised when sheet data fails validation."""

    def __init__(
        self,
        message: str = "Sheet data validation failed",
        *,
        sheet_id: str | None = None,
        row_number: int | None = None,
        field_name: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.row_number = row_number
        self.field_name = field_name
        details = details or {}
        if row_number:
            details["row_number"] = row_number
        if field_name:
            details["field_name"] = field_name
        super().__init__(
            message,
            sheet_id=sheet_id,
            code="SHEET_VALIDATION_ERROR",
            details=details,
        )


class SheetRateLimitError(GoogleSheetsError):
    """Raised when Google Sheets API rate limit is exceeded."""

    def __init__(
        self,
        message: str = "Google Sheets API rate limit exceeded",
        *,
        retry_after: int = 60,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.retry_after = retry_after
        details = details or {}
        details["retry_after"] = retry_after
        super().__init__(
            message,
            code="SHEETS_RATE_LIMIT",
            details=details,
        )


# =============================================================================
# Enums and Constants
# =============================================================================


class RowStatus(str, Enum):
    """Status values for sheet rows as per 003-GOOGLE-SHEETS-TEMPLATE.md."""

    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    VALIDATION_ERROR = "validation_error"


# Column mapping from sheet headers to field names
# Based on 003-GOOGLE-SHEETS-TEMPLATE.md "Basvurular" sheet structure
COLUMN_MAPPING = {
    "A": "ref_no",           # REF_NO - Agency reference
    "B": "first_name",       # AD - First name
    "C": "last_name",        # SOYAD - Last name
    "D": "birth_date",       # DOGUM_TARIHI - Birth date
    "E": "nationality",      # UYRUK - Nationality (ISO alpha-2)
    "F": "passport_number",  # PASAPORT_NO - Passport number
    "G": "passport_expiry",  # PASAPORT_BITIS - Passport expiry
    "H": "phone",            # TELEFON - Phone number
    "I": "email",            # EMAIL - Email address
    "J": "target_country",   # HEDEF_ULKE - Target country
    "K": "target_city",      # HEDEF_SEHIR - Target city
    "L": "visa_type",        # VIZE_TIPI - Visa type
    "M": "date_from",        # TARIH_BASLANGIC - Preferred start date
    "N": "date_to",          # TARIH_BITIS - Preferred end date
    "O": "exclude_weekends", # HAFTA_SONU_HARIC - Exclude weekends
    "P": "family_group_id",  # AILE_GRUP_ID - Family group ID
    "Q": "parent_ref",       # EBEVEYN_REF - Parent reference
    "R": "priority",         # ONCELIK - Priority (1-10)
    "S": "notes",            # NOTLAR - Notes
    "T": "status",           # STATUS - Processing status (auto)
    "U": "booking_id",       # ISLEM_ID - Booking request UUID (auto)
    "V": "result_message",   # SONUC - Result message (auto)
    "W": "appointment_date", # RANDEVU_TARIHI - Appointment date (auto)
    "X": "appointment_time", # RANDEVU_SAATI - Appointment time (auto)
    "Y": "confirmation_no",  # ONAY_NO - Confirmation number (auto)
    "Z": "last_updated",     # SON_GUNCELLEME - Last update timestamp (auto)
}

# Reverse mapping for writing back to sheet
FIELD_TO_COLUMN = {v: k for k, v in COLUMN_MAPPING.items()}

# Required fields (per spec)
REQUIRED_FIELDS = {
    "first_name",
    "last_name",
    "birth_date",
    "nationality",
    "passport_number",
    "passport_expiry",
    "phone",
    "target_country",
    "visa_type",
}

# Country to city mapping (per spec)
COUNTRY_CITIES = {
    "DE": ["Istanbul", "Ankara", "Izmir", "Antalya", "Gaziantep"],
    "IT": ["Istanbul", "Ankara", "Izmir"],
    "FR": ["Istanbul_Beyoglu", "Istanbul_Altunizade", "Ankara", "Izmir"],
    "NL": ["Istanbul_Altunizade", "Ankara"],
    "ES": ["Istanbul", "Ankara", "Izmir"],
    "NO": ["Istanbul", "Ankara"],
    "SE": ["Istanbul", "Ankara"],
    "GR": ["Istanbul", "Ankara"],
}

# Valid visa types by country (per spec)
COUNTRY_VISA_TYPES = {
    "DE": ["tourist", "business", "family", "student", "medical", "conference"],
    "IT": ["tourist", "business", "family", "student", "elective_residence"],
    "FR": ["tourist", "business", "business_pro", "family", "student", "transit"],
    "NL": ["tourist", "business", "family", "student"],
    "ES": ["tourist", "business", "family", "student"],
    "NO": ["tourist", "business", "family", "student"],
    "SE": ["tourist", "business", "family", "student"],
    "GR": ["tourist", "business", "family", "student"],
}

# Supported target countries
SUPPORTED_COUNTRIES = ["DE", "IT", "FR", "NL", "ES", "NO", "SE", "GR"]

# Google Sheets API scopes
SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class SheetRow:
    """
    Represents a row from the Google Sheets "Basvurular" sheet.

    Contains both the raw data and parsed values with validation results.
    """

    row_number: int
    raw_data: dict[str, str]

    # Parsed and validated fields
    ref_no: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    birth_date: date | None = None
    nationality: str | None = None
    passport_number: str | None = None
    passport_expiry: date | None = None
    phone: str | None = None
    email: str | None = None
    target_country: str | None = None
    target_city: str | None = None
    visa_type: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    exclude_weekends: bool = False
    family_group_id: str | None = None
    parent_ref: str | None = None
    priority: int = 5
    notes: str | None = None
    status: str | None = None
    booking_id: str | None = None
    result_message: str | None = None
    appointment_date: date | None = None
    appointment_time: str | None = None
    confirmation_no: str | None = None
    last_updated: datetime | None = None

    # Validation
    is_valid: bool = False
    validation_errors: list[str] = field(default_factory=list)

    def to_applicant_dict(self) -> dict[str, Any]:
        """
        Convert to dictionary suitable for ApplicantCreate schema.

        Returns:
            Dictionary with fields mapped to applicant schema.
        """
        data: dict[str, Any] = {
            "external_ref": self.ref_no,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "birth_date": self.birth_date.isoformat() if self.birth_date else None,
            "nationality": self.nationality,
            "passport_number": self.passport_number,
            "passport_expiry": self.passport_expiry.isoformat() if self.passport_expiry else None,
            "phone": self.phone,
            "email": self.email,
            "target_country": self.target_country,
            "target_city": self.target_city,
            "visa_type": self.visa_type,
            "exclude_weekends": self.exclude_weekends,
            "family_group_id": self.family_group_id,
        }

        # Add preferred dates if specified
        if self.date_from and self.date_to:
            data["preferred_dates"] = {
                "from": self.date_from.isoformat(),
                "to": self.date_to.isoformat(),
            }

        return {k: v for k, v in data.items() if v is not None}


@dataclass
class SyncResult:
    """Result of a sheet sync operation."""

    sheet_id: str
    rows_processed: int = 0
    rows_valid: int = 0
    rows_invalid: int = 0
    rows_created: int = 0
    rows_skipped: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    sync_timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# =============================================================================
# Validation Functions
# =============================================================================


def validate_name(value: str, field_name: str) -> tuple[str | None, str | None]:
    """
    Validate name field (first_name, last_name).

    Per spec: Min 2 characters, only letters and Turkish characters.
    """
    if not value or len(value.strip()) < 2:
        return None, f"{field_name} en az 2 karakter olmali"

    pattern = r"^[A-Za-zÇçĞğİıÖöŞşÜü\s]+$"
    if not re.match(pattern, value.strip()):
        return None, f"{field_name} sadece harf içermeli"

    return value.strip(), None


def validate_date(value: str, field_name: str, fmt: str = "%d.%m.%Y") -> tuple[date | None, str | None]:
    """
    Validate date field.

    Per spec: DD.MM.YYYY format.
    """
    if not value or not value.strip():
        return None, f"{field_name} zorunlu"

    try:
        # Try DD.MM.YYYY format first
        parsed = datetime.strptime(value.strip(), fmt).date()
        return parsed, None
    except ValueError:
        # Try ISO format as fallback
        try:
            parsed = datetime.strptime(value.strip(), "%Y-%m-%d").date()
            return parsed, None
        except ValueError:
            return None, f"{field_name} geçerli bir tarih değil (DD.MM.YYYY)"


def validate_passport_number(value: str) -> tuple[str | None, str | None]:
    """
    Validate passport number.

    Per spec: 7-9 alphanumeric characters, uppercase.
    """
    if not value or not value.strip():
        return None, "Pasaport numarasi zorunlu"

    cleaned = value.strip().upper().replace(" ", "")

    if len(cleaned) < 7 or len(cleaned) > 9:
        return None, "Pasaport numarasi 7-9 karakter olmali"

    if not re.match(r"^[A-Z0-9]+$", cleaned):
        return None, "Pasaport numarasi sadece harf ve rakam içermeli"

    return cleaned, None


def validate_phone(value: str) -> tuple[str | None, str | None]:
    """
    Validate phone number.

    Per spec: +90 prefix, 12-13 digits total.
    """
    if not value or not value.strip():
        return None, "Telefon numarasi zorunlu"

    cleaned = value.strip().replace(" ", "").replace("-", "")

    # Ensure +90 prefix
    if not cleaned.startswith("+90"):
        if cleaned.startswith("90"):
            cleaned = "+" + cleaned
        elif cleaned.startswith("0"):
            cleaned = "+90" + cleaned[1:]
        else:
            cleaned = "+90" + cleaned

    if not re.match(r"^\+90[0-9]{10}$", cleaned):
        return None, "Telefon +905XXXXXXXXX formatinda olmali"

    return cleaned, None


def validate_email(value: str | None) -> tuple[str | None, str | None]:
    """
    Validate email address.

    Per spec: Optional, valid email format.
    """
    if not value or not value.strip():
        return None, None  # Optional field, no error

    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    if not re.match(pattern, value.strip()):
        return None, "Geçerli bir email adresi girin"

    return value.strip().lower(), None


def validate_country(value: str) -> tuple[str | None, str | None]:
    """
    Validate target country.

    Per spec: Must be one of supported countries.
    """
    if not value or not value.strip():
        return None, "Hedef ülke zorunlu"

    cleaned = value.strip().upper()

    if cleaned not in SUPPORTED_COUNTRIES:
        return None, f"Desteklenmeyen ülke: {cleaned}"

    return cleaned, None


def validate_visa_type(value: str, country: str | None) -> tuple[str | None, str | None]:
    """
    Validate visa type for given country.

    Per spec: Must be valid for the target country.
    """
    if not value or not value.strip():
        return None, "Vize tipi zorunlu"

    cleaned = value.strip().lower()

    if country and country in COUNTRY_VISA_TYPES:
        valid_types = COUNTRY_VISA_TYPES[country]
        if cleaned not in valid_types:
            return None, f"Bu ülke için geçersiz vize tipi: {cleaned}"

    return cleaned, None


def validate_nationality(value: str) -> tuple[str | None, str | None]:
    """
    Validate nationality code.

    Per spec: ISO 3166-1 alpha-2 format.
    """
    if not value or not value.strip():
        return None, "Uyruk zorunlu"

    cleaned = value.strip().upper()

    if len(cleaned) != 2 or not cleaned.isalpha():
        return None, "Uyruk ISO alpha-2 formatinda olmali (örn: TR)"

    return cleaned, None


# =============================================================================
# Client Class
# =============================================================================


class GoogleSheetsClient:
    """
    Async-compatible client for Google Sheets operations.

    Provides methods for reading applications, updating statuses,
    and syncing data with the VISE OS system.

    Attributes:
        credentials_path: Path to service account JSON file.
        timeout: Request timeout in seconds.
        max_retries: Maximum retries for failed operations.

    Example:
        async with GoogleSheetsClient() as client:
            # Get pending applications from a sheet
            rows = await client.get_pending_applications(
                sheet_id="abc123...",
                sheet_name="Basvurular"
            )

            # Update row status after processing
            await client.update_row_status(
                sheet_id="abc123...",
                row_number=5,
                status=RowStatus.QUEUED,
                booking_id="uuid-here"
            )
    """

    # Default sheet name per spec
    DEFAULT_SHEET_NAME = "Başvurular"
    SETTINGS_SHEET_NAME = "Ayarlar"
    HISTORY_SHEET_NAME = "Geçmiş"

    def __init__(
        self,
        credentials_path: str | None = None,
        credentials_dict: dict[str, Any] | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        """
        Initialize the Google Sheets client.

        Args:
            credentials_path: Path to service account JSON. Falls back to
                GOOGLE_SERVICE_ACCOUNT_PATH from settings if not provided.
            credentials_dict: Service account credentials as dict. Takes
                precedence over credentials_path.
            timeout: Request timeout in seconds (default: 30).
            max_retries: Maximum retries for failed operations (default: 3).
        """
        self._credentials_path = credentials_path
        self._credentials_dict = credentials_dict
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: gspread.Client | None = None
        self._logger = logger.bind(service="google_sheets")

    def _get_credentials(self) -> Credentials:
        """Get Google service account credentials."""
        if self._credentials_dict:
            return Credentials.from_service_account_info(
                self._credentials_dict,
                scopes=SHEETS_SCOPES,
            )

        creds_path = self._credentials_path
        if not creds_path:
            settings = get_settings()
            creds_path = getattr(settings, "GOOGLE_SERVICE_ACCOUNT_PATH", None)

        if not creds_path:
            raise GoogleSheetsError(
                "No Google service account credentials provided",
                code="SHEETS_NO_CREDENTIALS",
            )

        return Credentials.from_service_account_file(
            creds_path,
            scopes=SHEETS_SCOPES,
        )

    def _get_client(self) -> gspread.Client:
        """Get or create the gspread client."""
        if self._client is None:
            credentials = self._get_credentials()
            self._client = gspread.authorize(credentials)
        return self._client

    async def __aenter__(self) -> "GoogleSheetsClient":
        """Async context manager entry."""
        # Initialize client in executor to avoid blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._get_client)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        self._client = None

    async def close(self) -> None:
        """Close the client and release resources."""
        self._client = None

    async def _run_sync(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """
        Run a synchronous gspread function in an executor.

        This makes the blocking gspread calls async-compatible.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    # -------------------------------------------------------------------------
    # Sheet Access
    # -------------------------------------------------------------------------

    async def open_spreadsheet(self, sheet_id: str) -> gspread.Spreadsheet:
        """
        Open a spreadsheet by ID.

        Args:
            sheet_id: The Google Sheets spreadsheet ID.

        Returns:
            gspread.Spreadsheet object.

        Raises:
            SheetNotFoundError: If spreadsheet doesn't exist or access denied.
        """
        client = self._get_client()

        try:
            spreadsheet = await self._run_sync(client.open_by_key, sheet_id)
            self._logger.debug("spreadsheet_opened", sheet_id=sheet_id)
            return spreadsheet
        except gspread.exceptions.SpreadsheetNotFound:
            raise SheetNotFoundError(
                f"Spreadsheet not found: {sheet_id}",
                sheet_id=sheet_id,
            )
        except gspread.exceptions.APIError as e:
            if "PERMISSION_DENIED" in str(e):
                raise SheetNotFoundError(
                    f"Permission denied for spreadsheet: {sheet_id}",
                    sheet_id=sheet_id,
                )
            raise GoogleSheetsError(
                f"API error accessing spreadsheet: {e}",
                sheet_id=sheet_id,
            )

    async def get_worksheet(
        self,
        sheet_id: str,
        sheet_name: str | None = None,
    ) -> gspread.Worksheet:
        """
        Get a specific worksheet from a spreadsheet.

        Args:
            sheet_id: The Google Sheets spreadsheet ID.
            sheet_name: Name of the worksheet. Defaults to "Basvurular".

        Returns:
            gspread.Worksheet object.
        """
        spreadsheet = await self.open_spreadsheet(sheet_id)
        name = sheet_name or self.DEFAULT_SHEET_NAME

        try:
            worksheet = await self._run_sync(spreadsheet.worksheet, name)
            return worksheet
        except gspread.exceptions.WorksheetNotFound:
            raise SheetNotFoundError(
                f"Worksheet not found: {name}",
                sheet_id=sheet_id,
                sheet_name=name,
            )

    # -------------------------------------------------------------------------
    # Read Operations
    # -------------------------------------------------------------------------

    async def get_all_rows(
        self,
        sheet_id: str,
        sheet_name: str | None = None,
        *,
        skip_header: bool = True,
    ) -> list[dict[str, str]]:
        """
        Get all rows from a worksheet.

        Args:
            sheet_id: The spreadsheet ID.
            sheet_name: Worksheet name (default: "Basvurular").
            skip_header: Whether to skip the header row (default: True).

        Returns:
            List of dictionaries with column letter keys and cell values.
        """
        worksheet = await self.get_worksheet(sheet_id, sheet_name)

        # Get all values as list of lists
        all_values = await self._run_sync(worksheet.get_all_values)

        if not all_values:
            return []

        # Skip header row if requested
        start_row = 1 if skip_header else 0
        data_rows = all_values[start_row:]

        # Convert to list of dicts with column letter keys
        result = []
        for row_values in data_rows:
            row_dict = {}
            for col_idx, value in enumerate(row_values):
                col_letter = chr(ord("A") + col_idx)
                if col_letter in COLUMN_MAPPING:
                    row_dict[col_letter] = value
            result.append(row_dict)

        self._logger.debug(
            "sheets_get_all_rows",
            sheet_id=sheet_id,
            row_count=len(result),
        )

        return result

    async def get_pending_applications(
        self,
        sheet_id: str,
        sheet_name: str | None = None,
        *,
        validate: bool = True,
    ) -> list[SheetRow]:
        """
        Get pending applications from the sheet.

        Returns rows where STATUS is empty or "pending".

        Args:
            sheet_id: The spreadsheet ID.
            sheet_name: Worksheet name (default: "Basvurular").
            validate: Whether to validate the rows (default: True).

        Returns:
            List of SheetRow objects with parsed and validated data.
        """
        worksheet = await self.get_worksheet(sheet_id, sheet_name)

        # Get all values
        all_values = await self._run_sync(worksheet.get_all_values)

        if len(all_values) < 2:  # Only header or empty
            return []

        pending_rows: list[SheetRow] = []

        # Process each data row (skip header at index 0)
        for row_idx, row_values in enumerate(all_values[1:], start=2):
            # Build raw data dict
            raw_data = {}
            for col_idx, value in enumerate(row_values):
                col_letter = chr(ord("A") + col_idx)
                if col_letter in COLUMN_MAPPING:
                    raw_data[col_letter] = value

            # Check status - only include pending or empty
            status = raw_data.get("T", "").strip().lower()
            if status and status not in ("", "pending"):
                continue

            # Check if row has any required data
            has_data = any(
                raw_data.get(FIELD_TO_COLUMN.get(f, ""), "").strip()
                for f in REQUIRED_FIELDS
            )
            if not has_data:
                continue

            # Parse row
            sheet_row = self._parse_row(row_idx, raw_data)

            # Validate if requested
            if validate:
                self._validate_row(sheet_row)

            pending_rows.append(sheet_row)

        self._logger.info(
            "sheets_get_pending",
            sheet_id=sheet_id,
            pending_count=len(pending_rows),
            valid_count=sum(1 for r in pending_rows if r.is_valid),
        )

        return pending_rows

    def _parse_row(self, row_number: int, raw_data: dict[str, str]) -> SheetRow:
        """Parse raw row data into SheetRow object."""
        row = SheetRow(row_number=row_number, raw_data=raw_data)

        # Parse each field from raw data
        row.ref_no = raw_data.get("A", "").strip() or None
        row.first_name = raw_data.get("B", "").strip() or None
        row.last_name = raw_data.get("C", "").strip() or None
        row.nationality = raw_data.get("E", "").strip().upper() or None
        row.passport_number = raw_data.get("F", "").strip().upper() or None
        row.phone = raw_data.get("H", "").strip() or None
        row.email = raw_data.get("I", "").strip().lower() or None
        row.target_country = raw_data.get("J", "").strip().upper() or None
        row.target_city = raw_data.get("K", "").strip() or None
        row.visa_type = raw_data.get("L", "").strip().lower() or None
        row.family_group_id = raw_data.get("P", "").strip() or None
        row.parent_ref = raw_data.get("Q", "").strip() or None
        row.notes = raw_data.get("S", "").strip() or None
        row.status = raw_data.get("T", "").strip().lower() or None
        row.booking_id = raw_data.get("U", "").strip() or None
        row.result_message = raw_data.get("V", "").strip() or None
        row.confirmation_no = raw_data.get("Y", "").strip() or None

        # Parse dates
        birth_date_str = raw_data.get("D", "").strip()
        if birth_date_str:
            parsed, _ = validate_date(birth_date_str, "birth_date")
            row.birth_date = parsed

        passport_expiry_str = raw_data.get("G", "").strip()
        if passport_expiry_str:
            parsed, _ = validate_date(passport_expiry_str, "passport_expiry")
            row.passport_expiry = parsed

        date_from_str = raw_data.get("M", "").strip()
        if date_from_str:
            parsed, _ = validate_date(date_from_str, "date_from")
            row.date_from = parsed

        date_to_str = raw_data.get("N", "").strip()
        if date_to_str:
            parsed, _ = validate_date(date_to_str, "date_to")
            row.date_to = parsed

        # Parse boolean
        exclude_weekends = raw_data.get("O", "").strip().upper()
        row.exclude_weekends = exclude_weekends in ("TRUE", "EVET", "1", "YES")

        # Parse priority
        priority_str = raw_data.get("R", "").strip()
        if priority_str:
            try:
                priority = int(priority_str)
                row.priority = max(1, min(10, priority))  # Clamp 1-10
            except ValueError:
                row.priority = 5

        return row

    def _validate_row(self, row: SheetRow) -> None:
        """Validate a parsed row and populate validation results."""
        errors: list[str] = []

        # Validate required fields
        _, err = validate_name(row.first_name or "", "Ad")
        if err:
            errors.append(err)

        _, err = validate_name(row.last_name or "", "Soyad")
        if err:
            errors.append(err)

        if not row.birth_date:
            errors.append("Dogum tarihi zorunlu")
        elif row.birth_date >= date.today():
            errors.append("Dogum tarihi geçmişte olmali")

        _, err = validate_nationality(row.nationality or "")
        if err:
            errors.append(err)

        _, err = validate_passport_number(row.passport_number or "")
        if err:
            errors.append(err)

        if not row.passport_expiry:
            errors.append("Pasaport bitiş tarihi zorunlu")
        elif row.passport_expiry <= date.today():
            errors.append("Pasaport bitiş tarihi geçerli olmali")
        elif (row.passport_expiry - date.today()).days < 90:
            errors.append("Pasaport bitiş tarihi en az 3 ay sonra olmali")

        validated_phone, err = validate_phone(row.phone or "")
        if err:
            errors.append(err)
        else:
            row.phone = validated_phone

        _, err = validate_email(row.email)
        if err:
            errors.append(err)

        _, err = validate_country(row.target_country or "")
        if err:
            errors.append(err)

        _, err = validate_visa_type(row.visa_type or "", row.target_country)
        if err:
            errors.append(err)

        # Validate date range
        if row.date_from and row.date_to:
            if row.date_to < row.date_from:
                errors.append("Bitiş tarihi başlangiç tarihinden sonra olmali")

        row.validation_errors = errors
        row.is_valid = len(errors) == 0

    # -------------------------------------------------------------------------
    # Write Operations
    # -------------------------------------------------------------------------

    async def update_row_status(
        self,
        sheet_id: str,
        row_number: int,
        *,
        status: RowStatus | str,
        booking_id: str | None = None,
        result_message: str | None = None,
        appointment_date: date | str | None = None,
        appointment_time: str | None = None,
        confirmation_no: str | None = None,
        sheet_name: str | None = None,
    ) -> None:
        """
        Update the status columns of a row.

        Updates columns T-Z (STATUS through SON_GUNCELLEME).

        Args:
            sheet_id: The spreadsheet ID.
            row_number: Row number to update (1-indexed, including header).
            status: New status value.
            booking_id: Booking request UUID.
            result_message: Result/error message.
            appointment_date: Booked appointment date.
            appointment_time: Booked appointment time.
            confirmation_no: Appointment confirmation number.
            sheet_name: Worksheet name (default: "Basvurular").
        """
        worksheet = await self.get_worksheet(sheet_id, sheet_name)

        # Prepare update values
        status_value = status.value if isinstance(status, RowStatus) else status
        now_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M")

        updates = {
            "T": status_value,
            "Z": now_str,
        }

        if booking_id is not None:
            updates["U"] = booking_id

        if result_message is not None:
            updates["V"] = result_message[:500]  # Max 500 chars per spec

        if appointment_date is not None:
            if isinstance(appointment_date, date):
                updates["W"] = appointment_date.strftime("%d.%m.%Y")
            else:
                updates["W"] = appointment_date

        if appointment_time is not None:
            updates["X"] = appointment_time

        if confirmation_no is not None:
            updates["Y"] = confirmation_no

        # Build batch update
        for col_letter, value in updates.items():
            col_idx = ord(col_letter) - ord("A") + 1
            cell = f"{col_letter}{row_number}"
            await self._run_sync(worksheet.update_acell, cell, value)

        self._logger.info(
            "sheets_row_status_updated",
            sheet_id=sheet_id,
            row_number=row_number,
            status=status_value,
            booking_id=booking_id,
        )

    async def batch_update_cells(
        self,
        sheet_id: str,
        updates: list[dict[str, Any]],
        sheet_name: str | None = None,
    ) -> None:
        """
        Batch update multiple cells.

        More efficient for updating many cells at once.

        Args:
            sheet_id: The spreadsheet ID.
            updates: List of dicts with 'cell' (A1 notation) and 'value'.
            sheet_name: Worksheet name (default: "Basvurular").
        """
        if not updates:
            return

        worksheet = await self.get_worksheet(sheet_id, sheet_name)

        # Use gspread batch_update for efficiency
        cell_list = []
        for update in updates:
            cell = update["cell"]
            value = update["value"]
            cell_list.append(gspread.Cell.from_address(cell))
            cell_list[-1].value = value

        if cell_list:
            await self._run_sync(worksheet.update_cells, cell_list)

        self._logger.debug(
            "sheets_batch_update",
            sheet_id=sheet_id,
            cell_count=len(updates),
        )

    async def archive_to_history(
        self,
        sheet_id: str,
        row_number: int,
        *,
        mask_pii: bool = True,
    ) -> None:
        """
        Archive a row to the "Gecmis" (History) sheet.

        Per spec, PII is masked when archiving.

        Args:
            sheet_id: The spreadsheet ID.
            row_number: Row number to archive from "Basvurular".
            mask_pii: Whether to mask PII fields (default: True).
        """
        # Get source data
        source_ws = await self.get_worksheet(sheet_id, self.DEFAULT_SHEET_NAME)
        row_data = await self._run_sync(source_ws.row_values, row_number)

        if not row_data:
            return

        # Build history row per spec
        now_str = datetime.now(timezone.utc).strftime("%d.%m.%Y")

        # Mask name if requested
        first_name = row_data[1] if len(row_data) > 1 else ""
        last_name = row_data[2] if len(row_data) > 2 else ""
        if mask_pii and first_name and last_name:
            masked_name = f"{first_name[0]}*** {last_name[0]}***"
        else:
            masked_name = f"{first_name} {last_name}"

        history_row = [
            now_str,                                    # TARIH
            row_data[0] if len(row_data) > 0 else "",   # REF_NO
            masked_name,                                # AD_SOYAD (masked)
            row_data[9] if len(row_data) > 9 else "",   # ULKE (target_country)
            row_data[19] if len(row_data) > 19 else "", # SONUC
            row_data[22] if len(row_data) > 22 else "", # RANDEVU (date)
            row_data[24] if len(row_data) > 24 else "", # ONAY_NO
            "1",                                        # KREDI
        ]

        # Append to history sheet
        try:
            history_ws = await self.get_worksheet(sheet_id, self.HISTORY_SHEET_NAME)
            await self._run_sync(history_ws.append_row, history_row)
            self._logger.info(
                "sheets_row_archived",
                sheet_id=sheet_id,
                row_number=row_number,
            )
        except SheetNotFoundError:
            # History sheet doesn't exist, skip archiving
            self._logger.warning(
                "sheets_history_not_found",
                sheet_id=sheet_id,
            )

    async def delete_row(
        self,
        sheet_id: str,
        row_number: int,
        sheet_name: str | None = None,
    ) -> None:
        """
        Delete a row from the worksheet.

        Args:
            sheet_id: The spreadsheet ID.
            row_number: Row number to delete.
            sheet_name: Worksheet name (default: "Basvurular").
        """
        worksheet = await self.get_worksheet(sheet_id, sheet_name)
        await self._run_sync(worksheet.delete_rows, row_number)

        self._logger.info(
            "sheets_row_deleted",
            sheet_id=sheet_id,
            row_number=row_number,
        )

    # -------------------------------------------------------------------------
    # Sync Operations
    # -------------------------------------------------------------------------

    async def sync_pending_applications(
        self,
        sheet_id: str,
        agency_id: str,
    ) -> SyncResult:
        """
        Sync pending applications from sheet.

        This is the main sync operation called by the Celery beat task.
        It reads pending rows, validates them, and prepares them for
        the booking queue.

        Note: This method only reads and validates. The actual Directus
        writes and booking queue submissions should be handled by the
        calling task to maintain transaction boundaries.

        Args:
            sheet_id: The spreadsheet ID.
            agency_id: The agency UUID for these applications.

        Returns:
            SyncResult with processing statistics.
        """
        result = SyncResult(sheet_id=sheet_id)

        try:
            pending_rows = await self.get_pending_applications(
                sheet_id,
                validate=True,
            )
        except GoogleSheetsError as e:
            self._logger.error(
                "sheets_sync_failed",
                sheet_id=sheet_id,
                error=str(e),
            )
            result.errors.append({"type": "sheet_access", "error": str(e)})
            return result

        result.rows_processed = len(pending_rows)
        result.rows_valid = sum(1 for r in pending_rows if r.is_valid)
        result.rows_invalid = result.rows_processed - result.rows_valid

        # Mark invalid rows with validation errors
        for row in pending_rows:
            if not row.is_valid:
                error_msg = "; ".join(row.validation_errors[:3])  # First 3 errors
                await self.update_row_status(
                    sheet_id,
                    row.row_number,
                    status=RowStatus.VALIDATION_ERROR,
                    result_message=error_msg,
                )
                result.errors.append({
                    "type": "validation",
                    "row": row.row_number,
                    "errors": row.validation_errors,
                })

        self._logger.info(
            "sheets_sync_complete",
            sheet_id=sheet_id,
            agency_id=agency_id,
            processed=result.rows_processed,
            valid=result.rows_valid,
            invalid=result.rows_invalid,
        )

        return result

    async def get_agency_settings(
        self,
        sheet_id: str,
    ) -> dict[str, str]:
        """
        Get agency settings from the "Ayarlar" sheet.

        Args:
            sheet_id: The spreadsheet ID.

        Returns:
            Dictionary of settings from the Ayarlar sheet.
        """
        try:
            worksheet = await self.get_worksheet(sheet_id, self.SETTINGS_SHEET_NAME)
            all_values = await self._run_sync(worksheet.get_all_values)

            settings = {}
            for row in all_values:
                if len(row) >= 2:
                    key = row[0].strip()
                    value = row[1].strip() if len(row) > 1 else ""
                    if key:
                        settings[key] = value

            return settings

        except SheetNotFoundError:
            return {}


# =============================================================================
# Factory Functions
# =============================================================================


@lru_cache
def get_sheets_client() -> GoogleSheetsClient:
    """
    Get a cached Google Sheets client instance.

    The client is cached and reused across requests.

    Returns:
        Configured GoogleSheetsClient instance.
    """
    return GoogleSheetsClient()


def clear_sheets_client_cache() -> None:
    """
    Clear the cached Google Sheets client.

    Useful for testing or when credentials change.
    """
    get_sheets_client.cache_clear()


@asynccontextmanager
async def sheets_client(
    credentials_path: str | None = None,
    credentials_dict: dict[str, Any] | None = None,
):
    """
    Async context manager for a Google Sheets client.

    Creates a new client instance that is automatically closed on exit.

    Args:
        credentials_path: Path to service account JSON.
        credentials_dict: Service account credentials as dict.

    Yields:
        Configured GoogleSheetsClient instance.

    Example:
        async with sheets_client() as client:
            rows = await client.get_pending_applications(sheet_id)
    """
    client = GoogleSheetsClient(
        credentials_path=credentials_path,
        credentials_dict=credentials_dict,
    )
    try:
        await client.__aenter__()
        yield client
    finally:
        await client.close()


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Client
    "GoogleSheetsClient",
    "get_sheets_client",
    "clear_sheets_client_cache",
    "sheets_client",
    # Data classes
    "SheetRow",
    "SyncResult",
    # Enums
    "RowStatus",
    # Exceptions
    "GoogleSheetsError",
    "SheetNotFoundError",
    "SheetValidationError",
    "SheetRateLimitError",
    # Constants
    "COLUMN_MAPPING",
    "FIELD_TO_COLUMN",
    "REQUIRED_FIELDS",
    "SUPPORTED_COUNTRIES",
    "COUNTRY_CITIES",
    "COUNTRY_VISA_TYPES",
]
