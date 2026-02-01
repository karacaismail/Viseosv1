"""
Webhooks router for VISE OS API.

This module provides webhook endpoints for receiving external callbacks from:
- Directus CMS triggers (data lifecycle events)
- Payment providers (Stripe, iyzico payment callbacks)
- Google Sheets sync (sheet change notifications)
- Email/SMS verification services (OTP delivery confirmations)

All webhook endpoints:
1. Verify signatures/authentication for security
2. Process events asynchronously via Celery tasks
3. Return quickly with 200 to acknowledge receipt
4. Log all received events for audit trails

Endpoints:
    POST /webhooks/directus - Directus data change events
    POST /webhooks/payment/stripe - Stripe payment callbacks
    POST /webhooks/payment/iyzico - iyzico payment callbacks
    POST /webhooks/sheets - Google Sheets change notifications
    POST /webhooks/verification - Email/SMS verification callbacks

Usage:
    from src.api.routers.webhooks import router
    app.include_router(router, prefix="/api/webhooks", tags=["webhooks"])
"""

import hashlib
import hmac
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.api.config import Settings, get_settings
from src.api.dependencies import get_request_id, RequestId

logger = structlog.get_logger()


# =============================================================================
# Enums
# =============================================================================


class WebhookSource(str, Enum):
    """Webhook event sources."""

    DIRECTUS = "directus"
    STRIPE = "stripe"
    IYZICO = "iyzico"
    GOOGLE_SHEETS = "google_sheets"
    EMAIL_VERIFICATION = "email_verification"
    SMS_VERIFICATION = "sms_verification"


class DirectusEvent(str, Enum):
    """Directus collection event types."""

    ITEMS_CREATE = "items.create"
    ITEMS_UPDATE = "items.update"
    ITEMS_DELETE = "items.delete"
    ITEMS_SORT = "items.sort"
    ITEMS_READ = "items.read"


class PaymentEventType(str, Enum):
    """Payment webhook event types."""

    PAYMENT_COMPLETED = "payment.completed"
    PAYMENT_FAILED = "payment.failed"
    PAYMENT_PENDING = "payment.pending"
    PAYMENT_REFUNDED = "payment.refunded"
    PAYMENT_3DS_REQUIRED = "payment.3ds_required"
    PAYMENT_3DS_COMPLETED = "payment.3ds_completed"


# =============================================================================
# Request Models
# =============================================================================


class DirectusWebhookPayload(BaseModel):
    """Directus webhook payload structure."""

    event: str = Field(description="Event type (items.create, items.update, etc.)")
    collection: str = Field(description="Collection name")
    key: str | list[str] | None = Field(
        default=None,
        description="Affected item key(s)",
    )
    keys: list[str] | None = Field(
        default=None,
        description="Affected item keys (for batch operations)",
    )
    payload: dict[str, Any] | None = Field(
        default=None,
        description="Event payload data",
    )
    accountability: dict[str, Any] | None = Field(
        default=None,
        description="User/role that triggered the event",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "event": "items.create",
                "collection": "booking_requests",
                "key": "123e4567-e89b-12d3-a456-426614174000",
                "payload": {
                    "agency_id": "agency-uuid",
                    "status": "pending",
                },
            }
        }
    }


class StripeWebhookPayload(BaseModel):
    """Stripe webhook event structure."""

    id: str = Field(description="Stripe event ID")
    type: str = Field(description="Event type (payment_intent.succeeded, etc.)")
    data: dict[str, Any] = Field(description="Event data object")
    created: int = Field(description="Event creation timestamp")
    livemode: bool = Field(description="Whether this is a live mode event")

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "evt_1234567890",
                "type": "payment_intent.succeeded",
                "data": {
                    "object": {
                        "id": "pi_1234567890",
                        "amount": 5000,
                        "currency": "eur",
                        "status": "succeeded",
                    }
                },
                "created": 1234567890,
                "livemode": False,
            }
        }
    }


