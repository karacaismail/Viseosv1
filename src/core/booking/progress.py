"""
Booking Progress Tracker.

Real-time progress tracking for booking requests. Writes stage updates
to both booking_requests (for Directus panel display) and system_logs
(for detailed audit trail).

Each booking goes through these stages:
    idle → queued → account_acquired → proxy_acquired → browser_launched
    → logged_in → searching_slots → slot_found → filling_form → submitting
    → payment → verifying → completed / failed

Usage:
    from src.core.booking.progress import BookingProgressTracker

    tracker = BookingProgressTracker(client)
    await tracker.emit(booking_id, "searching_slots", "Slot aranıyor...", 40)
    await tracker.emit(booking_id, "slot_found", "14 Mart 10:30 slot bulundu!", 55)
    await tracker.fail(booking_id, "login_failed", "VFS hesap bilgileri hatalı")
    await tracker.complete(booking_id, "Randevu onaylandı: ABC123")
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog

from src.integrations.directus import DirectusClient, get_directus_client

logger = structlog.get_logger()


# Stage definitions with progress percentages and human-readable labels
STAGES: dict[str, dict[str, Any]] = {
    "idle":              {"percent": 0,   "label": "Bekliyor",                "label_en": "Idle"},
    "queued":            {"percent": 5,   "label": "Kuyruğa alındı",         "label_en": "Queued"},
    "validating":        {"percent": 8,   "label": "Veriler doğrulanıyor",    "label_en": "Validating data"},
    "credit_reserved":   {"percent": 10,  "label": "Kredi rezerve edildi",    "label_en": "Credit reserved"},
    "account_acquired":  {"percent": 15,  "label": "Bot hesabı seçildi",      "label_en": "Bot account acquired"},
    "proxy_acquired":    {"percent": 20,  "label": "Proxy bağlantısı hazır",  "label_en": "Proxy acquired"},
    "browser_launched":  {"percent": 25,  "label": "Tarayıcı başlatıldı",     "label_en": "Browser launched"},
    "navigating":        {"percent": 30,  "label": "VFS sitesine gidiliyor",  "label_en": "Navigating to site"},
    "logging_in":        {"percent": 35,  "label": "Giriş yapılıyor",        "label_en": "Logging in"},
    "logged_in":         {"percent": 40,  "label": "Giriş başarılı",         "label_en": "Logged in"},
    "searching_slots":   {"percent": 45,  "label": "Randevu aranıyor",       "label_en": "Searching slots"},
    "slot_found":        {"percent": 55,  "label": "Randevu bulundu!",       "label_en": "Slot found"},
    "filling_form":      {"percent": 65,  "label": "Form dolduruluyor",      "label_en": "Filling form"},
    "submitting":        {"percent": 75,  "label": "Başvuru gönderiliyor",    "label_en": "Submitting"},
    "payment":           {"percent": 80,  "label": "Ödeme işleniyor",        "label_en": "Processing payment"},
    "payment_3ds":       {"percent": 83,  "label": "3DS doğrulama bekleniyor","label_en": "Waiting 3DS verification"},
    "verifying":         {"percent": 90,  "label": "Doğrulama bekleniyor",   "label_en": "Verifying"},
    "completing":        {"percent": 95,  "label": "Tamamlanıyor",           "label_en": "Completing"},
    "completed":         {"percent": 100, "label": "Tamamlandı ✓",           "label_en": "Completed"},
    "failed":            {"percent": 0,   "label": "Başarısız ✗",            "label_en": "Failed"},
    "retry_scheduled":   {"percent": 5,   "label": "Yeniden denenecek",      "label_en": "Retry scheduled"},
}


class BookingProgressTracker:
    """
    Tracks and persists booking progress in real-time.

    Updates two Directus collections on each stage change:
    1. booking_requests — current_stage, stage_message, progress_percent, stage_log
    2. system_logs — detailed audit entry with full context

    Attributes:
        _client: DirectusClient for API calls.
    """

    def __init__(self, client: DirectusClient | None = None) -> None:
        self._client = client or get_directus_client()
        self._log = logger.bind(component="progress_tracker")

    async def emit(
        self,
        booking_id: str | UUID,
        stage: str,
        message: str | None = None,
        percent: int | None = None,
        *,
        details: dict[str, Any] | None = None,
        agency_id: str | UUID | None = None,
    ) -> None:
        """
        Emit a progress stage update.

        Args:
            booking_id: Booking request UUID.
            stage: Stage key from STAGES dict (e.g. "slot_found").
            message: Optional human-readable message override.
            percent: Optional progress percent override.
            details: Optional extra details for the log entry.
            agency_id: Optional agency ID for system_logs FK.
        """
        stage_def = STAGES.get(stage, {"percent": percent or 0, "label": stage})
        final_percent = percent if percent is not None else stage_def["percent"]
        final_message = message or stage_def["label"]
        now = datetime.now(timezone.utc).isoformat()

        # Build stage log entry
        log_entry = {
            "stage": stage,
            "message": final_message,
            "percent": final_percent,
            "timestamp": now,
        }
        if details:
            log_entry["details"] = details

        self._log.info(
            "booking_progress",
            booking_id=str(booking_id),
            stage=stage,
            message=final_message,
            percent=final_percent,
        )

        # 1. Update booking_requests with current progress
        try:
            # We append to stage_log array via SQL-safe JSON append
            # First get current stage_log, then append
            current = await self._client.get_item(
                "booking_requests",
                booking_id,
                fields=["stage_log"],
            )

            current_log = []
            if current and current.get("stage_log"):
                current_log = current["stage_log"]
                if not isinstance(current_log, list):
                    current_log = []

            current_log.append(log_entry)

            # Keep last 50 entries to prevent bloat
            if len(current_log) > 50:
                current_log = current_log[-50:]

            update_data: dict[str, Any] = {
                "current_stage": stage,
                "stage_message": final_message,
                "progress_percent": final_percent,
                "stage_started_at": now,
                "stage_log": current_log,
                "updated_at": now,
            }

            # Also update main status for key transitions
            status_map = {
                "queued": "queued",
                "account_acquired": "processing",
                "slot_found": "slot_found",
                "filling_form": "booking",
                "payment": "payment",
                "verifying": "verifying",
                "completed": "completed",
                "failed": "failed",
            }
            if stage in status_map:
                update_data["status"] = status_map[stage]

            # Set completed_at for terminal states
            if stage in ("completed", "failed"):
                update_data["completed_at"] = now

            await self._client.update_item("booking_requests", booking_id, update_data)

        except Exception as e:
            self._log.error(
                "booking_progress_update_failed",
                booking_id=str(booking_id),
                stage=stage,
                error=str(e),
            )

        # 2. Write to system_logs for audit trail
        try:
            log_data: dict[str, Any] = {
                "level": "ERROR" if stage == "failed" else "INFO",
                "category": "booking_progress",
                "event": f"stage:{stage}",
                "booking_request_id": str(booking_id),
                "message": final_message,
                "details": {
                    "stage": stage,
                    "percent": final_percent,
                    **(details or {}),
                },
            }
            if agency_id:
                log_data["agency_id"] = str(agency_id)

            await self._client.create_item("system_logs", log_data)

        except Exception as e:
            self._log.error(
                "system_log_write_failed",
                booking_id=str(booking_id),
                error=str(e),
            )

    async def fail(
        self,
        booking_id: str | UUID,
        error_code: str,
        error_message: str,
        *,
        agency_id: str | UUID | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """
        Mark booking as failed with error details.

        Args:
            booking_id: Booking request UUID.
            error_code: Machine-readable error code.
            error_message: Human-readable error message.
            agency_id: Optional agency ID.
            details: Optional extra details.
        """
        fail_details = {
            "error_code": error_code,
            "error_message": error_message,
            **(details or {}),
        }
        await self.emit(
            booking_id,
            "failed",
            f"Hata: {error_message}",
            details=fail_details,
            agency_id=agency_id,
        )

        # Also set error fields on booking_requests
        try:
            await self._client.update_item("booking_requests", booking_id, {
                "error_code": error_code,
                "error_message": error_message,
            })
        except Exception as e:
            self._log.error(
                "booking_error_update_failed",
                booking_id=str(booking_id),
                error=str(e),
            )

    async def complete(
        self,
        booking_id: str | UUID,
        message: str = "Randevu başarıyla alındı",
        *,
        agency_id: str | UUID | None = None,
        confirmation_number: str | None = None,
        appointment_date: str | None = None,
        appointment_time: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """
        Mark booking as completed with success details.

        Args:
            booking_id: Booking request UUID.
            message: Success message.
            agency_id: Optional agency ID.
            confirmation_number: Appointment confirmation number.
            appointment_date: Appointment date.
            appointment_time: Appointment time.
            details: Optional extra details.
        """
        complete_details = {
            **(details or {}),
        }
        if confirmation_number:
            complete_details["confirmation_number"] = confirmation_number
        if appointment_date:
            complete_details["appointment_date"] = appointment_date
        if appointment_time:
            complete_details["appointment_time"] = appointment_time

        await self.emit(
            booking_id,
            "completed",
            message,
            details=complete_details,
            agency_id=agency_id,
        )

    async def retry(
        self,
        booking_id: str | UUID,
        reason: str,
        next_attempt_at: str | None = None,
        *,
        agency_id: str | UUID | None = None,
    ) -> None:
        """
        Mark booking for retry.

        Args:
            booking_id: Booking request UUID.
            reason: Reason for retry.
            next_attempt_at: Scheduled time for next attempt.
            agency_id: Optional agency ID.
        """
        retry_details: dict[str, Any] = {"reason": reason}
        if next_attempt_at:
            retry_details["next_attempt_at"] = next_attempt_at

        message = f"Yeniden denenecek: {reason}"
        await self.emit(
            booking_id,
            "retry_scheduled",
            message,
            details=retry_details,
            agency_id=agency_id,
        )

        if next_attempt_at:
            try:
                await self._client.update_item("booking_requests", booking_id, {
                    "next_attempt_at": next_attempt_at,
                    "status": "queued",
                })
            except Exception:
                pass


__all__ = [
    "BookingProgressTracker",
    "STAGES",
]
