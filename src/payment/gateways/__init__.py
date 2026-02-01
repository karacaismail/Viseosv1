"""
VISE OS Payment Gateways Module.

This module provides implementations of various payment gateway integrations
for processing visa appointment booking payments.

Available Gateways:
- IyzicoGateway: Turkish local payment gateway (iyzico)

Each gateway implements the PaymentGateway abstract base class from
src.payment.processor, providing consistent interface for payment processing,
3DS result checking, and refunds.

Usage:
    from src.payment.gateways import IyzicoGateway
    from src.api.config import get_settings

    settings = get_settings()
    gateway = IyzicoGateway(
        api_key=settings.IYZICO_API_KEY.get_secret_value(),
        secret_key=settings.IYZICO_SECRET_KEY.get_secret_value(),
    )

    result = await gateway.process_payment(payment_request)
"""

from src.payment.gateways.iyzico import IyzicoGateway

__all__ = [
    "IyzicoGateway",
]