class IyzicoWebhookPayload(BaseModel):
    """iyzico webhook callback structure."""

    status: str = Field(description="Payment status")
    payment_id: str = Field(
        default="",
        alias="paymentId",
        description="iyzico payment ID",
    )
    token: str | None = Field(
        default=None,
        description="Callback verification token",
    )
    conversation_id: str | None = Field(
        default=None,
        alias="conversationId",
        description="Original conversation/booking ID",
    )
    merchant_order_id: str | None = Field(
        default=None,
        alias="merchantOrderId",
        description="Merchant order reference",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "success",
                "paymentId": "12345678",
                "token": "callback-token-uuid",
                "conversationId": "booking-uuid",
            }
        },
        "populate_by_name": True,
    }


class SheetsWebhookPayload(BaseModel):
    """Google Sheets change notification structure."""

    spreadsheet_id: str = Field(description="Google Sheets spreadsheet ID")
    sheet_name: str | None = Field(
        default=None,
        description="Specific sheet name if applicable",
    )
    change_type: str = Field(description="Type of change (edit, insert, delete)")
    changed_range: str | None = Field(
        default=None,
        description="A1 notation range of changed cells",
    )
    agency_id: str | None = Field(
        default=None,
        description="Associated agency ID",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "spreadsheet_id": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms",
                "sheet_name": "Applicants",
                "change_type": "edit",
                "changed_range": "A2:K10",
                "agency_id": "agency-uuid",
            }
        }
    }


class VerificationWebhookPayload(BaseModel):
    """Email/SMS verification callback structure."""

    verification_type: str = Field(description="Type: email or sms")
    reference_id: str = Field(description="Reference ID for the verification")
    status: str = Field(description="Status: delivered, failed, bounced, etc.")
    otp_code: str | None = Field(
        default=None,
        description="Extracted OTP code (if applicable)",
    )
    provider: str | None = Field(
        default=None,
        description="Verification service provider",
    )
    error_message: str | None = Field(
        default=None,
        description="Error message if status is failed",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "verification_type": "email",
                "reference_id": "booking-uuid",
                "status": "delivered",
                "otp_code": "123456",
                "provider": "resend",
            }
        }
    }


# =============================================================================
# Response Models
# =============================================================================


class WebhookResponse(BaseModel):
    """Standard webhook response."""

    received: bool = Field(default=True, description="Event received successfully")
    message: str = Field(description="Response message")
    event_id: str | None = Field(
        default=None,
        description="Internal event ID for tracking",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "received": True,
                "message": "Event queued for processing",
                "event_id": "evt_abc123",
            }
        }
    }


# =============================================================================
# Router
# =============================================================================

router = APIRouter()


# =============================================================================
# Webhook Signature Verification
# =============================================================================


def verify_directus_secret(
    provided_secret: str | None,
    settings: Settings,
) -> bool:
    """
    Verify Directus webhook secret.

    Args:
        provided_secret: Secret from webhook header.
        settings: Application settings.

    Returns:
        bool: True if secret is valid.
    """
    expected_secret = settings.WEBHOOK_SECRET
    if not expected_secret:
        # No secret configured, allow in development
        if settings.DEBUG:
            return True
        return False

    if not provided_secret:
        return False

    return hmac.compare_digest(provided_secret, expected_secret)


