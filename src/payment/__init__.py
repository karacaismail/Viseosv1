"""
VISE OS Payment Module.

Automated payment processing with 3DS authentication support.
Provides PCI-DSS compliant card data encryption and multi-gateway
support for visa appointment booking payments.

Components:
- processor: Main payment processing service with card encryption
- three_ds: 3D Secure authentication handler (1.0/2.0+)

Core Classes:
- PaymentProcessor: Orchestrates payment flows
- CardDataEncryptor: PCI-DSS compliant card encryption
- ThreeDSHandler: 3DS authentication handling
- PaymentGatewayRouter: Multi-gateway routing

Usage:
    from src.payment import PaymentProcessor, PaymentRequest

    processor = PaymentProcessor(encryptor, gateway_router)
    result = await processor.process_payment(request)

    if result.success:
        print(f"Payment successful: {result.transaction_id}")
    elif result.requires_3ds:
        # Handle 3DS authentication
        pass

Security Notes:
    - Card data is encrypted using Fernet (AES-128-CBC + HMAC-SHA256)
    - CVV is never stored permanently
    - Sensitive data is only decrypted in memory during use
    - All card operations use memory-only decryption
"""

from src.payment.processor import (
    CardDataEncryptor,
    CardType,
    PaymentGateway,
    PaymentGatewayRouter,
    PaymentGatewayType,
    PaymentProcessor,
    PaymentRequest,
    PaymentResult,
    SecureCardData,
)
from src.payment.three_ds import (
    ThreeDSChallenge,
    ThreeDSHandler,
    ThreeDSMethod,
    ThreeDSResult,
    ThreeDSStatus,
    ThreeDSVersion,
)

__all__ = [
    # Processor
    "CardDataEncryptor",
    "CardType",
    "PaymentGateway",
    "PaymentGatewayRouter",
    "PaymentGatewayType",
    "PaymentProcessor",
    "PaymentRequest",
    "PaymentResult",
    "SecureCardData",
    # 3DS
    "ThreeDSChallenge",
    "ThreeDSHandler",
    "ThreeDSMethod",
    "ThreeDSResult",
    "ThreeDSStatus",
    "ThreeDSVersion",
]