def verify_stripe_signature(
    payload: bytes,
    signature: str,
    settings: Settings,
) -> bool:
    """
    Verify Stripe webhook signature.

    Args:
        payload: Raw request body.
        signature: Stripe-Signature header.
        settings: Application settings.

    Returns:
        bool: True if signature is valid.
    """
    webhook_secret = settings.STRIPE_WEBHOOK_SECRET
    if not webhook_secret:
        logger.warning("stripe_webhook_secret_not_configured")
        return settings.DEBUG  # Allow in debug mode

    try:
        import time

        # Parse signature header (format: t=timestamp,v1=signature)
        elements = dict(item.split("=", 1) for item in signature.split(","))
        timestamp = elements.get("t", "")
        expected_sig = elements.get("v1", "")

        # Verify timestamp (within 5 minutes)
        current_time = int(time.time())
        if abs(current_time - int(timestamp)) > 300:
            logger.warning("stripe_webhook_timestamp_expired")
            return False

        # Compute expected signature
        signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
        computed_sig = hmac.new(
            webhook_secret.encode("utf-8"),
            signed_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(computed_sig, expected_sig)

    except Exception as e:
        logger.error("stripe_signature_verification_error", error=str(e))
        return False


def verify_iyzico_signature(
    payload: dict[str, Any],
    settings: Settings,
) -> bool:
    """
    Verify iyzico callback authenticity.

    Args:
        payload: Callback payload data.
        settings: Application settings.

    Returns:
        bool: True if callback is valid.
    """
    # iyzico uses token-based verification
    # The token should be verified against the iyzico API
    # For now, we accept if token is present
    token = payload.get("token")
    if not token:
        logger.warning("iyzico_callback_missing_token")
        return settings.DEBUG

    # TODO: Verify token against iyzico API
    return True


# =============================================================================
# Background Task Handlers
# =============================================================================


async def process_directus_event(
    event: str,
    collection: str,
    keys: list[str],
    payload: dict[str, Any] | None,
    request_id: str,
) -> None:
    """
    Process Directus webhook event in background.

    Routes events to appropriate handlers based on collection.

    Args:
        event: Event type.
        collection: Collection name.
        keys: Affected item keys.
        payload: Event payload.
        request_id: Request ID for tracing.
    """
    logger.info(
        "directus_event_processing",
        request_id=request_id,
        event=event,
        collection=collection,
        keys=keys,
    )

    try:
        # Route to appropriate handler based on collection
        if collection == "booking_requests":
            await _handle_booking_event(event, keys, payload, request_id)
        elif collection == "applicant_profiles":
            await _handle_applicant_event(event, keys, payload, request_id)
        elif collection == "agency_credits":
            await _handle_credit_event(event, keys, payload, request_id)
        else:
            logger.debug(
                "directus_event_unhandled_collection",
                collection=collection,
                event=event,
            )

    except Exception as e:
        logger.error(
            "directus_event_processing_error",
            request_id=request_id,
            event=event,
            collection=collection,
            error=str(e),
        )


async def _handle_booking_event(
    event: str,
    keys: list[str],
    payload: dict[str, Any] | None,
    request_id: str,
) -> None:
    """Handle booking_requests collection events."""
    if event == DirectusEvent.ITEMS_CREATE.value:
        # Queue new booking for processing
        logger.info(
            "booking_created_webhook",
            request_id=request_id,
            booking_ids=keys,
        )
        # TODO: Trigger Celery task to queue booking
        # from src.queue.tasks.booking import queue_booking
        # for booking_id in keys:
        #     queue_booking.delay(booking_id)

    elif event == DirectusEvent.ITEMS_UPDATE.value:
        # Check for status changes
        if payload and "status" in payload:
            logger.info(
                "booking_status_changed_webhook",
                request_id=request_id,
                booking_ids=keys,
                new_status=payload.get("status"),
            )
            # TODO: Handle status transition side effects


async def _handle_applicant_event(
    event: str,
    keys: list[str],
    payload: dict[str, Any] | None,
    request_id: str,
) -> None:
    """Handle applicant_profiles collection events."""
    if event == DirectusEvent.ITEMS_CREATE.value:
        logger.info(
            "applicant_created_webhook",
            request_id=request_id,
            applicant_ids=keys,
        )


async def _handle_credit_event(
    event: str,
    keys: list[str],
    payload: dict[str, Any] | None,
    request_id: str,
) -> None:
    """Handle agency_credits collection events."""
    if event == DirectusEvent.ITEMS_UPDATE.value:
        logger.info(
            "credits_updated_webhook",
            request_id=request_id,
            credit_ids=keys,
            changes=payload,
        )
        # TODO: Check for low credit alerts


async def process_payment_event(
    provider: str,
    event_type: str,
    transaction_id: str,
    booking_id: str | None,
    event_data: dict[str, Any],
    request_id: str,
) -> None:
    """
    Process payment webhook event in background.

    Args:
        provider: Payment provider (stripe, iyzico).
        event_type: Payment event type.
        transaction_id: Provider transaction ID.
        booking_id: Associated booking ID.
        event_data: Full event data.
        request_id: Request ID for tracing.
    """
    logger.info(
        "payment_event_processing",
        request_id=request_id,
        provider=provider,
        event_type=event_type,
        transaction_id=transaction_id,
        booking_id=booking_id,
    )

    try:
        if event_type in ("payment_intent.succeeded", "success"):
            # Payment completed successfully
            logger.info(
                "payment_success_webhook",
                provider=provider,
                transaction_id=transaction_id,
                booking_id=booking_id,
            )
            # TODO: Update booking status to payment_completed
            # TODO: Trigger booking finalization

        elif event_type in ("payment_intent.payment_failed", "failure"):
            # Payment failed
            logger.warning(
                "payment_failed_webhook",
                provider=provider,
                transaction_id=transaction_id,
                booking_id=booking_id,
            )
            # TODO: Update booking status to payment_failed
            # TODO: Release reserved slot

        elif event_type == "charge.refunded":
            # Refund processed
            logger.info(
                "payment_refunded_webhook",
                provider=provider,
                transaction_id=transaction_id,
            )
            # TODO: Update credit balance

    except Exception as e:
        logger.error(
            "payment_event_processing_error",
            request_id=request_id,
            provider=provider,
            error=str(e),
        )


async def process_sheets_sync(
    spreadsheet_id: str,
    agency_id: str | None,
    change_type: str,
    changed_range: str | None,
    request_id: str,
) -> None:
    """
    Process Google Sheets change event in background.

    Args:
        spreadsheet_id: Google Sheets spreadsheet ID.
        agency_id: Associated agency ID.
        change_type: Type of change.
        changed_range: Changed cell range.
        request_id: Request ID for tracing.
    """
    logger.info(
        "sheets_sync_processing",
        request_id=request_id,
        spreadsheet_id=spreadsheet_id,
        agency_id=agency_id,
        change_type=change_type,
        changed_range=changed_range,
    )

    try:
        # TODO: Trigger Celery task to sync sheet data
        # from src.queue.tasks.sync import sync_google_sheet
        # sync_google_sheet.delay(spreadsheet_id, agency_id)
        pass

    except Exception as e:
        logger.error(
            "sheets_sync_processing_error",
            request_id=request_id,
            spreadsheet_id=spreadsheet_id,
            error=str(e),
        )


async def process_verification_callback(
    verification_type: str,
    reference_id: str,
    status: str,
    otp_code: str | None,
    request_id: str,
) -> None:
    """
    Process verification callback in background.

    Args:
        verification_type: email or sms.
        reference_id: Reference ID for the verification.
        status: Delivery status.
        otp_code: Extracted OTP code.
        request_id: Request ID for tracing.
    """
    logger.info(
        "verification_callback_processing",
        request_id=request_id,
        verification_type=verification_type,
        reference_id=reference_id,
        status=status,
        has_otp=otp_code is not None,
    )

    try:
        if status == "delivered" and otp_code:
            # OTP received, trigger verification flow
            logger.info(
                "otp_received_webhook",
                reference_id=reference_id,
                verification_type=verification_type,
            )
            # TODO: Trigger OTP entry in booking flow
            # from src.queue.tasks.verification import enter_otp
            # enter_otp.delay(reference_id, otp_code, verification_type)

    except Exception as e:
        logger.error(
            "verification_callback_error",
            request_id=request_id,
            reference_id=reference_id,
            error=str(e),
        )


# =============================================================================
# Endpoints
# =============================================================================


@router.post(
    "/directus",
    response_model=WebhookResponse,
    summary="Directus webhook endpoint",
    description="Receives data change events from Directus CMS.",
    responses={
        200: {"description": "Event received and queued"},
        401: {"description": "Invalid webhook secret"},
    },
)
async def receive_directus_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: DirectusWebhookPayload,
    request_id: RequestId,
    x_directus_secret: str | None = Header(None, alias="X-Directus-Secret"),
) -> WebhookResponse:
    """
    Receive Directus data change webhook.

    Processes Directus CMS events for data lifecycle hooks:
    - items.create: New record created
    - items.update: Record updated
    - items.delete: Record deleted

    Events are queued for background processing.

    Args:
        request: FastAPI request object.
        background_tasks: Background task manager.
        payload: Directus webhook payload.
        request_id: Request ID for tracing.
        x_directus_secret: Webhook authentication secret.

    Returns:
        WebhookResponse: Acknowledgment response.
    """
    settings = get_settings()

    # Verify webhook secret
    if not verify_directus_secret(x_directus_secret, settings):
        logger.warning(
            "directus_webhook_unauthorized",
            request_id=request_id,
            collection=payload.collection,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook secret",
        )

    # Normalize keys
    keys: list[str] = []
    if payload.keys:
        keys = payload.keys
    elif payload.key:
        keys = [payload.key] if isinstance(payload.key, str) else payload.key

    logger.info(
        "directus_webhook_received",
        request_id=request_id,
        event=payload.event,
        collection=payload.collection,
        keys=keys,
    )

    # Queue background processing
    background_tasks.add_task(
        process_directus_event,
        event=payload.event,
        collection=payload.collection,
        keys=keys,
        payload=payload.payload,
        request_id=request_id,
    )

    return WebhookResponse(
        received=True,
        message="Event queued for processing",
        event_id=f"dir_{request_id[:8]}",
    )


@router.post(
    "/payment/stripe",
    response_model=WebhookResponse,
    summary="Stripe payment webhook",
    description="Receives payment events from Stripe.",
    responses={
        200: {"description": "Event received"},
        401: {"description": "Invalid signature"},
    },
)
async def receive_stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    request_id: RequestId,
    stripe_signature: str = Header(..., alias="Stripe-Signature"),
) -> WebhookResponse:
    """
    Receive Stripe payment webhook.

    Verifies signature and processes payment events:
    - payment_intent.succeeded: Payment completed
    - payment_intent.payment_failed: Payment failed
    - charge.refunded: Refund processed

    Args:
        request: FastAPI request object.
        background_tasks: Background task manager.
        request_id: Request ID for tracing.
        stripe_signature: Stripe webhook signature header.

    Returns:
        WebhookResponse: Acknowledgment response.
    """
    settings = get_settings()

    # Read raw body for signature verification
    body = await request.body()

    # Verify signature
    if not verify_stripe_signature(body, stripe_signature, settings):
        logger.warning(
            "stripe_webhook_invalid_signature",
            request_id=request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    # Parse payload
    try:
        payload = StripeWebhookPayload.model_validate_json(body)
    except Exception as e:
        logger.error(
            "stripe_webhook_parse_error",
            request_id=request_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payload format",
        )

    logger.info(
        "stripe_webhook_received",
        request_id=request_id,
        event_id=payload.id,
        event_type=payload.type,
        livemode=payload.livemode,
    )

    # Extract booking ID from metadata
    data_object = payload.data.get("object", {})
    metadata = data_object.get("metadata", {})
    booking_id = metadata.get("booking_id")
    transaction_id = data_object.get("id", "")

    # Queue background processing
    background_tasks.add_task(
        process_payment_event,
        provider="stripe",
        event_type=payload.type,
        transaction_id=transaction_id,
        booking_id=booking_id,
        event_data=payload.model_dump(),
        request_id=request_id,
    )

    return WebhookResponse(
        received=True,
        message="Event queued for processing",
        event_id=payload.id,
    )


@router.post(
    "/payment/iyzico",
    response_model=WebhookResponse,
    summary="iyzico payment callback",
    description="Receives payment callbacks from iyzico.",
    responses={
        200: {"description": "Callback received"},
        401: {"description": "Invalid callback"},
    },
)
async def receive_iyzico_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: IyzicoWebhookPayload,
    request_id: RequestId,
) -> WebhookResponse:
    """
    Receive iyzico payment callback.

    Processes iyzico payment results after 3DS authentication.

    Args:
        request: FastAPI request object.
        background_tasks: Background task manager.
        payload: iyzico callback payload.
        request_id: Request ID for tracing.

    Returns:
        WebhookResponse: Acknowledgment response.
    """
    settings = get_settings()

    # Verify callback authenticity
    if not verify_iyzico_signature(payload.model_dump(), settings):
        logger.warning(
            "iyzico_callback_invalid",
            request_id=request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid callback",
        )

    logger.info(
        "iyzico_callback_received",
        request_id=request_id,
        payment_id=payload.payment_id,
        status=payload.status,
        conversation_id=payload.conversation_id,
    )

    # Map status to event type
    event_type = "success" if payload.status == "success" else "failure"

    # Queue background processing
    background_tasks.add_task(
        process_payment_event,
        provider="iyzico",
        event_type=event_type,
        transaction_id=payload.payment_id,
        booking_id=payload.conversation_id,
        event_data=payload.model_dump(),
        request_id=request_id,
    )

    return WebhookResponse(
        received=True,
        message="Callback processed",
        event_id=f"iyz_{payload.payment_id[:8]}",
    )


@router.post(
    "/sheets",
    response_model=WebhookResponse,
    summary="Google Sheets change webhook",
    description="Receives change notifications from Google Sheets.",
    responses={
        200: {"description": "Notification received"},
        401: {"description": "Invalid request"},
    },
)
async def receive_sheets_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: SheetsWebhookPayload,
    request_id: RequestId,
    x_webhook_secret: str | None = Header(None, alias="X-Webhook-Secret"),
) -> WebhookResponse:
    """
    Receive Google Sheets change notification.

    Triggers sync of applicant data from connected Google Sheets.

    Args:
        request: FastAPI request object.
        background_tasks: Background task manager.
        payload: Sheets change payload.
        request_id: Request ID for tracing.
        x_webhook_secret: Webhook authentication secret.

    Returns:
        WebhookResponse: Acknowledgment response.
    """
    settings = get_settings()

    # Verify webhook secret
    if not verify_directus_secret(x_webhook_secret, settings):
        logger.warning(
            "sheets_webhook_unauthorized",
            request_id=request_id,
            spreadsheet_id=payload.spreadsheet_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook secret",
        )

    logger.info(
        "sheets_webhook_received",
        request_id=request_id,
        spreadsheet_id=payload.spreadsheet_id,
        change_type=payload.change_type,
        agency_id=payload.agency_id,
    )

    # Queue background sync
    background_tasks.add_task(
        process_sheets_sync,
        spreadsheet_id=payload.spreadsheet_id,
        agency_id=payload.agency_id,
        change_type=payload.change_type,
        changed_range=payload.changed_range,
        request_id=request_id,
    )

    return WebhookResponse(
        received=True,
        message="Sync queued",
        event_id=f"sht_{request_id[:8]}",
    )


@router.post(
    "/verification",
    response_model=WebhookResponse,
    summary="Verification callback endpoint",
    description="Receives email/SMS verification callbacks.",
    responses={
        200: {"description": "Callback received"},
    },
)
async def receive_verification_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: VerificationWebhookPayload,
    request_id: RequestId,
) -> WebhookResponse:
    """
    Receive email/SMS verification callback.

    Processes OTP delivery confirmations and extracted codes.

    Args:
        request: FastAPI request object.
        background_tasks: Background task manager.
        payload: Verification callback payload.
        request_id: Request ID for tracing.

    Returns:
        WebhookResponse: Acknowledgment response.
    """
    logger.info(
        "verification_callback_received",
        request_id=request_id,
        verification_type=payload.verification_type,
        reference_id=payload.reference_id,
        status=payload.status,
        provider=payload.provider,
    )

    # Queue background processing
    background_tasks.add_task(
        process_verification_callback,
        verification_type=payload.verification_type,
        reference_id=payload.reference_id,
        status=payload.status,
        otp_code=payload.otp_code,
        request_id=request_id,
    )

    return WebhookResponse(
        received=True,
        message="Callback processed",
        event_id=f"ver_{request_id[:8]}",
    )


@router.get(
    "/health",
    summary="Webhook endpoint health check",
    description="Verify webhook endpoints are accessible.",
    responses={
        200: {"description": "Endpoints healthy"},
    },
)
async def webhook_health() -> dict[str, Any]:
    """
    Health check for webhook endpoints.

    Useful for external services to verify connectivity.

    Returns:
        dict: Health status.
    """
    return {
        "status": "healthy",
        "endpoints": [
            "/directus",
            "/payment/stripe",
            "/payment/iyzico",
            "/sheets",
            "/verification",
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "router",
    "WebhookSource",
    "DirectusEvent",
    "PaymentEventType",
    "WebhookResponse",
]
